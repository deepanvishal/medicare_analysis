# Medicare Network Adequacy & Capacity Modeling
## Data Decisions & Assumptions

---

## 1. Specialty Mapping Decisions

### 1.1 Aetna to CMS Specialty Crosswalk
Aetna uses an internal specialty coding system that does not align 1:1 with CMS 422.116 specialty categories. Two crosswalk tables are used for different purposes:

| Table | Grain | Rows | Used For |
|-------|-------|------|----------|
| `ref_specialty_crosswalk_expanded` | `specialty_cd` (raw code) | 442 | Provider lookup in `stg_providers_multi_specialty_v2` — maps each raw Aetna specialty code to a CMS specialty |
| `ref_specialty_crosswalk` | `specialty_ctg_cd` (category code) | 43 | Building the complete county × specialty grid in `fact_gap_analysis_v2` — one row per CMS specialty |

| Decision | Rationale |
|----------|-----------|
| Used `specialty_cd` (raw code) for provider mapping, not `specialty_ctg_cd` (category) | Raw code is more granular and accurate. Category codes are aggregated and can group unrelated specialties together |
| Accepted overlapping Aetna codes for multiple CMS specialties | Some Aetna codes map to multiple CMS specialties (e.g. `2PSOC` counts for both Plastic Surgery and Ophthalmology). This intentionally fans out one provider row to multiple CMS specialty rows |
| No `inflated` flag in current pipeline | The `ref_specialty_crosswalk_expanded` approach at `specialty_cd` level reduces inflation compared to the prior `specialty_ctg_cd` approach. Overlapping providers are still a known limitation but are not separately flagged |

### 1.2 Unmatched CMS Specialties
The following CMS specialties had no exact Aetna code match and were mapped to proxy codes:

| CMS Specialty | Aetna Proxy Code | Reason | Inflation Risk |
|---------------|-----------------|--------|----------------|
| Clinical Psychology | VVMH | Mental Health Professional is closest available code | High — shared with Clinical Social Work |
| Clinical Social Work | VVMH | Same as above | High — shared with Clinical Psychology |
| Physical Therapy | VVRH | Physical Rehabilitation Professionals | High — shared with OT and ST |
| Occupational Therapy | VVRH | Same as above | High — shared with PT and ST |
| Speech Therapy | VVRH | Same as above | High — shared with PT and OT |
| Mammography | VRAD | Radiology Center is closest facility type | Medium |
| Inpatient Psychiatric | WBHF | Behavioral Health Facility | Medium |
| Outpatient Behavioral Health | WBHF | Same as above | Medium — shared with Inpatient Psychiatric |
| Outpatient Infusion/Chemo | WHOS | Acute Short Term Hospital used as proxy | Medium |
| Cardiac Surgery Program | CS | Cardiothoracic Surgery used as proxy | Medium — shared with Cardiothoracic Surgery |
| Cardiac Catheterization | C | Cardiology used as proxy | Medium — shared with Cardiology |
| Skilled Nursing Facility | WLTC | Long Term Care Facility | Low |

---

## 2. Geographic Decisions

### 2.1 Zip Code vs County Level Analysis
| Decision | Rationale |
|----------|-----------|
| Distance matrix computed at zip code level | More precise than county centroid to county centroid. Providers and beneficiaries are located at zip level |
| Compliance rolled up to county level | CMS 422.116 evaluates compliance at county level. Zip level is only used for distance precision |
| Provider location = zip code centroid | Provider file contains zip code, not exact address. Zip centroid is the best available approximation |
| Beneficiary location = zip code centroid | CMS penetration data is at county level. We distribute to zip level using ACS population weights |

### 2.2 ST_DISTANCE vs ST_INTERSECTS
| Decision | Rationale |
|----------|-----------|
| Used `ST_DISTANCE` for provider-to-beneficiary distance | CMS 422.116 specifies distance-based access standards. `ST_DISTANCE` measures actual geographic distance between zip centroids |
| Used `ST_INTERSECTS` for zip-to-county mapping | To assign each zip code to its primary county, we use polygon intersection. Where a zip crosses county boundaries, we assign it to the county with the largest overlap area |
| Used `ST_CONTAINS` within the Florida boundary filter | To identify Florida zip codes, we check if the zip centroid falls inside a Florida county polygon. This prevents border zips from GA/AL being excluded while still capturing FL border zips that serve FL members |

### 2.3 Confidence Intervals Using Zip Radii
| Decision | Rationale |
|----------|-----------|
| Computed zip radius as `SQRT(area_sq_miles / PI())` | Approximates each zip as a circle. The radius represents location uncertainty — a provider's actual location could be anywhere within their zip |
| Confidence interval = distance ± (bene_zip_radius + provider_zip_radius) | Combined uncertainty from both beneficiary and provider zip approximations |
| Flagged borderline cases separately | Counties where distance straddles the threshold should not be treated as definitive pass/fail. These are marked BORDERLINE for manual review |

---

## 3. Population & Enrollment Decisions

### 3.1 Census 2018 vs 2020 at Zip Level
| Decision | Rationale |
|----------|-----------|
| Used ACS 2018 5-year estimates at zip level | ACS 2020 zip-level data was not available in BigQuery public datasets at time of analysis. 2018 is the most recent available zip-level ACS data |
| Used ACS 2020 5-year estimates at county level | County-level 2020 data is available and used for county type classification |
| Impact | Florida zip-level population counts may underestimate fast-growing areas (Miami-Dade suburbs, Orlando metro). Rural areas are less affected |
| Mitigation | CMS penetration file provides county-level eligible beneficiary counts as of most recent month. Used as validation and denominator for required provider count calculation |

### 3.2 CMS Penetration File Usage
| Decision | Rationale |
|----------|-----------|
| Used most recent ingest_time from monthly file | Ensures most current enrollment snapshot. Selected using `MAX(ingest_time)` |
| Used `eligibles` not `enrolled` as denominator for required provider count | CMS 422.116 uses total Medicare eligibles not just MA enrolled for minimum number calculation |
| Penetration rate stored as context only | Not used in core compliance calculation. Stored for business context and reporting |

---

## 4. Provider Data Decisions

### 4.1 Filtering
| Decision | Rationale |
|----------|-----------|
| Filtered to `state = 'FL'` | Analysis scoped to Florida only |
| Dropped providers with `zip_lat IS NULL` after zip join | These providers had zip codes not present in Florida zip reference — confirmed to be out-of-state providers (Missouri etc.) incorrectly flagged as FL in source data |
| No date filter applied | Provider file is a pre-filtered snapshot of active providers as of most recent month. No `eff_dt` or `term_dt` filter needed |

### 4.2 County Name Matching
| Decision | Rationale |
|----------|-----------|
| Built manual county name crosswalk | Aetna and Census use different county name formats. Three mismatches identified: Desoto/DeSoto, Saint Johns/St. Johns, Saint Lucie/St. Lucie |
| 26 Florida counties flagged as `no_coverage` | No Aetna providers found in these counties. These counties are automatically non-compliant for all specialties |
| Used LEFT JOIN on county crosswalk | Preserves all providers. Providers in unmatched counties get NULL county_fips and are excluded from compliance calculation but visible for investigation |

### 4.3 Plan Type Mapping
| Decision | Rationale |
|----------|-----------|
| `HMO IVL` mapped to `MA-HMO` | IVL = Individual enrollment HMO product |
| `PPO IVL` mapped to `MA-PPO` | IVL = Individual enrollment PPO product |
| Analysis run independently per plan type | A provider participating in MA-HMO does not automatically count toward MA-PPO compliance. CMS evaluates networks at contract/plan type level |

---

## 5. Compliance Calculation Decisions

### 5.1 Distance Threshold Application
| Decision | Rationale |
|----------|-----------|
| Used beneficiary county type for threshold lookup | CMS 422.116 standards are based on where the beneficiary lives, not where the provider is located |
| Provider in different county can count toward beneficiary county compliance | A cardiologist in Palm Beach County counts toward Martin County compliance if within the distance threshold. This is correct per CMS methodology |
| Cross-county access is captured by zip-to-zip distance | The distance matrix considers all provider zips within threshold distance of each beneficiary zip, regardless of county boundaries |

### 5.2 Minimum Provider Count
| Decision | Rationale |
|----------|-----------|
| Used exact counts from CMS 2026 HSD Reference File | CMS derives required counts using a 95th percentile base population ratio that cannot be precisely reproduced from available data. Using the published HSD file directly eliminates approximation error |
| `ref_hsd_required_counts` loaded via `14_reference_file.sql` | Hardcoded from the CMS HSD file (published Dec 17, 2025). Must be re-run when CMS publishes a new HSD file each cycle |
| Acute Inpatient Hospitals use contracted bed count, not provider count | CMS requires 12.2 beds per 1,000 beneficiaries for hospitals. `hosp_list_cmi.Beds` is used as the actual count; required count comes from `ref_hsd_required_counts` |
| All other specialties use distinct contracted provider count | `COUNT(DISTINCT provider_id)` per county × specialty × plan type, filtered to providers within the CMS distance threshold of at least one beneficiary zip |

---

## 6. Known Limitations & Future Improvements

| Limitation | Impact | Future Fix |
|------------|--------|------------|
| Zip centroid approximation | ±5-30 mile error depending on zip size | Use actual provider address geocoding |
| Census 2018 zip population | May undercount growing areas | Update when 2020 zip ACS available in BigQuery |
| Proxy specialty mappings | Inflated compliance scores for 12 specialties | Map to NPI taxonomy codes for exact specialty identification |
| No telehealth credit applied | 422.116 allows 10% credit for telehealth providers | Add telehealth flag to provider file and apply credit |
| No CON law credit applied | Florida has CON laws for some facility types | Research FL CON applicability and apply 10% credit |
| 26 counties with no Aetna providers | Automatic non-compliance | Investigate if providers exist but are uncoded |
