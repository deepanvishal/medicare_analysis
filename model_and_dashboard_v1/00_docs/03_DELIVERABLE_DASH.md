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

## Mock-to-real gap list

Notebook numbers reference 00_MASTER_PLAN.md.

| Component | Mock behavior | Real-data behavior | Producing notebook | Open questions |
|---|---|---|---|---|
| County list | One fictional county | All footprint counties across FL, OH, AZ, IL; county dropdown becomes real and drives every table | 06 (enrollment), 21 (extract) | County display: name only or name + state |
| Age bands | Three bands (65-74, 75-84, 85+) | Four bands: 60-64, 65-74, 75-84, 85+; one more override slider | 03 (member spine), 06 | None |
| Condition list | 15 hardcoded conditions | Full condition list at HCC or CCIR level; top-N table absorbs the scale; a true intermediate of the demand cascade per D09 | 05 (flags), 07 (rates) | HCC vs CCIR level not decided |
| Specialty list | 4 hardcoded specialties | Full specialty list from claims; top-N chart absorbs the scale | 04 (visits base), 08 (rates) | Specialty axis: specialty_ctg_cd vs cms_specialty |
| Provider rows | 10 fictional providers | Real providers with intake shares and modeled ceilings; table likely paginated or top-N per county | 09 (profile), 17 (ceilings) | Display cap per county |
| Expected-growth defaults | Hardcoded +3 / +2 / +3 / +6, same everywhere | Per-county, per-band defaults from the growth model | 10-12, exported by 21 | Slider step vs off-step defaults |
| Sickness and visit rates | Hardcoded 15x3 and 15x4 matrices | Frozen sickness rates per county x band x condition and per-condition visit rates (condition x specialty, plus the base rate); both drive demand per D09 | 07, 08 (after 13-15) | None |
| Scope section wording | Generic frozen-rates text | Scope text must state that demographic changes move condition counts, and condition counts move specialty demand; wording change lands when real data is wired in | 21 (with the real extracts) | None |
| Extract loading | CONFIG block in the .py file | Dashboard loads coefficient files produced by notebook 21; CONFIG block deleted | 21 | File format: parquet or csv, PENDING |

## Extract contract

Notebook 21_dashboard_extracts.py exports these coefficient tables. The
dashboard reads ONLY these extracts; no other data source, no direct
BigQuery access.

| Extract | Grain | Content |
|---|---|---|
| enrollment_baseline | county x band | December-current member counts |
| growth_defaults | county x band | expected-growth slider defaults from the growth model |
| sickness_rates | county x band x condition | prevalence fractions; feeds both the demand math and the condition display (D09) |
| visit_rates | condition x specialty (plus the base rate for members with no mapped conditions) | per-condition annual visit rates from the visit-splitting model (D09) |
| provider_profile | provider x specialty | current visits, intake share, modeled monthly and yearly ceiling |
| county_reference | county | fips, name, state for dropdowns and labels |

Formats: parquet or csv, decided later; PENDING.

## Deployment note

Currently run via the Vertex Workbench proxy on port 8050 using the
DASH_PROXY_PREFIX pattern (requests_pathname_prefix when set, root path
otherwise; host 0.0.0.0, debug False). Sharing beyond the author is an
open item (Cloud Run conversation), not blocking build.
