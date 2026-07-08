"""
36 - ms_dc_gap   [PYTHON runner / BigQuery DDL]   *** DEMAND/CAPACITY EXTENSION -- M7 ***

WHAT : Gap. One row per state_cd x county_fips x cms_specialty x plan_type (the
       compliance fact's grid) with demand, capacity, gap, gap_status, and the
       compliant-but-strained risk_flag.
WHY  : The end product of the demand/capacity extension; joins compliance,
       demand, and capacity into one decision table.
SOURCE: ms_fact_gap_analysis + ms_dc_demand + ms_dc_capacity
GRAIN : state_cd x county_fips x cms_specialty x plan_type
NOTE : Gap is like-for-like: ma_demand_visits minus capacity_visits (both built from the Aetna member
       population and observed-visit ruler). market_demand_visits appears as context only via
       market_opportunity_ratio; a subtraction against market demand is not meaningful because capacity
       is measured on the Aetna-observed ruler. Demand is at county x specialty_ctg_cd and is joined to
       the compliance grid by county only; the specialty taxonomies (specialty_ctg_cd vs cms_specialty)
       do not map 1:1, so demand columns are NULL where no category matches the CMS specialty name.
Run  : python expanded_scope/36_dc_gap.py
"""

import config as cfg

OUT  = cfg.table("dc_gap")
FACT = cfg.table("fact_gap_analysis")
DEM  = cfg.table("dc_demand")
CAP  = cfg.table("dc_capacity")

DDL = f"""
CREATE OR REPLACE TABLE `{OUT}`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
WITH demand_bridged AS (
  SELECT
    d.county_fips,
    UPPER(d.specialty_desc) AS specialty_key,
    d.market_demand_visits,
    d.ma_demand_visits
  FROM `{DEM}` d
)
SELECT
  f.state_cd,
  f.county_fips,
  f.county_name,
  f.county_type,
  f.cms_specialty,
  f.plan_type,
  f.compliance_status,
  f.access_compliant,
  f.count_compliant,
  f.required_provider_count,
  f.actual_count,
  f.provider_gap,
  d.market_demand_visits,
  d.ma_demand_visits,
  c.contracted_providers,
  c.active_providers,
  COALESCE(c.capacity_visits, 0) AS capacity_visits,
  d.ma_demand_visits - COALESCE(c.capacity_visits, 0) AS demand_capacity_gap,
  SAFE_DIVIDE(COALESCE(c.capacity_visits, 0), d.market_demand_visits) AS market_opportunity_ratio,
  CASE
    WHEN d.ma_demand_visits IS NULL THEN 'NO_DEMAND_MAPPING'
    WHEN d.ma_demand_visits - COALESCE(c.capacity_visits, 0) > 0.2 * d.ma_demand_visits THEN 'DESERT'
    WHEN COALESCE(c.capacity_visits, 0) > 1.5 * d.ma_demand_visits THEN 'OVERSUPPLY'
    ELSE 'BALANCED'
  END AS gap_status,
  (f.compliance_status = 'COMPLIANT'
   AND d.ma_demand_visits IS NOT NULL
   AND d.ma_demand_visits - COALESCE(c.capacity_visits, 0) > 0) AS risk_flag
FROM `{FACT}` f
LEFT JOIN demand_bridged d
  ON f.county_fips = d.county_fips AND UPPER(f.cms_specialty) = d.specialty_key
LEFT JOIN `{CAP}` c
  ON f.county_fips = c.county_fips AND f.cms_specialty = c.cms_specialty
  AND f.plan_type = c.plan_type
"""

CHECKS = {
    "gap_status mix per state":
        f"SELECT state_cd, gap_status, COUNT(*) AS cells FROM `{OUT}` "
        f"GROUP BY 1, 2 ORDER BY 1, 2",
    "risk_flag count per state (the headline: compliant but strained)":
        f"SELECT state_cd, COUNTIF(risk_flag) AS compliant_but_strained, COUNT(*) AS cells "
        f"FROM `{OUT}` GROUP BY 1 ORDER BY 1",
    "specialty bridge coverage (share of cells with demand mapped)":
        f"SELECT state_cd, COUNTIF(ma_demand_visits IS NOT NULL) AS mapped, COUNT(*) AS cells "
        f"FROM `{OUT}` GROUP BY 1 ORDER BY 1",
    "top 15 deserts by absolute gap":
        f"SELECT state_cd, county_name, cms_specialty, plan_type, "
        f"CAST(ma_demand_visits AS INT64) AS ma_demand, "
        f"CAST(capacity_visits AS INT64) AS capacity, "
        f"CAST(demand_capacity_gap AS INT64) AS gap FROM `{OUT}` "
        f"WHERE gap_status = 'DESERT' ORDER BY demand_capacity_gap DESC LIMIT 15",
    "compliant-but-strained examples (top 10 by gap)":
        f"SELECT state_cd, county_name, cms_specialty, plan_type, "
        f"CAST(demand_capacity_gap AS INT64) AS gap FROM `{OUT}` "
        f"WHERE risk_flag ORDER BY demand_capacity_gap DESC LIMIT 10",
}


def main():
    cfg.run_ddl(DDL, CHECKS)


if __name__ == "__main__":
    main()
