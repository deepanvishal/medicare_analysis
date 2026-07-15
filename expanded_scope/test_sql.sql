WITH cl AS (
  SELECT DISTINCT UPPER(REPLACE(TRIM(pri_icd9_dx_cd), '.', '')) AS dx
  FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_2025_claims`
  WHERE EXTRACT(YEAR FROM srv_start_dt) = 2024
)
SELECT
  COUNT(*) AS claims_distinct_codes,
  COUNTIF(m.diagnosis_code IS NOT NULL) AS found_in_map,
  ROUND(COUNTIF(m.diagnosis_code IS NOT NULL) / COUNT(*), 4) AS hit_rate
FROM cl
LEFT JOIN `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.HCC_ICD_Mapping_2025` m
  ON cl.dx = UPPER(TRIM(m.diagnosis_code));



drop table if exists anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_2025_claims;
create table anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_2025_claims
options (labels=[("owner", "deepan_thulasi_aetna_com")]) as

with mbr as (
  -- one row per member per month; monthly grain per DD 08
  select distinct
    member_id,
    extract(year  from eff_dt) as eff_yr,
    extract(month from eff_dt) as eff_mo,
    age_nbr,
    gender_cd,
    member_county_cd,
    zip_cd as mbr_zip_cd
  from edp-prod-hcbstorage.edp_hcb_core_cnsv.EMIS_MEMBERSHIP
  where medical_ind = 'Y'
    and business_ln_cd in ('CP','ME')
    and extract(year from eff_dt) between 2023 and 2025
),

zipx as (
  -- one county per zip (deterministic)
  select zip_cd,
         min(county_cd) as county_cd,
         min(state_postal_cd) as state_postal_cd
  from edp-prod-hcbstorage.edp_hcb_core_cnsv.ZIP_X_ST_X_COUNTY
  group by zip_cd
),

mkt as (
  -- one submarket per county (fips-change patched)
  select distinct
    ifnull(b.fips_new, a.county_cd) as county_cd,
    a.state, a.market, a.submarket
  from anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.mdcr_base_market a
  left join anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.MA_CT_fips_changes_xwalk b
    on a.county_cd = b.fips_old
)

select
  a.member_id
, b.age_nbr
, b.gender_cd
-- member geography (demand attribution)
, coalesce(b.member_county_cd, mz.county_cd) as mbr_county_cd
, mmkt.submarket as mbr_submarket
-- claim core
, a.srv_start_dt
, a.pri_icd9_dx_cd
, a.allowed_amt
, a.business_ln_cd
-- provider identity + geography (capacity attribution)
, case when d.epdb_dw_prvdr_id < 10 then c.epdb_dw_prvdr_id else d.epdb_dw_prvdr_id end as epdb_dw_prvdr_id
, initcap(d.county_nm) as prvdr_county
, g.submarket as prvdr_submarket
, case when d.epdb_dw_prvdr_id < 10 then c.specialty_ctg_cd else d.specialty_ctg_cd end as specialty_ctg_cd

from edp-prod-hcbstorage.edp_hcb_core_cnsv.EMIS_CLAIM_LINE a
inner join mbr b
  on a.member_id = b.member_id
  and extract(year  from a.srv_start_dt) = b.eff_yr
  and extract(month from a.srv_start_dt) = b.eff_mo
inner join edp-prod-hcbstorage.edp_hcb_core_cnsv.PROVIDER_DM c
  on a.srv_prvdr_id = c.provider_id
inner join edp-prod-hcbstorage.edp_hcb_core_cnsv.PROVIDER_DM d
  on c.epdb_dw_prvdr_id = d.provider_id
-- provider zip -> county -> submarket
left join zipx f
  on trim(cast(case when d.epdb_dw_prvdr_id < 10 then c.zip_cd else d.zip_cd end as string))
   = trim(cast(f.zip_cd as string))
left join mkt g
  on trim(cast(f.county_cd as string)) = trim(cast(g.county_cd as string))
-- member zip -> county -> submarket
left join zipx mz
  on trim(cast(b.mbr_zip_cd as string)) = trim(cast(mz.zip_cd as string))
left join mkt mmkt
  on trim(cast(coalesce(b.member_county_cd, mz.county_cd) as string))
   = trim(cast(mmkt.county_cd as string))

where a.summarized_srv_ind = 'Y'
  and a.med_cost_ctg_cd not in ('015')          -- exclude dental
  and a.ntwk_srv_area_id not in ('N0003')       -- dppo net
  and a.srv_start_dt between '2023-01-01' and '2025-12-31'
  and a.duplicate_ind = 'N'
  and ( g.submarket    in ({{SUBMARKET_LIST}})   -- provider in footprint
     or mmkt.submarket in ({{SUBMARKET_LIST}}) ) -- OR member in footprint
;



drop table if exists anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_membership;
create table anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_membership
options (labels=[("owner", "deepan_thulasi_aetna_com")]) as
with mkt as (
  select distinct
    ifnull(b.fips_new, a.county_cd) as county_cd,
    a.state, a.market, a.submarket
  from anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.mdcr_base_market a
  left join anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.MA_CT_fips_changes_xwalk b
    on a.county_cd = b.fips_old
),
zipx as (
  select zip_cd, min(county_cd) as county_cd, min(state_postal_cd) as state_postal_cd
  from edp-prod-hcbstorage.edp_hcb_core_cnsv.ZIP_X_ST_X_COUNTY
  group by zip_cd
)
select distinct
  m.member_id
, extract(year  from m.eff_dt) as eff_yr
, extract(month from m.eff_dt) as eff_mo
, m.age_nbr
, m.gender_cd
, coalesce(m.member_county_cd, z.county_cd) as mbr_county_cd
, k.state as mbr_state
, k.submarket as mbr_submarket
from edp-prod-hcbstorage.edp_hcb_core_cnsv.EMIS_MEMBERSHIP m
left join zipx z
  on trim(cast(m.zip_cd as string)) = trim(cast(z.zip_cd as string))
left join mkt k
  on trim(cast(coalesce(m.member_county_cd, z.county_cd) as string)) = trim(cast(k.county_cd as string))
where m.medical_ind = 'Y'
  and m.business_ln_cd in ('CP','ME')
  and extract(year from m.eff_dt) between 2023 and 2025
;
