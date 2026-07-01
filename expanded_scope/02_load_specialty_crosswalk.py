"""
02 - Load the CMS<->Aetna specialty crosswalk (load once; reused by every report).  [PYTHON / BigQuery load]

WHAT   : Load cms_to_aetna_final (2).csv -> ms_ref_specialty_crosswalk_expanded.
         Maps each raw Aetna specialty code (aetna_code) to one of the 43 CMS
         specialties, with a human-readable description.
WHY    : State-agnostic reference. Loading it as a table means the specialty map is
         NOT rebuilt on every report run (replaces the hardcoded UNNEST in FL Step3).
INPUT  : cms_to_aetna_final (2).csv   (cms_specialty, aetna_code, aetna_description)
OUTPUT : ms_ref_specialty_crosswalk_expanded   grain: cms_specialty x aetna_code
NOTE   : All columns STRING. Explicit schema, WRITE_TRUNCATE. 09_stg_providers joins
         on aetna_code -> cms_specialty. Expected: ~327 rows, 43 distinct cms_specialty.
"""

import pandas as pd
import config as cfg

CSV_PATH = cfg.data_file("cms_to_aetna*.csv")
COLS = ["cms_specialty", "aetna_code", "aetna_description"]


def build():
    """Return the crosswalk dataframe (no BQ / auth needed)."""
    df = pd.read_csv(CSV_PATH, dtype=str, keep_default_na=False)[COLS].copy()
    for c in ["cms_specialty", "aetna_code"]:
        df[c] = df[c].astype(str).str.strip()          # join keys trimmed
    before = len(df)
    df = df.drop_duplicates().reset_index(drop=True)
    if len(df) != before:
        print(f"dropped {before - len(df)} exact-duplicate rows")
    return df


def validate(df):
    n_spec = df["cms_specialty"].nunique()
    print(f"rows={len(df)}  distinct_cms_specialty={n_spec}  distinct_aetna_code={df['aetna_code'].nunique()}")
    multi = df["aetna_code"].duplicated(keep=False).sum()
    print(f"aetna_code values shared across rows (info only): {multi}")
    assert n_spec == 43, f"expected 43 CMS specialties, got {n_spec}"
    assert (df["aetna_code"] != "").all(), "blank aetna_code present"
    assert (df["cms_specialty"] != "").all(), "blank cms_specialty present"
    print("VALIDATION OK")


def load_to_bq(df):
    from google.cloud import bigquery
    schema = [
        bigquery.SchemaField("cms_specialty", "STRING"),
        bigquery.SchemaField("aetna_code", "STRING"),
        bigquery.SchemaField("aetna_description", "STRING"),
    ]
    table_id = cfg.table("ref_specialty_crosswalk_expanded")
    job_config = bigquery.LoadJobConfig(schema=schema, write_disposition="WRITE_TRUNCATE")
    cfg.client().load_table_from_dataframe(df, table_id, job_config=job_config).result()
    print(f"Loaded {len(df)} rows -> {table_id}")


def main():
    df = build()
    validate(df)
    load_to_bq(df)


if __name__ == "__main__":
    main()
