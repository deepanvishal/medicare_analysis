"""
Smoke test: load CMS HSD reference XLSX content into a BigQuery table.

Mirrors the XLSX -> BigQuery pattern in Step1_load_hsd_to_bq.ipynb, but as a
small standalone script. Reads the 'Minimum Provider #s' sheet from
data/ma_reference_file_12-17-2025.xlsx (ALL states, no state filter) and loads
it to a throwaway test table with an explicit schema and WRITE_TRUNCATE.

Auth: requires `gcloud auth application-default login`
      (billing/auth project = anbc-dev-prv-nc-ds).

Run:  python test_load_xlsx_to_bq.py
"""

import pandas as pd
from google.cloud import bigquery

# --- config (matches CLAUDE.md / Step4_tab1_tab2.py) ---
PROJECT        = "anbc-hcb-dev"            # table project (where the table lives)
CLIENT_PROJECT = "anbc-dev-prv-nc-ds"      # billing/auth project (client runs here)
DATASET        = "provider_ds_netconf_data_hcb_dev"
PREFIX         = "A870800_medicare_supply_demand"

XLSX_PATH  = "data/ma_reference_file_12-17-2025.xlsx"
SHEET      = "Minimum Provider #s"
TEST_TABLE = f"{PREFIX}_test_hsd_xlsx_load"

STRING_COLS = {"COUNTY", "ST", "COUNTY_STATE", "SSACD", "COUNTY_DESIGNATION"}


def read_sheet(path, sheet):
    # row index 1 = headers; row 2 = specialty codes (skip); data from row 3+
    raw = pd.read_excel(path, sheet_name=sheet, header=None)
    raw.columns = raw.iloc[1].tolist()
    return raw.iloc[3:].reset_index(drop=True)


def clean_columns(df):
    df = df.copy()
    cols = (
        pd.Index(df.columns)
        .str.strip()
        .str.replace(r"\n", " ", regex=True)
        .str.replace(r"\s+", " ", regex=True)
        .str.replace(r"[^a-zA-Z0-9_ ]", "", regex=True)
        .str.strip()
        .str.replace(" ", "_")
        .str.upper()
    )
    # BigQuery column names cannot start with a digit (e.g. 95TH_PERCENTILE...)
    df.columns = [f"_{c}" if c[:1].isdigit() else c for c in cols]
    return df


def coerce_numeric(df):
    df = df.copy()
    for c in df.columns:
        if c not in STRING_COLS:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def bq_schema_from_df(df):
    type_map = {
        "object":  bigquery.enums.SqlTypeNames.STRING,
        "int64":   bigquery.enums.SqlTypeNames.INT64,
        "Int64":   bigquery.enums.SqlTypeNames.INT64,
        "float64": bigquery.enums.SqlTypeNames.FLOAT64,
        "bool":    bigquery.enums.SqlTypeNames.BOOL,
    }
    return [
        bigquery.SchemaField(col, type_map.get(str(dtype), bigquery.enums.SqlTypeNames.STRING))
        for col, dtype in df.dtypes.items()
    ]


def main():
    df = coerce_numeric(clean_columns(read_sheet(XLSX_PATH, SHEET)))
    print(f"Read {len(df)} rows x {len(df.columns)} cols from '{SHEET}'")
    print("States:", sorted(df["ST"].dropna().unique().tolist()))

    client = bigquery.Client(project=CLIENT_PROJECT)
    table_id = f"{PROJECT}.{DATASET}.{TEST_TABLE}"
    job_config = bigquery.LoadJobConfig(
        schema=bq_schema_from_df(df),
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    client.load_table_from_dataframe(df, table_id, job_config=job_config).result()
    print(f"Loaded {len(df)} rows -> {table_id}")

    n = next(iter(client.query(f"SELECT COUNT(*) AS c FROM `{table_id}`").result())).c
    print(f"Row count in BigQuery: {n}")


if __name__ == "__main__":
    main()
