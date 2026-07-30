"""
64 - deflation, daily cap, feasibility flags   [PYTHON runner / BigQuery DDL]

WHAT  : Applies calibrated deflation (cap_params) at code-class level, the
        clinical-CPT fallback ladder (Stage 0 zero-minute rule: everything
        else gets 0), the daily cap, fractional days, impossible/high-day
        flags -> cap_daily_capped. Then fills the defl columns left NULL by
        module 62 (cap_hours_daily.defl_hrs, cap_hours_annual.defl_hrs_yr).
        GATE (STOP): impossible-day rate pre-cap must be < 1% (CD-03, V2).
GRAIN : cap_daily_capped -> npi/epdb_dw_prvdr_id x prvdr_county +
        prvdr_state_cd x svc_dt (AETNA_MA only; rule 12)
INPUTS: cap_observed_detail, ref_mpfs_time, cap_params (module 63)
OUTPUT: cap_daily_capped + UPDATEs to cap_hours_daily / cap_hours_annual.
Run   : python expanded_scope/dc_v2/08_capacity_risk/64_ceiling.py
"""

# ASSUMPTION [1]: clinical-CPT fallback ladder (M60c rule: unmatched 5-digit
#   numeric CPT in clinical ranges only) = code-family average intra_mins
#   (first 3 chars, from ref_mpfs_time) -> provider's own avg matched minutes
#   -> cohort (specialty x state) avg. Order from the pre-M60c Stage 0
#   ladder; M60c narrowed eligibility, not the ladder order.
# ASSUMPTION [2]: "clinical ranges" = codes that would class EM or PROC under
#   the module-60 rule (starts '99', or numeric 10021-69990). F-codes,
#   J-codes, supplies etc. stay at 0 minutes.
# ASSUMPTION [3]: cap_hours_annual.defl_hrs_yr for CMS rows = raw_hrs_yr x
#   the provider's own internal defl/raw ratio (cohort specialty x state
#   ratio when absent, global ratio last). The spec defines CMS deflation
#   nowhere below the "deflated" note in the data model.
# ASSUMPTION [4]: impossible_day uses RAW (undeflated) hours > 24, matching
#   the OIG construct in the definitions table.
# ASSUMPTION [5]: defl_hrs in cap_hours_daily/cap_daily_capped is UNCAPPED
#   deflated hours; capped_hrs carries the cap. cap_provider_year sums
#   capped_hrs (module 65).

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
MPFS   = cfg.table("ref_mpfs_time")
PARAMS = cfg.table("cap_params")
DAILY  = cfg.table("cap_hours_daily")
ANNUAL = cfg.table("cap_hours_annual")
CAPPED = cfg.table("cap_daily_capped")

P = f"(SELECT param_val FROM `{PARAMS}` WHERE param_nm = '{{n}}' AND param_scope = 'GLOBAL')"
CAP_HRS = P.format(n="DAILY_CAP_HRS")
FTE_HRS = P.format(n="FTE_DAY_HRS")

DDL_CAPPED = f"""
CREATE OR REPLACE TABLE `{CAPPED}`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
WITH defl AS (
  SELECT param_scope AS code_class_cd, param_val AS defl_factor
  FROM `{PARAMS}` WHERE param_nm = 'DEFLATION'
),
fam_avg AS (
  SELECT code_family_cd, AVG(intra_mins) AS fam_mins
  FROM `{MPFS}` WHERE intra_mins > 0 GROUP BY 1
),
lines AS (
  SELECT
    o.npi, o.epdb_dw_prvdr_id, o.prvdr_county, o.prvdr_state_cd,
    o.specialty_ctg_cd, o.period_start AS svc_dt, o.svc_cnt, o.hcpcs_cd,
    m.hcpcs_cd IS NOT NULL AS matched,
    m.intra_mins, m.code_class_cd,
    REGEXP_CONTAINS(o.hcpcs_cd, r'^[0-9]{{5}}$')
      AND (STARTS_WITH(o.hcpcs_cd, '99')
           OR SAFE_CAST(o.hcpcs_cd AS INT64) BETWEEN 10021 AND 69990)
      AS clinical_cpt,
    f.fam_mins
  FROM `{OBS}` o
  LEFT JOIN `{MPFS}` m ON o.hcpcs_cd = m.hcpcs_cd
  LEFT JOIN fam_avg f ON SUBSTR(o.hcpcs_cd, 1, 3) = f.code_family_cd
  WHERE o.src = 'AETNA_MA'
),
own_avg AS (
  SELECT COALESCE(npi, epdb_dw_prvdr_id) AS pid,
         SAFE_DIVIDE(SUM(svc_cnt * intra_mins), SUM(IF(matched, svc_cnt, 0))) AS own_mins
  FROM lines WHERE matched GROUP BY 1
),
cohort_avg AS (
  SELECT specialty_ctg_cd, prvdr_state_cd,
         SAFE_DIVIDE(SUM(svc_cnt * intra_mins), SUM(IF(matched, svc_cnt, 0))) AS coh_mins
  FROM lines WHERE matched GROUP BY 1, 2
),
timed AS (
  SELECT
    l.*,
    CASE
      WHEN l.matched THEN COALESCE(l.intra_mins, 0)
      WHEN l.clinical_cpt THEN COALESCE(l.fam_mins, oa.own_mins, ca.coh_mins, 0)
      ELSE 0 END AS line_mins,
    CASE
      WHEN l.matched THEN l.code_class_cd
      WHEN l.clinical_cpt AND STARTS_WITH(l.hcpcs_cd, '99') THEN 'EM'
      WHEN l.clinical_cpt THEN 'PROC'
      ELSE 'OTHER' END AS class_eff,
    (NOT l.matched AND l.clinical_cpt) AS via_fallback
  FROM lines l
  LEFT JOIN own_avg oa ON COALESCE(l.npi, l.epdb_dw_prvdr_id) = oa.pid
  LEFT JOIN cohort_avg ca
    ON l.specialty_ctg_cd = ca.specialty_ctg_cd AND l.prvdr_state_cd = ca.prvdr_state_cd
)
SELECT
  npi, epdb_dw_prvdr_id, prvdr_county, prvdr_state_cd, svc_dt,
  SUM(svc_cnt * line_mins) / 60                                    AS raw_hrs,
  SUM(svc_cnt * line_mins * d.defl_factor) / 60                    AS defl_hrs,
  LEAST(SUM(svc_cnt * line_mins * d.defl_factor) / 60, {CAP_HRS})  AS capped_hrs,
  LEAST(SUM(svc_cnt * line_mins * d.defl_factor) / 60 / {FTE_HRS}, 1) AS frac_day,
  IF(SUM(svc_cnt * line_mins) / 60 > 24, 1, 0)                     AS impossible_day_flag,
  IF(SUM(svc_cnt * line_mins * d.defl_factor) / 60 > {CAP_HRS}, 1, 0) AS high_day_flag,
  SUM(IF(via_fallback, svc_cnt, 0))                                AS fallback_svc_cnt,
  SUM(IF(NOT matched AND NOT clinical_cpt, svc_cnt, 0))            AS unmapped_svc_cnt
FROM timed
JOIN defl d ON timed.class_eff = d.code_class_cd
GROUP BY 1, 2, 3, 4, 5
"""

GATE_SQL = f"""
SELECT ROUND(COUNTIF(impossible_day_flag = 1) / COUNT(*), 5) AS impossible_rate
FROM `{CAPPED}`
"""

UPDATE_DAILY = f"""
UPDATE `{DAILY}` d
SET defl_hrs = c.defl_hrs
FROM `{CAPPED}` c
WHERE COALESCE(d.npi, '') = COALESCE(c.npi, '')
  AND COALESCE(d.epdb_dw_prvdr_id, '') = COALESCE(c.epdb_dw_prvdr_id, '')
  AND d.prvdr_county = c.prvdr_county AND d.svc_dt = c.svc_dt
"""

UPDATE_ANNUAL_INTERNAL = f"""
UPDATE `{ANNUAL}` a
SET defl_hrs_yr = s.defl
FROM (
  SELECT COALESCE(npi, '') AS n, COALESCE(epdb_dw_prvdr_id, '') AS e,
         prvdr_county, EXTRACT(YEAR FROM svc_dt) AS yr, SUM(defl_hrs) AS defl
  FROM `{CAPPED}` GROUP BY 1, 2, 3, 4
) s
WHERE a.src = 'AETNA_MA' AND COALESCE(a.npi, '') = s.n
  AND COALESCE(a.epdb_dw_prvdr_id, '') = s.e
  AND a.prvdr_county = s.prvdr_county AND a.period_yr = s.yr
"""

MERGE_ANNUAL_CMS = f"""
MERGE `{ANNUAL}` a
USING (
  SELECT
    t.npi, t.prvdr_county, t.period_yr,
    t.raw_hrs_yr * COALESCE(own.own_ratio, coh.coh_ratio, glob.glob_ratio)
      AS defl_new
  FROM `{ANNUAL}` t
  LEFT JOIN (SELECT npi, SAFE_DIVIDE(SUM(defl_hrs), SUM(raw_hrs)) AS own_ratio
             FROM `{CAPPED}` WHERE npi IS NOT NULL GROUP BY npi) own
    ON t.npi = own.npi
  LEFT JOIN (SELECT specialty_ctg_cd, prvdr_state_cd,
                    SAFE_DIVIDE(SUM(defl_hrs_yr), SUM(raw_hrs_yr)) AS coh_ratio
             FROM `{ANNUAL}` WHERE src = 'AETNA_MA' GROUP BY 1, 2) coh
    ON t.specialty_ctg_cd = coh.specialty_ctg_cd
    AND t.prvdr_state_cd = coh.prvdr_state_cd
  CROSS JOIN (SELECT SAFE_DIVIDE(SUM(defl_hrs), SUM(raw_hrs)) AS glob_ratio
              FROM `{CAPPED}`) glob
  WHERE t.src = 'CMS_FFS'
) s
ON a.src = 'CMS_FFS'
  AND a.npi = s.npi
  AND COALESCE(a.prvdr_county, '') = COALESCE(s.prvdr_county, '')
  AND a.period_yr = s.period_yr
WHEN MATCHED THEN UPDATE SET defl_hrs_yr = s.defl_new
"""

CHECKS = {
    "impossible-day rate pre-cap (V2 gate, must be < 0.01)": GATE_SQL,
    "impossible-day rate post-cap (must be 0 on capped_hrs)":
        f"SELECT COUNTIF(capped_hrs > 24) AS post_cap_over_24 FROM `{CAPPED}`",
    "high-day (team billing) share":
        f"SELECT ROUND(COUNTIF(high_day_flag = 1) / COUNT(*), 4) AS pct FROM `{CAPPED}`",
    "fallback vs zero-minute service counts":
        f"SELECT SUM(fallback_svc_cnt) AS fallback_svcs, SUM(unmapped_svc_cnt) AS zero_min_svcs "
        f"FROM `{CAPPED}`",
    "defl fill coverage":
        f"SELECT src, COUNTIF(defl_hrs_yr IS NULL) AS null_defl, COUNT(*) AS n "
        f"FROM `{ANNUAL}` GROUP BY src",
}


def _run(client, label, sql):
    t0 = time.time()
    result = client.query(sql).result()
    print(f"[{label}] {time.time() - t0:.1f}s")
    return result


def main():
    print(f"RUN_MODE = {RUN_MODE}")
    client = cfg.client()
    _run(client, "create cap_daily_capped", DDL_CAPPED)
    rate = list(client.query(GATE_SQL).result())[0][0]
    if rate is not None and rate >= 0.01:
        raise SystemExit(f"GATE FAILED -- impossible-day rate {rate} >= 1% (CD-03). STOP.")
    print(f"impossible-day gate OK ({rate})")
    _run(client, "fill cap_hours_daily.defl_hrs", UPDATE_DAILY)
    _run(client, "fill cap_hours_annual.defl_hrs_yr internal", UPDATE_ANNUAL_INTERNAL)
    _run(client, "fill cap_hours_annual.defl_hrs_yr CMS", MERGE_ANNUAL_CMS)
    for label, q in CHECKS.items():
        print(f"--- {label} ---")
        for row in _run(client, label, q):
            print("  ", dict(row))


if __name__ == "__main__":
    main()


# REVIEW
# Reviewer 1 LOGIC:
#  - prvdr_county + prvdr_state_cd carried everywhere (rule 12); both
#    provider keys kept; NULL-safe key compare in the UPDATEs (COALESCE '').
#  - Zero-minute rule and clinical-CPT-only fallback per Stage 0 as amended
#    by M60c; F-codes/supplies contribute nothing.
# Reviewer 2 SPEC:
#  - Deviations = the five ASSUMPTION blocks (ladder order, clinical range,
#    CMS deflation ratio, impossible on raw, uncapped defl_hrs).
#  - All tuning numbers read from cap_params via scalar subqueries; the
#    only literals are definitional (24-hr OIG bound, clinical CPT range,
#    99-prefix EM rule).
# Reviewer 3 EFFICIENCY:
#  - Zero claims scans (reads cap_observed_detail: one pass for the build;
#    own/cohort averages derived from the same CTE). UPDATEs are keyed
#    merges, no CROSS JOIN except two deliberate 1-row scalar joins in the
#    CMS ratio update. Relative cost: below module 61.
