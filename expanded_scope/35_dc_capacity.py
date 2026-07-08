"""
35 - ms_dc_provider_capacity + ms_dc_capacity   [PYTHON runner / BigQuery DDL]   *** DEMAND/CAPACITY EXTENSION -- M6 ***

WHAT : Capacity. First a provider-grain table (typical annual capacity, senior
       saturation, provider slots), then the county x specialty x plan rollup.
       Builds BOTH tables, like 12_provider_par_flag.py.
WHY  : The supply side of the demand/capacity gap; converts the contracted network
       into deliverable visit volumes.
SOURCE: ms_provider_par_flag + A870800_medicare_analysis_2025_claims
GRAIN : provider_capacity -> provider x plan x specialty x county (PAR grain)
        capacity          -> state x county x cms_specialty x plan_type
NOTE : Capacity knobs (approved): typical_annual_capacity = p75 of observed 2025 Aetna ME visit volume
       per provider within state x specialty (4-state pooled p75 where a state x specialty has fewer
       than 20 providers); senior_saturation = provider percentile of tot_benes within state x
       cms_specialty among providers with tot_benes > 0; providers with tot_benes = 0 receive the
       state x specialty median saturation, flagged saturation_imputed. provider_slots =
       typical_annual_capacity x active_flag x (1 - senior_saturation).
Run  : python expanded_scope/35_dc_capacity.py
"""

import config as cfg

OUT1   = cfg.table("dc_provider_capacity")
OUT2   = cfg.table("dc_capacity")
PAR    = cfg.table("provider_par_flag")
CLAIMS = cfg.src("A870800_medicare_analysis_2025_claims")
DEMAND = cfg.table("dc_demand")

DDL_PROVIDER = f"""
CREATE OR REPLACE TABLE `{OUT1}`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
WITH provider_volume AS (
  SELECT
    CAST(srv_prvdr_id AS STRING) AS provider_id,
    COUNT(DISTINCT CONCAT(member_id, '|', CAST(srv_prvdr_id AS STRING), '|',
                          CAST(srv_start_dt AS STRING))) AS me_visits_2025
  FROM `{CLAIMS}`
  WHERE business_ln_cd = 'ME'
  GROUP BY 1
),
par AS (
  SELECT provider_id, plan_type, cms_specialty, state_cd, county_name, county_fips,
         aetna_par_flag, tot_benes
  FROM `{PAR}`
  GROUP BY 1, 2, 3, 4, 5, 6, 7, 8
),
spec_capacity_state AS (
  SELECT
    p.state_cd,
    p.cms_specialty,
    COUNT(DISTINCT p.provider_id) AS providers_with_volume,
    APPROX_QUANTILES(v.me_visits_2025, 100)[OFFSET(75)] AS p75_state
  FROM par p
  JOIN provider_volume v USING (provider_id)
  GROUP BY 1, 2
),
spec_capacity_all AS (
  SELECT
    p.cms_specialty,
    APPROX_QUANTILES(v.me_visits_2025, 100)[OFFSET(75)] AS p75_all
  FROM par p
  JOIN provider_volume v USING (provider_id)
  GROUP BY 1
),
saturation AS (
  SELECT
    provider_id,
    state_cd,
    cms_specialty,
    PERCENT_RANK() OVER (PARTITION BY state_cd, cms_specialty ORDER BY tot_benes) AS senior_saturation
  FROM (
    SELECT DISTINCT provider_id, state_cd, cms_specialty, tot_benes
    FROM par
    WHERE tot_benes > 0
  )
),
median_saturation AS (
  SELECT
    state_cd,
    cms_specialty,
    APPROX_QUANTILES(senior_saturation, 100)[OFFSET(50)] AS median_sat
  FROM saturation
  GROUP BY 1, 2
)
SELECT
  p.provider_id,
  p.plan_type,
  p.cms_specialty,
  p.state_cd,
  p.county_name,
  p.county_fips,
  p.aetna_par_flag AS active_flag,
  p.tot_benes,
  COALESCE(v.me_visits_2025, 0) AS me_visits_2025,
  CASE WHEN sc.providers_with_volume >= 20 THEN sc.p75_state ELSE sa.p75_all END AS typical_annual_capacity,
  COALESCE(sat.senior_saturation, ms.median_sat) AS senior_saturation,
  sat.provider_id IS NULL AS saturation_imputed,
  COALESCE(CASE WHEN sc.providers_with_volume >= 20 THEN sc.p75_state ELSE sa.p75_all END, 0)
    * p.aetna_par_flag
    * (1 - COALESCE(sat.senior_saturation, ms.median_sat, 0.5)) AS provider_slots
FROM par p
LEFT JOIN provider_volume v
  ON p.provider_id = v.provider_id
LEFT JOIN spec_capacity_state sc
  ON p.state_cd = sc.state_cd AND p.cms_specialty = sc.cms_specialty
LEFT JOIN spec_capacity_all sa
  ON p.cms_specialty = sa.cms_specialty
LEFT JOIN saturation sat
  ON p.provider_id = sat.provider_id AND p.state_cd = sat.state_cd
  AND p.cms_specialty = sat.cms_specialty
LEFT JOIN median_saturation ms
  ON p.state_cd = ms.state_cd AND p.cms_specialty = ms.cms_specialty
"""

DDL_COUNTY = f"""
CREATE OR REPLACE TABLE `{OUT2}`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
SELECT
  state_cd,
  county_fips,
  county_name,
  cms_specialty,
  plan_type,
  COUNT(DISTINCT provider_id) AS contracted_providers,
  COUNT(DISTINCT CASE WHEN active_flag = 1 THEN provider_id END) AS active_providers,
  SUM(provider_slots) AS capacity_visits
FROM `{OUT1}`
GROUP BY 1, 2, 3, 4, 5
"""

CHECKS_PROVIDER = {
    "saturation_imputed share per state":
        f"SELECT state_cd, COUNTIF(saturation_imputed) AS imputed, COUNT(*) AS row_count "
        f"FROM `{OUT1}` GROUP BY 1 ORDER BY 1",
    "active share per state":
        f"SELECT state_cd, COUNTIF(active_flag = 1) AS active, "
        f"COUNT(DISTINCT provider_id) AS providers FROM `{OUT1}` GROUP BY 1 ORDER BY 1",
    "typical capacity spot check (top 10 specialties by provider count, FL)":
        f"SELECT cms_specialty, COUNT(DISTINCT provider_id) AS providers, "
        f"ANY_VALUE(typical_annual_capacity) AS typ_cap FROM `{OUT1}` "
        f"WHERE state_cd = 'FL' GROUP BY 1 ORDER BY providers DESC LIMIT 10",
}

CHECKS_COUNTY = {
    "capacity vs market demand order of magnitude per state (cross-pillar sanity)":
        f"SELECT c.state_cd, CAST(SUM(c.capacity_visits) AS INT64) AS capacity, "
        f"(SELECT CAST(SUM(d.market_demand_visits) AS INT64) FROM `{DEMAND}` d "
        f"WHERE d.state_cd = c.state_cd) AS market_demand "
        f"FROM `{OUT2}` c GROUP BY 1 ORDER BY 1",
    "top 10 capacity cells":
        f"SELECT state_cd, county_name, cms_specialty, plan_type, "
        f"CAST(capacity_visits AS INT64) AS capacity FROM `{OUT2}` "
        f"ORDER BY capacity_visits DESC LIMIT 10",
}


def main():
    cfg.run_ddl(DDL_PROVIDER, CHECKS_PROVIDER)
    cfg.run_ddl(DDL_COUNTY, CHECKS_COUNTY)


if __name__ == "__main__":
    main()
