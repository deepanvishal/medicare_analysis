# CLAUDE.md — Medicare Supply Demand

## Project Overview

Medicare network adequacy and capacity modeling for Aetna Medicare Advantage (MA) plans in Florida.
Evaluates whether the contracted provider network meets CMS 42 CFR 422.116 requirements across
67 Florida counties × 43 CMS specialties × 2 plan types (MA-HMO, MA-PPO).

**Regulation:** 42 CFR 422.116 — Network Adequacy Standards
**Reference File:** CMS 2026 HSD Reference File (published December 17, 2025)
**Plan Year:** 2026

---

## BigQuery Configuration

```
Table Project:   anbc-hcb-dev
Dataset:         provider_ds_netconf_data_hcb_dev
Table Prefix:    A870800_medicare_supply_demand_
Billing Project: anbc-dev-prv-nc-ds   ← always use this for client = bigquery.Client(project=...)
```

**Full table reference pattern:**
```
`anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_{table_name}`
```

**External source tables:**
```
edp-prod-hcbstorage.edp_hcb_core_cnsv.RPDB_RPNPRAC          ← multi-specialty network table
edp-prod-hcbstorage.edp_hcb_core_srcv.EPDB_PRVDR             ← provider base table
edp-prod-hcbstorage.edp_hcb_core_srcv.RPDB_RINPR             ← network participation
edp-prod-hcbstorage.edp_hcb_core_cnsv.PRVDR_TY_X_SPCLTY      ← specialty crosswalk
edp-prod-hcbstorage.edp_hcb_core_srcv.GLOBAL_LOOKUP          ← code descriptions
anbc-hcb-prod.provider_ds_netconf_data_hcb_prod.cms_medicare_penetration  ← beneficiaries
anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_mbr_with_zip
anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.hosp_list_cmi  ← hospital beds (Pin, Beds)
bigquery-public-data.geo_us_boundaries.counties
bigquery-public-data.geo_us_boundaries.zip_codes
bigquery-public-data.census_bureau_acs.county_2020_5yr
bigquery-public-data.census_bureau_acs.zip_codes_2018_5yr
```

---

## File Structure

| File | Role | Run Order |
|------|------|-----------|
| `13_load_hsd_to_bq.ipynb` | Loads raw CMS 2026 HSD Excel → BigQuery staging tables (`ref_hsd_provider_min`, `ref_hsd_facility_min`) | 1st |
| `14_reference_file.sql` | Creates `ref_hsd_required_counts` (2,881 rows: 67 counties × 43 specialties with exact CMS required counts) | 2nd |
| `17_specialty_cd_based_report.sql` | Main pipeline — all 10 steps from reference tables through final compliance output | 3rd |
| `19_tab1_tab2.py` | Report generator — queries `fact_gap_analysis_v2` and builds formatted Excel workbook | 4th |

---

## Pipeline Execution Order

Run steps in order. Each step depends on previous steps.

```
Step 0:  ref_specialty_crosswalk_expanded    ← 442 Aetna specialty_cd → 43 CMS specialties
Step 1:  ref_specialty_crosswalk             ← 43 rows (specialty_ctg_cd); used in fact_gap_analysis_v2 all_combinations CTE
Step 2:  ref_time_distance                   ← CMS max time/distance + min_ratio_per_1000 per specialty × county type
Step 3:  ref_county_classification           ← 67 FL counties → Large Metro/Metro/Micro/Rural/CEAC
Step 4:  ref_zip_reference                   ← zip centroids, area_sq_miles, radius, county mapping
Step 5:  ref_county_name_crosswalk           ← Aetna vs Census county name reconciliation
Step 6:  ref_hsd_required_counts             ← PREREQUISITE: created by 14_reference_file.sql (not this file).
                                                Must exist before running steps 7+.
Step 7:  stg_beneficiaries                   ← demand side: zip population + Medicare beneficiaries
Step 8:  stg_providers_multi_specialty_v2    ← supply side: all specialties per provider via RPDB explosion
Step 9:  fact_zip_access_v2                  ← distance matrix: has_access per member zip × specialty × plan type
Step 10: fact_gap_analysis_v2               ← county compliance rollup: Test 1 + Test 2
```

**If only specialty mapping changes:** re-run Steps 0 → 8 → 9 → 10
**If ref_time_distance changes:** re-run Steps 2 → 9 → 10
**If HSD file changes:** re-run 13_load_hsd_to_bq.ipynb → 14_reference_file.sql → Step 10

---

## Table Descriptions & Grain

### Reference Tables

| Table | Grain | Key Columns |
|-------|-------|-------------|
| ref_specialty_crosswalk_expanded | cms_specialty × aetna_code | cms_specialty, aetna_code, aetna_description |
| ref_specialty_crosswalk | aetna_cd | aetna_cd, cms_specialty |
| ref_time_distance | cms_specialty × county_type | cms_specialty, county_type, max_time_min, max_distance_miles, min_ratio_per_1000 |
| ref_county_classification | county_fips | county_fips, county_name, county_type, compliance_threshold |
| ref_zip_reference | zip_code | zip_code, zip_lat, zip_long, zip_radius_miles, area_sq_miles, county_fips |
| ref_county_name_crosswalk | aetna_county_nm | aetna_county_nm, census_county_nm, county_fips |
| ref_hsd_required_counts | county_name × cms_specialty | county_name, county_type, cms_specialty, total_beneficiaries, ratio_95th_percentile, beneficiaries_required_to_cover, required_count |

### Staging Tables

| Table | Grain | Key Columns |
|-------|-------|-------------|
| stg_beneficiaries | zip_code | zip_code, county_fips, county_name, county_type, compliance_threshold, total_population, zip_radius_miles, county_eligibles, county_ma_enrolled |
| stg_providers_multi_specialty_v2 | provider_id × cms_specialty × plan_type | provider_id, zip_cd, zip_lat, zip_long, zip_radius_miles, cms_specialty, plan_type, county_fips |

**Note:** `stg_beneficiaries` does NOT store zip_lat/zip_long. Lat/long are joined from `ref_zip_reference` at query time in `fact_zip_access_v2` and `fact_gap_analysis_v2`.

### Fact Tables

| Table | Grain | Key Columns |
|-------|-------|-------------|
| fact_zip_access_v2 | bene_zip × cms_specialty × plan_type | bene_zip, bene_county_fips, bene_county_name, bene_county_type, cms_specialty, plan_type, has_access, provider_count_within_threshold |
| fact_gap_analysis_v2 | county_name × cms_specialty × plan_type | county_name, county_type, cms_specialty, plan_type, county_total_beneficiaries, ratio_95th_percentile, beneficiaries_required_to_cover, min_ratio_per_1000, required_provider_count, compliance_threshold, max_distance_miles, total_county_population, population_with_access, pct_covered, actual_count, total_contracted_beds, provider_gap, access_compliant, count_compliant, compliance_status |

---

## Business Rules

### Two Compliance Tests (Both Must Pass)

**Test 1 — Access Standard (422.116(d)(4)):**
```
pct_covered = population_with_access / total_county_population
PASS if pct_covered >= compliance_threshold

compliance_threshold:
  Large Metro → 0.90
  Metro       → 0.90
  Micro       → 0.85
  Rural       → 0.85
  CEAC        → 0.85
```

**Test 2 — Minimum Count Standard (422.116(e)(3)):**
```
required_count = ref_hsd_required_counts.required_count  ← use directly, do not recalculate
PASS if actual_count >= required_count

actual_count:
  - All specialties except Acute Inpatient: COUNT(DISTINCT provider_id) within max_distance_miles
  - Acute Inpatient Hospitals: SUM(Beds) from hosp_list_cmi

Provider counts toward minimum ONLY if within max_distance_miles of at least 1 member zip
Per 422.116(e)(1)(i)
```

**Overall:**
```
compliance_status = 'COMPLIANT' if access_compliant = TRUE AND count_compliant = TRUE
                  = 'NON-COMPLIANT' otherwise
```

### Key Formulas

```
beneficiaries_required_to_cover = ratio_95th_percentile × total_beneficiaries
  (ratio_95th_percentile ≈ 0.1059 for Metro — from HSD file, varies by county type)

required_count → read directly from ref_hsd_required_counts.required_count
  (CMS pre-calculates this using their 95th percentile method — do not re-derive)

Acute Inpatient required_count = CEIL(12.2 × beneficiaries_required_to_cover / 1000)
  (beds, not hospitals)

distance_miles = ST_DISTANCE(
  ST_GEOGPOINT(bene_zip_long, bene_zip_lat),
  ST_GEOGPOINT(provider_zip_long, provider_zip_lat)
) / 1609.34
```

### Facility vs Provider Specialties

```
Provider specialties (29): ratio-based required_count from ref_hsd_required_counts
Facility specialties (13): required_count = 1 (flat minimum per county per 422.116(e)(2)(iii))
  → SNF, ASC, Radiology, PT, OT, Speech, Cardiac Surgery, ICU, Mammography,
    Inpatient Psych, Outpatient Infusion, Outpatient BH, Cardiac Cath
Acute Inpatient (1): beds-based, ratio = 12.2 per 1000
```

---

## Specialty Mapping

**Approach (current):**
```
specialty_cd (raw code from RPDB_RPNPRAC) → ref_specialty_crosswalk_expanded → cms_specialty
```

**Source file:** `cms_to_aetna_final (2).csv` — specialty mapping reference

**Excluded codes:**
- Pediatric codes (stripped — MA is 65+/disabled, not pediatric)
- Telehealth codes (91175, Behavioral Health Services Tel)
- Palliative care codes (2PLMD, 91001) — not a CMS specialty
- Generic catch-all: PH (Physician, 135K providers) — not mappable to specific CMS specialty
- Hospice surgery codes (30811)

---

## Column Naming Conventions

```
provider_id             ← CAST(prvdr_id_no AS STRING) from source
zip_cd                  ← provider zip (5-digit)
zip_code                ← member/beneficiary zip (5-digit)
bene_zip                ← member zip in fact_zip_access
bene_county_fips        ← member county FIPS
county_fips             ← 5-digit FIPS code
county_type             ← Large Metro | Metro | Micro | Rural | CEAC
compliance_threshold    ← 0.90 or 0.85 (float, not %)
pct_covered             ← float 0-1 (not %)
has_access              ← BOOLEAN
access_compliant        ← BOOLEAN
count_compliant         ← BOOLEAN
compliance_status       ← 'COMPLIANT' | 'NON-COMPLIANT' (exact strings)
plan_type               ← 'MA-HMO' | 'MA-PPO' (exact strings)
cms_specialty           ← exact string matching ref_specialty_crosswalk_expanded.cms_specialty
actual_count            ← COUNT(DISTINCT provider_id) OR SUM(Beds) for hospitals
total_contracted_beds   ← NULL for non-hospital rows, integer for Acute Inpatient
provider_gap            ← required_count - actual_count (negative = surplus)
min_ratio_per_1000      ← NULL for facility types (min=1 flat), 12.2 for Acute Inpatient
```

---

## Known Limitations & Assumptions

| # | Limitation | Impact | Fix |
|---|-----------|--------|-----|
| 1 | Straight-line distance, not drive time | Underestimates travel in rural/CEAC counties | Drive time API |
| 2 | ACS 2018 all-ages zip population for Test 1 | Not Medicare-specific; retirement clusters understated | Zip-level Medicare beneficiary distribution |
| 3 | Zip centroid for provider location | All providers in same zip treated as equidistant | Actual address geocoding |
| 4 | Zip centroid for beneficiary location | Not individual member address | ZIP+4 precision |
| 5 | Telehealth credit NOT applied | Some borderline NON-COMPLIANT may actually pass | Need telehealth flag in provider file |
| 6 | 26 FL counties have no Aetna providers | May get access from neighboring county | Validate in output |
| 7 | County type derived from Census ACS, not CMS published list | May differ from CMS's official classification | Load from HSD file county_type column |
| 8 | PH (Physician) excluded (135K providers) | Generic catch-all — unmappable | Data-driven specialty validation |

---

## distinct_providers CTE (Critical Fix)

`fact_gap_analysis_v2` uses a `distinct_providers` CTE for Test 2 actual_count.

**Why:** `fact_zip_access_v2` stores `provider_count_within_threshold` per bene_zip — summing this double-counts providers serving multiple zips.

**Fix:** Go back to source tables, re-apply distance filter, COUNT(DISTINCT provider_id) per county:

```sql
distinct_providers AS (
  SELECT
    b.county_fips,
    p.cms_specialty,
    p.plan_type,
    COUNT(DISTINCT p.provider_id) AS actual_provider_count
  FROM stg_beneficiaries b
  JOIN ref_zip_reference bene_zip ON b.zip_code = bene_zip.zip_code
  JOIN stg_providers_multi_specialty_v2 p ON TRUE
  JOIN ref_time_distance t ON t.cms_specialty = p.cms_specialty AND t.county_type = b.county_type
  WHERE ST_DISTANCE(
    ST_GEOGPOINT(bene_zip.zip_long, bene_zip.zip_lat),
    ST_GEOGPOINT(p.zip_long, p.zip_lat)
  ) / 1609.34 <= t.max_distance_miles
  GROUP BY b.county_fips, p.cms_specialty, p.plan_type
)
```

---

## Output Files

| File | Description |
|------|-------------|
| `17_specialty_cd_based_report.sql` | Full pipeline Steps 0-10 |
| `14_reference_file.sql` | ref_hsd_required_counts (prerequisite) |
| `13_load_hsd_to_bq.ipynb` | Jupyter notebook for HSD file load |
| `19_tab1_tab2.py` | Python Excel report generator |
| `cms_to_aetna_final (2).csv` | Specialty mapping reference |
| `medicare_supply_demand_v2 (1).xlsx` | Excel compliance report |
| `medicare_supply_demand.pptx` | Presentation deck |
| `medicare_supply_demand_report.docx` | Technical Word document |

---

## Excel Report Structure (19_tab1_tab2.py)

```
Tab 1: Project Overview (static — methodology, assumptions, specialty mapping)
Tab 2: Compliance Report (from fact_gap_analysis_v2)
Tab 3: Executive Summary of violations
Tab 4: County-level risk summary
Tab 5: Methodology appendix
Tab 6: Data decisions & limitations
```

**BQ client setup:**
```python
PROJECT        = "anbc-hcb-dev"
CLIENT_PROJECT = "anbc-dev-prv-nc-ds"
DATASET        = "provider_ds_netconf_data_hcb_dev"
PREFIX         = "A870800_medicare_supply_demand"

client = bigquery.Client(project=CLIENT_PROJECT)
```

---

## Code Style

- No fluff. No unnecessary commenting. No emojis in comments.
- Confirm before writing or changing code.
- If assumptions were made, flag them explicitly.
- Never fabricate BigQuery results — flag if data is needed from BQ.
- Always read existing code before editing — never guess column names.
- Use `COALESCE(col, 0)` for numeric nulls in final SELECT.
- Use `TRIM(CAST(col AS STRING))` for joins on code columns.
- All SQL table references use full path: `project.dataset.table`.
- Python uses `google-cloud-bigquery` and `openpyxl`.

---

## 43 CMS Specialties

```
Provider (29):
  Primary Care, Allergy and Immunology, Cardiology, Chiropractor,
  Clinical Psychology, Clinical Social Work, Dermatology, Endocrinology,
  ENT/Otolaryngology, Gastroenterology, General Surgery, Gynecology OB/GYN,
  Infectious Diseases, Nephrology, Neurology, Neurosurgery,
  Oncology Medical/Surgical, Oncology Radiation, Ophthalmology,
  Orthopedic Surgery, Physiatry Rehabilitative Med, Plastic Surgery,
  Podiatry, Psychiatry, Pulmonology, Rheumatology, Urology,
  Vascular Surgery, Cardiothoracic Surgery

Facility (13) — minimum 1 per county:
  Cardiac Surgery Program, Cardiac Catheterization, Critical Care ICU,
  Surgical Services ASC, Skilled Nursing Facility, Diagnostic Radiology,
  Mammography, Physical Therapy, Occupational Therapy, Speech Therapy,
  Inpatient Psychiatric, Outpatient Infusion/Chemo, Outpatient Behavioral Health

Beds-based (1):
  Acute Inpatient Hospitals (12.2 beds per 1,000 beneficiaries)
```

---

## Project Delivery Plan

### Project Objective

Build analytic models to determine whether the provider network has the right capacity, specialties, and geographic distribution, and to identify where to add, remove, or reconfigure providers under multiple demand, regulatory, and scenario assumptions.

This plan explicitly incorporates:
- CMS MA Network Adequacy Standards (time & distance, specialty lists)
- County-based requirements (critical for Medicare)
- Utilization patterns (higher chronic, specialty-heavy demand) — next phase
- Provider participation nuances (accepting MA ≠ participating in all products) — next phase
- Facility-based access expectations (hospitals, SNF, dialysis) — next phase

Items marked 'next phase' are scoped for Weeks 5+ and are not part of the current 4-week delivery plan.

---

### Weeks 1-2 — Medicare Scope, Rules & Success Criteria

**Objectives:** Lock Medicare-specific requirements upfront to avoid rework.

| Deliverable | Status | Output |
|-------------|--------|--------|
| CMS MA network adequacy rules coded (42 CFR 422.116) | COMPLETE | ref_time_distance |
| 43 CMS specialties mapped to 442 Aetna specialty codes | COMPLETE | cms_to_aetna_final.csv |
| Time & distance thresholds by specialty × county type | COMPLETE | ref_time_distance |
| Minimum provider counts from CMS 2026 HSD file | COMPLETE | ref_hsd_required_counts |
| Plan types confirmed: MA-HMO, MA-PPO | COMPLETE | stg_providers (plan_type) |
| 67 Florida counties in scope | COMPLETE | ref_county_classification |
| Compliance report (county × specialty × plan type) | COMPLETE | fact_gap_analysis_v2 |
| Business-ready Excel report (5 tabs) | COMPLETE | medicare_supply_demand.xlsx |
| Business-ready slides | COMPLETE | medicare_supply_demand.pptx |
| Business-ready Word document | COMPLETE | medicare_supply_demand_report.docx |
| DSNP, MA Group plan types | PENDING | Not yet scoped |

---

### Week 3 — Medicare Data Sourcing & Integration

**Objectives:** Assemble Medicare-specific datasets.

**Key Data Sources:**

| Source | Status | Description |
|--------|--------|-------------|
| mdcr_base_claim | COMPLETE | Aetna MA claims 2024-2025, HMO IVL + PPO IVL |
| cms_medicare_physician_ffs_2023 | COMPLETE | CMS Original Medicare participation by NPI |
| xwalk_pin_npi_all | COMPLETE | Aetna PIN to NPI crosswalk (np_perc >= 0.5) |
| mdcr_tin_par_flag | COMPLETE | TIN-level par flag (not used — too coarse) |
| CMS county classification | COMPLETE | From HSD file county_type column directly |

**Deliverables:**

| Deliverable | Status | Output |
|-------------|--------|--------|
| Deliverable 1: Data inventory by specialty × county × submarket × product | COMPLETE | 22_provider_par_flag.sql (deliverable 1 query) |
| Deliverable 2: Data quality assessment | PENDING | In scope |
| Deliverable 3: Provider participation flags with drilldown | COMPLETE | 22_provider_par_flag.sql |
| Provider-level par flag (claims-based, not TIN) | COMPLETE | provider_par_flag table |
| NPI crosswalk join (np_perc >= 0.5, bad_match_ind = 0) | COMPLETE | xwalk_pin_npi_all |
| Original Medicare flag (rndrng_prvdr_mdcr_prtcptg_ind) | COMPLETE | cms_medicare_physician_ffs_2023 |
| Participation status classification (6 categories) | COMPLETE | participation_status column |

**NOTE:** mdcr_tin_par_flag not used — it is at TIN level. par_flag = 1 if >50% of PINs in TIN had claims, which masks individual inactive providers. Provider-level flag derived directly from mdcr_base_claim (allowed_amt > 0 in 2024-2025).

---

### Week 4 — Medicare Geospatial Normalization

**Objectives:** Ensure geography aligns with CMS standards.

| Deliverable | Status | Output |
|-------------|--------|--------|
| Zip code centroids (member + provider) | COMPLETE | ref_zip_reference |
| County overlays with CMS classification | COMPLETE | ref_county_classification |
| CMS county type from HSD file (official) | COMPLETE | ref_hsd_required_counts.county_type |
| Distance matrix (straight-line, zip centroid) | COMPLETE | fact_zip_access_v2 |
| Liberty County FL classified as CEAC | COMPLETE | Confirmed from HSD file |
| Drive time vs straight-line evaluation | PENDING | Week 5+ |
| ZIP+4 / census block precision | PENDING | Currently zip-5 centroid only |
| Specialty groupings finalized (PCP vs Specialist) | COMPLETE | cms_to_aetna_final.csv |
| Provider address geocoding (vs zip centroid) | PENDING | Week 5+ |

---

### Week 5+ — Next Phase

| Planned Activity | Notes |
|-----------------|-------|
| Utilization patterns | Claims-based utilization rates by specialty for Medicare population |
| Provider participation nuances | Accepting MA ≠ participating in all products. DSNP and MA Group evaluation |
| Drive time distance | Replace straight-line with drive time for rural/CEAC counties |
| Telehealth credit | 14 specialties eligible for 10% access credit per 422.116(d)(5) |
| Medicare-specific population | Replace Census all-ages zip population with zip-level Medicare beneficiary estimates |
| Exception requests | Flag counties eligible for CMS exception filing where no providers exist |
| Data quality assessment | NULL zips, unmapped specialty codes, NPI match rates, county name mismatches |

---

## County Types (CMS 422.116(c))

```
Large Metro: MSA pop >= 1,000,000 AND density >= 1,000/sq mi OR density >= 5,000/sq mi
Metro:       MSA pop >= 50,000 but < 1,000,000
Micro:       Non-metropolitan with urban cluster pop 10,000-49,999
Rural:       Non-metropolitan, not Micro, density >= 10/sq mi
CEAC:        County Extreme Access Consideration — density < 10/sq mi

Compliance threshold: Large Metro/Metro = 90%, Micro/Rural/CEAC = 85%

WARNING: ref_county_classification derives county type from Census ACS data.
CMS publishes official designations in the HSD file (county_type column).
Validate ref_county_classification against HSD file before production use.
```
