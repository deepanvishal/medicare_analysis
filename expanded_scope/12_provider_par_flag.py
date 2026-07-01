"""
12 - ms_provider_par_flag + ms_week3_data_inventory   [PYTHON runner / BigQuery DDL]

WHAT : Provider participation flags (Aetna claims activity + CMS Original Medicare),
       then the Week-3 data-inventory rollup (MA-contracted vs Aetna-participating vs
       CMS-Medicare provider counts). Builds BOTH tables, like the FL Step7.
WHY  : Feeds the report's W3 Par Flags + W3 Data Inventory tabs (and submarket rollups).
SOURCE: ms_stg_providers_multi_specialty + ms_ref_county + mdcr_base_claim
        + xwalk_pin_npi_all + cms_medicare_physician_ffs_2023
GRAIN : par_flag   -> provider x plan x specialty x county (carries state_cd)
        inventory  -> state x specialty x plan x county
NOTE : CMS FFS filtered to scope states (was FL-only). aetna_par_flag = had a claim
       (allowed_amt>0, HMO/PPO IVL, 2024-2025). Do NOT sum counts across counties --
       multi-location providers double-count. DATA RISK: OH/AZ/IL claims/FFS coverage.
Run  : python expanded_scope/12_provider_par_flag.py
"""

import config as cfg

PAR   = cfg.table("provider_par_flag")
INV   = cfg.table("week3_data_inventory")
PROV  = cfg.table("stg_providers_multi_specialty")
CTY   = cfg.table("ref_county")
CLAIM = cfg.src("mdcr_base_claim")
XWALK = cfg.src("xwalk_pin_npi_all")
FFS   = cfg.src("cms_medicare_physician_ffs_2023")
ABBR  = cfg.state_abbr_sql()

DDL_PAR = f"""
CREATE OR REPLACE TABLE `{PAR}`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
WITH claims_activity AS (
  SELECT
    CAST(srv_prvdr_id AS STRING)                                     AS provider_id,
    prod_type,
    COUNT(*)                                                         AS claim_count,
    SUM(allowed_amt)                                                 AS total_allowed_amt,
    MIN(srv_start_dt)                                                AS first_claim_dt,
    MAX(srv_start_dt)                                                AS last_claim_dt,
    1                                                                AS has_claims_flag
  FROM `{CLAIM}`
  WHERE prod_type IN ('HMO IVL', 'PPO IVL')
    AND EXTRACT(YEAR FROM srv_start_dt) IN (2024, 2025)
    AND allowed_amt > 0
  GROUP BY srv_prvdr_id, prod_type
),
aetna_network AS (
  SELECT DISTINCT
    CAST(p.provider_id AS STRING)                                    AS provider_id,
    p.plan_type, p.cms_specialty,
    rc.state_cd, rc.county_name, p.county_fips, p.zip_cd
  FROM `{PROV}` p
  JOIN `{CTY}` rc ON p.county_fips = rc.county_fips
),
cms_ffs AS (
  SELECT
    CAST(x.provider_id AS STRING)                                    AS provider_id,
    c.rndrng_prvdr_mdcr_prtcptg_ind                                  AS original_medicare_flag,
    SAFE_CAST(c.tot_benes AS INT64)                                  AS tot_benes,
    SAFE_CAST(c.tot_srvcs AS INT64)                                  AS tot_srvcs,
    SAFE_CAST(c.tot_mdcr_pymt_amt AS FLOAT64)                        AS tot_mdcr_pymt_amt
  FROM `{XWALK}` x
  JOIN `{FFS}` c ON CAST(x.npi AS STRING) = CAST(c.rndrng_npi AS STRING)
  WHERE x.np_perc >= 0.5
    AND x.bad_match_ind = 0
    AND c.rndrng_prvdr_state_abrvtn IN {ABBR}
)
SELECT
  a.provider_id, a.plan_type, a.cms_specialty,
  a.state_cd, a.county_name, a.county_fips, a.zip_cd,
  COALESCE(cl.has_claims_flag, 0)                                    AS aetna_par_flag,
  cl.claim_count, cl.total_allowed_amt, cl.first_claim_dt, cl.last_claim_dt,
  f.original_medicare_flag,
  COALESCE(f.tot_benes, 0)                                           AS tot_benes,
  COALESCE(f.tot_srvcs, 0)                                           AS tot_srvcs,
  COALESCE(f.tot_mdcr_pymt_amt, 0)                                   AS tot_mdcr_pymt_amt,
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
    ELSE 'CONTRACTED NOT ACTIVE - NOT IN ORIGINAL MEDICARE'
  END                                                                AS participation_status
FROM aetna_network a
LEFT JOIN claims_activity cl
  ON a.provider_id = cl.provider_id
  AND a.plan_type = CASE cl.prod_type WHEN 'HMO IVL' THEN 'MA-HMO' WHEN 'PPO IVL' THEN 'MA-PPO' END
LEFT JOIN cms_ffs f ON a.provider_id = f.provider_id
"""

DDL_INV = f"""
CREATE OR REPLACE TABLE `{INV}`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
SELECT
  p.state_cd, p.cms_specialty, p.plan_type, p.county_name,
  COUNT(DISTINCT p.provider_id)                                      AS ma_contracted_providers,
  COUNT(DISTINCT CASE WHEN p.aetna_par_flag = 1 THEN p.provider_id END)          AS aetna_participating_providers,
  COUNT(DISTINCT CASE WHEN p.original_medicare_flag = 'Y' THEN p.provider_id END) AS cms_medicare_providers
FROM `{PAR}` p
GROUP BY p.state_cd, p.cms_specialty, p.plan_type, p.county_name
"""

CHECKS_PAR = {
    "participation_status mix per state":
        f"SELECT state_cd, participation_status, COUNT(DISTINCT provider_id) AS providers "
        f"FROM `{PAR}` GROUP BY state_cd, participation_status ORDER BY state_cd, providers DESC",
}
CHECKS_INV = {
    "inventory totals per state (contracted / participating / cms) -- county-level, do NOT sum blindly":
        f"SELECT state_cd, SUM(ma_contracted_providers) AS contracted, "
        f"SUM(aetna_participating_providers) AS participating, SUM(cms_medicare_providers) AS cms "
        f"FROM `{INV}` GROUP BY state_cd ORDER BY state_cd",
}


def main():
    cfg.run_ddl(DDL_PAR, CHECKS_PAR)
    cfg.run_ddl(DDL_INV, CHECKS_INV)


if __name__ == "__main__":
    main()
