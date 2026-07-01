"""
Phase 0 GATE: confirm the data needed for the 4-state build actually exists.

Run this BEFORE building anything. If a state has no CMS/Aetna coverage, or
hosp_list_cmi carries no usable beds, it surfaces here -- cheap to check now,
expensive to discover mid-pipeline.

Only queries VERIFIED sources. Note on the Aetna supply side (SETTLED):
  - mbr_with_zip (ours, A870800_) already holds ALL zip/state data -- national.
  - mbr_with_all_zips is only FL because Step5 applies WHERE m.state='FL'.
  So multi-state supply = rebuild ms_mbr_with_all_zips with the filter opened to all
  4 states. The mbr_with_zip_states check below is just a sanity count, not a gate.

Auth: gcloud auth application-default login   (billing project anbc-dev-prv-nc-ds)
Run:  python expanded_scope/00_check_data_availability.py
"""

import config as cfg


CHECKS = {
    # CMS Original Medicare FFS providers by state (national; has state column)
    "cms_ffs_providers_by_state": f"""
        SELECT rndrng_prvdr_state_abrvtn AS state,
               COUNT(DISTINCT rndrng_npi) AS providers
        FROM `{cfg.src('cms_medicare_physician_ffs_2023')}`
        WHERE rndrng_prvdr_state_abrvtn IN {cfg.state_abbr_sql()}
        GROUP BY state
        ORDER BY state
    """,

    # Public county polygons present per state (drives zip->county + county_type)
    "counties_present_by_state": f"""
        SELECT state_fips_code, COUNT(*) AS counties
        FROM `bigquery-public-data.geo_us_boundaries.counties`
        WHERE state_fips_code IN {cfg.state_fips_sql()}
        GROUP BY state_fips_code
        ORDER BY state_fips_code
    """,

    # hosp_list_cmi: inspect schema (no column guessing) -- is it state-scoped?
    "hosp_list_cmi_schema": f"""
        SELECT column_name, data_type
        FROM `{cfg.TABLE_PROJECT}.{cfg.DATASET}`.INFORMATION_SCHEMA.COLUMNS
        WHERE table_name = 'hosp_list_cmi'
        ORDER BY ordinal_position
    """,
    "hosp_list_cmi_rowcount": f"""
        SELECT COUNT(*) AS hospitals
        FROM `{cfg.src('hosp_list_cmi')}`
    """,

    # Sanity count (not a gate): mbr_with_zip already holds all zip/state data.
    # Confirms OH/AZ/IL row/provider counts before we relax the FL filter in Step5.
    "mbr_with_zip_states": f"""
        SELECT state,
               COUNT(*)                    AS row_count,
               COUNT(DISTINCT prvdr_id_no) AS providers
        FROM `{cfg.base('mbr_with_zip')}`
        GROUP BY state
        ORDER BY row_count DESC
    """,
}


def main():
    client = cfg.client()
    print(f"Scope: {', '.join(cfg.STATE_ABBRS)}  |  FIPS {', '.join(cfg.STATE_FIPS)}")
    for label, sql in CHECKS.items():
        print(f"\n=== {label} ===")
        try:
            result_rows = list(client.query(sql).result())
            if not result_rows:
                print("  (no rows)")
            for row in result_rows:
                print("  ", dict(row))
        except Exception as e:
            print("  ERROR:", e)


if __name__ == "__main__":
    main()
