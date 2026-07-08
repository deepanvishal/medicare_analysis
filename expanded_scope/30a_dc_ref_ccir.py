"""
30a - ms_dc_ref_ccir   [PYTHON / BigQuery load + read-only diagnostics]   *** DC PIPELINE -- INTERMEDIATE ***

WHAT : Loads the AHRQ Chronic Condition Indicator Refined (CCIR v2026.1) reference CSV
       into BigQuery, then runs read-only sanity checks: how many claims diagnosis
       codes match CCIR, and the distribution of the chronic indicator across claim
       lines and members.
WHY  : Coverage diagnostic ahead of the morbidity-axis decision (binary vs count).
       Run in a notebook and read the printed output; no pipeline table is altered.
INPUT : CCIR_v2026-1.csv -- place in the working directory before running.
        CSV has THREE preamble lines; real header on line 3; data from line 4.
        ICD codes are single-quoted and dot-free; indicator is 0 / 1 / 9.
OUTPUT: ms_dc_ref_ccir   grain: icd_code
Run   : python expanded_scope/30a_dc_ref_ccir.py
"""

import pandas as pd
import config as cfg

CSV_PATH = "CCIR_v2026-1.csv"
CLAIMS   = "anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_2025_claims"
CCIR     = cfg.table("dc_ref_ccir")


def load_ccir(client):
    from google.cloud import bigquery
    df = pd.read_csv(CSV_PATH, skiprows=3, header=None,
                     names=["icd_code", "icd_description", "chronic_indicator"], dtype=str)
    df["icd_code"] = df["icd_code"].str.strip().str.strip("'").str.replace(".", "", regex=False)
    df["icd_description"] = df["icd_description"].str.strip().str.strip("'\"")
    df["chronic_indicator"] = pd.to_numeric(df["chronic_indicator"].str.strip(),
                                            errors="coerce").astype("Int64")
    df = df.dropna(subset=["icd_code", "chronic_indicator"])
    df = df[df["icd_code"] != ""]
    df["chronic_label"] = df["chronic_indicator"].map(
        {0: "NOT_CHRONIC", 1: "CHRONIC", 9: "NO_DETERMINATION"})

    print(f"rows after clean: {len(df)}")
    print(df["chronic_label"].value_counts())
    print("expected ~75,725 codes; NOT_CHRONIC ~52,155 / CHRONIC ~12,955 / "
          "NO_DETERMINATION ~10,615")

    schema = [
        bigquery.SchemaField("icd_code", "STRING"),
        bigquery.SchemaField("icd_description", "STRING"),
        bigquery.SchemaField("chronic_indicator", "INT64"),
        bigquery.SchemaField("chronic_label", "STRING"),
    ]
    job_config = bigquery.LoadJobConfig(schema=schema, write_disposition="WRITE_TRUNCATE")
    client.load_table_from_dataframe(df, CCIR, job_config=job_config).result()
    table = client.get_table(CCIR)
    table.labels = {"owner": "deepan_thulasi_aetna_com"}
    client.update_table(table, ["labels"])
    print(f"loaded {len(df)} rows -> {CCIR}")


def run(client, label, sql):
    print(f"--- {label} ---")
    for row in client.query(sql).result():
        print("  ", dict(row))


D1 = f"""
WITH dx AS (
  SELECT DISTINCT REPLACE(pri_icd9_dx_cd, '.', '') AS code
  FROM `{CLAIMS}` WHERE pri_icd9_dx_cd IS NOT NULL
)
SELECT
  COUNT(*) AS distinct_dx_codes,
  COUNTIF(code IN (SELECT icd_code FROM `{CCIR}`)) AS matched_codes,
  ROUND(COUNTIF(code IN (SELECT icd_code FROM `{CCIR}`)) / COUNT(*), 3) AS distinct_match_rate
FROM dx
"""

D2 = f"""
SELECT
  COALESCE(r.chronic_label, 'NO_CCIR_MATCH') AS chronic_label,
  COUNT(*) AS claim_lines,
  COUNT(DISTINCT c.member_id) AS members
FROM `{CLAIMS}` c
LEFT JOIN `{CCIR}` r
  ON REPLACE(c.pri_icd9_dx_cd, '.', '') = r.icd_code
GROUP BY 1 ORDER BY claim_lines DESC
"""

D3 = f"""
WITH member_chronic AS (
  SELECT c.member_id,
         COUNT(DISTINCT CASE WHEN r.chronic_indicator = 1 THEN r.icd_code END) AS distinct_chronic_conditions
  FROM `{CLAIMS}` c
  LEFT JOIN `{CCIR}` r ON REPLACE(c.pri_icd9_dx_cd, '.', '') = r.icd_code
  GROUP BY c.member_id
)
SELECT distinct_chronic_conditions, COUNT(*) AS members
FROM member_chronic
GROUP BY 1 ORDER BY distinct_chronic_conditions
"""


def main():
    client = cfg.client()
    load_ccir(client)
    run(client, "D1 - distinct-code match rate (breadth of coverage)", D1)
    run(client, "D2 - claim-line weighted coverage + chronic breakdown (the real signal)", D2)
    run(client, "D3 - member-level chronic-condition distribution (binary vs count axis)", D3)
    print("D3 shows the shape for the morbidity axis decision: binary (0 vs 1+) or count "
          "(banded). Decision made after review, not in this script.")


if __name__ == "__main__":
    main()
