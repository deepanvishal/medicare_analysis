"""
37 - ms_dc_forecast_example   [PYTHON runner / BigQuery DDL]   *** DEMAND/CAPACITY EXTENSION -- M8 ***

WHAT : Forecast example. One row per county_fips x cms_specialty x plan_type x
       forecast_month (12 rows per example cell); example cells are the compliant
       cells closest to tipping (smallest capacity surplus) per state, selected
       inside the query, so the crossover month is meaningful.
WHY  : Shows how the gap table projects forward: monthly demand shape x demographic
       growth vs flat capacity, and where the crossover months land.
SOURCE: ms_dc_gap + A870800_medicare_analysis_2025_claims
GRAIN : county_fips x cms_specialty x plan_type x forecast_month
NOTE : Illustrative one-time projection, not a validated forecast and not a refresh pipeline: trend is
       demographic (flat 3 percent annual eligible growth applied monthly as 1.03^(1/12), a stated
       placeholder until penetration YoY is loaded), shape is the observed 2025 within-year seasonality
       at state x specialty level, capacity is held flat. One year of claims allows no holdout
       validation.
Run  : python expanded_scope/37_dc_forecast_example.py
"""

import config as cfg

OUT    = cfg.table("dc_forecast_example")
GAP    = cfg.table("dc_gap")
CLAIMS = cfg.src("A870800_medicare_analysis_2025_claims")

DDL = f"""
CREATE OR REPLACE TABLE `{OUT}`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
WITH example_cells AS (
  SELECT state_cd, county_fips, county_name, cms_specialty, plan_type,
         ma_demand_visits, capacity_visits
  FROM (
    SELECT *,
           ROW_NUMBER() OVER (PARTITION BY state_cd ORDER BY demand_capacity_gap DESC) AS rn
    FROM `{GAP}`
    WHERE compliance_status = 'COMPLIANT' AND demand_capacity_gap < 0
  )
  WHERE rn = 1
),
monthly_shape AS (
  SELECT
    UPPER(LEFT(prvdr_submarket, 2)) AS state_cd,
    specialty_ctg_cd_desc,
    EXTRACT(MONTH FROM srv_start_dt) AS mo,
    COUNT(DISTINCT CONCAT(member_id, '|', CAST(srv_prvdr_id AS STRING), '|',
                          CAST(srv_start_dt AS STRING))) AS visits
  FROM `{CLAIMS}`
  WHERE business_ln_cd = 'ME'
    AND UPPER(LEFT(prvdr_submarket, 2)) IN ('FL', 'OH', 'AZ', 'IL')
  GROUP BY 1, 2, 3
),
seasonality AS (
  SELECT
    state_cd,
    UPPER(specialty_ctg_cd_desc) AS specialty_key,
    mo,
    visits / NULLIF(SUM(visits) OVER (PARTITION BY state_cd, specialty_ctg_cd_desc), 0)
      AS seasonality_index
  FROM monthly_shape
),
months AS (
  SELECT mo AS forecast_month FROM UNNEST(GENERATE_ARRAY(1, 12)) AS mo
)
SELECT
  e.state_cd,
  e.county_fips,
  e.county_name,
  e.cms_specialty,
  e.plan_type,
  m.forecast_month,
  COALESCE(s.seasonality_index, 1/12) AS seasonality_index,
  POW(1.03, m.forecast_month / 12.0) AS growth_factor,
  e.ma_demand_visits * COALESCE(s.seasonality_index, 1/12) * POW(1.03, m.forecast_month / 12.0)
    AS projected_demand_visits,
  e.capacity_visits / 12.0 AS monthly_capacity,
  (e.ma_demand_visits * COALESCE(s.seasonality_index, 1/12) * POW(1.03, m.forecast_month / 12.0))
    > (e.capacity_visits / 12.0) AS crossover_flag
FROM example_cells e
CROSS JOIN months m
LEFT JOIN seasonality s
  ON s.state_cd = e.state_cd
  AND s.specialty_key = UPPER(e.cms_specialty)
  AND s.mo = m.forecast_month
"""

CHECKS = {
    "example cells chosen (one per state)":
        f"SELECT DISTINCT state_cd, county_name, cms_specialty, plan_type FROM `{OUT}` ORDER BY 1",
    "12 months per cell":
        f"SELECT state_cd, county_fips, cms_specialty, plan_type, COUNT(*) AS months "
        f"FROM `{OUT}` GROUP BY 1, 2, 3, 4 HAVING COUNT(*) != 12",
    "crossover months per cell":
        f"SELECT state_cd, county_name, cms_specialty, COUNTIF(crossover_flag) AS months_over_capacity "
        f"FROM `{OUT}` GROUP BY 1, 2, 3 ORDER BY 1",
}


def main():
    cfg.run_ddl(DDL, CHECKS)


if __name__ == "__main__":
    main()
