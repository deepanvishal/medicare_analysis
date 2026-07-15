"""
40 - h1 mapping coverage   [PYTHON / read-only BigQuery query]

WHAT  : Coverage of the CMS-HCC V24 ICD mapping over 2024 claims (CP + ME):
        distinct dx codes, claim lines, allowed dollars, and members with at
        least one mapped claim. Mapped = joined mapping row has HCC_v24 IS
        NOT NULL; join on UPPER(REPLACE(TRIM(pri_icd9_dx_cd), '.', '')) =
        UPPER(TRIM(diagnosis_code)). Step 0 prints a join-format check
        (raw samples + hit rate) and gates on YES before the metrics run.
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

SQL_SAMPLE_CLAIMS = f"""
SELECT pri_icd9_dx_cd AS claims_dx_raw, COUNT(*) AS claim_lines
FROM `{CLAIMS}`
WHERE EXTRACT(YEAR FROM srv_start_dt) = 2024
  AND business_ln_cd IN ('CP', 'ME')
GROUP BY 1
ORDER BY claim_lines DESC
LIMIT 20
"""

SQL_SAMPLE_MAP = f"""
SELECT diagnosis_code AS map_dx_raw
FROM `{MAP}`
LIMIT 20
"""

SQL_HIT_RATE = f"""
WITH claims_codes AS (
  SELECT DISTINCT UPPER(REPLACE(TRIM(pri_icd9_dx_cd), '.', '')) AS dx_code
  FROM `{CLAIMS}`
  WHERE EXTRACT(YEAR FROM srv_start_dt) = 2024
    AND business_ln_cd IN ('CP', 'ME')
),
map_codes AS (
  SELECT DISTINCT UPPER(TRIM(diagnosis_code)) AS dx_code
  FROM `{MAP}`
)
SELECT
  COUNT(*)                                      AS claims_distinct_codes,
  COUNTIF(m.dx_code IS NOT NULL)                AS found_in_map,
  ROUND(SAFE_DIVIDE(COUNTIF(m.dx_code IS NOT NULL), COUNT(*)), 4) AS hit_rate
FROM claims_codes c
LEFT JOIN map_codes m ON c.dx_code = m.dx_code
"""

SQL = f"""
WITH claims AS (
  SELECT
    UPPER(REPLACE(TRIM(pri_icd9_dx_cd), '.', '')) AS dx_code,
    member_id,
    SAFE_CAST(allowed_amt AS FLOAT64)             AS allowed_amt
  FROM `{CLAIMS}`
  WHERE EXTRACT(YEAR FROM srv_start_dt) = 2024
    AND business_ln_cd IN ('CP', 'ME')
),
mapped_codes AS (
  SELECT DISTINCT UPPER(TRIM(diagnosis_code)) AS dx_code
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

    print("=" * 60)
    print("JOIN FORMAT CHECK")
    print("=" * 60)
    claims_sample = client.query(SQL_SAMPLE_CLAIMS).result().to_dataframe()
    map_sample = client.query(SQL_SAMPLE_MAP).result().to_dataframe()
    print(f"{'claims_dx_raw':<20}{'claim_lines':>12}    {'map_dx_raw':<20}")
    for i in range(20):
        c_code = str(claims_sample['claims_dx_raw'].iloc[i]) if i < len(claims_sample) else ""
        c_n = f"{int(claims_sample['claim_lines'].iloc[i]):,}" if i < len(claims_sample) else ""
        m_code = str(map_sample['map_dx_raw'].iloc[i]) if i < len(map_sample) else ""
        print(f"{c_code:<20}{c_n:>12}    {m_code:<20}")

    hit = client.query(SQL_HIT_RATE).result().to_dataframe()
    print()
    print(hit.to_string(index=False))
    print(f"hit_rate: {float(hit['hit_rate'].iloc[0]):.4f}")

    answer = input("Review the format check above. Type YES to continue to coverage "
                   "metrics, anything else to abort. ")
    if answer.strip() != "YES":
        print("aborted - no file written")
        return

    df = client.query(SQL).result().to_dataframe()
    df["metric_name"] = df["metric_name"].astype(str)
    df = df.set_index("metric_name").loc[METRIC_ORDER].reset_index()
    df.to_csv(OUT_CSV, index=False)
    print(df.to_string(index=False))
    print(f"wrote {OUT_CSV}")


if __name__ == "__main__":
    main()
