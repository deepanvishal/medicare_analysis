"""
62 - services to raw hours   [PYTHON runner / BigQuery DDL]

WHAT  : Turns cap_observed_detail into RAW clinical hours. No deflation here
        - deflation happens in module 64 after calibration (defl columns
        created NULL). Zero-minute rule per Stage 0: unmatched, blank, or
        NULL procedure codes contribute 0 minutes; the clinical-CPT fallback
        is module 64's concern. CMS side has no procedure detail: annual
        hours = med_tot_srvcs x avg raw minutes per matched internal service
        (provider's OWN rate, else specialty x state COHORT rate; NULL when
        the provider has no internal presence at all - share printed).
GRAIN : cap_hours_daily  -> npi/epdb_dw_prvdr_id x prvdr_county x svc_dt
        (AETNA_MA only)
        cap_hours_annual -> provider x prvdr_county x src x period_yr
        (internal 2024, 2025; CMS 2023)
INPUTS: cap_observed_detail + ref_mpfs_time only. No claims scan.
OUTPUT: cap_hours_daily + cap_hours_annual (BigQuery tables) with sanity
        checks printed to stdout. No files written.
Run   : python expanded_scope/dc_v2/08_capacity_risk/62_hours.py
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

RUN_MODE = "sample"   # no claims scan in this module: sample vs full is
                      # governed entirely by what module 61 loaded

OBS    = cfg.table("cap_observed_detail")
MPFS   = cfg.table("ref_mpfs_time")
DAILY  = cfg.table("cap_hours_daily")
ANNUAL = cfg.table("cap_hours_annual")

DDL_DAILY = f"""
CREATE OR REPLACE TABLE `{DAILY}`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
SELECT
  o.npi,
  o.epdb_dw_prvdr_id,
  o.prvdr_county,
  o.period_start                        AS svc_dt,
  SUM(IF(m.hcpcs_cd IS NOT NULL,
         o.svc_cnt * COALESCE(m.intra_mins, 0), 0)) / 60 AS raw_hrs,
  CAST(NULL AS FLOAT64)                 AS defl_hrs,
  SUM(IF(m.hcpcs_cd IS NOT NULL, o.svc_cnt, 0))          AS mapped_svc_cnt,
  SUM(IF(m.hcpcs_cd IS NULL, o.svc_cnt, 0))              AS unmapped_svc_cnt,
  'AETNA_MA'                            AS src
FROM `{OBS}` o
LEFT JOIN `{MPFS}` m
  ON o.hcpcs_cd = m.hcpcs_cd
WHERE o.src = 'AETNA_MA'
GROUP BY 1, 2, 3, 4
"""

DDL_ANNUAL = f"""
CREATE OR REPLACE TABLE `{ANNUAL}`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
WITH internal_annual AS (
  SELECT
    o.npi,
    o.epdb_dw_prvdr_id,
    o.prvdr_county,
    o.prvdr_state_cd,
    o.specialty_ctg_cd,
    EXTRACT(YEAR FROM o.period_start) AS period_yr,
    SUM(IF(m.hcpcs_cd IS NOT NULL,
           o.svc_cnt * COALESCE(m.intra_mins, 0), 0)) AS raw_mins,
    SUM(o.svc_cnt)                                    AS svc_cnt_yr,
    SUM(IF(m.hcpcs_cd IS NOT NULL, o.svc_cnt, 0))     AS mapped_svc_cnt,
    SUM(IF(m.hcpcs_cd IS NULL, o.svc_cnt, 0))         AS unmapped_svc_cnt
  FROM `{OBS}` o
  LEFT JOIN `{MPFS}` m
    ON o.hcpcs_cd = m.hcpcs_cd
  WHERE o.src = 'AETNA_MA'
  GROUP BY 1, 2, 3, 4, 5, 6
),
own_avg AS (
  SELECT
    npi,
    SAFE_DIVIDE(SUM(raw_mins), SUM(mapped_svc_cnt)) AS own_avg_mins,
    SUM(mapped_svc_cnt)                             AS own_matched_svcs,
    ARRAY_AGG(specialty_ctg_cd ORDER BY svc_cnt_yr DESC LIMIT 1)[SAFE_OFFSET(0)]
                                                    AS specialty_ctg_cd
  FROM internal_annual
  WHERE npi IS NOT NULL
  GROUP BY npi
),
cohort_avg AS (
  SELECT
    specialty_ctg_cd,
    prvdr_state_cd,
    SAFE_DIVIDE(SUM(raw_mins), SUM(mapped_svc_cnt)) AS cohort_avg_mins
  FROM internal_annual
  GROUP BY 1, 2
),
cms_rows AS (
  SELECT
    npi,
    prvdr_county,
    prvdr_state_cd,
    EXTRACT(YEAR FROM period_start) AS period_yr,
    svc_cnt
  FROM `{OBS}`
  WHERE src = 'CMS_FFS'
)
SELECT
  npi,
  epdb_dw_prvdr_id,
  prvdr_county,
  prvdr_state_cd,
  specialty_ctg_cd,
  'AETNA_MA'                                    AS src,
  period_yr,
  raw_mins / 60                                 AS raw_hrs_yr,
  CAST(NULL AS FLOAT64)                         AS defl_hrs_yr,
  svc_cnt_yr,
  mapped_svc_cnt,
  unmapped_svc_cnt,
  SAFE_DIVIDE(raw_mins, mapped_svc_cnt)         AS avg_mins_per_svc,
  'OWN'                                         AS avg_mins_src_cd,
  'HOURS'                                       AS ceiling_unit_cd
FROM internal_annual
UNION ALL
SELECT
  c.npi,
  CAST(NULL AS STRING)                          AS epdb_dw_prvdr_id,
  c.prvdr_county,
  c.prvdr_state_cd,
  oa.specialty_ctg_cd,
  'CMS_FFS'                                     AS src,
  c.period_yr,
  c.svc_cnt * CASE
    WHEN oa.own_matched_svcs > 0 THEN oa.own_avg_mins
    ELSE ca.cohort_avg_mins END / 60            AS raw_hrs_yr,
  CAST(NULL AS FLOAT64)                         AS defl_hrs_yr,
  c.svc_cnt                                     AS svc_cnt_yr,
  CAST(NULL AS INT64)                           AS mapped_svc_cnt,
  CAST(NULL AS INT64)                           AS unmapped_svc_cnt,
  CASE
    WHEN oa.own_matched_svcs > 0 THEN oa.own_avg_mins
    ELSE ca.cohort_avg_mins END                 AS avg_mins_per_svc,
  CASE
    WHEN oa.own_matched_svcs > 0 THEN 'OWN'
    WHEN ca.cohort_avg_mins IS NOT NULL THEN 'COHORT'
    ELSE NULL END                               AS avg_mins_src_cd,
  'HOURS'                                       AS ceiling_unit_cd
FROM cms_rows c
LEFT JOIN own_avg oa
  ON c.npi = oa.npi
LEFT JOIN cohort_avg ca
  ON ca.specialty_ctg_cd = oa.specialty_ctg_cd
  AND ca.prvdr_state_cd = c.prvdr_state_cd
"""

CHECKS = {
    "total raw hours by src":
        f"SELECT src, ROUND(SUM(raw_hrs_yr), 0) AS raw_hrs "
        f"FROM `{ANNUAL}` GROUP BY src ORDER BY src",
    "CMS avg-mins path distribution (pct on COHORT fallback; NULL = no internal presence)":
        f"SELECT avg_mins_src_cd, COUNT(*) AS n, "
        f"ROUND(COUNT(*) / SUM(COUNT(*)) OVER (), 4) AS pct "
        f"FROM `{ANNUAL}` WHERE src = 'CMS_FFS' "
        f"GROUP BY avg_mins_src_cd ORDER BY n DESC",
    "top 10 providers by raw annual hours (eyeball check)":
        f"SELECT npi, epdb_dw_prvdr_id, specialty_ctg_cd, prvdr_county, src, "
        f"period_yr, ROUND(raw_hrs_yr, 0) AS raw_hrs_yr "
        f"FROM `{ANNUAL}` WHERE raw_hrs_yr IS NOT NULL "
        f"ORDER BY raw_hrs_yr DESC LIMIT 10",
}


def _run(client, label, sql):
    t0 = time.time()
    result = client.query(sql).result()
    print(f"[{label}] {time.time() - t0:.1f}s")
    return result


def main():
    print(f"RUN_MODE = {RUN_MODE} (content governed by module 61's run mode)")
    client = cfg.client()
    _run(client, "create cap_hours_daily", DDL_DAILY)
    print("cap_hours_daily created/replaced")
    _run(client, "create cap_hours_annual", DDL_ANNUAL)
    print("cap_hours_annual created/replaced")
    for label, q in CHECKS.items():
        print(f"--- {label} ---")
        for row in _run(client, label, q):
            print("  ", dict(row))


if __name__ == "__main__":
    main()


# REVIEW
# Reviewer 1 LOGIC:
#  - hcpcs join: both sides UPPER+TRIM at their loads (module 60 uppercases
#    ref_mpfs_time.hcpcs_cd, module 61 uppercases claims prcdr_cd), so the
#    plain equality join is key-safe.
#  - Daily and annual grains carry BOTH provider keys and group by both:
#    grouping by npi alone would merge every unmatched-npi provider into one
#    NULL-npi row per county x day. Consequence: two epdb ids sharing one
#    npi produce two rows against the data model's npi-only PK - preferred
#    over silently merging distinct providers.
#  - CMS OWN/COHORT rates use only matched internal services (denominator
#    mapped_svc_cnt), consistent with 'avg raw mins per matched internal
#    service'.
# Reviewer 2 SPEC (deviations listed):
#  - raw_hrs_yr and period_yr columns ADDED to cap_hours_annual (data model
#    has only defl_hrs_yr and a year-less grain): raw-only mandate plus
#    internal 2024 AND 2025 vs CMS 2023 make a year column necessary
#    (cross-cutting rule 9, vintages never mixed silently). defl_hrs_yr
#    created NULL for module 64. avg_mins_src_cd and prvdr_state_cd also
#    ADDED per this prompt / module 61. Doc micro-amendment needed.
#  - cap_hours_daily: mapped_svc_cnt added, fallback_svc_cnt omitted (the
#    clinical-CPT fallback runs in module 64; nothing is timed via fallback
#    here). defl_hrs NULL as specified.
#  - Internal annual rows get avg_mins_src_cd = 'OWN' (their rate IS their
#    own); spec named the codes for the CMS path only.
#  - CMS-only providers (no internal rows at all): specialty unknown ->
#    no cohort -> raw_hrs_yr NULL, avg_mins_src_cd NULL. Share visible in
#    the path-distribution print. Spec gap flagged to Deepan: needs either
#    a rndrng_prvdr_type -> specialty_ctg_cd mapping or an explicit
#    exclusion decision.
#  - Cohort includes NULL-npi internal providers' volume (valid minutes
#    signal even where the xwalk missed).
# Reviewer 3 EFFICIENCY:
#  - Zero scans of the claims table (reads cap_observed_detail only, 3
#    passes: daily, internal_annual, cms_rows - each a fraction of a claims
#    scan). ref_mpfs_time is ~10k rows. own_avg/cohort_avg are grouped
#    before joining (no fan-out); no CROSS JOINs. Relative cost: small vs
#    module 61.
