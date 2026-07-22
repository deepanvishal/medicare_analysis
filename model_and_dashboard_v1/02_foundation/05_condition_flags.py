"""
05 - condition flags   [PYTHON runner / BigQuery DDL]

WHAT  : Member x year x condition flags. One row exists when at least one
        claim in the year (2024 or 2025) maps to the condition; condition
        = HCC_v24 from HCC_ICD_Mapping_2025 after the ICD cleaning rule
        (UPPER(REPLACE(TRIM(code), '.', '')) both sides). Carries the
        mapping's description, a claim_count per row, and a chronic_label
        joined from the CCIR reference where a determination exists (null
        otherwise, counted in the sanity print). When a condition row's
        underlying dx codes carry mixed CCIR labels, the highest-priority
        determination wins: CHRONIC > NOT_CHRONIC > NO_DETERMINATION.
SCOPE : R6 restated: age_nbr >= 60; business_ln_cd IN ('CP','ME'); member
        county in FL/OH/AZ/IL via ms_ref_county with LPAD defense.
R3    : Attribution = none stored here. This is a member-level table;
        member county is NOT stored — joins to md1_member_base (03) supply
        geography, so demand-side attribution stays in one place.
GRAIN : member_id x year x HCC_v24.
INPUTS: anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_2025_claims
        anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.HCC_ICD_Mapping_2025
        cfg.table("dc_ref_ccir")
        cfg.table("ref_county")
OUTPUT: md1_condition_flags (BigQuery table).
Run   : python model_and_dashboard_v1/02_foundation/05_condition_flags.py
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
HCC    = "anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.HCC_ICD_Mapping_2025"
CCIR   = cfg.table("dc_ref_ccir")
CTY    = cfg.table("ref_county")
OUT    = cfg.src("md1_condition_flags")

FOOTPRINT = "('FL', 'OH', 'AZ', 'IL')"

DDL = f"""
CREATE OR REPLACE TABLE `{OUT}`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
WITH scoped AS (
  SELECT
    c.member_id,
    EXTRACT(YEAR FROM c.srv_start_dt) AS year,
    UPPER(REPLACE(TRIM(c.pri_icd9_dx_cd), '.', '')) AS dx_clean
  FROM `{CLAIMS}` c
  JOIN `{CTY}` rc
    ON LPAD(TRIM(CAST(c.mbr_county_cd AS STRING)), 5, '0') = rc.county_fips
  WHERE rc.state_cd IN {FOOTPRINT}
    AND c.age_nbr >= 60
    AND c.business_ln_cd IN ('CP', 'ME')
    AND EXTRACT(YEAR FROM c.srv_start_dt) IN (2024, 2025)
),
mapped AS (
  SELECT
    s.member_id,
    s.year,
    h.HCC_v24,
    h.description,
    r.chronic_label
  FROM scoped s
  JOIN `{HCC}` h
    ON s.dx_clean = UPPER(TRIM(h.diagnosis_code))
  LEFT JOIN `{CCIR}` r
    ON s.dx_clean = UPPER(TRIM(r.icd_code))
  WHERE h.HCC_v24 IS NOT NULL
)
SELECT
  member_id,
  year,
  HCC_v24,
  ANY_VALUE(description) AS description,
  COUNT(*) AS claim_count,
  CASE MAX(CASE chronic_label
             WHEN 'CHRONIC' THEN 3
             WHEN 'NOT_CHRONIC' THEN 2
             WHEN 'NO_DETERMINATION' THEN 1
             ELSE NULL END)
    WHEN 3 THEN 'CHRONIC'
    WHEN 2 THEN 'NOT_CHRONIC'
    WHEN 1 THEN 'NO_DETERMINATION'
    ELSE NULL END AS chronic_label
FROM mapped
GROUP BY member_id, year, HCC_v24
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

    keys = q(client, "row count, key uniqueness, null condition (R2)", f"""
        SELECT COUNT(*) AS row_count,
               COUNT(DISTINCT CONCAT(CAST(member_id AS STRING), '|',
                                     CAST(year AS STRING), '|',
                                     CAST(HCC_v24 AS STRING))) AS distinct_keys,
               COUNTIF(HCC_v24 IS NULL) AS null_condition,
               COUNTIF(chronic_label IS NULL) AS null_chronic_label
        FROM `{OUT}`""")[0]
    print(f"\nnull chronic_label rows (no CCIR determination, counted): "
          f"{keys['null_chronic_label']:,}")

    shares = q(client, "per year: non-null dx share and mapped share (R7)", f"""
        WITH scope_claims AS (
          SELECT
            EXTRACT(YEAR FROM c.srv_start_dt) AS year,
            UPPER(REPLACE(TRIM(c.pri_icd9_dx_cd), '.', '')) AS dx_clean
          FROM `{CLAIMS}` c
          JOIN `{CTY}` rc
            ON LPAD(TRIM(CAST(c.mbr_county_cd AS STRING)), 5, '0') = rc.county_fips
          WHERE rc.state_cd IN {FOOTPRINT}
            AND c.age_nbr >= 60
            AND c.business_ln_cd IN ('CP', 'ME')
            AND EXTRACT(YEAR FROM c.srv_start_dt) IN (2024, 2025)
        ),
        mapped_codes AS (
          SELECT DISTINCT UPPER(TRIM(diagnosis_code)) AS dx_clean
          FROM `{HCC}` WHERE HCC_v24 IS NOT NULL
        )
        SELECT
          s.year,
          COUNT(*) AS scope_claims,
          SAFE_DIVIDE(COUNTIF(s.dx_clean IS NOT NULL AND s.dx_clean != ''),
                      COUNT(*)) AS non_null_dx_share,
          SAFE_DIVIDE(COUNTIF(m.dx_clean IS NOT NULL), COUNT(*)) AS mapped_share
        FROM scope_claims s
        LEFT JOIN mapped_codes m ON s.dx_clean = m.dx_clean
        GROUP BY s.year ORDER BY s.year""")

    assert keys["null_condition"] == 0, (
        f"GATE FAILED (R2): {keys['null_condition']} rows with null condition")
    assert keys["row_count"] == keys["distinct_keys"], (
        f"GATE FAILED (R2): key not unique at member x year x condition: {keys}")
    for r in shares:
        assert float(r["non_null_dx_share"]) >= 0.90, (
            f"GATE FAILED (R7): year {r['year']} non-null cleaned dx share "
            f"{float(r['non_null_dx_share']):.4f} is below 90 percent")
    print("\nALL GATES PASSED (R2 key + condition, R7 dx coverage)")


if __name__ == "__main__":
    main()
