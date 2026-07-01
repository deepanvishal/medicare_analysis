-- ============================================================================
-- demand_utilization.sql   --   Demand / Utilization report (extension)
--
-- RULES (locked)
--   Payer:        member present in mdcr_base_membership -> Medicare, else Commercial
--   HCC:          HCC_v24  (used in later pages, not page 1)
--   Utilization:  COUNT(DISTINCT claim_line_id)
--   Diagnosis:    pri_icd9_dx_cd (primary only)
--   Population:   age_nbr >= 60, bucketed 60-64 / 65-69 / 70-74 / 75-79 / 80+
--
-- SOURCE TABLES (anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.)
--   A870800_medicare_analysis_2025_claims   -- claims (already filtered: no dental,
--                                              summarized, de-duped, 2025 dates)
--   mdcr_base_membership                     -- Medicare membership (presence = Medicare)
-- ============================================================================


-- ----------------------------------------------------------------------------
-- PAGE 1 : Utilization by Provider x Specialty, split by Age Bucket & Payer
--          Grain: prvdr_state x prvdr_submarket x prvdr_county x pin
--                 x specialty_ctg_cd x age_bucket x payer
-- ----------------------------------------------------------------------------
CREATE OR REPLACE TABLE `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_001_analytics_utilization_page1`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
WITH medicare_members AS (
  -- presence in this table = Medicare member
  SELECT DISTINCT member_id
  FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.mdcr_base_membership`
)
SELECT
  LEFT(c.prvdr_submarket, 2)                              AS prvdr_state,     -- state = submarket prefix
  c.prvdr_submarket,
  c.prvdr_county,
  c.srv_prvdr_id                                          AS pin,
  c.specialty_ctg_cd,
  c.specialty_ctg_cd_desc,
  CASE
    WHEN c.age_nbr BETWEEN 60 AND 64 THEN '60-64'
    WHEN c.age_nbr BETWEEN 65 AND 69 THEN '65-69'
    WHEN c.age_nbr BETWEEN 70 AND 74 THEN '70-74'
    WHEN c.age_nbr BETWEEN 75 AND 79 THEN '75-79'
    WHEN c.age_nbr >= 80             THEN '80+'
  END                                                     AS age_bucket,
  IF(mm.member_id IS NOT NULL, 'Medicare', 'Commercial')  AS payer,
  COUNT(DISTINCT c.claim_line_id)                         AS utilization
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_2025_claims` c
LEFT JOIN medicare_members mm
  ON c.member_id = mm.member_id
WHERE c.age_nbr >= 60
GROUP BY
  prvdr_state, prvdr_submarket, prvdr_county, pin,
  specialty_ctg_cd, specialty_ctg_cd_desc, age_bucket, payer;


-- ----------------------------------------------------------------------------
-- PAGE 1 -- summary check (headline totals: utilization by age bucket x payer)
-- ----------------------------------------------------------------------------
SELECT
  age_bucket,
  payer,
  COUNT(DISTINCT pin)     AS providers,
  SUM(utilization)        AS utilization
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_001_analytics_utilization_page1`
GROUP BY age_bucket, payer
ORDER BY age_bucket, payer;
