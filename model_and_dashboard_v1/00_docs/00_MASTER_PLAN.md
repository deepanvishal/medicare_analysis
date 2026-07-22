# 00_MASTER_PLAN — model_and_dashboard_v1

## Purpose

This pipeline produces two deliverables: (1) an Excel report with demand and
capacity estimates plus per-model documentation sheets, and (2) an
interactive Dash what-if simulation. It is a fresh build: nothing is assumed
from prior pipelines; every input is re-verified by the check notebooks.
Existing dc_v2 tables may be READ as sources but their correctness is not
assumed.

## Architecture summary

Demand = enrollment by band x sickness rates x per-condition visit rates,
assembled in one pass. Rates are frozen during simulation. The condition
counts on screen are the actual intermediate of the demand calculation.
The user's sliders change enrollment. One existing ML model (the dc_v2
demand model) is kept outside the dashboard as an independent cross-check.

## DECISION CLOSED — see D09 in 05_DECISIONS.md

The demand-architecture decision is final: conditions sit INSIDE the
demand math; per-condition visit rates come via the visit-splitting model
(notebooks 13-15). D09 supersedes the earlier D08 closure.

## Notebook sequence

| # | Folder | Filename | Purpose | Key inputs | Gate |
|---|--------|----------|---------|-----------|------|
| 00 | 01_checks | 00_data_availability.py | Years and months coverage of claims and membership, row counts, distinct members, no assumption carried | A870800_medicare_analysis_2025_claims, A870800_medicare_analysis_membership | Coverage windows confirmed and written into 01_DATA_DICTIONARY.md |
| 01 | 01_checks | 01_new_patient_definition.py | Re-derive and validate the new-patient rule (member x provider pair with no visit in prior 12 months) on current data | A870800_medicare_analysis_2025_claims | Rule reproduces plausible new-patient shares; definition frozen |
| 02 | 01_checks | 02_joiner_vs_existing.py | Compare condition prevalence of first-year members vs tenured members, same age bands; output decides one rate table vs two | A870800_medicare_analysis_2025_claims, A870800_medicare_analysis_membership, HCC_ICD_Mapping_2025 | One-vs-two rate table decision logged in 05_DECISIONS.md |
| 03 | 02_foundation | 03_member_base.py | In-scope member spine: age bands (60-64, 65-74, 75-84, 85+), county, tenure flag | A870800_medicare_analysis_membership, A870800_medicare_supply_demand_ms_ref_county | R1/R2/R6 assertions pass |
| 04 | 02_foundation | 04_visits_base.py | Visit-level table: member, provider, specialty, service date, visit key = distinct member x provider x date | A870800_medicare_analysis_2025_claims | Visit totals reconcile to claims (R1); R2 passes |
| 05 | 02_foundation | 05_condition_flags.py | Member x condition flags from diagnosis-to-HCC mapping | A870800_medicare_analysis_2025_claims, HCC_ICD_Mapping_2025 | Join hit rate reported; R7 passes |
| 05b | 02_foundation | 05b_ref_specialty_demand.py | Demand-only specialty mapping: exactly one CMS specialty per aetna code via the D12 primary-pick policy; 14 and 15 join it, the compliance crosswalk stays one-to-many for adequacy counting | A870800_medicare_supply_demand_ref_specialty_crosswalk | aetna_cd unique (R2); residual multi-maps fail loudly, never auto-picked |
| 06 | 03_rates | 06_enrollment_history.py | County x band x month member counts | output of 03 | Totals reconcile to member spine (R4) |
| 07 | 03_rates | 07_sickness_rates.py | Prevalence per county x band x condition; feeds BOTH the demand math and the dashboard condition display | outputs of 03 and 05 | R4 reconciliation passes |
| 08 | 03_rates | 08_visit_rates.py | Ships the dashboard rate table (md1_visit_rates, from the 14 fit) AND the county x specialty calibration factors (md1_county_calibration) so the assembled chain reproduces 2025 actuals at baseline (D13 - calibration pulled forward from 19); depends on 13-15 | outputs of 13-15, 04 and 05, md1_ref_specialty_demand (05b - never the compliance crosswalk, per D12) | R2 keys + factor bounds; R4 actuals reconciliation + calibration identity |
| 09 | 03_rates | 09_provider_profile.py | Per provider: current visits, new-patient share, panel age mix | outputs of 04 and 01 | R2/R3 pass at provider grain |
| 10 | 04_models | 10_growth_eda.py | EDA behind the growth method; its findings fed the 11/12 build and D11 (slider defaults are 0; the growth table is context) | output of 06 | Variables documented with keep/drop/engineer reasons |
| 11 | 04_models | 11_growth_model.py | EXECUTED. Context table for last-year labels: md1_growth_defaults yoy is shown beside each slider as "last year: +X%"; slider defaults are 0 per D11 | output of 06, EDA findings from 10 | md1_growth_defaults built; repurposed as context per D11 |
| 12 | 04_models | 12_growth_validation.py | EXECUTED. Backtest of the growth method against the actual Dec 2024 to Dec 2025 change | outputs of 06 and 11 | Verdict REVIEW - zero-growth beat the shrunken method; slider defaults set to 0, see D11 |
| 13 | 04_models | 13_visitsplit_eda.py | EDA for the visit-splitting model: how multi-condition members' visits distribute across their conditions | outputs of 04 and 05 | Variables documented with keep/drop/engineer reasons |
| 14 | 04_models | 14_visitsplit_model.py | Fit the visit-splitting model: allocate each member's visits across their conditions without double counting | outputs of 04 and 05, EDA findings from 13 | Seeded fit (R8); allocations sum to each member's observed visits |
| 15 | 04_models | 15_visitsplit_validation.py | Validation-EDA and generalization checks for visit splitting | outputs of 05 and 14 | Out-of-sample test passes; behavior matches EDA patterns |
| 16 | 04_models | 16_capacity_v0.py (shipped) + 16_capacity_eda.py (pending) | v0 capacity per D14: ceiling = provider's max observed month (2024-2025) x 12, intake_weight = new-patient share within county x specialty; explicitly labeled v0, replaced later by the modeled 16-18 trio (EDA still pending) | outputs of 04, 09, 05b | md1_capacity_v0: R2 key + ceiling floor, R4 intake weights; full-trio EDA gate pending |
| 17 | 04_models | 17_capacity_model.py | Fit per-provider ceilings, monthly and yearly | outputs of 04 and 09, EDA findings from 16 | Seeded fit (R8); ceilings exported |
| 18 | 04_models | 18_capacity_validation.py | Validation-EDA and generalization checks for capacity | outputs of 09 and 17 | Out-of-sample test passes; behavior matches EDA patterns |
| 19 | 05_calibration | 19_chain_vs_2025_actuals.py | End-to-end chain verification against 2025 actuals using 08's tables (calibration itself now built in 08 per D13): confirms enrollment -> conditions -> visit rates -> calibration reproduces baseline actuals | outputs of 06-09 (incl. md1_visit_rates and md1_county_calibration), A870800_medicare_analysis_2025_claims | Baseline reproduction confirmed; deviations explained |
| 20 | 05_calibration | 20_referee_comparison.py | Chain output vs dc_v2 demand model forecast | outputs of 19, dc2_demand_predictions | Divergence explained and logged |
| 21 | 06_outputs | 21_dashboard_extracts.py | EXECUTED SPEC. Writes the seven parquet extracts + manifest.json to 07_dashboard/extracts/: enrollment, growth_context (label only, defaults 0 per D11), sickness_rates, visit_rates, county_calibration, providers (capacity v0 per D14), conditions_meta | outputs of 06, 07, 08, 11 (context), 16 v0 | No empty extract (R1); enrollment counties match 2025-12 exactly (R4); manifest carries the capacity=v0 line |
| 22 | 06_outputs | 22_excel_report.py | Deliverable 1 | outputs of 21 and model documentation | Report builds; numbers match extracts |
| 23 | 07_dashboard | whatif_dashboard.py | Deliverable 2, WIRED (MVP): loads the seven 21 extracts + manifest; defaults 0 with last-year labels; capacity v0 banner | outputs of 21 | Dashboard math matches 19 calibration |

Run-order exception: notebook 08 runs AFTER 13-15 (it consumes the
visit-splitting model), despite its lower number. Notebook 05b runs
BEFORE 14 and 15 - they join its demand mapping.

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
