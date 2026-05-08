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
  -- all providers in stg_providers (contracted)
  -- distinct on provider × plan × specialty × county
  -- --------------------------------------------------------
  SELECT DISTINCT
    CAST(provider_id AS STRING)                                      AS provider_id,
    plan_type,
    cms_specialty,
    county_fips,
    zip_cd
  FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_stg_providers_multi_specialty_v2`
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
    CAST(c.rndrng_npi AS STRING)                                     AS npi,
    c.rndrng_prvdr_mdcr_prtcptg_ind                                  AS original_medicare_flag,
    c.rndrng_prvdr_ent_cd                                            AS entity_type,
    c.rndrng_prvdr_type                                              AS cms_provider_type,
    c.rndrng_prvdr_zip5                                              AS cms_zip,
    CAST(c.tot_benes AS INT64)                                       AS tot_benes,
    CAST(c.tot_srvcs AS INT64)                                       AS tot_srvcs,
    CAST(c.tot_mdcr_pymt_amt AS FLOAT64)                             AS tot_mdcr_pymt_amt
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
  a.county_fips,
  a.zip_cd,

  -- aetna claims-based participation
  COALESCE(cl.has_claims_flag, 0)                                    AS aetna_par_flag,
  cl.prod_type,
  cl.claim_count,
  cl.total_allowed_amt,
  cl.first_claim_dt,
  cl.last_claim_dt,

  -- cms original medicare
  f.npi,
  f.original_medicare_flag,
  f.entity_type,
  f.cms_provider_type,
  f.cms_zip,
  COALESCE(f.tot_benes, 0)                                           AS tot_benes,
  COALESCE(f.tot_srvcs, 0)                                           AS tot_srvcs,
  COALESCE(f.tot_mdcr_pymt_amt, 0)                                   AS tot_mdcr_pymt_amt,

  -- participation classification
  -- combines aetna claims activity + original medicare status
  CASE
    WHEN COALESCE(cl.has_claims_flag, 0) = 1
      AND f.original_medicare_flag = 'Y'
      THEN 'ACTIVE BOTH'
    WHEN COALESCE(cl.has_claims_flag, 0) = 1
      AND f.original_medicare_flag != 'Y'
      THEN 'AETNA ACTIVE - NOT IN ORIGINAL MEDICARE'
    WHEN COALESCE(cl.has_claims_flag, 0) = 1
      AND f.provider_id IS NULL
      THEN 'AETNA ACTIVE - NO NPI MATCH'
    WHEN COALESCE(cl.has_claims_flag, 0) = 0
      AND f.original_medicare_flag = 'Y'
      THEN 'CONTRACTED NOT ACTIVE - IN ORIGINAL MEDICARE'
    WHEN COALESCE(cl.has_claims_flag, 0) = 0
      AND f.provider_id IS NULL
      THEN 'CONTRACTED NOT ACTIVE - NO CMS RECORD'
    ELSE
      'CONTRACTED NOT ACTIVE - NOT IN ORIGINAL MEDICARE'
  END                                                                AS participation_status

FROM aetna_network a
LEFT JOIN claims_activity cl
  ON a.provider_id = cl.provider_id
  AND a.plan_type  = cl.prod_type
LEFT JOIN cms_ffs f
  ON a.provider_id = f.provider_id
ORDER BY
  a.county_fips,
  a.cms_specialty,
  participation_status;


-- ============================================================
-- DRILLDOWN SUMMARY
-- run after the table above is created
-- ============================================================

SELECT
  cms_specialty,
  plan_type,
  county_fips,
  participation_status,
  COUNT(DISTINCT provider_id)                                        AS provider_count,
  SUM(tot_benes)                                                     AS total_medicare_benes_served,
  SUM(total_allowed_amt)                                             AS total_aetna_allowed_amt,
  ROUND(AVG(tot_mdcr_pymt_amt), 2)                                   AS avg_cms_payment
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_provider_par_flag`
GROUP BY
  cms_specialty,
  plan_type,
  county_fips,
  participation_status
ORDER BY
  cms_specialty,
  county_fips,
  participation_status;
