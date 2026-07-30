"""
66 - cohort benchmarks + intake rates   [PYTHON runner / BigQuery DDL]

WHAT  : Materializes cap_cohort_bench. 'ALL' rows carry the ceiling
        benchmarks from cap_provider_year's INT-ONLY columns (BENCH_PCTL
        of int_capped_hrs_yr / int_fte_days_yr, row-level per provider x
        county) - identical to module 65's inline cohort by construction
        (alignment ruling). CAPPED rates only, uplift never enters cohort
        math (CD-23). Segment rows carry cohort_intake_rate (new patients
        per active month, 12-mo pair lookback) and avg_first_yr_hrs (hours
        a new patient consumes in year 1) per ref_segment cell. Small
        cohorts fall back to state x specialty, fallback_flag = 1.
GRAIN : specialty_ctg_cd x county_band_cd x prvdr_state_cd x segment_cd
        (segment_cd = 'ALL' for ceiling rows)
INPUTS: cap_provider_year, ref_segment, cap_params, ref_mpfs_time,
        A870800_medicare_analysis_2025_claims (ONE scan), HCC map,
        cap_observed_detail (npi attach)
OUTPUT: cap_cohort_bench (BigQuery table) + sanity prints.
Run   : python expanded_scope/dc_v2/08_capacity_risk/66_cohort_bench.py
"""

# ASSUMPTION [1]: cohort = specialty x band x STATE (spec: specialty x
#   county band) - same deviation as 65 A3; PK gains prvdr_state_cd.
# ASSUMPTION [2]: 'active month' (intake denominator) = calendar month with
#   >= 1 visit by the provider. Spec defines intake as "new patients of a
#   segment accepted per active month" without defining active.
# ASSUMPTION [3]: cohort_intake_rate = ratio of sums (cohort new patients /
#   cohort active provider-months), not mean of provider rates - robust to
#   thin providers.
# ASSUMPTION [4]: avg_first_yr_hrs = cohort avg of a new patient's deflated
#   visit hours in the 12 months from first visit; new-patient cohort
#   restricted to first visits in 2024-01..2024-12 so the 12-month forward
#   window fits inside the extract. Minutes = matched intra_mins x
#   DEFLATION(class); unmatched = 0 (zero-minute rule; module 64's fallback
#   ladder not repeated here - second-order for an average).
# ASSUMPTION [5]: boot_ci_width_pct = NULL. Resampling is module 63's scope
#   (MIN_COHORT_N derivation); persisting per-cohort widths would need 63
#   to write them back. Falsified if run review wants them here.
# ASSUMPTION [6]: segment definitions per ref_segment/CD-20: age 60-74 vs
#   75+ (age_nbr at visit), chronic = HCC_v24-mapped primary dx in the 24
#   months ending with the visit month, new = no visit to the SAME provider
#   in the prior 12 months (48's pair rule).

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

RUN_MODE = "sample"

PY     = cfg.table("cap_provider_year")
SEG    = cfg.table("ref_segment")
PARAMS = cfg.table("cap_params")
MPFS   = cfg.table("ref_mpfs_time")
OBS    = cfg.table("cap_observed_detail")
OUT    = cfg.table("cap_cohort_bench")
CLAIMS = "anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_2025_claims"
MAP    = "anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.HCC_ICD_Mapping_2025"

PCTL  = (f"(SELECT CAST(param_val AS INT64) FROM `{PARAMS}` "
         f"WHERE param_nm = 'BENCH_PCTL' AND param_scope = 'GLOBAL')")
MIN_N = (f"(SELECT param_val FROM `{PARAMS}` "
         f"WHERE param_nm = 'MIN_COHORT_N' AND param_scope = 'GLOBAL')")

SAMPLE = ("AND MOD(ABS(FARM_FINGERPRINT(CAST(c.member_id AS STRING))), 100) = 0"
          if RUN_MODE == "sample" else "")

DDL = f"""
CREATE OR REPLACE TABLE `{OUT}`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
WITH defl AS (
  SELECT param_scope AS code_class_cd, param_val AS defl_factor
  FROM `{PARAMS}` WHERE param_nm = 'DEFLATION'
),
claims_f AS (
  SELECT
    c.member_id,
    TRIM(CAST(c.epdb_dw_prvdr_id AS STRING)) AS epdb_dw_prvdr_id,
    c.srv_start_dt,
    DATE_TRUNC(c.srv_start_dt, MONTH)        AS month,
    c.age_nbr,
    UPPER(REPLACE(TRIM(c.pri_icd9_dx_cd), '.', '')) AS dx,
    UPPER(TRIM(CAST(c.prcdr_cd AS STRING)))  AS hcpcs_cd
  FROM `{CLAIMS}` c
  WHERE c.age_nbr >= 60
    {SAMPLE}
),
chronic_members AS (
  SELECT DISTINCT m.month, mm.member_id
  FROM (SELECT DISTINCT month FROM claims_f) m
  JOIN (SELECT DISTINCT cf.member_id, cf.month AS claim_month
        FROM claims_f cf JOIN `{MAP}` h
          ON cf.dx = UPPER(TRIM(h.diagnosis_code))
        WHERE h.HCC_v24 IS NOT NULL) mm
    ON mm.claim_month BETWEEN DATE_SUB(m.month, INTERVAL 23 MONTH) AND m.month
),
pair_new AS (
  SELECT member_id, epdb_dw_prvdr_id, month,
         COALESCE(LAG(month) OVER (PARTITION BY member_id, epdb_dw_prvdr_id
                                   ORDER BY month)
                    < DATE_SUB(month, INTERVAL 12 MONTH), TRUE) AS is_new
  FROM (SELECT DISTINCT member_id, epdb_dw_prvdr_id, month FROM claims_f)
),
segmented AS (
  SELECT
    cf.member_id, cf.epdb_dw_prvdr_id, cf.month, cf.srv_start_dt, cf.hcpcs_cd,
    CONCAT(IF(pn.is_new, 'NEW', 'RET'), '_',
           IF(ch.member_id IS NOT NULL, 'CHR', 'NONCHR'), '_',
           IF(cf.age_nbr BETWEEN 60 AND 74, '60_74', '75P')) AS segment_cd,
    pn.is_new
  FROM claims_f cf
  JOIN pair_new pn
    ON cf.member_id = pn.member_id AND cf.epdb_dw_prvdr_id = pn.epdb_dw_prvdr_id
    AND cf.month = pn.month
  LEFT JOIN chronic_members ch
    ON ch.month = cf.month AND ch.member_id = cf.member_id
  WHERE EXTRACT(YEAR FROM cf.month) IN (2024, 2025)
),
prov_dim AS (
  SELECT COALESCE(npi, epdb_dw_prvdr_id) AS pid, epdb_dw_prvdr_id,
         specialty_ctg_cd, county_band_cd, prvdr_state_cd,
         SAFE_DIVIDE(capped_hrs_yr, fte_days_yr) AS hrs_per_fte_day,
         fte_days_yr, fte_days_src_cd
  FROM `{PY}`
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY COALESCE(npi, epdb_dw_prvdr_id)
    ORDER BY capped_hrs_yr DESC) = 1
),
active_months AS (
  SELECT epdb_dw_prvdr_id, COUNT(DISTINCT month) AS active_mths
  FROM segmented GROUP BY 1
),
intake AS (
  SELECT
    pd.specialty_ctg_cd, pd.county_band_cd, pd.prvdr_state_cd, s.segment_cd,
    COUNT(DISTINCT IF(s.is_new, CONCAT(s.member_id, '|', s.epdb_dw_prvdr_id,
                                       '|', CAST(s.month AS STRING)), NULL)) AS new_patients,
    SUM(am.active_mths)  AS prov_month_wt,
    COUNT(DISTINCT s.epdb_dw_prvdr_id) AS n_prov
  FROM segmented s
  JOIN prov_dim pd ON s.epdb_dw_prvdr_id = pd.epdb_dw_prvdr_id
  JOIN active_months am ON s.epdb_dw_prvdr_id = am.epdb_dw_prvdr_id
  GROUP BY 1, 2, 3, 4
),
first_visits AS (
  SELECT member_id, epdb_dw_prvdr_id, segment_cd, MIN(srv_start_dt) AS first_dt
  FROM segmented
  WHERE is_new AND srv_start_dt BETWEEN '2024-01-01' AND '2024-12-31'
  GROUP BY 1, 2, 3
),
first_yr AS (
  SELECT
    pd.specialty_ctg_cd, pd.county_band_cd, pd.prvdr_state_cd, fv.segment_cd,
    SAFE_DIVIDE(
      SUM(IF(m.hcpcs_cd IS NOT NULL, COALESCE(m.intra_mins, 0) * d.defl_factor, 0)) / 60,
      COUNT(DISTINCT CONCAT(fv.member_id, '|', fv.epdb_dw_prvdr_id))) AS avg_first_yr_hrs
  FROM first_visits fv
  JOIN segmented s
    ON s.member_id = fv.member_id AND s.epdb_dw_prvdr_id = fv.epdb_dw_prvdr_id
    AND s.srv_start_dt BETWEEN fv.first_dt AND DATE_ADD(fv.first_dt, INTERVAL 12 MONTH)
  JOIN prov_dim pd ON s.epdb_dw_prvdr_id = pd.epdb_dw_prvdr_id
  LEFT JOIN `{MPFS}` m ON s.hcpcs_cd = m.hcpcs_cd
  LEFT JOIN defl d ON COALESCE(m.code_class_cd, 'OTHER') = d.code_class_cd
  GROUP BY 1, 2, 3, 4
),
bench_rows AS (
  -- 65-66 alignment ruling: benchmark basis = the INT-ONLY columns of
  -- cap_provider_year, row-level per provider x county - the exact row
  -- set, rate and filters of module 65's inline cohort CTE, so the bench
  -- numbers are identical by construction. No provider dedup here.
  SELECT COALESCE(npi, epdb_dw_prvdr_id) AS pid,
         specialty_ctg_cd, county_band_cd, prvdr_state_cd,
         SAFE_DIVIDE(int_capped_hrs_yr, int_fte_days_yr) AS int_rate,
         SUM(int_fte_days_yr) OVER (PARTITION BY COALESCE(npi, epdb_dw_prvdr_id))
           AS fte_days_int_tot,
         fte_days_src_cd
  FROM `{PY}`
),
bench_all AS (
  SELECT specialty_ctg_cd, county_band_cd, prvdr_state_cd, 'ALL' AS segment_cd,
         APPROX_QUANTILES(int_rate, 100)[OFFSET({PCTL})]  AS bench_rate_hrs_day,
         APPROX_QUANTILES(fte_days_int_tot, 2)[OFFSET(1)] AS median_fte_days,
         CAST(NULL AS FLOAT64) AS cohort_intake_rate,
         CAST(NULL AS FLOAT64) AS avg_first_yr_hrs,
         COUNT(DISTINCT pid) AS n_npi
  FROM bench_rows
  WHERE fte_days_src_cd = 'OBSERVED' AND int_rate IS NOT NULL
  GROUP BY 1, 2, 3
),
bench_seg AS (
  SELECT i.specialty_ctg_cd, i.county_band_cd, i.prvdr_state_cd, i.segment_cd,
         CAST(NULL AS FLOAT64) AS bench_rate_hrs_day,
         CAST(NULL AS FLOAT64) AS median_fte_days,
         SAFE_DIVIDE(i.new_patients, i.prov_month_wt) AS cohort_intake_rate,
         fy.avg_first_yr_hrs,
         i.n_prov AS n_npi
  FROM intake i
  LEFT JOIN first_yr fy
    ON i.specialty_ctg_cd = fy.specialty_ctg_cd
    AND COALESCE(i.county_band_cd, '') = COALESCE(fy.county_band_cd, '')
    AND i.prvdr_state_cd = fy.prvdr_state_cd
    AND i.segment_cd = fy.segment_cd
),
unioned AS (
  SELECT * FROM bench_all UNION ALL SELECT * FROM bench_seg
),
state_fallback AS (
  SELECT specialty_ctg_cd, prvdr_state_cd, segment_cd,
         SUM(bench_rate_hrs_day * n_npi) / NULLIF(SUM(IF(bench_rate_hrs_day IS NULL, 0, n_npi)), 0)
           AS fb_bench,
         SUM(median_fte_days * n_npi) / NULLIF(SUM(IF(median_fte_days IS NULL, 0, n_npi)), 0)
           AS fb_fte,
         SUM(cohort_intake_rate * n_npi) / NULLIF(SUM(IF(cohort_intake_rate IS NULL, 0, n_npi)), 0)
           AS fb_intake,
         SUM(avg_first_yr_hrs * n_npi) / NULLIF(SUM(IF(avg_first_yr_hrs IS NULL, 0, n_npi)), 0)
           AS fb_first_yr
  FROM unioned GROUP BY 1, 2, 3
)
SELECT
  u.specialty_ctg_cd, u.county_band_cd, u.prvdr_state_cd, u.segment_cd,
  IF(u.n_npi < {MIN_N}, sf.fb_bench,   u.bench_rate_hrs_day) AS bench_rate_hrs_day,
  IF(u.n_npi < {MIN_N}, sf.fb_fte,     u.median_fte_days)    AS median_fte_days,
  IF(u.n_npi < {MIN_N}, sf.fb_intake,  u.cohort_intake_rate) AS cohort_intake_rate,
  IF(u.n_npi < {MIN_N}, sf.fb_first_yr, u.avg_first_yr_hrs)  AS avg_first_yr_hrs,
  u.n_npi,
  IF(u.n_npi < {MIN_N}, 1, 0) AS fallback_flag,
  CAST(NULL AS FLOAT64)       AS boot_ci_width_pct
FROM unioned u
LEFT JOIN state_fallback sf
  ON u.specialty_ctg_cd = sf.specialty_ctg_cd
  AND u.prvdr_state_cd = sf.prvdr_state_cd
  AND u.segment_cd = sf.segment_cd
"""

CHECKS = {
    "row counts by segment_cd (expect 'ALL' + the 8 ref_segment cells)":
        f"SELECT segment_cd, COUNT(*) AS n FROM `{OUT}` GROUP BY 1 ORDER BY 1",
    "segment_cd validity vs ref_segment":
        f"SELECT COUNT(*) AS bad_segments FROM `{OUT}` o "
        f"LEFT JOIN `{SEG}` s ON o.segment_cd = s.segment_cd "
        f"WHERE o.segment_cd != 'ALL' AND s.segment_cd IS NULL",
    "fallback share (cohorts under MIN_COHORT_N)":
        f"SELECT ROUND(COUNTIF(fallback_flag = 1) / COUNT(*), 4) AS pct_fallback "
        f"FROM `{OUT}`",
    "intake rate ranges by segment (eyeball)":
        f"SELECT segment_cd, ROUND(MIN(cohort_intake_rate), 4) AS min_rate, "
        f"ROUND(APPROX_QUANTILES(cohort_intake_rate, 2)[OFFSET(1)], 4) AS med_rate, "
        f"ROUND(MAX(cohort_intake_rate), 4) AS max_rate "
        f"FROM `{OUT}` WHERE segment_cd != 'ALL' GROUP BY 1 ORDER BY 1",
    "avg_first_yr_hrs by segment (eyeball)":
        f"SELECT segment_cd, ROUND(APPROX_QUANTILES(avg_first_yr_hrs, 2)[OFFSET(1)], 2) "
        f"AS med_hrs FROM `{OUT}` WHERE segment_cd != 'ALL' GROUP BY 1 ORDER BY 1",
}


def _run(client, label, sql):
    t0 = time.time()
    result = client.query(sql).result()
    print(f"[{label}] {time.time() - t0:.1f}s")
    return result


def main():
    print(f"RUN_MODE = {RUN_MODE}")
    client = cfg.client()
    _run(client, "create cap_cohort_bench", DDL)
    for label, q in CHECKS.items():
        print(f"--- {label} ---")
        for row in _run(client, label, q):
            print("  ", dict(row))


if __name__ == "__main__":
    main()


# REVIEW
# Reviewer 1 LOGIC:
#  - Segment rules reuse 48's exact expressions (12-mo pair LAG rule, 24-mo
#    chronic window, age >= 60 scope); provider dimension joined on
#    epdb_dw_prvdr_id from cap_provider_year (deduped to one row/provider).
#  - CD-23 honored: benchmarks derive from capped hrs_per_fte_day only;
#    team_uplift_hrs never read here.
# Reviewer 2 SPEC:
#  - Deviations = six ASSUMPTION blocks (state in cohort key, active-month
#    definition, ratio-of-sums, first-yr window, NULL boot widths, segment
#    operationalization).
#  - BENCH_PCTL / MIN_COHORT_N / DEFLATION all read from cap_params.
#  - Alignment ruling: bench_rows mirrors 65's cohort CTE exactly (int-only
#    rate, provider x county row set, OBSERVED + non-NULL-rate filters,
#    same quantile expressions on the same inputs).
# Reviewer 3 EFFICIENCY:
#  - Exactly ONE claims scan (claims_f CTE; all segment logic downstream of
#    it). first_yr joins segmented to first_visits on member+provider with a
#    date window - bounded fan (a member-provider pair's own 12-month
#    claims). No CROSS JOINs. Relative cost ~ one claims scan + windowed
#    aggregation; sample mode cuts to 1%.
