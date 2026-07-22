"""
09 - provider profile   [PYTHON runner / BigQuery DDL]

WHAT  : One row per provider from md1_visits_base only. Modal specialty
        (ties broken by higher visit count then alphabetical) with its
        share of the provider's visits; modal provider county raw and
        cleaned per the dictionary's normalization trap (UPPER, TRIM,
        leading SAINT replaced with ST); modal submarket; visits_2024,
        visits_2025 and yoy_growth; new_patient_share_2025; monthly 2025
        average (over all 12 calendar months, zero months included) and
        max (over observed months); 2025 panel age mix as four share
        columns computed over non-null-band visits (null-band visits are
        excluded from the shares and counted in null_age_visits);
        distinct_members_2025.
SCOPE : R6 restated: rows re-checked against the FL/OH/AZ/IL footprint via
        ms_ref_county on the member county with LPAD defense (age and LOB
        scope are inherited from md1_visits_base, which was built under
        the full R6 filter).
R3    : Attribution = PROVIDER county (capacity side). This profile
        describes providers where they practice; the member-county join
        below is scope re-assertion only and is never stored.
GRAIN : srv_prvdr_id.
INPUTS: md1_visits_base (batch A2 output), cfg.table("ref_county")
OUTPUT: md1_provider_profile (BigQuery table).
Run   : python model_and_dashboard_v1/03_rates/09_provider_profile.py
        Run after batch A2 tables are built; independent of notebook 07.
        Writes exactly one table and prints its sanity block.
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

VISITS = cfg.src("md1_visits_base")
CTY    = cfg.table("ref_county")
OUT    = cfg.src("md1_provider_profile")

FOOTPRINT = "('FL', 'OH', 'AZ', 'IL')"

DDL = f"""
CREATE OR REPLACE TABLE `{OUT}`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
WITH scoped AS (
  SELECT v.*
  FROM `{VISITS}` v
  JOIN `{CTY}` rc
    ON LPAD(TRIM(CAST(v.mbr_county_cd AS STRING)), 5, '0') = rc.county_fips
  WHERE rc.state_cd IN {FOOTPRINT}
),
spec_modal AS (
  SELECT srv_prvdr_id, specialty_ctg_cd, COUNT(*) AS spec_visits
  FROM scoped
  GROUP BY srv_prvdr_id, specialty_ctg_cd
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY srv_prvdr_id
    ORDER BY spec_visits DESC, specialty_ctg_cd ASC) = 1
),
cty_modal AS (
  SELECT srv_prvdr_id, prvdr_county, COUNT(*) AS cty_visits
  FROM scoped
  GROUP BY srv_prvdr_id, prvdr_county
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY srv_prvdr_id
    ORDER BY cty_visits DESC, prvdr_county ASC) = 1
),
sm_modal AS (
  SELECT srv_prvdr_id, prvdr_submarket, COUNT(*) AS sm_visits
  FROM scoped
  GROUP BY srv_prvdr_id, prvdr_submarket
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY srv_prvdr_id
    ORDER BY sm_visits DESC, prvdr_submarket ASC) = 1
),
monthly_2025 AS (
  SELECT srv_prvdr_id, month, COUNT(*) AS month_visits
  FROM scoped
  WHERE EXTRACT(YEAR FROM month) = 2025
  GROUP BY srv_prvdr_id, month
),
monthly_agg AS (
  SELECT srv_prvdr_id,
         SUM(month_visits) / 12.0 AS monthly_visits_2025_avg,
         MAX(month_visits) AS monthly_visits_2025_max
  FROM monthly_2025
  GROUP BY srv_prvdr_id
),
totals AS (
  SELECT
    srv_prvdr_id,
    COUNT(*) AS visits_total,
    COUNTIF(EXTRACT(YEAR FROM month) = 2024) AS visits_2024,
    COUNTIF(EXTRACT(YEAR FROM month) = 2025) AS visits_2025,
    COUNTIF(EXTRACT(YEAR FROM month) = 2025 AND is_new_patient) AS new_visits_2025,
    COUNT(DISTINCT IF(EXTRACT(YEAR FROM month) = 2025, member_id, NULL))
      AS distinct_members_2025,
    COUNTIF(EXTRACT(YEAR FROM month) = 2025 AND age_band IS NULL)
      AS null_age_visits,
    COUNTIF(EXTRACT(YEAR FROM month) = 2025 AND age_band = '60-64') AS v_60_64,
    COUNTIF(EXTRACT(YEAR FROM month) = 2025 AND age_band = '65-74') AS v_65_74,
    COUNTIF(EXTRACT(YEAR FROM month) = 2025 AND age_band = '75-84') AS v_75_84,
    COUNTIF(EXTRACT(YEAR FROM month) = 2025 AND age_band = '85p') AS v_85p
  FROM scoped
  GROUP BY srv_prvdr_id
)
SELECT
  t.srv_prvdr_id,
  s.specialty_ctg_cd,
  SAFE_DIVIDE(s.spec_visits, t.visits_total) AS specialty_share,
  c.prvdr_county,
  REGEXP_REPLACE(UPPER(TRIM(c.prvdr_county)), r'^SAINT\\s+', 'ST ')
    AS prvdr_county_clean,
  sm.prvdr_submarket,
  t.visits_2024,
  t.visits_2025,
  SAFE_DIVIDE(t.visits_2025 - t.visits_2024, t.visits_2024) AS yoy_growth,
  SAFE_DIVIDE(t.new_visits_2025, t.visits_2025) AS new_patient_share_2025,
  ma.monthly_visits_2025_avg,
  ma.monthly_visits_2025_max,
  SAFE_DIVIDE(t.v_60_64, t.visits_2025 - t.null_age_visits) AS share_60_64,
  SAFE_DIVIDE(t.v_65_74, t.visits_2025 - t.null_age_visits) AS share_65_74,
  SAFE_DIVIDE(t.v_75_84, t.visits_2025 - t.null_age_visits) AS share_75_84,
  SAFE_DIVIDE(t.v_85p, t.visits_2025 - t.null_age_visits) AS share_85p,
  t.null_age_visits,
  t.distinct_members_2025
FROM totals t
JOIN spec_modal s ON t.srv_prvdr_id = s.srv_prvdr_id
JOIN cty_modal c ON t.srv_prvdr_id = c.srv_prvdr_id
JOIN sm_modal sm ON t.srv_prvdr_id = sm.srv_prvdr_id
LEFT JOIN monthly_agg ma ON t.srv_prvdr_id = ma.srv_prvdr_id
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
    client.query(DDL).result()
    print(f"table created: {OUT}")

    keys = q(client, "row count, key uniqueness, share bounds (R2)", f"""
        SELECT COUNT(*) AS row_count,
               COUNT(DISTINCT srv_prvdr_id) AS distinct_keys,
               COUNTIF(new_patient_share_2025 IS NOT NULL
                       AND (new_patient_share_2025 < 0
                            OR new_patient_share_2025 > 1)) AS bad_new_share
        FROM `{OUT}`""")[0]

    recount = q(client, "sum of visits_2025 vs md1_visits_base 2025 count (R1)", f"""
        SELECT
          (SELECT SUM(visits_2025) FROM `{OUT}`) AS profile_visits_2025,
          (SELECT COUNT(*) FROM `{VISITS}`
           WHERE EXTRACT(YEAR FROM month) = 2025) AS base_visits_2025""")[0]

    shares = q(client, "age-mix share sums (R4)", f"""
        SELECT COUNTIF(ABS(share_60_64 + share_65_74 + share_75_84 + share_85p - 1)
                       > 0.001) AS bad_share_sums
        FROM `{OUT}`
        WHERE share_60_64 IS NOT NULL AND share_65_74 IS NOT NULL
          AND share_75_84 IS NOT NULL AND share_85p IS NOT NULL""")[0]

    assert keys["row_count"] == keys["distinct_keys"], (
        f"GATE FAILED (R2): srv_prvdr_id not unique: {keys}")
    assert keys["bad_new_share"] == 0, (
        f"GATE FAILED (R2): {keys['bad_new_share']} providers with "
        f"new_patient_share_2025 outside 0-1")
    assert recount["profile_visits_2025"] == recount["base_visits_2025"], (
        f"GATE FAILED (R1): profile visits_2025 sum "
        f"{recount['profile_visits_2025']:,} does not equal md1_visits_base 2025 "
        f"count {recount['base_visits_2025']:,}")
    assert shares["bad_share_sums"] == 0, (
        f"GATE FAILED (R4): {shares['bad_share_sums']} providers whose age-mix "
        f"shares do not sum to 1 within 0.001")
    print("\nALL GATES PASSED (R2 key + share bounds, R1 visit total, R4 age mix)")


if __name__ == "__main__":
    main()
