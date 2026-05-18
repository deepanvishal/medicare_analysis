-- ============================================================
-- WEEK 3 DELIVERABLE 3: PROVIDER PARTICIPATION FLAGS
-- TABLE: A870800_medicare_supply_demand_provider_par_flag
--
-- PURPOSE:
--   Provider-level participation flag combining:
--   1. Aetna claims activity (mdcr_base_claim)
--   2. CMS Original Medicare participation (cms_medicare_physician_ffs_2023)
--   3. NPI crosswalk (xwalk_pin_npi_all)
--
-- GRAIN:   provider_id × plan_type × cms_specialty × county_name
--
-- ASSUMPTION:
--   A provider is "participating" if they have at least 1 claim
--   with allowed_amt > 0 in 2024 or 2025 for HMO IVL or PPO IVL.
--
--   We do NOT use mdcr_tin_par_flag because:
--     - It is at TIN level not provider level
--     - par_flag = 1 if >50% of PINs in TIN had claims
--     - This masks individual inactive providers within active TINs
--     - For compliance we need provider-level activity signal
--
-- LIMITATION:
--   Claims-based flag only captures providers who saw patients.
--   New providers, low-volume providers, or providers seeing
--   mostly non-Medicare patients may appear as NOT ACTIVE
--   even if actively contracted.
--
--   CMS FFS file is calendar year 2023 — one year lag vs
--   Aetna claims 2024-2025. Some mismatches expected.
--
--   NPI crosswalk uses np_perc >= 0.5 and bad_match_ind = 0.
--   Providers without a confident NPI match will show as
--   NO CMS RECORD — not necessarily inactive.
--
--   Multi-location providers appear once per county.
--   COUNT(DISTINCT provider_id) at county level is correct.
--   Do NOT sum contracted/active counts across counties —
--   multi-location providers will be double-counted.
-- ============================================================

CREATE OR REPLACE TABLE `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_provider_par_flag`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS

WITH claims_activity AS (
  -- --------------------------------------------------------
  -- AETNA CLAIMS-BASED PARTICIPATION
  -- providers with actual claims in 2024-2025
  -- HMO IVL and PPO IVL only
  -- allowed_amt > 0 ensures real activity not $0 claims
  -- --------------------------------------------------------
  SELECT
    CAST(srv_prvdr_id AS STRING)                                     AS provider_id,
    prod_type,
    COUNT(*)                                                         AS claim_count,
    SUM(allowed_amt)                                                 AS total_allowed_amt,
    MIN(srv_start_dt)                                                AS first_claim_dt,
    MAX(srv_start_dt)                                                AS last_claim_dt,
    1                                                                AS has_claims_flag
  FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.mdcr_base_claim`
  WHERE prod_type IN ('HMO IVL', 'PPO IVL')
    AND EXTRACT(YEAR FROM srv_start_dt) IN (2024, 2025)
    AND allowed_amt > 0
  GROUP BY
    srv_prvdr_id,
    prod_type
),

aetna_network AS (
  -- --------------------------------------------------------
  -- AETNA CONTRACTED NETWORK
  -- restricted to 67 FL counties in ref_county_classification
  -- distinct on provider × plan × specialty × county
  -- --------------------------------------------------------
  SELECT DISTINCT
    CAST(p.provider_id AS STRING)                                    AS provider_id,
    p.plan_type,
    p.cms_specialty,
    rc.county_name,
    p.county_fips,
    p.zip_cd
  FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_stg_providers_multi_specialty_v2` p
  JOIN `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_ref_county_classification` rc
    ON p.county_fips = rc.county_fips
),

cms_ffs AS (
  -- --------------------------------------------------------
  -- CMS ORIGINAL MEDICARE PARTICIPATION
  -- join via NPI crosswalk (np_perc >= 0.5, bad_match_ind = 0)
  -- Florida only
  -- rndrng_prvdr_mdcr_prtcptg_ind = Y means provider
  -- participates in Original Medicare / accepts assignment
  -- --------------------------------------------------------
  SELECT
    CAST(x.provider_id AS STRING)                                    AS provider_id,
    c.rndrng_prvdr_mdcr_prtcptg_ind                                  AS original_medicare_flag,
    SAFE_CAST(c.tot_benes AS INT64)                                  AS tot_benes,
    SAFE_CAST(c.tot_srvcs AS INT64)                                  AS tot_srvcs,
    SAFE_CAST(c.tot_mdcr_pymt_amt AS FLOAT64)                        AS tot_mdcr_pymt_amt
  FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.xwalk_pin_npi_all` x
  JOIN `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.cms_medicare_physician_ffs_2023` c
    ON CAST(x.npi AS STRING) = CAST(c.rndrng_npi AS STRING)
  WHERE x.np_perc >= 0.5
    AND x.bad_match_ind = 0
    AND c.rndrng_prvdr_state_abrvtn = 'FL'
)

SELECT
  a.provider_id,
  a.plan_type,
  a.cms_specialty,
  a.county_name,
  a.county_fips,
  a.zip_cd,

  -- aetna claims-based participation
  COALESCE(cl.has_claims_flag, 0)                                    AS aetna_par_flag,
  cl.claim_count,
  cl.total_allowed_amt,
  cl.first_claim_dt,
  cl.last_claim_dt,

  -- cms original medicare
  f.original_medicare_flag,
  COALESCE(f.tot_benes, 0)                                           AS tot_benes,
  COALESCE(f.tot_srvcs, 0)                                           AS tot_srvcs,
  COALESCE(f.tot_mdcr_pymt_amt, 0)                                   AS tot_mdcr_pymt_amt,

  -- participation classification
  -- NULL-safe: IS NULL check before any flag comparison
  CASE
    WHEN COALESCE(cl.has_claims_flag, 0) = 1 AND f.provider_id IS NULL
      THEN 'AETNA ACTIVE - NO NPI MATCH'
    WHEN COALESCE(cl.has_claims_flag, 0) = 1 AND f.original_medicare_flag = 'Y'
      THEN 'ACTIVE BOTH'
    WHEN COALESCE(cl.has_claims_flag, 0) = 1
      THEN 'AETNA ACTIVE - NOT IN ORIGINAL MEDICARE'
    WHEN f.provider_id IS NULL
      THEN 'CONTRACTED NOT ACTIVE - NO CMS RECORD'
    WHEN f.original_medicare_flag = 'Y'
      THEN 'CONTRACTED NOT ACTIVE - IN ORIGINAL MEDICARE'
    ELSE
      'CONTRACTED NOT ACTIVE - NOT IN ORIGINAL MEDICARE'
  END                                                                AS participation_status

FROM aetna_network a
LEFT JOIN claims_activity cl
  ON a.provider_id = cl.provider_id
  AND a.plan_type  = CASE cl.prod_type
    WHEN 'HMO IVL' THEN 'MA-HMO'
    WHEN 'PPO IVL' THEN 'MA-PPO'
  END
LEFT JOIN cms_ffs f
  ON a.provider_id = f.provider_id
ORDER BY
  a.county_name,
  a.cms_specialty,
  a.plan_type;


-- ============================================================
-- WEEK 3 DELIVERABLE 1: MEDICARE DATA INVENTORY
-- Distribution by specialty, plan type, county, submarket
--
-- SOURCE: A870800_medicare_supply_demand_provider_par_flag
--         mdcr_base_claim (submarket)
--
-- COLUMNS:
--   ma_contracted_providers    = all providers in Aetna MA network
--   aetna_participating_providers = had claims 2024-2025 (aetna_par_flag = 1)
--   cms_medicare_providers     = participating in Original Medicare (flag = Y)
--
-- NOTE: counts are correct at county level only.
--       Do NOT sum across counties — multi-location providers
--       will be double-counted at state/plan level.
--
-- GRAIN: cms_specialty × plan_type × county_name
-- ============================================================

CREATE OR REPLACE TABLE `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_week3_data_inventory`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS

SELECT
  p.cms_specialty,
  p.plan_type,
  p.county_name,
  COUNT(DISTINCT p.provider_id)                                      AS ma_contracted_providers,
  COUNT(DISTINCT CASE WHEN p.aetna_par_flag = 1
    THEN p.provider_id END)                                          AS aetna_participating_providers,
  COUNT(DISTINCT CASE WHEN p.original_medicare_flag = 'Y'
    THEN p.provider_id END)                                          AS cms_medicare_providers
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_provider_par_flag` p
GROUP BY
  p.cms_specialty,
  p.plan_type,
  p.county_name
ORDER BY
  p.county_name,
  p.cms_specialty,
  p.plan_type;
