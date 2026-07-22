# 01_DATA_DICTIONARY — model_and_dashboard_v1

Purpose: single source of truth for tables, columns, types, filters, and
known traps. Update this file whenever a data issue is found — fixing it
here fixes it everywhere. Built ONLY from reading this repository's code;
nothing was verified against live BigQuery.

Status labels used throughout:

- VERIFIED-IN-CODE: the repo code reads or writes this column with this
  meaning.
- VERIFIED-IN-BQ: confirmed by the user against live BigQuery.
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

**Provider id CORRECTED (VERIFIED-IN-BQ by INFORMATION_SCHEMA
screenshot).** The provider id column is `epdb_dw_prvdr_id` (INT64).
`srv_prvdr_id` does NOT exist; the earlier dictionary entry recording the
opposite was a misreading during review — dc_v2 code selecting
`epdb_dw_prvdr_id` was correct all along. `claim_line_id` remains absent.
Observed full column list with types: member_id (INT64), age_nbr (INT64),
gender_cd (STRING), mbr_county_cd (STRING), mbr_submarket (STRING),
srv_start_dt (DATE), pri_icd9_dx_cd (STRING), allowed_amt (NUMERIC),
business_ln_cd (STRING), epdb_dw_prvdr_id (INT64), prvdr_county (STRING),
prvdr_submarket (STRING), specialty_ctg_cd (STRING).
The `srv_start_dt` window is settled: 2023-01-01 to 2025-12-31,
VERIFIED-IN-BQ (Resolved gap 2).

| Column | Type | Meaning | Status | Source |
|---|---|---|---|---|
| member_id | INT64 | member identifier | VERIFIED-IN-BQ | INFORMATION_SCHEMA screenshot; dc_v2/03_demand/46_demand_history_table.py |
| epdb_dw_prvdr_id | INT64 | the provider id; the visit key and new-patient rule use it (CAST to STRING when concatenating) | VERIFIED-IN-BQ | INFORMATION_SCHEMA screenshot; dc_v2/03_demand/46_demand_history_table.py |
| srv_prvdr_id | - | CORRECTION: never existed in the live table; the prior entry claiming it was the only id was a misreading during review | VERIFIED-IN-BQ (absent) | INFORMATION_SCHEMA screenshot |
| claim_line_id | - | does not exist in the rebuilt table | VERIFIED-IN-BQ (absent) | INFORMATION_SCHEMA screenshot |
| gender_cd | STRING | member gender code | VERIFIED-IN-BQ | INFORMATION_SCHEMA screenshot |
| srv_start_dt | date-like (DATE_TRUNC works) | service date; the visit-key date; window 2023-01-01 to 2025-12-31 | VERIFIED-IN-CODE; window VERIFIED-IN-BQ | dc_v2/03_demand/46_demand_history_table.py; user confirmation (Resolved gap 2) |
| pri_icd9_dx_cd | string-like (TRIM/REPLACE) | PRIMARY diagnosis code only; may carry dots | VERIFIED-IN-CODE | dc_v2/03_demand/46_demand_history_table.py |
| age_nbr | numeric | member age at claim | VERIFIED-IN-CODE | dc_v2/03_demand/46_demand_history_table.py |
| business_ln_cd | - | line of business; carries exactly two values, CP and ME | VERIFIED-IN-BQ (value set); VERIFIED-IN-CODE (usage) | user confirmation (Resolved gap 3); expanded_scope/32_dc_rate.py, 34_dc_book_utilization.py |
| mbr_county_cd | 5-char string | MEMBER county code — demand attribution; stored clean at 5 characters (LPAD stays as harmless defense) | VERIFIED-IN-BQ (Resolved gap 4) | dc_v2/03_demand/46_demand_history_table.py; GAP 4 result |
| mbr_submarket | - | member submarket; formerly the scope filter, now retired | VERIFIED-IN-CODE (column exists) | dc_v2/03_demand/46_demand_history_table.py header |
| prvdr_county | name string (UPPER/TRIM match) | PROVIDER county NAME — capacity attribution | VERIFIED-IN-CODE | dc_v2/04_capacity/48_provider_history_table.py |
| prvdr_submarket | string "XX ..." | provider submarket; state = UPPER(LEFT(...,2)) | VERIFIED-IN-CODE | expanded_scope/34_dc_book_utilization.py |
| specialty_ctg_cd | - | claims specialty category code (e.g. FP, C, PY) | VERIFIED-IN-CODE | dc_v2/03_demand/46_demand_history_table.py |
| specialty_ctg_cd_desc | - | specialty description | VERIFIED-IN-CODE (older files) | demand_report.py, 37_dc_forecast_example.py |
| allowed_amt | numeric (SAFE_CAST FLOAT64) | allowed amount; cost only, never a demand input | VERIFIED-IN-CODE | dc_v2/01_hcc_chronic/40_h1_mapping_coverage.py; dc_v2/00_docs/model_decisions.md |

### 2.2 `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_membership`

Monthly membership extract. Grain: member x month (eff_yr, eff_mo); one
row = enrolled that month. Status: VERIFIED-IN-BQ. Observed columns:
member_id, eff_yr, eff_mo, age_nbr, gender_cd, zip_cd, mbr_county_cd,
mbr_state, mbr_submarket (GAP 7 schema output). Monthly coverage is
continuous, no missing months, 2023-01 through 2025-12, with distinct
member counts rising from about 20.3M (2023-01) through about 22.5M
(2025-12) (Resolved gap 7).

| Column | Type | Meaning | Status | Source |
|---|---|---|---|---|
| member_id | - | member identifier | VERIFIED-IN-BQ | dc_v2/03_demand/46_demand_history_table.py; GAP 7 output |
| eff_yr | castable INT64 | membership year | VERIFIED-IN-BQ | dc_v2/03_demand/46_demand_history_table.py; GAP 7 output |
| eff_mo | castable INT64 | membership month | VERIFIED-IN-BQ | dc_v2/03_demand/46_demand_history_table.py; GAP 7 output |
| age_nbr | numeric | member age | VERIFIED-IN-BQ | dc_v2/03_demand/46_demand_history_table.py; GAP 7 output |
| gender_cd | STRING | gender code | VERIFIED-IN-BQ (Resolved gap 7) | GAP 7 schema output |
| zip_cd | - | member zip | VERIFIED-IN-BQ | GAP 7 schema output |
| mbr_county_cd | 5-char string | member county code; stored clean at 5 characters (Resolved gap 4) | VERIFIED-IN-BQ | dc_v2/06_weave/55_weave.py; GAP 4 result |
| mbr_state | - | member state code; joined to rate-table state_cd ('FL' style) | VERIFIED-IN-BQ | dc_v2/05_models/53_baselines_and_ceilings.py; GAP 7 output |
| mbr_submarket | - | member submarket; display context only, spans the whole book, includes nulls | VERIFIED-IN-BQ (Resolved gap 13) | dc_v2/03_demand/46_demand_history_table.py; GAP 13 result |

### 2.3 `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.HCC_ICD_Mapping_2025`

ICD-to-HCC map. Grain: diagnosis code (duplicates possible across model
versions; code dedupes before joining). Status: VERIFIED-IN-CODE.

| Column | Type | Meaning | Status | Source |
|---|---|---|---|---|
| diagnosis_code | STRING | ICD code, stored without dots; code still TRIMs and strips dots defensively | VERIFIED-IN-BQ | dc_v2/03_demand/46_demand_history_table.py; GAP 8 schema output |
| HCC_v24 | STRING | CMS-HCC V24 category; mapped = HCC_v24 IS NOT NULL (decided rule) | VERIFIED-IN-BQ | dc_v2/01_hcc_chronic/40_h1_mapping_coverage.py; GAP 8 schema output |
| HCC_v28 | STRING | CMS-HCC V28 category | VERIFIED-IN-BQ (Resolved gap 8) | GAP 8 schema output |
| is_pay_v24 | STRING | payment flag; value set Yes, No, and dash; decided NOT used as a filter | VERIFIED-IN-BQ (Resolved gap 8) | GAP 8 result; data_decisions.md DD 05 |
| is_pay_v28 | STRING | payment flag; value set Yes, No, and dash | VERIFIED-IN-BQ (Resolved gap 8) | GAP 8 result |
| description | STRING | ICD description (not an HCC category name) | VERIFIED-IN-BQ (Resolved gap 8) | GAP 8 schema output |

### 2.4 `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.mdcr_base_membership`

Medicare membership reference. Status: table VERIFIED-IN-CODE; semantics
partly INFERRED.

- Older rule: a member present in this table is Medicare, else commercial
  — INFERRED (implemented as presence-join in demand_report.py; superseded
  in dc_v2 by business_ln_cd = 'ME').
- **Date column SETTLED.** The column is `eff_dt`, and one row = one
  member-month, both confirmed in BigQuery (Resolved gap 6). PLAN.md's
  statement that "eff_df is the membership date column" is the historical
  error (source: expanded_scope/dc_v2/00_docs/PLAN.md; working code in
  expanded_scope/eda_runner.py and expanded_scope/30_dc_member_dim.py;
  data_decisions.md DD 08).

| Column | Type | Meaning | Status | Source |
|---|---|---|---|---|
| member_id | - | member identifier | VERIFIED-IN-CODE | expanded_scope/eda_runner.py |
| eff_dt | date-like | membership month; one row = enrolled that member-month | VERIFIED-IN-BQ | user confirmation (Resolved gap 6); eda_runner.py; data_decisions.md DD 08 |
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
cms_specialty (fan-out is real: VVMH -> Clinical Psychology + Clinical
Social Work; WHOS and VVRH likewise, see trap 15). COMPLIANCE counting
only - demand visit counting joins md1_ref_specialty_demand (2.15)
instead, per D12. Status: VERIFIED-IN-CODE.

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
expanded_scope/30a_dc_ref_ccir.py). 75,725 rows with the expected
chronic_label distribution (CHRONIC, NOT_CHRONIC, NO_DETERMINATION
observed) — VERIFIED-IN-BQ (Resolved gap 10).

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

### 2.15 `md1_ref_specialty_demand` (cfg.src) — demand-only specialty mapping

Built by model_and_dashboard_v1/02_foundation/05b_ref_specialty_demand.py
from the 2.6 compliance crosswalk by applying the D12 primary-pick
policy (WHOS -> Acute Inpatient Hospitals; VVRH -> Physical Therapy;
C -> Cardiology; CS -> Cardiothoracic Surgery; WBHF -> Outpatient
Behavioral Health). Grain: aetna_cd UNIQUE - exactly one CMS specialty
per code; the build fails loudly on any residual multi-map and never
auto-picks. Status: VERIFIED-IN-CODE.

| Column | Type | Meaning | Status | Source |
|---|---|---|---|---|
| aetna_cd | string | join key; holds specialty_ctg_cd values (same semantics as 2.6, trap 15) | VERIFIED-IN-CODE | 05b_ref_specialty_demand.py |
| cms_specialty | string | the single CMS specialty a demand visit counts toward | VERIFIED-IN-CODE | 05b_ref_specialty_demand.py |

Demand visit counting joins THIS table only. The 2.6 crosswalk stays
one-to-many BY DESIGN for compliance counting; joining it for visit
counting clones visits (trap 15, D12). CMS specialties dropped by the
pick policy leave the demand axis; compliance reporting keeps them.
Consumers: notebooks 14 and 15.

---

## 3. dc2_* derived tables (written by dc_v2 code)

All plain-dataset names via cfg.src(); all VERIFIED-IN-CODE as written.
Note: these are legitimate READ sources for the new pipeline, but their
correctness is not assumed (fresh-build rule).

Observed row counts (VERIFIED-IN-BQ): dc2_baselines 11,696;
dc2_capacity_county 197,397; dc2_capacity_predictions 197,397;
dc2_capacity_provider 4,895,666; dc2_capacity_provider_future 203,920;
dc2_demand_base 335,761; dc2_demand_chronic 469,364;
dc2_demand_predictions 335,761; dc2_weave 10,573.

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

1. Membership date column: SETTLED as `eff_dt` on mdcr_base_membership
   (VERIFIED-IN-BQ, Resolved gap 6); PLAN.md's `eff_df` reference is the
   historical error. The A870800 membership EXTRACT still uses
   `eff_yr`/`eff_mo` instead — two different date shapes across membership
   sources (sources: dc_v2/00_docs/PLAN.md; expanded_scope/eda_runner.py;
   dc_v2/03_demand/46_demand_history_table.py).
2. The claims table is named `_2025_claims` but holds a 2023-01-01 to
   2025-12-31 window (VERIFIED-IN-BQ, Resolved gap 2); dc_v2 code treats
   2023 as lookback memory only (sources: data_decisions.md DD 07;
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
10. `mbr_county_cd` is a CODE; `prvdr_county` is a NAME (UPPER/TRIM before
    joining county_name). County codes are stored as clean 5-character
    strings in both extracts (VERIFIED-IN-BQ, Resolved gap 4); LPAD stays
    as harmless defense. AZ FIPS still lose leading zeros in OTHER sources
    such as census geo_id — LPAD there is load-bearing (sources:
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
    Step3_specialty_cd_based_report.sql). The crosswalk is also
    one-to-many on aetna_cd BY DESIGN - one provider can satisfy
    several adequacy standards (WHOS spans Acute Inpatient Hospitals
    AND Outpatient Infusion/Chemo; VVRH spans four therapy standards) -
    and it must NEVER be joined for visit counting: the fan-out clones
    visits. Demand joins use md1_ref_specialty_demand only (section
    2.15, built by 05b under the D12 primary-pick policy).
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
21. The visit key and the 12-month new-patient rule use
    `epdb_dw_prvdr_id` (INT64; CAST to STRING when concatenating into the
    visit key). This fact was misrecorded once as srv_prvdr_id during a
    review misreading; scripts 01, 04 and 09 now carry schema asserts
    that guard it (VERIFIED-IN-BQ, INFORMATION_SCHEMA screenshot).
22. Provider county names need one normalization rule (Saint to St.,
    trim, upper) before capacity joins — the reference stores St. forms
    while claims carry Saint Lucie / Saint Johns variants plus blanks.
    Out-of-footprint provider volume (Boulder, Westchester, Dallas, Santa
    Fe and similar) is excluded from capacity by design, not lost
    (VERIFIED-IN-BQ, Resolved gap 5: unmatched share 0.1172).
23. Demand scope is CMS-recognized physician specialties via the 43-row
    bridge. The mapping was deliberately conservative: only Aetna
    specialty codes with a clean CMS equivalent (matched on
    specialty_ctg_cd and description) were bridged; ancillary, lab,
    mid-level, and ambiguous codes (WLAB, VVNP, VPTH, VVDM, P, VVPA, VER,
    VVHC and similar) were excluded by mapping policy. The 37.6 percent
    out-of-bridge share is intentional and stable, not data loss; no
    re-investigation needed. Any new pipeline restates this as a scope
    decision. If CMS specialty definitions or code descriptions change,
    the bridge needs re-review (VERIFIED-IN-BQ, Resolved gap 9).
24. Submarket columns (`mbr_submarket`, `prvdr_submarket`) are display
    context only: they span the whole book of business (NJ North, NY
    Metro, TX Houston, CA Bay Area and similar) and include nulls. Never
    use them as a footprint filter (VERIFIED-IN-BQ, Resolved gap 13).

---

## 5. Gaps to verify in BigQuery

Check queries for the open gaps live in
model_and_dashboard_v1/01_checks/test_data.sql.

### Open

No open BigQuery verification gaps remain as of this update.

### Resolved

1. RESOLVED, then CORRECTED (VERIFIED-IN-BQ): the provider id is
   `epdb_dw_prvdr_id` (INT64); `srv_prvdr_id` does not exist. The earlier
   resolution recording the opposite was a misreading during review;
   re-verified by INFORMATION_SCHEMA screenshot. `claim_line_id` remains
   absent.
2. RESOLVED (VERIFIED-IN-BQ): `srv_start_dt` window confirmed 2023-01-01
   to 2025-12-31.
3. RESOLVED (VERIFIED-IN-BQ): `business_ln_cd` carries exactly two
   values, CP and ME.
4. RESOLVED (VERIFIED-IN-BQ): zero rows with county-code length under 5
   in both claims and membership; codes are stored as clean 5-character
   strings. LPAD stays as harmless defense.
5. RESOLVED (VERIFIED-IN-BQ): prvdr_county match against the county
   reference — 733,188,951 total claim lines; 647,243,497 matched;
   unmatched share 0.1172 (11.7 percent). Unmatched decomposes into
   out-of-footprint counties (Boulder, Westchester, Dallas, Santa Fe and
   similar — excluded from capacity by design), Florida spelling variants
   (Saint Lucie and Saint Johns vs the reference's St. forms), and blank
   values.
6. RESOLVED (VERIFIED-IN-BQ): the mdcr_base_membership date column is
   `eff_dt`; one row = member-month confirmed. PLAN.md's `eff_df`
   reference is the historical error.
7. RESOLVED (VERIFIED-IN-BQ): `gender_cd` exists (STRING). Observed
   membership columns: member_id, eff_yr, eff_mo, age_nbr, gender_cd,
   zip_cd, mbr_county_cd, mbr_state, mbr_submarket. Monthly coverage
   continuous, no missing months, 2023-01 through 2025-12; distinct
   member counts rise from about 20.3M (2023-01) to about 22.5M
   (2025-12).
8. RESOLVED (VERIFIED-IN-BQ): HCC mapping columns diagnosis_code,
   description, HCC_v24, HCC_v28, is_pay_v24, is_pay_v28 all exist as
   STRING; both is_pay value sets are Yes, No, and dash.
9. RESOLVED (VERIFIED-IN-BQ): specialty bridge coverage — 733,186,951
   total claim lines; 275,612,726 outside the 43-row bridge; unmatched
   share 0.3759 (37.6 percent). Top unmatched codes are ancillary,
   facility, and mid-level categories (WLAB, VVNP, VPTH, VVDM, P, VVPA,
   VER, VVHC and similar), consistent with the bridge covering
   CMS-recognized physician specialties only; the out-of-bridge share is
   intentional mapping policy, not data loss (see Known traps 23).
10. RESOLVED (VERIFIED-IN-BQ): ms_dc_ref_ccir carries 75,725 rows with
    the expected chronic_label distribution (CHRONIC, NOT_CHRONIC,
    NO_DETERMINATION observed).
11. RESOLVED (VERIFIED-IN-BQ): hosp_list_cmi schema observed (Pin,
    Hospital_Name, System_id, System, TIN, Region, MarketHead,
    PlanMarket, Market, Sub_Market, Hospital_Type_DW, Par_Detail,
    Address, City, ST, Zip, County, Upin, Beds, Cmi, transfer_adj_cmi,
    Ip_Ccr, Ip_Type, Op_Ccr, Op_Type, BadDebtPercentage, Teaching,
    Urban_Rural, LastUpdated). State scope: Florida only, confirming the
    known regeneration need for OH, AZ, IL.
12. RESOLVED (VERIFIED-IN-BQ): dc2 tables exist with row counts —
    dc2_baselines 11,696; dc2_capacity_county 197,397;
    dc2_capacity_predictions 197,397; dc2_capacity_provider 4,895,666;
    dc2_capacity_provider_future 203,920; dc2_demand_base 335,761;
    dc2_demand_chronic 469,364; dc2_demand_predictions 335,761;
    dc2_weave 10,573.
13. RESOLVED (VERIFIED-IN-BQ): `mbr_submarket` and `prvdr_submarket`
    carry many values beyond the four footprint states (FL South, IL
    Chicago, FL West, AZ Phoenix, OH Columbus, NJ North, NY Metro, TX
    Houston, CA Bay Area and similar, plus null). Display context only;
    never a footprint filter.
