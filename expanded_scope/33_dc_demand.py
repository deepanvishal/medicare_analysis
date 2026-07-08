"""
33 - ms_dc_demand   [PYTHON runner / BigQuery DDL]   *** DEMAND/CAPACITY EXTENSION -- M4 ***

WHAT : Demand. One row per county_fips x specialty_ctg_cd: expected market visits
       (eligibles x blended rate) and Aetna MA visits (members x resolved rate).
WHY  : The demand side of the demand/capacity gap; converts rates and population
       into visit volumes per county and specialty.
SOURCE: ms_dc_rate + ms_dc_county_population + ms_dc_member_dim
GRAIN : county_fips x specialty_ctg_cd
NOTE : Market demand applies the state morbidity mix to every county (county_morbidity_index pending);
       rates are MA-proxy (rate_basis = MA_PROXY). Thin cells resolve to the ALL-state pooled rate and
       are counted in pct_cells_thin.
Run  : python expanded_scope/33_dc_demand.py
"""

import config as cfg

OUT    = cfg.table("dc_demand")
RATE   = cfg.table("dc_rate")
POP    = cfg.table("dc_county_population")
MEMDIM = cfg.table("dc_member_dim")

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
market_demand AS (
  SELECT
    p.state_cd,
    p.county_fips,
    ANY_VALUE(p.county_name) AS county_name,
    b.specialty_ctg_cd,
    ANY_VALUE(b.specialty_desc) AS specialty_desc,
    SUM(p.eligibles_in_band * b.band_rate) AS market_demand_visits,
    SAFE_DIVIDE(SUM(CAST(b.any_fallback_in_band AS INT64)), COUNT(*)) AS pct_cells_thin
  FROM `{POP}` p
  JOIN blended_rate b
    ON p.state_cd = b.state_cd AND p.age_band = b.age_band
  GROUP BY p.state_cd, p.county_fips, b.specialty_ctg_cd
),
member_cells AS (
  SELECT state_cd, county_fips, age_band, morbidity_level, COUNT(*) AS members
  FROM `{MEMDIM}`
  WHERE age_band != 'UNDER_60' AND county_fips IS NOT NULL
  GROUP BY 1, 2, 3, 4
),
ma_demand AS (
  SELECT
    mc.county_fips,
    rr.specialty_ctg_cd,
    SUM(mc.members * rr.rate_used) AS ma_demand_visits
  FROM member_cells mc
  JOIN rate_resolved rr
    ON mc.state_cd = rr.state_cd AND mc.age_band = rr.age_band
    AND mc.morbidity_level = rr.morbidity_level
  GROUP BY 1, 2
)
SELECT
  md.state_cd,
  md.county_fips,
  md.county_name,
  md.specialty_ctg_cd,
  md.specialty_desc,
  md.market_demand_visits,
  COALESCE(ma.ma_demand_visits, 0) AS ma_demand_visits,
  'MA_PROXY' AS rate_basis,
  md.pct_cells_thin
FROM market_demand md
LEFT JOIN ma_demand ma
  ON md.county_fips = ma.county_fips AND md.specialty_ctg_cd = ma.specialty_ctg_cd
"""

CHECKS = {
    "demand rows and counties per state":
        f"SELECT state_cd, COUNT(DISTINCT county_fips) AS counties, COUNT(*) AS row_count "
        f"FROM `{OUT}` GROUP BY 1 ORDER BY 1",
    "top 10 market demand cells (eyeball: big counties x big specialties)":
        f"SELECT state_cd, county_name, specialty_ctg_cd, "
        f"CAST(market_demand_visits AS INT64) AS market_demand "
        f"FROM `{OUT}` ORDER BY market_demand_visits DESC LIMIT 10",
    "market vs ma demand ratio per state (market should exceed ma everywhere)":
        f"SELECT state_cd, CAST(SUM(market_demand_visits) AS INT64) AS market, "
        f"CAST(SUM(ma_demand_visits) AS INT64) AS ma FROM `{OUT}` GROUP BY 1 ORDER BY 1",
    "thin-cell exposure (counties with pct_cells_thin > 0.5)":
        f"SELECT state_cd, COUNT(DISTINCT county_fips) AS counties_majority_thin "
        f"FROM `{OUT}` WHERE pct_cells_thin > 0.5 GROUP BY 1 ORDER BY 1",
}


def main():
    cfg.run_ddl(DDL, CHECKS)


if __name__ == "__main__":
    main()
