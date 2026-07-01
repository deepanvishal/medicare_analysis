"""
11 - ms_fact_gap_analysis   [PYTHON runner / BigQuery DDL]

WHAT : County compliance output. Full county x specialty x plan_type grid;
       Test 1 (access %) + Test 2 (actual_count vs HSD required_count).
WHY  : THE table the report reads. compliance_status = COMPLIANT iff both pass.
SOURCE: ms_stg_beneficiaries + ms_ref_zip_reference + ms_stg_providers_multi_specialty
        + ms_ref_hsd_required_counts + ms_ref_time_distance + ms_fact_zip_access + hosp_list_cmi
GRAIN : state_cd x county_fips x cms_specialty x plan_type
NOTE : HSD and time/distance joined on (state_cd, county_name) -- collision-safe.
       distinct_providers re-counts from source (distance filter), grouped by MEMBER
       county. Acute Inpatient actual_count = SUM(hosp_list_cmi.Beds); a state with 0
       beds means hosp_list_cmi lacks it (see the beds-per-state check).
Run  : python expanded_scope/11_fact_gap_analysis.py
"""

import config as cfg

OUT   = cfg.table("fact_gap_analysis")
BENE  = cfg.table("stg_beneficiaries")
ZIP   = cfg.table("ref_zip_reference")
PROV  = cfg.table("stg_providers_multi_specialty")
HSD   = cfg.table("ref_hsd_required_counts")
TD    = cfg.table("ref_time_distance")
ACCESS = cfg.table("fact_zip_access")
HOSP  = cfg.src("hosp_list_cmi")

DDL = f"""
CREATE OR REPLACE TABLE `{OUT}`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
WITH all_combinations AS (
  SELECT
    b.zip_code, b.county_fips, b.state_cd, b.county_name, b.county_type,
    b.compliance_threshold, b.total_population, b.zip_medicare_eligibles,
    sc.cms_specialty, pt.plan_type
  FROM `{BENE}` b
  CROSS JOIN (SELECT DISTINCT cms_specialty FROM `{HSD}`) sc
  CROSS JOIN (SELECT DISTINCT plan_type FROM `{PROV}`) pt
),
zip_access_complete AS (
  SELECT
    a.zip_code, a.county_fips, a.state_cd, a.county_name, a.county_type,
    a.compliance_threshold, a.total_population, a.zip_medicare_eligibles,
    a.cms_specialty, a.plan_type,
    COALESCE(z.provider_count_within_threshold, 0)                  AS provider_count,
    COALESCE(z.has_access, FALSE)                                   AS has_access
  FROM all_combinations a
  LEFT JOIN `{ACCESS}` z
    ON a.zip_code = z.bene_zip AND a.cms_specialty = z.cms_specialty AND a.plan_type = z.plan_type
),
county_rollup AS (
  SELECT
    county_fips, state_cd, county_name, county_type, compliance_threshold,
    cms_specialty, plan_type,
    SUM(zip_medicare_eligibles)                                     AS total_county_population,
    SUM(CASE WHEN has_access THEN zip_medicare_eligibles ELSE 0 END) AS population_with_access,
    ROUND(SUM(CASE WHEN has_access THEN zip_medicare_eligibles ELSE 0 END)
          / NULLIF(SUM(zip_medicare_eligibles), 0), 4)             AS pct_covered
  FROM zip_access_complete
  GROUP BY county_fips, state_cd, county_name, county_type, compliance_threshold, cms_specialty, plan_type
),
distinct_providers AS (
  SELECT
    b.county_fips, b.state_cd, b.county_name, p.cms_specialty, p.plan_type,
    COUNT(DISTINCT p.provider_id)                                   AS actual_provider_count
  FROM `{BENE}` b
  JOIN `{ZIP}` bene_zip ON b.zip_code = bene_zip.zip_code
  JOIN `{PROV}` p
    ON ST_DWITHIN(
         ST_GEOGPOINT(bene_zip.zip_long, bene_zip.zip_lat),
         ST_GEOGPOINT(p.zip_long,        p.zip_lat),
         800 * 1609.34)                                                -- spatial prune > 800 mi (> the ~505 mi max CMS threshold)
  JOIN `{TD}` t
    ON t.cms_specialty = p.cms_specialty AND t.state_cd = b.state_cd AND t.county_name = b.county_name
  WHERE ST_DISTANCE(
          ST_GEOGPOINT(bene_zip.zip_long, bene_zip.zip_lat),
          ST_GEOGPOINT(p.zip_long,        p.zip_lat)
        ) / 1609.34 <= t.max_distance_miles
  GROUP BY b.county_fips, b.state_cd, b.county_name, p.cms_specialty, p.plan_type
),
hospital_beds AS (
  SELECT
    p.county_fips, p.plan_type, SUM(CAST(h.Beds AS INT64))         AS total_contracted_beds
  FROM `{PROV}` p
  JOIN `{HOSP}` h ON CAST(p.provider_id AS STRING) = CAST(h.Pin AS STRING)
  WHERE p.cms_specialty = 'Acute Inpatient Hospitals' AND h.Beds IS NOT NULL
  GROUP BY p.county_fips, p.plan_type
)
SELECT
  r.state_cd, r.county_fips, r.county_name, r.county_type, r.cms_specialty, r.plan_type,
  hsd.total_beneficiaries                                          AS county_total_beneficiaries,
  hsd.beneficiaries_required_to_cover,
  hsd.ratio_95th_percentile,
  r.total_county_population, r.population_with_access, r.pct_covered, r.compliance_threshold,
  hsd.required_count                                              AS required_provider_count,
  t.min_ratio_per_1000, t.max_distance_miles,
  CASE WHEN r.cms_specialty = 'Acute Inpatient Hospitals'
       THEN COALESCE(b.total_contracted_beds, 0) ELSE NULL END    AS total_contracted_beds,
  CASE WHEN r.cms_specialty = 'Acute Inpatient Hospitals'
       THEN COALESCE(b.total_contracted_beds, 0)
       ELSE COALESCE(dp.actual_provider_count, 0) END             AS actual_count,
  CASE WHEN r.cms_specialty = 'Acute Inpatient Hospitals'
       THEN hsd.required_count - COALESCE(b.total_contracted_beds, 0)
       ELSE hsd.required_count - COALESCE(dp.actual_provider_count, 0) END AS provider_gap,
  (r.pct_covered >= r.compliance_threshold)                       AS access_compliant,
  CASE WHEN r.cms_specialty = 'Acute Inpatient Hospitals'
       THEN COALESCE(b.total_contracted_beds, 0) >= hsd.required_count
       ELSE COALESCE(dp.actual_provider_count, 0) >= hsd.required_count END AS count_compliant,
  CASE
    WHEN r.pct_covered >= r.compliance_threshold
     AND (CASE WHEN r.cms_specialty = 'Acute Inpatient Hospitals'
               THEN COALESCE(b.total_contracted_beds, 0) >= hsd.required_count
               ELSE COALESCE(dp.actual_provider_count, 0) >= hsd.required_count END)
    THEN 'COMPLIANT' ELSE 'NON-COMPLIANT'
  END                                                             AS compliance_status
FROM county_rollup r
LEFT JOIN distinct_providers dp
  ON r.county_fips = dp.county_fips AND r.cms_specialty = dp.cms_specialty AND r.plan_type = dp.plan_type
JOIN `{HSD}` hsd
  ON hsd.state_cd = r.state_cd AND hsd.county_name = r.county_name AND hsd.cms_specialty = r.cms_specialty
LEFT JOIN `{TD}` t
  ON t.state_cd = r.state_cd AND t.county_name = r.county_name AND t.cms_specialty = r.cms_specialty
LEFT JOIN hospital_beds b
  ON r.county_fips = b.county_fips AND r.plan_type = b.plan_type
"""

CHECKS = {
    "compliance mix per state x plan":
        f"SELECT state_cd, plan_type, compliance_status, COUNT(*) AS rows FROM `{OUT}` "
        f"GROUP BY state_cd, plan_type, compliance_status ORDER BY state_cd, plan_type, compliance_status",
    "RISK: Acute Inpatient beds per state (0 => hosp_list_cmi lacks that state)":
        f"SELECT state_cd, SUM(COALESCE(total_contracted_beds, 0)) AS acute_beds FROM `{OUT}` "
        f"WHERE cms_specialty = 'Acute Inpatient Hospitals' GROUP BY state_cd ORDER BY state_cd",
}


def main():
    cfg.run_ddl(DDL, CHECKS)


if __name__ == "__main__":
    main()
