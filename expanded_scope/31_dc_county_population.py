"""
31 - ms_dc_county_population   [PYTHON runner / BigQuery DDL]   *** DEMAND/CAPACITY EXTENSION -- M2 ***

WHAT : County population by age band. One row per scope county_fips x age_band
       (60-64, 65-69, 70-74, 75-79, 80+): Medicare eligibles split into bands by
       ACS age shares, plus Aetna MA member counts from the member dimension.
WHY  : The demand denominator at county x age-band grain for the demand/capacity modules.
SOURCE: cms_medicare_penetration + census_bureau_acs.county_2020_5yr + ms_ref_county
        + ms_dc_member_dim
GRAIN : county_fips x age_band (five bands)
NOTE : county_morbidity_index is NULL pending the CMS Geographic Variation county risk score load;
       the 60-64 band age_share uses the census 60-64 general population bracket as a proxy for
       disability-eligible under-65 Medicare eligibles. ACS source: county_2020_5yr; if this table is
       absent at run time the query fails loudly — substitute the most recent census_bureau_acs
       county_*_5yr table and rerun.
Run  : python expanded_scope/31_dc_county_population.py
"""

import config as cfg

OUT    = cfg.table("dc_county_population")
PEN    = "anbc-hcb-prod.provider_ds_netconf_data_hcb_prod.cms_medicare_penetration"
ACS    = "bigquery-public-data.census_bureau_acs.county_2020_5yr"
CTY    = cfg.table("ref_county")
MEMDIM = cfg.table("dc_member_dim")
FIPS   = cfg.state_fips_sql()

DDL = f"""
CREATE OR REPLACE TABLE `{OUT}`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
WITH latest_penetration AS (
  SELECT MAX(ingest_time) AS max_ingest FROM `{PEN}`
),
county_penetration AS (
  SELECT
    CONCAT(LPAD(CAST(fipsst AS STRING), 2, '0'), LPAD(CAST(fipscnty AS STRING), 3, '0')) AS county_fips,
    SAFE_CAST(REPLACE(CAST(eligibles AS STRING), ',', '') AS FLOAT64) AS county_eligibles,
    enrolled                                                          AS county_ma_enrolled,
    ingest_time                                                       AS data_as_of
  FROM `{PEN}`
  CROSS JOIN latest_penetration
  WHERE fipsst IN {FIPS}
    AND ingest_time = latest_penetration.max_ingest
),
census_age AS (
  SELECT LPAD(CAST(geo_id AS STRING), 5, '0') AS county_fips, '60-64' AS age_band,
         male_60_to_61 + male_62_to_64 + female_60_to_61 + female_62_to_64 AS band_pop
  FROM `{ACS}`
  UNION ALL
  SELECT LPAD(CAST(geo_id AS STRING), 5, '0') AS county_fips, '65-69' AS age_band,
         male_65_to_66 + male_67_to_69 + female_65_to_66 + female_67_to_69 AS band_pop
  FROM `{ACS}`
  UNION ALL
  SELECT LPAD(CAST(geo_id AS STRING), 5, '0') AS county_fips, '70-74' AS age_band,
         male_70_to_74 + female_70_to_74 AS band_pop
  FROM `{ACS}`
  UNION ALL
  SELECT LPAD(CAST(geo_id AS STRING), 5, '0') AS county_fips, '75-79' AS age_band,
         male_75_to_79 + female_75_to_79 AS band_pop
  FROM `{ACS}`
  UNION ALL
  SELECT LPAD(CAST(geo_id AS STRING), 5, '0') AS county_fips, '80+' AS age_band,
         male_80_to_84 + male_85_and_over + female_80_to_84 + female_85_and_over AS band_pop
  FROM `{ACS}`
),
age_share AS (
  SELECT
    county_fips,
    age_band,
    band_pop / NULLIF(SUM(band_pop) OVER (PARTITION BY county_fips), 0) AS age_share
  FROM census_age
),
aetna_members AS (
  SELECT county_fips, age_band, COUNT(*) AS aetna_ma_members
  FROM `{MEMDIM}`
  WHERE age_band != 'UNDER_60' AND county_fips IS NOT NULL
  GROUP BY 1, 2
),
county_band_grid AS (
  SELECT rc.state_cd, rc.county_fips, rc.county_name, age_band
  FROM `{CTY}` rc
  CROSS JOIN UNNEST(['60-64', '65-69', '70-74', '75-79', '80+']) AS age_band
)
SELECT
  g.state_cd,
  g.county_fips,
  g.county_name,
  g.age_band,
  p.county_eligibles                             AS county_eligibles_total,
  a.age_share,
  ROUND(p.county_eligibles * a.age_share, 0)     AS eligibles_in_band,
  p.county_ma_enrolled,
  COALESCE(m.aetna_ma_members, 0)                AS aetna_ma_members,
  CAST(NULL AS FLOAT64)                          AS county_morbidity_index,
  p.data_as_of
FROM county_band_grid g
LEFT JOIN age_share a
  ON g.county_fips = a.county_fips AND g.age_band = a.age_band
LEFT JOIN county_penetration p
  ON g.county_fips = p.county_fips
LEFT JOIN aetna_members m
  ON g.county_fips = m.county_fips AND g.age_band = m.age_band
"""

CHECKS = {
    "counties per state (expect FL 67 / OH 88 / AZ 15 / IL 102)":
        f"SELECT state_cd, COUNT(DISTINCT county_fips) AS counties FROM `{OUT}` GROUP BY 1 ORDER BY 1",
    "band shares sum to 1 per county (max deviation)":
        f"SELECT MAX(ABS(s - 1)) AS max_dev FROM "
        f"(SELECT county_fips, SUM(age_share) AS s FROM `{OUT}` GROUP BY 1)",
    "eligibles reconcile, worst 5 counties":
        f"SELECT county_fips, ANY_VALUE(county_eligibles_total) AS total, "
        f"SUM(eligibles_in_band) AS banded FROM `{OUT}` GROUP BY 1 "
        f"ORDER BY ABS(ANY_VALUE(county_eligibles_total) - SUM(eligibles_in_band)) DESC LIMIT 5",
    "aetna members rollup vs member_dim":
        f"SELECT (SELECT SUM(aetna_ma_members) FROM `{OUT}`) AS in_pop_table, "
        f"(SELECT COUNT(*) FROM `{MEMDIM}` WHERE age_band != 'UNDER_60' "
        f"AND county_fips IS NOT NULL) AS in_member_dim",
}


def main():
    cfg.run_ddl(DDL, CHECKS)


if __name__ == "__main__":
    main()
