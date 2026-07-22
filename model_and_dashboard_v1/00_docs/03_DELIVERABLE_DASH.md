# 03_DELIVERABLE_DASH — Dashboard Plan

## Purpose

Interactive what-if simulation. All interactivity is arithmetic on
precomputed coefficient tables. No live model calls. No BigQuery queries
fired by slider moves. The models run upstream and export rate tables;
the dashboard only multiplies and displays.

## Current mock state

Read from model_and_dashboard_v1/whatif_dashboard.py. Components that
exist today:

- MOCK DATA banner, visually prominent.
- County dropdown (single fictional option, TEST_COUNTY_FL) and Reset
  button.
- Collapsible Scope section, expanded by default, stating: directional
  tool, Aetna-relative capacity, routing assumes past choice behavior,
  rates frozen, all values fictional.
- Two-way synced sliders: master sets all band sliders to its value; a
  band slider moves only its band; total readout under the master
  recomputes from the band values; loop protection via dash.ctx and a
  sync store. Expected-growth default marks labeled "expected" (master +3,
  bands +2/+3/+6) and Reset returns to those defaults, not zero.
- Four KPI tiles: scenario enrollment, scenario annual visits, providers
  at capacity, providers over capacity. KPI counts run over all providers
  regardless of the display filter.
- Enrollment-by-band table.
- Dynamic top-N condition table with rank, movement markers (^k / vk / -),
  and an Other aggregate row; Top 10 / Top 20 toggle. Per D09 this is a
  true intermediate of the demand cascade: computed as members x sickness
  rates, and the same condition counts flow into the specialty demand
  numbers.
- Side-by-side horizontal top-N bar charts (specialty demand left,
  condition members right), gray baseline + green growth segments, dashed
  baseline marker on shrink, end-of-bar value labels; shared toggle. The
  condition chart shows the same true intermediate: members x sickness
  rates, feeding the specialty demand shown beside it.
- Specialty demand table with a display-only specialty multiselect (also
  filters the left chart and the provider table; never the math).
- Provider table: provider, specialty, current visits, estimated max
  (~ prefix), new demand coming (signed), room left ("short by N" when
  negative), status chips with text labels and color.
- Collapsible Mock coefficients section: prevalence, visit rates, provider
  config, read-only.

Differences from the target list to flag:
- The mock has THREE age bands (65-74, 75-84, 85+); the real build needs
  FOUR including 60-64.
- The mock carries 15 fictional conditions and 4 specialties, one
  fictional county, 10 fictional providers.
- Everything sits in one CONFIG block; there is no file loading yet.
- The mock still shows "expected" default marks (+3/+2/+3/+6) and Reset
  returns to them; per D11 the real build starts every slider at 0 with
  a "last year: +X%" info label instead, and Reset returns to 0.

## Mock-to-real gap list

Notebook numbers reference 00_MASTER_PLAN.md.

| Component | Mock behavior | Real-data behavior | Producing notebook | Open questions |
|---|---|---|---|---|
| County list | One fictional county | All footprint counties across FL, OH, AZ, IL; county dropdown becomes real and drives every table | 06 (enrollment), 21 (extract) | County display: name only or name + state |
| Age bands | Three bands (65-74, 75-84, 85+) | Four bands: 60-64, 65-74, 75-84, 85+; one more override slider | 03 (member spine), 06 | None |
| Condition list | 15 hardcoded conditions | Full condition list at HCC or CCIR level; top-N table absorbs the scale; a true intermediate of the demand cascade per D09 | 05 (flags), 07 (rates) | HCC vs CCIR level not decided |
| Specialty list | 4 hardcoded specialties | Full specialty list from claims; top-N chart absorbs the scale | 04 (visits base), 08 (rates) | Specialty axis: specialty_ctg_cd vs cms_specialty |
| Provider rows | 10 fictional providers | Real providers from providers.parquet (md1_capacity_v0, 16 v0 per D14): observed-peak ceilings labeled v0, intake_weight routing; modeled ceilings arrive with the 16-18 trio later; table likely paginated or top-N per county | 16 v0 (from 09 profile), 21 (extract) | Display cap per county |
| Slider defaults and last-year labels | Hardcoded +3 / +2 / +3 / +6 "expected" default marks, same everywhere; Reset returns to them | Defaults are 0 for every county and band (D11); an info label per slider shows last year's change from md1_growth_defaults ("last year: +X%"); the expected-growth tick concept is replaced by this label; Reset returns to 0. Enrollment moves are made at AEP and take effect January 1, so last year's change is context, not a starting position | 11 (context table), exported by 21 | None |
| Sickness and visit rates | Hardcoded 15x3 and 15x4 matrices | Frozen sickness rates per county x band x condition and per-condition visit rates (condition x specialty, plus the base rate); both drive demand per D09 | 07, 08 (after 13-15) | None |
| Scope section wording | Generic frozen-rates text | Scope text must state that demographic changes move condition counts, and condition counts move specialty demand; wording change lands when real data is wired in | 21 (with the real extracts) | None |
| Extract loading | CONFIG block in the .py file | Dashboard loads the seven parquet extracts + manifest.json produced by notebook 21 from 07_dashboard/extracts/; CONFIG block deleted | 21 | None - format decided: parquet |

## Extract contract

Notebook 21_dashboard_extracts.py exports these coefficient tables. The
dashboard reads ONLY these extracts; no other data source, no direct
BigQuery access. The demand formula is: members x sickness rates x
visit rates x county calibration factor (D13) - at baseline the
calibration factor makes the chain reproduce 2025 actuals exactly;
slider deltas are shaped by the rate tables.

| Extract | Grain | Content |
|---|---|---|
| enrollment.parquet | county x band | 2025-12 member counts with state_cd (md1_enrollment_history) |
| growth_context.parquet | county x band (incl ALL_BANDS rows) | last_year_yoy_pct in percentage points for the "last year: +X%" label beside each slider; slider defaults are 0 per D11 and are never taken from this file |
| sickness_rates.parquet | county x band x condition | 2025 tenure-ALL prevalence, non-null rows only, with description and chronic_label; feeds both the demand math and the condition display (D09) |
| visit_rates.parquet | specialty x condition (BASE_RATE and OTHER_CONDITION rows included) | md1_visit_rates (08): the visits[condition, specialty] coefficients from the visit-splitting model |
| county_calibration.parquet | county x specialty | md1_county_calibration shipped factor (08, D13): multiplied onto the demand chain; baseline reproduces 2025 actuals, small cells shrunken toward the state factor, clamped 0.1..3.0 |
| providers.parquet | provider | md1_capacity_v0 all columns (16 v0, D14): observed-peak ceilings, intake_weight routing by new-patient share; capacity is v0, labeled as such |
| conditions_meta.parquet | condition | distinct condition + description for display labels |

Format: parquet, written by 21_dashboard_extracts.py to
model_and_dashboard_v1/07_dashboard/extracts/ together with
manifest.json (row counts, source tables, build timestamp, and the line
"capacity=v0 observed-peak; demand=calibrated to 2025 actuals").

## Deployment note

Currently run via the Vertex Workbench proxy on port 8050 using the
DASH_PROXY_PREFIX pattern (requests_pathname_prefix when set, root path
otherwise; host 0.0.0.0, debug False). Sharing beyond the author is an
open item (Cloud Run conversation), not blocking build.
