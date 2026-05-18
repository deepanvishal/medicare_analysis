-- ============================================================
-- TABLE: mbr_with_all_zips
-- PURPOSE: Enriches mbr_with_zip with all provider locations
--          from mdcr_base_provider_mdcr_ntwk.
--          Replaces mbr_with_zip as the source in mbr_exploded
--          CTE inside stg_providers_multi_specialty_v2 so that
--          providers with multiple practice locations contribute
--          distance calculations from each zip, not just primary.
-- GRAIN:   One row per provider per zip per network match
-- SOURCE:  A870800_medicare_supply_demand_mbr_with_zip
--          mdcr_base_provider_mdcr_ntwk
-- JOIN:    prvdr_id_no = pin
--          UNNEST(network).ntwk_id_no IN SPLIT(network_id, '-')
-- NOTE:    additional_zip is the location zip from mdcr table.
--          All original mbr_with_zip columns preserved as-is.
--          Update stg_providers_multi_specialty_v2 to use
--          additional_zip instead of zip_cd after validating.
-- ============================================================

CREATE OR REPLACE TABLE `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_mbr_with_all_zips`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
SELECT
  m.*,
  n.zip_code                                                   AS additional_zip
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_mbr_with_zip` m
JOIN (
  SELECT
    n.*,
    ntwk.ntwk_id_no                                            AS ntwk_id
  FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.mdcr_base_provider_mdcr_ntwk` n
  CROSS JOIN UNNEST(n.network) AS ntwk
) n
  ON CAST(m.prvdr_id_no AS STRING)  = CAST(n.pin AS STRING)
  AND CAST(n.ntwk_id AS STRING)    IN UNNEST(SPLIT(m.network_id, '-'))
WHERE m.state = 'FL'
  AND n.zip_code IS NOT NULL;


-- ============================================================
-- TEST 1: Find PINs with multiple zip codes in source table
-- Expected: pins with zip_count > 1
-- Run this first to get candidate PINs for TEST 2 and TEST 4
-- ============================================================

SELECT
  pin,
  COUNT(DISTINCT zip_code)                                     AS zip_count,
  STRING_AGG(DISTINCT zip_code, ', ' ORDER BY zip_code)       AS all_zips
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.mdcr_base_provider_mdcr_ntwk`
WHERE zip_code IS NOT NULL
GROUP BY pin
HAVING COUNT(DISTINCT zip_code) > 1
ORDER BY zip_count DESC
LIMIT 10;


-- ============================================================
-- TEST 2: Verify those same PINs appear with multiple zips
--         in the new table
-- Plug in pins from TEST 1 output
-- Expected: zip_count matches TEST 1 for each pin
-- If TEST 2 zip_count < TEST 1 zip_count: network join is
-- dropping rows — check if ntwk_id_no is in network_id
-- ============================================================

SELECT
  prvdr_id_no                                                  AS pin,
  COUNT(DISTINCT additional_zip)                               AS zip_count,
  STRING_AGG(DISTINCT additional_zip, ', ' ORDER BY additional_zip) AS all_zips
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_mbr_with_all_zips`
WHERE prvdr_id_no IN (
  -- paste pin values from TEST 1 here
)
GROUP BY prvdr_id_no
ORDER BY zip_count DESC;


-- ============================================================
-- TEST 3: Row count sanity check
-- Expected: mbr_with_all_zips row count >= mbr_with_zip
-- If equal: no multi-zip providers joined — investigate
-- ============================================================

SELECT 'mbr_with_zip'      AS tbl, COUNT(*) AS rows
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_mbr_with_zip`
WHERE state = 'FL'

UNION ALL

SELECT 'mbr_with_all_zips' AS tbl, COUNT(*) AS rows
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_mbr_with_all_zips`;


-- ============================================================
-- TEST 4: Spot check one PIN end-to-end
-- Pick any pin from TEST 1
-- Expected: mbr_with_zip columns intact, additional_zip varies
-- ============================================================

SELECT
  prvdr_id_no,
  tin_owner_nm,
  network_id,
  zip_cd                                                       AS primary_zip,
  additional_zip,
  plan_type,
  state
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_mbr_with_all_zips`
WHERE prvdr_id_no = '<pin_from_test1>'
ORDER BY additional_zip;
