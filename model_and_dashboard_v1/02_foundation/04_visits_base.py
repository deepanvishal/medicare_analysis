"""
04 - visits base   [PYTHON runner / BigQuery DDL]

WHAT  : Visit-level table. One row per visit = distinct member_id x
        srv_prvdr_id x srv_start_dt, months 2024-01 through 2025-12; 2023
        is used only as lookback memory for is_new_patient (the 12-month
        member x provider rule). Age at service comes from the membership
        extract joined on member and service month; when the service month
        row is missing, the nearest prior month within 3 months is used,
        else age is null and counted. Attribute columns that vary across a
        visit's claim lines are collapsed with MAX for a deterministic
        single row per key.
SCOPE : R6 restated: age_nbr >= 60; business_ln_cd IN ('CP','ME'); member
        county in FL/OH/AZ/IL via ms_ref_county with LPAD defense.
R3    : BOTH county columns are kept: demand analyses use mbr_county_cd
        (member county), capacity analyses use prvdr_county (provider
        county). This table is the one place both lenses share a row;
        downstream tables must pick exactly one and say so.
GRAIN : member_id x srv_prvdr_id x srv_start_dt.
INPUTS: anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_2025_claims
        anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_membership
        cfg.table("ref_county")
OUTPUT: md1_visits_base (BigQuery table).
Run   : python model_and_dashboard_v1/02_foundation/04_visits_base.py
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

CLAIMS = "anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_2025_claims"
MBRSHP = "anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_membership"
CTY    = cfg.table("ref_county")
OUT    = cfg.src("md1_visits_base")

FOOTPRINT = "('FL', 'OH', 'AZ', 'IL')"

DDL = f"""
CREATE OR REPLACE TABLE `{OUT}`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
WITH scoped AS (
  SELECT
    c.member_id,
    c.srv_prvdr_id,
    c.srv_start_dt,
    DATE_TRUNC(c.srv_start_dt, MONTH) AS month,
    MAX(c.mbr_county_cd)    AS mbr_county_cd,
    MAX(c.specialty_ctg_cd) AS specialty_ctg_cd,
    MAX(c.prvdr_county)     AS prvdr_county,
    MAX(c.prvdr_submarket)  AS prvdr_submarket
  FROM `{CLAIMS}` c
  JOIN `{CTY}` rc
    ON LPAD(TRIM(CAST(c.mbr_county_cd AS STRING)), 5, '0') = rc.county_fips
  WHERE rc.state_cd IN {FOOTPRINT}
    AND c.age_nbr >= 60
    AND c.business_ln_cd IN ('CP', 'ME')
  GROUP BY c.member_id, c.srv_prvdr_id, c.srv_start_dt, month
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
        < DATE_SUB(month, INTERVAL 12 MONTH),
      TRUE) AS is_new_patient
  FROM pair_months
),
mbr_months AS (
  SELECT
    member_id,
    DATE(CAST(eff_yr AS INT64), CAST(eff_mo AS INT64), 1) AS mo,
    MAX(age_nbr) AS age_nbr
  FROM `{MBRSHP}`
  WHERE CAST(eff_yr AS INT64) BETWEEN 2023 AND 2025
  GROUP BY member_id, mo
),
aged AS (
  SELECT
    s.member_id,
    s.srv_prvdr_id,
    s.srv_start_dt,
    s.month,
    s.mbr_county_cd,
    s.specialty_ctg_cd,
    s.prvdr_county,
    s.prvdr_submarket,
    m.age_nbr
  FROM scoped s
  LEFT JOIN mbr_months m
    ON s.member_id = m.member_id
    AND m.mo BETWEEN DATE_SUB(s.month, INTERVAL 3 MONTH) AND s.month
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY s.member_id, s.srv_prvdr_id, s.srv_start_dt
    ORDER BY m.mo DESC) = 1
)
SELECT
  a.member_id,
  a.srv_prvdr_id,
  a.srv_start_dt,
  a.month,
  a.mbr_county_cd,
  a.specialty_ctg_cd,
  a.prvdr_county,
  a.prvdr_submarket,
  f.is_new_patient,
  a.age_nbr,
  CASE WHEN a.age_nbr BETWEEN 60 AND 64 THEN '60-64'
       WHEN a.age_nbr BETWEEN 65 AND 74 THEN '65-74'
       WHEN a.age_nbr BETWEEN 75 AND 84 THEN '75-84'
       WHEN a.age_nbr >= 85 THEN '85p'
       ELSE NULL END AS age_band
FROM aged a
JOIN flagged f
  ON a.member_id = f.member_id
  AND a.srv_prvdr_id = f.srv_prvdr_id
  AND a.month = f.month
WHERE a.month BETWEEN DATE '2024-01-01' AND DATE '2025-12-01'
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
               COUNT(DISTINCT CONCAT(CAST(member_id AS STRING), '|',
                                     CAST(srv_prvdr_id AS STRING), '|',
                                     CAST(srv_start_dt AS STRING))) AS distinct_keys,
               COUNTIF(is_new_patient IS NULL) AS null_new_flag,
               COUNTIF(age_band IS NULL) AS null_age_band,
               SAFE_DIVIDE(COUNTIF(age_band IS NULL), COUNT(*)) AS null_age_band_share
        FROM `{OUT}`""")[0]

    recount = q(client, "independent visit recount from raw claims (R1)", f"""
        SELECT COUNT(DISTINCT CONCAT(CAST(c.member_id AS STRING), '|',
                                     CAST(c.srv_prvdr_id AS STRING), '|',
                                     CAST(c.srv_start_dt AS STRING))) AS raw_visits
        FROM `{CLAIMS}` c
        JOIN `{CTY}` rc
          ON LPAD(TRIM(CAST(c.mbr_county_cd AS STRING)), 5, '0') = rc.county_fips
        WHERE rc.state_cd IN {FOOTPRINT}
          AND c.age_nbr >= 60
          AND c.business_ln_cd IN ('CP', 'ME')
          AND DATE_TRUNC(c.srv_start_dt, MONTH)
              BETWEEN DATE '2024-01-01' AND DATE '2025-12-01'""")[0]

    assert keys["row_count"] == keys["distinct_keys"], (
        f"GATE FAILED (R2): key not unique at member x provider x date: {keys}")
    diff = abs(keys["row_count"] - recount["raw_visits"]) / max(recount["raw_visits"], 1)
    assert diff <= 0.01, (
        f"GATE FAILED (R1): table visits {keys['row_count']:,} vs raw recount "
        f"{recount['raw_visits']:,} differ by {diff:.4f} (over 1 percent)")
    assert keys["null_new_flag"] == 0, (
        f"GATE FAILED (R2): {keys['null_new_flag']} rows with null is_new_patient")
    assert float(keys["null_age_band_share"]) < 0.02, (
        f"GATE FAILED (R7): null age_band share "
        f"{float(keys['null_age_band_share']):.4f} is not below 2 percent")
    print(f"\nnull age_band rows (counted, allowed under 2 percent): "
          f"{keys['null_age_band']:,}")
    print("\nALL GATES PASSED (R2 key + flag, R1 recount, R7 age nulls)")


if __name__ == "__main__":
    main()
