# 00_MASTER_PLAN — model_and_dashboard_v1

## Purpose

This pipeline produces two deliverables: (1) an Excel report with demand and
capacity estimates plus per-model documentation sheets, and (2) an
interactive Dash what-if simulation. It is a fresh build: nothing is assumed
from prior pipelines; every input is re-verified by the check notebooks.
Existing dc_v2 tables may be READ as sources but their correctness is not
assumed.

## Architecture summary

Demand is assembled from rate tables: enrollment times sickness rates times
visit rates. The user's sliders change enrollment. All rates are frozen
during simulation. One existing ML model (the dc_v2 demand model) is kept
outside the dashboard as an independent cross-check.

## OPEN DECISION — read before writing any rate or model code

**Conditions inside vs beside the demand math — not yet decided.**
Notebooks 08 and 13-15 cannot be spec'd until this is closed.

- Inside: conditions sit in the demand chain itself — enrollment splits into
  condition groups and each group carries its own visit rates. Demand moves
  when the condition mix moves, at the cost of a larger, harder-to-calibrate
  rate stack.
- Beside: the demand chain runs on age bands only, and conditions are shown
  as a parallel display layer computed from prevalence. Simpler math and
  easier calibration, but a condition shift never feeds back into visit
  counts.

## Notebook sequence

| # | Folder | Filename | Purpose | Key inputs | Gate |
|---|--------|----------|---------|-----------|------|
| 00 | 01_checks | 00_data_availability.py | Years and months coverage of claims and membership, row counts, distinct members, no assumption carried | A870800_medicare_analysis_2025_claims, A870800_medicare_analysis_membership | Coverage windows confirmed and written into 01_DATA_DICTIONARY.md |
| 01 | 01_checks | 01_new_patient_definition.py | Re-derive and validate the new-patient rule (member x provider pair with no visit in prior 12 months) on current data | A870800_medicare_analysis_2025_claims | Rule reproduces plausible new-patient shares; definition frozen |
| 02 | 01_checks | 02_joiner_vs_existing.py | Compare condition prevalence of first-year members vs tenured members, same age bands; output decides one rate table vs two | A870800_medicare_analysis_2025_claims, A870800_medicare_analysis_membership, HCC_ICD_Mapping_2025 | One-vs-two rate table decision logged in 05_DECISIONS.md |
| 03 | 02_foundation | 03_member_base.py | In-scope member spine: age bands (60-64, 65-74, 75-84, 85+), county, tenure flag | A870800_medicare_analysis_membership, A870800_medicare_supply_demand_ms_ref_county | R1/R2/R6 assertions pass |
| 04 | 02_foundation | 04_visits_base.py | Visit-level table: member, provider, specialty, service date, visit key = distinct member x provider x date | A870800_medicare_analysis_2025_claims | Visit totals reconcile to claims (R1); R2 passes |
| 05 | 02_foundation | 05_condition_flags.py | Member x condition flags from diagnosis-to-HCC mapping | A870800_medicare_analysis_2025_claims, HCC_ICD_Mapping_2025 | Join hit rate reported; R7 passes |
| 06 | 03_rates | 06_enrollment_history.py | County x band x month member counts | output of 03 | Totals reconcile to member spine (R4) |
| 07 | 03_rates | 07_sickness_rates.py | Prevalence per county x band x condition | outputs of 03 and 05 | R4 reconciliation passes |
| 08 | 03_rates | 08_visit_rates.py | SPEC PENDING the inside/beside decision | TBD after checks | Blocked by the OPEN DECISION |
| 09 | 03_rates | 09_provider_profile.py | Per provider: current visits, new-patient share, panel age mix | outputs of 04 and 01 | R2/R3 pass at provider grain |
| 10 | 04_models | 10_growth_eda.py | EDA for expected-growth slider defaults | output of 06 | Variables documented with keep/drop/engineer reasons |
| 11 | 04_models | 11_growth_model.py | Fit the expected-growth model | output of 06, EDA findings from 10 | Seeded fit (R8); coefficients exported |
| 12 | 04_models | 12_growth_validation.py | Validation-EDA and generalization checks for growth | outputs of 06 and 11 | Out-of-sample test passes; behavior matches EDA patterns |
| 13 | 04_models | 13_visitsplit_eda.py | ONLY IF the inside decision is taken | TBD after checks | Blocked by the OPEN DECISION |
| 14 | 04_models | 14_visitsplit_model.py | ONLY IF the inside decision is taken | TBD after checks | Blocked by the OPEN DECISION |
| 15 | 04_models | 15_visitsplit_validation.py | ONLY IF the inside decision is taken | TBD after checks | Blocked by the OPEN DECISION |
| 16 | 04_models | 16_capacity_eda.py | EDA for per-provider ceilings | outputs of 04 and 09 | Variables documented with keep/drop/engineer reasons |
| 17 | 04_models | 17_capacity_model.py | Fit per-provider ceilings, monthly and yearly | outputs of 04 and 09, EDA findings from 16 | Seeded fit (R8); ceilings exported |
| 18 | 04_models | 18_capacity_validation.py | Validation-EDA and generalization checks for capacity | outputs of 09 and 17 | Out-of-sample test passes; behavior matches EDA patterns |
| 19 | 05_calibration | 19_chain_vs_2025_actuals.py | The assembled rate math must reproduce 2025 actual visits within a stated tolerance | outputs of 06-09, A870800_medicare_analysis_2025_claims | Tolerance met and stated; else rates rework |
| 20 | 05_calibration | 20_referee_comparison.py | Chain output vs dc_v2 demand model forecast | outputs of 19, dc2_demand_predictions | Divergence explained and logged |
| 21 | 06_outputs | 21_dashboard_extracts.py | The coefficient tables the dashboard loads | outputs of 06-09, 11, 17 | Extracts reconcile to sources (R4) |
| 22 | 06_outputs | 22_excel_report.py | Deliverable 1 | outputs of 21 and model documentation | Report builds; numbers match extracts |
| 23 | 07_dashboard | whatif_dashboard.py | Deliverable 2, real data wired in | outputs of 21 | Dashboard math matches 19 calibration |

## Model rigor requirement

Every model follows EDA -> build -> validation-EDA -> generalization checks.
The EDA documents each variable examined, the pattern found, and the keep,
drop, or engineer decision with the reason. The validation notebook confirms
the model's behavior matches the patterns the EDA found and includes at
least one out-of-sample test (time holdout or county holdout). Quality over
quantity.

## How to work on this pipeline

Read 00_MASTER_PLAN.md, 01_DATA_DICTIONARY.md, and 05_DECISIONS.md before
writing any code. Detailed notebook specs are written per phase, just in
time, not all upfront.
