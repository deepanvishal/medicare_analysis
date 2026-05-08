# Medicare Network Adequacy & Capacity Modeling
## Technical Steps

---

## File Structure

| File | Role | Run Order |
|------|------|-----------|
| `13_load_hsd_to_bq.ipynb` | Prerequisite — loads raw CMS 2026 HSD Excel into BigQuery staging tables (`ref_hsd_provider_min`, `ref_hsd_facility_min`) | 1st |
| `14_reference_file.sql` | Prerequisite — creates `ref_hsd_required_counts` (2,881 rows: 67 counties × 43 specialties with exact CMS required counts) | 2nd |
| `17_specialty_cd_based_report.sql` | Main pipeline — all 10 steps from reference tables through final compliance output | 3rd |
| `19_tab1_tab2.py` | Report generator — queries `fact_gap_analysis_v2` and builds the formatted Excel workbook | 4th |

---

## Architecture Overview

```
CMS 2026 HSD Reference File (Excel)
        ↓
13_load_hsd_to_bq.ipynb  →  ref_hsd_provider_min / ref_hsd_facility_min
        ↓
14_reference_file.sql    →  ref_hsd_required_counts (exact county × specialty counts)
        ↓
17_specialty_cd_based_report.sql
  ├── ref_specialty_crosswalk_expanded  (442 rows — specialty_cd level)
  ├── ref_specialty_crosswalk           (43 rows  — specialty_ctg_cd level)
  ├── ref_time_distance                 (215 rows — max distance per specialty × county type)
  ├── ref_county_classification         (67 FL counties with type + compliance threshold)
  ├── ref_zip_reference                 (FL zip centroids, area, county mapping)
  ├── ref_county_name_crosswalk         (Aetna → Census county name + FIPS)
  ├── stg_beneficiaries                 (demand: zip population + county context)
  ├── stg_providers_multi_specialty_v2  (supply: provider × specialty × plan type)
  ├── fact_zip_access_v2                (distance matrix + has_access flag per zip)
  └── fact_gap_analysis_v2              (county compliance: pct_covered, gap, status)
        ↓
19_tab1_tab2.py  →  medicare_supply_demand.xlsx
```

---

## Table Build Order (17_specialty_cd_based_report.sql)

| Step | Table | Type | Purpose | Key Sources |
|------|-------|------|---------|-------------|
| 0 | `ref_specialty_crosswalk_expanded` | Reference | Maps CMS specialties to Aetna `specialty_cd` codes (raw code level). 442 rows — all known Aetna codes per CMS specialty. Used for provider lookup in `stg_providers_multi_specialty_v2` | Manually coded from Global Lookup Table (SPECIALTY_CD) + 42 CFR 422.116 |
| 1 | `ref_specialty_crosswalk` | Reference | Maps CMS specialties to Aetna `specialty_ctg_cd` (category code). 43 rows — one category code per CMS specialty. Used in `fact_gap_analysis_v2` all_combinations CTE | Manually coded from 42 CFR 422.116 + Aetna Global Lookup |
| 2 | `ref_time_distance` | Reference | Max time + distance thresholds per specialty × county type (215 rows). Also stores `min_ratio_per_1000` for reference | 42 CFR 422.116 Table 1 |
| 3 | `ref_county_classification` | Reference | Florida county type classification (Large Metro / Metro / Micro / Rural / CEAC) + compliance threshold (90% or 85%) | `bigquery-public-data.geo_us_boundaries.counties` + `census_bureau_acs.county_2020_5yr` |
| 4 | `ref_zip_reference` | Reference | Florida zip code centroids (lat/long), area, radius, county mapping via spatial intersection | `geo_us_boundaries.zip_codes` + `census_bureau_acs.zip_codes_2018_5yr` + `geo_us_boundaries.counties` + `ref_county_classification` |
| 5 | `ref_county_name_crosswalk` | Reference | Maps Aetna county names to Census county names + FIPS. Covers 38 exact matches, 3 name mismatches, 26 no-coverage counties | Manually coded from Aetna vs Census county comparison |
| 6 | `ref_hsd_required_counts` | Reference | **Prerequisite — created by `14_reference_file.sql`, not this file.** Verify exists before proceeding. Exact CMS required counts per county × specialty (2,881 rows) | CMS 2026 HSD Reference File loaded via `13_load_hsd_to_bq.ipynb` + `14_reference_file.sql` |
| 7 | `stg_beneficiaries` | Staging | Demand side — one row per FL zip with ACS population, county context, and CMS Medicare eligibles | `ref_zip_reference` + `anbc-hcb-prod.*.cms_medicare_penetration` |
| 8 | `stg_providers_multi_specialty_v2` | Staging | Supply side — one row per provider × CMS specialty × plan type. Explodes multi-specialty providers via network_id + RPDB join | `A870800_medicare_supply_demand_mbr_with_zip` + `edp-prod-hcbstorage.*` (RPDB_RPNPRAC, EPDB_PRVDR, RPDB_RINPR, PRVDR_TY_X_SPCLTY, GLOBAL_LOOKUP) + `ref_specialty_crosswalk_expanded` + `ref_county_name_crosswalk` + `ref_zip_reference` |
| 9 | `fact_zip_access_v2` | Fact | For each bene zip × CMS specialty × plan type: count distinct providers within CMS distance threshold, flag `has_access = TRUE`. Sparse table — only zips with at least 1 provider stored | `stg_beneficiaries` + `ref_zip_reference` + `stg_providers_multi_specialty_v2` + `ref_time_distance` |
| 10 | `fact_gap_analysis_v2` | Fact | County-level compliance output. Builds complete county × specialty × plan type grid, rolls up zip access to county pct_covered, joins exact HSD required counts, evaluates both compliance tests | `stg_beneficiaries` + `ref_zip_reference` + `stg_providers_multi_specialty_v2` + `ref_specialty_crosswalk` + `ref_time_distance` + `ref_hsd_required_counts` + `fact_zip_access_v2` + `hosp_list_cmi` |

---

## Key SQL Logic

### County Type Classification
```sql
CASE
  WHEN (population >= 1000000 AND pop_density >= 1000)
    OR (population >= 500000  AND pop_density >= 1500)
    OR (pop_density >= 5000)                          THEN 'Large Metro'
  WHEN (population >= 1000000 AND pop_density >= 10)
    OR (population >= 500000  AND pop_density >= 10)
    OR (population >= 200000  AND pop_density >= 10)
    OR (population >= 50000   AND pop_density >= 100)
    OR (population >= 10000   AND pop_density >= 1000) THEN 'Metro'
  WHEN (population >= 50000   AND pop_density >= 10)
    OR (population >= 10000   AND pop_density >= 50)  THEN 'Micro'
  WHEN pop_density < 10                               THEN 'CEAC'
  WHEN (population >= 10000   AND pop_density >= 10)
    OR (population < 10000    AND pop_density >= 50)  THEN 'Rural'
  ELSE                                                     'Rural'
END
```

### Distance Calculation (meters to miles)
```sql
ST_DISTANCE(
  ST_GEOGPOINT(bene_zip_long, bene_zip_lat),
  ST_GEOGPOINT(provider_zip_long, provider_zip_lat)
) / 1609.34 AS distance_miles
```

### Core Distance Filter
```sql
WHERE ST_DISTANCE(
        ST_GEOGPOINT(bene_zip.zip_long, bene_zip.zip_lat),
        ST_GEOGPOINT(p.zip_long, p.zip_lat)
      ) / 1609.34 <= ref_time_distance.max_distance_miles
```

### Required Provider Count
```sql
-- Exact counts from CMS 2026 HSD Reference File — no approximation
required_count = ref_hsd_required_counts.required_count

-- For Acute Inpatient Hospitals: bed count used instead of provider count
actual_count = SUM(hosp_list_cmi.Beds) WHERE cms_specialty = 'Acute Inpatient Hospitals'
```

### Compliance Tests
```sql
-- Test 1: Access
pct_covered = SUM(zip_population WHERE has_access) / SUM(zip_population)
access_compliant = pct_covered >= compliance_threshold  -- 0.90 (Large Metro/Metro) or 0.85 (Micro/Rural/CEAC)

-- Test 2: Count
count_compliant = actual_count >= required_provider_count  -- from ref_hsd_required_counts

-- Overall: both tests must pass per 42 CFR 422.116
compliance_status = CASE WHEN access_compliant AND count_compliant
                    THEN 'COMPLIANT' ELSE 'NON-COMPLIANT' END
```

---

## Key Joins

| Join | Left Table | Right Table | Join Key | Purpose |
|------|-----------|-------------|----------|---------|
| Specialty mapping (provider) | `stg_providers_multi_specialty_v2` | `ref_specialty_crosswalk_expanded` | `specialty_cd = aetna_code` | Map raw Aetna specialty codes to CMS specialty names |
| County name fix | `stg_providers_multi_specialty_v2` | `ref_county_name_crosswalk` | `county_nm = aetna_county_nm` | Resolve 3 known name mismatches + get county_fips |
| Zip geo | `stg_providers_multi_specialty_v2` / `stg_beneficiaries` | `ref_zip_reference` | `zip_cd = zip_code` | Get lat/long for distance calculation |
| Distance filter | `stg_beneficiaries` | `stg_providers_multi_specialty_v2` | `ST_DISTANCE <= max_distance_miles` | Core compliance filter |
| Threshold lookup | distance matrix | `ref_time_distance` | `cms_specialty + county_type` | Get max allowed distance per specialty × county type |
| HSD required count | `fact_gap_analysis_v2` | `ref_hsd_required_counts` | `county_name + cms_specialty` | Get exact CMS required provider/facility count |
| Hospital beds | `stg_providers_multi_specialty_v2` | `hosp_list_cmi` | `provider_id = Pin` | Get contracted bed count for Acute Inpatient compliance |
| All combinations grid | `stg_beneficiaries` | `ref_specialty_crosswalk` + `stg_providers_multi_specialty_v2` | CROSS JOIN | Ensure zips with zero providers are included in rollup |

---

## BigQuery Dataset Details

| Environment | Project | Dataset |
|-------------|---------|---------|
| Development | `anbc-hcb-dev` | `provider_ds_netconf_data_hcb_dev` |
| Production source | `anbc-hcb-prod` | `provider_ds_netconf_data_hcb_prod` |
| EDP source systems | `edp-prod-hcbstorage` | `edp_hcb_core_cnsv` / `edp_hcb_core_srcv` |
| Public geo | `bigquery-public-data` | `geo_us_boundaries` |
| Public census | `bigquery-public-data` | `census_bureau_acs` |

All output tables prefixed: `A870800_medicare_supply_demand_`

---

## Prerequisite Tables (must exist before running step 7+)

| Table | Created By | Notes |
|-------|-----------|-------|
| `A870800_medicare_supply_demand_ref_hsd_required_counts` | `14_reference_file.sql` | Run once per CMS HSD cycle. 2,881 rows (67 counties × 43 specialties) |
| `A870800_medicare_supply_demand_mbr_with_zip` | External upstream process | Aetna provider network + zip join. Not created by any file in this repo |
| `hosp_list_cmi` | Loaded separately | Hospital bed counts by provider PIN |
