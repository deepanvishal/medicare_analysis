"""
34 - ms_dc_book_utilization   [PYTHON runner / BigQuery DDL]   *** DEMAND/CAPACITY EXTENSION -- M5 ***

WHAT : Book-of-business utilization. One row per state_cd x prvdr_county x
       specialty_ctg_cd x lob x age_band; lob values CP, ME, and TOTAL (CP+ME
       materialized as its own rows).
WHY  : Total vs Medicare delivered-visit context by provider geography.
SOURCE: A870800_medicare_analysis_2025_claims
GRAIN : state_cd x prvdr_county x specialty_ctg_cd x lob x age_band
NOTE : Delivered-visit view keyed to the PROVIDER's county (member home county is not attributable);
       lob TOTAL = CP+ME. This is the Total vs MA utilization context tab, not the demand gap driver.
Run  : python expanded_scope/34_dc_book_utilization.py
"""

import config as cfg

OUT    = cfg.table("dc_book_utilization")
CLAIMS = cfg.src("A870800_medicare_analysis_2025_claims")

DDL = f"""
CREATE OR REPLACE TABLE `{OUT}`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
WITH base AS (
  SELECT
    UPPER(LEFT(prvdr_submarket, 2)) AS state_cd,
    UPPER(prvdr_county)             AS prvdr_county,
    specialty_ctg_cd,
    specialty_ctg_cd_desc,
    business_ln_cd                  AS lob,
    CASE WHEN age_nbr BETWEEN 60 AND 64 THEN '60-64'
         WHEN age_nbr BETWEEN 65 AND 69 THEN '65-69'
         WHEN age_nbr BETWEEN 70 AND 74 THEN '70-74'
         WHEN age_nbr BETWEEN 75 AND 79 THEN '75-79'
         ELSE '80+' END             AS age_band,
    member_id,
    srv_prvdr_id,
    srv_start_dt
  FROM `{CLAIMS}`
  WHERE UPPER(LEFT(prvdr_submarket, 2)) IN ('FL', 'OH', 'AZ', 'IL')
    AND age_nbr >= 60
),
by_lob AS (
  SELECT
    state_cd,
    prvdr_county,
    specialty_ctg_cd,
    ANY_VALUE(specialty_ctg_cd_desc) AS specialty_desc,
    lob,
    age_band,
    COUNT(DISTINCT CONCAT(member_id, '|', CAST(srv_prvdr_id AS STRING), '|',
                          CAST(srv_start_dt AS STRING))) AS visits
  FROM base
  GROUP BY state_cd, prvdr_county, specialty_ctg_cd, lob, age_band
),
total_rows AS (
  SELECT
    state_cd,
    prvdr_county,
    specialty_ctg_cd,
    ANY_VALUE(specialty_ctg_cd_desc) AS specialty_desc,
    'TOTAL' AS lob,
    age_band,
    COUNT(DISTINCT CONCAT(member_id, '|', CAST(srv_prvdr_id AS STRING), '|',
                          CAST(srv_start_dt AS STRING))) AS visits
  FROM base
  GROUP BY state_cd, prvdr_county, specialty_ctg_cd, age_band
)
SELECT state_cd, prvdr_county, specialty_ctg_cd, specialty_desc, lob, age_band, visits
FROM by_lob
UNION ALL
SELECT state_cd, prvdr_county, specialty_ctg_cd, specialty_desc, lob, age_band, visits
FROM total_rows
"""

CHECKS = {
    "visits by state x lob (ME share eyeball)":
        f"SELECT state_cd, lob, SUM(visits) AS visits FROM `{OUT}` GROUP BY 1, 2 ORDER BY 1, 2",
    "TOTAL equals CP+ME (max discrepancy, expect 0)":
        f"SELECT MAX(ABS(t.visits - (COALESCE(c.visits, 0) + COALESCE(m.visits, 0)))) AS max_diff "
        f"FROM (SELECT state_cd, prvdr_county, specialty_ctg_cd, age_band, visits "
        f"FROM `{OUT}` WHERE lob = 'TOTAL') t "
        f"LEFT JOIN (SELECT state_cd, prvdr_county, specialty_ctg_cd, age_band, visits "
        f"FROM `{OUT}` WHERE lob = 'CP') c "
        f"USING (state_cd, prvdr_county, specialty_ctg_cd, age_band) "
        f"LEFT JOIN (SELECT state_cd, prvdr_county, specialty_ctg_cd, age_band, visits "
        f"FROM `{OUT}` WHERE lob = 'ME') m "
        f"USING (state_cd, prvdr_county, specialty_ctg_cd, age_band)",
    "top 10 delivery counties by ME visits":
        f"SELECT state_cd, prvdr_county, SUM(visits) AS me_visits FROM `{OUT}` "
        f"WHERE lob = 'ME' GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 10",
}


def main():
    cfg.run_ddl(DDL, CHECKS)


if __name__ == "__main__":
    main()
