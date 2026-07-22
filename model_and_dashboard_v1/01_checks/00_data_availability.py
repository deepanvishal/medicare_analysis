"""
00 - data availability   [PYTHON / read-only BigQuery checks]

WHAT  : Phase-1 gate. Confirms the claims and membership extracts cover the
        years the pipeline needs: claims window and per-year volumes,
        membership month-by-month continuity, in-scope share, and county
        coverage per footprint state against config expectations.
SCOPE : R6 restated in every query: age_nbr >= 60; business_ln_cd IN
        ('CP','ME') on claims (membership carries no LOB column, so age and
        footprint only there); member county in FL/OH/AZ/IL via
        ms_ref_county with LPAD defense on the county code.
GRAIN : stdout report only; no tables created.
INPUTS: anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_2025_claims
        anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_membership
        cfg.table("ref_county")
OUTPUT: stdout report only.
Run   : python model_and_dashboard_v1/01_checks/00_data_availability.py
        Runnable in any order; 00 first is recommended. Each takes only
        BigQuery read access.
"""

import datetime
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
MBRSHP = "anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_membership"
CTY    = cfg.table("ref_county")

FOOTPRINT = "('FL', 'OH', 'AZ', 'IL')"
FP_JOIN_CLAIMS = (f"JOIN `{CTY}` rc "
                  f"ON LPAD(TRIM(CAST(c.mbr_county_cd AS STRING)), 5, '0') = rc.county_fips")
FP_JOIN_MBR = (f"JOIN `{CTY}` rc "
               f"ON LPAD(TRIM(CAST(m.mbr_county_cd AS STRING)), 5, '0') = rc.county_fips")
CLAIMS_SCOPE = (f"rc.state_cd IN {FOOTPRINT} AND c.age_nbr >= 60 "
                f"AND c.business_ln_cd IN ('CP', 'ME')")
MBR_SCOPE = f"rc.state_cd IN {FOOTPRINT} AND m.age_nbr >= 60"


def q(client, label, sql):
    print(f"\n=== {label} ===")
    rows = [dict(r) for r in client.query(sql).result()]
    if not rows:
        print("  (zero rows)")
    for r in rows:
        print("  ", r)
    return rows


def main():
    client = cfg.client()

    window = q(client, "1a. claims srv_start_dt window (in scope)", f"""
        SELECT MIN(c.srv_start_dt) AS min_dt, MAX(c.srv_start_dt) AS max_dt
        FROM `{CLAIMS}` c
        {FP_JOIN_CLAIMS}
        WHERE {CLAIMS_SCOPE}""")[0]

    q(client, "1b. claims rows and distinct members per year (in scope)", f"""
        SELECT EXTRACT(YEAR FROM c.srv_start_dt) AS yr,
               COUNT(*) AS row_count,
               COUNT(DISTINCT c.member_id) AS members
        FROM `{CLAIMS}` c
        {FP_JOIN_CLAIMS}
        WHERE {CLAIMS_SCOPE}
          AND EXTRACT(YEAR FROM c.srv_start_dt) IN (2023, 2024, 2025)
        GROUP BY yr ORDER BY yr""")

    months = q(client, "2. membership distinct members per eff_yr x eff_mo (in scope)", f"""
        SELECT CAST(m.eff_yr AS INT64) AS eff_yr,
               CAST(m.eff_mo AS INT64) AS eff_mo,
               COUNT(DISTINCT m.member_id) AS members
        FROM `{MBRSHP}` m
        {FP_JOIN_MBR}
        WHERE {MBR_SCOPE}
          AND CAST(m.eff_yr AS INT64) BETWEEN 2023 AND 2025
        GROUP BY eff_yr, eff_mo ORDER BY eff_yr, eff_mo""")
    have = {(r["eff_yr"], r["eff_mo"]) for r in months}
    gaps = [(y, m) for y in (2023, 2024, 2025) for m in range(1, 13)
            if (y, m) not in have]
    print(f"\nmembership month gaps 2023-2025: {gaps if gaps else 'none'}")

    share = q(client, "3. claims in-scope share", f"""
        SELECT
          (SELECT COUNT(*) FROM `{CLAIMS}`) AS total_rows,
          (SELECT COUNT(*) FROM `{CLAIMS}` c {FP_JOIN_CLAIMS}
           WHERE {CLAIMS_SCOPE}) AS in_scope_rows,
          SAFE_DIVIDE(
            (SELECT COUNT(*) FROM `{CLAIMS}` c {FP_JOIN_CLAIMS} WHERE {CLAIMS_SCOPE}),
            (SELECT COUNT(*) FROM `{CLAIMS}`)) AS in_scope_share""")[0]
    print(f"\nin-scope share: {share['in_scope_share']:.4f}")

    counties = q(client, "4. distinct in-scope counties per state (vs config expectation)", f"""
        SELECT rc.state_cd, COUNT(DISTINCT rc.county_fips) AS counties
        FROM `{CLAIMS}` c
        {FP_JOIN_CLAIMS}
        WHERE {CLAIMS_SCOPE}
        GROUP BY rc.state_cd ORDER BY rc.state_cd""")
    print(f"\nconfig expectation: {cfg.COUNTY_COUNTS}")

    min_dt, max_dt = window["min_dt"], window["max_dt"]
    assert min_dt <= datetime.date(2023, 1, 31), (
        f"GATE FAILED (R1): claims window starts {min_dt}, must cover 2023-01")
    assert max_dt >= datetime.date(2025, 12, 1), (
        f"GATE FAILED (R1): claims window ends {max_dt}, must cover 2025-12")
    gaps_24_25 = [g for g in gaps if g[0] in (2024, 2025)]
    assert not gaps_24_25, (
        f"GATE FAILED (R1): membership months missing in 2024-2025: {gaps_24_25}")
    found = {r["state_cd"]: r["counties"] for r in counties}
    for state, expected in cfg.COUNTY_COUNTS.items():
        assert state in found, (
            f"GATE FAILED (R6): footprint state {state} absent from in-scope claims")
        assert found[state] <= expected, (
            f"GATE FAILED (R6): {state} shows {found[state]} counties, "
            f"config expects at most {expected}")
    print("\nALL GATES PASSED (R1 window, R1 membership continuity, R6 counties)")


if __name__ == "__main__":
    main()
