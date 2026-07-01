"""
01 - Load CMS HSD reference workbook -> multi-state ms_ref_hsd_required_counts.  [PYTHON / BigQuery load]

WHAT   : Read data/ma_reference_file_12-17-2025.xlsx (wide 'Minimum Provider #s' +
         'Minimum Facility #s' sheets), keep scope states, unpivot the specialty
         columns to the long grain, and load ms_ref_hsd_required_counts.
WHY    : Replaces the 707 KB hand-typed UNNEST in the FL Step2. required_count is
         CMS-precalculated -> loaded AS-IS from the sheet, never recalculated.
INPUT  : data/ma_reference_file_12-17-2025.xlsx   (national; ST filtered to scope)
OUTPUT : ms_ref_hsd_required_counts
         cols: county_name, state_cd, county_type, total_beneficiaries,
               ratio_95th_percentile, beneficiaries_required_to_cover,
               cms_specialty, required_count      (grain: county x cms_specialty)
NOTE   : 29 provider + 14 facility columns -> 43 cms_specialty (the 6 Primary-Care
         sub-components are dropped). Schema matches the FL table. Explicit BQ
         schema, WRITE_TRUNCATE. Validated: FL reproduces 2,881 rows / 43 specialties
         (e.g. Alachua Acute Inpatient beds = 68, facility minimums = 1).
         Downstream joins key on state_cd + county_name (unique within a state);
         county_fips is attached where the facts are built.
"""

import pandas as pd
import config as cfg

XLSX_PATH = cfg.repo_path("data", "ma_reference_file_12-17-2025.xlsx")

# id columns:  normalized HSD header -> target column
ID_COLS = {
    "COUNTY": "county_name",
    "ST": "state_cd",
    "COUNTY DESIGNATION": "county_type",
    "TOTAL BENEFICIARIES": "total_beneficiaries",
    "95th PERCENTILE BASE POPULATION RATIO": "ratio_95th_percentile",
    "BENEFICIARIES REQUIRED TO COVER": "beneficiaries_required_to_cover",
}

# HSD provider-sheet header (normalized) -> cms_specialty  (29; PC sub-components excluded)
PROVIDER_MAP = {
    "Primary Care (see Notes)": "Primary Care",
    "Allergy and Immunology": "Allergy and Immunology",
    "Cardiology": "Cardiology",
    "Chiropractor": "Chiropractor",
    "Dermatology": "Dermatology",
    "Endocrinology": "Endocrinology",
    "ENT/Otolaryngology": "ENT/Otolaryngology",
    "Gastroenterology": "Gastroenterology",
    "General Surgery": "General Surgery",
    "Gynecology, OB/GYN": "Gynecology OB/GYN",
    "Infectious Diseases": "Infectious Diseases",
    "Nephrology": "Nephrology",
    "Neurology": "Neurology",
    "Neurosurgery": "Neurosurgery",
    "Oncology - Medical, Surgical": "Oncology Medical/Surgical",
    "Oncology - Radiation/ Radiation Oncology": "Oncology Radiation",
    "Ophthalmology": "Ophthalmology",
    "Orthopedic Surgery": "Orthopedic Surgery",
    "Physiatry, Rehabilitative Medicine (see Notes)": "Physiatry Rehabilitative Med",
    "Plastic Surgery": "Plastic Surgery",
    "Podiatry": "Podiatry",
    "Psychiatry": "Psychiatry",
    "Pulmonology": "Pulmonology",
    "Rheumatology": "Rheumatology",
    "Urology": "Urology",
    "Vascular Surgery": "Vascular Surgery",
    "Cardiothoracic Surgery (see Notes)": "Cardiothoracic Surgery",
    "Clinical Psychology (see Notes)": "Clinical Psychology",
    "Clinical Social Work (see Notes)": "Clinical Social Work",
}

# HSD facility-sheet header (normalized) -> cms_specialty  (14, incl. Acute Inpatient beds)
FACILITY_MAP = {
    "Acute Inpatient Hospital Beds": "Acute Inpatient Hospitals",
    "Cardiac Surgery Program": "Cardiac Surgery Program",
    "Cardiac Catheterization Services": "Cardiac Catheterization",
    "Critical Care Services/Intensive Care Units": "Critical Care ICU",
    "Surgical Services (Outpatient or ASC)": "Surgical Services ASC",
    "Skilled Nursing Facilities": "Skilled Nursing Facility",
    "Diagnostic Radiology": "Diagnostic Radiology",
    "Mammography": "Mammography",
    "Physical Therapy (See Notes)": "Physical Therapy",
    "Occupational Therapy (See Notes)": "Occupational Therapy",
    "Speech Therapy (See Notes)": "Speech Therapy",
    "Inpatient Psychiatric Facility Services": "Inpatient Psychiatric",
    "Outpatient Infusion/Chemotherapy": "Outpatient Infusion/Chemo",
    "Outpatient Behavioral Health (see Notes)": "Outpatient Behavioral Health",
}

TARGET_COLS = [
    "county_name", "state_cd", "county_type", "total_beneficiaries",
    "ratio_95th_percentile", "beneficiaries_required_to_cover",
    "cms_specialty", "required_count",
]


def _norm(header):
    """Collapse whitespace (incl. embedded newlines) and strip -- stable header key."""
    return " ".join(str(header).split())


def _load_sheet_long(path, sheet, spec_map):
    raw = pd.read_excel(path, sheet_name=sheet, header=None)
    raw.columns = [_norm(c) for c in raw.iloc[1].tolist()]   # row 1 = headers
    df = raw.iloc[3:].reset_index(drop=True)                  # data from row 3
    df = df[df["ST"].isin(cfg.STATE_ABBRS)].copy()            # scope states only

    spec_cols = [c for c in df.columns if c in spec_map]
    long = df.melt(
        id_vars=list(ID_COLS.keys()),
        value_vars=spec_cols,
        var_name="hsd_specialty",
        value_name="required_count",
    )
    long["cms_specialty"] = long["hsd_specialty"].map(spec_map)
    long = long.rename(columns=ID_COLS)
    long["county_name"] = long["county_name"].astype(str).str.strip()
    return long[TARGET_COLS]


def build():
    """Return the long ms_ref_hsd_required_counts dataframe (no BQ / auth needed)."""
    prov = _load_sheet_long(XLSX_PATH, "Minimum Provider #s", PROVIDER_MAP)
    fac = _load_sheet_long(XLSX_PATH, "Minimum Facility #s", FACILITY_MAP)
    df = pd.concat([prov, fac], ignore_index=True)

    df["total_beneficiaries"] = pd.to_numeric(df["total_beneficiaries"], errors="coerce").astype("Int64")
    df["ratio_95th_percentile"] = pd.to_numeric(df["ratio_95th_percentile"], errors="coerce").astype(float)
    df["beneficiaries_required_to_cover"] = pd.to_numeric(df["beneficiaries_required_to_cover"], errors="coerce").astype("Int64")
    df["required_count"] = pd.to_numeric(df["required_count"], errors="coerce").astype("Int64")
    return df[TARGET_COLS]


def validate(df):
    n_spec = df["cms_specialty"].nunique()
    print(f"rows={len(df)}  specialties={n_spec}  states={sorted(df['state_cd'].dropna().unique())}")
    g = df.groupby("state_cd").agg(
        counties=("county_name", "nunique"),
        specialties=("cms_specialty", "nunique"),
        rows=("required_count", "size"),
    )
    print(g.to_string())
    assert df["cms_specialty"].isna().sum() == 0, "unmapped specialty headers present"
    fl_rows = int(g.loc["FL", "rows"]) if "FL" in g.index else 0
    assert fl_rows == 2881, f"FL expected 2,881 rows, got {fl_rows}"
    assert n_spec == 43, f"expected 43 specialties, got {n_spec}"
    print("VALIDATION OK")


def load_to_bq(df):
    from google.cloud import bigquery
    schema = [
        bigquery.SchemaField("county_name", "STRING"),
        bigquery.SchemaField("state_cd", "STRING"),
        bigquery.SchemaField("county_type", "STRING"),
        bigquery.SchemaField("total_beneficiaries", "INT64"),
        bigquery.SchemaField("ratio_95th_percentile", "FLOAT64"),
        bigquery.SchemaField("beneficiaries_required_to_cover", "INT64"),
        bigquery.SchemaField("cms_specialty", "STRING"),
        bigquery.SchemaField("required_count", "INT64"),
    ]
    table_id = cfg.table("ref_hsd_required_counts")
    job_config = bigquery.LoadJobConfig(schema=schema, write_disposition="WRITE_TRUNCATE")
    cfg.client().load_table_from_dataframe(df, table_id, job_config=job_config).result()
    print(f"Loaded {len(df)} rows -> {table_id}")


def main():
    df = build()
    validate(df)
    load_to_bq(df)


if __name__ == "__main__":
    main()
