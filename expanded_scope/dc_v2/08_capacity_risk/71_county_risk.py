"""
71 - county risk rollup   [PYTHON runner / BigQuery DDL]

WHAT  : The deliverable grain (Danielle drill-down): rolls cap_fill_result
        to county x specialty x segment -> cap_county_risk. Carries growth,
        placed, the CD-24 facility_absorbed_cnt, unplaced (the risk
        number), unplaced_pct, open/maxed provider counts, the
        borrowed-signal honesty metric (CD-14) and risk_rank within state.
        n_maxed uses the real budget test: a provider is maxed when their
        remaining absorbing capacity after BOTH lanes is <= 1% of budget,
        or any of their NEW cells overflowed in pass 1 (or filled to its
        scaled cap).
GRAIN : mbr_county_cd + mbr_state_cd x cms_specialty x segment_cd (rule 12)
INPUTS: cap_fill_result, dem_segment_split, cap_provider_year,
        cap_provider_segment, cap_cohort_bench, cap_hours_annual
OUTPUT: cap_county_risk (BigQuery table) + sanity prints.
Run   : python expanded_scope/dc_v2/08_capacity_risk/71_county_risk.py
"""

# ASSUMPTION [1]: n_maxed (rebuilt after the degenerate first draft):
#   remaining = (spare_hrs + team_uplift_hrs, 69's budget basis) - placed
#   hours across BOTH lanes, converting counts with the SAME rates as 69
#   (NEW: cohort avg_first_yr_hrs with specialty-avg fallback; RET: county
#   x specialty consumption rate with overall fallback). Maxed when
#   remaining <= 1% of budget, OR any NEW cell has returned_cnt > 0, OR a
#   NEW cell's placed count reached its scaled cap (pass-2 fill-to-cap is
#   the same economic state as pass-1 overflow). NULL budgets (CD-21
#   providers) count as maxed only via the cell conditions.
# ASSUMPTION [2]: n_open_providers = distinct providers with
#   seg_market_share > 0 in the cell's fill rows (open doors that actually
#   participate in that county x specialty x segment).
# ASSUMPTION [3]: rows whose cms_specialty is NULL (bridge leakage) are
#   kept as their own group - dropping them would hide unplaced volume.
# ASSUMPTION [4]: risk_rank 1 = worst (highest unplaced_pct, ties by
#   unplaced_cnt), ranked within mbr_state_cd. The spec fixes the sort
#   keys, not the direction.
# ASSUMPTION [5]: growth reconstructed from the three lanes (placed +
#   facility + unplaced); V6 (module 69 gate) guarantees identity with
#   dem_segment_split.

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

RUN_MODE = "sample"   # no claims scan; governed by upstream runs

FILL   = cfg.table("cap_fill_result")
DEM    = cfg.table("dem_segment_split")
PY     = cfg.table("cap_provider_year")
MATRIX = cfg.table("cap_provider_segment")
BENCH  = cfg.table("cap_cohort_bench")
ANNUAL = cfg.table("cap_hours_annual")
OUT    = cfg.table("cap_county_risk")

INTERNAL_YR = 2025   # capacity base year, matching modules 65/69

DDL = f"""
CREATE OR REPLACE TABLE `{OUT}`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
WITH prov_rows AS (
  SELECT * FROM `{FILL}`
  WHERE absorbed_by IS NULL AND (npi IS NOT NULL OR epdb_dw_prvdr_id IS NOT NULL)
),
budget AS (
  SELECT COALESCE(npi, epdb_dw_prvdr_id) AS pid, prvdr_county, prvdr_state_cd,
         specialty_ctg_cd, county_band_cd,
         spare_hrs + COALESCE(team_uplift_hrs, 0) AS absorbing_hrs
  FROM `{PY}`
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
  SELECT b.prvdr_county, b.prvdr_state_cd, b.specialty_ctg_cd,
         SAFE_DIVIDE(SUM(ph.defl_hrs), SUM(pp.panel_tot)) AS ret_hrs
  FROM budget b
  JOIN prov_hours ph
    ON b.pid = ph.pid
    AND COALESCE(b.prvdr_county, '(NULL)') = COALESCE(ph.prvdr_county, '(NULL)')
  JOIN prov_panel pp
    ON b.pid = pp.pid
    AND COALESCE(b.prvdr_county, '(NULL)') = COALESCE(pp.prvdr_county, '(NULL)')
  GROUP BY 1, 2, 3
),
ret_rate_overall AS (
  SELECT SAFE_DIVIDE(SUM(ph.defl_hrs), SUM(pp.panel_tot)) AS ret_hrs
  FROM prov_hours ph
  JOIN prov_panel pp
    ON ph.pid = pp.pid
    AND COALESCE(ph.prvdr_county, '(NULL)') = COALESCE(pp.prvdr_county, '(NULL)')
),
bench_hrs AS (
  SELECT specialty_ctg_cd, county_band_cd, prvdr_state_cd, segment_cd,
         COALESCE(avg_first_yr_hrs,
                  AVG(avg_first_yr_hrs) OVER (PARTITION BY specialty_ctg_cd))
           AS new_hrs
  FROM `{BENCH}` WHERE segment_cd != 'ALL'
),
placed_hrs AS (
  -- per provider x county: placed counts -> hours with 69's rates (A1)
  SELECT
    COALESCE(f.npi, f.epdb_dw_prvdr_id) AS pid,
    f.prvdr_county,
    SUM(CASE
      WHEN f.fill_lane_cd = 'NEW_INTAKE'
        THEN COALESCE(f.placed_cnt, 0) * COALESCE(bh.new_hrs, 0)
      WHEN f.fill_lane_cd = 'RET_PANEL'
        THEN COALESCE(f.placed_cnt, 0) * COALESCE(rr.ret_hrs, ro.ret_hrs, 0)
      ELSE 0 END)                                        AS placed_hrs,
    LOGICAL_OR(f.fill_lane_cd = 'NEW_INTAKE'
               AND COALESCE(f.returned_cnt, 0) > 0)      AS any_new_overflow,
    LOGICAL_OR(f.fill_lane_cd = 'NEW_INTAKE'
               AND COALESCE(m.cell_cap_scaled_cnt, 0) > 0
               AND COALESCE(f.placed_cnt, 0)
                   >= m.cell_cap_scaled_cnt * 0.999999)  AS any_new_cap_hit
  FROM prov_rows f
  LEFT JOIN budget b
    ON COALESCE(f.npi, f.epdb_dw_prvdr_id) = b.pid
    AND COALESCE(f.prvdr_county, '(NULL)') = COALESCE(b.prvdr_county, '(NULL)')
  LEFT JOIN bench_hrs bh
    ON b.specialty_ctg_cd = bh.specialty_ctg_cd
    AND COALESCE(b.county_band_cd, '') = COALESCE(bh.county_band_cd, '')
    AND b.prvdr_state_cd = bh.prvdr_state_cd
    AND bh.segment_cd = f.segment_cd
  LEFT JOIN ret_rate rr
    ON COALESCE(b.prvdr_county, '(NULL)') = COALESCE(rr.prvdr_county, '(NULL)')
    AND b.prvdr_state_cd = rr.prvdr_state_cd
    AND b.specialty_ctg_cd = rr.specialty_ctg_cd
  CROSS JOIN ret_rate_overall ro
  LEFT JOIN `{MATRIX}` m
    ON COALESCE(f.npi, f.epdb_dw_prvdr_id) = COALESCE(m.npi, m.epdb_dw_prvdr_id)
    AND COALESCE(f.prvdr_county, '(NULL)') = COALESCE(m.prvdr_county, '(NULL)')
    AND m.segment_cd = f.segment_cd
  GROUP BY 1, 2
),
maxed AS (
  SELECT
    ph.pid, ph.prvdr_county,
    COALESCE((b.absorbing_hrs - ph.placed_hrs) <= 0.01 * b.absorbing_hrs, FALSE)
      OR ph.any_new_overflow OR ph.any_new_cap_hit AS is_maxed
  FROM placed_hrs ph
  LEFT JOIN budget b
    ON ph.pid = b.pid
    AND COALESCE(ph.prvdr_county, '(NULL)') = COALESCE(b.prvdr_county, '(NULL)')
),
cell_maxed AS (
  SELECT f.mbr_county_cd, f.mbr_state_cd, f.cms_specialty, f.segment_cd,
         COUNT(DISTINCT IF(mx.is_maxed, mx.pid, NULL)) AS n_maxed_providers
  FROM prov_rows f
  JOIN maxed mx
    ON COALESCE(f.npi, f.epdb_dw_prvdr_id) = mx.pid
    AND COALESCE(f.prvdr_county, '(NULL)') = COALESCE(mx.prvdr_county, '(NULL)')
  GROUP BY 1, 2, 3, 4
),
cell_rollup AS (
  SELECT
    f.mbr_county_cd, f.mbr_state_cd, f.cms_specialty, f.segment_cd,
    SUM(IF(f.absorbed_by IS NULL, COALESCE(f.placed_cnt, 0), 0))   AS placed_cnt,
    SUM(IF(f.absorbed_by = 'FACILITY', COALESCE(f.placed_cnt, 0), 0))
                                                                   AS facility_absorbed_cnt,
    SUM(COALESCE(f.unplaced_cnt, 0))                               AS unplaced_cnt,
    COUNT(DISTINCT IF(f.seg_market_share > 0,
      COALESCE(f.npi, f.epdb_dw_prvdr_id), NULL))                  AS n_open_providers,
    SAFE_DIVIDE(
      SUM(IF(f.signal_src_cd = 'BORROWED', COALESCE(f.placed_cnt, 0), 0)),
      SUM(IF(f.absorbed_by IS NULL, COALESCE(f.placed_cnt, 0), 0))) AS borrowed_signal_pct
  FROM `{FILL}` f
  GROUP BY 1, 2, 3, 4
)
SELECT
  c.mbr_county_cd,
  c.mbr_state_cd,
  c.cms_specialty,
  c.segment_cd,
  COALESCE(c.placed_cnt, 0) + COALESCE(c.facility_absorbed_cnt, 0)
    + COALESCE(c.unplaced_cnt, 0)                     AS growth_demand,
  c.placed_cnt,
  c.facility_absorbed_cnt,
  c.unplaced_cnt,
  SAFE_DIVIDE(c.unplaced_cnt,
    COALESCE(c.placed_cnt, 0) + COALESCE(c.facility_absorbed_cnt, 0)
    + COALESCE(c.unplaced_cnt, 0))                    AS unplaced_pct,
  c.n_open_providers,
  COALESCE(cm.n_maxed_providers, 0)                   AS n_maxed_providers,
  c.borrowed_signal_pct,
  ROW_NUMBER() OVER (
    PARTITION BY c.mbr_state_cd
    ORDER BY SAFE_DIVIDE(c.unplaced_cnt,
      COALESCE(c.placed_cnt, 0) + COALESCE(c.facility_absorbed_cnt, 0)
      + COALESCE(c.unplaced_cnt, 0)) DESC,
      c.unplaced_cnt DESC)                            AS risk_rank
FROM cell_rollup c
LEFT JOIN cell_maxed cm
  ON c.mbr_county_cd = cm.mbr_county_cd
  AND COALESCE(c.mbr_state_cd, '') = COALESCE(cm.mbr_state_cd, '')
  AND COALESCE(c.cms_specialty, '') = COALESCE(cm.cms_specialty, '')
  AND COALESCE(c.segment_cd, '') = COALESCE(cm.segment_cd, '')
"""

CHECKS = {
    "rows / states / counties":
        f"SELECT COUNT(*) AS rows_n, COUNT(DISTINCT mbr_state_cd) AS states, "
        f"COUNT(DISTINCT mbr_county_cd) AS counties FROM `{OUT}`",
    "top risk cells (rank 1 = worst, per state)":
        f"SELECT mbr_state_cd, mbr_county_cd, cms_specialty, segment_cd, "
        f"ROUND(unplaced_cnt, 1) AS unplaced, ROUND(unplaced_pct, 4) AS pct, risk_rank "
        f"FROM `{OUT}` WHERE risk_rank <= 3 ORDER BY mbr_state_cd, risk_rank LIMIT 12",
    "n_maxed sanity (must NOT equal every placed provider)":
        f"SELECT ROUND(AVG(n_maxed_providers), 2) AS avg_maxed, "
        f"ROUND(AVG(n_open_providers), 2) AS avg_open, "
        f"ROUND(SAFE_DIVIDE(SUM(n_maxed_providers), SUM(n_open_providers)), 4) "
        f"AS maxed_share FROM `{OUT}` WHERE n_open_providers > 0",
    "facility lane share by state (CD-24)":
        f"SELECT mbr_state_cd, ROUND(SAFE_DIVIDE(SUM(facility_absorbed_cnt), "
        f"SUM(growth_demand)), 4) AS fac_share FROM `{OUT}` GROUP BY 1 ORDER BY 1",
    "borrowed-signal share overall (CD-14 honesty)":
        f"SELECT ROUND(SAFE_DIVIDE(SUM(borrowed_signal_pct * placed_cnt), "
        f"SUM(placed_cnt)), 4) AS borrowed_pct FROM `{OUT}` WHERE placed_cnt > 0",
    "NULL cms_specialty volume (bridge leakage kept, A3)":
        f"SELECT COUNT(*) AS rows_n, ROUND(SUM(growth_demand), 0) AS volume "
        f"FROM `{OUT}` WHERE cms_specialty IS NULL",
}


def _run(client, label, sql):
    t0 = time.time()
    result = client.query(sql).result()
    print(f"[{label}] {time.time() - t0:.1f}s")
    return result


def main():
    print(f"RUN_MODE = {RUN_MODE}")
    client = cfg.client()
    _run(client, "create cap_county_risk", DDL)
    for label, q in CHECKS.items():
        print(f"--- {label} ---")
        for row in _run(client, label, q):
            print("  ", dict(row))


if __name__ == "__main__":
    main()


# REVIEW
# Reviewer 1 LOGIC:
#  - n_maxed rebuilt: budget = 69's basis (spare + uplift), placed hours
#    converted with 69's exact rates (NEW cohort first-year hours, RET
#    county x specialty consumption rate) - the prior draft's cap_used
#    expression was algebraically identical to placed_cnt and counted
#    every placed provider as maxed.
#  - Maxed is judged per provider x county across BOTH lanes, then counted
#    DISTINCT per county x specialty x segment cell via that cell's
#    participating providers.
#  - Grain = member county + state (rule 12); growth reconstructed from
#    the three lanes (V6-guaranteed identity).
# Reviewer 2 SPEC:
#  - Deviations = five ASSUMPTION blocks; the 1% remaining-budget threshold
#    and the fill-to-cap extension of "overflow" are the instruction's
#    definition, stated in A1. cap_county_risk columns match the amended
#    data model.
# Reviewer 3 EFFICIENCY:
#  - Zero claims scans; fill result read twice (provider rows + cell
#    rollup), matrix/budget/bench joins all keyed provider(+county/segment);
#    the only CROSS JOIN is the 1-row overall-rate fallback. Relative
#    cost: trivial.
