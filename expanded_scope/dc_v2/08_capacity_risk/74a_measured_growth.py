"""
74a - measured growth scenarios (export lane)   [PYTHON runner / BigQuery DDL]

WHAT  : EXPORT lane for the Excel/HTML exports (73/74). Modules 59-72 and
        the dashboard's forecast/slider path are untouched.
        STEP 1: state growth measured from ENROLLMENT - distinct members
        per state 2024 vs 2025 from A870800_medicare_analysis_membership
        (the EMIS_MEMBERSHIP extract, member x month grain, DD-08 - the
        membership source notebooks 46/48/53/55 read). NOT claims
        utilizers. g(state) = members_2025 / members_2024 - 1, persisted
        to cap_growth_measured (per state + ALL_FOOTPRINT rollup row) so
        the export reports (73/74) read the measured rates from BQ.
        STEP 2: cap_scenario_input - dem_segment_split baseline x three
        frozen scenarios: growth_demand = segment_demand x g_applied,
        g_applied = max(g + delta, 0), delta in (-0.02, 0, +0.02),
        scenario_cd in ('G_MINUS2','G_BASE','G_PLUS2'),
        growth_src_cd = 'MEASURED'. dem_segment_split is NOT modified.
        STEP 3: cap_scenario_results - module 69's fill replicated exactly
        (same CTE chain: facility peel, NEW lane with cell caps + total
        constraint + two passes, RET lane on panel shares vs remaining
        budget, deduped specialty bridge), run over all three scenarios in
        one pass (scenario_cd added to every window partition and join).
        Conservation gate per scenario - STOP on failure.
        STEP 4: cap_county_drivers - G_BASE unplaced decomposed by cause,
        priority NO_PROVIDERS / DOORS_CLOSED / AT_CAPACITY, with the
        county-level PAPER_NETWORK count as a context column.
        STEP 5: cap_action_lists - per county: top 15 providers by
        remaining room, all contracted zero-claim, all at-capacity.
GRAIN : cap_growth_measured  state_cd (4 states + 'ALL_FOOTPRINT')
        cap_scenario_input   scenario_cd x mbr_county_cd + mbr_state_cd x
                             specialty_ctg_cd x segment_cd
        cap_scenario_results row_type_cd 'CELL' = scenario x demand cell
                             (growth/facility/placed/unplaced); 'ALLOC' =
                             scenario x provider x segment x lane rows
        cap_county_drivers   mbr_state_cd x mbr_county_cd x cms_specialty
        cap_action_lists     list_cd x prvdr_state_cd x prvdr_county x
                             provider
INPUTS: A870800_medicare_analysis_membership, dem_segment_split,
        cap_provider_segment, cap_provider_year, cap_cohort_bench,
        cap_hours_annual, cap_willing, ms_ref_county,
        ref_specialty_crosswalk (cfg.base), HCC map,
        A870800_medicare_analysis_2025_claims (ONE scan - facility share)
OUTPUT: cap_growth_measured, cap_scenario_input, cap_scenario_results,
        cap_county_drivers, cap_action_lists (BigQuery tables) + gates +
        sanity prints.
Run   : python expanded_scope/dc_v2/08_capacity_risk/74a_measured_growth.py
"""

# ASSUMPTION [1]: the true enrollment source is
#   A870800_medicare_analysis_membership (EMIS_MEMBERSHIP extract,
#   member x month, DD-08) - the membership table the dc_v2 foundation
#   notebooks (46/48/53/55) actually read. g uses distinct member_id per
#   eff_yr (2024 vs 2025), age_nbr >= 60 (the demand scope of 46/68),
#   state = UPPER(LEFT(mbr_submarket, 2)) - the locked fact, so the join
#   key matches dem_segment_split.mbr_state_cd exactly. Members with NULL
#   submarket fall outside the state rates (counted in the print).
# ASSUMPTION [2]: dem_segment_split rows whose mbr_state_cd is NULL (kept
#   by 68 A5) take the footprint-overall g as fallback (g_fallback_flag=1,
#   volume printed) - dropping them would silently shrink the scenarios.
# ASSUMPTION [3]: cap_scenario_results carries two row types instead of
#   69's remainder-row shape: 'CELL' rows (one per input growth cell per
#   scenario, with growth/facility/placed/unplaced - the export grain) and
#   'ALLOC' provider rows (both individual lanes, with 69's alloc columns).
#   The facility peel is identical to 69 but surfaces as the CELL row's
#   facility_absorbed_cnt instead of a keyless lane row. Conservation by
#   construction is preserved: a CELL row is emitted for EVERY input cell
#   with growth > 0, unplaced = growth - placed - facility, floor 0.
# ASSUMPTION [4]: physical specialty grain stays specialty_ctg_cd (the
#   demand grain, 68 ruling); bridged cms_specialty is carried on every row
#   via the deduped crosswalk (MIN per aetna_cd - 69 A5), NULL kept where
#   unbridged (leakage printed). Exports GROUP BY cms_specialty.
# ASSUMPTION [5]: ALLOC placed_hrs = (placed1 + p2) x seg_hrs, the SAME
#   hour rates 69 uses (NEW: cohort avg_first_yr_hrs with specialty-avg
#   fallback; RET: county x specialty consumption rate with overall
#   fallback) - the basis for remaining room in step 5.
# ASSUMPTION [6]: driver cause per demand cell, priority order per the
#   instruction: NO_PROVIDERS = no individual provider row matched the cell
#   (includes 68's segment-NULL unsplit cells - no observed mix means no
#   in-county providers to deal to); DOORS_CLOSED = provider rows exist but
#   none has seg_market_share > 0 (NEW: all doors closed / zero open-door
#   panels; RET: zero panels); AT_CAPACITY = an open share basis existed,
#   caps or budget bound. Cells with unplaced = 0 contribute nothing.
# ASSUMPTION [7]: PAPER_NETWORK = cap_willing.zero_utilization_flag = 1
#   (contracted, zero Aetna MA claims, CD-07), counted per provider county
#   + state and repeated per specialty row as a context column; provider
#   county matched to the demand county by name + state (69 A2 pattern).
# ASSUMPTION [8]: AT_CAPACITY list rule mirrors 71 A1 minus the matrix
#   cap-hit condition (kept: remaining absorbing budget <= 1% after G_BASE
#   placements, or absorbing = 0, or any NEW cell overflowed in pass 1);
#   CD-21 NULL-budget providers are excluded - unmodeled, not maxed.
# ASSUMPTION [9]: RUN_MODE ('sample') applies to the single claims scan
#   (facility share) only, per R2. The enrollment g always uses the full
#   membership table - it is cheap and sampling it would distort the rates
#   the scenarios are built from.

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
WILL   = cfg.table("cap_willing")
CTY    = cfg.table("ref_county")
XWALK  = cfg.base("ref_specialty_crosswalk")
GROWTH = cfg.table("cap_growth_measured")
IN_T   = cfg.table("cap_scenario_input")
RES    = cfg.table("cap_scenario_results")
DRV    = cfg.table("cap_county_drivers")
ACT    = cfg.table("cap_action_lists")
CLAIMS = "anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_2025_claims"
MAP    = "anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.HCC_ICD_Mapping_2025"
MBRSHP = "anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_membership"

SAMPLE = ("AND MOD(ABS(FARM_FINGERPRINT(CAST(c.member_id AS STRING))), 100) = 0"
          if RUN_MODE == "sample" else "")

INTERNAL_YR = 2025   # capacity base year, matching modules 65/69

STATES_SQL = cfg.state_abbr_sql()

# ---------------------------------------------------------------- STEP 1
# Enrollment counts and measured growth -> cap_growth_measured (persisted
# for the 73/74 export reports), printed + gated in main().
# ROLLUP row = 'ALL_FOOTPRINT' (overall; also the A2 fallback rate).

DDL_GROWTH = f"""
CREATE OR REPLACE TABLE `{GROWTH}`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
WITH mbr AS (
  SELECT UPPER(LEFT(m.mbr_submarket, 2)) AS state_cd,
         m.member_id,
         CAST(m.eff_yr AS INT64)         AS eff_yr
  FROM `{MBRSHP}` m
  WHERE m.age_nbr >= 60
    AND CAST(m.eff_yr AS INT64) IN (2024, 2025)
)
SELECT
  IFNULL(state_cd, 'ALL_FOOTPRINT') AS state_cd,
  COUNT(DISTINCT IF(eff_yr = 2024, member_id, NULL)) AS members_2024,
  COUNT(DISTINCT IF(eff_yr = 2025, member_id, NULL)) AS members_2025,
  SAFE_DIVIDE(COUNT(DISTINCT IF(eff_yr = 2025, member_id, NULL)),
              COUNT(DISTINCT IF(eff_yr = 2024, member_id, NULL))) - 1
    AS g_state,
  CURRENT_TIMESTAMP() AS load_ts
FROM mbr
WHERE state_cd IN {STATES_SQL}
GROUP BY ROLLUP(state_cd)
"""

ENROLLMENT = f"""
SELECT state_cd, members_2024, members_2025, ROUND(g_state, 4) AS g_state
FROM `{GROWTH}`
ORDER BY state_cd
"""

NULL_SUBMKT = f"""
SELECT COUNT(DISTINCT member_id) AS members_no_submarket
FROM `{MBRSHP}`
WHERE age_nbr >= 60
  AND CAST(eff_yr AS INT64) IN (2024, 2025)
  AND (mbr_submarket IS NULL OR UPPER(LEFT(mbr_submarket, 2)) NOT IN {STATES_SQL})
"""

# ---------------------------------------------------------------- STEP 2

DDL_INPUT = f"""
CREATE OR REPLACE TABLE `{IN_T}`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
WITH g_state AS (
  SELECT state_cd, g_state AS g
  FROM `{GROWTH}` WHERE state_cd != 'ALL_FOOTPRINT'
),
g_overall AS (
  SELECT g_state AS g
  FROM `{GROWTH}` WHERE state_cd = 'ALL_FOOTPRINT'
),
scenarios AS (
  SELECT 'G_MINUS2' AS scenario_cd, -0.02 AS g_delta UNION ALL
  SELECT 'G_BASE', 0.0 UNION ALL
  SELECT 'G_PLUS2', 0.02
)
SELECT
  s.scenario_cd,
  d.mbr_county_cd,
  d.mbr_state_cd,
  d.specialty_ctg_cd,
  d.segment_cd,
  d.anchor_demand,
  d.segment_share,
  d.segment_demand,
  COALESCE(gs.g, go.g)                                      AS g_state,
  IF(gs.g IS NULL, 1, 0)                                    AS g_fallback_flag,
  GREATEST(COALESCE(gs.g, go.g) + s.g_delta, 0)             AS g_applied,
  d.segment_demand * GREATEST(COALESCE(gs.g, go.g) + s.g_delta, 0)
                                                            AS growth_demand,
  'MEASURED'                                                AS growth_src_cd
FROM `{DEM}` d
CROSS JOIN scenarios s
LEFT JOIN g_state gs ON d.mbr_state_cd = gs.state_cd
CROSS JOIN g_overall go
"""

# ---------------------------------------------------------------- STEP 3
# Module 69's fill, replicated CTE by CTE; scenario_cd added to every
# window partition and join so the three scenarios never share a budget.

DDL_FILL = f"""
CREATE OR REPLACE TABLE `{RES}`
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
  FROM `{IN_T}` d
  LEFT JOIN `{CTY}` rc
    ON LPAD(TRIM(CAST(d.mbr_county_cd AS STRING)), 5, '0')
     = LPAD(TRIM(CAST(rc.county_fips AS STRING)), 5, '0')
  WHERE d.growth_demand > 0
),
laned AS (
  -- 69 fix (c): facility share peeled ONLY where > 0; the CELL row's
  -- facility measure is emitted from this same value
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
    l.scenario_cd,
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
        PARTITION BY scenario_cd, mbr_county_cd, mbr_state_cd,
                     specialty_ctg_cd, segment_cd)), 0)
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
    SUM(p1 - placed1) OVER (PARTITION BY scenario_cd, mbr_county_cd,
                            mbr_state_cd, specialty_ctg_cd, segment_cd) AS pool,
    SUM(cell_cap_scaled_cnt - placed1)
      OVER (PARTITION BY scenario_cd, mbr_county_cd, mbr_state_cd,
            specialty_ctg_cd, segment_cd)                               AS room_total
  FROM new_pass
),
new_dealt AS (
  SELECT *,
    COALESCE(room * LEAST(1, SAFE_DIVIDE(pool, NULLIF(room_total, 0))), 0) AS p2
  FROM new_pooled
),
new_used AS (
  SELECT scenario_cd, pid, prvdr_county,
         SUM((placed1 + p2) * seg_hrs) AS new_used_hrs
  FROM new_dealt WHERE pid IS NOT NULL
  GROUP BY 1, 2, 3
),

-- LANE 2: RET_* segments (no cell caps; panel share; remaining budget)
ret_p1 AS (
  SELECT c.*,
    COALESCE(SAFE_DIVIDE(c.panel_cnt,
      SUM(c.panel_cnt) OVER (PARTITION BY c.scenario_cd, c.mbr_county_cd,
                             c.mbr_state_cd, c.specialty_ctg_cd, c.segment_cd)), 0)
      AS seg_market_share,
    GREATEST(c.absorbing_hrs - COALESCE(nu.new_used_hrs, 0), 0) AS ret_budget_hrs
  FROM cells c
  LEFT JOIN new_used nu
    ON c.scenario_cd = nu.scenario_cd
    AND c.pid = nu.pid
    AND COALESCE(c.prvdr_county, '(NULL)') = COALESCE(nu.prvdr_county, '(NULL)')
  WHERE NOT c.is_new_seg
),
ret_scaled AS (
  SELECT *,
    rem_growth * seg_market_share AS p1,
    SUM(rem_growth * seg_market_share * seg_hrs)
      OVER (PARTITION BY scenario_cd, pid, prvdr_county) AS demanded_hrs
  FROM ret_p1
),
ret_pass AS (
  -- 69 fix (d): scale coalesces to 1 when demanded hours are 0/NULL (a
  -- zero hours rate cannot bind the budget); NULL never erases demand
  SELECT *,
    p1 * COALESCE(LEAST(1, SAFE_DIVIDE(ret_budget_hrs, NULLIF(demanded_hrs, 0))), 1)
      AS placed1
  FROM ret_scaled
),
ret_pooled AS (
  SELECT *,
    p1 - placed1 AS returned_cnt,
    SUM(p1 - placed1) OVER (PARTITION BY scenario_cd, mbr_county_cd,
                            mbr_state_cd, specialty_ctg_cd, segment_cd) AS pool,
    GREATEST(ret_budget_hrs - SUM(placed1 * seg_hrs)
      OVER (PARTITION BY scenario_cd, pid, prvdr_county), 0)            AS leftover_hrs
  FROM ret_pass
),
ret_cells_n AS (
  SELECT *,
    SUM(IF(pool > 0, 1, 0))
      OVER (PARTITION BY scenario_cd, pid, prvdr_county) AS n_pool_cells
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
      NULLIF(SUM(room) OVER (PARTITION BY scenario_cd, mbr_county_cd,
                             mbr_state_cd, specialty_ctg_cd, segment_cd), 0))), 0)
      AS p2
  FROM ret_room
),

alloc_rows AS (
  SELECT scenario_cd, mbr_county_cd, mbr_state_cd, specialty_ctg_cd, segment_cd,
         npi, epdb_dw_prvdr_id, prvdr_county, prvdr_state_cd,
         'NEW_INTAKE' AS fill_lane_cd, signal_src_cd,
         seg_market_share, p1 AS pass1_alloc_cnt, returned_cnt,
         p2 AS pass2_alloc_cnt,
         placed1 + p2 AS placed_cnt,
         (placed1 + p2) * seg_hrs AS placed_hrs
  FROM new_dealt WHERE pid IS NOT NULL
  UNION ALL
  SELECT scenario_cd, mbr_county_cd, mbr_state_cd, specialty_ctg_cd, segment_cd,
         npi, epdb_dw_prvdr_id, prvdr_county, prvdr_state_cd,
         'RET_PANEL', signal_src_cd,
         seg_market_share, p1, returned_cnt, p2,
         placed1 + p2,
         (placed1 + p2) * seg_hrs
  FROM ret_dealt WHERE pid IS NOT NULL
),
fac_by_cell AS (
  SELECT DISTINCT scenario_cd, mbr_county_cd, mbr_state_cd, specialty_ctg_cd,
                  segment_cd, facility_absorbed
  FROM laned
  WHERE facility_absorbed > 0
),
placed_by_cell AS (
  SELECT scenario_cd, mbr_county_cd, mbr_state_cd, specialty_ctg_cd, segment_cd,
         SUM(COALESCE(placed_cnt, 0)) AS placed_ind
  FROM alloc_rows
  GROUP BY 1, 2, 3, 4, 5
),
cell_rows AS (
  -- conservation by construction (69 fix b / A3): one CELL row for EVERY
  -- input growth cell, even with zero provider and facility rows
  SELECT
    d.scenario_cd,
    d.mbr_county_cd, d.mbr_state_cd, d.specialty_ctg_cd, d.segment_cd,
    d.growth_demand,
    COALESCE(fc.facility_absorbed, 0) AS facility_absorbed_cnt,
    COALESCE(pb.placed_ind, 0)        AS placed_cnt,
    GREATEST(d.growth_demand - COALESCE(pb.placed_ind, 0)
             - COALESCE(fc.facility_absorbed, 0), 0) AS unplaced_cnt
  FROM `{IN_T}` d
  LEFT JOIN placed_by_cell pb
    ON d.scenario_cd = pb.scenario_cd
    AND COALESCE(d.mbr_county_cd, '(NULL)') = COALESCE(pb.mbr_county_cd, '(NULL)')
    AND COALESCE(d.mbr_state_cd, '') = COALESCE(pb.mbr_state_cd, '')
    AND d.specialty_ctg_cd = pb.specialty_ctg_cd
    AND COALESCE(d.segment_cd, 'X') = COALESCE(pb.segment_cd, 'X')
  LEFT JOIN fac_by_cell fc
    ON d.scenario_cd = fc.scenario_cd
    AND COALESCE(d.mbr_county_cd, '(NULL)') = COALESCE(fc.mbr_county_cd, '(NULL)')
    AND COALESCE(d.mbr_state_cd, '') = COALESCE(fc.mbr_state_cd, '')
    AND d.specialty_ctg_cd = fc.specialty_ctg_cd
    AND COALESCE(d.segment_cd, 'X') = COALESCE(fc.segment_cd, 'X')
  WHERE d.growth_demand > 0
),
xwalk_dedup AS (
  -- 69 A5: the crosswalk is one-to-many; deduped to one deterministic
  -- cms_specialty per aetna_cd (MIN) so no row ever fans
  SELECT aetna_cd, MIN(cms_specialty) AS cms_specialty
  FROM `{XWALK}`
  GROUP BY aetna_cd
),
unioned AS (
  SELECT scenario_cd, 'CELL' AS row_type_cd,
         mbr_county_cd, mbr_state_cd, specialty_ctg_cd, segment_cd,
         CAST(NULL AS STRING) AS npi, CAST(NULL AS STRING) AS epdb_dw_prvdr_id,
         CAST(NULL AS STRING) AS prvdr_county, CAST(NULL AS STRING) AS prvdr_state_cd,
         CAST(NULL AS STRING) AS fill_lane_cd, CAST(NULL AS STRING) AS signal_src_cd,
         CAST(NULL AS FLOAT64) AS seg_market_share,
         CAST(NULL AS FLOAT64) AS pass1_alloc_cnt,
         CAST(NULL AS FLOAT64) AS returned_cnt,
         CAST(NULL AS FLOAT64) AS pass2_alloc_cnt,
         CAST(NULL AS FLOAT64) AS placed_hrs,
         growth_demand, facility_absorbed_cnt, placed_cnt, unplaced_cnt
  FROM cell_rows
  UNION ALL
  SELECT scenario_cd, 'ALLOC',
         mbr_county_cd, mbr_state_cd, specialty_ctg_cd, segment_cd,
         npi, epdb_dw_prvdr_id, prvdr_county, prvdr_state_cd,
         fill_lane_cd, signal_src_cd,
         seg_market_share, pass1_alloc_cnt, returned_cnt, pass2_alloc_cnt,
         placed_hrs,
         CAST(NULL AS FLOAT64), CAST(NULL AS FLOAT64),
         placed_cnt, CAST(NULL AS FLOAT64)
  FROM alloc_rows
)
SELECT
  u.scenario_cd, u.row_type_cd,
  u.mbr_county_cd, u.mbr_state_cd,
  x.cms_specialty,
  u.specialty_ctg_cd,
  u.segment_cd,
  u.npi, u.epdb_dw_prvdr_id, u.prvdr_county, u.prvdr_state_cd,
  u.fill_lane_cd, u.signal_src_cd,
  u.seg_market_share, u.pass1_alloc_cnt, u.returned_cnt, u.pass2_alloc_cnt,
  u.placed_hrs,
  u.growth_demand, u.facility_absorbed_cnt, u.placed_cnt, u.unplaced_cnt,
  CAST(NULL AS INT64) AS conservation_ok_flag
FROM unioned u
LEFT JOIN xwalk_dedup x ON u.specialty_ctg_cd = x.aetna_cd
"""

COMPLETENESS = f"""
SELECT scenario_cd,
       MAX(growth_cells) AS growth_cells,
       MAX(cell_rows)    AS cell_rows
FROM (
  SELECT scenario_cd, COUNT(*) AS growth_cells, 0 AS cell_rows
  FROM `{IN_T}` WHERE growth_demand > 0 GROUP BY 1
  UNION ALL
  SELECT scenario_cd, 0, COUNT(*)
  FROM `{RES}` WHERE row_type_cd = 'CELL' GROUP BY 1
)
GROUP BY 1 ORDER BY 1
"""

GATE_CONSERVATION = f"""
WITH cell AS (
  SELECT scenario_cd, mbr_county_cd, mbr_state_cd, specialty_ctg_cd, segment_cd,
         COALESCE(placed_cnt, 0) + COALESCE(facility_absorbed_cnt, 0)
           + COALESCE(unplaced_cnt, 0) AS accounted,
         COALESCE(placed_cnt, 0)       AS cell_placed
  FROM `{RES}` WHERE row_type_cd = 'CELL'
),
alloc AS (
  SELECT scenario_cd, mbr_county_cd, mbr_state_cd, specialty_ctg_cd, segment_cd,
         SUM(COALESCE(placed_cnt, 0)) AS alloc_placed
  FROM `{RES}` WHERE row_type_cd = 'ALLOC'
  GROUP BY 1, 2, 3, 4, 5
)
SELECT c.scenario_cd,
       COUNTIF(ABS(c.accounted - d.growth_demand) > 0.5)              AS bad_vs_growth,
       COUNTIF(ABS(c.cell_placed - COALESCE(a.alloc_placed, 0)) > 0.5) AS bad_internal
FROM cell c
JOIN `{IN_T}` d
  ON c.scenario_cd = d.scenario_cd
  AND COALESCE(c.mbr_county_cd, '(NULL)') = COALESCE(d.mbr_county_cd, '(NULL)')
  AND COALESCE(c.mbr_state_cd, '') = COALESCE(d.mbr_state_cd, '')
  AND c.specialty_ctg_cd = d.specialty_ctg_cd
  AND COALESCE(c.segment_cd, 'X') = COALESCE(d.segment_cd, 'X')
LEFT JOIN alloc a
  ON c.scenario_cd = a.scenario_cd
  AND COALESCE(c.mbr_county_cd, '(NULL)') = COALESCE(a.mbr_county_cd, '(NULL)')
  AND COALESCE(c.mbr_state_cd, '') = COALESCE(a.mbr_state_cd, '')
  AND c.specialty_ctg_cd = a.specialty_ctg_cd
  AND COALESCE(c.segment_cd, 'X') = COALESCE(a.segment_cd, 'X')
WHERE d.growth_demand > 0
GROUP BY 1 ORDER BY 1
"""

# ---------------------------------------------------------------- STEP 4

DDL_DRIVERS = f"""
CREATE OR REPLACE TABLE `{DRV}`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
WITH cell AS (
  SELECT mbr_county_cd, mbr_state_cd, cms_specialty, specialty_ctg_cd,
         segment_cd, growth_demand, unplaced_cnt
  FROM `{RES}`
  WHERE scenario_cd = 'G_BASE' AND row_type_cd = 'CELL'
),
prov_per_cell AS (
  SELECT mbr_county_cd, mbr_state_cd, specialty_ctg_cd, segment_cd,
         COUNT(*)                       AS prov_rows,
         COUNTIF(seg_market_share > 0)  AS open_rows
  FROM `{RES}`
  WHERE scenario_cd = 'G_BASE' AND row_type_cd = 'ALLOC'
  GROUP BY 1, 2, 3, 4
),
classified AS (
  -- priority order per instruction: NO_PROVIDERS, then DOORS_CLOSED,
  -- then AT_CAPACITY (A6)
  SELECT c.*,
    CASE
      WHEN COALESCE(p.prov_rows, 0) = 0 THEN 'NO_PROVIDERS'
      WHEN COALESCE(p.open_rows, 0) = 0 THEN 'DOORS_CLOSED'
      ELSE 'AT_CAPACITY'
    END AS driver_cd
  FROM cell c
  LEFT JOIN prov_per_cell p
    ON COALESCE(c.mbr_county_cd, '(NULL)') = COALESCE(p.mbr_county_cd, '(NULL)')
    AND COALESCE(c.mbr_state_cd, '') = COALESCE(p.mbr_state_cd, '')
    AND c.specialty_ctg_cd = p.specialty_ctg_cd
    AND COALESCE(c.segment_cd, 'X') = COALESCE(p.segment_cd, 'X')
),
paper AS (
  -- county-level PAPER_NETWORK context: contracted, zero Aetna MA claims
  -- (cap_willing zero_utilization_flag, CD-07) (A7)
  SELECT w.prvdr_state_cd, UPPER(TRIM(w.prvdr_county)) AS county_key,
         COUNT(DISTINCT COALESCE(w.npi, w.epdb_dw_prvdr_id)) AS paper_network_cnt
  FROM `{WILL}` w
  WHERE w.zero_utilization_flag = 1
  GROUP BY 1, 2
),
county_names AS (
  SELECT DISTINCT LPAD(TRIM(CAST(county_fips AS STRING)), 5, '0') AS fips,
                  county_name
  FROM `{CTY}`
)
SELECT
  cl.mbr_state_cd,
  cl.mbr_county_cd,
  cl.cms_specialty,
  SUM(cl.growth_demand)                                     AS growth_demand,
  SUM(cl.unplaced_cnt)                                      AS unplaced_cnt,
  SUM(IF(cl.driver_cd = 'NO_PROVIDERS', cl.unplaced_cnt, 0)) AS unplaced_no_providers,
  SUM(IF(cl.driver_cd = 'DOORS_CLOSED', cl.unplaced_cnt, 0)) AS unplaced_doors_closed,
  SUM(IF(cl.driver_cd = 'AT_CAPACITY', cl.unplaced_cnt, 0))  AS unplaced_at_capacity,
  ANY_VALUE(COALESCE(pp.paper_network_cnt, 0))               AS paper_network_cnt
FROM classified cl
LEFT JOIN county_names cn
  ON LPAD(TRIM(CAST(cl.mbr_county_cd AS STRING)), 5, '0') = cn.fips
LEFT JOIN paper pp
  ON UPPER(TRIM(COALESCE(cn.county_name, ''))) = pp.county_key
  AND cl.mbr_state_cd = pp.prvdr_state_cd
GROUP BY 1, 2, 3
"""

# ---------------------------------------------------------------- STEP 5

DDL_ACTIONS = f"""
CREATE OR REPLACE TABLE `{ACT}`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
WITH used AS (
  SELECT COALESCE(npi, epdb_dw_prvdr_id) AS pid, prvdr_county, prvdr_state_cd,
         SUM(COALESCE(placed_hrs, 0)) AS used_hrs,
         LOGICAL_OR(fill_lane_cd = 'NEW_INTAKE'
                    AND COALESCE(returned_cnt, 0) > 0) AS any_new_overflow
  FROM `{RES}`
  WHERE scenario_cd = 'G_BASE' AND row_type_cd = 'ALLOC'
  GROUP BY 1, 2, 3
),
room AS (
  SELECT
    py.npi, py.epdb_dw_prvdr_id, py.prvdr_county, py.prvdr_state_cd,
    py.specialty_ctg_cd, py.util_ratio,
    py.spare_hrs IS NULL AS null_budget,
    COALESCE(py.spare_hrs, 0) + COALESCE(py.team_uplift_hrs, 0) AS absorbing_hrs,
    COALESCE(u.used_hrs, 0)                                     AS used_hrs_g_base,
    COALESCE(py.spare_hrs, 0) + COALESCE(py.team_uplift_hrs, 0)
      - COALESCE(u.used_hrs, 0)                                 AS remaining_room_hrs,
    COALESCE(u.any_new_overflow, FALSE)                         AS any_new_overflow
  FROM `{PY}` py
  LEFT JOIN used u
    ON COALESCE(py.npi, py.epdb_dw_prvdr_id) = u.pid
    AND COALESCE(py.prvdr_county, '(NULL)') = COALESCE(u.prvdr_county, '(NULL)')
),
top_room AS (
  SELECT *, ROW_NUMBER() OVER (
    PARTITION BY prvdr_state_cd, prvdr_county
    ORDER BY remaining_room_hrs DESC) AS rn
  FROM room
  WHERE remaining_room_hrs > 0
)
SELECT 'TOP_ROOM' AS list_cd,
       prvdr_state_cd, prvdr_county, npi, epdb_dw_prvdr_id, specialty_ctg_cd,
       util_ratio, absorbing_hrs, used_hrs_g_base, remaining_room_hrs,
       rn AS rank_in_county
FROM top_room
WHERE rn <= 15
UNION ALL
SELECT 'ZERO_CLAIM',
       w.prvdr_state_cd, w.prvdr_county, w.npi, w.epdb_dw_prvdr_id,
       r.specialty_ctg_cd,
       r.util_ratio, r.absorbing_hrs, r.used_hrs_g_base, r.remaining_room_hrs,
       CAST(NULL AS INT64)
FROM `{WILL}` w
LEFT JOIN room r
  ON COALESCE(w.npi, '') = COALESCE(r.npi, '')
  AND COALESCE(w.epdb_dw_prvdr_id, '') = COALESCE(r.epdb_dw_prvdr_id, '')
  AND COALESCE(w.prvdr_county, '(NULL)') = COALESCE(r.prvdr_county, '(NULL)')
WHERE w.zero_utilization_flag = 1
UNION ALL
SELECT 'AT_CAPACITY',
       prvdr_state_cd, prvdr_county, npi, epdb_dw_prvdr_id, specialty_ctg_cd,
       util_ratio, absorbing_hrs, used_hrs_g_base, remaining_room_hrs,
       CAST(NULL AS INT64)
FROM room
WHERE (NOT null_budget
       AND (absorbing_hrs <= 0 OR remaining_room_hrs <= 0.01 * absorbing_hrs))
   OR any_new_overflow
"""

# ---------------------------------------------------------------- sanity

CHECKS = {
    "per-scenario growth / placed / facility / unplaced (CELL rows)":
        f"SELECT scenario_cd, ROUND(SUM(growth_demand), 0) AS growth, "
        f"ROUND(SUM(placed_cnt), 0) AS placed, "
        f"ROUND(SUM(facility_absorbed_cnt), 0) AS facility, "
        f"ROUND(SUM(unplaced_cnt), 0) AS unplaced, "
        f"ROUND(SAFE_DIVIDE(SUM(unplaced_cnt), SUM(growth_demand)), 4) AS unplaced_pct "
        f"FROM `{RES}` WHERE row_type_cd = 'CELL' GROUP BY 1 ORDER BY 1",
    "G_BASE unplaced share by state":
        f"SELECT mbr_state_cd, ROUND(SUM(growth_demand), 0) AS growth, "
        f"ROUND(SAFE_DIVIDE(SUM(unplaced_cnt), SUM(growth_demand)), 4) AS unplaced_pct "
        f"FROM `{RES}` WHERE row_type_cd = 'CELL' AND scenario_cd = 'G_BASE' "
        f"GROUP BY 1 ORDER BY 1",
    "input rows on overall-g fallback (NULL state, A2)":
        f"SELECT COUNT(*) AS fallback_rows, ROUND(SUM(growth_demand), 0) AS growth "
        f"FROM `{IN_T}` WHERE g_fallback_flag = 1 AND scenario_cd = 'G_BASE'",
    "specialty bridge leakage (rule 6)":
        f"SELECT COUNTIF(cms_specialty IS NULL) AS unbridged_rows, "
        f"ROUND(SUM(IF(cms_specialty IS NULL, "
        f"COALESCE(placed_cnt, 0) + COALESCE(unplaced_cnt, 0), 0)), 0) AS unbridged_volume "
        f"FROM `{RES}` WHERE row_type_cd = 'CELL'",
    "driver shares (G_BASE)":
        f"SELECT ROUND(SUM(unplaced_no_providers), 0) AS no_providers, "
        f"ROUND(SUM(unplaced_doors_closed), 0) AS doors_closed, "
        f"ROUND(SUM(unplaced_at_capacity), 0) AS at_capacity, "
        f"ROUND(SAFE_DIVIDE(SUM(unplaced_no_providers), SUM(unplaced_cnt)), 4) "
        f"AS no_providers_pct, "
        f"ROUND(SAFE_DIVIDE(SUM(unplaced_doors_closed), SUM(unplaced_cnt)), 4) "
        f"AS doors_closed_pct, "
        f"ROUND(SAFE_DIVIDE(SUM(unplaced_at_capacity), SUM(unplaced_cnt)), 4) "
        f"AS at_capacity_pct FROM `{DRV}`",
    "paper-network totals (context, distinct per county)":
        f"SELECT COUNT(*) AS counties_with_paper, SUM(paper_network_cnt) AS providers "
        f"FROM (SELECT mbr_state_cd, mbr_county_cd, MAX(paper_network_cnt) "
        f"AS paper_network_cnt FROM `{DRV}` GROUP BY 1, 2) "
        f"WHERE paper_network_cnt > 0",
    "action list counts":
        f"SELECT list_cd, COUNT(*) AS n, "
        f"COUNT(DISTINCT CONCAT(COALESCE(prvdr_state_cd, ''), '|', "
        f"COALESCE(prvdr_county, ''))) AS counties "
        f"FROM `{ACT}` GROUP BY 1 ORDER BY 1",
}


def _run(client, label, sql):
    t0 = time.time()
    result = client.query(sql).result()
    print(f"[{label}] {time.time() - t0:.1f}s")
    return result


def main():
    print(f"RUN_MODE = {RUN_MODE}")
    client = cfg.client()

    # STEP 1 - enrollment growth by state (persist + print + gate)
    _run(client, "create cap_growth_measured", DDL_GROWTH)
    print("--- enrollment (distinct members 60+, 2024 vs 2025) ---")
    g_by_state = {}
    for row in _run(client, "enrollment by state", ENROLLMENT):
        r = dict(row)
        print("  ", r)
        g_by_state[r["state_cd"]] = r
    for row in _run(client, "members outside footprint submarkets", NULL_SUBMKT):
        print("  ", dict(row))
    missing = [s for s in cfg.STATE_ABBRS
               if s not in g_by_state
               or not g_by_state[s]["members_2024"]
               or not g_by_state[s]["members_2025"]]
    if missing:
        raise SystemExit(
            f"GATE FAILED -- enrollment missing/zero for states {missing} in "
            f"A870800_medicare_analysis_membership (member x month, DD-08). "
            f"Other member-count sources in the repo: dc2_demand_base.members "
            f"(county x month rollup of the same table) - none is a substitute; "
            f"fix the membership extract before building scenarios.")

    # STEP 2 - scenario inputs
    _run(client, "create cap_scenario_input", DDL_INPUT)

    # STEP 3 - three-scenario fill (module 69 mirror; ONE claims scan)
    _run(client, "create cap_scenario_results", DDL_FILL)
    print("--- completeness (CELL rows == input growth cells, per scenario) ---")
    for row in _run(client, "completeness", COMPLETENESS):
        r = dict(row)
        print("  ", r)
        if r["growth_cells"] != r["cell_rows"]:
            raise SystemExit(
                f"GATE FAILED -- {r['scenario_cd']}: CELL rows "
                f"({r['cell_rows']:,}) != input growth cells "
                f"({r['growth_cells']:,})")
    print("--- conservation gate (per scenario, tolerance 0.5) ---")
    bad = []
    for row in _run(client, "conservation gate", GATE_CONSERVATION):
        r = dict(row)
        print("  ", r)
        if r["bad_vs_growth"] or r["bad_internal"]:
            bad.append(r["scenario_cd"])
    if bad:
        raise SystemExit(
            f"GATE FAILED -- conservation broken in scenarios {bad} "
            f"(|placed + facility + unplaced - growth| > 0.5, or CELL placed "
            f"!= ALLOC sum). STOPPING before drivers/action lists.")
    print("conservation OK (all scenarios)")
    _run(client, "set conservation_ok_flag",
         f"UPDATE `{RES}` SET conservation_ok_flag = 1 WHERE TRUE")

    # STEP 4 - driver decomposition (G_BASE)
    _run(client, "create cap_county_drivers", DDL_DRIVERS)

    # STEP 5 - action lists (G_BASE)
    _run(client, "create cap_action_lists", DDL_ACTIONS)

    for label, q in CHECKS.items():
        print(f"--- {label} ---")
        for row in _run(client, label, q):
            print("  ", dict(row))


if __name__ == "__main__":
    main()


# REVIEW
# Reviewer 1 LOGIC:
#  - Growth measured from the MEMBERSHIP extract (member x month, DD-08),
#    never from claims utilizers; distinct member_id per eff_yr; state key
#    UPPER(LEFT(mbr_submarket, 2)) matches dem_segment_split.mbr_state_cd
#    (the locked fact), so the g join cannot silently miss.
#  - The fill replicates 69's chain verbatim; every window partition and
#    every join gained scenario_cd (new_p1/new_pooled/new_used/ret_p1/
#    ret_scaled/ret_pooled/ret_cells_n/ret_dealt, fac and placed cell
#    joins), so scenarios never share pools, budgets, or new_used_hrs.
#  - Conservation by construction: a CELL row per input growth cell
#    (unplaced = growth - placed - facility, floor 0); the gate then
#    catches over-placement (fan signature) and CELL-vs-ALLOC drift.
#  - Attribution: demand side mbr_county_cd + mbr_state_cd, capacity side
#    prvdr_county + prvdr_state_cd; the two meet only in the fill join and
#    the drivers' name+state paper join (rule 1, rule 12).
#  - Driver priority is deterministic per cell (NO_PROVIDERS before
#    DOORS_CLOSED before AT_CAPACITY); RET cells with zero panels land in
#    DOORS_CLOSED, 68's segment-NULL cells in NO_PROVIDERS (A6).
# Reviewer 2 SPEC:
#  - Steps 1-5 of prompt M74a implemented; deviations = nine ASSUMPTION
#    blocks (notably A3 row_type_cd shape and A8 at-capacity rule).
#  - dem_segment_split, modules 59-72, and the dashboard slider path are
#    untouched; growth_src_cd = 'MEASURED'; deltas -0.02/0/+0.02 with
#    floor 0 are the prompt's frozen spec, not tuning params, so they do
#    not belong in cap_params; the 0.01 at-capacity threshold is 71 A1's
#    instruction constant, restated here.
#  - All four outputs via cfg.table() (rule 10); bridge applied exactly
#    once, deduped (rule 6, 69 A5); conservation_ok_flag set only after
#    the gate passes.
# Reviewer 3 EFFICIENCY:
#  - Exactly ONE claims scan (fac_share, sampled per R2). Membership is
#    scanned twice (cap_growth_measured DDL + the outside-footprint print) -
#    a small extract, not claims; the input DDL reads the persisted rates.
#  - The scenarios CROSS JOIN is a deliberate 3-row fan on the demand
#    table; g_overall and ret_rate_overall are 1-row CROSS JOINs; no other
#    CROSS JOINs, no row-explosion joins (bridge deduped before use).
#  - Fill CTEs process 3x the demand cells of 69; provider/bench joins are
#    keyed identically to 69. Relative cost ~ one claims scan + ~3x 69's
#    post-claims work.
