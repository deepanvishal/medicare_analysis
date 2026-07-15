"""
40 - h1 mapping coverage   [PYTHON / read-only BigQuery query]

WHAT  : Coverage of the CMS-HCC V24 ICD mapping over 2024 claims (CP + ME):
        distinct dx codes, claim lines, allowed dollars, and members with at
        least one mapped claim. Mapped = joined mapping row has HCC_v24 IS
        NOT NULL; join on UPPER(REPLACE(pri_icd9_dx_cd, '.', '')) =
        UPPER(diagnosis_code).
GRAIN : one result set, 10 rows (metric_name, value); ratios rounded to 4
        decimals. No BigQuery table is created.
INPUTS: anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_2025_claims
        anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.HCC_ICD_Mapping_2025
OUTPUT: expanded_scope/dc_v2/01_hcc_chronic/h1_mapping_coverage_2024.csv
        (also printed to stdout)
"""

import os
import sys


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

CLAIMS = "anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_2025_claims"
MAP    = "anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.HCC_ICD_Mapping_2025"
OUT_CSV = cfg.repo_path("expanded_scope", "dc_v2", "01_hcc_chronic",
                        "h1_mapping_coverage_2024.csv")

SQL = f"""
WITH claims AS (
  SELECT
    UPPER(REPLACE(pri_icd9_dx_cd, '.', '')) AS dx_code,
    member_id,
    SAFE_CAST(allowed_amt AS FLOAT64)       AS allowed_amt
  FROM `{CLAIMS}`
  WHERE EXTRACT(YEAR FROM srv_start_dt) = 2024
    AND business_ln_cd IN ('CP', 'ME')
),
mapped_codes AS (
  SELECT DISTINCT UPPER(diagnosis_code) AS dx_code
  FROM `{MAP}`
  WHERE HCC_v24 IS NOT NULL
),
joined AS (
  SELECT c.dx_code, c.member_id, c.allowed_amt,
         m.dx_code IS NOT NULL AS mapped
  FROM claims c
  LEFT JOIN mapped_codes m ON c.dx_code = m.dx_code
),
agg AS (
  SELECT
    COUNT(DISTINCT dx_code)                          AS total_distinct_icds,
    COUNT(DISTINCT IF(mapped, dx_code, NULL))        AS mapped_distinct_icds,
    COUNT(*)                                         AS total_claim_lines,
    COUNTIF(mapped)                                  AS mapped_claim_lines,
    SUM(allowed_amt)                                 AS total_allowed_amt,
    SUM(IF(mapped, allowed_amt, 0))                  AS mapped_allowed_amt,
    COUNT(DISTINCT member_id)                        AS total_members,
    COUNT(DISTINCT IF(mapped, member_id, NULL))      AS mapped_members
  FROM joined
)
SELECT 'total_distinct_icds' AS metric_name, CAST(total_distinct_icds AS FLOAT64) AS value FROM agg
UNION ALL
SELECT 'mapped_distinct_icds', CAST(mapped_distinct_icds AS FLOAT64) FROM agg
UNION ALL
SELECT 'pct_distinct_icds_mapped', ROUND(SAFE_DIVIDE(mapped_distinct_icds, total_distinct_icds), 4) FROM agg
UNION ALL
SELECT 'total_claim_lines', CAST(total_claim_lines AS FLOAT64) FROM agg
UNION ALL
SELECT 'mapped_claim_lines', CAST(mapped_claim_lines AS FLOAT64) FROM agg
UNION ALL
SELECT 'pct_claim_lines_mapped', ROUND(SAFE_DIVIDE(mapped_claim_lines, total_claim_lines), 4) FROM agg
UNION ALL
SELECT 'total_allowed_amt', total_allowed_amt FROM agg
UNION ALL
SELECT 'mapped_allowed_amt', mapped_allowed_amt FROM agg
UNION ALL
SELECT 'pct_allowed_mapped', ROUND(SAFE_DIVIDE(mapped_allowed_amt, total_allowed_amt), 4) FROM agg
UNION ALL
SELECT 'pct_members_mapped', ROUND(SAFE_DIVIDE(mapped_members, total_members), 4) FROM agg
"""

METRIC_ORDER = [
    "total_distinct_icds", "mapped_distinct_icds", "pct_distinct_icds_mapped",
    "total_claim_lines", "mapped_claim_lines", "pct_claim_lines_mapped",
    "total_allowed_amt", "mapped_allowed_amt", "pct_allowed_mapped",
    "pct_members_mapped",
]


def main():
    client = cfg.client()
    df = client.query(SQL).result().to_dataframe()
    df["metric_name"] = df["metric_name"].astype(str)
    df = df.set_index("metric_name").loc[METRIC_ORDER].reset_index()
    df.to_csv(OUT_CSV, index=False)
    print(df.to_string(index=False))
    print(f"wrote {OUT_CSV}")


if __name__ == "__main__":
    main()
