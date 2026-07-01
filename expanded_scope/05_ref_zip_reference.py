"""
05 - ms_ref_zip_reference   [PYTHON runner / BigQuery DDL]

WHAT : Zip-level geography for the scope states -- centroid lat/long, area, radius,
       population, and zip -> county_fips via spatial intersection (largest overlap).
WHY  : Geographic backbone. Distance tests use zip centroids; county rollups use
       zip -> county_fips. Both stg_beneficiaries and stg_providers join here.
SOURCE: geo_us_boundaries.zip_codes + census_bureau_acs.zip_codes_2018_5yr
        + geo_us_boundaries.counties
GRAIN : zip_code
NOTE : counties filtered to scope FIPS; ST_INTERSECTS keeps border zips of neighboring
       states (as GA/AL did for FL). County attrs live in ms_ref_county (join by fips).
Run  : python expanded_scope/05_ref_zip_reference.py
"""

import config as cfg

OUT      = cfg.table("ref_zip_reference")
ZIPS     = "bigquery-public-data.geo_us_boundaries.zip_codes"
ZIP_POP  = "bigquery-public-data.census_bureau_acs.zip_codes_2018_5yr"
COUNTIES = "bigquery-public-data.geo_us_boundaries.counties"
FIPS     = cfg.state_fips_sql()

DDL = f"""
CREATE OR REPLACE TABLE `{OUT}`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
WITH zip_county AS (
  -- every zip x scope-county overlap via a spatial JOIN (a regular join CAN be
  -- spatial-only; the EXISTS semi-join could not). Rank by overlap area per zip.
  SELECT
    z.zip_code,
    z.area_land_meters,
    z.internal_point_geom,
    c.geo_id                                                       AS county_fips,
    ROW_NUMBER() OVER (
      PARTITION BY z.zip_code
      ORDER BY ST_AREA(ST_INTERSECTION(z.zip_code_geom, c.county_geom)) DESC
    )                                                              AS rnk
  FROM `{ZIPS}` z
  JOIN `{COUNTIES}` c
    ON c.state_fips_code IN {FIPS}
    AND ST_INTERSECTS(z.zip_code_geom, c.county_geom)
),
scope_zips AS (
  -- one row per zip = its largest-overlap scope county (keeps border zips)
  SELECT zip_code, area_land_meters, internal_point_geom, county_fips
  FROM zip_county
  WHERE rnk = 1
),
zip_population AS (
  SELECT geo_id AS zip_code, total_pop FROM `{ZIP_POP}`
)
SELECT
  s.zip_code,
  s.area_land_meters / 2589988.11                                AS area_sq_miles,
  ROUND(SQRT((s.area_land_meters / 2589988.11) / ACOS(-1)), 2)   AS zip_radius_miles,
  ST_Y(s.internal_point_geom)                                    AS zip_lat,
  ST_X(s.internal_point_geom)                                    AS zip_long,
  p.total_pop                                                    AS zip_population,
  s.county_fips
FROM scope_zips s
LEFT JOIN zip_population p ON s.zip_code = p.zip_code
"""

CHECKS = {
    "zips + counties per state":
        f"SELECT LEFT(county_fips, 2) AS state_fips, COUNT(*) AS zips, "
        f"COUNT(DISTINCT county_fips) AS counties FROM `{OUT}` "
        f"WHERE county_fips IS NOT NULL GROUP BY 1 ORDER BY 1",
}


def main():
    cfg.run_ddl(DDL, CHECKS)


if __name__ == "__main__":
    main()
