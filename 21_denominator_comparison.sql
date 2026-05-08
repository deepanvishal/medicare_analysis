-- ============================================================
-- PURPOSE: Demonstrate impact of switching pct_covered
--          denominator from ACS all-ages population (as-is)
--          to Medicare-eligible population (to-be).
-- CONTEXT: Code review point 4 — county_eligibles is stored
--          in stg_beneficiaries but unused in Test 1.
--          Current pct_covered uses zip_population (ACS 2018
--          all-ages). CMS 422.116 evaluates access for
--          Medicare enrollees, not general population.
-- METHOD:  Distribute county_eligibles to zips proportionally
--          by each zip's share of county total_population.
-- GRAIN:   One row per county (for chosen specialty + plan)
-- SWAP:    Change cms_specialty and plan_type filters as needed
-- ============================================================

WITH zip_base AS (
  SELECT
    b.zip_code,
    b.county_name,
    b.county_fips,

    -- as-is denominator: ACS 2018 all-ages population
    b.total_population                                                    AS asis_denominator,

    -- to-be denominator: county Medicare eligibles distributed to zips
    -- proportionally by each zip's share of county total population
    ROUND(
      b.total_population
        * b.county_eligibles
        / NULLIF(SUM(b.total_population) OVER (PARTITION BY b.county_fips), 0)
    , 0)                                                                  AS tobe_denominator,

    COALESCE(z.has_access, FALSE)                                         AS has_access
  FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_stg_beneficiaries` b
  LEFT JOIN `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_fact_zip_access_v2` z
    ON  b.zip_code      = z.bene_zip
    AND z.cms_specialty = 'Cardiology'
    AND z.plan_type     = 'MA-HMO'
)

SELECT
  county_name,

  -- as-is
  SUM(CASE WHEN has_access THEN asis_denominator ELSE 0 END)             AS asis_numerator,
  SUM(asis_denominator)                                                   AS asis_denominator,
  ROUND(
    SUM(CASE WHEN has_access THEN asis_denominator ELSE 0 END)
    / NULLIF(SUM(asis_denominator), 0)
  , 4)                                                                    AS asis_pct_covered,

  -- to-be
  SUM(CASE WHEN has_access THEN tobe_denominator ELSE 0 END)             AS tobe_numerator,
  SUM(tobe_denominator)                                                   AS tobe_denominator,
  ROUND(
    SUM(CASE WHEN has_access THEN tobe_denominator ELSE 0 END)
    / NULLIF(SUM(tobe_denominator), 0)
  , 4)                                                                    AS tobe_pct_covered

FROM zip_base
GROUP BY county_name, county_fips
ORDER BY county_name
LIMIT 20;
