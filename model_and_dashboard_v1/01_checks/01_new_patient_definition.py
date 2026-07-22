"""
01 - new-patient definition   [PYTHON / read-only BigQuery checks]

WHAT  : Re-derives and validates the 12-month new-patient rule on the
        rebuilt claims table using srv_prvdr_id (the only provider id,
        per the data dictionary). Visit key = distinct member_id x
        srv_prvdr_id x srv_start_dt. Months 2024-01 through 2025-12 are
        reported; 2023 serves as lookback memory only. Prints the monthly
        new-share series, the 2024 vs 2025 averages, and a sensitivity of
        the 2025 average under 6- and 18-month windows.
SCOPE : R6 restated in every query: age_nbr >= 60; business_ln_cd IN
        ('CP','ME'); member county in FL/OH/AZ/IL via ms_ref_county with
        LPAD defense on the county code.
GRAIN : stdout report only; no tables created.
INPUTS: anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_2025_claims
        cfg.table("ref_county")
OUTPUT: stdout report only.
Run   : python model_and_dashboard_v1/01_checks/01_new_patient_definition.py
        Runnable in any order; 00 first is recommended. Each takes only
        BigQuery read access.
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

CLAIMS = "anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_2025_claims"
CTY    = cfg.table("ref_county")

FOOTPRINT = "('FL', 'OH', 'AZ', 'IL')"
VISIT_KEY = ("CONCAT(CAST(s.member_id AS STRING), '|', CAST(s.srv_prvdr_id AS STRING), "
             "'|', CAST(s.srv_start_dt AS STRING))")


def monthly_new_share_sql(window_months):
    return f"""
    WITH scoped AS (
      SELECT DISTINCT
        c.member_id,
        c.srv_prvdr_id,
        c.srv_start_dt,
        DATE_TRUNC(c.srv_start_dt, MONTH) AS month
      FROM `{CLAIMS}` c
      JOIN `{CTY}` rc
        ON LPAD(TRIM(CAST(c.mbr_county_cd AS STRING)), 5, '0') = rc.county_fips
      WHERE rc.state_cd IN {FOOTPRINT}
        AND c.age_nbr >= 60
        AND c.business_ln_cd IN ('CP', 'ME')
    ),
    pair_months AS (
      SELECT DISTINCT member_id, srv_prvdr_id, month FROM scoped
    ),
    flagged AS (
      SELECT
        member_id,
        srv_prvdr_id,
        month,
        COALESCE(
          LAG(month) OVER (PARTITION BY member_id, srv_prvdr_id ORDER BY month)
            < DATE_SUB(month, INTERVAL {window_months} MONTH),
          TRUE) AS is_new
      FROM pair_months
    )
    SELECT
      f.month,
      COUNT(DISTINCT {VISIT_KEY}) AS visits,
      COUNT(DISTINCT IF(f.is_new, {VISIT_KEY}, NULL)) AS new_visits,
      SAFE_DIVIDE(
        COUNT(DISTINCT IF(f.is_new, {VISIT_KEY}, NULL)),
        COUNT(DISTINCT {VISIT_KEY})) AS new_share
    FROM scoped s
    JOIN flagged f
      ON s.member_id = f.member_id
      AND s.srv_prvdr_id = f.srv_prvdr_id
      AND s.month = f.month
    WHERE f.month BETWEEN DATE '2024-01-01' AND DATE '2025-12-01'
    GROUP BY f.month
    ORDER BY f.month
    """


def avg_share(rows, year):
    shares = [float(r["new_share"]) for r in rows
              if r["month"].year == year and r["new_share"] is not None]
    return sum(shares) / len(shares) if shares else None


def main():
    client = cfg.client()

    print("=== monthly new-patient share, 12-month window ===")
    rows12 = [dict(r) for r in client.query(monthly_new_share_sql(12)).result()]
    for r in rows12:
        print(f"  {r['month']}  visits={r['visits']:,}  "
              f"new={r['new_visits']:,}  share={float(r['new_share']):.4f}")
    avg_2024 = avg_share(rows12, 2024)
    avg_2025 = avg_share(rows12, 2025)
    print(f"\n2024 average new-share: {avg_2024:.4f}")
    print(f"2025 average new-share: {avg_2025:.4f}")

    print("\n=== sensitivity: 2025 average under 6 / 12 / 18 month windows ===")
    rows6 = [dict(r) for r in client.query(monthly_new_share_sql(6)).result()]
    rows18 = [dict(r) for r in client.query(monthly_new_share_sql(18)).result()]
    avg6 = avg_share(rows6, 2025)
    avg18 = avg_share(rows18, 2025)
    print(f"  6-month window : {avg6:.4f}")
    print(f"  12-month window: {avg_2025:.4f}")
    print(f"  18-month window: {avg18:.4f}")

    for r in rows12:
        share = float(r["new_share"])
        assert 0.0 < share < 1.0, (
            f"GATE FAILED (R2): new-share for {r['month']} is {share}; "
            f"must be strictly between 0 and 1")
    jan_2024 = next(float(r["new_share"]) for r in rows12
                    if r["month"].year == 2024 and r["month"].month == 1)
    assert jan_2024 <= avg_2024 + 0.15, (
        f"GATE FAILED (R1): January 2024 new-share {jan_2024:.4f} exceeds the "
        f"2024 average {avg_2024:.4f} by more than 15 points - 2023 lookback "
        f"memory is not working")
    print("\nALL GATES PASSED (R2 share bounds, R1 lookback memory)")


if __name__ == "__main__":
    main()
