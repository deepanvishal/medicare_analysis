"""
10 - ms_fact_zip_access   [PYTHON runner / BigQuery DDL]

WHAT : For each bene zip x cms_specialty x plan_type, count providers within the CMS
       distance and flag has_access. Sparse -- only rows with >= 1 provider stored.
WHY  : Test 1 (access) building block; rolled up to county % in 11.
SOURCE: ms_stg_beneficiaries + ms_ref_zip_reference + ms_stg_providers_multi_specialty
        + ms_ref_time_distance
GRAIN : bene_zip x cms_specialty x plan_type
NOTE : Threshold is the BENEFICIARY county's PER-COUNTY value -- ms_ref_time_distance
       joined on (state_cd, county_name), NOT county_type. distance = straight-line
       ST_DISTANCE(centroid, centroid) / 1609.34 miles.
Run  : python expanded_scope/10_fact_zip_access.py
"""

import config as cfg

OUT   = cfg.table("fact_zip_access")
BENE  = cfg.table("stg_beneficiaries")
ZIP   = cfg.table("ref_zip_reference")
PROV  = cfg.table("stg_providers_multi_specialty")
TD    = cfg.table("ref_time_distance")

DDL = f"""
CREATE OR REPLACE TABLE `{OUT}`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
WITH zip_provider_pairs AS (
  SELECT
    b.zip_code                                                       AS bene_zip,
    b.county_fips                                                    AS bene_county_fips,
    b.state_cd                                                       AS bene_state_cd,
    b.county_name                                                    AS bene_county_name,
    b.county_type                                                    AS bene_county_type,
    b.compliance_threshold,
    b.zip_medicare_eligibles                                         AS bene_zip_population,
    b.zip_radius_miles                                               AS bene_zip_radius,
    p.provider_id, p.cms_specialty, p.plan_type,
    t.max_distance_miles
  FROM `{BENE}` b
  JOIN `{ZIP}` bene_zip ON b.zip_code = bene_zip.zip_code
  JOIN `{PROV}` p
    ON ST_DWITHIN(
         ST_GEOGPOINT(bene_zip.zip_long, bene_zip.zip_lat),
         ST_GEOGPOINT(p.zip_long,        p.zip_lat),
         800 * 1609.34)                                                -- spatial prune > 800 mi (> the ~505 mi max CMS threshold)
  JOIN `{TD}` t
    ON t.cms_specialty = p.cms_specialty
    AND t.state_cd     = b.state_cd
    AND t.county_name  = b.county_name
  WHERE ST_DISTANCE(
          ST_GEOGPOINT(bene_zip.zip_long, bene_zip.zip_lat),
          ST_GEOGPOINT(p.zip_long,        p.zip_lat)
        ) / 1609.34 <= t.max_distance_miles
)
SELECT
  bene_zip, bene_county_fips, bene_state_cd, bene_county_name, bene_county_type,
  compliance_threshold, bene_zip_population, bene_zip_radius,
  cms_specialty, plan_type, max_distance_miles,
  COUNT(DISTINCT provider_id)                                        AS provider_count_within_threshold,
  TRUE                                                               AS has_access
FROM zip_provider_pairs
GROUP BY
  bene_zip, bene_county_fips, bene_state_cd, bene_county_name, bene_county_type,
  compliance_threshold, bene_zip_population, bene_zip_radius,
  cms_specialty, plan_type, max_distance_miles
"""

CHECKS = {
    "access rows per state":
        f"SELECT bene_state_cd, COUNT(*) AS access_rows, COUNT(DISTINCT bene_zip) AS zips_with_access "
        f"FROM `{OUT}` GROUP BY bene_state_cd ORDER BY bene_state_cd",
}


def main():
    cfg.run_ddl(DDL, CHECKS)


if __name__ == "__main__":
    main()
