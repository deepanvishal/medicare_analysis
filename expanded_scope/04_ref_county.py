"""
04 - ms_ref_county   [PYTHON runner / BigQuery DDL]

WHAT : County dimension for the scope states -- county_fips (canonical key),
       state_cd, county_name, county_type (from HSD), compliance_threshold.
WHY  : Single place FIPS <-> county <-> type <-> threshold is established; every
       downstream table joins on county_fips. Replaces FL's Census classification.
SOURCE: ms_ref_hsd_required_counts (type/names) + geo_us_boundaries.counties (fips)
GRAIN : county_fips (~272: FL 67, OH 88, AZ 15, IL 102)
NOTE : county_type straight from HSD, not Census. FIPS by normalized name match;
       the gate fails loudly if any scope county does not resolve to a FIPS.
Run  : python expanded_scope/04_ref_county.py
"""

import config as cfg

OUT      = cfg.table("ref_county")
HSD      = cfg.table("ref_hsd_required_counts")
COUNTIES = "bigquery-public-data.geo_us_boundaries.counties"

_SCOPE = ",\n    ".join(
    f"STRUCT('{a}' AS state_cd, '{f}' AS state_fips)" for a, f in cfg.STATES.items()
)

DDL = f"""
CREATE OR REPLACE TABLE `{OUT}`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
WITH hsd_counties AS (
  SELECT DISTINCT state_cd, county_name, county_type FROM `{HSD}`
),
scope AS (
  SELECT * FROM UNNEST([
    {_SCOPE}
  ])
),
census AS (
  SELECT
    geo_id                                                                          AS county_fips,
    state_fips_code,
    UPPER(REGEXP_REPLACE(REPLACE(county_name, 'Saint', 'St'), r'[^A-Za-z0-9]', '')) AS name_key
  FROM `{COUNTIES}`
  WHERE state_fips_code IN {cfg.state_fips_sql()}
)
SELECT
  c.county_fips,
  h.state_cd,
  h.county_name,
  h.county_type,
  CASE WHEN h.county_type IN ('Large Metro', 'Metro') THEN 0.90 ELSE 0.85 END AS compliance_threshold
FROM hsd_counties h
JOIN scope s
  ON h.state_cd = s.state_cd
LEFT JOIN census c
  ON c.state_fips_code = s.state_fips
  AND c.name_key = UPPER(REGEXP_REPLACE(REPLACE(h.county_name, 'Saint', 'St'), r'[^A-Za-z0-9]', ''))
"""

GATE = f"SELECT COUNT(*) FROM `{OUT}` WHERE county_fips IS NULL"

CHECKS = {
    "counties per state (expect FL 67, OH 88, AZ 15, IL 102; with_fips == counties)":
        f"SELECT state_cd, COUNT(*) AS counties, COUNT(county_fips) AS with_fips "
        f"FROM `{OUT}` GROUP BY state_cd ORDER BY state_cd",
}


def main():
    cfg.run_ddl(DDL, CHECKS, gate_sql=GATE, gate_msg="counties unresolved to FIPS")


if __name__ == "__main__":
    main()
