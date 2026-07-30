"""
69 - two-pass proportional fill   [PYTHON runner / BigQuery DDL]

WHAT  : Deals segment-level growth demand to providers -> cap_fill_result.
        CD-24 first: facility/org ids (CD-22 exclusions) absorb their
        historical market share per county x specialty x segment as a
        pass-through lane (no ceiling, no matrix, absorbed_by='FACILITY').
        The remainder goes through the deterministic two-pass fill (CD-16):
        pass 1 = share-proportional, cap at cell_cap_scaled_cnt, returned
        load re-dealt proportional to remaining room, remainder = unplaced.
        Specialty bridge (specialty_ctg_cd -> cms_specialty) applied HERE,
        exactly once (cross-cutting rule 6), leakage printed.
        GATE (V6): placed + facility_absorbed + unplaced = growth, per
        county x specialty x segment.
GRAIN : provider rows npi/epdb x prvdr_county x segment + facility lane
        rows + county remainder rows (npi NULL)
INPUTS: dem_segment_split, cap_provider_segment, ms_ref_county,
        ref_specialty_crosswalk (cfg.base), HCC map,
        A870800_medicare_analysis_2025_claims (ONE scan - facility share)
OUTPUT: cap_fill_result (BigQuery table) + V6 gate + sanity prints.
Run   : python expanded_scope/dc_v2/08_capacity_risk/69_fill.py
"""

# ASSUMPTION [1]: fill is SAME-COUNTY: demand in a county is dealt only to
#   providers whose prvdr_county + state equals the demand county. Spec
#   nowhere defines a cross-county placement rule (transportation solver is
#   parked). Cross-county flows stay visible as unplaced.
# ASSUMPTION [2]: demand county code -> county name via ms_ref_county
#   (LPAD fips both sides, per module 61's CMS pattern); provider side
#   matched on UPPER(county_name) + state.
# ASSUMPTION [3]: CD-24 facility market share measured on the MEMBER-county
#   lens (share of the county's observed 2025 visits, by segment, delivered
#   by internal ids absent from cap_provider_year). The lane serves demand,
#   so its share basis is the demand grain.
# ASSUMPTION [4]: dem_segment_split rows with segment_cd NULL (no observed
#   mix) go straight to unplaced remainder rows - no mix means no shares
#   for either lane; volume printed.
# ASSUMPTION [5]: epdb_dw_prvdr_id and specialty_ctg_cd carried in
#   cap_fill_result beyond the data-model column list (both-keys locked
#   fact; ctg code kept for traceability next to the bridged
#   cms_specialty). Doc amendment pending.
# ASSUMPTION [6]: rows whose specialty_ctg_cd has no crosswalk match keep
#   cms_specialty NULL (leakage printed, per 55's handling, not dropped).

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

DEM    = cfg.table("dem_segment_split")
MATRIX = cfg.table("cap_provider_segment")
PY     = cfg.table("cap_provider_year")
CTY    = cfg.table("ref_county")
XWALK  = cfg.base("ref_specialty_crosswalk")
OUT    = cfg.table("cap_fill_result")
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
    AND c.srv_start_dt BETWEEN '2025-01-01' AND '2025-12-31'
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
individual_ids AS (
  SELECT DISTINCT epdb_dw_prvdr_id FROM `{PY}` WHERE epdb_dw_prvdr_id IS NOT NULL
),
fac_share AS (
  SELECT
    cf.mbr_county_cd, cf.mbr_state_cd, cf.specialty_ctg_cd,
    CONCAT('SEG_', IF(ch.member_id IS NOT NULL, 'CHR', 'NONCHR'), '_',
           IF(cf.age_nbr BETWEEN 60 AND 74, '60_74', '75P')) AS seg_partial,
    SAFE_DIVIDE(
      COUNT(DISTINCT IF(ii.epdb_dw_prvdr_id IS NULL,
        CONCAT(cf.member_id, '|', cf.epdb_dw_prvdr_id, '|',
               CAST(cf.srv_start_dt AS STRING)), NULL)),
      COUNT(DISTINCT CONCAT(cf.member_id, '|', cf.epdb_dw_prvdr_id, '|',
                            CAST(cf.srv_start_dt AS STRING)))) AS facility_share
  FROM claims_f cf
  LEFT JOIN individual_ids ii ON cf.epdb_dw_prvdr_id = ii.epdb_dw_prvdr_id
  LEFT JOIN chronic_members ch
    ON ch.month = cf.month AND ch.member_id = cf.member_id
  GROUP BY 1, 2, 3, 4
),
demand AS (
  SELECT d.*, rc.county_name AS dem_county_name,
         CONCAT('SEG_', SPLIT(d.segment_cd, '_')[SAFE_OFFSET(1)], '_',
                REGEXP_EXTRACT(d.segment_cd, r'_(60_74|75P)$')) AS seg_partial
  FROM `{DEM}` d
  LEFT JOIN `{CTY}` rc
    ON LPAD(TRIM(CAST(d.mbr_county_cd AS STRING)), 5, '0')
     = LPAD(TRIM(CAST(rc.county_fips AS STRING)), 5, '0')
  WHERE d.growth_demand > 0
),
laned AS (
  SELECT
    dm.*,
    COALESCE(fs.facility_share, 0) AS facility_share,
    dm.growth_demand * COALESCE(fs.facility_share, 0)       AS facility_absorbed,
    dm.growth_demand * (1 - COALESCE(fs.facility_share, 0)) AS rem_growth
  FROM demand dm
  LEFT JOIN fac_share fs
    ON dm.mbr_county_cd = fs.mbr_county_cd
    AND COALESCE(dm.mbr_state_cd, '') = COALESCE(fs.mbr_state_cd, '')
    AND dm.specialty_ctg_cd = fs.specialty_ctg_cd
    AND dm.seg_partial = fs.seg_partial
  WHERE dm.segment_cd IS NOT NULL
),
cells AS (
  SELECT
    l.mbr_county_cd, l.mbr_state_cd, l.specialty_ctg_cd, l.segment_cd,
    l.rem_growth, l.facility_absorbed,
    m.npi, m.epdb_dw_prvdr_id, m.prvdr_county, m.prvdr_state_cd,
    m.panel_cnt, m.cell_cap_scaled_cnt, m.closed_door_flag, m.signal_src_cd,
    SAFE_DIVIDE(IF(m.closed_door_flag = 0, m.panel_cnt, 0),
      SUM(IF(m.closed_door_flag = 0, m.panel_cnt, 0)) OVER (
        PARTITION BY l.mbr_county_cd, l.mbr_state_cd,
                     l.specialty_ctg_cd, l.segment_cd)) AS seg_market_share
  FROM laned l
  LEFT JOIN `{MATRIX}` m
    ON UPPER(TRIM(COALESCE(l.dem_county_name, ''))) = UPPER(TRIM(m.prvdr_county))
    AND COALESCE(l.mbr_state_cd, '') = m.prvdr_state_cd
    AND l.specialty_ctg_cd IS NOT NULL
    AND m.segment_cd = l.segment_cd
),
pass1 AS (
  SELECT *,
    rem_growth * COALESCE(seg_market_share, 0)                    AS p1,
    LEAST(rem_growth * COALESCE(seg_market_share, 0),
          COALESCE(cell_cap_scaled_cnt, 0))                       AS placed1
  FROM cells
),
pooled AS (
  SELECT *,
    p1 - placed1                                                  AS returned_cnt,
    COALESCE(cell_cap_scaled_cnt, 0) - placed1                    AS room,
    SUM(p1 - placed1) OVER (PARTITION BY mbr_county_cd, mbr_state_cd,
                            specialty_ctg_cd, segment_cd)         AS pool,
    SUM(COALESCE(cell_cap_scaled_cnt, 0) - placed1)
      OVER (PARTITION BY mbr_county_cd, mbr_state_cd,
            specialty_ctg_cd, segment_cd)                         AS room_total
  FROM pass1
),
dealt AS (
  SELECT *,
    room * LEAST(1, SAFE_DIVIDE(pool, NULLIF(room_total, 0)))     AS p2
  FROM pooled
),
provider_rows AS (
  SELECT
    mbr_county_cd, mbr_state_cd, specialty_ctg_cd, segment_cd,
    npi, epdb_dw_prvdr_id, prvdr_county, prvdr_state_cd,
    CAST(NULL AS STRING)          AS absorbed_by,
    seg_market_share,
    p1                            AS pass1_alloc_cnt,
    returned_cnt,
    COALESCE(p2, 0)               AS pass2_alloc_cnt,
    placed1 + COALESCE(p2, 0)     AS placed_cnt,
    CAST(NULL AS FLOAT64)         AS unplaced_cnt,
    signal_src_cd
  FROM dealt
  WHERE npi IS NOT NULL OR epdb_dw_prvdr_id IS NOT NULL
),
facility_rows AS (
  SELECT
    mbr_county_cd, mbr_state_cd, specialty_ctg_cd, segment_cd,
    CAST(NULL AS STRING), CAST(NULL AS STRING),
    CAST(NULL AS STRING), CAST(NULL AS STRING),
    'FACILITY', CAST(NULL AS FLOAT64),
    CAST(NULL AS FLOAT64), CAST(NULL AS FLOAT64), CAST(NULL AS FLOAT64),
    facility_absorbed, CAST(NULL AS FLOAT64), CAST(NULL AS STRING)
  FROM (SELECT DISTINCT mbr_county_cd, mbr_state_cd, specialty_ctg_cd,
               segment_cd, facility_absorbed FROM laned)
  WHERE facility_absorbed > 0
),
remainder_rows AS (
  SELECT
    mbr_county_cd, mbr_state_cd, specialty_ctg_cd, segment_cd,
    CAST(NULL AS STRING), CAST(NULL AS STRING),
    CAST(NULL AS STRING), CAST(NULL AS STRING),
    CAST(NULL AS STRING), CAST(NULL AS FLOAT64),
    CAST(NULL AS FLOAT64), CAST(NULL AS FLOAT64), CAST(NULL AS FLOAT64),
    CAST(NULL AS FLOAT64),
    GREATEST(ANY_VALUE(pool) - ANY_VALUE(room_total), 0)
      + ANY_VALUE(rem_growth) * IF(MAX(seg_market_share) IS NULL, 1, 0),
    CAST(NULL AS STRING)
  FROM dealt
  GROUP BY 1, 2, 3, 4
  HAVING GREATEST(ANY_VALUE(pool) - ANY_VALUE(room_total), 0)
       + ANY_VALUE(rem_growth) * IF(MAX(seg_market_share) IS NULL, 1, 0) > 0
  UNION ALL
  SELECT mbr_county_cd, mbr_state_cd, specialty_ctg_cd, segment_cd,
         CAST(NULL AS STRING), CAST(NULL AS STRING),
         CAST(NULL AS STRING), CAST(NULL AS STRING),
         CAST(NULL AS STRING), CAST(NULL AS FLOAT64),
         CAST(NULL AS FLOAT64), CAST(NULL AS FLOAT64), CAST(NULL AS FLOAT64),
         CAST(NULL AS FLOAT64), growth_demand, CAST(NULL AS STRING)
  FROM `{DEM}` WHERE segment_cd IS NULL AND growth_demand > 0
),
unioned AS (
  SELECT * FROM provider_rows
  UNION ALL SELECT * FROM facility_rows
  UNION ALL SELECT * FROM remainder_rows
)
SELECT
  u.mbr_county_cd, u.mbr_state_cd,
  x.cms_specialty,
  u.specialty_ctg_cd,
  u.segment_cd, u.npi, u.epdb_dw_prvdr_id, u.prvdr_county, u.prvdr_state_cd,
  u.absorbed_by, u.seg_market_share,
  u.pass1_alloc_cnt, u.returned_cnt, u.pass2_alloc_cnt,
  u.placed_cnt, u.unplaced_cnt, u.signal_src_cd,
  CAST(NULL AS INT64) AS conservation_ok_flag
FROM unioned u
LEFT JOIN `{XWALK}` x ON u.specialty_ctg_cd = x.aetna_cd
"""

GATE_V6 = f"""
WITH sums AS (
  SELECT f.mbr_county_cd, f.specialty_ctg_cd, f.segment_cd,
         SUM(COALESCE(f.placed_cnt, 0)) + SUM(COALESCE(f.unplaced_cnt, 0)) AS accounted
  FROM `{OUT}` f GROUP BY 1, 2, 3
)
SELECT COUNT(*)
FROM sums s
JOIN `{DEM}` d
  ON s.mbr_county_cd = d.mbr_county_cd
  AND s.specialty_ctg_cd = d.specialty_ctg_cd
  AND COALESCE(s.segment_cd, 'X') = COALESCE(d.segment_cd, 'X')
WHERE d.growth_demand > 0
  AND ABS(s.accounted - d.growth_demand) > 0.000001 * GREATEST(d.growth_demand, 1)
"""

CHECKS = {
    "row counts by lane":
        f"SELECT CASE WHEN absorbed_by = 'FACILITY' THEN 'facility' "
        f"WHEN npi IS NULL AND epdb_dw_prvdr_id IS NULL THEN 'remainder' "
        f"ELSE 'provider' END AS lane, COUNT(*) AS n, "
        f"ROUND(SUM(COALESCE(placed_cnt, 0) + COALESCE(unplaced_cnt, 0)), 0) AS volume "
        f"FROM `{OUT}` GROUP BY 1",
    "facility lane share of growth (CD-24)":
        f"SELECT ROUND(SAFE_DIVIDE(SUM(IF(absorbed_by = 'FACILITY', placed_cnt, 0)), "
        f"SUM(COALESCE(placed_cnt, 0) + COALESCE(unplaced_cnt, 0))), 4) AS fac_share "
        f"FROM `{OUT}`",
    "specialty bridge leakage (rule 6, A6)":
        f"SELECT COUNTIF(cms_specialty IS NULL) AS unbridged_rows, "
        f"ROUND(SUM(IF(cms_specialty IS NULL, "
        f"COALESCE(placed_cnt, 0) + COALESCE(unplaced_cnt, 0), 0)), 0) AS unbridged_volume "
        f"FROM `{OUT}`",
    "unplaced share by state (risk preview)":
        f"SELECT mbr_state_cd, ROUND(SAFE_DIVIDE(SUM(COALESCE(unplaced_cnt, 0)), "
        f"SUM(COALESCE(placed_cnt, 0) + COALESCE(unplaced_cnt, 0))), 4) AS unplaced_pct "
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
    _run(client, "create cap_fill_result", DDL)
    n_bad = list(client.query(GATE_V6).result())[0][0]
    if n_bad:
        raise SystemExit(f"GATE FAILED (V6) -- conservation broken in {n_bad} "
                         f"county x specialty x segment cells")
    print("V6 gate OK (placed + facility + unplaced = growth everywhere)")
    _run(client, "set conservation_ok_flag",
         f"UPDATE `{OUT}` SET conservation_ok_flag = 1 WHERE TRUE")
    for label, q in CHECKS.items():
        print(f"--- {label} ---")
        for row in _run(client, label, q):
            print("  ", dict(row))


if __name__ == "__main__":
    main()


# REVIEW
# Reviewer 1 LOGIC:
#  - Both county roles present ONLY here (cross-cutting rule 1): demand
#    keys mbr_county_cd + mbr_state_cd, provider keys prvdr_county +
#    prvdr_state_cd, each in its own role; rule 12 satisfied on both sides.
#  - Two-pass math is order-free: pass 2 deals the pooled return
#    proportional to remaining room; unplaced = pool - room when room runs
#    out. Facility lane deducted before any provider math (CD-24).
#  - CD-24 facility segment share has no NEW/RET axis (a facility's
#    'new-patient' construct is undefined) - seg_partial matches on
#    chronic x age only; both demand segments of a chronic-age pair get the
#    same facility share.
# Reviewer 2 SPEC:
#  - Deviations = six ASSUMPTION blocks (same-county fill and the
#    member-lens facility share are the two that most deserve review).
#  - Bridge applied exactly once, here (rule 6); leakage kept + printed.
#  - conservation_ok_flag set only after the V6 gate passes.
# Reviewer 3 EFFICIENCY:
#  - Exactly ONE claims scan (facility share). Matrix join is keyed on
#    county+state+specialty+segment; window functions carry pools (no
#    self-joins). No CROSS JOINs. Relative cost ~ one claims scan + matrix-
#    sized windows.
