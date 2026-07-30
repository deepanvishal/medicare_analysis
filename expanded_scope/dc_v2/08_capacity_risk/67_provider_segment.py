"""
67 - provider x segment matrix   [PYTHON runner / BigQuery DDL]

WHAT  : Builds cap_provider_segment - the 8-cell matrix (CD-12): panel
        profile, own intake rate, credibility blending with cohort rates
        (CD-13, w = n/(n+k)), OWN/BORROWED tags (CD-14), closed-door logic,
        and the two-level caps (CD-15): cell_cap = blended x 12 x
        HORIZON_FACTOR, then proportional scale-down so the cells' implied
        hours never exceed the provider's absorbing capacity = spare_hrs +
        team_uplift_hrs (CD-23), converted via segment avg_first_yr_hrs.
GRAIN : npi/epdb_dw_prvdr_id x prvdr_county + prvdr_state_cd x segment_cd
        (all 8 cells per provider-county, even empty ones)
INPUTS: A870800_medicare_analysis_2025_claims (ONE scan), HCC map,
        cap_provider_year, cap_cohort_bench, ref_segment, cap_params
OUTPUT: cap_provider_segment (BigQuery table) + sanity prints.
Run   : python expanded_scope/dc_v2/08_capacity_risk/67_provider_segment.py
"""

# ASSUMPTION [1]: n_cell (credibility n) = panel_cnt - Section 5 defines
#   "n = provider's patient count in the cell". Falsified if run review
#   wants n = observed new patients behind the intake rate instead.
# ASSUMPTION [2]: panel window = trailing 12 months ending 2025-12 (48's
#   panel convention anchored at the latest extract month). A panel
#   member's segment = their segment at their most recent visit to that
#   provider in the window.
# ASSUMPTION [3]: intake window = 2024-01..2025-12 (24 months), rate =
#   distinct new patient-months / provider active months, per segment.
# ASSUMPTION [4]: the full 8-cell matrix is materialized per provider x
#   county (ref_segment cross join); cells with no panel get panel_cnt 0,
#   own rate NULL, cred_w 0, blended = cohort rate.
# ASSUMPTION [5]: cohort rate lookup = specialty x band x state from
#   cap_cohort_bench (66's cohort key, ASSUMPTION 66-[1]).
# ASSUMPTION [6]: providers absent from cap_provider_year (CD-22 exclusions)
#   get NO matrix rows - they are the CD-24 facility pass-through
#   population, handled in module 69.

import os
import sys
import time


def _expanded_scope_dir():
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        return os.path.dirname(os.path.dirname(here))
    except NameError:
        probe = os.getcwd()
        for _ in range(6):
            if os.path.isfile(os.path.join(probe, "config.py")):
                return probe
            cand = os.path.join(probe, "expanded_scope")
            if os.path.isfile(os.path.join(cand, "config.py")):
                return cand
            probe = os.path.dirname(probe)
    raise FileNotFoundError(
        "config.py not found - run from the repo root or any folder inside it")


sys.path.insert(0, _expanded_scope_dir())
import config as cfg

RUN_MODE = "sample"

PY     = cfg.table("cap_provider_year")
BENCH  = cfg.table("cap_cohort_bench")
SEG    = cfg.table("ref_segment")
PARAMS = cfg.table("cap_params")
OUT    = cfg.table("cap_provider_segment")
CLAIMS = "anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_2025_claims"
MAP    = "anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.HCC_ICD_Mapping_2025"

K       = (f"(SELECT param_val FROM `{PARAMS}` WHERE param_nm = 'CRED_K' "
           f"AND param_scope = 'GLOBAL')")
HORIZON = (f"(SELECT param_val FROM `{PARAMS}` WHERE param_nm = 'HORIZON_FACTOR' "
           f"AND param_scope = 'GLOBAL')")

SAMPLE = ("AND MOD(ABS(FARM_FINGERPRINT(CAST(c.member_id AS STRING))), 100) = 0"
          if RUN_MODE == "sample" else "")

PANEL_ANCHOR = "DATE '2025-12-01'"

DDL = f"""
CREATE OR REPLACE TABLE `{OUT}`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
WITH claims_f AS (
  SELECT
    c.member_id,
    TRIM(CAST(c.epdb_dw_prvdr_id AS STRING)) AS epdb_dw_prvdr_id,
    NULLIF(TRIM(c.prvdr_county), '')         AS prvdr_county,
    UPPER(LEFT(c.prvdr_submarket, 2))        AS prvdr_state_cd,
    DATE_TRUNC(c.srv_start_dt, MONTH)        AS month,
    c.age_nbr,
    UPPER(REPLACE(TRIM(c.pri_icd9_dx_cd), '.', '')) AS dx
  FROM `{CLAIMS}` c
  WHERE c.age_nbr >= 60
    {SAMPLE}
),
chronic_members AS (
  SELECT DISTINCT m.month, mm.member_id
  FROM (SELECT DISTINCT month FROM claims_f) m
  JOIN (SELECT DISTINCT cf.member_id, cf.month AS claim_month
        FROM claims_f cf JOIN `{MAP}` h
          ON cf.dx = UPPER(TRIM(h.diagnosis_code))
        WHERE h.HCC_v24 IS NOT NULL) mm
    ON mm.claim_month BETWEEN DATE_SUB(m.month, INTERVAL 23 MONTH) AND m.month
),
pair_new AS (
  SELECT member_id, epdb_dw_prvdr_id, month,
         COALESCE(LAG(month) OVER (PARTITION BY member_id, epdb_dw_prvdr_id
                                   ORDER BY month)
                    < DATE_SUB(month, INTERVAL 12 MONTH), TRUE) AS is_new
  FROM (SELECT DISTINCT member_id, epdb_dw_prvdr_id, month FROM claims_f)
),
segmented AS (
  SELECT
    cf.member_id, cf.epdb_dw_prvdr_id, cf.prvdr_county, cf.prvdr_state_cd,
    cf.month,
    CONCAT(IF(pn.is_new, 'NEW', 'RET'), '_',
           IF(ch.member_id IS NOT NULL, 'CHR', 'NONCHR'), '_',
           IF(cf.age_nbr BETWEEN 60 AND 74, '60_74', '75P')) AS segment_cd,
    pn.is_new
  FROM claims_f cf
  JOIN pair_new pn
    ON cf.member_id = pn.member_id AND cf.epdb_dw_prvdr_id = pn.epdb_dw_prvdr_id
    AND cf.month = pn.month
  LEFT JOIN chronic_members ch
    ON ch.month = cf.month AND ch.member_id = cf.member_id
  WHERE EXTRACT(YEAR FROM cf.month) IN (2024, 2025)
),
panel AS (
  SELECT epdb_dw_prvdr_id, prvdr_county, prvdr_state_cd, segment_cd,
         COUNT(DISTINCT member_id) AS panel_cnt
  FROM (
    SELECT *, ROW_NUMBER() OVER (
      PARTITION BY member_id, epdb_dw_prvdr_id, prvdr_county
      ORDER BY month DESC) AS rn
    FROM segmented
    WHERE month BETWEEN DATE_SUB({PANEL_ANCHOR}, INTERVAL 11 MONTH) AND {PANEL_ANCHOR}
  )
  WHERE rn = 1
  GROUP BY 1, 2, 3, 4
),
active_months AS (
  SELECT epdb_dw_prvdr_id, prvdr_county, COUNT(DISTINCT month) AS active_mths
  FROM segmented GROUP BY 1, 2
),
own_intake AS (
  SELECT s.epdb_dw_prvdr_id, s.prvdr_county, s.prvdr_state_cd, s.segment_cd,
         COUNT(DISTINCT IF(s.is_new,
           CONCAT(s.member_id, '|', CAST(s.month AS STRING)), NULL)) AS new_patients,
         ANY_VALUE(am.active_mths) AS active_mths
  FROM segmented s
  JOIN active_months am
    ON s.epdb_dw_prvdr_id = am.epdb_dw_prvdr_id AND s.prvdr_county = am.prvdr_county
  GROUP BY 1, 2, 3, 4
),
matrix_frame AS (
  SELECT py.npi, py.epdb_dw_prvdr_id, py.prvdr_county, py.prvdr_state_cd,
         py.specialty_ctg_cd, py.county_band_cd,
         py.spare_hrs, py.team_uplift_hrs, sg.segment_cd
  FROM `{PY}` py
  CROSS JOIN `{SEG}` sg
  WHERE py.epdb_dw_prvdr_id IS NOT NULL
),
cells AS (
  SELECT
    mf.*,
    COALESCE(p.panel_cnt, 0)                          AS panel_cnt,
    SAFE_DIVIDE(oi.new_patients, oi.active_mths)      AS own_intake_rate,
    COALESCE(p.panel_cnt, 0)                          AS n_cell,
    cb.cohort_intake_rate,
    cb.avg_first_yr_hrs
  FROM matrix_frame mf
  LEFT JOIN panel p
    ON mf.epdb_dw_prvdr_id = p.epdb_dw_prvdr_id
    AND mf.prvdr_county = p.prvdr_county AND mf.segment_cd = p.segment_cd
  LEFT JOIN own_intake oi
    ON mf.epdb_dw_prvdr_id = oi.epdb_dw_prvdr_id
    AND mf.prvdr_county = oi.prvdr_county AND mf.segment_cd = oi.segment_cd
  LEFT JOIN `{BENCH}` cb
    ON mf.specialty_ctg_cd = cb.specialty_ctg_cd
    AND COALESCE(mf.county_band_cd, '') = COALESCE(cb.county_band_cd, '')
    AND mf.prvdr_state_cd = cb.prvdr_state_cd
    AND mf.segment_cd = cb.segment_cd
),
blended AS (
  SELECT
    *,
    SAFE_DIVIDE(n_cell, n_cell + {K})                 AS cred_w,
    SAFE_DIVIDE(n_cell, n_cell + {K}) * COALESCE(own_intake_rate, 0)
      + (1 - SAFE_DIVIDE(n_cell, n_cell + {K})) * COALESCE(cohort_intake_rate, 0)
                                                      AS blended_rate,
    SUM(COALESCE(own_intake_rate, 0))
      OVER (PARTITION BY epdb_dw_prvdr_id, prvdr_county) = 0 AS closed_door
  FROM cells
),
capped AS (
  SELECT
    *,
    IF(closed_door, 0, blended_rate * 12 * {HORIZON}) AS cell_cap_cnt,
    SUM(IF(closed_door, 0, blended_rate * 12 * {HORIZON})
        * COALESCE(avg_first_yr_hrs, 0))
      OVER (PARTITION BY epdb_dw_prvdr_id, prvdr_county) AS implied_hrs,
    spare_hrs + team_uplift_hrs                       AS absorbing_hrs
  FROM blended
)
SELECT
  npi, epdb_dw_prvdr_id, prvdr_county, prvdr_state_cd, segment_cd,
  panel_cnt,
  SAFE_DIVIDE(panel_cnt,
    SUM(panel_cnt) OVER (PARTITION BY epdb_dw_prvdr_id, prvdr_county)) AS panel_share,
  own_intake_rate,
  n_cell,
  cred_w,
  blended_rate,
  IF(cred_w >= 0.5, 'OWN', 'BORROWED')  AS signal_src_cd,
  IF(closed_door, 1, 0)                 AS closed_door_flag,
  cell_cap_cnt,
  cell_cap_cnt * IF(implied_hrs > absorbing_hrs AND implied_hrs > 0,
                    SAFE_DIVIDE(absorbing_hrs, implied_hrs), 1) AS cell_cap_scaled_cnt
FROM capped
"""

CHECKS = {
    "rows / providers / cells per provider-county (expect 8)":
        f"SELECT COUNT(*) AS rows_n, COUNT(DISTINCT epdb_dw_prvdr_id) AS providers, "
        f"ROUND(COUNT(*) / COUNT(DISTINCT CONCAT(epdb_dw_prvdr_id, '|', prvdr_county)), 1) "
        f"AS cells_per_prov_cty FROM `{OUT}`",
    "panel_share sums to 1 per provider-county (nonzero panels)":
        f"SELECT COUNT(*) AS bad FROM (SELECT epdb_dw_prvdr_id, prvdr_county, "
        f"SUM(panel_share) AS s FROM `{OUT}` GROUP BY 1, 2 "
        f"HAVING s IS NOT NULL AND ABS(s - 1.0) > 0.000001)",
    "signal source split (CD-14 honesty)":
        f"SELECT signal_src_cd, COUNT(*) AS n, ROUND(COUNT(*) / SUM(COUNT(*)) OVER (), 4) "
        f"AS pct FROM `{OUT}` GROUP BY 1",
    "closed doors":
        f"SELECT ROUND(COUNTIF(closed_door_flag = 1) / COUNT(*), 4) AS pct_closed_cells "
        f"FROM `{OUT}`",
    "scale-down incidence (cells hitting the total constraint)":
        f"SELECT ROUND(COUNTIF(cell_cap_scaled_cnt < cell_cap_cnt) / COUNTIF(cell_cap_cnt > 0), 4) "
        f"AS pct_scaled FROM `{OUT}`",
}


def _run(client, label, sql):
    t0 = time.time()
    result = client.query(sql).result()
    print(f"[{label}] {time.time() - t0:.1f}s")
    return result


def main():
    print(f"RUN_MODE = {RUN_MODE}")
    client = cfg.client()
    _run(client, "create cap_provider_segment", DDL)
    for label, q in CHECKS.items():
        print(f"--- {label} ---")
        for row in _run(client, label, q):
            print("  ", dict(row))


if __name__ == "__main__":
    main()


# REVIEW
# Reviewer 1 LOGIC:
#  - Capacity attribution: prvdr_county (+ prvdr_state_cd, rule 12) from the
#    claims scan; segment rules identical to module 66 (same CTE shapes).
#  - Closed door = zero own intake across ALL segments in the window; forced
#    0 caps per Section 5.
#  - Blending exactly w x own + (1-w) x cohort with w = n/(n+k), k from
#    cap_params.
# Reviewer 2 SPEC:
#  - Deviations = six ASSUMPTION blocks (n_cell = panel_cnt is the one most
#    worth review; also full-matrix materialization and the cohort key).
#  - Two-level caps per Section 6: cells share one absorbing-hours budget
#    (spare + uplift, CD-23) via proportional scale-down.
# Reviewer 3 EFFICIENCY:
#  - Exactly ONE claims scan (claims_f). The only CROSS JOIN is deliberate
#    and bounded: providers x 8 segment rows. Window functions replace
#    self-joins for shares/scaling. Relative cost ~ one claims scan.
