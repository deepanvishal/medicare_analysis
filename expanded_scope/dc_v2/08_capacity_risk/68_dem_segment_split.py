"""
68 - demand segment split   [PYTHON runner / BigQuery DDL]

WHAT  : Splits the anchored demand forecast into the 8 ref_segment cells -
        dem_segment_split. Anchor = dc2_demand_predictions (module 50),
        future row per county x specialty: pred_next_12m_xgb at feature
        month 2025-12. Split by OBSERVED segment shares (member-county
        attribution - mbr_county_cd, never prvdr_county). growth_demand =
        the forecast's excess over observed trailing-12m visits, split by
        the same shares (CD-17: the split invents no demand). V5 GATE:
        segment shares re-sum to 1 and segment demand re-sums to the
        anchor, exactly.
GRAIN : mbr_county_cd + mbr_state_cd x specialty_ctg_cd x segment_cd
INPUTS: A870800_medicare_analysis_2025_claims (ONE scan, demand lens),
        HCC map, dc2_demand_predictions, ref_segment
OUTPUT: dem_segment_split (BigQuery table) + V5 gate + sanity prints.
Run   : python expanded_scope/dc_v2/08_capacity_risk/68_dem_segment_split.py
"""

# ASSUMPTION [1]: RESOLVED by M63-72 triage ruling - specialty_ctg_cd grain
#   is CORRECT here: the forecast anchor lives at ctg grain and rule 6
#   bridges exactly once at the fill join (module 69). The data model has
#   been amended; cap_fill_result/cap_county_risk carry the bridged
#   cms_specialty from the fill join onward.
# ASSUMPTION [2]: anchor rows = split_label='future' AND month='2025-12-01'
#   (50's docstring: feature month 2025-12 scored = the 2026 predictions),
#   measure = pred_next_12m_xgb (55 carries xgb columns only for demand).
# ASSUMPTION [3]: growth_demand = GREATEST(anchor - observed trailing-12m
#   visits, 0) x segment_share, growth_src_cd='FORECAST'. The spec offers
#   forecast delta or scenario slider; the slider path writes SCENARIO rows
#   from the dashboard later.
# ASSUMPTION [4]: demand-side segment of a visit uses the same member x
#   provider pair 12-mo new/returning rule as the supply side - a visit is
#   'new' if the member is new to THAT provider. Chronic and age band are
#   member-level (24-mo HCC window, age_nbr).
# ASSUMPTION [5]: mbr_state_cd = UPPER(LEFT(mbr_submarket, 2)) (locked
#   fact); rows with NULL member county or state are kept and printed, not
#   dropped.
# ASSUMPTION [6]: counties x specialties present in the forecast but absent
#   from observed claims (no share basis) get the anchor placed in a single
#   NULL-segment row, printed - the split cannot invent a segment mix.

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

FCST   = cfg.src("dc2_demand_predictions")
SEG    = cfg.table("ref_segment")
OUT    = cfg.table("dem_segment_split")
CLAIMS = "anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_2025_claims"
MAP    = "anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.HCC_ICD_Mapping_2025"

SAMPLE = ("AND MOD(ABS(FARM_FINGERPRINT(CAST(c.member_id AS STRING))), 100) = 0"
          if RUN_MODE == "sample" else "")

DDL = f"""
CREATE OR REPLACE TABLE `{OUT}`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
WITH claims_f AS (
  SELECT
    c.member_id,
    TRIM(CAST(c.epdb_dw_prvdr_id AS STRING))  AS epdb_dw_prvdr_id,
    TRIM(CAST(c.mbr_county_cd AS STRING))     AS mbr_county_cd,
    UPPER(LEFT(c.mbr_submarket, 2))           AS mbr_state_cd,
    c.specialty_ctg_cd,
    DATE_TRUNC(c.srv_start_dt, MONTH)         AS month,
    c.srv_start_dt,
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
seg_visits AS (
  SELECT
    cf.mbr_county_cd, cf.mbr_state_cd, cf.specialty_ctg_cd,
    CONCAT(IF(pn.is_new, 'NEW', 'RET'), '_',
           IF(ch.member_id IS NOT NULL, 'CHR', 'NONCHR'), '_',
           IF(cf.age_nbr BETWEEN 60 AND 74, '60_74', '75P')) AS segment_cd,
    COUNT(DISTINCT CONCAT(cf.member_id, '|', cf.epdb_dw_prvdr_id, '|',
                          CAST(cf.srv_start_dt AS STRING)))  AS visits
  FROM claims_f cf
  JOIN pair_new pn
    ON cf.member_id = pn.member_id AND cf.epdb_dw_prvdr_id = pn.epdb_dw_prvdr_id
    AND cf.month = pn.month
  LEFT JOIN chronic_members ch
    ON ch.month = cf.month AND ch.member_id = cf.member_id
  WHERE cf.month BETWEEN DATE '2025-01-01' AND DATE '2025-12-01'
  GROUP BY 1, 2, 3, 4
),
county_totals AS (
  SELECT mbr_county_cd, mbr_state_cd, specialty_ctg_cd,
         SUM(visits) AS observed_12m
  FROM seg_visits GROUP BY 1, 2, 3
),
anchor AS (
  SELECT TRIM(CAST(mbr_county_cd AS STRING)) AS mbr_county_cd,
         specialty_ctg_cd,
         SUM(pred_next_12m_xgb) AS anchor_demand
  FROM `{FCST}`
  WHERE split_label = 'future' AND month = DATE '2025-12-01'
  GROUP BY 1, 2
),
shares AS (
  SELECT sv.*, ct.observed_12m,
         SAFE_DIVIDE(sv.visits, ct.observed_12m) AS segment_share
  FROM seg_visits sv
  JOIN county_totals ct
    ON sv.mbr_county_cd = ct.mbr_county_cd
    AND COALESCE(sv.mbr_state_cd, '') = COALESCE(ct.mbr_state_cd, '')
    AND sv.specialty_ctg_cd = ct.specialty_ctg_cd
)
SELECT
  a.mbr_county_cd,
  s.mbr_state_cd,
  a.specialty_ctg_cd,
  s.segment_cd,
  a.anchor_demand,
  COALESCE(s.segment_share, 1.0)                    AS segment_share,
  a.anchor_demand * COALESCE(s.segment_share, 1.0)  AS segment_demand,
  GREATEST(a.anchor_demand - COALESCE(s.observed_12m, 0), 0)
    * COALESCE(s.segment_share, 1.0)                AS growth_demand,
  'FORECAST'                                        AS growth_src_cd
FROM anchor a
LEFT JOIN shares s
  ON a.mbr_county_cd = s.mbr_county_cd
  AND a.specialty_ctg_cd = s.specialty_ctg_cd
"""

GATE_V5 = f"""
SELECT COUNT(*) FROM (
  SELECT mbr_county_cd, specialty_ctg_cd,
         SUM(segment_share) AS share_sum,
         SUM(segment_demand) AS seg_sum,
         ANY_VALUE(anchor_demand) AS anchor
  FROM `{OUT}`
  GROUP BY 1, 2
  HAVING ABS(share_sum - 1.0) > 0.000001
      OR ABS(seg_sum - anchor) > 0.000001 * GREATEST(anchor, 1))
"""

CHECKS = {
    "row count / counties / specialties":
        f"SELECT COUNT(*) AS rows_n, COUNT(DISTINCT mbr_county_cd) AS counties, "
        f"COUNT(DISTINCT specialty_ctg_cd) AS specialties FROM `{OUT}`",
    "segment share distribution (eyeball)":
        f"SELECT segment_cd, ROUND(AVG(segment_share), 4) AS avg_share "
        f"FROM `{OUT}` GROUP BY 1 ORDER BY avg_share DESC",
    "no-observed-mix rows (anchor placed unsplit, segment NULL)":
        f"SELECT COUNT(*) AS unsplit_rows, ROUND(SUM(anchor_demand), 0) AS anchor_vol "
        f"FROM `{OUT}` WHERE segment_cd IS NULL",
    "NULL member geography (kept, A5)":
        f"SELECT COUNTIF(mbr_county_cd IS NULL) AS null_county, "
        f"COUNTIF(mbr_state_cd IS NULL) AS null_state FROM `{OUT}`",
    "growth totals by state":
        f"SELECT mbr_state_cd, ROUND(SUM(growth_demand), 0) AS growth "
        f"FROM `{OUT}` GROUP BY 1 ORDER BY 1",
}


def _run(client, label, sql):
    t0 = time.time()
    result = client.query(sql).result()
    print(f"[{label}] {time.time() - t0:.1f}s")
    return result


def main():
    print(f"RUN_MODE = {RUN_MODE}")
    client = cfg.client()
    _run(client, "create dem_segment_split", DDL)
    n_bad = list(client.query(GATE_V5).result())[0][0]
    if n_bad:
        raise SystemExit(f"GATE FAILED (V5) -- {n_bad} county x specialty cells "
                         f"where shares or segment demand do not re-sum to the anchor")
    print("V5 gate OK (shares and segment demand re-sum to the anchor)")
    for label, q in CHECKS.items():
        print(f"--- {label} ---")
        for row in _run(client, label, q):
            print("  ", dict(row))


if __name__ == "__main__":
    main()


# REVIEW
# Reviewer 1 LOGIC:
#  - Demand attribution: mbr_county_cd + mbr_state_cd only (the member
#    lens); prvdr_county never read. epdb_dw_prvdr_id appears solely inside
#    the visit key and the pair-level new/returning rule.
#  - The split invents no demand (CD-17): anchor times shares, shares from
#    observed visits, V5 enforced as a hard gate.
# Reviewer 2 SPEC:
#  - Deviations = six ASSUMPTION blocks; A1 (specialty_ctg_cd vs the doc's
#    cms_specialty PK name) needs a doc ruling before module 69 freezes.
#  - Visit definition = 48's distinct member x provider x date.
# Reviewer 3 EFFICIENCY:
#  - Exactly ONE claims scan. Anchor table is county x specialty (small);
#    LEFT JOIN cannot explode (shares unique per county x spec x segment).
#    No CROSS JOINs. Relative cost ~ one claims scan.
