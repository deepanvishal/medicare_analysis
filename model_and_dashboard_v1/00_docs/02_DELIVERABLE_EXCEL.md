# 02_DELIVERABLE_EXCEL — Excel Report Plan

## Purpose

One workbook holding the estimates plus full model transparency. It follows
the established house style: Arial, the DARK_BLUE/MID_BLUE/LIGHT_BLUE
scheme, and two header rows above every data sheet — a color-coded
block-tag row and a derivation-sentence row (italic, small, grey) with the
verbatim column name row beneath. The style is defined in practice by
expanded_scope/13_build_report.py (styling helpers, banded headers) and
expanded_scope/dc_v2/06_weave/56_final_report.py (block-tag row plus
derivation row, conditional status colors). Exact styling is copied from
those files, not reinvented.

## Sheet plan

### Sheet "master"

Estimates at county x specialty grain. One row per county x specialty.
Specialty axis: PENDING — specialty_ctg_cd vs cms_specialty; depends on
where (or whether) the crosswalk bridge sits in this pipeline.

Column blocks and intended columns:

**ID block**
- state_cd: state code of the county. As stored.
- county_fips: canonical county key from the county reference. As stored.
- county_name: display name from the county reference. As stored.
- specialty: the specialty key. PENDING the specialty-axis decision above.

**ACTUAL 2025 block**
- enrollment_2025: December 2025 in-scope members in the county (notebook
  06 output).
- visits_2025: visits that actually happened in 2025, member-county
  attribution (notebook 04 rollup).
- new_patient_share_2025: share of 2025 visits where the member had not
  seen that provider in the prior 12 months (notebooks 01 and 09).

**ESTIMATE 2026 block**
- enrollment_2026_expected: enrollment moved by the expected-growth model
  (notebook 11). PENDING until the growth model is built.
- demand_visits_2026: enrollment times sickness rates times visit rates.
  PENDING — blocked by the inside/beside decision (D07) and notebook 08.
- demand columns split by condition group: PENDING — exists only if the
  inside decision is taken.

**CAPACITY block**
- provider_count: providers serving the county x specialty (notebook 09).
- capacity_visits_2026: sum of per-provider modeled ceilings, yearly
  (notebook 17). PENDING until the capacity model is built.
- capacity_monthly_min: the tightest month's summed ceiling (notebook 17).
  PENDING until the capacity model is built.

**GAP block**
- gap_2026: demand_visits_2026 minus capacity_visits_2026; positive =
  shortage. PENDING on both parents.
- gap_status: UNDER/OVER label from the sign of gap_2026. PENDING on
  gap_2026.

**MODEL QUALITY block**
- calibration_error_pct: how far the assembled rate chain missed 2025
  actual visits (notebook 19).
- growth_model_error: held-out error of the growth model (notebook 12).
  PENDING until validation runs.
- capacity_model_error: held-out error of the capacity model (notebook
  18). PENDING until validation runs.
- referee_divergence_pct: chain output vs the dc_v2 demand model forecast
  (notebook 20).

**CONTEXT block**
- condition_mix_summary: top conditions by member count in the county
  (notebook 07).
- market_context: eligibles-based ceiling context. PENDING — source and
  definition to be confirmed during checks; not in any gap.

Conditional color follows the house pattern: gap red-positive /
green-negative; status labels always carry text, never color alone.

### Sheet "model_growth"

Top line, plain formula: expected 2026 enrollment per county x band =
f(enrollment history). Then sections:
- Inputs used (notebook 06 output; anything else added during EDA).
- Variables kept and dropped, with reasons, copied from notebook 10's EDA
  findings. PENDING until 10 runs.
- Validation results summary (notebook 12). PENDING.
- Generalization check summary (time or county holdout, notebook 12).
  PENDING.
- Known limitations, stated plainly.

### Sheet "model_visitsplit" (conditional)

Exists ONLY if the inside decision is taken (D07). Same structure as
model_growth: formula line, inputs, kept/dropped variables from notebook
13's EDA, validation (15), generalization (15), limitations. PENDING in
full.

### Sheet "model_capacity"

Top line, plain formula: per-provider monthly and yearly ceiling =
f(provider profile). Then the same five sections, drawing from notebooks
16 (EDA), 17 (model), 18 (validation and generalization). PENDING until
those run.

### Sheet "rates"

The frozen rate tables shipped with the report, each with its as-of date:
- enrollment (county x band, notebook 06)
- sickness rates (county x band x condition, notebook 07)
- visit rates (shape PENDING the inside/beside decision, notebook 08)
- intake shares (provider x specialty, notebook 09)

### Sheet "assumptions"

Each as its own visible row:
- Capacity is Aetna-relative: only Aetna claims are visible; a provider
  busy with other payers may look like they have room.
- Rates are frozen during simulation; sliders move enrollment only.
- Scope: members aged 60+, LOB in (CP, ME), footprint states FL, OH, AZ,
  IL.
- The tool is directional, not a precise forecaster.
- Attribution rule: demand uses member county; capacity uses provider
  county; never mixed.

## Build notes

Produced by notebook 22_excel_report.py with openpyxl, reusing the styling
helper pattern (fill, thin, cell, section_header, kv, title, derivation
rows) from expanded_scope/13_build_report.py and
expanded_scope/dc_v2/06_weave/56_final_report.py. Exact styling copied,
not reinvented. Every number on the master sheet must come from the
notebook-21 extracts so report and dashboard cannot disagree.
