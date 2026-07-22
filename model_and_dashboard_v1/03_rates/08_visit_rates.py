"""
08 - visit rates + county calibration   [PYTHON runner / BigQuery DDL]

WHAT  : Two shipping tables (D13). (1) md1_visit_rates - the dashboard's
        visits[condition, specialty] coefficients: cms_specialty x
        condition (BASE_RATE and OTHER_CONDITION rows included), copied
        from md1_visitsplit_rates.coef_deflated as visit_rate with
        members_with_condition and r2 carried through. (2)
        md1_county_calibration - per county x specialty factor that
        makes the assembled chain reproduce 2025 actuals exactly at
        baseline while the rate table shapes slider deltas (standard
        post-stratification; planned as notebook 19's reconcile step,
        pulled forward here per D13). Post-dedupe validation context:
        reconstruction WAPE 27.27 percent, p90 57.8 - verdict REVIEW;
        root cause is national rates vs local practice patterns.
FORMULA: predicted_2025(county, spec) = members(county) * base_rate +
        sum over conditions of members_with_condition(county, cond) *
        visit_rate(cond, spec) - the same aggregation as notebook 15
        section 2. raw_factor = SAFE_DIVIDE(actual_2025,
        predicted_2025). Shipped calibration_factor protections: cells
        with actual under SHRINK_N visits shrink toward the state x
        specialty factor with weight n / (n + SHRINK_N); the final
        factor is clamped to CLAMP_LO..CLAMP_HI (clamped count
        printed). Demand formula downstream: members x sickness rates x
        visit rates x county calibration factor.
SCOPE : R6 restated asymmetrically as in 15: the prediction exposure
        re-applies age_nbr >= 60 and the FL/OH/AZ/IL footprint on the
        membership spine (ms_ref_county with LPAD defense); the actuals
        leg re-applies only the footprint on each visit's member county
        - age and the CP/ME LOB rule are inherited from the
        claims-built inputs per the data dictionary.
R3    : Attribution = MEMBER county (demand side) on both legs; the
        exposure uses each member's last observed 2025 month's county,
        the actuals use each visit's own member county. Provider
        geography never enters.
GRAIN : md1_visit_rates: cms_specialty x condition.
        md1_county_calibration: mbr_county_cd x cms_specialty
        (mbr_county_cd stored LPAD-normalized to 5 characters).
INPUTS: md1_visitsplit_rates (14), md1_visits_base, md1_member_base,
        md1_condition_flags (batch A2), md1_ref_specialty_demand (05b -
        never the compliance crosswalk, per D12), cfg.table("ref_county")
OUTPUT: md1_visit_rates and md1_county_calibration (BigQuery tables).
Run   : python model_and_dashboard_v1/03_rates/08_visit_rates.py
        Run after 05b, 14 and the 15 validation (its verdicts are
        advisory input to D13); independent of 07 and 09.
"""

import os
import sys


def _expanded_scope_dir():
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        repo = os.path.dirname(os.path.dirname(here))
        return os.path.join(repo, "expanded_scope")
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

RATES_SRC = cfg.src("md1_visitsplit_rates")
VISITS    = cfg.src("md1_visits_base")
CFLAGS    = cfg.src("md1_condition_flags")
MBASE     = cfg.src("md1_member_base")
DMAP      = cfg.src("md1_ref_specialty_demand")
CTY       = cfg.table("ref_county")
OUT_RATES = cfg.src("md1_visit_rates")
OUT_CAL   = cfg.src("md1_county_calibration")

FOOTPRINT = "('FL', 'OH', 'AZ', 'IL')"

STUDY_YEAR = 2025
SHRINK_N = 500
CLAMP_LO = 0.1
CLAMP_HI = 3.0
IDENTITY_MIN_VISITS = 1000

DDL_RATES = f"""
CREATE OR REPLACE TABLE `{OUT_RATES}`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
SELECT
  cms_specialty,
  condition,
  coef_deflated AS visit_rate,
  members_with_condition,
  r2
FROM `{RATES_SRC}`
"""

DDL_CAL = f"""
CREATE OR REPLACE TABLE `{OUT_CAL}`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
WITH rates AS (
  SELECT cms_specialty, condition, coef_deflated
  FROM `{RATES_SRC}`
),
spine_cty AS (
  SELECT mb.member_id,
         LPAD(TRIM(CAST(mb.mbr_county_cd AS STRING)), 5, '0')
           AS mbr_county_cd,
         rc.state_cd
  FROM `{MBASE}` mb
  JOIN `{CTY}` rc
    ON LPAD(TRIM(CAST(mb.mbr_county_cd AS STRING)), 5, '0') = rc.county_fips
  WHERE rc.state_cd IN {FOOTPRINT}
    AND mb.age_nbr >= 60
    AND EXTRACT(YEAR FROM mb.month) = {STUDY_YEAR}
  QUALIFY ROW_NUMBER() OVER (PARTITION BY mb.member_id
                             ORDER BY mb.month DESC) = 1
),
county_members AS (
  SELECT mbr_county_cd, ANY_VALUE(state_cd) AS state_cd,
         COUNT(*) AS member_count
  FROM spine_cty
  GROUP BY mbr_county_cd
),
mc AS (
  SELECT DISTINCT s.member_id, s.mbr_county_cd,
    CASE WHEN CAST(f.HCC_v24 AS STRING) IN
              (SELECT condition FROM rates
               WHERE condition NOT IN ('BASE_RATE', 'OTHER_CONDITION'))
         THEN CAST(f.HCC_v24 AS STRING)
         ELSE 'OTHER_CONDITION' END AS condition
  FROM `{CFLAGS}` f
  JOIN spine_cty s ON f.member_id = s.member_id
  WHERE f.year = {STUDY_YEAR}
),
county_cond AS (
  SELECT mbr_county_cd, condition, COUNT(DISTINCT member_id) AS member_count
  FROM mc
  GROUP BY mbr_county_cd, condition
),
pred_base AS (
  SELECT cm.mbr_county_cd, cm.state_cd, r.cms_specialty,
         cm.member_count * r.coef_deflated AS pred
  FROM county_members cm
  CROSS JOIN (SELECT cms_specialty, coef_deflated
              FROM rates WHERE condition = 'BASE_RATE') r
),
pred_cond AS (
  SELECT cc.mbr_county_cd, r.cms_specialty,
         SUM(cc.member_count * r.coef_deflated) AS pred
  FROM county_cond cc
  JOIN rates r ON r.condition = cc.condition
  GROUP BY cc.mbr_county_cd, r.cms_specialty
),
predicted AS (
  SELECT b.mbr_county_cd, b.state_cd, b.cms_specialty,
         b.pred + COALESCE(c.pred, 0) AS predicted_2025
  FROM pred_base b
  LEFT JOIN pred_cond c
    ON b.mbr_county_cd = c.mbr_county_cd
    AND b.cms_specialty = c.cms_specialty
),
actual AS (
  SELECT LPAD(TRIM(CAST(v.mbr_county_cd AS STRING)), 5, '0')
           AS mbr_county_cd,
         rc.state_cd, dm.cms_specialty, COUNT(*) AS actual_2025
  FROM `{VISITS}` v
  JOIN `{DMAP}` dm
    ON TRIM(CAST(v.specialty_ctg_cd AS STRING)) = TRIM(CAST(dm.aetna_cd AS STRING))
  JOIN `{CTY}` rc
    ON LPAD(TRIM(CAST(v.mbr_county_cd AS STRING)), 5, '0') = rc.county_fips
  WHERE EXTRACT(YEAR FROM v.month) = {STUDY_YEAR}
    AND rc.state_cd IN {FOOTPRINT}
    AND dm.cms_specialty IN (SELECT DISTINCT cms_specialty FROM rates)
  GROUP BY mbr_county_cd, rc.state_cd, dm.cms_specialty
),
cells AS (
  SELECT COALESCE(p.mbr_county_cd, a.mbr_county_cd) AS mbr_county_cd,
         COALESCE(p.state_cd, a.state_cd) AS state_cd,
         COALESCE(p.cms_specialty, a.cms_specialty) AS cms_specialty,
         COALESCE(a.actual_2025, 0) AS actual_2025,
         COALESCE(p.predicted_2025, 0) AS predicted_2025
  FROM predicted p
  FULL OUTER JOIN actual a
    ON p.mbr_county_cd = a.mbr_county_cd
    AND p.cms_specialty = a.cms_specialty
),
state_factor AS (
  SELECT state_cd, cms_specialty,
         SAFE_DIVIDE(SUM(actual_2025), SUM(predicted_2025)) AS sf
  FROM cells
  GROUP BY state_cd, cms_specialty
),
factored AS (
  SELECT c.mbr_county_cd, c.state_cd, c.cms_specialty,
         c.actual_2025, c.predicted_2025, s.sf,
         SAFE_DIVIDE(c.actual_2025, c.predicted_2025) AS raw_factor,
         CASE WHEN c.actual_2025 >= {SHRINK_N}
              THEN SAFE_DIVIDE(c.actual_2025, c.predicted_2025)
              ELSE (c.actual_2025 / (c.actual_2025 + {SHRINK_N})) *
                   COALESCE(SAFE_DIVIDE(c.actual_2025, c.predicted_2025),
                            s.sf)
                   + ({SHRINK_N} / (c.actual_2025 + {SHRINK_N})) * s.sf
         END AS factor_pre
  FROM cells c
  LEFT JOIN state_factor s
    ON c.state_cd = s.state_cd AND c.cms_specialty = s.cms_specialty
)
SELECT
  mbr_county_cd,
  state_cd,
  cms_specialty,
  actual_2025,
  predicted_2025,
  raw_factor,
  LEAST(GREATEST(COALESCE(factor_pre, sf, 1.0), {CLAMP_LO}), {CLAMP_HI})
    AS calibration_factor,
  (COALESCE(factor_pre, sf, 1.0) < {CLAMP_LO}
   OR COALESCE(factor_pre, sf, 1.0) > {CLAMP_HI}) AS clamped
FROM factored
"""


def q(client, label, sql):
    print(f"\n=== {label} ===")
    rows = [dict(r) for r in client.query(sql).result()]
    for r in rows[:40]:
        print("  ", r)
    if len(rows) > 40:
        print(f"  ... ({len(rows) - 40} more rows)")
    return rows


def main():
    client = cfg.client()

    client.query(DDL_RATES).result()
    print(f"table created: {OUT_RATES}")
    rk = q(client, "md1_visit_rates: keys, nonnegativity, BASE_RATE rows "
                   "(R1/R2)", f"""
        SELECT COUNT(*) AS row_count,
               COUNT(DISTINCT CONCAT(cms_specialty, '|', condition))
                 AS distinct_keys,
               COUNTIF(visit_rate IS NULL OR visit_rate < 0) AS bad_values,
               (SELECT COUNTIF(base_rows != 1) FROM (
                  SELECT cms_specialty,
                         COUNTIF(condition = 'BASE_RATE') AS base_rows
                  FROM `{OUT_RATES}`
                  GROUP BY cms_specialty)) AS specs_without_one_base
        FROM `{OUT_RATES}`""")[0]
    assert rk["row_count"] == rk["distinct_keys"], (
        f"GATE FAILED (R2): md1_visit_rates key not unique: {rk}")
    assert rk["bad_values"] == 0, (
        f"GATE FAILED (R2): {rk['bad_values']} null or negative visit_rate "
        f"values")
    assert rk["specs_without_one_base"] == 0, (
        f"GATE FAILED (R1): {rk['specs_without_one_base']} specialties "
        f"without exactly one BASE_RATE row")

    client.query(DDL_CAL).result()
    print(f"table created: {OUT_CAL}")

    ck = q(client, "md1_county_calibration: keys, bounds, cell mix (R2)", f"""
        SELECT COUNT(*) AS row_count,
               COUNT(DISTINCT CONCAT(mbr_county_cd, '|', cms_specialty))
                 AS distinct_keys,
               COUNT(DISTINCT mbr_county_cd) AS counties,
               COUNT(DISTINCT cms_specialty) AS specialties,
               COUNTIF(calibration_factor IS NULL
                       OR calibration_factor < {CLAMP_LO}
                       OR calibration_factor > {CLAMP_HI}) AS out_of_bounds,
               COUNTIF(clamped) AS clamped_cells,
               COUNTIF(actual_2025 < {SHRINK_N}) AS shrunken_cells
        FROM `{OUT_CAL}`""")[0]
    print(f"\nclamped cells: {ck['clamped_cells']:,}; small cells shrunk "
          f"toward the state factor (actual under {SHRINK_N}): "
          f"{ck['shrunken_cells']:,}")

    recon = q(client, "actual totals vs bridged 2025 total (R4)", f"""
        SELECT
          (SELECT SUM(actual_2025) FROM `{OUT_CAL}`) AS cell_actuals,
          (SELECT COUNT(*)
           FROM `{VISITS}` v
           JOIN `{DMAP}` dm
             ON TRIM(CAST(v.specialty_ctg_cd AS STRING))
                = TRIM(CAST(dm.aetna_cd AS STRING))
           WHERE EXTRACT(YEAR FROM v.month) = {STUDY_YEAR}) AS bridged_total
        """)[0]

    identity = q(client, "calibration identity pre-clamp on cells with "
                         f">= {IDENTITY_MIN_VISITS} actual visits (R4)", f"""
        SELECT COUNTIF(raw_factor IS NULL
                       OR ABS(actual_2025 - raw_factor * predicted_2025)
                          > 0.005 * actual_2025) AS broken_cells
        FROM `{OUT_CAL}`
        WHERE actual_2025 >= {IDENTITY_MIN_VISITS}""")[0]

    assert ck["row_count"] == ck["distinct_keys"], (
        f"GATE FAILED (R2): calibration key not unique at county x "
        f"specialty: {ck}")
    assert ck["out_of_bounds"] == 0, (
        f"GATE FAILED (R2): {ck['out_of_bounds']} calibration factors null "
        f"or outside {CLAMP_LO}..{CLAMP_HI}")
    assert recon["bridged_total"] > 0, (
        f"GATE FAILED (R4): no bridged {STUDY_YEAR} visits found")
    gap = (recon["bridged_total"] - recon["cell_actuals"]) / \
        recon["bridged_total"]
    assert gap <= 0.005, (
        f"GATE FAILED (R4): calibration cells cover "
        f"{recon['cell_actuals']:,} of {recon['bridged_total']:,} bridged "
        f"visits (gap {100 * gap:.3f}%, over 0.5 percent) - check "
        f"county-join failures and non-footprint member counties")
    assert identity["broken_cells"] == 0, (
        f"GATE FAILED (R4 calibration identity): "
        f"{identity['broken_cells']} cells with >= "
        f"{IDENTITY_MIN_VISITS} actual visits where actual != raw_factor x "
        f"predicted within 0.5 percent")
    print(f"\nALL GATES PASSED (R2 keys + factor bounds on both tables, "
          f"R1 one BASE_RATE per specialty, R4 actuals within 0.5 percent "
          f"of bridged total {recon['bridged_total']:,} + calibration "
          f"identity)")


if __name__ == "__main__":
    main()
