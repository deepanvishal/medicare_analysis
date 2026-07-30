"""
65 - provider-year consolidation + ceilings   [PYTHON runner / BigQuery DDL]

WHAT  : Consolidates capped hours, FTE-days, team uplift (CD-23) and
        ceilings into cap_provider_year. ALLOC-GRAIN RULING applied:
        provider x county rows MERGE across both sources (one row per
        provider x county, per the data model grain) and
        county_alloc_share is computed over COMBINED internal + CMS
        volumes, partition by provider only - a dual-source provider's
        shares sum to 1 across the union of their counties. CD-22
        individuals-only applied here: NULL/'ZZZZ' specialty excluded,
        ind_src_cd = 'CMS_I' when the npi is in the CMS ent_cd='I' set,
        'ASSUMED' for unmatched-npi-with-real-specialty; exclusion counts
        printed. Ceiling_low = cohort benchmark rate x own FTE-days x
        county share; ceiling_high = x cohort median FTE-days (CD-04).
GRAIN : npi/epdb_dw_prvdr_id x prvdr_county + prvdr_state_cd (rule 12);
        ONE row per provider x county across both sources
INPUTS: cap_daily_capped, cap_hours_annual, cap_observed_detail,
        cap_params, ms_ref_county (county_type = CMS 5-way band)
OUTPUT: cap_provider_year (BigQuery table) with gates + sanity prints.
Run   : python expanded_scope/dc_v2/08_capacity_risk/65_provider_year.py
"""

# ASSUMPTION [1]: the provider-year is INTERNAL YEAR 2025 (latest complete
#   year) + CMS 2023 hours merged in (CD-09 ratio-stability); 2024 remains
#   available in cap_hours_annual. Confirmed by triage (base year = 2025).
# ASSUMPTION [2]: county_band_cd = ms_ref_county.county_type (HSD 5-way,
#   same values as the CMS SSA bands named in the spec); joined on
#   UPPER(county_name) + state (rule 12). No separate SSA file exists in
#   the repo.
# ASSUMPTION [3]: cohort for benchmarks/fte estimation = specialty x band x
#   state (spec says specialty x county band; state added per rule-12
#   spirit and to match the state x specialty fallback).
# ASSUMPTION [4]: benchmark quantile via APPROX_QUANTILES(_, 100)[OFFSET(
#   CAST(BENCH_PCTL AS INT64))] - the only param-driven percentile BigQuery
#   allows. Module 66 materializes benchmarks from the published int-only
#   columns - identical by construction (A6, alignment ruling).
# ASSUMPTION [5]: internal org billers are undetectable (no ent_cd for
#   epdb-only ids); CD-22 wording keeps real-specialty unmatched ids as
#   ASSUMED individuals, so only CMS-side 'O' (already excluded in 61) and
#   bad-specialty ids drop here.
# ASSUMPTION [6]: ALLOC-GRAIN RULING implementation: provider x county rows
#   merge via FULL OUTER JOIN on npi + county (NULL-safe); same county from
#   two sources becomes one row with hours consolidated (internal capped +
#   CMS estimated). The CMS component's FTE-days are estimated via the
#   cohort median INTERNAL rate and top up the observed internal days;
#   fte_days_src_cd = 'OBSERVED' when any internal days exist, else
#   'ESTIMATED'. Cohort benchmarks use the INTERNAL-only rate
#   (non-circular; CMS estimates never poison benchmarks). The int-only
#   components are published as int_capped_hrs_yr / int_fte_days_yr and
#   module 66 reads those - identical bench numbers by construction
#   (65-66 alignment ruling).

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

CAPPED = cfg.table("cap_daily_capped")
ANNUAL = cfg.table("cap_hours_annual")
OBS    = cfg.table("cap_observed_detail")
PARAMS = cfg.table("cap_params")
CTY    = cfg.table("ref_county")
OUT    = cfg.table("cap_provider_year")

INTERNAL_YR = 2025   # ASSUMPTION [1]
PCTL = (f"(SELECT CAST(param_val AS INT64) FROM `{PARAMS}` "
        f"WHERE param_nm = 'BENCH_PCTL' AND param_scope = 'GLOBAL')")

DDL = f"""
CREATE OR REPLACE TABLE `{OUT}`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
WITH internal_cty AS (
  SELECT
    npi, epdb_dw_prvdr_id,
    COALESCE(npi, epdb_dw_prvdr_id)         AS pid,
    prvdr_county, prvdr_state_cd,
    SUM(capped_hrs)                         AS int_capped_hrs,
    SUM(frac_day)                           AS int_fte_days,
    SUM(GREATEST(defl_hrs - capped_hrs, 0)) AS team_uplift_hrs,
    SUM(impossible_day_flag)                AS impossible_day_cnt
  FROM `{CAPPED}`
  WHERE EXTRACT(YEAR FROM svc_dt) = {INTERNAL_YR}
  GROUP BY 1, 2, 3, 4, 5
),
cms_cty AS (
  SELECT npi, npi AS pid, prvdr_county, prvdr_state_cd,
         specialty_ctg_cd AS cms_specialty_ctg_cd,
         COALESCE(defl_hrs_yr, 0) AS cms_hrs
  FROM `{ANNUAL}`
  WHERE src = 'CMS_FFS'
),
spec_pick AS (
  SELECT COALESCE(npi, epdb_dw_prvdr_id) AS pid,
         ARRAY_AGG(specialty_ctg_cd ORDER BY svc_cnt_yr DESC LIMIT 1)[SAFE_OFFSET(0)]
           AS specialty_ctg_cd
  FROM `{ANNUAL}`
  WHERE src = 'AETNA_MA' AND period_yr = {INTERNAL_YR}
  GROUP BY 1
),
vol_combined AS (
  -- RULING: allocation over COMBINED volumes, both sources, per pid x county
  SELECT COALESCE(npi, epdb_dw_prvdr_id) AS pid, prvdr_county,
         SUM(COALESCE(svc_cnt_yr, 0)) AS svc_cnt
  FROM `{ANNUAL}`
  WHERE (src = 'AETNA_MA' AND period_yr = {INTERNAL_YR}) OR src = 'CMS_FFS'
  GROUP BY 1, 2
),
alloc AS (
  SELECT pid, prvdr_county,
         SAFE_DIVIDE(svc_cnt, SUM(svc_cnt) OVER (PARTITION BY pid))
           AS county_alloc_share
  FROM vol_combined
),
cms_i_set AS (
  SELECT DISTINCT npi FROM `{OBS}` WHERE src = 'CMS_FFS'
),
merged AS (
  -- RULING: ONE row per provider x county across BOTH sources; same county
  -- from two sources merges with hours consolidated
  SELECT
    COALESCE(i.pid, c.pid)                       AS pid,
    COALESCE(i.npi, c.npi)                       AS npi,
    i.epdb_dw_prvdr_id,
    COALESCE(i.prvdr_county, c.prvdr_county)     AS prvdr_county,
    COALESCE(i.prvdr_state_cd, c.prvdr_state_cd) AS prvdr_state_cd,
    COALESCE(i.int_capped_hrs, 0)                AS int_capped_hrs,
    COALESCE(c.cms_hrs, 0)                       AS cms_hrs,
    i.int_fte_days,
    COALESCE(i.team_uplift_hrs, 0)               AS team_uplift_hrs,
    i.impossible_day_cnt,
    c.cms_specialty_ctg_cd,
    i.pid IS NOT NULL                            AS has_int,
    c.pid IS NOT NULL                            AS has_cms
  FROM internal_cty i
  FULL OUTER JOIN cms_cty c
    ON i.npi = c.npi
    AND COALESCE(i.prvdr_county, '(NULL)') = COALESCE(c.prvdr_county, '(NULL)')
),
prov_flags AS (
  SELECT pid,
         SUM(int_fte_days)   AS fte_days_int_tot,
         SUM(cms_hrs)        AS cms_hrs_tot,
         LOGICAL_OR(has_int) AS any_int,
         LOGICAL_OR(has_cms) AS any_cms
  FROM merged GROUP BY pid
),
base AS (
  SELECT
    m.npi, m.epdb_dw_prvdr_id, m.pid, m.prvdr_county, m.prvdr_state_cd,
    COALESCE(sp.specialty_ctg_cd, m.cms_specialty_ctg_cd) AS specialty_ctg_cd,
    m.int_capped_hrs + m.cms_hrs                 AS capped_hrs_yr,
    m.int_capped_hrs, m.cms_hrs, m.int_fte_days,
    pf.fte_days_int_tot, pf.cms_hrs_tot,
    m.team_uplift_hrs, m.impossible_day_cnt,
    CASE WHEN pf.any_int AND pf.any_cms THEN 'BOTH'
         WHEN pf.any_int THEN 'AETNA_ONLY'
         ELSE 'CMS_ONLY' END                     AS src_mix_cd,
    IF(m.int_fte_days IS NOT NULL, 'OBSERVED', 'ESTIMATED') AS fte_days_src_cd,
    IF(ci.npi IS NOT NULL, 'CMS_I', 'ASSUMED')   AS ind_src_cd,
    CASE WHEN al.pid IS NOT NULL THEN al.county_alloc_share
         ELSE 1.0 END                            AS county_alloc_share
  FROM merged m
  JOIN prov_flags pf ON m.pid = pf.pid
  LEFT JOIN spec_pick sp ON m.pid = sp.pid
  LEFT JOIN alloc al
    ON m.pid = al.pid
    AND COALESCE(m.prvdr_county, '(NULL)') = COALESCE(al.prvdr_county, '(NULL)')
  LEFT JOIN cms_i_set ci ON m.npi = ci.npi
),
kept AS (
  SELECT b.*, rc.county_type AS county_band_cd
  FROM base b
  LEFT JOIN `{CTY}` rc
    ON UPPER(TRIM(b.prvdr_county)) = UPPER(TRIM(rc.county_name))
    AND b.prvdr_state_cd = rc.state_cd
  WHERE b.specialty_ctg_cd IS NOT NULL
    AND UPPER(TRIM(b.specialty_ctg_cd)) != 'ZZZZ'
),
rates AS (
  -- benchmark basis = INTERNAL observed rate only (A6): CMS estimated
  -- hours never poison the benchmarks
  SELECT *, SAFE_DIVIDE(int_capped_hrs, int_fte_days) AS int_rate
  FROM kept
),
cohort AS (
  SELECT specialty_ctg_cd, county_band_cd, prvdr_state_cd,
         APPROX_QUANTILES(int_rate, 100)[OFFSET({PCTL})] AS bench_rate,
         APPROX_QUANTILES(fte_days_int_tot, 2)[OFFSET(1)] AS median_fte_days,
         APPROX_QUANTILES(int_rate, 2)[OFFSET(1)]         AS median_rate
  FROM rates
  WHERE fte_days_src_cd = 'OBSERVED' AND int_rate IS NOT NULL
  GROUP BY 1, 2, 3
),
enriched AS (
  SELECT r.*, c.bench_rate, c.median_fte_days, c.median_rate,
    CASE WHEN r.fte_days_int_tot IS NOT NULL
         THEN r.fte_days_int_tot + COALESCE(SAFE_DIVIDE(r.cms_hrs_tot, c.median_rate), 0)
         ELSE SAFE_DIVIDE(r.cms_hrs_tot, c.median_rate) END AS fte_days_tot_eff,
    CASE WHEN r.int_fte_days IS NOT NULL
         THEN r.int_fte_days + COALESCE(SAFE_DIVIDE(r.cms_hrs, c.median_rate), 0)
         ELSE SAFE_DIVIDE(r.cms_hrs, c.median_rate) END     AS fte_days_yr
  FROM rates r
  LEFT JOIN cohort c
    ON r.specialty_ctg_cd = c.specialty_ctg_cd
    AND COALESCE(r.county_band_cd, '') = COALESCE(c.county_band_cd, '')
    AND r.prvdr_state_cd = c.prvdr_state_cd
)
SELECT
  npi, epdb_dw_prvdr_id, prvdr_county, prvdr_state_cd,
  specialty_ctg_cd, county_band_cd,
  capped_hrs_yr,
  int_capped_hrs                                        AS int_capped_hrs_yr,
  fte_days_yr,
  int_fte_days                                          AS int_fte_days_yr,
  fte_days_src_cd,
  SAFE_DIVIDE(capped_hrs_yr, fte_days_yr)               AS hrs_per_fte_day,
  bench_rate * fte_days_tot_eff * county_alloc_share    AS ceiling_low_hrs,
  bench_rate * median_fte_days * county_alloc_share     AS ceiling_high_hrs,
  county_alloc_share,
  GREATEST(bench_rate * fte_days_tot_eff * county_alloc_share
           - capped_hrs_yr, 0)                          AS spare_hrs,
  team_uplift_hrs,
  SAFE_DIVIDE(capped_hrs_yr,
    bench_rate * fte_days_tot_eff * county_alloc_share) AS util_ratio,
  impossible_day_cnt,
  src_mix_cd,
  ind_src_cd
FROM enriched
"""

GATE_ALLOC = f"""
SELECT COUNT(*) FROM (
  SELECT COALESCE(npi, epdb_dw_prvdr_id) AS pid, SUM(county_alloc_share) AS s
  FROM `{OUT}` GROUP BY 1
  HAVING s IS NOT NULL AND ABS(s - 1.0) > 0.001)
"""

_DIAG_CTES = f"""
WITH vol AS (
  SELECT COALESCE(npi, epdb_dw_prvdr_id) AS pid,
         SUM(COALESCE(svc_cnt_yr, 0)) AS svc_total
  FROM `{ANNUAL}`
  WHERE (src = 'AETNA_MA' AND period_yr = {INTERNAL_YR}) OR src = 'CMS_FFS'
  GROUP BY 1
),
sums AS (
  SELECT COALESCE(npi, epdb_dw_prvdr_id) AS pid,
         SUM(county_alloc_share) AS s,
         COUNTIF(prvdr_county IS NULL) AS null_cty_rows,
         COUNTIF(npi IS NULL) AS npi_null_rows,
         COUNTIF(npi IS NOT NULL) AS npi_rows
  FROM `{OUT}`
  GROUP BY 1
),
bucketed AS (
  SELECT s.pid, s.s,
    CASE
      WHEN COALESCE(v.svc_total, 0) = 0 THEN 'a_zero_volume'
      WHEN s.null_cty_rows > 0 THEN 'b_null_county'
      WHEN s.npi_null_rows > 0 AND s.npi_rows > 0 THEN 'c_key_mismatch'
      WHEN s.s IS NOT NULL AND ABS(s.s - 1.0) < 0.001 THEN 'd_fp_noise'
      ELSE 'e_other'
    END AS bucket
  FROM sums s
  LEFT JOIN vol v ON s.pid = v.pid
  WHERE s.s IS NULL OR ABS(s.s - 1.0) > 0.000001
)
"""

DIAG_BUCKETS = _DIAG_CTES + """
SELECT bucket, COUNT(*) AS n FROM bucketed GROUP BY bucket ORDER BY bucket
"""

DIAG_EXAMPLES = _DIAG_CTES + """
SELECT pid, ROUND(s, 6) AS share_sum, bucket FROM bucketed
WHERE bucket IN ('c_key_mismatch', 'e_other') LIMIT 5
"""

DIAG_NULL_CTY_CEILING = f"""
SELECT
  ROUND(SUM(IF(prvdr_county IS NULL, ceiling_low_hrs, 0)), 0) AS null_county_ceiling_hrs,
  ROUND(SAFE_DIVIDE(SUM(IF(prvdr_county IS NULL, ceiling_low_hrs, 0)),
        SUM(ceiling_low_hrs)), 4) AS pct_of_total_ceiling
FROM `{OUT}`
"""

CE_STOP_THRESHOLD = 50   # c + e providers above this = STOP for key-grain decision

CHECKS = {
    "CD-22 exclusion counts (NULL / ZZZZ specialty dropped)":
        f"SELECT COUNTIF(specialty_ctg_cd IS NULL) AS null_spec, "
        f"COUNTIF(UPPER(TRIM(COALESCE(specialty_ctg_cd, ''))) = 'ZZZZ') AS zzzz_spec "
        f"FROM `{ANNUAL}` WHERE period_yr IN (2023, {INTERNAL_YR})",
    "kept providers by ind_src_cd / src_mix_cd":
        f"SELECT ind_src_cd, src_mix_cd, COUNT(*) AS n FROM `{OUT}` GROUP BY 1, 2 ORDER BY n DESC",
    "fte_days_src_cd split":
        f"SELECT fte_days_src_cd, COUNT(*) AS n, ROUND(AVG(fte_days_yr), 1) AS avg_fte_days "
        f"FROM `{OUT}` GROUP BY 1",
    "team uplift totals (CD-23)":
        f"SELECT ROUND(SUM(team_uplift_hrs), 0) AS uplift_hrs, "
        f"ROUND(SUM(spare_hrs), 0) AS spare_hrs FROM `{OUT}`",
    "utilization distribution (V3 eyeball)":
        f"SELECT ROUND(APPROX_QUANTILES(util_ratio, 4)[OFFSET(2)], 3) AS median_util, "
        f"ROUND(COUNTIF(util_ratio > 1) / COUNT(*), 4) AS pct_over_1 FROM `{OUT}` "
        f"WHERE util_ratio IS NOT NULL",
}


def _run(client, label, sql):
    t0 = time.time()
    result = client.query(sql).result()
    print(f"[{label}] {time.time() - t0:.1f}s")
    return result


def main():
    print(f"RUN_MODE = {RUN_MODE}; internal year = {INTERNAL_YR}")
    client = cfg.client()
    _run(client, "create cap_provider_year", DDL)

    print("--- alloc-share diagnostic (pre-gate buckets, exact 1e-6 test) ---")
    buckets = {}
    for row in _run(client, "alloc diagnostic", DIAG_BUCKETS):
        r = dict(row)
        buckets[r["bucket"]] = r["n"]
        print("  ", r)

    print("--- ceiling_low_hrs in '(NULL)' county bucket "
          "(unplaceable by the fill - conservative loss, watch the size) ---")
    for row in _run(client, "null-county ceiling share", DIAG_NULL_CTY_CEILING):
        print("  ", dict(row))

    hard = buckets.get("c_key_mismatch", 0) + buckets.get("e_other", 0)
    if hard:
        print("--- 5 example pids from c/e buckets ---")
        for row in _run(client, "alloc diag examples", DIAG_EXAMPLES):
            print("  ", dict(row))
        if hard > CE_STOP_THRESHOLD:
            raise SystemExit(
                f"STOP -- {hard} providers in key-mismatch/other buckets "
                f"(> {CE_STOP_THRESHOLD}); key-grain decision needed (Deepan), "
                f"not a silent patch")
        print(f"c/e buckets total {hard} providers (<= {CE_STOP_THRESHOLD}) - "
              f"continuing to gate")
    print(f"zero-volume providers (NULL alloc share, excluded from gate): "
          f"{buckets.get('a_zero_volume', 0)}")

    n_bad = list(client.query(GATE_ALLOC).result())[0][0]
    if n_bad:
        raise SystemExit(f"GATE FAILED -- county_alloc_share sum outside 1.0 +/- 0.001 "
                         f"for {n_bad} providers")
    print("alloc-share gate OK (1.0 +/- 0.001 per provider; zero-volume excluded)")
    for label, q in CHECKS.items():
        print(f"--- {label} ---")
        for row in _run(client, label, q):
            print("  ", dict(row))


if __name__ == "__main__":
    main()


# REVIEW
# Reviewer 1 LOGIC:
#  - ALLOC-GRAIN RULING: shares come from COMBINED internal + CMS volumes,
#    partition by pid only - a dual-source provider's shares sum to 1
#    across the union of their counties; the old per-src NOT IN pattern is
#    gone (FULL OUTER merge on npi + NULL-safe county replaces it).
#  - prvdr_county always paired with prvdr_state_cd (rule 12) including the
#    ref_county band join; both provider keys carried; provider identity =
#    COALESCE(npi, epdb) consistently. Internal NULL-npi providers cannot
#    merge with CMS rows (no shared key) - they stay single-source rows.
#  - CD-23: team_uplift_hrs = sum of (defl - capped) positive slack, kept
#    out of the benchmark basis (internal rate only, A6).
# Reviewer 2 SPEC:
#  - Deviations = six ASSUMPTION blocks; A6 carries the ruling
#    implementation. int_capped_hrs_yr / int_fte_days_yr published so
#    module 66's benchmarks match this module's inline cohort by
#    construction (alignment ruling); data model amendment pending.
#  - CD-22 ind_src_cd / exclusions implemented as specified; counts printed.
# Reviewer 3 EFFICIENCY:
#  - Zero claims scans; reads module 62/64 outputs. FULL OUTER merge is
#    keyed npi + county (no fan-out); window function for alloc shares;
#    prov_flags is a provider-level aggregate. Relative cost: small.
