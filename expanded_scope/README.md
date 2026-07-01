# expanded_scope — Multi-State Network Adequacy (FL + OH + AZ + IL)

Self-contained rebuild of the Medicare network-adequacy pipeline covering
**FL, OH, AZ, IL** in one combined dataset and one combined report (filterable
by a `state` column).

## Rules (do not break)
- **No cross-folder dependencies.** Files here import/reference only what is
  inside `expanded_scope/`. The root-level `StepN_*` files are **reference-only** —
  read the logic, re-implement it here; never import from them.
- **Never overwrites FL production.** All output tables use the `_ms_`
  (multi-state) infix, e.g. `A870800_medicare_supply_demand_ms_ref_hsd_required_counts`.
  The existing FL tables and FL report stay intact until a deliberate cutover.
- Shared config lives in `config.py`.

## Scope
| State | FIPS | Counties |
|-------|------|----------|
| FL | 12 | 67 |
| OH | 39 | 88 |
| AZ | 04 | 15 |
| IL | 17 | 102 |

## Planned run order
| File | Purpose |
|------|---------|
| `config.py` | Shared config: states/FIPS, projects, dataset, `_ms_` table naming, BQ client. |
| `00_check_data_availability.py` | **Gate.** Confirm Aetna network/members, CMS FFS providers, counties, and hospital beds exist for all 4 states before building. |
| `01_load_hsd_reference.py` | **py** · Load HSD wide sheets (all states) + build `ms_ref_hsd_required_counts` data-driven (replaces hardcoded UNNEST). |
| `02_load_specialty_crosswalk.py` | **py** · Aetna `specialty_cd` → 43 CMS specialties (from CSV). State-agnostic. |
| `03_load_time_distance.py` | **py** · CMS time/distance + `min_ratio_per_1000` per specialty × county_type (from HSD T&D sheets). |
| `04_ref_county.py` | **py** · County dimension: `county_fips` + `county_type` (from HSD) + `compliance_threshold`. Replaces FL's Census classification. |
| `05_ref_zip_reference.py` | **py** · Zip centroid + zip→county spatial intersection (+ border zips). |
| ~~`06_ref_county_name_crosswalk`~~ | **DROPPED** · provider `county_fips` derived from the provider's zip in `09` (no name→FIPS crosswalk needed). |
| `07_mbr_with_all_zips.py` | **py** · Supply source: `mbr_with_zip` + provider zips, filter opened to 4 states. |
| `08_stg_beneficiaries.py` | **py** · Demand side: zip population + Medicare eligibles + county attrs (from `ms_ref_county`). |
| `09_stg_providers.py` | **py** · Supply side: provider × cms_specialty × plan_type. `county_fips` from zip; keeps `aetna_county_nm` + QA. |
| `10_fact_zip_access.py` | **py** · Distance matrix: `has_access` per bene_zip × specialty × plan. Per-county T&D join. |
| `11_fact_gap_analysis.py` | **py** · County compliance: Test 1 + Test 2, `(state_cd,county_name)`-keyed. **The report reads this.** |
| `12_provider_par_flag.py` | **py** · Participation flags (Aetna claims + CMS Original Medicare). *(DEFERRED — supplementary, not core report.)* |
| `13_build_report.py` | **py** · One workbook, `State` filter column + per-state rollup. |

## Key design decisions
- **Combined data, combined report.** One `ms_fact_gap_analysis_v2` holds all
  states; the report filters by `state`, not separate workbooks.
- **FIPS-keyed joins.** County names collide across states (Lake, Madison, Union,
  Monroe, Marion). All county joins use `county_fips` / `state_cd + county_name`.
- **Supply-side source (SETTLED).** `mbr_with_zip` (ours, `A870800_`) already holds
  **all zip/state data** — treat it as national. `mbr_with_all_zips` is only FL because
  Step5 applies `WHERE m.state='FL'`. So multi-state supply is simply: rebuild
  `ms_mbr_with_all_zips` with Step5's recipe and the filter opened to all 4 states.
  No source hunt. The Phase 0 `mbr_with_zip_states` check is now just a sanity count.

  Ownership rule: anything named `A870800_*` is ours. Everything else in the
  dataset (e.g. `mdcr_base_claim`, `mdcr_base_provider_mdcr_ntwk`, `hosp_list_cmi`,
  `cms_medicare_physician_ffs_2023`, `xwalk_pin_npi_all`) plus `edp-prod-hcbstorage.*`,
  `bigquery-public-data.*`, and `anbc-hcb-prod.*` are not ours (read-only).

## Auth
`gcloud auth application-default login`  (billing/auth project `anbc-dev-prv-nc-ds`).
