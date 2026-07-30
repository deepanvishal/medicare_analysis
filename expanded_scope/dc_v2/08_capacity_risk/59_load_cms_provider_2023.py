"""
59 - load CMS by-Provider summary 2023   [PYTHON loader / BigQuery load]

WHAT  : Loads the CMS Medicare Physician & Other Practitioners "by Provider"
        SUMMARY file (CY2023) into BigQuery as cms_medicare_physician_ffs_2023.
        Provider-level annual rollup - one row per NPI, NOT NPI x HCPCS
        (DD 09 grain correction pending). CMS-side hours in modules 60-72
        therefore use average minutes per service, not code-level minutes.
        The table name intentionally reuses the id already referenced by
        expanded_scope/12_provider_par_flag.py and CLAUDE.md; that table does
        not currently exist in BigQuery - this load makes those references
        valid.
GRAIN : rndrng_npi (one row per provider; ~1,259,343 rows expected)
INPUTS: expanded_scope/dc_v2/08_capacity_risk/inputs/
        Medicare_Physician_Other_Practitioners_by_Provider_2023.csv
OUTPUT: anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.cms_medicare_physician_ffs_2023
        (WRITE_TRUNCATE) with sanity checks printed to stdout. No files written.
Run   : python expanded_scope/dc_v2/08_capacity_risk/59_load_cms_provider_2023.py
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

import pandas as pd

INPUT_FILE = cfg.repo_path(
    "expanded_scope", "dc_v2", "08_capacity_risk", "inputs",
    "Medicare_Physician_Other_Practitioners_by_Provider_2023.csv")

OUT_TABLE = cfg.src("cms_medicare_physician_ffs_2023")

EXPECTED_ROWS = 1_259_343   # published row count of the 2023 by-Provider file
ROW_TOLERANCE = 0.01        # stop if actual rows deviate more than 1%

# Compared against the CSV header after lowercasing (CMS vintages differ in
# case only; any other mismatch stops the run - no substitutes guessed).
KEEP_COLUMNS = [
    "rndrng_npi",                      # TODO VERIFY
    "rndrng_prvdr_ent_cd",             # TODO VERIFY  I = individual, O = organization
    "rndrng_prvdr_type",               # TODO VERIFY
    "rndrng_prvdr_state_abrvtn",       # TODO VERIFY
    "rndrng_prvdr_zip5",               # TODO VERIFY  kept STRING - leading zeros
    "rndrng_prvdr_mdcr_prtcptg_ind",   # TODO VERIFY
    "tot_srvcs",                       # TODO VERIFY
    "tot_benes",                       # TODO VERIFY
    "tot_med_srvcs",                   # TODO VERIFY
    "tot_drug_srvcs",                  # TODO VERIFY
    "tot_mdcr_pymt_amt",               # TODO VERIFY
    "bene_avg_age",                    # TODO VERIFY
    "bene_age_lt_65_cnt",              # TODO VERIFY
    "bene_age_65_74_cnt",              # TODO VERIFY
    "bene_age_75_84_cnt",              # TODO VERIFY
    "bene_age_gt_84_cnt",              # TODO VERIFY
    "bene_avg_risk_scre",              # TODO VERIFY
]
# plus every column starting with bene_cc_ (kept dynamically; names vary by vintage)

NUMERIC_BASE = [
    "tot_srvcs", "tot_benes", "tot_med_srvcs", "tot_drug_srvcs",
    "tot_mdcr_pymt_amt", "bene_avg_age", "bene_age_lt_65_cnt",
    "bene_age_65_74_cnt", "bene_age_75_84_cnt", "bene_age_gt_84_cnt",
    "bene_avg_risk_scre",
]

SANITY_CHECKS = {
    "row count":
        f"SELECT COUNT(*) AS n FROM `{OUT_TABLE}`",
    "pct ent_cd = 'I'":
        f"SELECT ROUND(COUNTIF(rndrng_prvdr_ent_cd = 'I') / COUNT(*), 4) AS pct_individual "
        f"FROM `{OUT_TABLE}`",
    "pct participation flag populated":
        f"SELECT ROUND(COUNTIF(TRIM(COALESCE(rndrng_prvdr_mdcr_prtcptg_ind, '')) != '') "
        f"/ COUNT(*), 4) AS pct_populated FROM `{OUT_TABLE}`",
    "NULL rate tot_med_srvcs":
        f"SELECT ROUND(COUNTIF(tot_med_srvcs IS NULL) / COUNT(*), 4) AS null_rate "
        f"FROM `{OUT_TABLE}`",
    "distinct rndrng_prvdr_type count":
        f"SELECT COUNT(DISTINCT rndrng_prvdr_type) AS n_types FROM `{OUT_TABLE}`",
    "sum(tot_med_srvcs) vs sum(tot_srvcs)":
        f"SELECT ROUND(SUM(tot_med_srvcs), 0) AS sum_tot_med_srvcs, "
        f"ROUND(SUM(tot_srvcs), 0) AS sum_tot_srvcs FROM `{OUT_TABLE}`",
}


def main():
    # first action: header row only - print it, stop if any KEEP column absent
    header_raw = list(pd.read_csv(INPUT_FILE, nrows=0).columns)
    print("CSV header row:")
    print(header_raw)
    header = [c.strip().lower() for c in header_raw]

    missing = [c for c in KEEP_COLUMNS if c not in header]
    if missing:
        raise SystemExit(
            "STOP - columns missing from CSV header, fix the TODO VERIFY entries "
            "in KEEP_COLUMNS (do not guess substitutes): " + ", ".join(missing))

    cc_cols = [c for c in header if c.startswith("bene_cc_")]
    print(f"bene_cc_ columns kept: {len(cc_cols)}")

    keep = KEEP_COLUMNS + cc_cols
    usecols = [header_raw[header.index(c)] for c in keep]
    df = pd.read_csv(INPUT_FILE, usecols=usecols, dtype=str)
    df.columns = [c.strip().lower() for c in df.columns]
    df = df[keep]

    n = len(df)
    if abs(n - EXPECTED_ROWS) / EXPECTED_ROWS > ROW_TOLERANCE:
        raise SystemExit(
            f"STOP - row count {n:,} outside {ROW_TOLERANCE:.0%} of expected "
            f"{EXPECTED_ROWS:,}; wrong file or truncated download")
    print(f"rows read: {n:,}")

    numeric_cols = NUMERIC_BASE + cc_cols
    for col in numeric_cols:
        s = df[col].str.strip()
        s = s.replace({"": None, "*": None, "#": None})   # CMS suppression -> NULL, never 0
        df[col] = pd.to_numeric(s, errors="coerce")

    for col in [c for c in keep if c not in numeric_cols]:
        df[col] = df[col].str.strip()

    df["load_ts"] = pd.Timestamp.now(tz="UTC")
    df["src_file"] = os.path.basename(INPUT_FILE)

    from google.cloud import bigquery
    client = cfg.client()
    job = client.load_table_from_dataframe(
        df, OUT_TABLE,
        job_config=bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE"))
    job.result()
    print(f"loaded {OUT_TABLE}")

    for label, q in SANITY_CHECKS.items():
        print(f"--- {label} ---")
        for row in client.query(q).result():
            print("  ", dict(row))


if __name__ == "__main__":
    main()
