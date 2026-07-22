# 01_DATA_DICTIONARY — model_and_dashboard_v1

Purpose: single source of truth for tables, columns, types, filters, and
known traps. Update this file whenever a data issue is found — fixing it
here fixes it everywhere. Built ONLY from reading this repository's code;
nothing was verified against live BigQuery.

Status labels used throughout:

- VERIFIED-IN-CODE: the repo code reads or writes this column with this
  meaning.
- INFERRED: the meaning is deduced from naming or context but no code
  proves it. The evidence is stated.
- UNVERIFIED: mentioned in docs or comments only. Must be confirmed in
  BigQuery before any new code uses it.

Where code shows a column exists but not its full value set, that is said
exactly.

---

## 1. Projects and datasets

(source: expanded_scope/config.py)

| Item | Value |
|---|---|
| Table project | `anbc-hcb-dev` |
| Client / billing project | `anbc-dev-prv-nc-ds` (always used for `bigquery.Client`) |
| Dataset | `provider_ds_netconf_data_hcb_dev` |
| Base prefix | `A870800_medicare_supply_demand` (`cfg.base(name)`) |
| Multi-state infix | `ms` — `cfg.table(name)` = `A870800_medicare_supply_demand_ms_{name}` |
| Raw-table helper | `cfg.src(name)` = `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.{name}` |
| Footprint states | FL=12, OH=39, AZ=04, IL=17 (`STATES`, `state_fips_sql`, `state_abbr_sql`) |
| Expected county counts | FL 67, OH 88, AZ 15, IL 102 (`COUNTY_COUNTS`) |

---

## 2. Source tables

### 2.1 `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_2025_claims`

Claim-line extract for the demand/capacity work. Grain: one row per claim
line (not per visit; visits are derived). Status: VERIFIED-IN-CODE as a
table; the schema has TWO GENERATIONS in this repo (see conflict below).

**CONFLICT — provider id and line id.** Older code (expanded_scope/30–38,
demand_report.py, eda_runner.py) reads `srv_prvdr_id` and
`claim_line_id`. Newer dc_v2 code (46, 48, 55, 57) reads
`epdb_dw_prvdr_id` and never touches `claim_line_id`. dc_v2 docs say the
extract was rebuilt with provider resolution via `epdb_dw_prvdr_id` case
logic and a 2023-01-01 to 2025-12-31 window (source:
expanded_scope/dc_v2/00_docs/data_decisions.md, DD 07). Which columns
survive in the live table is a BigQuery check (Gaps #1).

| Column | Type | Meaning | Status | Source |
|---|---|---|---|---|
| member_id | - | member identifier | VERIFIED-IN-CODE | dc_v2/03_demand/46_demand_history_table.py |
| epdb_dw_prvdr_id | - | provider identifier (rebuilt extract) | VERIFIED-IN-CODE | dc_v2/03_demand/46_demand_history_table.py |
| srv_prvdr_id | - | provider identifier (older extract generation) | VERIFIED-IN-CODE (older files only) | expanded_scope/32_dc_rate.py, demand_report.py |
| claim_line_id | - | claim line identifier | VERIFIED-IN-CODE (older files only); presence post-rebuild UNVERIFIED | expanded_scope/32_dc_rate.py; dc_v2/00_docs/data_decisions.md DD 07 |
| srv_start_dt | date-like (DATE_TRUNC works) | service date; the visit-key date | VERIFIED-IN-CODE | dc_v2/03_demand/46_demand_history_table.py |
| pri_icd9_dx_cd | string-like (TRIM/REPLACE) | PRIMARY diagnosis code only; may carry dots | VERIFIED-IN-CODE | dc_v2/03_demand/46_demand_history_table.py |
| age_nbr | numeric | member age at claim | VERIFIED-IN-CODE | dc_v2/03_demand/46_demand_history_table.py |
| business_ln_cd | - | line of business; code filters IN ('CP','ME') and = 'ME'; full value set not proven | VERIFIED-IN-CODE (usage) | expanded_scope/32_dc_rate.py, 34_dc_book_utilization.py |
| mbr_county_cd | code (LPAD to 5 works) | MEMBER county code — demand attribution | VERIFIED-IN-CODE | dc_v2/03_demand/46_demand_history_table.py |
| mbr_submarket | - | member submarket; formerly the scope filter, now retired | VERIFIED-IN-CODE (column exists) | dc_v2/03_demand/46_demand_history_table.py header |
| prvdr_county | name string (UPPER/TRIM match) | PROVIDER county NAME — capacity attribution | VERIFIED-IN-CODE | dc_v2/04_capacity/48_provider_history_table.py |
| prvdr_submarket | string "XX ..." | provider submarket; state = UPPER(LEFT(...,2)) | VERIFIED-IN-CODE | expanded_scope/34_dc_book_utilization.py |
| specialty_ctg_cd | - | claims specialty category code (e.g. FP, C, PY) | VERIFIED-IN-CODE | dc_v2/03_demand/46_demand_history_table.py |
| specialty_ctg_cd_desc | - | specialty description | VERIFIED-IN-CODE (older files) | demand_report.py, 37_dc_forecast_example.py |
| allowed_amt | numeric (SAFE_CAST FLOAT64) | allowed amount; cost only, never a demand input | VERIFIED-IN-CODE | dc_v2/01_hcc_chronic/40_h1_mapping_coverage.py; dc_v2/00_docs/model_decisions.md |

### 2.2 `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_membership`

Monthly membership extract. Grain: member x month (eff_yr, eff_mo); one
row = enrolled that month. Status: VERIFIED-IN-CODE.

| Column | Type | Meaning | Status | Source |
|---|---|---|---|---|
| member_id | - | member identifier | VERIFIED-IN-CODE | dc_v2/03_demand/46_demand_history_table.py |
| eff_yr | castable INT64 | membership year | VERIFIED-IN-CODE | dc_v2/03_demand/46_demand_history_table.py |
| eff_mo | castable INT64 | membership month | VERIFIED-IN-CODE | dc_v2/03_demand/46_demand_history_table.py |
| age_nbr | numeric | member age | VERIFIED-IN-CODE | dc_v2/03_demand/46_demand_history_table.py |
| mbr_county_cd | code | member county code | VERIFIED-IN-CODE | dc_v2/06_weave/55_weave.py |
| mbr_state | - | member state code; joined to rate-table state_cd ('FL' style) | VERIFIED-IN-CODE | dc_v2/05_models/53_baselines_and_ceilings.py |
| mbr_submarket | - | member submarket; used only as IS NOT NULL historically | VERIFIED-IN-CODE (column exists) | dc_v2/03_demand/46_demand_history_table.py |
| gender_cd | - | gender code | UNVERIFIED (docs/prompt only; no repo code reads it) | dc_v2/00_docs/data_decisions.md DD 08 |

### 2.3 `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.HCC_ICD_Mapping_2025`

ICD-to-HCC map. Grain: diagnosis code (duplicates possible across model
versions; code dedupes before joining). Status: VERIFIED-IN-CODE.

| Column | Type | Meaning | Status | Source |
|---|---|---|---|---|
| diagnosis_code | string | ICD code, stored without dots; code still TRIMs and strips dots defensively | VERIFIED-IN-CODE | dc_v2/03_demand/46_demand_history_table.py |
| HCC_v24 | - | CMS-HCC V24 category; mapped = HCC_v24 IS NOT NULL (decided rule) | VERIFIED-IN-CODE | dc_v2/01_hcc_chronic/40_h1_mapping_coverage.py; data_decisions.md DD 05 |
| HCC_v28 | - | CMS-HCC V28 category | UNVERIFIED (docs only) | dc_v2/00_docs/data_decisions.md DD 05 |
| is_pay_v24 | - | 'Yes'/'No'/'-' payment flag; identical criterion to HCC_v24 populated; decided NOT used as a filter | UNVERIFIED (docs record a run result; no repo code reads it) | data_decisions.md DD 05 mapped-filter verification |
| description | - | ICD description (not an HCC category name) | UNVERIFIED (docs only) | demand_report.py history; docs |

### 2.4 `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.mdcr_base_membership`

Medicare membership reference. Status: table VERIFIED-IN-CODE; semantics
partly INFERRED.

- Older rule: a member present in this table is Medicare, else commercial
  — INFERRED (implemented as presence-join in demand_report.py; superseded
  in dc_v2 by business_ln_cd = 'ME').
- **CONFLICT — date column name.** expanded_scope/dc_v2/00_docs/PLAN.md
  states "eff_df is the membership date column (not eff_dt)". Working code
  uses `eff_dt` (source: expanded_scope/eda_runner.py, after an explicit
  correction; expanded_scope/30_dc_member_dim.py). dc_v2 docs say eff_dt
  IS the membership month, one row per member-month (source:
  data_decisions.md DD 08, which names the upstream EMIS_MEMBERSHIP).
  Record: code says `eff_dt`; PLAN.md says `eff_df`; confirm in BigQuery
  before new code touches it (Gaps #6).

| Column | Type | Meaning | Status | Source |
|---|---|---|---|---|
| member_id | - | member identifier | VERIFIED-IN-CODE | expanded_scope/eda_runner.py |
| eff_dt | date-like | membership month (one row = enrolled that month, per DD 08) | VERIFIED-IN-CODE (name), INFERRED (month semantics) | eda_runner.py; data_decisions.md DD 08 |
| state_postal_cd | - | member state ('FL' style) | VERIFIED-IN-CODE | expanded_scope/30_dc_member_dim.py |
| county_nm | string | member county name (UPPERed before joining) | VERIFIED-IN-CODE | expanded_scope/30_dc_member_dim.py |
| zip_cd | - | member zip | VERIFIED-IN-CODE | expanded_scope/30_dc_member_dim.py |
| age_nbr | numeric | member age | VERIFIED-IN-CODE | expanded_scope/30_dc_member_dim.py |

### 2.5 `A870800_medicare_supply_demand_ms_ref_county` (cfg.table("ref_county"))

County reference for the 4 states; the canonical county key. Grain:
county_fips (gate asserts none unresolved). Status: VERIFIED-IN-CODE.

| Column | Type | Meaning | Status | Source |
|---|---|---|---|---|
| county_fips | 5-char string | canonical county key (geo_id from census boundaries) | VERIFIED-IN-CODE | expanded_scope/04_ref_county.py |
| state_cd | - | 'FL'/'OH'/'AZ'/'IL' | VERIFIED-IN-CODE | expanded_scope/04_ref_county.py |
| county_name | - | HSD county name | VERIFIED-IN-CODE | expanded_scope/04_ref_county.py |
| county_type | - | CMS county designation from HSD | VERIFIED-IN-CODE | expanded_scope/04_ref_county.py |
| compliance_threshold | 0.90/0.85 | access threshold by county type | VERIFIED-IN-CODE | expanded_scope/04_ref_county.py |

### 2.6 `A870800_medicare_supply_demand_ref_specialty_crosswalk` (cfg.base)

43-row specialty bridge: claims category code to CMS specialty. Grain: one
row per (cms_specialty, aetna_cd) pair; one aetna_cd can map to multiple
cms_specialty (fan-out is real, e.g. VVMH). Status: VERIFIED-IN-CODE.

| Column | Type | Meaning | Status | Source |
|---|---|---|---|---|
| cms_specialty | string | CMS specialty name | VERIFIED-IN-CODE | Step3_specialty_cd_based_report.sql (creation); dc_v2/06_weave/55_weave.py |
| aetna_cd | string | the join key; HOLDS specialty_ctg_cd values (FP, C, PY...) despite the name | VERIFIED-IN-CODE | expanded_scope/36_dc_gap.py; Step3_specialty_cd_based_report.sql |

### 2.7 `A870800_medicare_supply_demand_ms_ref_specialty_crosswalk_expanded` (cfg.table)

Raw-code specialty map loaded from CSV (~327 rows, 43 distinct
cms_specialty). Grain: cms_specialty x aetna_code. Status:
VERIFIED-IN-CODE. Columns: cms_specialty, aetna_code, aetna_description —
all STRING by explicit load schema (source:
expanded_scope/02_load_specialty_crosswalk.py). Note: `aetna_code` here is
the RAW provider specialty code, a different code space from
`aetna_cd`/`specialty_ctg_cd` in 2.6.

### 2.8 `xwalk_pin_npi_all` (cfg.src)

Aetna PIN to NPI crosswalk. Status: VERIFIED-IN-CODE. Columns used:
provider_id, npi, np_perc (filter >= 0.5), bad_match_ind (filter = 0)
(source: expanded_scope/12_provider_par_flag.py).

### 2.9 `cms_medicare_physician_ffs_2023` (cfg.src)

CMS Original Medicare FFS provider file. Status: VERIFIED-IN-CODE.
Columns used: rndrng_npi, rndrng_prvdr_state_abrvtn,
rndrng_prvdr_mdcr_prtcptg_ind, tot_benes, tot_srvcs, tot_mdcr_pymt_amt —
the numeric columns are SAFE_CAST because CMS suppression markers exist
(source: expanded_scope/12_provider_par_flag.py;
expanded_scope/dc_v2/00_docs/PLAN.md). No age columns are read anywhere in
repo code.

### 2.10 `mdcr_base_claim` (cfg.src)

Aetna MA claim table used for provider par flags. Status:
VERIFIED-IN-CODE. Columns used: srv_prvdr_id, prod_type ('HMO IVL'/'PPO
IVL'), srv_start_dt, allowed_amt (source:
expanded_scope/12_provider_par_flag.py). Trap: `mdcr_tin_par_flag` is
TIN-level and deliberately NOT used; provider-level par comes from this
table (source: dc_v2/00_docs/PLAN.md).

### 2.11 `A870800_medicare_supply_demand_ms_provider_par_flag` (cfg.table)

Derived: provider participation flags. Grain: provider x plan x specialty
x county. Status: VERIFIED-IN-CODE. Key columns: provider_id, plan_type
('MA-HMO'/'MA-PPO'), cms_specialty, state_cd, county_name, county_fips,
zip_cd, aetna_par_flag (0/1), claim_count, total_allowed_amt,
first/last_claim_dt, original_medicare_flag ('Y'), tot_benes, tot_srvcs,
tot_mdcr_pymt_amt, participation_status (6 fixed strings) (source:
expanded_scope/12_provider_par_flag.py).

### 2.12 `dc2_ref_... note` — `A870800_medicare_supply_demand_ms_dc_ref_ccir` (cfg.table("dc_ref_ccir"))

AHRQ CCIR chronic-condition reference loaded from CSV. Grain: icd_code.
Status: VERIFIED-IN-CODE. Columns (explicit load schema): icd_code STRING
(dot-free), icd_description STRING, chronic_indicator INT64 (0/1/9),
chronic_label STRING (NOT_CHRONIC/CHRONIC/NO_DETERMINATION) (source:
expanded_scope/30a_dc_ref_ccir.py). Expected ~75,725 rows per the loader's
printed sanity line — UNVERIFIED until run.

### 2.13 `A870800_medicare_supply_demand_ms_fact_gap_analysis` (cfg.table("fact_gap_analysis"))

v1 compliance fact. Grain: state x county x cms_specialty x plan_type.
Status: VERIFIED-IN-CODE. Columns read by this codebase: state_cd,
county_fips, county_name, county_type, cms_specialty, plan_type,
compliance_status ('COMPLIANT'/'NON-COMPLIANT'), access_compliant,
count_compliant, required_provider_count, actual_count, provider_gap,
ma_demand_visits, total_demand_visits, capacity_visits,
demand_capacity_gap, market_opportunity_ratio, gap_status, risk_flag
(sources: expanded_scope/36_dc_gap.py which writes ms_dc_gap from it;
expanded_scope/13_build_report.py; dc_v2/06_weave/55_weave.py reads
compliance_status).

### 2.14 v1 dc_ tables still read as sources

All VERIFIED-IN-CODE; written by expanded_scope/30–36 and read by dc_v2
notebook 53.

- `ms_dc_rate` (cfg.table("dc_rate")): state_cd (incl. 'ALL' pooled rows),
  specialty_ctg_cd, specialty_desc, age_band, morbidity_level
  (CHRONIC/NON_CHRONIC), ma_visits, cell_n, rate_ma_proxy, is_thin_cell,
  rate_total_medicare NULL (source: expanded_scope/32_dc_rate.py).
- `ms_dc_member_dim` (cfg.table("dc_member_dim")): member_id, state_cd,
  county_fips, county_nm, county_source, zip_cd, age_nbr, age_band
  (60-64/65-69/70-74/75-79/80+/UNDER_60), chronic_condition_count,
  morbidity_level (source: expanded_scope/30_dc_member_dim.py).
- `ms_dc_gap` (cfg.table("dc_gap")): see 2.13 output columns (source:
  expanded_scope/36_dc_gap.py).

---

## 3. dc2_* derived tables (written by dc_v2 code)

All plain-dataset names via cfg.src(); all VERIFIED-IN-CODE as written.
Note: these are legitimate READ sources for the new pipeline, but their
correctness is not assumed (fresh-build rule).

### 3.1 `dc2_demand_base`

Grain: mbr_county_cd x specialty_ctg_cd x month (DATE, first of month;
2024-2025). MEMBER-county attribution; scope age 60+, footprint via
ref_county. Columns: mbr_county_cd, specialty_ctg_cd, month, visits,
target_next_1m, target_next_12m, members, mbr_age_60_64, mbr_age_65_74,
mbr_age_75_84, mbr_age_85p, pct_new_patients, month_of_year, year,
month_index (source: dc_v2/03_demand/46_demand_history_table.py).

### 3.2 `dc2_demand_chronic`

Grain: mbr_county_cd x month x HCC_v24 (2024-2025); trailing-24-month
condition window. Columns: mbr_county_cd, month, HCC_v24,
members_with_hcc, members, prevalence (source:
dc_v2/03_demand/46_demand_history_table.py).

### 3.3 `dc2_capacity_provider`

Grain: epdb_dw_prvdr_id x specialty_ctg_cd x month (2024-2025);
PROVIDER-county attribution. Columns: epdb_dw_prvdr_id, specialty_ctg_cd,
month, prvdr_county, visits, target_next_1m, target_next_12m,
panel_members, panel_60_64, panel_65_74, panel_75_84, panel_85p,
panel_chronic_members, pct_new_patients, distinct_mbr_counties,
tenure_months, month_of_year, year, month_index (source:
dc_v2/04_capacity/48_provider_history_table.py).

### 3.4 `dc2_capacity_county`

Grain: prvdr_county x specialty_ctg_cd x month; rollup of 3.3. Columns:
prvdr_county, specialty_ctg_cd, month, visits, target_next_1m,
target_next_12m, providers, pct_new_patients, month_of_year, year,
month_index (source: dc_v2/04_capacity/48_provider_history_table.py).

### 3.5 `dc2_demand_predictions`

Grain: mbr_county_cd x specialty_ctg_cd x month. Columns: mbr_county_cd,
specialty_ctg_cd, month, actual_next_1m, pred_next_1m_linear,
pred_next_1m_xgb, actual_next_12m, pred_next_12m_linear,
pred_next_12m_xgb, split_label ('train'/'validation'/'future'; future =
2025-12) (source: dc_v2/05_models/50_demand_model.py).

### 3.6 `dc2_capacity_predictions`

Grain: prvdr_county x specialty_ctg_cd x month. Columns: prvdr_county,
specialty_ctg_cd, month, actual_next_1m, actual_next_12m,
bottom_up_next_1m, bottom_up_next_12m, top_down_next_1m_linear,
top_down_next_1m_xgb, top_down_next_12m_linear, top_down_next_12m_xgb,
divergence_pct_next_1m, divergence_pct_next_12m, split_label. bottom_up_*
populated only on validation and future months (source:
dc_v2/05_models/51_capacity_models.py).

### 3.7 `dc2_capacity_provider_future`

Grain: epdb_dw_prvdr_id x specialty_ctg_cd x prvdr_county x month
(2025-12 only). Columns: epdb_dw_prvdr_id, specialty_ctg_cd, prvdr_county,
month, provider_pred_next_1m, provider_pred_next_12m (source:
dc_v2/05_models/51_capacity_models.py). Trap: written from pandas —
epdb_dw_prvdr_id arrives as object dtype and must be normalized before
merging (source: dc_v2/06_weave/57_finegrain_report.py
_normalize_provider_keys).

### 3.8 `dc2_baselines`

Grain: county_fips x cms_specialty (within state_cd). Columns: state_cd,
county_fips, cms_specialty, capacity_current, demand_current_book,
gap_current_book, market_max_demand, source_note (source:
dc_v2/05_models/53_baselines_and_ceilings.py).

### 3.9 `dc2_weave`

Grain: state_cd x county_fips x cms_specialty. Exactly 19 columns, in
order: state_cd, county_fips, county_name, cms_specialty,
demand_visits_2025_actual, capacity_visits_2025_actual, gap_2025_actual,
demand_next_12m_xgb, capacity_next_12m_bottom_up, gap_model_2026,
gap_status ('UNDER'/'OVER'/NULL), capacity_to_demand_ratio,
compliance_status ('COMPLIANT'/'NON-COMPLIANT'/NULL, collapsed over
plan_type), expected_error_pct, expected_error_band ('A'/'B'/'C'),
capacity_potential_p75, pct_medicare_age_members, demand_rate_estimate,
market_max_demand (source: dc_v2/06_weave/55_weave.py).

---

## 4. Known traps

1. Membership date column conflict: PLAN.md says `eff_df`; working code
   and DD 08 use/describe `eff_dt` on mdcr_base_membership. The A870800
   membership EXTRACT uses `eff_yr`/`eff_mo` instead — three different
   date shapes across membership sources (sources: dc_v2/00_docs/PLAN.md;
   expanded_scope/eda_runner.py; dc_v2/03_demand/46_demand_history_table.py).
2. The claims table is named `_2025_claims` but holds a 2023-01-01 to
   2025-12-31 window; dc_v2 code treats 2023 as lookback memory only
   (sources: data_decisions.md DD 07;
   dc_v2/03_demand/46_demand_history_table.py header).
3. Provider id has two generations on the same claims table:
   `srv_prvdr_id` in older code, `epdb_dw_prvdr_id` in dc_v2 code (sources:
   expanded_scope/32_dc_rate.py vs dc_v2/04_capacity/48_provider_history_table.py;
   DD 07).
4. Attribution rule: demand tables use MEMBER county (`mbr_county_cd`),
   capacity tables use PROVIDER county (`prvdr_county`); mixing them
   collapses the county-level gap to zero (sources:
   dc_v2/00_docs/model_decisions.md CRITICAL section; 46/48 headers).
5. Visit key: one visit = one distinct member_id x provider id x
   srv_start_dt; claim lines are NOT visits (sources: 46/48 VISIT_KEY;
   model_decisions.md).
6. New-patient rule: member x provider pair with no visit in the 12 months
   before month M, implemented with LAG over pair-months; months in 2023
   serve as lookback only (source:
   dc_v2/03_demand/46_demand_history_table.py pair_new CTE).
7. Age scope: members aged 60+ everywhere; under-60 excluded from every
   number (sources: 46/48 SCOPE header lines).
8. LOB values are CP and ME — not 'MA'; Medicare = 'ME' (sources:
   dc_v2/00_docs/PLAN.md; expanded_scope/32_dc_rate.py filter).
9. Footprint filter is a ref_county join (member code side LPAD-to-5 FIPS;
   provider side UPPER name match), NOT `submarket IS NOT NULL` (sources:
   46/48 FOOTPRINT headers). County NAMES collide across states (Lake,
   Union...); 48 filters through a DISTINCT-name semi-join to avoid row
   fan-out, and 55 prints a collision warning on the name-to-fips mapping
   (sources: 48 footprint_counties CTE; 55 norm_provider_county).
10. `mbr_county_cd` is a CODE (zero-pad to 5 before joining county_fips);
    `prvdr_county` is a NAME (UPPER/TRIM before joining county_name). AZ
    FIPS lose leading zeros in some sources — always LPAD (sources:
    dc_v2/06_weave/55_weave.py normalization helpers; PLAN.md;
    expanded_scope/31_dc_county_population.py census geo_id LPAD).
11. ICD join cleaning: UPPER(REPLACE(TRIM(code), '.', '')) on BOTH sides
    of any diagnosis join (sources: 46 mapped_claims;
    dc_v2/01_hcc_chronic/40_h1_mapping_coverage.py).
12. CMS-sourced numerics can carry suppression markers ('*', '#'):
    always SAFE_CAST and count the loss (sources: dc_v2/00_docs/PLAN.md;
    expanded_scope/12_provider_par_flag.py SAFE_CAST tot_benes).
13. All SQL division goes through SAFE_DIVIDE; pandas division is guarded
    with np.where on a nonzero denominator (sources: 46/55/57; the F3
    sweep in dc_v2/00_docs/prompt_pack_dc2.md).
14. `rows` is a BigQuery reserved word — never use it as an alias; the
    repo convention is `row_count` (source: check queries across
    expanded_scope and dc_v2, e.g. 46 CHECKS).
15. The 43-row crosswalk's join column is `aetna_cd`, and its VALUES are
    specialty_ctg_cd codes; joining on a column named specialty_ctg_cd
    fails with "not found" (sources: expanded_scope/36_dc_gap.py;
    Step3_specialty_cd_based_report.sql).
16. State from `prvdr_submarket` requires UPPER(LEFT(..., 2)) (sources:
    dc_v2/00_docs/PLAN.md; expanded_scope/34_dc_book_utilization.py).
17. `mdcr_tin_par_flag` is TIN-level and masks inactive individual
    providers; provider-level par comes from mdcr_base_claim (sources:
    dc_v2/00_docs/PLAN.md; expanded_scope/12_provider_par_flag.py NOTE).
18. Older "Medicare = present in mdcr_base_membership" rule is INFERRED
    presence semantics and was superseded by business_ln_cd = 'ME'
    (sources: demand_report.py; dc_v2 code).
19. Census ACS age bracket columns use `_to_` names (male_60_to_61, not
    male_60_61) in county_2020_5yr (source:
    expanded_scope/31_dc_county_population.py census_age CTE and its fix
    history).
20. Categorical pandas columns break .map().fillna() band logic — cast a
    temporary series to str first (source:
    dc_v2/05_models/51_capacity_models.py county_band_series).

---

## 5. Gaps to verify in BigQuery

1. `A870800_medicare_analysis_2025_claims`: post-rebuild schema — does
   `claim_line_id` still exist, and are both `srv_prvdr_id` and
   `epdb_dw_prvdr_id` present, or only the latter?
2. Same table: actual MIN/MAX of `srv_start_dt` — is the window really
   2023-01-01 to 2025-12-31 as DD 07 states?
3. Same table: full distinct value set of `business_ln_cd` — only CP and
   ME, or more?
4. Same table: `mbr_county_cd` format — 5-digit FIPS? Do AZ codes carry
   leading zeros as stored?
5. Same table: distinct `prvdr_county` values — casing/format match rate
   against ms_ref_county.county_name under UPPER/TRIM.
6. `mdcr_base_membership`: full column list — settle `eff_dt` vs `eff_df`;
   confirm one row = member-month.
7. `A870800_medicare_analysis_membership`: does `gender_cd` exist? What
   months does eff_yr/eff_mo actually cover, and are any months missing?
8. `HCC_ICD_Mapping_2025`: confirm `HCC_v28`, `is_pay_v24`, `is_pay_v28`,
   `description` exist and their value sets (docs claim
   'Yes'/'No'/'-' for is_pay_v24).
9. `specialty_ctg_cd`: full distinct value set in claims vs the 43-row
   crosswalk's aetna_cd values — how much volume falls outside the bridge?
10. `ms_dc_ref_ccir`: row count (~75,725 expected) and chronic_label
    distribution match the loader's expectations.
11. `hosp_list_cmi`: schema and state scope (queried by
    00_check_data_availability.py without assuming columns).
12. All `dc2_*` tables: exist and carry post-rerun row counts consistent
    with the latest 46-55 run (age-60 floor and ref_county footprint
    applied).
13. `mbr_submarket` / `prvdr_submarket`: full value sets (footprint
    submarket list), now that they are display/context columns rather
    than filters.
