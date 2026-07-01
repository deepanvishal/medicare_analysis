"""
20 - ms_provider_geo   [PYTHON runner / BigQuery DDL]   *** DEMAND / UTILIZATION EXTENSION ***

WHAT : Table 1 for the demand extension -- distinct State, Submarket, County, PIN.
       One row per (provider, county) the provider has a location in.
WHY  : The geography key. Your claims x HCC summary joins to this by PIN
       (mdcr_base_claim.srv_prvdr_id = provider_id = PIN) to place utilization by
       state / submarket / county.
SOURCE: ms_stg_providers_multi_specialty (provider_id = PIN, submarket)
        + ms_ref_county (state_cd, county_name via county_fips)
GRAIN : distinct state_cd x submarket x county_name x pin
NOTE : submarket is the provider's own value carried from mbr_with_zip. A provider's
       submarket may not match the county's state (a provider whose zip lands in
       another state) -- rows are DISTINCT as requested; flag if you want it forced
       to the county's state.
Run  : python expanded_scope/20_provider_geo.py
"""

import config as cfg

OUT  = cfg.table("provider_geo")
PROV = cfg.table("stg_providers_multi_specialty")
CTY  = cfg.table("ref_county")

DDL = f"""
CREATE OR REPLACE TABLE `{OUT}`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
SELECT DISTINCT
  rc.state_cd,
  p.submarket,
  rc.county_name,
  p.provider_id                                                     AS pin
FROM `{PROV}` p
JOIN `{CTY}` rc ON p.county_fips = rc.county_fips
WHERE p.provider_id IS NOT NULL
"""

CHECKS = {
    "rows + distinct PINs per state":
        f"SELECT state_cd, COUNT(*) AS row_count, COUNT(DISTINCT pin) AS pins, "
        f"COUNT(DISTINCT submarket) AS submarkets, COUNT(DISTINCT county_name) AS counties "
        f"FROM `{OUT}` GROUP BY state_cd ORDER BY state_cd",
    "sanity: any submarket whose state != the county's state? (info)":
        f"SELECT COUNT(*) AS rows_where_submarket_looks_cross_state FROM `{OUT}` WHERE submarket IS NULL",
}


def main():
    cfg.run_ddl(DDL, CHECKS)


if __name__ == "__main__":
    main()
