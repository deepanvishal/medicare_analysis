-- ============================================================================
-- 21_hcc_description_join.sql   *** DEMAND / UTILIZATION EXTENSION -- PLACEHOLDER ***
--
-- Attaches a human-readable HCC description to your HCC-code summary, and (optionally)
-- the provider geography from Table 1 (ms_provider_geo).
--
-- FILL IN two tables:
--   <YOUR_HCC_SUMMARY>  = the table YOU build: claims (1 yr) x HCC code, members age 60+,
--                         summarized at HCC-code level. Expected to have at least:
--                           hcc_code, <your utilization measure(s)>, and (if per-provider) pin
--   <HCC_LABELS>        = the HCC code -> description crosswalk you load as a reference
--                         (suggest: ms_ref_hcc_labels, cols: hcc_code, hcc_description).
--
-- VERSION: make sure the HCC label crosswalk matches the model version of your
--          ICD->HCC mapping (V24 vs V28 -- the numbers mean different conditions).
-- ============================================================================


-- A) HCC summary + description (minimal).
SELECT
  s.*,                                                 -- your summarized columns (hcc_code, utilization, ...)
  l.hcc_description
FROM `<YOUR_HCC_SUMMARY>` s
LEFT JOIN `<HCC_LABELS>` l
  ON CAST(s.hcc_code AS STRING) = CAST(l.hcc_code AS STRING);


-- B) HCC summary + description + provider geography from Table 1 (if your summary is per-PIN).
--    ms_provider_geo is distinct (state_cd, submarket, county_name, pin) -- a provider can
--    span multiple counties, so a per-PIN summary fans out across their counties. Aggregate
--    afterwards at the grain you want (e.g., state/submarket/county x hcc_code).
SELECT
  g.state_cd,
  g.submarket,
  g.county_name,
  s.hcc_code,
  l.hcc_description,
  s.*                                                  -- your utilization measure(s)
FROM `<YOUR_HCC_SUMMARY>` s
LEFT JOIN `<HCC_LABELS>` l
  ON CAST(s.hcc_code AS STRING) = CAST(l.hcc_code AS STRING)
LEFT JOIN `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_ms_provider_geo` g
  ON CAST(s.pin AS STRING) = CAST(g.pin AS STRING);
