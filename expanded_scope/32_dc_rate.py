"""
32 - ms_dc_rate   [PYTHON runner / BigQuery DDL]   *** DEMAND/CAPACITY EXTENSION -- M3 ***

WHAT : Utilization rates. One row per state_cd x specialty_ctg_cd x age_band x
       morbidity_level; state_cd includes FL/OH/AZ/IL plus 'ALL' (4-state pooled
       fallback rows for thin cells).
WHY  : The rate layer for the demand/capacity modules; visits per member per year
       by cell, with thin-cell flags for the national-fallback path.
SOURCE: A870800_medicare_analysis_2025_claims + ms_dc_member_dim
GRAIN : state_cd x specialty_ctg_cd x age_band x morbidity_level
NOTE : Specialty is mapped at specialty_ctg_cd (category) level; upgrade to raw specialty_cd via
       ref_specialty_crosswalk_expanded if claims carry it. Rates are MA-proxy: numerator is Aetna ME
       visits, denominator is Aetna Medicare members; rate_total_medicare is NULL in v1.
Run  : python expanded_scope/32_dc_rate.py
"""

import config as cfg

OUT    = cfg.table("dc_rate")
CLAIMS = cfg.src("A870800_medicare_analysis_2025_claims")
MEMDIM = cfg.table("dc_member_dim")

DDL = f"""
CREATE OR REPLACE TABLE `{OUT}`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
WITH visits AS (
  SELECT
    m.state_cd,
    c.specialty_ctg_cd,
    ANY_VALUE(c.specialty_ctg_cd_desc) AS specialty_desc,
    m.age_band,
    m.morbidity_level,
    COUNT(DISTINCT CONCAT(c.member_id, '|', CAST(c.srv_prvdr_id AS STRING), '|',
                          CAST(c.srv_start_dt AS STRING))) AS ma_visits
  FROM `{CLAIMS}` c
  JOIN `{MEMDIM}` m ON c.member_id = m.member_id
  WHERE c.business_ln_cd = 'ME' AND m.age_band != 'UNDER_60'
  GROUP BY 1, 2, 4, 5
),
members AS (
  SELECT state_cd, age_band, morbidity_level, COUNT(*) AS ma_members_in_cell
  FROM `{MEMDIM}`
  WHERE age_band != 'UNDER_60'
  GROUP BY 1, 2, 3
),
grid AS (
  SELECT s.state_cd, sp.specialty_ctg_cd, sp.specialty_desc, age_band, morbidity_level
  FROM (SELECT DISTINCT specialty_ctg_cd, specialty_desc FROM visits) sp
  CROSS JOIN (SELECT DISTINCT state_cd FROM members) s
  CROSS JOIN UNNEST(['60-64', '65-69', '70-74', '75-79', '80+']) AS age_band
  CROSS JOIN UNNEST(['CHRONIC', 'NON_CHRONIC']) AS morbidity_level
),
state_rates AS (
  SELECT
    g.state_cd,
    g.specialty_ctg_cd,
    g.specialty_desc,
    g.age_band,
    g.morbidity_level,
    COALESCE(v.ma_visits, 0)                    AS ma_visits,
    COALESCE(mem.ma_members_in_cell, 0)         AS ma_members_in_cell,
    SAFE_DIVIDE(COALESCE(v.ma_visits, 0),
                COALESCE(mem.ma_members_in_cell, 0)) AS rate_ma_proxy,
    COALESCE(mem.ma_members_in_cell, 0) < 30    AS is_thin_cell
  FROM grid g
  LEFT JOIN visits v
    ON g.state_cd = v.state_cd AND g.specialty_ctg_cd = v.specialty_ctg_cd
    AND g.age_band = v.age_band AND g.morbidity_level = v.morbidity_level
  LEFT JOIN members mem
    ON g.state_cd = mem.state_cd AND g.age_band = mem.age_band
    AND g.morbidity_level = mem.morbidity_level
),
national_rates AS (
  SELECT
    'ALL'                                       AS state_cd,
    g.specialty_ctg_cd,
    g.specialty_desc,
    g.age_band,
    g.morbidity_level,
    COALESCE(v.ma_visits, 0)                    AS ma_visits,
    COALESCE(mem.ma_members_in_cell, 0)         AS ma_members_in_cell,
    SAFE_DIVIDE(COALESCE(v.ma_visits, 0),
                COALESCE(mem.ma_members_in_cell, 0)) AS rate_ma_proxy,
    COALESCE(mem.ma_members_in_cell, 0) < 30    AS is_thin_cell
  FROM (SELECT DISTINCT specialty_ctg_cd, specialty_desc, age_band, morbidity_level FROM grid) g
  LEFT JOIN (
    SELECT specialty_ctg_cd, age_band, morbidity_level, SUM(ma_visits) AS ma_visits
    FROM visits GROUP BY 1, 2, 3
  ) v
    ON g.specialty_ctg_cd = v.specialty_ctg_cd
    AND g.age_band = v.age_band AND g.morbidity_level = v.morbidity_level
  LEFT JOIN (
    SELECT age_band, morbidity_level, SUM(ma_members_in_cell) AS ma_members_in_cell
    FROM members GROUP BY 1, 2
  ) mem
    ON g.age_band = mem.age_band AND g.morbidity_level = mem.morbidity_level
)
SELECT
  state_cd,
  specialty_ctg_cd,
  specialty_desc,
  age_band,
  morbidity_level,
  ma_visits,
  ma_members_in_cell AS cell_n,
  rate_ma_proxy,
  is_thin_cell,
  CAST(NULL AS FLOAT64) AS rate_total_medicare
FROM (
  SELECT * FROM state_rates
  UNION ALL
  SELECT * FROM national_rates
)
"""

CHECKS = {
    "thin-cell share per state":
        f"SELECT state_cd, COUNTIF(is_thin_cell) AS thin_cells, COUNT(*) AS total_cells "
        f"FROM `{OUT}` GROUP BY 1 ORDER BY 1",
    "rate sanity: rows with rate > 50 visits/member/yr (should be rare, eyeball)":
        f"SELECT state_cd, specialty_ctg_cd, age_band, morbidity_level, rate_ma_proxy, cell_n "
        f"FROM `{OUT}` WHERE rate_ma_proxy > 50 ORDER BY rate_ma_proxy DESC LIMIT 20",
    "morbidity gradient eyeball (top 5 specialties by visits, FL, 70-74)":
        f"SELECT specialty_ctg_cd, morbidity_level, rate_ma_proxy FROM `{OUT}` "
        f"WHERE state_cd = 'FL' AND age_band = '70-74' AND specialty_ctg_cd IN "
        f"(SELECT specialty_ctg_cd FROM `{OUT}` WHERE state_cd = 'FL' "
        f"GROUP BY 1 ORDER BY SUM(ma_visits) DESC LIMIT 5) "
        f"ORDER BY specialty_ctg_cd, morbidity_level",
    "visit total reconciles to EDA scale":
        f"SELECT SUM(ma_visits) AS total_ma_visits FROM `{OUT}` WHERE state_cd != 'ALL'",
}


def main():
    cfg.run_ddl(DDL, CHECKS)


if __name__ == "__main__":
    main()
