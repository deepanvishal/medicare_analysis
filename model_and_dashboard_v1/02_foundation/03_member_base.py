"""
03 - member base   [PYTHON runner / BigQuery DDL]

WHAT  : In-scope member spine. One row per member_id x month (2023-01
        through 2025-12) with age band, gender, geography, and tenure.
        first_seen_month = the member's earliest eff month in the window;
        tenure_months = months since it; is_first_year = tenure under 12
        months. LEFT-CENSORING at 2023-01: a member enrolled since 2019
        looks identical to one enrolled since 2023-01.
        NOTE: the membership extract carries NO business_ln_cd column
        (data dictionary, GAP 7 verified column list), so no LOB column is
        stored here; the CP/ME scope rule binds on claims-side tables.
SCOPE : R6 restated: age_nbr >= 60; member county in FL/OH/AZ/IL via
        ms_ref_county with LPAD defense (membership carries no LOB column,
        so age and footprint only).
R3    : Attribution = MEMBER county (demand side). This table carries
        mbr_county_cd because everything built on it counts members where
        they live, never where providers practice.
GRAIN : member_id x month.
INPUTS: anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_membership
        cfg.table("ref_county")
OUTPUT: md1_member_base (BigQuery table).
Run   : python model_and_dashboard_v1/02_foundation/03_member_base.py
        The four foundation scripts are mutually independent: none reads
        another's output; all read only source tables. Run in any order
        after checks 00-02 pass. Each writes exactly one table.
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

MBRSHP = "anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_membership"
CTY    = cfg.table("ref_county")
OUT    = cfg.src("md1_member_base")

FOOTPRINT = "('FL', 'OH', 'AZ', 'IL')"

DDL = f"""
CREATE OR REPLACE TABLE `{OUT}`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
WITH scoped AS (
  SELECT
    m.member_id,
    DATE(CAST(m.eff_yr AS INT64), CAST(m.eff_mo AS INT64), 1) AS month,
    MAX(m.age_nbr)        AS age_nbr,
    MAX(m.gender_cd)      AS gender_cd,
    MAX(m.mbr_county_cd)  AS mbr_county_cd,
    MAX(m.mbr_state)      AS mbr_state,
    MAX(m.mbr_submarket)  AS mbr_submarket
  FROM `{MBRSHP}` m
  JOIN `{CTY}` rc
    ON LPAD(TRIM(CAST(m.mbr_county_cd AS STRING)), 5, '0') = rc.county_fips
  WHERE rc.state_cd IN {FOOTPRINT}
    AND m.age_nbr >= 60
    AND CAST(m.eff_yr AS INT64) BETWEEN 2023 AND 2025
  GROUP BY m.member_id, month
)
SELECT
  member_id,
  month,
  age_nbr,
  CASE WHEN age_nbr BETWEEN 60 AND 64 THEN '60-64'
       WHEN age_nbr BETWEEN 65 AND 74 THEN '65-74'
       WHEN age_nbr BETWEEN 75 AND 84 THEN '75-84'
       ELSE '85p' END AS age_band,
  gender_cd,
  mbr_county_cd,
  mbr_state,
  mbr_submarket,
  MIN(month) OVER (PARTITION BY member_id) AS first_seen_month,
  DATE_DIFF(month, MIN(month) OVER (PARTITION BY member_id), MONTH) AS tenure_months,
  DATE_DIFF(month, MIN(month) OVER (PARTITION BY member_id), MONTH) < 12 AS is_first_year
FROM scoped
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

    keys = q(client, "row count and key uniqueness (R2)", f"""
        SELECT COUNT(*) AS row_count,
               COUNT(DISTINCT CONCAT(member_id, '|', CAST(month AS STRING))) AS distinct_keys,
               COUNTIF(member_id IS NULL OR month IS NULL) AS null_keys
        FROM `{OUT}`""")[0]

    nulls = q(client, "null age_band / county (R2)", f"""
        SELECT COUNTIF(age_band IS NULL) AS null_age_band,
               COUNTIF(mbr_county_cd IS NULL) AS null_county
        FROM `{OUT}`""")[0]

    compare = q(client, "2024-2025 monthly members: table vs source (R1)", f"""
        WITH tbl AS (
          SELECT month, COUNT(DISTINCT member_id) AS tbl_members
          FROM `{OUT}`
          WHERE EXTRACT(YEAR FROM month) IN (2024, 2025)
          GROUP BY month
        ),
        src AS (
          SELECT DATE(CAST(m.eff_yr AS INT64), CAST(m.eff_mo AS INT64), 1) AS month,
                 COUNT(DISTINCT m.member_id) AS src_members
          FROM `{MBRSHP}` m
          JOIN `{CTY}` rc
            ON LPAD(TRIM(CAST(m.mbr_county_cd AS STRING)), 5, '0') = rc.county_fips
          WHERE rc.state_cd IN {FOOTPRINT}
            AND m.age_nbr >= 60
            AND CAST(m.eff_yr AS INT64) IN (2024, 2025)
          GROUP BY month
        )
        SELECT t.month, t.tbl_members, s.src_members,
               SAFE_DIVIDE(ABS(t.tbl_members - s.src_members), s.src_members) AS diff_share
        FROM tbl t
        JOIN src s ON t.month = s.month
        ORDER BY t.month""")

    assert keys["row_count"] == keys["distinct_keys"] and keys["null_keys"] == 0, (
        f"GATE FAILED (R2): key not unique or null at member x month: {keys}")
    assert nulls["null_age_band"] == 0 and nulls["null_county"] == 0, (
        f"GATE FAILED (R2): null age_band or county present: {nulls}")
    for r in compare:
        assert r["diff_share"] is not None and float(r["diff_share"]) <= 0.01, (
            f"GATE FAILED (R1): {r['month']} table members deviate from source "
            f"by {r['diff_share']}: {r}")
    print("\nALL GATES PASSED (R2 key + nulls, R1 monthly continuity)")


if __name__ == "__main__":
    main()
