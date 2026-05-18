-- MEDICARE NETWORK ADEQUACY & CAPACITY MODELING
-- COUNTY TYPE VALIDATION
--
-- PROJECT:  anbc-hcb-dev
-- DATASET:  provider_ds_netconf_data_hcb_dev
-- PREFIX:   A870800_medicare_supply_demand_
-- AUTHOR:   deepan_thulasi_aetna_com
-- SOURCE:   42 CFR 422.116, CMS 2026 HSD Reference File
-- SCOPE:    Florida only
--
-- PURPOSE:  Validates whether Census-derived county type classification
--           matches the official CMS HSD county type for all 67 FL counties.
-- ============================================================

WITH raw_counties AS (
  SELECT
    geo_id                                                           AS county_fips,
    county_name,
    area_land_meters / 2589988.11                                   AS area_sq_miles
  FROM `bigquery-public-data.geo_us_boundaries.counties`
  WHERE state_fips_code = '12'
),

population AS (
  SELECT
    geo_id                                                           AS county_fips,
    total_pop
  FROM `bigquery-public-data.census_bureau_acs.county_2020_5yr`
  WHERE LEFT(geo_id, 2) = '12'
),

joined AS (
  SELECT
    r.county_fips,
    r.county_name,
    r.area_sq_miles,
    p.total_pop                                                      AS population,
    ROUND(p.total_pop / NULLIF(r.area_sq_miles, 0), 2)             AS pop_density
  FROM raw_counties r
  LEFT JOIN population p USING (county_fips)
),

classified AS (
  SELECT
    *,
    CASE
      WHEN (population >= 1000000 AND pop_density >= 1000)
        OR (population >= 500000  AND pop_density >= 1500)
        OR (pop_density >= 5000)                                     THEN 'Large Metro'
      WHEN (population >= 1000000 AND pop_density >= 10)
        OR (population >= 500000  AND pop_density >= 10)
        OR (population >= 200000  AND pop_density >= 10)
        OR (population >= 50000   AND pop_density >= 100)
        OR (population >= 10000   AND pop_density >= 1000)          THEN 'Metro'
      WHEN (population >= 50000   AND pop_density >= 10)
        OR (population >= 10000   AND pop_density >= 50)            THEN 'Micro'
      WHEN pop_density < 10                                          THEN 'CEAC'
      WHEN (population >= 10000   AND pop_density >= 10)
        OR (population < 10000    AND pop_density >= 50)            THEN 'Rural'
      ELSE 'Rural'
    END                                                              AS census_derived_type
  FROM joined
),

hsd_types AS (
  SELECT DISTINCT
    county_name,
    county_type                                                      AS hsd_official_type
  FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_ref_hsd_required_counts`
),

xwalk AS (
  SELECT
    aetna_county_nm,
    census_county_nm
  FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_ref_county_name_crosswalk`
)

SELECT
  c.county_fips,
  c.county_name,
  c.population,
  ROUND(c.area_sq_miles, 2)                                         AS area_sq_miles,
  c.pop_density,
  c.census_derived_type,
  h.hsd_official_type,
  CASE
    WHEN c.census_derived_type = h.hsd_official_type               THEN 'MATCH'
    ELSE                                                                 'MISMATCH'
  END                                                                AS status,
  CASE
    WHEN c.census_derived_type != h.hsd_official_type
      THEN CONCAT('Census: ', c.census_derived_type, ' | HSD: ', h.hsd_official_type)
    ELSE NULL
  END                                                                AS discrepancy_note
FROM classified c
LEFT JOIN xwalk      ON c.county_name      = xwalk.census_county_nm
LEFT JOIN hsd_types h ON xwalk.census_county_nm = h.county_name
ORDER BY status DESC, c.county_name
