"""
53 - baselines and ceilings   [PYTHON runner / BigQuery DDL]

WHAT  : One baseline table at county x cms_specialty: the v1 capacity number
        carried verbatim, a cohort-clean rebuild of the v1 demand number
        (ENROLLED members age 60+, December 2025, replacing eligibles), the
        book gap, and the eligibles-based ceiling.
        Column sources (v1 table names read from 31/32/33/35/36 code, not
        guessed): state_cd / county_fips / cms_specialty and
        capacity_current (= capacity_visits, SUM across plan_type rows) and
        market_max_demand (= ma_demand_visits, MAX across plan_type rows
        where it repeats) from cfg.table("dc_gap") (36). market_max_demand
        derivation: eligibles-based demand, ceiling context only.
        demand_current_book reproduces 33_dc_demand.py exactly: the
        rate_resolved, state_morbidity_mix, and blended_rate CTEs (33 lines
        28-71) are copied verbatim (rates from cfg.table("dc_rate"), mix
        from cfg.table("dc_member_dim")), and the market_demand CTE's
        SUM(eligibles_in_band x band_rate) (33 lines 66-79) is reproduced
        with eligibles_in_band replaced by December 2025 enrolled member
        counts (A870800_medicare_analysis_membership, age_nbr >= 60) in the
        same five v1 age bands; the county x specialty_ctg_cd result is
        bridged to cms_specialty via cfg.base("ref_specialty_crosswalk") on
        aetna_cd, the 36 join pattern.
GRAIN : county_fips x cms_specialty (within state_cd).
INPUTS: cfg.table("dc_gap"), cfg.table("dc_rate"), cfg.table("dc_member_dim"),
        cfg.base("ref_specialty_crosswalk"),
        A870800_medicare_analysis_membership
OUTPUT: dc2_baselines (BigQuery table) with sanity checks printed.
Run   : python expanded_scope/dc_v2/05_models/53_baselines_and_ceilings.py
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

GAP    = cfg.table("dc_gap")
RATE   = cfg.table("dc_rate")
MEMDIM = cfg.table("dc_member_dim")
XWALK  = cfg.base("ref_specialty_crosswalk")
MBRSHP = "anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_membership"
OUT    = cfg.src("dc2_baselines")

DDL = f"""
CREATE OR REPLACE TABLE `{OUT}`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
WITH rate_resolved AS (
  SELECT
    r.state_cd,
    r.specialty_ctg_cd,
    r.specialty_desc,
    r.age_band,
    r.morbidity_level,
    CASE WHEN r.is_thin_cell THEN n.rate_ma_proxy ELSE r.rate_ma_proxy END AS rate_used,
    r.is_thin_cell AS used_fallback
  FROM `{RATE}` r
  LEFT JOIN `{RATE}` n
    ON n.state_cd = 'ALL' AND n.specialty_ctg_cd = r.specialty_ctg_cd
    AND n.age_band = r.age_band AND n.morbidity_level = r.morbidity_level
  WHERE r.state_cd != 'ALL'
),
state_morbidity_mix AS (
  SELECT
    state_cd,
    age_band,
    morbidity_level,
    COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY state_cd, age_band) AS mix_share
  FROM `{MEMDIM}`
  WHERE age_band != 'UNDER_60'
  GROUP BY 1, 2, 3
),
blended_rate AS (
  SELECT
    rr.state_cd,
    rr.specialty_ctg_cd,
    ANY_VALUE(rr.specialty_desc) AS specialty_desc,
    rr.age_band,
    SUM(rr.rate_used * mx.mix_share) AS band_rate,
    MAX(CAST(rr.used_fallback AS INT64)) AS any_fallback_in_band
  FROM rate_resolved rr
  JOIN state_morbidity_mix mx
    ON rr.state_cd = mx.state_cd AND rr.age_band = mx.age_band
    AND rr.morbidity_level = mx.morbidity_level
  GROUP BY rr.state_cd, rr.specialty_ctg_cd, rr.age_band
),
enrolled AS (
  SELECT
    mbr_county_cd AS county_fips,
    mbr_state     AS state_cd,
    CASE WHEN age_nbr BETWEEN 60 AND 64 THEN '60-64'
         WHEN age_nbr BETWEEN 65 AND 69 THEN '65-69'
         WHEN age_nbr BETWEEN 70 AND 74 THEN '70-74'
         WHEN age_nbr BETWEEN 75 AND 79 THEN '75-79'
         ELSE '80+' END AS age_band,
    COUNT(DISTINCT member_id) AS enrolled_in_band
  FROM `{MBRSHP}`
  WHERE age_nbr >= 60
    AND CAST(eff_yr AS INT64) = 2025
    AND CAST(eff_mo AS INT64) = 12
  GROUP BY 1, 2, 3
),
book_demand_ctg AS (
  SELECT
    e.county_fips,
    b.specialty_ctg_cd,
    SUM(e.enrolled_in_band * b.band_rate) AS demand_current_book
  FROM enrolled e
  JOIN blended_rate b
    ON e.state_cd = b.state_cd AND e.age_band = b.age_band
  GROUP BY 1, 2
),
book_demand AS (
  SELECT
    d.county_fips,
    x.cms_specialty,
    SUM(d.demand_current_book) AS demand_current_book
  FROM book_demand_ctg d
  JOIN `{XWALK}` x ON d.specialty_ctg_cd = x.aetna_cd
  GROUP BY 1, 2
),
gap_v1 AS (
  SELECT
    state_cd,
    county_fips,
    cms_specialty,
    SUM(capacity_visits)  AS capacity_current,
    MAX(ma_demand_visits) AS market_max_demand
  FROM `{GAP}`
  GROUP BY 1, 2, 3
)
SELECT
  g.state_cd,
  g.county_fips,
  g.cms_specialty,
  g.capacity_current,
  b.demand_current_book,
  b.demand_current_book - g.capacity_current AS gap_current_book,
  g.market_max_demand,
  'v1 pipeline + cohort-clean rebuild, age 60+' AS source_note
FROM gap_v1 g
LEFT JOIN book_demand b
  ON g.county_fips = b.county_fips AND g.cms_specialty = b.cms_specialty
"""

CHECKS = {
    "row count dc2_baselines":
        f"SELECT COUNT(*) AS row_count FROM `{OUT}`",
    "measure totals":
        f"SELECT CAST(SUM(capacity_current) AS INT64) AS sum_capacity_current, "
        f"CAST(SUM(demand_current_book) AS INT64) AS sum_demand_current_book, "
        f"CAST(SUM(gap_current_book) AS INT64) AS sum_gap_current_book, "
        f"CAST(SUM(market_max_demand) AS INT64) AS sum_market_max_demand FROM `{OUT}`",
    "rows where demand_current_book > market_max_demand (should be near zero)":
        f"SELECT COUNT(*) AS violation_count FROM `{OUT}` "
        f"WHERE demand_current_book > market_max_demand",
    "top 10 such rows if any exist":
        f"SELECT state_cd, county_fips, cms_specialty, "
        f"CAST(demand_current_book AS INT64) AS demand_current_book, "
        f"CAST(market_max_demand AS INT64) AS market_max_demand "
        f"FROM `{OUT}` WHERE demand_current_book > market_max_demand "
        f"ORDER BY demand_current_book - market_max_demand DESC LIMIT 10",
}


def main():
    cfg.run_ddl(DDL, CHECKS)


if __name__ == "__main__":
    main()
