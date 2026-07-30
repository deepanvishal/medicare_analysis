"""
69 - two-lane proportional fill   [PYTHON runner / BigQuery DDL]

WHAT  : Deals segment-level growth demand to providers -> cap_fill_result.
        CD-24 first: facility/org ids absorb their historical market share
        (peeled ONLY where the share > 0, and the lane row is always
        emitted with it). Then TWO individual lanes (CD-25), NEW before
        RET: NEW_* = cell caps + total constraint, two passes; RET_* = no
        cell caps, panel-share split against remaining absorbing capacity.
        CONSERVATION BY CONSTRUCTION: an explicit remainder row is emitted
        for EVERY county x specialty x segment with growth_demand > 0
        (unplaced = growth - placed), so demand can never vanish into
        NULL shares, closed doors, or missing lane rows. V6 gate:
        |placed + unplaced - growth| <= 0.5 patients per cell.
        Specialty bridge applied HERE, exactly once (rule 6).
GRAIN : provider rows npi/epdb x prvdr_county x segment (per lane) +
        facility lane rows + one remainder row per growth cell
INPUTS: dem_segment_split, cap_provider_segment, cap_provider_year,
        cap_cohort_bench, cap_hours_annual, ms_ref_county,
        ref_specialty_crosswalk (cfg.base), HCC map,
        A870800_medicare_analysis_2025_claims (ONE scan - facility share)
OUTPUT: cap_fill_result (BigQuery table) + V6 gate + sanity prints.
Run   : python expanded_scope/dc_v2/08_capacity_risk/69_fill.py
"""

# ASSUMPTION [1]: fill is SAME-COUNTY (limitation 14): demand is dealt only
#   to providers whose prvdr_county + state equals the demand county.
# ASSUMPTION [2]: demand county code -> county name via ms_ref_county (LPAD
#   fips both sides); provider side matched on UPPER(county_name) + state.
# ASSUMPTION [3]: CD-24 facility market share measured on the MEMBER-county
#   lens (share of the county's observed 2025 visits, by chronic x age,
#   delivered by internal ids absent from cap_provider_year).
# ASSUMPTION [4]: dem_segment_split rows with segment_cd NULL get a
#   remainder row carrying their full growth (no mix = no shares), lane
#   NULL.
# ASSUMPTION [5]: epdb_dw_prvdr_id, specialty_ctg_cd, signal_src_cd and
#   fill_lane_cd carried in cap_fill_result; unbridged specialties keep
#   cms_specialty NULL (leakage printed, per 55's handling).
# ASSUMPTION [6]: CD-25 addendum: RET counts convert to hours via a
#   returning-patient consumption rate per county x specialty =
#   SUM(internal defl_hrs_yr 2025) / SUM(total panel_cnt) over that county
#   x specialty's providers; fallback = the overall avg hours per patient.
#   avg_first_yr_hrs applies to the NEW lane only.
# ASSUMPTION [7]: in the RET lane, a provider's leftover hours after pass 1
#   are split EQUALLY across their RET cells with a positive pool before
#   pass 2 (deterministic, no cross-segment overdraw). Zero/NULL hours
#   rates mean the budget cannot bind: the pass-1 scale coalesces to 1
#   (place, consume no hours) rather than NULL-erasing demand (V6 fix d).
# ASSUMPTION [8]: demand joins providers through cap_provider_year to
#   REQUIRE a specialty match (the matrix has no specialty column).
# ASSUMPTION [9]: V6-fix restructure: the remainder is computed by
#   FULL-covering the demand side (growth - COALESCE(placed, 0) per cell,
#   floored at 0) instead of per-lane pool arithmetic - cells with all
#   closed doors, zero panels, no providers, or NULL capacity now land in
#   unplaced instead of vanishing. Gate tolerance 0.5 patients (fix a).

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
BENCH  = cfg.table("cap_cohort_bench")
ANNUAL = cfg.table("cap_hours_annual")
CTY    = cfg.table("ref_county")
XWALK  = cfg.base("ref_specialty_crosswalk")
OUT    = cfg.table("cap_fill_result")
CLAIMS = "anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_2025_claims"
MAP    = "anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.HCC_ICD_Mapping_2025"

SAMPLE = ("AND MOD(ABS(FARM_FINGERPRINT(CAST(c.member_id AS STRING))), 100) = 0"
          if RUN_MODE == "sample" else "")

INTERNAL_YR = 2025   # capacity base year, matching module 65 A1

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
  -- fix (c): facility share peeled ONLY where > 0; its row is emitted from
  -- this same value, so peel and emission cannot diverge
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
    AND fs.facility_share > 0
  WHERE dm.segment_cd IS NOT NULL
),
prov_dim AS (
  SELECT COALESCE(npi, epdb_dw_prvdr_id) AS pid,
         npi, epdb_dw_prvdr_id, prvdr_county, prvdr_state_cd,
         specialty_ctg_cd, county_band_cd,
         COALESCE(spare_hrs, 0) + COALESCE(team_uplift_hrs, 0) AS absorbing_hrs
  FROM `{PY}`
),
bench_hrs AS (
  SELECT specialty_ctg_cd, county_band_cd, prvdr_state_cd, segment_cd,
         avg_first_yr_hrs
  FROM `{BENCH}` WHERE segment_cd != 'ALL'
),
prov_hours AS (
  SELECT COALESCE(npi, epdb_dw_prvdr_id) AS pid, prvdr_county,
         SUM(COALESCE(defl_hrs_yr, 0)) AS defl_hrs
  FROM `{ANNUAL}`
  WHERE src = 'AETNA_MA' AND period_yr = {INTERNAL_YR}
  GROUP BY 1, 2
),
prov_panel AS (
  SELECT COALESCE(npi, epdb_dw_prvdr_id) AS pid, prvdr_county,
         SUM(panel_cnt) AS panel_tot
  FROM `{MATRIX}`
  GROUP BY 1, 2
),
ret_rate AS (
  SELECT pd.prvdr_county, pd.prvdr_state_cd, pd.specialty_ctg_cd,
         SAFE_DIVIDE(SUM(ph.defl_hrs), SUM(pp.panel_tot)) AS ret_hrs
  FROM prov_dim pd
  JOIN prov_hours ph
    ON pd.pid = ph.pid
    AND COALESCE(pd.prvdr_county, '(NULL)') = COALESCE(ph.prvdr_county, '(NULL)')
  JOIN prov_panel pp
    ON pd.pid = pp.pid
    AND COALESCE(pd.prvdr_county, '(NULL)') = COALESCE(pp.prvdr_county, '(NULL)')
  GROUP BY 1, 2, 3
),
ret_rate_overall AS (
  SELECT SAFE_DIVIDE(SUM(ph.defl_hrs), SUM(pp.panel_tot)) AS ret_hrs
  FROM prov_hours ph
  JOIN prov_panel pp
    ON ph.pid = pp.pid
    AND COALESCE(ph.prvdr_county, '(NULL)') = COALESCE(pp.prvdr_county, '(NULL)')
),
cells AS (
  SELECT
    l.mbr_county_cd, l.mbr_state_cd, l.specialty_ctg_cd, l.segment_cd,
    l.rem_growth, l.facility_absorbed,
    pd.pid, pd.npi, pd.epdb_dw_prvdr_id, pd.prvdr_county, pd.prvdr_state_cd,
    COALESCE(pd.absorbing_hrs, 0)      AS absorbing_hrs,
    COALESCE(m.panel_cnt, 0)           AS panel_cnt,
    COALESCE(m.cell_cap_scaled_cnt, 0) AS cell_cap_scaled_cnt,
    m.closed_door_flag, m.signal_src_cd,
    STARTS_WITH(l.segment_cd, 'NEW')   AS is_new_seg,
    COALESCE(IF(STARTS_WITH(l.segment_cd, 'NEW'),
       COALESCE(bh.avg_first_yr_hrs,
                AVG(bh.avg_first_yr_hrs) OVER (PARTITION BY l.specialty_ctg_cd)),
       COALESCE(rr.ret_hrs, ro.ret_hrs)), 0) AS seg_hrs
  FROM laned l
  LEFT JOIN prov_dim pd
    ON UPPER(TRIM(COALESCE(l.dem_county_name, ''))) = UPPER(TRIM(pd.prvdr_county))
    AND COALESCE(l.mbr_state_cd, '') = pd.prvdr_state_cd
    AND l.specialty_ctg_cd = pd.specialty_ctg_cd
  LEFT JOIN `{MATRIX}` m
    ON pd.pid = COALESCE(m.npi, m.epdb_dw_prvdr_id)
    AND COALESCE(pd.prvdr_county, '(NULL)') = COALESCE(m.prvdr_county, '(NULL)')
    AND m.segment_cd = l.segment_cd
  LEFT JOIN bench_hrs bh
    ON pd.specialty_ctg_cd = bh.specialty_ctg_cd
    AND COALESCE(pd.county_band_cd, '') = COALESCE(bh.county_band_cd, '')
    AND pd.prvdr_state_cd = bh.prvdr_state_cd
    AND bh.segment_cd = l.segment_cd
  LEFT JOIN ret_rate rr
    ON COALESCE(pd.prvdr_county, '(NULL)') = COALESCE(rr.prvdr_county, '(NULL)')
    AND pd.prvdr_state_cd = rr.prvdr_state_cd
    AND pd.specialty_ctg_cd = rr.specialty_ctg_cd
  CROSS JOIN ret_rate_overall ro
),

-- LANE 1: NEW_* segments (cell caps + total constraint, two passes)
new_p1 AS (
  SELECT *,
    COALESCE(SAFE_DIVIDE(IF(closed_door_flag = 0, panel_cnt, 0),
      SUM(IF(closed_door_flag = 0, panel_cnt, 0)) OVER (
        PARTITION BY mbr_county_cd, mbr_state_cd, specialty_ctg_cd, segment_cd)), 0)
      AS seg_market_share
  FROM cells WHERE is_new_seg
),
new_pass AS (
  SELECT *,
    rem_growth * seg_market_share AS p1,
    LEAST(rem_growth * seg_market_share, cell_cap_scaled_cnt) AS placed1
  FROM new_p1
),
new_pooled AS (
  SELECT *,
    p1 - placed1 AS returned_cnt,
    cell_cap_scaled_cnt - placed1 AS room,
    SUM(p1 - placed1) OVER (PARTITION BY mbr_county_cd, mbr_state_cd,
                            specialty_ctg_cd, segment_cd) AS pool,
    SUM(cell_cap_scaled_cnt - placed1)
      OVER (PARTITION BY mbr_county_cd, mbr_state_cd,
            specialty_ctg_cd, segment_cd)                 AS room_total
  FROM new_pass
),
new_dealt AS (
  SELECT *,
    COALESCE(room * LEAST(1, SAFE_DIVIDE(pool, NULLIF(room_total, 0))), 0) AS p2
  FROM new_pooled
),
new_used AS (
  SELECT pid, prvdr_county,
         SUM((placed1 + p2) * seg_hrs) AS new_used_hrs
  FROM new_dealt WHERE pid IS NOT NULL
  GROUP BY 1, 2
),

-- LANE 2: RET_* segments (no cell caps; panel share; remaining budget)
ret_p1 AS (
  SELECT c.*,
    COALESCE(SAFE_DIVIDE(c.panel_cnt,
      SUM(c.panel_cnt) OVER (PARTITION BY c.mbr_county_cd, c.mbr_state_cd,
                             c.specialty_ctg_cd, c.segment_cd)), 0)
      AS seg_market_share,
    GREATEST(c.absorbing_hrs - COALESCE(nu.new_used_hrs, 0), 0) AS ret_budget_hrs
  FROM cells c
  LEFT JOIN new_used nu
    ON c.pid = nu.pid
    AND COALESCE(c.prvdr_county, '(NULL)') = COALESCE(nu.prvdr_county, '(NULL)')
  WHERE NOT c.is_new_seg
),
ret_scaled AS (
  SELECT *,
    rem_growth * seg_market_share AS p1,
    SUM(rem_growth * seg_market_share * seg_hrs)
      OVER (PARTITION BY pid, prvdr_county) AS demanded_hrs
  FROM ret_p1
),
ret_pass AS (
  -- fix (d): scale coalesces to 1 when demanded hours are 0/NULL (a zero
  -- hours rate cannot bind the budget); NULL never erases demand
  SELECT *,
    p1 * COALESCE(LEAST(1, SAFE_DIVIDE(ret_budget_hrs, NULLIF(demanded_hrs, 0))), 1)
      AS placed1
  FROM ret_scaled
),
ret_pooled AS (
  SELECT *,
    p1 - placed1 AS returned_cnt,
    SUM(p1 - placed1) OVER (PARTITION BY mbr_county_cd, mbr_state_cd,
                            specialty_ctg_cd, segment_cd) AS pool,
    GREATEST(ret_budget_hrs - SUM(placed1 * seg_hrs)
      OVER (PARTITION BY pid, prvdr_county), 0)           AS leftover_hrs
  FROM ret_pass
),
ret_cells_n AS (
  SELECT *,
    SUM(IF(pool > 0, 1, 0)) OVER (PARTITION BY pid, prvdr_county) AS n_pool_cells
  FROM ret_pooled
),
ret_room AS (
  SELECT *,
    COALESCE(IF(pool > 0,
       SAFE_DIVIDE(SAFE_DIVIDE(leftover_hrs, NULLIF(n_pool_cells, 0)),
                   NULLIF(seg_hrs, 0)), 0), 0) AS room
  FROM ret_cells_n
),
ret_dealt AS (
  SELECT *,
    COALESCE(room * LEAST(1, SAFE_DIVIDE(pool,
      NULLIF(SUM(room) OVER (PARTITION BY mbr_county_cd, mbr_state_cd,
                             specialty_ctg_cd, segment_cd), 0))), 0) AS p2
  FROM ret_room
),

alloc_rows AS (
  SELECT mbr_county_cd, mbr_state_cd, specialty_ctg_cd, segment_cd,
         npi, epdb_dw_prvdr_id, prvdr_county, prvdr_state_cd,
         CAST(NULL AS STRING) AS absorbed_by, 'NEW_INTAKE' AS fill_lane_cd,
         seg_market_share, p1 AS pass1_alloc_cnt, returned_cnt,
         p2 AS pass2_alloc_cnt,
         placed1 + p2 AS placed_cnt,
         CAST(NULL AS FLOAT64) AS unplaced_cnt, signal_src_cd
  FROM new_dealt WHERE pid IS NOT NULL
  UNION ALL
  SELECT mbr_county_cd, mbr_state_cd, specialty_ctg_cd, segment_cd,
         npi, epdb_dw_prvdr_id, prvdr_county, prvdr_state_cd,
         CAST(NULL AS STRING), 'RET_PANEL',
         seg_market_share, p1, returned_cnt, p2,
         placed1 + p2,
         CAST(NULL AS FLOAT64), signal_src_cd
  FROM ret_dealt WHERE pid IS NOT NULL
  UNION ALL
  SELECT mbr_county_cd, mbr_state_cd, specialty_ctg_cd, segment_cd,
         CAST(NULL AS STRING), CAST(NULL AS STRING),
         CAST(NULL AS STRING), CAST(NULL AS STRING),
         'FACILITY', 'FACILITY',
         CAST(NULL AS FLOAT64),
         CAST(NULL AS FLOAT64), CAST(NULL AS FLOAT64), CAST(NULL AS FLOAT64),
         facility_absorbed, CAST(NULL AS FLOAT64), CAST(NULL AS STRING)
  FROM (SELECT DISTINCT mbr_county_cd, mbr_state_cd, specialty_ctg_cd,
               segment_cd, facility_absorbed FROM laned)
  WHERE facility_absorbed > 0
),
placed_by_cell AS (
  SELECT mbr_county_cd, mbr_state_cd, specialty_ctg_cd, segment_cd,
         SUM(COALESCE(placed_cnt, 0)) AS placed_total
  FROM alloc_rows
  GROUP BY 1, 2, 3, 4
),
remainder_rows AS (
  -- fix (b): one remainder row for EVERY growth cell, even with zero
  -- provider/facility rows - conservation by construction (A9)
  SELECT d.mbr_county_cd, d.mbr_state_cd, d.specialty_ctg_cd, d.segment_cd,
         CAST(NULL AS STRING), CAST(NULL AS STRING),
         CAST(NULL AS STRING), CAST(NULL AS STRING),
         CAST(NULL AS STRING),
         CASE WHEN d.segment_cd IS NULL THEN CAST(NULL AS STRING)
              WHEN STARTS_WITH(d.segment_cd, 'NEW') THEN 'NEW_INTAKE'
              ELSE 'RET_PANEL' END,
         CAST(NULL AS FLOAT64),
         CAST(NULL AS FLOAT64), CAST(NULL AS FLOAT64), CAST(NULL AS FLOAT64),
         CAST(NULL AS FLOAT64),
         GREATEST(d.growth_demand - COALESCE(p.placed_total, 0), 0),
         CAST(NULL AS STRING)
  FROM `{DEM}` d
  LEFT JOIN placed_by_cell p
    ON d.mbr_county_cd = p.mbr_county_cd
    AND COALESCE(d.mbr_state_cd, '') = COALESCE(p.mbr_state_cd, '')
    AND d.specialty_ctg_cd = p.specialty_ctg_cd
    AND COALESCE(d.segment_cd, 'X') = COALESCE(p.segment_cd, 'X')
  WHERE d.growth_demand > 0
),
unioned AS (
  SELECT * FROM alloc_rows
  UNION ALL SELECT * FROM remainder_rows
)
SELECT
  u.mbr_county_cd, u.mbr_state_cd,
  x.cms_specialty,
  u.specialty_ctg_cd,
  u.segment_cd, u.npi, u.epdb_dw_prvdr_id, u.prvdr_county, u.prvdr_state_cd,
  u.absorbed_by, u.fill_lane_cd, u.seg_market_share,
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
  AND ABS(s.accounted - d.growth_demand) > 0.5
"""

_DIAG_CTES = f"""
WITH cell AS (
  SELECT d.mbr_county_cd, d.specialty_ctg_cd, d.segment_cd, d.growth_demand,
         SUM(COALESCE(f.placed_cnt, 0))   AS placed,
         SUM(COALESCE(f.unplaced_cnt, 0)) AS unplaced,
         SUM(IF(f.absorbed_by = 'FACILITY', COALESCE(f.placed_cnt, 0), 0)) AS fac_placed,
         COUNT(DISTINCT IF(f.seg_market_share > 0,
               COALESCE(f.npi, f.epdb_dw_prvdr_id), NULL)) AS open_provs,
         COUNTIF(f.absorbed_by IS NULL
                 AND (f.npi IS NOT NULL OR f.epdb_dw_prvdr_id IS NOT NULL)
                 AND f.placed_cnt IS NULL) AS null_placed_rows
  FROM `{DEM}` d
  LEFT JOIN `{OUT}` f
    ON d.mbr_county_cd = f.mbr_county_cd
    AND d.specialty_ctg_cd = f.specialty_ctg_cd
    AND COALESCE(d.segment_cd, 'X') = COALESCE(f.segment_cd, 'X')
  WHERE d.growth_demand > 0
  GROUP BY 1, 2, 3, 4
),
failing AS (
  SELECT *, placed + unplaced - growth_demand AS gap
  FROM cell
  WHERE ABS(placed + unplaced - growth_demand)
        > 0.000001 * GREATEST(growth_demand, 1)
),
bucketed AS (
  SELECT *,
    CASE
      WHEN ABS(gap) < 0.5 THEN 'a_fp_noise'
      WHEN open_provs = 0 THEN 'b_zero_open_providers'
      WHEN fac_placed = 0 AND placed > 0 AND unplaced = 0 THEN 'c_missing_lane_row'
      WHEN null_placed_rows > 0 THEN 'd_null_capacity'
      ELSE 'e_other'
    END AS bucket
  FROM failing
)
"""

DIAG_BUCKETS = _DIAG_CTES + """
SELECT bucket, COUNT(*) AS n, ROUND(SUM(ABS(gap)), 1) AS gap_volume
FROM bucketed GROUP BY bucket ORDER BY bucket
"""

DIAG_EXAMPLES = _DIAG_CTES + """
SELECT mbr_county_cd, specialty_ctg_cd, segment_cd,
       ROUND(growth_demand, 2) AS growth, ROUND(placed, 2) AS placed,
       ROUND(unplaced, 2) AS unplaced, ROUND(gap, 2) AS gap, bucket
FROM bucketed WHERE bucket = 'e_other' LIMIT 5
"""

CHECKS = {
    "rows / volume by fill_lane_cd (CD-25)":
        f"SELECT COALESCE(fill_lane_cd, '(unsplit)') AS lane, COUNT(*) AS n, "
        f"ROUND(SUM(COALESCE(placed_cnt, 0)), 0) AS placed, "
        f"ROUND(SUM(COALESCE(unplaced_cnt, 0)), 0) AS unplaced "
        f"FROM `{OUT}` GROUP BY 1 ORDER BY 1",
    "RET lane sanity: unplaced share (was 100% pre-CD-25 by artifact)":
        f"SELECT ROUND(SAFE_DIVIDE(SUM(COALESCE(unplaced_cnt, 0)), "
        f"SUM(COALESCE(placed_cnt, 0) + COALESCE(unplaced_cnt, 0))), 4) AS ret_unplaced_pct "
        f"FROM `{OUT}` WHERE fill_lane_cd = 'RET_PANEL'",
    "facility lane share of growth (CD-24)":
        f"SELECT ROUND(SAFE_DIVIDE(SUM(IF(absorbed_by = 'FACILITY', placed_cnt, 0)), "
        f"SUM(COALESCE(placed_cnt, 0) + COALESCE(unplaced_cnt, 0))), 4) AS fac_share "
        f"FROM `{OUT}`",
    "specialty bridge leakage (rule 6)":
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

    print("--- V6 diagnostic (exact-test buckets; gate itself allows 0.5) ---")
    buckets = {}
    for row in _run(client, "conservation diagnostic", DIAG_BUCKETS):
        r = dict(row)
        buckets[r["bucket"]] = r["n"]
        print("  ", r)
    if buckets.get("e_other", 0):
        print("--- 5 example e_other cells ---")
        for row in _run(client, "conservation examples", DIAG_EXAMPLES):
            print("  ", dict(row))

    n_bad = list(client.query(GATE_V6).result())[0][0]
    if n_bad:
        raise SystemExit(f"GATE FAILED (V6) -- |placed + unplaced - growth| > 0.5 "
                         f"in {n_bad} county x specialty x segment cells")
    print("V6 gate OK (placed + facility + unplaced = growth within 0.5 everywhere)")
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
#  - V6 root causes closed: (1) cells whose providers all had NULL/zero
#    shares (all closed doors, zero panels) placed nothing AND emitted no
#    remainder - the old remainder only fired when NO provider row matched;
#    (2) RET NULL propagation (demanded_hrs 0 -> scale NULL -> placed
#    NULL) erased demand. Remainder is now demand-side complete (A9) and
#    every allocation term is COALESCEd - placed_cnt cannot be NULL on
#    provider rows.
#  - Both county roles meet ONLY here (rule 1); rule 12 on both sides;
#    lane order NEW -> RET enforced via new_used_hrs.
#  - Facility peel and its lane row derive from the same laned value with
#    the same > 0 predicate (fix c) - they cannot diverge.
# Reviewer 2 SPEC:
#  - Deviations = nine ASSUMPTION blocks; A9 records the V6 restructure
#    and the 0.5-patient gate tolerance (fix a, per instruction).
#  - Bridge applied exactly once (rule 6); conservation_ok_flag set only
#    after the gate passes.
# Reviewer 3 EFFICIENCY:
#  - Exactly ONE claims scan (facility share). placed_by_cell aggregates
#    alloc_rows once; remainder join is cell-keyed. The only CROSS JOIN is
#    the 1-row overall-rate fallback. Relative cost ~ one claims scan.
