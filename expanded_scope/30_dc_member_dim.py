"""
30 - ms_dc_member_dim   [PYTHON runner / BigQuery DDL]   *** DEMAND/CAPACITY EXTENSION -- M1 ***

WHAT : Member dimension. One row per member_id (latest 2025 membership row) with a
       resolved county_fips, age band, chronic_condition_count and morbidity level.
WHY  : The member grain for the demand/capacity modules; supplies the Aetna MA member
       counts and morbidity mix consumed downstream.
SOURCE: mdcr_base_membership + A870800_medicare_analysis_2025_claims + dc_ref_ccir
        + ms_ref_county (county_fips by name) + ms_ref_zip_reference (county_fips by zip)
GRAIN : member_id
NOTE : membership date column is eff_dt (the build prompt's eff_df is the same
       confirmed typo fixed earlier in eda_runner.py).
Run  : python expanded_scope/30_dc_member_dim.py
"""

import config as cfg

OUT    = cfg.table("dc_member_dim")
MBR    = cfg.src("mdcr_base_membership")
CLAIMS = cfg.src("A870800_medicare_analysis_2025_claims")
CCIR   = cfg.table("dc_ref_ccir")
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
member_chronic AS (
  SELECT c.member_id,
         COUNT(DISTINCT CASE WHEN r.chronic_indicator = 1 THEN r.icd_code END) AS chronic_condition_count
  FROM `{CLAIMS}` c
  JOIN `{CCIR}` r
    ON REPLACE(c.pri_icd9_dx_cd, '.', '') = r.icd_code
  GROUP BY c.member_id
),
name_match AS (
  SELECT m.*, rc.county_fips AS fips_by_name
  FROM mbr_latest m
  LEFT JOIN `{CTY}` rc
    ON m.state_cd = rc.state_cd
    AND m.county_nm = UPPER(rc.county_name)
),
zip_match AS (
  SELECT nm.*, z.county_fips AS fips_by_zip
  FROM name_match nm
  LEFT JOIN `{ZIP}` z ON nm.zip_cd = z.zip_code
)
SELECT
  zm.member_id,
  zm.state_cd,
  zm.county_nm,
  zm.zip_cd,
  zm.age_nbr,
  COALESCE(zm.fips_by_name, zm.fips_by_zip) AS county_fips,
  CASE WHEN zm.fips_by_name IS NOT NULL THEN 'NAME'
       WHEN zm.fips_by_zip IS NOT NULL THEN 'ZIP'
       ELSE 'UNMATCHED' END AS county_source,
  CASE WHEN zm.age_nbr BETWEEN 60 AND 64 THEN '60-64'
       WHEN zm.age_nbr BETWEEN 65 AND 69 THEN '65-69'
       WHEN zm.age_nbr BETWEEN 70 AND 74 THEN '70-74'
       WHEN zm.age_nbr BETWEEN 75 AND 79 THEN '75-79'
       WHEN zm.age_nbr >= 80 THEN '80+'
       ELSE 'UNDER_60' END AS age_band,
  COALESCE(mc.chronic_condition_count, 0) AS chronic_condition_count,
  CASE WHEN COALESCE(mc.chronic_condition_count, 0) >= 1 THEN 'CHRONIC'
       ELSE 'NON_CHRONIC' END AS morbidity_level
FROM zip_match zm
LEFT JOIN member_chronic mc ON zm.member_id = mc.member_id
"""

CHECKS = {
    "members per state (expect ~372.8K FL / 306.8K OH / 66.5K AZ / 347.2K IL)":
        f"SELECT state_cd, COUNT(*) AS members FROM `{OUT}` GROUP BY 1 ORDER BY 1",
    "morbidity mix (CHRONIC vs NON_CHRONIC member counts)":
        f"SELECT morbidity_level, COUNT(*) AS members FROM `{OUT}` GROUP BY 1 ORDER BY 1",
    "county_source mix (UNMATCHED should be near zero)":
        f"SELECT county_source, COUNT(*) AS members FROM `{OUT}` GROUP BY 1 ORDER BY 1",
    "age band mix":
        f"SELECT age_band, COUNT(*) AS members FROM `{OUT}` GROUP BY 1 ORDER BY 1",
    "claims specialty columns (informational, answers the M3 crosswalk question)":
        "SELECT column_name, data_type "
        "FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.INFORMATION_SCHEMA.COLUMNS` "
        "WHERE table_name = 'A870800_medicare_analysis_2025_claims' "
        "AND LOWER(column_name) LIKE '%special%' ORDER BY ordinal_position",
}


def main():
    cfg.run_ddl(DDL, CHECKS)


if __name__ == "__main__":
    main()
