-- ============================================================
-- diagnose_county_mismatch.sql   [ad-hoc diagnostic -- run manually in BQ]
--
-- Context: 09_stg_providers' QA check reported ~1.46M provider rows where the
-- zip-derived county name != the Aetna-reported county name (aetna_county_nm).
-- That crude check only does UPPER(TRIM(...)), so it over-counts formatting
-- differences (St. Lucie vs ST LUCIE, Miami-Dade vs MIAMI DADE, DeSoto vs DE SOTO).
--
-- These queries separate REAL county mismatches from formatting noise, and show
-- what the mismatches actually look like. NOTE: aetna_county_nm feeds NOTHING in
-- the compliance math (Test 1/Test 2 are distance-based; Acute beds use the
-- zip-derived county_fips) -- this is informational / data-quality only.
--
-- Tables:
--   P  = A870800_medicare_supply_demand_ms_stg_providers_multi_specialty
--   RC = A870800_medicare_supply_demand_ms_ref_county
-- ============================================================


-- 1. Summary: how much of the mismatch is REAL vs just formatting?
--    real_county_mismatch  -> normalized names still differ (genuinely diff county)
--    formatting_only       -> same county, different spelling/punctuation/case
SELECT
  COUNT(*)              AS total_provider_rows,
  COUNTIF(crude_diff)   AS crude_flagged,            -- what 09 reports (~1.46M)
  COUNTIF(crude_diff AND na != nz) AS real_county_mismatch,
  COUNTIF(crude_diff AND na  = nz) AS formatting_only
FROM (
  SELECT
    UPPER(TRIM(p.aetna_county_nm)) != UPPER(TRIM(rc.county_name))                  AS crude_diff,
    UPPER(REGEXP_REPLACE(REPLACE(p.aetna_county_nm,'Saint','St'), r'[^A-Za-z0-9]','')) AS na,
    UPPER(REGEXP_REPLACE(REPLACE(rc.county_name,   'Saint','St'), r'[^A-Za-z0-9]','')) AS nz
  FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_ms_stg_providers_multi_specialty` p
  JOIN `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_ms_ref_county` rc
    ON p.county_fips = rc.county_fips
);


-- 2. Top mismatched name pairs (eyeball: formatting vs genuinely different county).
SELECT
  p.aetna_county_nm,
  rc.county_name                                                     AS zip_county,
  LEFT(p.county_fips, 2)                                             AS state_fips,
  COUNT(*)                                                           AS row_count
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_ms_stg_providers_multi_specialty` p
JOIN `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_ms_ref_county` rc
  ON p.county_fips = rc.county_fips
WHERE UPPER(TRIM(p.aetna_county_nm)) != UPPER(TRIM(rc.county_name))
GROUP BY 1, 2, 3
ORDER BY row_count DESC
LIMIT 25;


-- 3. What does aetna_county_nm actually look like? (is it a clean county field,
--    or a market/region label / codes?). Sample of distinct raw values.
SELECT
  aetna_county_nm,
  COUNT(*)                                                           AS row_count
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_ms_stg_providers_multi_specialty`
GROUP BY 1
ORDER BY row_count DESC
LIMIT 40;


-- ============================================================
-- HYPOTHESIS: the mismatch is home-county (aetna_county_nm) vs practice-location
-- county (county_fips, from additional_zip). Providers have MANY practice zips.
-- Queries 4-5 confirm it.
-- ============================================================

-- 4. Do providers span multiple counties via their practice zips? (the likely cause)
SELECT
  COUNT(*)                                                           AS providers,
  COUNTIF(practice_counties > 1)                                     AS providers_multi_county,
  ROUND(AVG(practice_counties), 2)                                   AS avg_counties_per_provider,
  MAX(practice_counties)                                             AS max_counties_per_provider
FROM (
  SELECT provider_id, COUNT(DISTINCT county_fips) AS practice_counties
  FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_ms_stg_providers_multi_specialty`
  GROUP BY provider_id
);

-- 5. Sample: one home county (aetna_county_nm) vs many practice counties.
SELECT
  provider_id,
  ANY_VALUE(aetna_county_nm)                                         AS home_county,
  COUNT(DISTINCT county_fips)                                        AS practice_counties,
  STRING_AGG(DISTINCT county_fips ORDER BY county_fips)              AS practice_fips
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_ms_stg_providers_multi_specialty`
GROUP BY provider_id
HAVING practice_counties > 1
ORDER BY practice_counties DESC
LIMIT 20;


-- ============================================================
-- SCALE (in PROVIDERS, not rows -- the rows counts above double-count providers
-- across specialty x plan x practice-zip). This is the real size of the problem.
--   providers_ok  -> home county IS one of the provider's practice counties
--   providers_off -> home county is NOWHERE in the provider's practice zips
--   providers_off / total_providers = the actual scale.
-- ============================================================

-- 6. Provider-level match vs mismatch.
SELECT
  COUNT(*)                        AS total_providers,
  COUNTIF(home_in_practice)       AS providers_ok,
  COUNTIF(NOT home_in_practice)   AS providers_off
FROM (
  SELECT
    p.provider_id,
    LOGICAL_OR(
      UPPER(REGEXP_REPLACE(REPLACE(p.aetna_county_nm,'Saint','St'), r'[^A-Za-z0-9]','')) =
      UPPER(REGEXP_REPLACE(REPLACE(rc.county_name,   'Saint','St'), r'[^A-Za-z0-9]',''))
    ) AS home_in_practice
  FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_ms_stg_providers_multi_specialty` p
  JOIN `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_ms_ref_county` rc
    ON p.county_fips = rc.county_fips
  GROUP BY p.provider_id
);
