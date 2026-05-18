-- MEDICARE NETWORK ADEQUACY & CAPACITY MODELING
-- FACILITY vs PROVIDER INVESTIGATION
--
-- PROJECT:  anbc-hcb-dev
-- DATASET:  provider_ds_netconf_data_hcb_dev
-- BILLING:  anbc-dev-prv-nc-ds
-- AUTHOR:   deepan_thulasi_aetna_com
--
-- PURPOSE:  Determine if pipeline mixes facility and individual
--           provider records in facility specialty buckets, and
--           whether double counting exists.
-- RUN ORDER: Q1 → Q2 → Q3 → Q4 → Q5
-- ============================================================


-- ============================================================
-- Q1: PROVIDER TYPE CODE DESCRIPTIONS
-- What are all rpnp_prvdr_type_cd values and are they
-- facilities or individual providers?
-- ============================================================

CREATE OR REPLACE TABLE `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_facility_inv_q1_provider_type_codes`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
SELECT
    g.global_lookup_cd          AS provider_type_cd,
    g.short_dscrptn,
    g.long_dscrptn,
    COUNT(DISTINCT r.prvdr_id_no) AS provider_count
FROM `edp-prod-hcbstorage.edp_hcb_core_srcv.GLOBAL_LOOKUP` g
LEFT JOIN `edp-prod-hcbstorage.edp_hcb_core_cnsv.RPDB_RPNPRAC` r
    ON TRIM(CAST(g.global_lookup_cd AS STRING)) = TRIM(CAST(r.rpnp_prvdr_type_cd AS STRING))
WHERE g.lookup_column_nm = 'PROVIDER_TYPE_CD'
GROUP BY 1, 2, 3
ORDER BY 4 DESC;


-- ============================================================
-- Q2: EPDB_PRVDR — prvdr_info_ty_cd VALUES
-- The pipeline uses this to branch specialty_cd logic.
-- What does each value mean?
-- ============================================================

CREATE OR REPLACE TABLE `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_facility_inv_q2_epdb_prvdr_types`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
SELECT
    prvdr_info_ty_cd,
    prvdr_type_cd,
    COUNT(*) AS cnt
FROM `edp-prod-hcbstorage.edp_hcb_core_srcv.EPDB_PRVDR`
GROUP BY 1, 2
ORDER BY 3 DESC
LIMIT 50;


-- ============================================================
-- Q3: NPI ENTITY TYPE — individual vs organization
-- 'I' = individual provider
-- 'O' = organization / facility
-- Shows how many of our contracted NPIs are facilities
-- ============================================================

CREATE OR REPLACE TABLE `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_facility_inv_q3_npi_entity_type`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
SELECT
    f.rndrng_prvdr_ent_cd                       AS entity_type,
    CASE f.rndrng_prvdr_ent_cd
        WHEN 'I' THEN 'Individual Provider'
        WHEN 'O' THEN 'Organization / Facility'
        ELSE 'Unknown'
    END                                          AS entity_label,
    COUNT(DISTINCT n.npi)                        AS npi_count,
    COUNT(DISTINCT n.provider_id)                AS pin_count
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.xwalk_pin_npi_all` n
JOIN `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.cms_medicare_physician_ffs_2023` f
    ON CAST(n.npi AS STRING) = CAST(f.rndrng_npi AS STRING)
WHERE n.np_perc >= 0.5
  AND n.bad_match_ind = 0
GROUP BY 1, 2
ORDER BY 3 DESC;


-- ============================================================
-- Q4: DOUBLE COUNT CHECK
-- For facility specialty buckets, how many provider_ids
-- appear more than once per specialty per county?
-- If count > 1 for same provider_id + cms_specialty + county,
-- that is double counting.
-- ============================================================

CREATE OR REPLACE TABLE `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_facility_inv_q4_double_count`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
SELECT
    cms_specialty,
    provider_id,
    provider_name,
    aetna_county_nm,
    plan_type,
    COUNT(*)                                     AS row_count,
    COUNT(DISTINCT zip_cd)                       AS distinct_zips
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_stg_providers_multi_specialty_v2`
WHERE cms_specialty IN (
    'Acute Inpatient Hospitals',
    'Cardiac Surgery Program',
    'Critical Care ICU',
    'Surgical Services ASC',
    'Skilled Nursing Facility',
    'Diagnostic Radiology',
    'Mammography',
    'Physical Therapy',
    'Occupational Therapy',
    'Speech Therapy',
    'Inpatient Psychiatric',
    'Outpatient Infusion/Chemo',
    'Outpatient Behavioral Health'
)
GROUP BY 1, 2, 3, 4, 5
HAVING COUNT(*) > 1
ORDER BY 6 DESC
LIMIT 50;


-- ============================================================
-- Q5: FACILITY SPECIALTY BREAKDOWN BY PROVIDER TYPE
-- For each facility CMS specialty bucket, what are the
-- rpnp_prvdr_type_cd values flowing in?
-- Shows exactly what types of records are being counted
-- as facilities.
-- ============================================================

CREATE OR REPLACE TABLE `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_facility_inv_q5_facility_breakdown`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
SELECT
    p.cms_specialty,
    p.rpnp_prvdr_type_cd,
    p.prvdr_type_desc,
    COUNT(DISTINCT p.provider_id)               AS provider_count,
    COUNT(DISTINCT p.aetna_county_nm)           AS county_count
FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_stg_providers_multi_specialty_v2` p
WHERE p.cms_specialty IN (
    'Acute Inpatient Hospitals',
    'Cardiac Surgery Program',
    'Critical Care ICU',
    'Surgical Services ASC',
    'Skilled Nursing Facility',
    'Diagnostic Radiology',
    'Mammography',
    'Physical Therapy',
    'Occupational Therapy',
    'Speech Therapy',
    'Inpatient Psychiatric',
    'Outpatient Infusion/Chemo',
    'Outpatient Behavioral Health'
)
GROUP BY 1, 2, 3
ORDER BY 1, 4 DESC;
