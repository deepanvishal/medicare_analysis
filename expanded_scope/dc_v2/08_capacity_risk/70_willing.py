"""
70 - willing capacity (Aetna layer)   [PYTHON runner / BigQuery DDL]

WHAT  : Applies the Aetna share ONCE, at the end (CD-06) -> cap_willing.
        aetna_share = SAFE_DIVIDE(aetna_ma_svcs, aetna_ma_svcs +
        cms_ffs_svcs), bounded [0,1]. contracted_flag from the ms_ network
        table (via the PIN->NPI xwalk). zero_utilization_flag (contracted,
        zero Aetna MA claims, CD-07) forces willing capacity to 0.
        share_stability_flag: quarterly internal volume swings beyond
        SHARE_STABILITY_TOL (cap_params).
GRAIN : npi/epdb_dw_prvdr_id x prvdr_county + prvdr_state_cd (rule 12)
INPUTS: cap_observed_detail, cap_provider_year, cap_fill_result,
        ms_stg_providers_multi_specialty, xwalk_pin_npi_all, cap_params
OUTPUT: cap_willing (BigQuery table) + sanity prints.
Run   : python expanded_scope/dc_v2/08_capacity_risk/70_willing.py
"""

# ASSUMPTION [1]: RESOLVED by M63-72 triage ruling - CD-23 governs:
#   willing_spare_hrs = (spare_hrs + team_uplift_hrs) x aetna_share
#   (absorbing capacity, not bare spare). The data model's spare-only
#   formula was stale and has been amended.
# ASSUMPTION [2]: contracted_flag = npi appears in
#   ms_stg_providers_multi_specialty via xwalk_pin_npi_all (np_perc >= 0.5,
#   bad_match_ind = 0) - the data model's open item 1 default ('ms_
#   pipeline network table').
# ASSUMPTION [3]: cms_ffs_svc_cnt is provider-level (the CMS row is one per
#   npi); it is attached to every county row of that provider, so
#   aetna_share is a PROVIDER-level ratio repeated per county, matching
#   CD-06's 'applied once to provider-level results'.
# ASSUMPTION [4]: share_stability_flag = 1 when max/min nonzero quarterly
#   internal service counts (2024-2025) exceed SHARE_STABILITY_TOL. V10
#   names no statistic; tolerance lives in cap_params (module 63, A7).
# ASSUMPTION [5]: willing_placed_cnt sums module 69's provider rows
#   (individual lane only) x aetna_share; the facility lane is out of scope
#   for willing capacity (facility ids have no cap_provider_year row).

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

RUN_MODE = "sample"   # no claims scan; governed by module 61's run

OBS    = cfg.table("cap_observed_detail")
PY     = cfg.table("cap_provider_year")
FILL   = cfg.table("cap_fill_result")
PROV   = cfg.table("stg_providers_multi_specialty")
XWALK  = cfg.src("xwalk_pin_npi_all")
PARAMS = cfg.table("cap_params")
OUT    = cfg.table("cap_willing")

TOL = (f"(SELECT param_val FROM `{PARAMS}` WHERE param_nm = 'SHARE_STABILITY_TOL' "
       f"AND param_scope = 'GLOBAL')")

DDL = f"""
CREATE OR REPLACE TABLE `{OUT}`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
WITH aetna_svc AS (
  SELECT npi, epdb_dw_prvdr_id, prvdr_county, prvdr_state_cd,
         SUM(svc_cnt) AS aetna_ma_svc_cnt
  FROM `{OBS}` WHERE src = 'AETNA_MA'
  GROUP BY 1, 2, 3, 4
),
quarterly AS (
  SELECT COALESCE(npi, epdb_dw_prvdr_id) AS pid,
         FORMAT_DATE('%Y-Q%Q', period_start) AS qtr, SUM(svc_cnt) AS svcs
  FROM `{OBS}` WHERE src = 'AETNA_MA'
  GROUP BY 1, 2
),
stability AS (
  SELECT pid,
         IF(SAFE_DIVIDE(MAX(svcs), NULLIF(MIN(svcs), 0)) > {TOL}, 1, 0)
           AS share_stability_flag
  FROM quarterly GROUP BY pid
),
cms_svc AS (
  SELECT npi, SUM(svc_cnt) AS cms_ffs_svc_cnt
  FROM `{OBS}` WHERE src = 'CMS_FFS' AND npi IS NOT NULL
  GROUP BY 1
),
contracted AS (
  SELECT DISTINCT TRIM(CAST(x.npi AS STRING)) AS npi
  FROM `{PROV}` p
  JOIN `{XWALK}` x
    ON CAST(p.provider_id AS STRING) = TRIM(CAST(x.provider_id AS STRING))
  WHERE x.np_perc >= 0.5 AND x.bad_match_ind = 0
),
placed AS (
  SELECT COALESCE(npi, epdb_dw_prvdr_id) AS pid, prvdr_county,
         SUM(COALESCE(placed_cnt, 0)) AS placed_cnt
  FROM `{FILL}`
  WHERE absorbed_by IS NULL AND (npi IS NOT NULL OR epdb_dw_prvdr_id IS NOT NULL)
  GROUP BY 1, 2
),
base AS (
  SELECT
    py.npi, py.epdb_dw_prvdr_id, py.prvdr_county, py.prvdr_state_cd,
    IF(ct.npi IS NOT NULL, 1, 0)               AS contracted_flag,
    COALESCE(av.aetna_ma_svc_cnt, 0)           AS aetna_ma_svc_cnt,
    COALESCE(cs.cms_ffs_svc_cnt, 0)            AS cms_ffs_svc_cnt,
    LEAST(GREATEST(COALESCE(SAFE_DIVIDE(av.aetna_ma_svc_cnt,
      av.aetna_ma_svc_cnt + COALESCE(cs.cms_ffs_svc_cnt, 0)), 0), 0), 1)
                                               AS aetna_share,
    COALESCE(st.share_stability_flag, 0)       AS share_stability_flag,
    IF(ct.npi IS NOT NULL AND COALESCE(av.aetna_ma_svc_cnt, 0) = 0, 1, 0)
                                               AS zero_utilization_flag,
    py.spare_hrs,
    py.team_uplift_hrs,
    pl.placed_cnt
  FROM `{PY}` py
  LEFT JOIN aetna_svc av
    ON COALESCE(py.npi, '') = COALESCE(av.npi, '')
    AND COALESCE(py.epdb_dw_prvdr_id, '') = COALESCE(av.epdb_dw_prvdr_id, '')
    AND py.prvdr_county = av.prvdr_county
  LEFT JOIN cms_svc cs ON py.npi = cs.npi
  LEFT JOIN contracted ct ON py.npi = ct.npi
  LEFT JOIN stability st ON COALESCE(py.npi, py.epdb_dw_prvdr_id) = st.pid
  LEFT JOIN placed pl
    ON COALESCE(py.npi, py.epdb_dw_prvdr_id) = pl.pid
    AND py.prvdr_county = pl.prvdr_county
)
SELECT
  npi, epdb_dw_prvdr_id, prvdr_county, prvdr_state_cd,
  contracted_flag, aetna_ma_svc_cnt, cms_ffs_svc_cnt,
  aetna_share, share_stability_flag, zero_utilization_flag,
  IF(zero_utilization_flag = 1, 0,
     (spare_hrs + COALESCE(team_uplift_hrs, 0)) * aetna_share) AS willing_spare_hrs,
  IF(zero_utilization_flag = 1, 0,
     COALESCE(placed_cnt, 0) * aetna_share)                   AS willing_placed_cnt
FROM base
"""

CHECKS = {
    "share bounds (must all be inside [0,1])":
        f"SELECT COUNTIF(aetna_share < 0 OR aetna_share > 1) AS out_of_bounds "
        f"FROM `{OUT}`",
    "contracted / zero-utilization counts (CD-07)":
        f"SELECT contracted_flag, zero_utilization_flag, COUNT(*) AS n "
        f"FROM `{OUT}` GROUP BY 1, 2 ORDER BY 1, 2",
    "share distribution (eyeball)":
        f"SELECT ROUND(APPROX_QUANTILES(aetna_share, 4)[OFFSET(1)], 3) AS p25, "
        f"ROUND(APPROX_QUANTILES(aetna_share, 4)[OFFSET(2)], 3) AS median, "
        f"ROUND(APPROX_QUANTILES(aetna_share, 4)[OFFSET(3)], 3) AS p75 FROM `{OUT}`",
    "stability flags (V10 feed)":
        f"SELECT ROUND(COUNTIF(share_stability_flag = 1) / COUNT(*), 4) AS pct_flagged "
        f"FROM `{OUT}`",
    "willing totals":
        f"SELECT ROUND(SUM(willing_spare_hrs), 0) AS willing_hrs, "
        f"ROUND(SUM(willing_placed_cnt), 0) AS willing_placed FROM `{OUT}`",
}


def _run(client, label, sql):
    t0 = time.time()
    result = client.query(sql).result()
    print(f"[{label}] {time.time() - t0:.1f}s")
    return result


def main():
    print(f"RUN_MODE = {RUN_MODE}")
    client = cfg.client()
    _run(client, "create cap_willing", DDL)
    for label, q in CHECKS.items():
        print(f"--- {label} ---")
        for row in _run(client, label, q):
            print("  ", dict(row))


if __name__ == "__main__":
    main()


# REVIEW
# Reviewer 1 LOGIC:
#  - Aetna share applied here and nowhere upstream (cross-cutting rule 8);
#    bounded [0,1]; zero_utilization forces both willing outputs to 0.
#  - xwalk join keys and filters copied from 12_provider_par_flag.py.
#  - prvdr_county always with prvdr_state_cd (rule 12); both provider keys.
# Reviewer 2 SPEC:
#  - Deviations = five ASSUMPTION blocks (A1 resolved by triage: willing
#    uses absorbing capacity, spare + uplift, per CD-23).
#  - Tolerance from cap_params - no tuning literal.
# Reviewer 3 EFFICIENCY:
#  - Zero claims scans; all inputs are cap_ tables + two small reference
#    joins. Every join keyed on provider (+county); contracted deduped
#    DISTINCT before join. Relative cost: trivial.
