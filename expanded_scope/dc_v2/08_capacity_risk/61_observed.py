"""
61 - observed throughput   [PYTHON runner / BigQuery DDL]

WHAT  : Builds cap_observed_detail - observed throughput per provider from
        both sources. Internal Aetna MA claims at DAY grain (one claims scan
        serves modules 61 and 62); CMS by-Provider summary at YEAR grain
        (no procedure detail, hcpcs_cd NULL). Capacity attribution:
        prvdr_county only - never mbr_county_cd. Both provider keys carried
        (epdb_dw_prvdr_id + npi via xwalk_pin_npi_all, PLAN.md locked fact).
GRAIN : AETNA_MA rows -> epdb_dw_prvdr_id x hcpcs_cd x prvdr_county x
        specialty_ctg_cd x period_start (day)
        CMS_FFS rows  -> npi x year (2023; one row per npi)
INPUTS: anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_2025_claims
        anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.xwalk_pin_npi_all
        anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.cms_medicare_physician_ffs_2023
        edp-prod-hcbstorage.edp_hcb_core_cnsv.ZIP_X_ST_X_COUNTY
        ms_ref_county
OUTPUT: cap_observed_detail (BigQuery table) with sanity checks printed to
        stdout. No files written.
Run   : python expanded_scope/dc_v2/08_capacity_risk/61_observed.py
"""

import os
import sys
import time


def _expanded_scope_dir():
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        return os.path.dirname(os.path.dirname(here))
    except NameError:
        probe = os.getcwd()
        for _ in range(6):
            if os.path.isfile(os.path.join(probe, "config.py")):
                return probe
            cand = os.path.join(probe, "expanded_scope")
            if os.path.isfile(os.path.join(cand, "config.py")):
                return cand
            probe = os.path.dirname(probe)
        raise FileNotFoundError(
            "config.py not found - run from the repo root or any folder inside it")


sys.path.insert(0, _expanded_scope_dir())
import config as cfg

RUN_MODE = "sample"   # 'sample' = 1% of members, timed; 'full' after timing review

OUT    = cfg.table("cap_observed_detail")
CLAIMS = "anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_2025_claims"
XWALK  = cfg.src("xwalk_pin_npi_all")
FFS    = cfg.src("cms_medicare_physician_ffs_2023")
CTY    = cfg.table("ref_county")
ZIPX   = "edp-prod-hcbstorage.edp_hcb_core_cnsv.ZIP_X_ST_X_COUNTY"
ABBR   = cfg.state_abbr_sql()

SAMPLE = ("AND MOD(ABS(FARM_FINGERPRINT(CAST(c.member_id AS STRING))), 100) = 0"
          if RUN_MODE == "sample" else "")

DDL = f"""
CREATE OR REPLACE TABLE `{OUT}`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
WITH xwalk_best AS (
  SELECT
    TRIM(CAST(provider_id AS STRING)) AS provider_id,
    TRIM(CAST(npi AS STRING))         AS npi
  FROM `{XWALK}`
  WHERE np_perc >= 0.5 AND bad_match_ind = 0
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY TRIM(CAST(provider_id AS STRING)) ORDER BY np_perc DESC) = 1
),
internal_day AS (
  SELECT
    TRIM(CAST(c.epdb_dw_prvdr_id AS STRING)) AS epdb_dw_prvdr_id,
    c.specialty_ctg_cd,
    NULLIF(TRIM(c.prvdr_county), '')         AS prvdr_county,
    UPPER(LEFT(c.prvdr_submarket, 2))        AS prvdr_state_cd,
    UPPER(TRIM(CAST(c.prcdr_cd AS STRING)))  AS hcpcs_cd,
    c.srv_start_dt                           AS period_start,
    COUNT(DISTINCT c.member_id)              AS svc_cnt,
    COUNT(DISTINCT c.member_id)              AS mbr_cnt
  FROM `{CLAIMS}` c
  WHERE c.srv_start_dt BETWEEN '2024-01-01' AND '2025-12-31'
    {SAMPLE}
  GROUP BY 1, 2, 3, 4, 5, 6
),
zipx AS (
  SELECT zip_cd, MIN(county_cd) AS county_cd
  FROM `{ZIPX}`
  GROUP BY zip_cd
),
cms_year AS (
  SELECT
    TRIM(CAST(f.rndrng_npi AS STRING))  AS npi,
    rc.county_name                      AS prvdr_county,
    f.rndrng_prvdr_state_abrvtn         AS prvdr_state_cd,
    SAFE_CAST(f.med_tot_srvcs AS INT64) AS svc_cnt,
    SAFE_CAST(f.tot_benes AS INT64)     AS mbr_cnt
  FROM `{FFS}` f
  LEFT JOIN zipx z
    ON TRIM(CAST(f.rndrng_prvdr_zip5 AS STRING)) = TRIM(CAST(z.zip_cd AS STRING))
  LEFT JOIN `{CTY}` rc
    ON LPAD(TRIM(CAST(z.county_cd AS STRING)), 5, '0')
     = LPAD(TRIM(CAST(rc.county_fips AS STRING)), 5, '0')
  WHERE f.rndrng_prvdr_ent_cd = 'I'
    AND f.rndrng_prvdr_state_abrvtn IN {ABBR}
)
SELECT
  x.npi,
  i.epdb_dw_prvdr_id,
  i.specialty_ctg_cd,
  i.prvdr_county,
  i.prvdr_state_cd,
  i.hcpcs_cd,
  'AETNA_MA'           AS src,
  'DAY'                AS period_type,
  i.period_start,
  i.svc_cnt,
  i.mbr_cnt,
  CURRENT_TIMESTAMP()  AS load_ts
FROM internal_day i
LEFT JOIN xwalk_best x
  ON i.epdb_dw_prvdr_id = x.provider_id
UNION ALL
SELECT
  npi,
  CAST(NULL AS STRING) AS epdb_dw_prvdr_id,
  CAST(NULL AS STRING) AS specialty_ctg_cd,
  prvdr_county,
  prvdr_state_cd,
  CAST(NULL AS STRING) AS hcpcs_cd,
  'CMS_FFS'            AS src,
  'YEAR'               AS period_type,
  DATE '2023-01-01'    AS period_start,
  svc_cnt,
  mbr_cnt,
  CURRENT_TIMESTAMP()  AS load_ts
FROM cms_year
"""

CHECKS = {
    "row counts by src":
        f"SELECT src, COUNT(*) AS n FROM `{OUT}` GROUP BY src ORDER BY src",
    "npi match rate internal side":
        f"SELECT ROUND(COUNTIF(npi IS NOT NULL) / COUNT(*), 4) AS pct_rows_matched, "
        f"ROUND(COUNT(DISTINCT IF(npi IS NOT NULL, epdb_dw_prvdr_id, NULL)) "
        f"/ COUNT(DISTINCT epdb_dw_prvdr_id), 4) AS pct_providers_matched, "
        f"COUNTIF(npi IS NULL) AS npi_null_rows, "
        f"COUNTIF(prvdr_county IS NULL) AS null_county_rows "
        f"FROM `{OUT}` WHERE src = 'AETNA_MA'",
    "distinct counties by src":
        f"SELECT src, COUNT(DISTINCT prvdr_county) AS n_counties, "
        f"COUNTIF(prvdr_county IS NULL) AS null_county_rows "
        f"FROM `{OUT}` GROUP BY src ORDER BY src",
    "sample of 5 county names per src (format check)":
        f"SELECT src, ARRAY_TO_STRING(ARRAY_AGG(DISTINCT prvdr_county IGNORE NULLS LIMIT 5), ' | ') "
        f"AS sample_counties FROM `{OUT}` GROUP BY src ORDER BY src",
}


def _run(client, label, sql):
    t0 = time.time()
    result = client.query(sql).result()
    print(f"[{label}] {time.time() - t0:.1f}s")
    return result


def main():
    print(f"RUN_MODE = {RUN_MODE}")
    client = cfg.client()
    _run(client, "create cap_observed_detail", DDL)
    print("table created/replaced")
    for label, q in CHECKS.items():
        print(f"--- {label} ---")
        for row in _run(client, label, q):
            print("  ", dict(row))


if __name__ == "__main__":
    main()


# REVIEW
# Reviewer 1 LOGIC:
#  - Attribution: prvdr_county on both sides (capacity lens); mbr_county_cd
#    never read. Internal state via UPPER(LEFT(prvdr_submarket, 2)) is the
#    PLAN.md locked fact.
#  - xwalk join keys taken from 12_provider_par_flag.py (provider_id, npi,
#    np_perc >= 0.5, bad_match_ind = 0). Claims key epdb_dw_prvdr_id equals
#    xwalk provider_id per PLAN.md locked fact ("both provider keys ...
#    joined via xwalk_pin_npi_all"); not independently provable from repo
#    code - the npi-match-rate print is the runtime check (a rate near zero
#    means the id spaces differ; STOP and revisit).
#  - zip5 -> county_cd via the test_sql.sql zipx pattern (MIN per zip);
#    county_cd -> county_name via ms_ref_county.county_fips with LPAD on
#    both sides (PLAN.md AZ leading-zero fact).
# Reviewer 2 SPEC (deviations listed):
#  - prvdr_state_cd column ADDED (not in data model): module 62's cohort is
#    specialty x state and cap_observed_detail is 62's only input. Needs a
#    doc micro-amendment.
#  - epdb_dw_prvdr_id column ADDED per PLAN.md both-keys locked fact.
#  - CMS rows: specialty_ctg_cd NULL (rndrng_prvdr_type is a different code
#    space, no mapping exists); epdb_dw_prvdr_id NULL (reverse xwalk not
#    requested); rows with unmapped zip keep prvdr_county NULL (printed).
#  - svc_cnt = mbr_cnt on AETNA_MA rows by construction: at day grain the
#    48 visit definition (distinct member x provider x date) collapses to
#    COUNT(DISTINCT member_id), the same expression as mbr_cnt.
#  - xwalk deduped to one npi per provider_id (highest np_perc) - not in
#    the source pattern, required to protect the output grain.
# Reviewer 3 EFFICIENCY:
#  - Claims table scanned exactly once. xwalk deduped before join (no
#    fan-out); zipx aggregated to one row per zip; FFS scanned once;
#    ref_county is ~272 rows. No CROSS JOINs. Relative cost ~ one scan of
#    the claims extract + small dimension joins; sample mode cuts the scan
#    to 1% of members.
