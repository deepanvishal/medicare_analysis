"""
09 - ms_stg_providers_multi_specialty   [PYTHON runner / BigQuery DDL]

WHAT : Supply side. One row per provider x cms_specialty x plan_type, with zip
       lat/long and zip-derived county_fips. RPDB network join explodes specialties.
WHY  : A multi-specialty provider counts toward every specialty it serves.
SOURCE: ms_mbr_with_all_zips + edp-prod-hcbstorage.* (RPDB_RPNPRAC, EPDB_PRVDR,
        RPDB_RINPR, PRVDR_TY_X_SPCLTY, GLOBAL_LOOKUP)
        + ms_ref_specialty_crosswalk_expanded + ms_ref_zip_reference
GRAIN : provider x cms_specialty x plan_type
NOTE : county_fips from the provider's ZIP (not the Aetna county name) -- no crosswalk,
       no collision. aetna_county_nm kept for QA (discrepancy count below). Providers
       whose zip does not resolve are dropped (z.zip_lat IS NOT NULL), as FL did.
Run  : python expanded_scope/09_stg_providers.py
"""

import config as cfg

OUT    = cfg.table("stg_providers_multi_specialty")
MBR    = cfg.table("mbr_with_all_zips")
XWALK  = cfg.table("ref_specialty_crosswalk_expanded")
ZIP    = cfg.table("ref_zip_reference")
CTY    = cfg.table("ref_county")
ABBR   = cfg.state_abbr_sql()

RPNPRAC   = "edp-prod-hcbstorage.edp_hcb_core_cnsv.RPDB_RPNPRAC"
EPDB      = "edp-prod-hcbstorage.edp_hcb_core_srcv.EPDB_PRVDR"
RINPR     = "edp-prod-hcbstorage.edp_hcb_core_srcv.RPDB_RINPR"
TYXSPCLTY = "edp-prod-hcbstorage.edp_hcb_core_cnsv.PRVDR_TY_X_SPCLTY"
LOOKUP    = "edp-prod-hcbstorage.edp_hcb_core_srcv.GLOBAL_LOOKUP"

DDL = f"""
CREATE OR REPLACE TABLE `{OUT}`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
WITH mbr_exploded AS (
  SELECT DISTINCT
    CAST(prvdr_id_no AS INT64)                                       AS pin,
    SAFE_CAST(TRIM(ntwk_id_exploded) AS INT64)                       AS ntwk_id_no,
    CAST(prvdr_id_no AS STRING)                                      AS provider_id,
    tin_owner_nm                                                     AS provider_name,
    tax_id_no,
    county_nm,
    additional_zip                                                   AS zip_cd,
    market,
    submarket,
    CASE
      WHEN prod_type = 'HMO IVL' THEN 'MA-HMO'
      WHEN prod_type = 'PPO IVL' THEN 'MA-PPO'
      ELSE prod_type
    END                                                              AS plan_type
  FROM `{MBR}`
  CROSS JOIN UNNEST(SPLIT(network_id, '-'))                          AS ntwk_id_exploded
  WHERE state IN {ABBR}
    AND network_id IS NOT NULL
    AND TRIM(ntwk_id_exploded) != ''
),
rpnprac AS (
  SELECT DISTINCT
    a.pin, a.provider_id, a.provider_name, a.tax_id_no, a.county_nm,
    a.zip_cd, a.plan_type, a.market, a.submarket,
    b.rpnp_prvdr_type_cd,
    CASE
      WHEN TRIM(c.prvdr_info_ty_cd) = 'N'
        THEN COALESCE(
          SAFE_CAST(SAFE_CAST(CONCAT(
            COALESCE(CAST(b.rpnp_spcl_majcl_cd AS STRING), ''),
            COALESCE(CAST(b.rpnp_spcl_ctgry_cd AS STRING), ''),
            COALESCE(CAST(b.rpnp_spcl_sbcls_cd AS STRING), '')
          ) AS INT64) AS STRING),
          CAST(c.prvdr_type_cd AS STRING))
      ELSE COALESCE(
          SAFE_CAST(SAFE_CAST(CONCAT(
            COALESCE(CAST(b.rpnp_spcl_majcl_cd AS STRING), ''),
            COALESCE(CAST(b.rpnp_spcl_ctgry_cd AS STRING), ''),
            COALESCE(CAST(b.rpnp_spcl_sbcls_cd AS STRING), '')
          ) AS INT64) AS STRING),
          CAST(d.rip_prvdr_type_cd AS STRING))
    END                                                              AS specialty_cd
  FROM mbr_exploded a
  LEFT JOIN `{RPNPRAC}` b
    ON CAST(a.ntwk_id_no AS INT64) = CAST(b.ntwk_id_no AS INT64)
    AND CAST(a.pin AS INT64)       = CAST(b.prvdr_id_no AS INT64) * 100 + 9
  LEFT JOIN `{EPDB}` c
    ON CAST(a.pin AS INT64) = CAST(c.prvdr_id_no AS INT64) * 100 + 9
  LEFT JOIN `{RINPR}` d
    ON CAST(a.pin AS INT64) = CAST(d.prvdr_id_no AS INT64) * 100 + 9
),
specialty_mapped AS (
  SELECT
    a.pin, a.provider_id, a.provider_name, a.tax_id_no, a.county_nm,
    a.zip_cd, a.plan_type, a.market, a.submarket, a.rpnp_prvdr_type_cd, a.specialty_cd,
    TRIM(b.specialty_ctg_cd)                                         AS specialty_ctg_cd,
    c.short_dscrptn                                                  AS specialty_cd_desc,
    d.short_dscrptn                                                  AS specialty_ctg_cd_desc,
    e.short_dscrptn                                                  AS prvdr_type_desc
  FROM rpnprac a
  LEFT JOIN `{TYXSPCLTY}` b
    ON a.rpnp_prvdr_type_cd IS NOT NULL
    AND a.specialty_cd IS NOT NULL
    AND TRIM(CAST(a.rpnp_prvdr_type_cd AS STRING)) = TRIM(CAST(b.provider_type_cd AS STRING))
    AND TRIM(CAST(a.specialty_cd AS STRING))        = TRIM(CAST(b.specialty_cd AS STRING))
  LEFT JOIN `{LOOKUP}` c
    ON TRIM(CAST(a.specialty_cd AS STRING))         = TRIM(CAST(c.global_lookup_cd AS STRING))
    AND c.lookup_column_nm                          = 'SPECIALTY_CD'
  LEFT JOIN `{LOOKUP}` d
    ON b.specialty_ctg_cd IS NOT NULL
    AND TRIM(CAST(b.specialty_ctg_cd AS STRING))    = TRIM(CAST(d.global_lookup_cd AS STRING))
    AND d.lookup_column_nm                          = 'SPECIALTY_CTG_CD'
  LEFT JOIN `{LOOKUP}` e
    ON TRIM(CAST(a.rpnp_prvdr_type_cd AS STRING))   = TRIM(CAST(e.global_lookup_cd AS STRING))
    AND e.lookup_column_nm                          = 'PROVIDER_TYPE_CD'
)
SELECT
  s.provider_id,
  s.provider_name,
  s.tax_id_no,
  s.rpnp_prvdr_type_cd,
  s.prvdr_type_desc,
  s.specialty_cd,
  s.specialty_cd_desc,
  s.specialty_ctg_cd                                                AS aetna_specialty_cd,
  s.specialty_ctg_cd_desc,
  sc.cms_specialty,
  s.county_nm                                                       AS aetna_county_nm,
  z.county_fips,
  s.zip_cd,
  z.zip_lat,
  z.zip_long,
  z.zip_radius_miles,
  s.plan_type,
  s.market,
  s.submarket,
  CASE WHEN STARTS_WITH(COALESCE(s.specialty_ctg_cd, ''), 'W') THEN 'FACILITY' ELSE 'PROVIDER' END AS record_type
FROM specialty_mapped s
LEFT JOIN `{XWALK}` sc
  ON TRIM(CAST(s.specialty_cd AS STRING)) = TRIM(CAST(sc.aetna_code AS STRING))
LEFT JOIN `{ZIP}` z
  ON TRIM(CAST(s.zip_cd AS STRING)) = TRIM(CAST(z.zip_code AS STRING))
WHERE sc.cms_specialty IS NOT NULL
  AND z.zip_lat IS NOT NULL
GROUP BY ALL
"""

CHECKS = {
    "provider rows per state":
        f"SELECT LEFT(county_fips, 2) AS state_fips, COUNT(*) AS rows, "
        f"COUNT(DISTINCT provider_id) AS providers, COUNT(DISTINCT cms_specialty) AS specialties "
        f"FROM `{OUT}` GROUP BY 1 ORDER BY 1",
    "QA: provider rows where zip-county != Aetna-reported county":
        f"SELECT COUNT(*) AS rows_with_county_discrepancy FROM `{OUT}` p "
        f"JOIN `{CTY}` rc ON p.county_fips = rc.county_fips "
        f"WHERE UPPER(TRIM(p.aetna_county_nm)) != UPPER(TRIM(rc.county_name))",
}


def main():
    cfg.run_ddl(DDL, CHECKS)


if __name__ == "__main__":
    main()
