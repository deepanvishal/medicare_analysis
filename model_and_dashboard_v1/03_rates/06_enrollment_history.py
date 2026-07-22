"""
06 - enrollment history   [PYTHON runner / BigQuery DDL]

WHAT  : County x age band x month member counts, 2023-01 through 2025-12,
        with state and submarket carried as context columns (submarket is
        the county's MAX value, context only, never a filter per the data
        dictionary). members = distinct member count; members_f and
        members_m split on UPPER(TRIM(gender_cd)) = 'F' / 'M'; any other
        gender code counts in members but in neither split (the gender
        value set is not verified beyond existence).
SCOPE : R6 restated: age_nbr >= 60; member county in FL/OH/AZ/IL via
        ms_ref_county with LPAD defense (membership carries no LOB column,
        so age and footprint only).
R3    : Attribution = MEMBER county (demand side). Enrollment counts
        members where they live; provider geography never enters here.
GRAIN : mbr_county_cd x age_band x month.
INPUTS: anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_membership
        cfg.table("ref_county")
OUTPUT: md1_enrollment_history (BigQuery table).
Run   : python model_and_dashboard_v1/03_rates/06_enrollment_history.py
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
OUT    = cfg.src("md1_enrollment_history")

FOOTPRINT = "('FL', 'OH', 'AZ', 'IL')"

DDL = f"""
CREATE OR REPLACE TABLE `{OUT}`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
WITH scoped AS (
  SELECT
    m.member_id,
    DATE(CAST(m.eff_yr AS INT64), CAST(m.eff_mo AS INT64), 1) AS month,
    m.mbr_county_cd,
    rc.state_cd,
    m.mbr_submarket,
    m.gender_cd,
    m.age_nbr
  FROM `{MBRSHP}` m
  JOIN `{CTY}` rc
    ON LPAD(TRIM(CAST(m.mbr_county_cd AS STRING)), 5, '0') = rc.county_fips
  WHERE rc.state_cd IN {FOOTPRINT}
    AND m.age_nbr >= 60
    AND CAST(m.eff_yr AS INT64) BETWEEN 2023 AND 2025
)
SELECT
  mbr_county_cd,
  CASE WHEN age_nbr BETWEEN 60 AND 64 THEN '60-64'
       WHEN age_nbr BETWEEN 65 AND 74 THEN '65-74'
       WHEN age_nbr BETWEEN 75 AND 84 THEN '75-84'
       ELSE '85p' END AS age_band,
  month,
  ANY_VALUE(state_cd) AS state_cd,
  MAX(mbr_submarket) AS mbr_submarket,
  COUNT(DISTINCT member_id) AS members,
  COUNT(DISTINCT IF(UPPER(TRIM(gender_cd)) = 'F', member_id, NULL)) AS members_f,
  COUNT(DISTINCT IF(UPPER(TRIM(gender_cd)) = 'M', member_id, NULL)) AS members_m
FROM scoped
GROUP BY mbr_county_cd, age_band, month
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
               COUNT(DISTINCT CONCAT(CAST(mbr_county_cd AS STRING), '|',
                                     age_band, '|',
                                     CAST(month AS STRING))) AS distinct_keys
        FROM `{OUT}`""")[0]

    monthly = q(client, "summed members per month vs source distinct members (R1/R4)", f"""
        WITH tbl AS (
          SELECT month, SUM(members) AS summed_members
          FROM `{OUT}` GROUP BY month
        ),
        src AS (
          SELECT DATE(CAST(m.eff_yr AS INT64), CAST(m.eff_mo AS INT64), 1) AS month,
                 COUNT(DISTINCT m.member_id) AS src_members
          FROM `{MBRSHP}` m
          JOIN `{CTY}` rc
            ON LPAD(TRIM(CAST(m.mbr_county_cd AS STRING)), 5, '0') = rc.county_fips
          WHERE rc.state_cd IN {FOOTPRINT}
            AND m.age_nbr >= 60
            AND CAST(m.eff_yr AS INT64) BETWEEN 2023 AND 2025
          GROUP BY month
        )
        SELECT t.month, t.summed_members, s.src_members,
               SAFE_DIVIDE(ABS(t.summed_members - s.src_members), s.src_members) AS diff_share
        FROM tbl t
        JOIN src s ON t.month = s.month
        ORDER BY t.month""")

    states = q(client, "footprint states present per month (R6)", f"""
        SELECT month, COUNT(DISTINCT state_cd) AS states_present
        FROM `{OUT}`
        GROUP BY month
        HAVING states_present < 4
        ORDER BY month""")

    assert keys["row_count"] == keys["distinct_keys"], (
        f"GATE FAILED (R2): key not unique at county x band x month: {keys}")
    for r in monthly:
        assert r["diff_share"] is not None and float(r["diff_share"]) <= 0.005, (
            f"GATE FAILED (R1/R4): {r['month']} summed members deviate from source "
            f"by {r['diff_share']}: {r}")
    assert not states, (
        f"GATE FAILED (R6): months with fewer than 4 footprint states: {states}")
    print("\nALL GATES PASSED (R2 key, R1/R4 monthly reconciliation, R6 states)")


if __name__ == "__main__":
    main()
