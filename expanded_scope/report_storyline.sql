-- ============================================================================
-- report_storyline.sql
--
-- PURPOSE
--   Fetches the numbers that back report_storyline.md, tab by tab, for the
--   16-page multi-state network-adequacy report (FL + OH + AZ + IL).
--   Each query is labelled S1, S2, ... and the .md references those labels.
--   Run top to bottom; paste each result into the matching [S#] slot in the .md.
--
-- METHODOLOGY (one paragraph, for the top of the story)
--   For every county x CMS specialty x plan type we ask two questions from
--   42 CFR 422.116:
--     Test 1 (access):  is the share of the county's Medicare members who have
--                       at least one in-network provider within the CMS distance
--                       >= the county's threshold (0.90 metro / 0.85 rural)?
--     Test 2 (count):   is the number of distinct in-network providers within
--                       that distance >= the CMS required count (beds for
--                       Acute Inpatient)?
--   compliance_status = COMPLIANT only if BOTH pass, else NON-COMPLIANT.
--
-- BOUNDARIES / ASSUMPTIONS (state these up front)
--   - Distance is straight-line between zip centroids (ms_fact_gap_analysis),
--     not drive time -- rural/CEAC counties are understated.
--   - Member population is ACS 2018 zip population scaled to county Medicare
--     eligibles (ms_stg_beneficiaries.zip_medicare_eligibles), not per-member.
--   - Provider location = the zip in mdcr_base_provider_mdcr_ntwk (additional_zip),
--     mapped to a county in ms_ref_zip_reference. It differs from the county_nm
--     field carried from mbr_with_zip (aetna_county_nm) -- both are shown, only
--     the zip-derived county_fips is used in the math.
--   - Telehealth credit is not applied (no telehealth flag in the data).
--   - Acute Inpatient count = SUM(hosp_list_cmi.Beds); a state with 0 beds means
--     hosp_list_cmi does not cover it.
--   - A county with no providers is included and scored NON-COMPLIANT.
--
-- TABLE SHORTHAND (all in anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.)
--   FACT = A870800_medicare_supply_demand_ms_fact_gap_analysis
--   CTY  = A870800_medicare_supply_demand_ms_ref_county
--   PROV = A870800_medicare_supply_demand_ms_stg_providers_multi_specialty
--   INV  = A870800_medicare_supply_demand_ms_week3_data_inventory
--   PAR  = A870800_medicare_supply_demand_ms_provider_par_flag
--   TD   = A870800_medicare_supply_demand_ms_ref_time_distance
-- ============================================================================


-- ============================================================================
-- TAB 1 - PROJECT OVERVIEW  (the headline)
-- ============================================================================

-- S1: scope + headline compliance, per state and overall (ROLLUP row = ALL).
SELECT
  COALESCE(state_cd, 'ALL')                                          AS state_cd,
  COUNT(DISTINCT county_fips)                                        AS counties,
  COUNT(DISTINCT cms_specialty)                                      AS specialties,
  COUNT(*)                                                           AS rows_evaluated,
  COUNTIF(compliance_status = 'COMPLIANT')                           AS compliant,
  COUNTIF(compliance_status = 'NON-COMPLIANT')                       AS non_compliant,
  ROUND(COUNTIF(compliance_status = 'COMPLIANT') / COUNT(*), 3)      AS pct_compliant
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_ms_fact_gap_analysis`
GROUP BY ROLLUP(state_cd)
ORDER BY state_cd;


-- ============================================================================
-- TAB 2 - COUNTY MAPPING  (name reconciliation across sources)
-- ============================================================================

-- S2: how many counties have the aetna_county_nm (from mbr_with_zip.county_nm)
--     differ from the county's name in ms_ref_county, per state.
SELECT
  rc.state_cd,
  COUNT(DISTINCT rc.county_fips)                                     AS counties,
  COUNT(DISTINCT IF(UPPER(TRIM(p.aetna_county_nm)) != UPPER(TRIM(rc.county_name)),
                    rc.county_fips, NULL))                           AS counties_with_a_name_diff
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_ms_ref_county` rc
LEFT JOIN `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_ms_stg_providers_multi_specialty` p
  ON rc.county_fips = p.county_fips
GROUP BY rc.state_cd
ORDER BY rc.state_cd;


-- ============================================================================
-- TAB 3 - COUNTY TYPE VALIDATION  (Census-derived vs CMS HSD county_type)
-- ============================================================================

-- S3: MATCH vs MISMATCH between the Census-derived county_type and the HSD
--     county_type (ms_ref_county.county_type), per state.
--     (Uses the same classification the report tab uses.)
WITH raw AS (
  SELECT geo_id AS county_fips, area_land_meters/2589988.11 AS area_sq_miles
  FROM `bigquery-public-data.geo_us_boundaries.counties` WHERE state_fips_code IN ('12','39','04','17')),
pop AS (
  SELECT geo_id AS county_fips, total_pop
  FROM `bigquery-public-data.census_bureau_acs.county_2020_5yr` WHERE LEFT(geo_id,2) IN ('12','39','04','17')),
cl AS (
  SELECT r.county_fips,
    CASE
      WHEN (p.total_pop>=1000000 AND p.total_pop/NULLIF(r.area_sq_miles,0)>=1000)
        OR (p.total_pop>=500000 AND p.total_pop/NULLIF(r.area_sq_miles,0)>=1500)
        OR (p.total_pop/NULLIF(r.area_sq_miles,0)>=5000) THEN 'Large Metro'
      WHEN (p.total_pop>=50000 AND p.total_pop/NULLIF(r.area_sq_miles,0)>=100)
        OR (p.total_pop>=200000 AND p.total_pop/NULLIF(r.area_sq_miles,0)>=10) THEN 'Metro'
      WHEN (p.total_pop>=50000 AND p.total_pop/NULLIF(r.area_sq_miles,0)>=10)
        OR (p.total_pop>=10000 AND p.total_pop/NULLIF(r.area_sq_miles,0)>=50) THEN 'Micro'
      WHEN p.total_pop/NULLIF(r.area_sq_miles,0)<10 THEN 'CEAC' ELSE 'Rural' END AS census_derived_type
  FROM raw r LEFT JOIN pop p USING (county_fips))
SELECT rc.state_cd,
  COUNTIF(cl.census_derived_type = rc.county_type)  AS matches,
  COUNTIF(cl.census_derived_type != rc.county_type) AS mismatches
FROM cl JOIN `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_ms_ref_county` rc
  ON cl.county_fips = rc.county_fips
GROUP BY rc.state_cd ORDER BY rc.state_cd;


-- ============================================================================
-- TAB 4 - COMPLIANCE REPORT  (the core county x specialty x plan grid)
-- ============================================================================

-- S4a: compliance by plan type, per state.
SELECT state_cd, plan_type,
  COUNTIF(compliance_status='COMPLIANT')     AS compliant,
  COUNTIF(compliance_status='NON-COMPLIANT') AS non_compliant,
  ROUND(COUNTIF(compliance_status='COMPLIANT')/COUNT(*),3) AS pct_compliant
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_ms_fact_gap_analysis`
GROUP BY state_cd, plan_type ORDER BY state_cd, plan_type;

-- S4b: which test drives failures -- access-only vs count-only vs both, per state.
SELECT state_cd,
  COUNTIF(access_compliant=FALSE AND count_compliant=TRUE)  AS fail_access_only,
  COUNTIF(access_compliant=TRUE  AND count_compliant=FALSE) AS fail_count_only,
  COUNTIF(access_compliant=FALSE AND count_compliant=FALSE) AS fail_both
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_ms_fact_gap_analysis`
GROUP BY state_cd ORDER BY state_cd;


-- ============================================================================
-- TAB 5 - SUMMARY BY SPECIALTY
-- ============================================================================

-- S5: worst 10 specialties by % of county-plan cells compliant, per state.
SELECT state_cd, cms_specialty,
  ROUND(COUNTIF(compliance_status='COMPLIANT')/COUNT(*),3) AS pct_compliant,
  COUNT(*) AS county_plan_cells
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_ms_fact_gap_analysis`
GROUP BY state_cd, cms_specialty
QUALIFY ROW_NUMBER() OVER (PARTITION BY state_cd ORDER BY pct_compliant ASC) <= 10
ORDER BY state_cd, pct_compliant;


-- ============================================================================
-- TAB 6 - SUMMARY BY COUNTY
-- ============================================================================

-- S6a: worst 10 counties by % of specialty-plan cells compliant, per state.
SELECT state_cd, county_name, county_type,
  ROUND(COUNTIF(compliance_status='COMPLIANT')/COUNT(*),3) AS pct_compliant
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_ms_fact_gap_analysis`
GROUP BY state_cd, county_name, county_type
QUALIFY ROW_NUMBER() OVER (PARTITION BY state_cd ORDER BY pct_compliant ASC) <= 10
ORDER BY state_cd, pct_compliant;

-- S6b: counties with ZERO providers anywhere (actual_count=0 for all cells) -- the
--      empty counties, all NON-COMPLIANT, per state.
SELECT state_cd, COUNT(*) AS empty_counties
FROM (
  SELECT state_cd, county_fips
  FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_ms_fact_gap_analysis`
  GROUP BY state_cd, county_fips
  HAVING MAX(actual_count) = 0)
GROUP BY state_cd ORDER BY state_cd;


-- ============================================================================
-- TAB 10 - W3 DATA INVENTORY  (county-level counts; do NOT sum across counties)
-- ============================================================================

-- S7: per state, distinct providers by pipeline (contracted / participating / cms).
--     Uses DISTINCT provider_id at STATE level (correct; the inventory table is
--     county-level and must not be summed).
SELECT state_cd,
  COUNT(DISTINCT provider_id) AS contracted_providers,
  COUNT(DISTINCT IF(aetna_par_flag=1, provider_id, NULL))               AS aetna_participating,
  COUNT(DISTINCT IF(original_medicare_flag='Y', provider_id, NULL))     AS cms_medicare
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_ms_provider_par_flag`
GROUP BY state_cd ORDER BY state_cd;


-- ============================================================================
-- TAB 11 - W3 PAR FLAGS  (participation status distribution)
-- ============================================================================

-- S8: participation_status mix, distinct providers, per state.
SELECT state_cd, participation_status, COUNT(DISTINCT provider_id) AS providers
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_ms_provider_par_flag`
GROUP BY state_cd, participation_status
ORDER BY state_cd, providers DESC;


-- ============================================================================
-- TABS 12-16 - SUBMARKET
-- ============================================================================

-- S9: submarket compliance mix per state (uses the dominant-submarket-per-county
--     crosswalk -- same as the report). NOTE: counties with no providers are not
--     in the crosswalk, so they are NOT in these submarket totals (see S10).
WITH sm AS (
  SELECT rc.state_cd, rc.county_name, p.submarket
  FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_ms_stg_providers_multi_specialty` p
  JOIN `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_ms_ref_county` rc
    ON p.county_fips = rc.county_fips
  WHERE p.submarket IS NOT NULL
  GROUP BY rc.state_cd, rc.county_name, p.submarket
  QUALIFY ROW_NUMBER() OVER (PARTITION BY rc.state_cd, rc.county_name ORDER BY COUNT(*) DESC) = 1)
SELECT sm.state_cd, sm.submarket,
  COUNT(DISTINCT sm.county_name) AS counties,
  COUNTIF(f.compliance_status='COMPLIANT')     AS compliant_cells,
  COUNTIF(f.compliance_status='NON-COMPLIANT') AS non_compliant_cells,
  ROUND(COUNTIF(f.compliance_status='COMPLIANT')/COUNT(*),3) AS pct_compliant
FROM sm
JOIN `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_ms_fact_gap_analysis` f
  ON f.state_cd=sm.state_cd AND f.county_name=sm.county_name
GROUP BY sm.state_cd, sm.submarket ORDER BY sm.state_cd, pct_compliant;

-- S10: the submarket coverage GAP -- counties in the results but MISSING from the
--      submarket crosswalk (no providers -> no submarket -> dropped from tabs 12-16).
WITH sm AS (
  SELECT DISTINCT rc.state_cd, rc.county_name
  FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_ms_stg_providers_multi_specialty` p
  JOIN `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_ms_ref_county` rc
    ON p.county_fips = rc.county_fips
  WHERE p.submarket IS NOT NULL)
SELECT f.state_cd,
  COUNT(DISTINCT f.county_name) AS counties_in_results,
  COUNT(DISTINCT IF(sm.county_name IS NULL, f.county_name, NULL)) AS counties_missing_from_submarket
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_ms_fact_gap_analysis` f
LEFT JOIN sm ON f.state_cd=sm.state_cd AND f.county_name=sm.county_name
GROUP BY f.state_cd ORDER BY f.state_cd;
