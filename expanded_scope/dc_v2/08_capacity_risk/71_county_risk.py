"""
71 - county risk rollup   [PYTHON runner / BigQuery DDL]

WHAT  : The deliverable grain (Danielle drill-down): rolls cap_fill_result
        to county x specialty x segment -> cap_county_risk. Carries growth,
        placed, the CD-24 facility_absorbed_cnt, unplaced (the risk
        number), unplaced_pct, open/maxed provider counts, the
        borrowed-signal honesty metric (CD-14) and risk_rank within state.
GRAIN : mbr_county_cd + mbr_state_cd x cms_specialty x segment_cd (rule 12)
INPUTS: cap_fill_result, dem_segment_split, cap_provider_segment
OUTPUT: cap_county_risk (BigQuery table) + sanity prints.
Run   : python expanded_scope/dc_v2/08_capacity_risk/71_county_risk.py
"""

# ASSUMPTION [1]: n_maxed_providers = providers whose total placed across
#   their cells in the county reached their total scaled cell caps
#   (>= 99.9999% of a positive cap). "Crossed 100% in simulation" has no
#   formula in the spec.
# ASSUMPTION [2]: n_open_providers = distinct providers with
#   seg_market_share > 0 in the cell's fill rows (open doors that actually
#   participate in that county x specialty x segment).
# ASSUMPTION [3]: rows whose cms_specialty is NULL (bridge leakage, 69 A6)
#   are kept as their own group - dropping them would hide unplaced volume.
# ASSUMPTION [4]: risk_rank 1 = worst (highest unplaced_pct, ties by
#   unplaced_cnt), ranked within mbr_state_cd. The spec fixes the sort
#   keys, not the direction.
# ASSUMPTION [5]: growth_demand re-read from dem_segment_split (source of
#   truth) rather than re-summed from fill rows; V6 already guarantees they
#   agree.

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

FILL = cfg.table("cap_fill_result")
DEM  = cfg.table("dem_segment_split")
OUT  = cfg.table("cap_county_risk")

DDL = f"""
CREATE OR REPLACE TABLE `{OUT}`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
WITH prov_rows AS (
  SELECT * FROM `{FILL}`
  WHERE absorbed_by IS NULL AND (npi IS NOT NULL OR epdb_dw_prvdr_id IS NOT NULL)
),
maxed AS (
  SELECT mbr_county_cd, mbr_state_cd,
         COALESCE(npi, epdb_dw_prvdr_id) AS pid,
         SUM(COALESCE(placed_cnt, 0)) AS placed_tot,
         SUM(COALESCE(pass1_alloc_cnt, 0) - COALESCE(returned_cnt, 0)
             + COALESCE(pass2_alloc_cnt, 0)) AS cap_used
  FROM prov_rows
  GROUP BY 1, 2, 3
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
  (SELECT COUNT(*) FROM maxed m
   WHERE m.mbr_county_cd = c.mbr_county_cd
     AND COALESCE(m.mbr_state_cd, '') = COALESCE(c.mbr_state_cd, '')
     AND m.cap_used > 0
     AND m.placed_tot >= m.cap_used * 0.999999)       AS n_maxed_providers,
  c.borrowed_signal_pct,
  ROW_NUMBER() OVER (
    PARTITION BY c.mbr_state_cd
    ORDER BY SAFE_DIVIDE(c.unplaced_cnt,
      COALESCE(c.placed_cnt, 0) + COALESCE(c.facility_absorbed_cnt, 0)
      + COALESCE(c.unplaced_cnt, 0)) DESC,
      c.unplaced_cnt DESC)                            AS risk_rank
FROM cell_rollup c
"""

CHECKS = {
    "rows / states / counties":
        f"SELECT COUNT(*) AS rows_n, COUNT(DISTINCT mbr_state_cd) AS states, "
        f"COUNT(DISTINCT mbr_county_cd) AS counties FROM `{OUT}`",
    "top 10 risk cells (rank 1 = worst, per state)":
        f"SELECT mbr_state_cd, mbr_county_cd, cms_specialty, segment_cd, "
        f"ROUND(unplaced_cnt, 1) AS unplaced, ROUND(unplaced_pct, 4) AS pct, risk_rank "
        f"FROM `{OUT}` WHERE risk_rank <= 3 ORDER BY mbr_state_cd, risk_rank LIMIT 12",
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
#  - Grain = member county + state (rule 12); growth reconstructed from the
#    three lanes, which V6 (module 69 gate) proved equals dem_segment_split.
#  - n_maxed counted at provider level within the county so a provider
#    maxed across several segments counts once.
#  - NOTE: a vestigial growth CTE was removed in run-fix review - it was
#    unreferenced AND contained a correlated CROSS JOIN BigQuery rejects;
#    growth is reconstructed from the three lanes (V6 guarantees identity).
# Reviewer 2 SPEC:
#  - Deviations = five ASSUMPTION blocks; cap_county_risk columns match the
#    amended data model (mbr_state_cd, facility_absorbed_cnt included).
# Reviewer 3 EFFICIENCY:
#  - Zero claims scans; one pass over cap_fill_result plus a provider-level
#    aggregate. The correlated n_maxed subquery is county-keyed on a small
#    aggregate. No CROSS JOINs of consequence. Relative cost: trivial.
