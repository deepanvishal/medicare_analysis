"""
08 - ms_stg_beneficiaries   [PYTHON runner / BigQuery DDL]

WHAT : Demand side. One row per scope zip: population, Medicare eligibles, and
       county attributes (state_cd, county_name, county_type, threshold).
WHY  : Denominator for Test 1 access %; the county rollup grain.
SOURCE: ms_ref_zip_reference + ms_ref_county + cms_medicare_penetration
GRAIN : zip_code (carries state_cd, county_fips)
NOTE : zip lat/long joined from ms_ref_zip_reference at query time. County attrs
       from ms_ref_county. zip_medicare_eligibles = county eligibles by ACS share.
Run  : python expanded_scope/08_stg_beneficiaries.py
"""

import config as cfg

OUT   = cfg.table("stg_beneficiaries")
ZIP   = cfg.table("ref_zip_reference")
CTY   = cfg.table("ref_county")
PEN   = "anbc-hcb-prod.provider_ds_netconf_data_hcb_prod.cms_medicare_penetration"
FIPS  = cfg.state_fips_sql()

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
    SAFE_CAST(REPLACE(penetration, '%', '') AS FLOAT64) / 100         AS county_penetration_rate,
    ingest_time                                                       AS data_as_of
  FROM `{PEN}`
  CROSS JOIN latest_penetration
  WHERE fipsst IN {FIPS}
    AND ingest_time = latest_penetration.max_ingest
)
SELECT
  z.zip_code,
  rc.state_cd,
  z.county_fips,
  rc.county_name,
  rc.county_type,
  rc.compliance_threshold,
  z.zip_population                                                   AS total_population,
  ROUND(
    z.zip_population
      * COALESCE(p.county_eligibles, 0)
      / NULLIF(SUM(z.zip_population) OVER (PARTITION BY z.county_fips), 0)
  , 0)                                                               AS zip_medicare_eligibles,
  z.zip_radius_miles,
  p.county_eligibles,
  p.county_ma_enrolled,
  p.county_penetration_rate,
  p.data_as_of
FROM `{ZIP}` z
JOIN `{CTY}` rc ON z.county_fips = rc.county_fips
LEFT JOIN county_penetration p ON z.county_fips = p.county_fips
"""

CHECKS = {
    "zips + counties per state (expect FL 67, OH 88, AZ 15, IL 102)":
        f"SELECT state_cd, COUNT(*) AS zips, COUNT(DISTINCT county_fips) AS counties "
        f"FROM `{OUT}` GROUP BY state_cd ORDER BY state_cd",
}


def main():
    cfg.run_ddl(DDL, CHECKS)


if __name__ == "__main__":
    main()
