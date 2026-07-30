"""
65 - provider-year consolidation + ceilings   [PYTHON runner / BigQuery DDL]

WHAT  : Consolidates capped hours, FTE-days, team uplift (CD-23) and
        ceilings into cap_provider_year. CD-22 individuals-only applied
        here: NULL/'ZZZZ' specialty excluded, ind_src_cd = 'CMS_I' when the
        npi is in the CMS ent_cd='I' set (module 61 filter), 'ASSUMED' for
        unmatched-npi-with-real-specialty; exclusion counts printed.
        Ceiling_low = cohort benchmark rate x own FTE-days x county share;
        ceiling_high = x cohort median FTE-days (CD-04). Multi-county
        allocation by service share, sums to 1.0 per provider (gate).
GRAIN : npi/epdb_dw_prvdr_id x prvdr_county + prvdr_state_cd (rule 12)
INPUTS: cap_daily_capped, cap_hours_annual, cap_observed_detail,
        cap_params, ms_ref_county (county_type = CMS 5-way band)
OUTPUT: cap_provider_year (BigQuery table) with gates + sanity prints.
Run   : python expanded_scope/dc_v2/08_capacity_risk/65_provider_year.py
"""

# ASSUMPTION [1]: the provider-year is INTERNAL YEAR 2025 (latest complete
#   year) + CMS 2023 rows for CMS-only providers. cap_provider_year's grain
#   has no year column, so the two internal years cannot both live here;
#   2024 remains available in cap_hours_annual. Falsified if run review
#   wants 2024+2025 averaged or both kept (then a period_yr column and doc
#   amendment are needed).
# ASSUMPTION [2]: county_band_cd = ms_ref_county.county_type (HSD 5-way,
#   same values as the CMS SSA bands named in the spec); joined on
#   UPPER(county_name) + state (rule 12). No separate SSA file exists in
#   the repo.
# ASSUMPTION [3]: cohort for benchmarks/fte estimation = specialty x band x
#   state (spec says specialty x county band; state added per rule-12
#   spirit and to match the state x specialty fallback).
# ASSUMPTION [4]: benchmark quantile via APPROX_QUANTILES(_, 100)[OFFSET(
#   CAST(BENCH_PCTL AS INT64))] - the only param-driven percentile BigQuery
#   allows. Module 66 materializes the same numbers into cap_cohort_bench;
#   formulas must stay in lockstep.
# ASSUMPTION [5]: internal org billers are undetectable (no ent_cd for
#   epdb-only ids); CD-22 wording keeps real-specialty unmatched ids as
#   ASSUMED individuals, so only CMS-side 'O' (already excluded in 61) and
#   bad-specialty ids drop here.
# ASSUMPTION [6]: CMS-only rows: capped_hrs_yr = defl_hrs_yr (no daily grain
#   to cap); fte_days_yr = defl_hrs_yr / cohort median hrs_per_fte_day,
#   fte_days_src_cd='ESTIMATED' per Stage 3.

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
    npi, epdb_dw_prvdr_id, prvdr_county, prvdr_state_cd,
    SUM(capped_hrs)                    AS capped_hrs_yr,
    SUM(frac_day)                      AS fte_days_cty,
    SUM(GREATEST(defl_hrs - capped_hrs, 0)) AS team_uplift_hrs,
    SUM(impossible_day_flag)           AS impossible_day_cnt
  FROM `{CAPPED}`
  WHERE EXTRACT(YEAR FROM svc_dt) = {INTERNAL_YR}
  GROUP BY 1, 2, 3, 4
),
spec_pick AS (
  SELECT COALESCE(npi, epdb_dw_prvdr_id) AS pid,
         ARRAY_AGG(specialty_ctg_cd ORDER BY svc_cnt_yr DESC LIMIT 1)[SAFE_OFFSET(0)]
           AS specialty_ctg_cd
  FROM `{ANNUAL}`
  WHERE src = 'AETNA_MA' AND period_yr = {INTERNAL_YR}
  GROUP BY 1
),
alloc_base AS (
  SELECT COALESCE(npi, epdb_dw_prvdr_id) AS pid, prvdr_county,
         SUM(svc_cnt_yr) AS svc_cnt
  FROM `{ANNUAL}`
  WHERE src = 'AETNA_MA' AND period_yr = {INTERNAL_YR}
  GROUP BY 1, 2
),
alloc AS (
  SELECT pid, prvdr_county,
         SAFE_DIVIDE(svc_cnt, SUM(svc_cnt) OVER (PARTITION BY pid))
           AS county_alloc_share
  FROM alloc_base
),
prov_tot AS (
  SELECT COALESCE(npi, epdb_dw_prvdr_id) AS pid,
         SUM(fte_days_cty) AS fte_days_tot, SUM(capped_hrs_yr) AS capped_tot
  FROM internal_cty GROUP BY 1
),
cms_i_set AS (
  SELECT DISTINCT npi FROM `{OBS}` WHERE src = 'CMS_FFS'
),
cms_only AS (
  SELECT a.npi, a.prvdr_county, a.prvdr_state_cd, a.specialty_ctg_cd,
         a.defl_hrs_yr AS capped_hrs_yr
  FROM `{ANNUAL}` a
  WHERE a.src = 'CMS_FFS'
    AND COALESCE(a.npi, '') NOT IN (
      SELECT COALESCE(npi, '') FROM internal_cty WHERE npi IS NOT NULL)
),
base AS (
  SELECT
    i.npi, i.epdb_dw_prvdr_id, i.prvdr_county, i.prvdr_state_cd,
    sp.specialty_ctg_cd,
    i.capped_hrs_yr, i.fte_days_cty, pt.fte_days_tot,
    i.team_uplift_hrs, i.impossible_day_cnt,
    'OBSERVED' AS fte_days_src_cd,
    IF(ci.npi IS NOT NULL, 'BOTH', 'AETNA_ONLY') AS src_mix_cd,
    IF(ci.npi IS NOT NULL, 'CMS_I', 'ASSUMED')   AS ind_src_cd,
    COALESCE(al.county_alloc_share, 1.0)         AS county_alloc_share
  FROM internal_cty i
  JOIN prov_tot pt ON COALESCE(i.npi, i.epdb_dw_prvdr_id) = pt.pid
  LEFT JOIN spec_pick sp ON COALESCE(i.npi, i.epdb_dw_prvdr_id) = sp.pid
  LEFT JOIN alloc al
    ON COALESCE(i.npi, i.epdb_dw_prvdr_id) = al.pid AND i.prvdr_county = al.prvdr_county
  LEFT JOIN cms_i_set ci ON i.npi = ci.npi
  UNION ALL
  SELECT
    c.npi, CAST(NULL AS STRING), c.prvdr_county, c.prvdr_state_cd,
    c.specialty_ctg_cd,
    c.capped_hrs_yr, CAST(NULL AS FLOAT64), CAST(NULL AS FLOAT64),
    0.0, CAST(NULL AS INT64),
    'ESTIMATED', 'CMS_ONLY', 'CMS_I', 1.0
  FROM cms_only c
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
  SELECT *, SAFE_DIVIDE(capped_hrs_yr, fte_days_cty) AS hrs_per_fte_day
  FROM kept
),
cohort AS (
  SELECT specialty_ctg_cd, county_band_cd, prvdr_state_cd,
         APPROX_QUANTILES(hrs_per_fte_day, 100)[OFFSET({PCTL})] AS bench_rate,
         APPROX_QUANTILES(fte_days_tot, 2)[OFFSET(1)]           AS median_fte_days,
         APPROX_QUANTILES(hrs_per_fte_day, 2)[OFFSET(1)]        AS median_rate
  FROM rates
  WHERE fte_days_src_cd = 'OBSERVED' AND hrs_per_fte_day IS NOT NULL
  GROUP BY 1, 2, 3
)
SELECT
  r.npi, r.epdb_dw_prvdr_id, r.prvdr_county, r.prvdr_state_cd,
  r.specialty_ctg_cd, r.county_band_cd,
  r.capped_hrs_yr,
  COALESCE(r.fte_days_cty, SAFE_DIVIDE(r.capped_hrs_yr, c.median_rate)) AS fte_days_yr,
  r.fte_days_src_cd,
  SAFE_DIVIDE(r.capped_hrs_yr,
    COALESCE(r.fte_days_cty, SAFE_DIVIDE(r.capped_hrs_yr, c.median_rate)))
                                                             AS hrs_per_fte_day,
  c.bench_rate * COALESCE(r.fte_days_tot,
    SAFE_DIVIDE(r.capped_hrs_yr, c.median_rate)) * r.county_alloc_share
                                                             AS ceiling_low_hrs,
  c.bench_rate * c.median_fte_days * r.county_alloc_share    AS ceiling_high_hrs,
  r.county_alloc_share,
  GREATEST(
    c.bench_rate * COALESCE(r.fte_days_tot,
      SAFE_DIVIDE(r.capped_hrs_yr, c.median_rate)) * r.county_alloc_share
    - r.capped_hrs_yr, 0)                                    AS spare_hrs,
  r.team_uplift_hrs,
  SAFE_DIVIDE(r.capped_hrs_yr,
    c.bench_rate * COALESCE(r.fte_days_tot,
      SAFE_DIVIDE(r.capped_hrs_yr, c.median_rate)) * r.county_alloc_share)
                                                             AS util_ratio,
  r.impossible_day_cnt,
  r.src_mix_cd,
  r.ind_src_cd
FROM rates r
LEFT JOIN cohort c
  ON r.specialty_ctg_cd = c.specialty_ctg_cd
  AND COALESCE(r.county_band_cd, '') = COALESCE(c.county_band_cd, '')
  AND r.prvdr_state_cd = c.prvdr_state_cd
"""

GATE_ALLOC = f"""
SELECT COUNT(*) FROM (
  SELECT COALESCE(npi, epdb_dw_prvdr_id) AS pid, SUM(county_alloc_share) AS s
  FROM `{OUT}` GROUP BY 1 HAVING ABS(s - 1.0) > 0.000001)
"""

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
    n_bad = list(client.query(GATE_ALLOC).result())[0][0]
    if n_bad:
        raise SystemExit(f"GATE FAILED -- county_alloc_share != 1.0 for {n_bad} providers")
    print("alloc-share gate OK (sums to 1.0 per provider)")
    for label, q in CHECKS.items():
        print(f"--- {label} ---")
        for row in _run(client, label, q):
            print("  ", dict(row))


if __name__ == "__main__":
    main()


# REVIEW
# Reviewer 1 LOGIC:
#  - prvdr_county always paired with prvdr_state_cd (rule 12) including the
#    ref_county band join; both provider keys carried; provider identity =
#    COALESCE(npi, epdb) consistently.
#  - CD-23: team_uplift_hrs = sum of (defl - capped) positive slack, kept
#    out of hrs_per_fte_day and therefore out of every benchmark input.
# Reviewer 2 SPEC:
#  - Deviations = six ASSUMPTION blocks; biggest are the 2025-only internal
#    year (A1) and the inline cohort benchmark that module 66 must
#    reproduce exactly (A4).
#  - CD-22 ind_src_cd/'exclusions implemented as specified; counts printed.
#  - BENCH_PCTL, and the median rates ride cap_params / data; no tuning
#    literal in this script.
# Reviewer 3 EFFICIENCY:
#  - Zero claims scans; reads module 62/64 outputs. All joins provider- or
#    cohort-keyed; window function for alloc shares avoids a self-join.
#    Relative cost: small.
