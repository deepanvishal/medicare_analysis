"""
03 - Load CMS time & distance standards from the HSD workbook (per COUNTY).  [PYTHON / BigQuery load]

WHAT   : Unpivot the 'Provider Time & Distance' + 'Facility Time & Distance' tabs of
         ma_reference_file_12-17-2025.xlsx into ms_ref_time_distance. Each specialty
         is a Time/Distance column pair; each row is a county.
WHY    : These tabs are the AUTHORITATIVE, county-specific max time/distance (CMS
         relaxes the base 422.116 standard for specific counties). Grain is
         county x cms_specialty -- NOT county_type. (The old base-by-type copy from
         the FL SQL under-stated allowed distance and over-flagged NON-COMPLIANT.)
INPUT  : data/ma_reference_file_12-17-2025.xlsx  (tabs above; ST filtered to scope)
         data/min_ratio_per_1000.csv  (42 CFR 422.116 min-ratio; NOT in the xlsx --
                                        context column only, joined by county_type)
OUTPUT : ms_ref_time_distance
         cols: state_cd, county_name, county_type, cms_specialty,
               max_time_min, max_distance_miles, min_ratio_per_1000
         grain: state_cd x county_name x cms_specialty  (~11,696 rows)
NOTE   : Downstream (10_fact_zip_access / 11_fact_gap_analysis) must join on the
         BENEFICIARY county (state_cd + county_name), not county_type. The 6 Primary
         Care sub-components are blank/dropped; facility headers use (see Notes) variants.
"""

import pandas as pd
import config as cfg

XLSX_PATH = cfg.repo_path("data", "ma_reference_file_12-17-2025.xlsx")
MIN_RATIO_CSV = cfg.repo_path("data", "min_ratio_per_1000.csv")
COUNTY_TYPES = {"Large Metro", "Metro", "Micro", "Rural", "CEAC"}

# HSD Time&Distance header (normalized) -> cms_specialty (29 provider + 14 facility).
# Provider headers match the min-count tab; facility headers use (see Notes) variants.
TD_MAP = {
    "Primary Care (see Notes)": "Primary Care",
    "Allergy and Immunology": "Allergy and Immunology", "Cardiology": "Cardiology",
    "Chiropractor": "Chiropractor", "Dermatology": "Dermatology", "Endocrinology": "Endocrinology",
    "ENT/Otolaryngology": "ENT/Otolaryngology", "Gastroenterology": "Gastroenterology",
    "General Surgery": "General Surgery", "Gynecology, OB/GYN": "Gynecology OB/GYN",
    "Infectious Diseases": "Infectious Diseases", "Nephrology": "Nephrology", "Neurology": "Neurology",
    "Neurosurgery": "Neurosurgery", "Oncology - Medical, Surgical": "Oncology Medical/Surgical",
    "Oncology - Radiation/ Radiation Oncology": "Oncology Radiation", "Ophthalmology": "Ophthalmology",
    "Orthopedic Surgery": "Orthopedic Surgery",
    "Physiatry, Rehabilitative Medicine (see Notes)": "Physiatry Rehabilitative Med",
    "Plastic Surgery": "Plastic Surgery", "Podiatry": "Podiatry", "Psychiatry": "Psychiatry",
    "Pulmonology": "Pulmonology", "Rheumatology": "Rheumatology", "Urology": "Urology",
    "Vascular Surgery": "Vascular Surgery", "Cardiothoracic Surgery (see Notes)": "Cardiothoracic Surgery",
    "Clinical Psychology (see Notes)": "Clinical Psychology",
    "Clinical Social Work (see Notes)": "Clinical Social Work",
    # facility (Time & Distance tab header variants)
    "Acute Inpatient Hospitals (see Notes)": "Acute Inpatient Hospitals",
    "Cardiac Surgery Program (see Notes)": "Cardiac Surgery Program",
    "Cardiac Catheterization Services": "Cardiac Catheterization",
    "Critical Care Services/Intensive Care Units": "Critical Care ICU",
    "Surgical Services (Outpatient or ASC)": "Surgical Services ASC",
    "Skilled Nursing Facilities": "Skilled Nursing Facility",
    "Diagnostic Radiology": "Diagnostic Radiology", "Mammography": "Mammography",
    "Physical Therapy (See Notes)": "Physical Therapy",
    "Occupational Therapy (See Notes)": "Occupational Therapy",
    "Speech Therapy (See Notes)": "Speech Therapy",
    "Inpatient Psychiatric Facility Services": "Inpatient Psychiatric",
    "Outpatient Infusion/Chemotherapy (see Notes)": "Outpatient Infusion/Chemo",
    "Outpatient Behavioral Health (see Notes)": "Outpatient Behavioral Health",
}

TARGET_COLS = [
    "state_cd", "county_name", "county_type", "cms_specialty",
    "max_time_min", "max_distance_miles", "min_ratio_per_1000",
]


def _norm(header):
    return " ".join(str(header).split())


def _load_td_tab(sheet):
    """Unpivot one Time & Distance tab -> long (county x specialty)."""
    raw = pd.read_excel(XLSX_PATH, sheet_name=sheet, header=None)
    names = [_norm(x) for x in raw.iloc[1].tolist()]     # row 1 = specialty headers
    data = raw.iloc[4:].reset_index(drop=True)           # data from row 4
    county = data.iloc[:, 0].astype(str).str.strip()
    state = data.iloc[:, 1].astype(str).str.strip()
    ctype = data.iloc[:, 4].map(_norm)
    frames = []
    for c in range(5, raw.shape[1]):                     # specialty = Time col c, Distance col c+1
        cms = TD_MAP.get(names[c])
        if cms is None:
            continue
        frames.append(pd.DataFrame({
            "state_cd": state, "county_name": county, "county_type": ctype,
            "cms_specialty": cms,
            "max_time_min": pd.to_numeric(data.iloc[:, c], errors="coerce"),
            "max_distance_miles": pd.to_numeric(data.iloc[:, c + 1], errors="coerce"),
        }))
    out = pd.concat(frames, ignore_index=True)
    return out[out["state_cd"].isin(cfg.STATE_ABBRS)]


def build():
    """Return the per-county time/distance dataframe (no BQ / auth needed)."""
    td = pd.concat(
        [_load_td_tab("Provider Time & Distance"), _load_td_tab("Facility Time & Distance")],
        ignore_index=True,
    )
    mr = pd.read_csv(MIN_RATIO_CSV)                       # cms_specialty, county_type, min_ratio_per_1000
    td = td.merge(mr, on=["cms_specialty", "county_type"], how="left")
    td["max_time_min"] = td["max_time_min"].astype("Int64")
    td["max_distance_miles"] = td["max_distance_miles"].astype("Int64")
    td["min_ratio_per_1000"] = td["min_ratio_per_1000"].astype(float)
    return td[TARGET_COLS]


def validate(df):
    n_spec = df["cms_specialty"].nunique()
    ct = set(df["county_type"].unique())
    print(f"rows={len(df)}  specialties={n_spec}  county_types={sorted(ct)}")
    g = df.groupby("state_cd").agg(counties=("county_name", "nunique"),
                                   rows=("cms_specialty", "size"))
    print(g.to_string())
    gaps = int(df["max_distance_miles"].isna().sum())
    print(f"rows missing max_distance (data gaps): {gaps}")
    # per-county variation IS expected within a county_type
    metro_pc = df[(df.cms_specialty == "Primary Care") & (df.county_type == "Metro")]
    print("Primary Care/Metro distinct max_distance across counties:",
          sorted(metro_pc["max_distance_miles"].dropna().unique().tolist()))
    assert ct <= COUNTY_TYPES, f"unexpected county_types: {sorted(ct - COUNTY_TYPES)}"
    assert n_spec == 43, f"expected 43 specialties, got {n_spec}"
    print("VALIDATION OK")


def load_to_bq(df):
    from google.cloud import bigquery
    schema = [
        bigquery.SchemaField("state_cd", "STRING"),
        bigquery.SchemaField("county_name", "STRING"),
        bigquery.SchemaField("county_type", "STRING"),
        bigquery.SchemaField("cms_specialty", "STRING"),
        bigquery.SchemaField("max_time_min", "INT64"),
        bigquery.SchemaField("max_distance_miles", "INT64"),
        bigquery.SchemaField("min_ratio_per_1000", "FLOAT64"),
    ]
    table_id = cfg.table("ref_time_distance")
    job_config = bigquery.LoadJobConfig(schema=schema, write_disposition="WRITE_TRUNCATE")
    cfg.client().load_table_from_dataframe(df, table_id, job_config=job_config).result()
    print(f"Loaded {len(df)} rows -> {table_id}")


def main():
    df = build()
    validate(df)
    load_to_bq(df)


if __name__ == "__main__":
    main()
