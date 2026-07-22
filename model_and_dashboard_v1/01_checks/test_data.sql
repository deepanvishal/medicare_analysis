-- test_data.sql
-- Gap-check queries for 00_docs/01_DATA_DICTIONARY.md, section "Gaps to verify".
-- Run query-by-query in the BigQuery console. Read-only SELECTs only.
-- Table paths resolved from expanded_scope/config.py and the code that
-- references each table.


-- ---------------------------------------------------------------------------
-- GAP 1 RECHECK: which provider id column exists in the claims extract?
-- Conflict: user observation says only srv_prvdr_id remains, but working
-- dc_v2 code (dc_v2/03_demand/46_demand_history_table.py) selects
-- epdb_dw_prvdr_id from this same table. This decides the visit key and the
-- new-patient rule. Read: the column list settles it by observation.
-- ---------------------------------------------------------------------------
SELECT column_name, data_type
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.INFORMATION_SCHEMA.COLUMNS`
WHERE table_name = 'A870800_medicare_analysis_2025_claims'
ORDER BY ordinal_position;


-- ---------------------------------------------------------------------------
-- GAP 4a: leading-zero loss on mbr_county_cd in the claims extract.
-- Read: any rows returned means codes are stored short and LPAD padding is
-- load-bearing everywhere this column is joined.
-- ---------------------------------------------------------------------------
SELECT
  TRIM(CAST(mbr_county_cd AS STRING)) AS mbr_county_cd_value,
  COUNT(*) AS row_count
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_2025_claims`
WHERE LENGTH(TRIM(CAST(mbr_county_cd AS STRING))) < 5
GROUP BY mbr_county_cd_value
ORDER BY row_count DESC
LIMIT 20;


-- ---------------------------------------------------------------------------
-- GAP 4b: same check on the membership extract.
-- Read: same as 4a; zero rows in both means codes arrive fully padded.
-- ---------------------------------------------------------------------------
SELECT
  TRIM(CAST(mbr_county_cd AS STRING)) AS mbr_county_cd_value,
  COUNT(*) AS row_count
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_membership`
WHERE LENGTH(TRIM(CAST(mbr_county_cd AS STRING))) < 5
GROUP BY mbr_county_cd_value
ORDER BY row_count DESC
LIMIT 20;


-- ---------------------------------------------------------------------------
-- GAP 5a: prvdr_county name-match rate against the county reference.
-- Read: unmatched_share near zero means the UPPER/TRIM name join is safe;
-- anything material means name cleanup before the capacity-side join.
-- ---------------------------------------------------------------------------
SELECT
  COUNT(*) AS total_rows,
  COUNTIF(rc.county_name_u IS NOT NULL) AS matched_rows,
  SAFE_DIVIDE(COUNTIF(rc.county_name_u IS NULL), COUNT(*)) AS unmatched_share
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_2025_claims` c
LEFT JOIN (
  SELECT DISTINCT UPPER(TRIM(county_name)) AS county_name_u
  FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_ms_ref_county`
) rc
  ON UPPER(TRIM(c.prvdr_county)) = rc.county_name_u;


-- ---------------------------------------------------------------------------
-- GAP 5b: top 20 unmatched prvdr_county values so misspellings are visible.
-- Read: out-of-state names are expected; footprint-state misspellings are not.
-- ---------------------------------------------------------------------------
SELECT
  c.prvdr_county,
  COUNT(*) AS row_count
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_2025_claims` c
LEFT JOIN (
  SELECT DISTINCT UPPER(TRIM(county_name)) AS county_name_u
  FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_ms_ref_county`
) rc
  ON UPPER(TRIM(c.prvdr_county)) = rc.county_name_u
WHERE rc.county_name_u IS NULL
GROUP BY c.prvdr_county
ORDER BY row_count DESC
LIMIT 20;


-- ---------------------------------------------------------------------------
-- GAP 7a: membership extract column list (settles gender_cd presence).
-- Read: the column list is the answer.
-- ---------------------------------------------------------------------------
SELECT column_name, data_type
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.INFORMATION_SCHEMA.COLUMNS`
WHERE table_name = 'A870800_medicare_analysis_membership'
ORDER BY ordinal_position;


-- ---------------------------------------------------------------------------
-- GAP 7b: membership month coverage.
-- Read: missing year-month pairs show as holes; member counts falling off a
-- cliff in one month means a partial load.
-- ---------------------------------------------------------------------------
SELECT
  CAST(eff_yr AS INT64) AS eff_yr,
  CAST(eff_mo AS INT64) AS eff_mo,
  COUNT(*) AS row_count,
  COUNT(DISTINCT member_id) AS members
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_membership`
GROUP BY eff_yr, eff_mo
ORDER BY eff_yr, eff_mo;


-- ---------------------------------------------------------------------------
-- GAP 8a: HCC mapping column list (settles HCC_v28, is_pay_v24, is_pay_v28,
-- description presence).
-- Read: the column list is the answer.
-- ---------------------------------------------------------------------------
SELECT column_name, data_type
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.INFORMATION_SCHEMA.COLUMNS`
WHERE table_name = 'HCC_ICD_Mapping_2025'
ORDER BY ordinal_position;


-- ---------------------------------------------------------------------------
-- GAP 8b: is_pay value sets and HCC counts per combination.
-- Read: docs claim is_pay_v24 in ('Yes','No','-') and that mapped equals
-- HCC_v24 populated; this shows the actual value sets.
-- ---------------------------------------------------------------------------
SELECT
  is_pay_v24,
  is_pay_v28,
  COUNT(*) AS row_count,
  COUNT(DISTINCT HCC_v24) AS distinct_hcc_v24,
  COUNT(DISTINCT HCC_v28) AS distinct_hcc_v28
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.HCC_ICD_Mapping_2025`
GROUP BY is_pay_v24, is_pay_v28
ORDER BY row_count DESC;


-- ---------------------------------------------------------------------------
-- GAP 9a: specialty bridge coverage, per code.
-- Crosswalk path and join column from expanded_scope/36_dc_gap.py:
-- cfg.base("ref_specialty_crosswalk"), join column aetna_cd.
-- Read: NO_MATCH rows with big volume are specialty codes the 43-row bridge
-- cannot carry.
-- ---------------------------------------------------------------------------
SELECT
  c.specialty_ctg_cd,
  IF(x.aetna_cd IS NULL, 'NO_MATCH', 'MATCHED') AS bridge_status,
  COUNT(*) AS claim_lines
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_2025_claims` c
LEFT JOIN (
  SELECT DISTINCT aetna_cd
  FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_ref_specialty_crosswalk`
) x
  ON c.specialty_ctg_cd = x.aetna_cd
GROUP BY c.specialty_ctg_cd, bridge_status
ORDER BY claim_lines DESC;


-- ---------------------------------------------------------------------------
-- GAP 9b: specialty bridge coverage, totals.
-- Read: unmatched_share is the share of claim volume outside the bridge.
-- ---------------------------------------------------------------------------
SELECT
  COUNT(*) AS total_claim_lines,
  COUNTIF(x.aetna_cd IS NULL) AS unmatched_claim_lines,
  SAFE_DIVIDE(COUNTIF(x.aetna_cd IS NULL), COUNT(*)) AS unmatched_share
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_2025_claims` c
LEFT JOIN (
  SELECT DISTINCT aetna_cd
  FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_ref_specialty_crosswalk`
) x
  ON c.specialty_ctg_cd = x.aetna_cd;


-- ---------------------------------------------------------------------------
-- GAP 10: CCIR reference row count and label distribution.
-- Path from expanded_scope/30a_dc_ref_ccir.py: cfg.table("dc_ref_ccir").
-- Read: loader expected roughly 75,725 rows; NOT_CHRONIC ~52,155 /
-- CHRONIC ~12,955 / NO_DETERMINATION ~10,615.
-- ---------------------------------------------------------------------------
SELECT
  chronic_label,
  COUNT(*) AS row_count
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_ms_dc_ref_ccir`
GROUP BY chronic_label
ORDER BY row_count DESC;


-- ---------------------------------------------------------------------------
-- GAP 11: hosp_list_cmi schema.
-- Read: the column list is the answer. No repo code proves a state column
-- (expanded_scope/00_check_data_availability.py inspects the schema for the
-- same reason), so no DISTINCT-state query is written here; identify the
-- state column from this output first, then query it.
-- ---------------------------------------------------------------------------
SELECT column_name, data_type
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.INFORMATION_SCHEMA.COLUMNS`
WHERE table_name = 'hosp_list_cmi'
ORDER BY ordinal_position;


-- ---------------------------------------------------------------------------
-- GAP 12: dc2_* table freshness (every dc2_ table this repo writes).
-- Read: a missing table errors on its SELECT line; stale counts mean the
-- latest 46-55 rerun has not landed.
-- ---------------------------------------------------------------------------
SELECT 'dc2_demand_base' AS table_name, COUNT(*) AS row_count
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.dc2_demand_base`
UNION ALL
SELECT 'dc2_demand_chronic', COUNT(*)
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.dc2_demand_chronic`
UNION ALL
SELECT 'dc2_capacity_provider', COUNT(*)
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.dc2_capacity_provider`
UNION ALL
SELECT 'dc2_capacity_county', COUNT(*)
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.dc2_capacity_county`
UNION ALL
SELECT 'dc2_demand_predictions', COUNT(*)
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.dc2_demand_predictions`
UNION ALL
SELECT 'dc2_capacity_predictions', COUNT(*)
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.dc2_capacity_predictions`
UNION ALL
SELECT 'dc2_capacity_provider_future', COUNT(*)
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.dc2_capacity_provider_future`
UNION ALL
SELECT 'dc2_baselines', COUNT(*)
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.dc2_baselines`
UNION ALL
SELECT 'dc2_weave', COUNT(*)
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.dc2_weave`
ORDER BY table_name;


-- ---------------------------------------------------------------------------
-- GAP 13a: member submarket value set, claims extract.
-- Read: the footprint submarket list; NULL share shows how many rows carry
-- no submarket now that it is a context column, not a filter.
-- ---------------------------------------------------------------------------
SELECT
  mbr_submarket,
  COUNT(*) AS row_count
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_2025_claims`
GROUP BY mbr_submarket
ORDER BY row_count DESC;


-- ---------------------------------------------------------------------------
-- GAP 13b: provider submarket value set, claims extract.
-- Read: same as 13a, provider side; state = first two characters.
-- ---------------------------------------------------------------------------
SELECT
  prvdr_submarket,
  COUNT(*) AS row_count
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_2025_claims`
GROUP BY prvdr_submarket
ORDER BY row_count DESC;


-- ---------------------------------------------------------------------------
-- GAP 13c: member submarket value set, membership extract.
-- Read: should agree with 13a; disagreement means the two extracts carry
-- different submarket vintages.
-- ---------------------------------------------------------------------------
SELECT
  mbr_submarket,
  COUNT(*) AS row_count
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_membership`
GROUP BY mbr_submarket
ORDER BY row_count DESC;
