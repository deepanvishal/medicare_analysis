"""
53 - p75 baseline extract   [PYTHON runner / BigQuery DDL]

WHAT  : Extracts the v1 (30-38 pipeline) demand and capacity numbers into
        one baseline table at county x cms_specialty grain. Source is the 36
        gap table (A870800_medicare_supply_demand_ms_dc_gap, via
        cfg.table("dc_gap")) because demand and capacity already coexist
        there post-bridge on cms_specialty.
        Column sources: county_fips <- ms_dc_gap.county_fips;
        cms_specialty <- ms_dc_gap.cms_specialty;
        demand_p75_annual <- ms_dc_gap.ma_demand_visits (originates in
        ms_dc_demand; identical across the plan_type rows of a county x
        specialty, so MAX dedupes without double counting);
        capacity_p75_annual <- ms_dc_gap.capacity_visits (originates in
        ms_dc_capacity; per plan_type, so SUM across plan rows).
GRAIN : county_fips x cms_specialty.
INPUTS: cfg.table("dc_gap") (v1 pipeline output; read from 33/35/36 code)
OUTPUT: dc2_p75_baseline (BigQuery table) with sanity checks printed.
Run   : python expanded_scope/dc_v2/05_models/53_p75_baseline_extract.py
"""

import os
import sys


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

GAP = cfg.table("dc_gap")
OUT = cfg.src("dc2_p75_baseline")

DDL = f"""
CREATE OR REPLACE TABLE `{OUT}`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
SELECT
  county_fips,
  cms_specialty,
  MAX(ma_demand_visits) AS demand_p75_annual,
  SUM(capacity_visits)  AS capacity_p75_annual,
  'v1 pipeline 30-38, p75 methodology' AS source_note
FROM `{GAP}`
GROUP BY county_fips, cms_specialty
"""

CHECKS = {
    "row count dc2_p75_baseline":
        f"SELECT COUNT(*) AS row_count FROM `{OUT}`",
    "distinct counties":
        f"SELECT COUNT(DISTINCT county_fips) AS counties FROM `{OUT}`",
    "distinct specialties":
        f"SELECT COUNT(DISTINCT cms_specialty) AS specialties FROM `{OUT}`",
    "measure totals":
        f"SELECT CAST(SUM(demand_p75_annual) AS INT64) AS sum_demand_p75_annual, "
        f"CAST(SUM(capacity_p75_annual) AS INT64) AS sum_capacity_p75_annual FROM `{OUT}`",
}


def main():
    cfg.run_ddl(DDL, CHECKS)


if __name__ == "__main__":
    main()
