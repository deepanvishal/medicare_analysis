"""
60 - load MPFS work time file + seed segments   [PYTHON loader / BigQuery load]

WHAT  : Loads the CMS MPFS physician work time file (CMS-1807-F, CY2025) into
        ref_mpfs_time and seeds the 8-row ref_segment reference. Keeps
        blank-modifier rows only - the internal claims source has no modifier
        column (methodology limitation 11), so modifier rows in the time file
        are excluded at load. Then prints the module-60 GATE: internal claims
        prcdr_cd match rate against ref_mpfs_time. Deepan reviews the gate
        report before module 61 runs. No CMS-side match query - the CMS
        by-Provider table has no procedure detail.
GRAIN : ref_mpfs_time -> hcpcs_cd (one row per code)
        ref_segment   -> segment_cd (exactly 8 rows)
INPUTS: expanded_scope/dc_v2/08_capacity_risk/inputs/
        CMS-1807-F_Work_Time_16OCT24.xlsx (tab 'Work Time')
        anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_2025_claims
OUTPUT: ref_mpfs_time + ref_segment (BigQuery, WRITE_TRUNCATE) with the
        match-rate gate report and sanity checks printed to stdout.
Run   : python expanded_scope/dc_v2/08_capacity_risk/60_load_time_file.py
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
    "CMS-1807-F_Work_Time_16OCT24.xlsx")
SHEET = "Work Time"

MPFS_TABLE = cfg.table("ref_mpfs_time")
SEG_TABLE  = cfg.table("ref_segment")
CLAIMS     = "anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_2025_claims"

MPFS_CY = 2025

# Source column names compared against the printed header after strip+lower
# (case/spacing differences alone will not trip the guard; real absences stop
# the run). Every entry TODO VERIFY against the printed header row.
COLUMN_MAP = {
    "hcpcs_cd":   "HCPCS",                              # TODO VERIFY
    "modifier":   "MOD",                                # TODO VERIFY
    "pre_mins":   ["PRE-SRVC EVAL TIME",                # TODO VERIFY
                   "PRE-SRVC POSITIONING TIME",         # TODO VERIFY
                   "PRE-SRVC SCRUB, DRESS, WAIT TIME"], # TODO VERIFY
    "intra_mins": "INTRA-SRVC TIME",                    # TODO VERIFY
    "post_mins":  ["IMMED POST-SRVC TIME"],             # TODO VERIFY
}

SEGMENT_ROWS = [
    ("NEW_CHR_60_74",    1, 1, "60_74", "New chronic 60-74"),
    ("NEW_CHR_75P",      1, 1, "75P",   "New chronic 75+"),
    ("NEW_NONCHR_60_74", 1, 0, "60_74", "New non-chronic 60-74"),
    ("NEW_NONCHR_75P",   1, 0, "75P",   "New non-chronic 75+"),
    ("RET_CHR_60_74",    0, 1, "60_74", "Returning chronic 60-74"),
    ("RET_CHR_75P",      0, 1, "75P",   "Returning chronic 75+"),
    ("RET_NONCHR_60_74", 0, 0, "60_74", "Returning non-chronic 60-74"),
    ("RET_NONCHR_75P",   0, 0, "75P",   "Returning non-chronic 75+"),
]

# One query, three sections: A_OVERALL (one row), B_BY_SPECIALTY (worst match
# pct first), C_TOP_UNMATCHED (top 25 unmatched codes by line count).
MATCH_RATE_QUERY = f"""
WITH claims AS (
  SELECT
    UPPER(TRIM(CAST(prcdr_cd AS STRING))) AS code,
    specialty_ctg_cd
  FROM `{CLAIMS}`
),
joined AS (
  SELECT c.code, c.specialty_ctg_cd, r.hcpcs_cd IS NOT NULL AS matched
  FROM claims c
  LEFT JOIN `{MPFS_TABLE}` r ON c.code = r.hcpcs_cd
),
overall AS (
  SELECT
    'A_OVERALL' AS section, CAST(NULL AS STRING) AS label,
    COUNT(*) AS line_cnt, COUNTIF(matched) AS matched_cnt,
    ROUND(COUNTIF(matched) / COUNT(*), 4) AS match_pct
  FROM joined
),
by_spec AS (
  SELECT
    'B_BY_SPECIALTY' AS section, specialty_ctg_cd AS label,
    COUNT(*) AS line_cnt, COUNTIF(matched) AS matched_cnt,
    ROUND(COUNTIF(matched) / COUNT(*), 4) AS match_pct
  FROM joined
  GROUP BY specialty_ctg_cd
),
unmatched AS (
  SELECT
    'C_TOP_UNMATCHED' AS section, COALESCE(code, '(NULL)') AS label,
    COUNT(*) AS line_cnt, 0 AS matched_cnt,
    CAST(NULL AS FLOAT64) AS match_pct
  FROM joined
  WHERE NOT matched
  GROUP BY code
  ORDER BY COUNT(*) DESC
  LIMIT 25
)
SELECT * FROM overall
UNION ALL SELECT * FROM by_spec
UNION ALL SELECT * FROM unmatched
ORDER BY section, COALESCE(match_pct, 1), line_cnt DESC
"""

SANITY_CHECKS = {
    "ref_mpfs_time row count":
        f"SELECT COUNT(*) AS n FROM `{MPFS_TABLE}`",
    "pct rows intra_mins > 0":
        f"SELECT ROUND(COUNTIF(intra_mins > 0) / COUNT(*), 4) AS pct "
        f"FROM `{MPFS_TABLE}`",
    "code_class_cd distribution":
        f"SELECT code_class_cd, COUNT(*) AS n FROM `{MPFS_TABLE}` "
        f"GROUP BY code_class_cd ORDER BY n DESC",
    "ref_segment row count (must be 8)":
        f"SELECT COUNT(*) AS n FROM `{SEG_TABLE}`",
}


def _safe_numeric(s):
    s = s.str.strip().replace({"": None})
    return pd.to_numeric(s, errors="coerce")


def _code_class(hcpcs):
    if hcpcs.startswith("99"):
        return "EM"
    if hcpcs.isdigit() and 10021 <= int(hcpcs) <= 69990:
        return "PROC"
    return "OTHER"


def main():
    # first action: read the tab, print its header row, stop if any mapped
    # source column is absent
    raw = pd.read_excel(INPUT_FILE, sheet_name=SHEET, dtype=str)
    header_raw = [str(c).strip() for c in raw.columns]
    print(f"'{SHEET}' header row:")
    print(header_raw)

    lookup = {c.lower(): c for c in header_raw}
    wanted = []
    for tgt, src in COLUMN_MAP.items():
        wanted += src if isinstance(src, list) else [src]
    missing = [c for c in wanted if c.lower() not in lookup]
    if missing:
        raise SystemExit(
            "STOP - COLUMN_MAP source columns absent from the header above, fix "
            "the TODO VERIFY entries: " + ", ".join(missing))

    raw.columns = header_raw

    def col(name):
        return raw[lookup[name.lower()]]

    hcpcs = col(COLUMN_MAP["hcpcs_cd"]).astype(str).str.strip().str.upper()
    modifier = col(COLUMN_MAP["modifier"]).astype(str).str.strip()
    blank_mod = modifier.isin(["", "nan", "None"]) | modifier.isna()

    all_codes = set(hcpcs)
    kept_codes = set(hcpcs[blank_mod])
    excluded_codes = all_codes - kept_codes
    print(f"hcpcs codes appearing ONLY with a modifier value (excluded): "
          f"{len(excluded_codes)}")

    df = pd.DataFrame({"hcpcs_cd": hcpcs})[blank_mod.values].copy()
    pre_cols = [_safe_numeric(col(c)[blank_mod.values]) for c in COLUMN_MAP["pre_mins"]]
    post_cols = [_safe_numeric(col(c)[blank_mod.values]) for c in COLUMN_MAP["post_mins"]]
    df["intra_mins"] = _safe_numeric(col(COLUMN_MAP["intra_mins"])[blank_mod.values])
    df["pre_mins"] = pd.concat(pre_cols, axis=1).sum(axis=1, min_count=1)
    df["post_mins"] = pd.concat(post_cols, axis=1).sum(axis=1, min_count=1)
    df["code_class_cd"] = df["hcpcs_cd"].map(_code_class)
    df["code_family_cd"] = df["hcpcs_cd"].str[:3]
    df["mpfs_cy"] = MPFS_CY
    df = df[["hcpcs_cd", "intra_mins", "pre_mins", "post_mins",
             "code_class_cd", "code_family_cd", "mpfs_cy"]]

    seg = pd.DataFrame(
        SEGMENT_ROWS,
        columns=["segment_cd", "new_flag", "chronic_flag", "age_band_cd", "segment_nm"])

    from google.cloud import bigquery
    client = cfg.client()
    truncate = bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE")
    client.load_table_from_dataframe(df, MPFS_TABLE, job_config=truncate).result()
    print(f"loaded {MPFS_TABLE}")
    client.load_table_from_dataframe(seg, SEG_TABLE, job_config=truncate).result()
    print(f"loaded {SEG_TABLE}")

    print("--- MODULE 60 GATE: internal claims match rate ---")
    for row in client.query(MATCH_RATE_QUERY).result():
        print("  ", dict(row))

    for label, q in SANITY_CHECKS.items():
        print(f"--- {label} ---")
        for row in client.query(q).result():
            print("  ", dict(row))

    n_seg = list(client.query(
        f"SELECT COUNT(*) AS n FROM `{SEG_TABLE}`").result())[0][0]
    if n_seg != 8:
        raise SystemExit(f"GATE FAILED -- ref_segment row count = {n_seg}, must be 8")


if __name__ == "__main__":
    main()
