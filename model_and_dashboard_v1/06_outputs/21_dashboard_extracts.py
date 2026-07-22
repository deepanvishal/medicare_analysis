"""
21 - dashboard extracts   [PYTHON / BigQuery read -> parquet files]

WHAT  : Writes the eight parquet extracts the dashboards load, plus
        manifest.json, to model_and_dashboard_v1/07_dashboard/extracts/
        (folder created if absent). The dashboards read ONLY these
        files. Extracts: enrollment (2025-12 snapshot per county x band
        with state and county_name from ms_ref_county, R7-gated),
        growth_context (last-year yoy per county x band - CONTEXT LABEL
        ONLY, slider defaults are 0 per D11), sickness_rates (2025,
        tenure ALL, non-null prevalence only), visit_rates
        (md1_visit_rates plus per-specialty deflation_factor joined
        from md1_visitsplit_rates for the v2 fit-quality view),
        county_calibration (shipped factor per county x specialty),
        providers (md1_capacity_v0 all columns - capacity is v0
        observed-peak per D14), conditions_meta (distinct condition +
        description), other_condition_prevalence (county x band share
        of 2025 tenure-ALL members holding at least one condition
        outside the kept set - closes the OTHER_CONDITION baseline gap
        in the dashboard cascade; R2-gated 0..1, key unique).
        Manifest carries row counts, source tables, build timestamp,
        and the line "capacity=v0 observed-peak; demand=calibrated to
        2025 actuals".
SCOPE : R6 inherited from the source tables; the enrollment extract
        re-applies the footprint state filter.
R3    : Mixed by design and stated per extract: enrollment, growth,
        sickness and calibration are MEMBER-county tables (demand
        side); providers is a PROVIDER-county table (capacity side).
        The dashboard never mixes the two axes in one join.
GRAIN : One parquet per extract; grains listed in the extract table of
        03_DELIVERABLE_DASH.md.
INPUTS: md1_enrollment_history (06), md1_growth_defaults (11),
        md1_sickness_rates (07), md1_visit_rates and
        md1_county_calibration (08), md1_visitsplit_rates (14,
        deflation), md1_capacity_v0 (16 v0), md1_condition_flags and
        md1_member_base (batch A2), cfg.table("ref_county")
OUTPUT: eight .parquet files + manifest.json under
        model_and_dashboard_v1/07_dashboard/extracts/ (no BigQuery
        writes).
Run   : python model_and_dashboard_v1/06_outputs/21_dashboard_extracts.py
        Requires pandas + pyarrow locally. Run after 06, 07, 08, 11 and
        16 v0.
"""

import json
import os
import sys
from datetime import datetime


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

ENR    = cfg.src("md1_enrollment_history")
GROW   = cfg.src("md1_growth_defaults")
SICK   = cfg.src("md1_sickness_rates")
RATES  = cfg.src("md1_visit_rates")
VSPLIT = cfg.src("md1_visitsplit_rates")
CAL    = cfg.src("md1_county_calibration")
CAP    = cfg.src("md1_capacity_v0")
CFLAGS = cfg.src("md1_condition_flags")
MBASE  = cfg.src("md1_member_base")
CTY    = cfg.table("ref_county")

FOOTPRINT = "('FL', 'OH', 'AZ', 'IL')"

EXTRACT_DIR = os.path.join(os.path.dirname(_expanded_scope_dir()),
                           "model_and_dashboard_v1", "07_dashboard",
                           "extracts")

MANIFEST_NOTE = "capacity=v0 observed-peak; demand=calibrated to 2025 actuals"

EXTRACTS = [
    ("enrollment.parquet", "md1_enrollment_history", f"""
        SELECT e.mbr_county_cd, e.state_cd, e.age_band, e.members,
               rc.county_name
        FROM `{ENR}` e
        JOIN `{CTY}` rc
          ON LPAD(TRIM(CAST(e.mbr_county_cd AS STRING)), 5, '0')
             = rc.county_fips
        WHERE e.month = DATE '2025-12-01'
          AND e.state_cd IN {FOOTPRINT}
        ORDER BY e.mbr_county_cd, e.age_band"""),
    # growth_context is a context label only: slider defaults are 0 per
    # D11; last_year_yoy_pct feeds the "last year: +X%" label, never the
    # slider position.
    ("growth_context.parquet", "md1_growth_defaults", f"""
        SELECT mbr_county_cd, state_cd, age_band,
               cell_yoy AS last_year_yoy_pct
        FROM `{GROW}`
        ORDER BY mbr_county_cd, age_band"""),
    ("sickness_rates.parquet", "md1_sickness_rates", f"""
        SELECT mbr_county_cd, age_band,
               CAST(HCC_v24 AS STRING) AS condition,
               description, chronic_label, members,
               members_with_condition, prevalence
        FROM `{SICK}`
        WHERE year = 2025
          AND tenure_group = 'ALL'
          AND prevalence IS NOT NULL
        ORDER BY mbr_county_cd, age_band, condition"""),
    ("visit_rates.parquet", "md1_visit_rates", f"""
        SELECT r.*, d.deflation_factor
        FROM `{RATES}` r
        JOIN (SELECT cms_specialty,
                     ANY_VALUE(deflation_factor) AS deflation_factor
              FROM `{VSPLIT}`
              GROUP BY cms_specialty) d
          ON r.cms_specialty = d.cms_specialty
        ORDER BY r.cms_specialty, r.condition"""),
    ("county_calibration.parquet", "md1_county_calibration", f"""
        SELECT mbr_county_cd, state_cd, cms_specialty, calibration_factor
        FROM `{CAL}`
        ORDER BY mbr_county_cd, cms_specialty"""),
    ("providers.parquet", "md1_capacity_v0", f"""
        SELECT * FROM `{CAP}`
        ORDER BY epdb_dw_prvdr_id"""),
    ("conditions_meta.parquet", "md1_sickness_rates", f"""
        SELECT CAST(HCC_v24 AS STRING) AS condition,
               ANY_VALUE(description) AS description
        FROM `{SICK}`
        GROUP BY condition
        ORDER BY condition"""),
    ("other_condition_prevalence.parquet",
     "md1_condition_flags + md1_member_base + md1_visit_rates", f"""
        WITH kept AS (
          SELECT DISTINCT condition
          FROM `{RATES}`
          WHERE condition NOT IN ('BASE_RATE', 'OTHER_CONDITION')
        ),
        member_year AS (
          SELECT b.member_id, b.mbr_county_cd, b.age_band
          FROM `{MBASE}` b
          JOIN `{CTY}` rc
            ON LPAD(TRIM(CAST(b.mbr_county_cd AS STRING)), 5, '0')
               = rc.county_fips
          WHERE rc.state_cd IN {FOOTPRINT}
            AND b.age_band IS NOT NULL
            AND EXTRACT(YEAR FROM b.month) = 2025
          QUALIFY ROW_NUMBER() OVER (PARTITION BY b.member_id
                                     ORDER BY b.month DESC) = 1
        ),
        other_members AS (
          SELECT DISTINCT f.member_id
          FROM `{CFLAGS}` f
          WHERE f.year = 2025
            AND CAST(f.HCC_v24 AS STRING) NOT IN
                (SELECT condition FROM kept)
        )
        SELECT my.mbr_county_cd, my.age_band,
               COUNT(*) AS members,
               COUNTIF(o.member_id IS NOT NULL) AS members_with_other,
               SAFE_DIVIDE(COUNTIF(o.member_id IS NOT NULL), COUNT(*))
                 AS other_prevalence
        FROM member_year my
        LEFT JOIN other_members o ON my.member_id = o.member_id
        GROUP BY my.mbr_county_cd, my.age_band
        ORDER BY my.mbr_county_cd, my.age_band"""),
]


def main():
    print("NOTE: extracts must be REGENERATED after any pipeline change - "
          "the dashboards read only what this script last wrote. v2 needs "
          "the eight-file set (county_name, deflation_factor and "
          "other_condition_prevalence included).")
    client = cfg.client()
    os.makedirs(EXTRACT_DIR, exist_ok=True)

    manifest = {
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "note": MANIFEST_NOTE,
        "extracts": {},
    }
    frames = {}
    for filename, source, sql in EXTRACTS:
        df = client.query(sql).to_dataframe()
        assert len(df) > 0, (
            f"GATE FAILED (R1): extract {filename} from {source} is empty")
        path = os.path.join(EXTRACT_DIR, filename)
        df.to_parquet(path, index=False)
        frames[filename] = df
        manifest["extracts"][filename] = {"row_count": len(df),
                                          "source": source}
        print(f"wrote {filename}: {len(df):,} rows  ({source})")

    county_check = [dict(r) for r in client.query(f"""
        SELECT COUNT(DISTINCT mbr_county_cd) AS county_count
        FROM `{ENR}`
        WHERE month = DATE '2025-12-01'
          AND state_cd IN {FOOTPRINT}""").result()][0]
    extract_counties = frames["enrollment.parquet"]["mbr_county_cd"].nunique()
    assert extract_counties == county_check["county_count"], (
        f"GATE FAILED (R4): enrollment extract has {extract_counties} "
        f"counties vs {county_check['county_count']} in "
        f"md1_enrollment_history 2025-12")

    edf = frames["enrollment.parquet"]
    unnamed = edf["county_name"].isna().sum() + \
        (edf["county_name"].astype(str).str.strip() == "").sum()
    assert unnamed == 0, (
        f"GATE FAILED (R7): {unnamed} enrollment rows with no county_name "
        f"after the ms_ref_county join")

    odf = frames["other_condition_prevalence.parquet"]
    other_keys = odf[["mbr_county_cd", "age_band"]].drop_duplicates()
    assert len(other_keys) == len(odf), (
        f"GATE FAILED (R2): other_condition_prevalence key not unique at "
        f"county x band: {len(odf)} rows, {len(other_keys)} keys")
    bad_prev = ((odf["other_prevalence"] < 0)
                | (odf["other_prevalence"] > 1)).sum()
    assert bad_prev == 0, (
        f"GATE FAILED (R2): {bad_prev} other_prevalence values outside 0..1")

    manifest_path = os.path.join(EXTRACT_DIR, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    print(f"\nwrote manifest.json: {manifest_path}")
    print(f"manifest note: {MANIFEST_NOTE}")
    print(f"\nALL GATES PASSED (R1 no empty extract, R4 enrollment county "
          f"match: {extract_counties} counties)")


if __name__ == "__main__":
    main()
