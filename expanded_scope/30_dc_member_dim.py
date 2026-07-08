"""
30 - ms_dc_member_dim   [PYTHON runner / BigQuery DDL]   *** DEMAND/CAPACITY EXTENSION -- M1 ***

WHAT : Member dimension. One row per member_id (latest 2025 membership row) with a
       resolved county_fips, age band, HCC count and morbidity level.
WHY  : The member grain for the demand/capacity modules; supplies the Aetna MA member
       counts and morbidity mix consumed by M2 and downstream.
SOURCE: mdcr_base_membership + A870800_medicare_analysis_2025_claims + HCC_ICD_Mapping_2025
        + ms_ref_county (county_fips by name) + ms_ref_zip_reference (county_fips by zip)
GRAIN : member_id
NOTE : membership date column is eff_dt (the build prompt's eff_df was a typo).
Run  : python expanded_scope/30_dc_member_dim.py
"""

import config as cfg

OUT    = cfg.table("dc_member_dim")
MBR    = cfg.src("mdcr_base_membership")
CLAIMS = cfg.src("A870800_medicare_analysis_2025_claims")
HCC    = cfg.src("HCC_ICD_Mapping_2025")
CTY    = cfg.table("ref_county")
ZIP    = cfg.table("ref_zip_reference")

DDL = f"""
CREATE OR REPLACE TABLE `{OUT}`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
WITH mbr_latest AS (
  SELECT
    member_id,
    state_postal_cd  AS state_cd,
    UPPER(county_nm) AS county_nm,
    zip_cd,
    age_nbr
  FROM (
    SELECT *,
           ROW_NUMBER() OVER (PARTITION BY member_id ORDER BY eff_dt DESC) AS rn
    FROM `{MBR}`
    WHERE EXTRACT(YEAR FROM eff_dt) = 2025
  )
  WHERE rn = 1
    AND state_postal_cd IN ('FL', 'OH', 'AZ', 'IL')
),
member_hcc AS (
  SELECT c.member_id, COUNT(DISTINCT h.HCC_v24) AS hcc_count
  FROM `{CLAIMS}` c
  JOIN `{HCC}` h
    ON REPLACE(c.pri_icd9_dx_cd, '.', '') = REPLACE(h.diagnosis_code, '.', '')
  GROUP BY c.member_id
),
county_match AS (
  SELECT m.*, rc.county_fips AS name_fips
  FROM mbr_latest m
  LEFT JOIN `{CTY}` rc
    ON m.state_cd = rc.state_cd
    AND m.county_nm = UPPER(rc.county_name)
),
zip_fallback AS (
  SELECT
    cm.member_id,
    cm.state_cd,
    cm.county_nm,
    cm.zip_cd,
    cm.age_nbr,
    COALESCE(cm.name_fips, z.county_fips) AS county_fips,
    CASE WHEN cm.name_fips IS NOT NULL THEN 'NAME'
         WHEN z.county_fips IS NOT NULL THEN 'ZIP'
         ELSE 'UNMATCHED' END              AS county_source
  FROM county_match cm
  LEFT JOIN `{ZIP}` z ON cm.zip_cd = z.zip_code
)
SELECT
  zf.member_id,
  zf.state_cd,
  zf.county_fips,
  zf.county_nm,
  zf.county_source,
  zf.zip_cd,
  zf.age_nbr,
  CASE WHEN zf.age_nbr BETWEEN 60 AND 64 THEN '60-64'
       WHEN zf.age_nbr BETWEEN 65 AND 69 THEN '65-69'
       WHEN zf.age_nbr BETWEEN 70 AND 74 THEN '70-74'
       WHEN zf.age_nbr BETWEEN 75 AND 79 THEN '75-79'
       WHEN zf.age_nbr >= 80 THEN '80+'
       ELSE 'UNDER_60' END AS age_band,
  COALESCE(mh.hcc_count, 0) AS hcc_count,
  CASE WHEN COALESCE(mh.hcc_count, 0) = 0 THEN 'LOW'
       WHEN COALESCE(mh.hcc_count, 0) BETWEEN 1 AND 2 THEN 'MEDIUM'
       ELSE 'HIGH' END AS morbidity_level
FROM zip_fallback zf
LEFT JOIN member_hcc mh ON zf.member_id = mh.member_id
"""

CHECKS = {
    "members per state (expect ~372.8K FL / 306.8K OH / 66.5K AZ / 347.2K IL)":
        f"SELECT state_cd, COUNT(*) AS members FROM `{OUT}` GROUP BY 1 ORDER BY 1",
    "morbidity mix (expect roughly 40/40/20 LOW/MEDIUM/HIGH)":
        f"SELECT morbidity_level, COUNT(*) AS members FROM `{OUT}` GROUP BY 1 ORDER BY 1",
    "county_source mix (UNMATCHED should be near zero)":
        f"SELECT county_source, COUNT(*) AS members FROM `{OUT}` GROUP BY 1 ORDER BY 1",
    "age band mix":
        f"SELECT age_band, COUNT(*) AS members FROM `{OUT}` GROUP BY 1 ORDER BY 1",
}


def main():
    cfg.run_ddl(DDL, CHECKS)


if __name__ == "__main__":
    main()
