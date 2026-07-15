# dc_v2 — Prompt Pack (execution order)

Paste each prompt into Claude Code separately, in this order. Run the
notebooks between prompts as noted. Every prompt is self-contained.

STATUS: all prompts below (1-7 and follow-ups F1-F3) are APPLIED in this
repo as of 2026-07-15. This file is the paste-ready record.

RUN ORDER OVERVIEW:
1. Prompt 1 (patch 46 + 48, age floor) -> run 46, run 48
2. Prompt 2 (patch 50 + 51, band renames) -> nothing to run yet
3. Prompt 3 (patch 51, persist provider future predictions) -> run 50, run 51
4. Prompt 4 (new 53) -> run 53
5. Prompt 5 (new 55) -> run 55, send sanity prints back before continuing
6. Prompt 6 (new 56) -> run 56
7. Prompt 7 (new 57) -> run 57
F1. Delete superseded 53_p75_baseline_extract.py (done); drop old
    dc2_p75_baseline table in BigQuery when convenient.
F2. Patch 57 Provider Level merge-key dtype (done).
F3. SAFE_DIVIDE audit sweep across 53/55/56/57 (done).

---

## PROMPT 1 — age floor patch (46 + 48)

You cannot run anything. Your only output is edits to two files:
`expanded_scope/dc_v2/03_demand/46_demand_history_table.py` and
`expanded_scope/dc_v2/04_capacity/48_provider_history_table.py`.

In BOTH files:
1. Every query reading the claims extract adds: `AND age_nbr >= 60`
   (claims-side age).
2. Every query reading the membership extract adds: `AND age_nbr >= 60`.
3. Age band buckets change everywhere from lt65/65_74/75_84/85p to:
   age_60_64 (60-64), age_65_74, age_75_84, age_85p. Rename the columns
   accordingly: mbr_age_60_64, mbr_age_65_74, mbr_age_75_84, mbr_age_85p in
   46; panel_60_64, panel_65_74, panel_75_84, panel_85p in 48.
4. In each header block, add one line: "SCOPE: CP and ME members aged 60+;
   under-60 members and their claims are excluded from every number in this
   table."

No other logic changes. The chronic lookback, visit definition, targets, and
attribution rules are untouched.

---

## PROMPT 2 — band rename patch (50 + 51)

You cannot run anything. Your only output is edits to
`expanded_scope/dc_v2/05_models/50_demand_model.py` and
`expanded_scope/dc_v2/05_models/51_capacity_models.py`. The age-band feature
column names changed: in 50, mbr_lt65/mbr_65_74/mbr_75_84/mbr_85p become
mbr_age_60_64/mbr_age_65_74/mbr_age_75_84/mbr_age_85p. In 51,
panel_lt65/panel_65_74/panel_75_84/panel_85p become
panel_60_64/panel_65_74/panel_75_84/panel_85p. Update feature lists and any
references. No other changes.

---

## PROMPT 3 — persist provider future predictions (51)

You cannot run anything. Your only output is edits to one file:
`expanded_scope/dc_v2/05_models/51_capacity_models.py`.

Currently the provider model's chunked scores are aggregated to county and
discarded. Change: persist the provider-grain scores for the FUTURE month
only. Exactly this:

1. In the provider scoring loop (per task), after `pred_scored` exists and
   before aggregation: select the subset of scored rows where month ==
   FUTURE_MONTH and collect epdb_dw_prvdr_id, specialty_ctg_cd,
   prvdr_county, month, and the prediction into a small frame per task.
   Accumulate across the two tasks by merging on the provider keys so one
   frame carries columns provider_pred_next_1m and provider_pred_next_12m
   (exact names).
2. After both tasks complete (before the provider frame is freed), write
   this frame to BigQuery table `{cfg dataset}.dc2_capacity_provider_future`
   via the same write mechanism used for the main predictions table,
   WRITE_TRUNCATE. Log the write with row count.
3. Memory guard: the future-month subset is small (one month of providers);
   build it from the already-scored chunks — do not re-score, do not retain
   full-period provider scores.
4. Add to sanity prints: row count of the future provider table, and
   SUM(provider_pred_next_12m) vs the county-aggregated bottom_up_next_12m
   future total — must match (same numbers, pre- and post-aggregation).

No changes to models, features, splits, metrics, or existing outputs.

---

## PROMPT 4 — notebook 53, baselines and ceilings

You cannot run anything. Do not execute any queries. Your only output is one
new file: `expanded_scope/dc_v2/05_models/53_baselines_and_ceilings.py`.

Read PLAN.md, config.py, and — before writing any SQL — read
`expanded_scope/31_dc_county_population.py`, `32_dc_rate.py`,
`33_dc_demand.py`, `35_dc_capacity.py`, `36_dc_gap.py` to identify the exact
v1 output table names and column names. Do not guess any name.

Creates one BigQuery table `{cfg dataset}.dc2_baselines` at county x
cms_specialty grain with exactly these columns:
1. state_cd, county_fips, cms_specialty — from the 36 gap table.
2. capacity_current — 36's capacity_visits, carried verbatim.
3. demand_current_book — cohort-clean current-method demand: ENROLLED
   members (from the membership extract
   `A870800_medicare_analysis_membership`, age_nbr >= 60, December 2025
   rows) x the v1 visit rate from the 32 rate table, at the same
   age/condition pooling 33 uses — read 33 and reproduce its formula with
   the enrolled-member count replacing the eligible count. State in the
   header block exactly which 33 lines were reproduced.
4. gap_current_book — demand_current_book minus capacity_current.
5. market_max_demand — 36's ma_demand_visits carried verbatim, renamed,
   derivation noted in header: "eligibles-based demand, ceiling context
   only."
6. source_note STRING = 'v1 pipeline + cohort-clean rebuild, age 60+'.

SANITY PRINTS: row count; SUM of each measure; count of rows where
demand_current_book > market_max_demand (should be near zero — enrolled
demand exceeding eligible ceiling means a rate mismatch; print the top 10
such rows if any exist).

---

## PROMPT 5 — notebook 55, weave

You cannot run anything. Your only output is one new file:
`expanded_scope/dc_v2/06_weave/55_weave.py`.

Same bootstrap as notebooks 40/46/48. Read PLAN.md and `36_dc_gap.py` (for
the specialty bridge pattern — reuse verbatim). Creates
`{cfg dataset}.dc2_weave` and prints sanity checks.

INPUTS: dc2_demand_predictions, dc2_capacity_predictions,
dc2_capacity_provider_future, dc2_baselines, ref_specialty_crosswalk,
dc2_capacity_county.

BUILD:
1. Future rows only (split_label='future') from demand and capacity
   predictions. XGB columns only for demand; bottom_up columns only for
   capacity. Linear and top_down columns must not appear anywhere.
2. Bridge specialty_ctg_cd -> cms_specialty (36 pattern; the only bridge
   point). SUM to county x cms_specialty. Print leakage: rows and visit
   volume with no cms_specialty match, demand and capacity separately.
3. FULL OUTER join demand and capacity on county x cms_specialty.
4. Join dc2_baselines on county x cms_specialty.
5. capacity_potential_p75 — from dc2_capacity_provider_future bridged the
   same way: per county x cms_specialty,
   PERCENTILE_CONT(provider_pred_next_12m, 0.75) x COUNT(providers).
6. Compute: gap_model_2026 = demand_next_12m_xgb minus
   capacity_next_12m_bottom_up; capacity_to_demand_ratio = SAFE division of
   capacity_next_12m_bottom_up by demand_next_12m_xgb; gap_status = 'UNDER'
   where gap_model_2026 > 0 else 'OVER' (exactly two values, NULL where
   either side missing).
7. expected_error_band: per county, MAPE of pred_next_1m_xgb vs
   actual_next_1m over validation rows with actual >= 10 (demand side),
   county-level average. Band: 'A' <= 0.25, 'B' <= 0.50, 'C' otherwise;
   carry the raw value as expected_error_pct. Counties with no qualifying
   validation rows: band 'C', expected_error_pct NULL.
8. pct_medicare_age_members: per county from the membership extract,
   December 2025: members age 65+ divided by ALL members (no age filter on
   the denominator).

OUTPUT COLUMNS (exact order): state_cd, county_fips, cms_specialty,
demand_current_book, capacity_current, gap_current_book,
demand_next_12m_xgb, capacity_next_12m_bottom_up, gap_model_2026,
capacity_to_demand_ratio, capacity_potential_p75, gap_status,
expected_error_pct, expected_error_band, pct_medicare_age_members,
market_max_demand.

SANITY PRINTS: row count; leakage from step 2; counties demand-only /
capacity-only / both; SUM demand vs SUM capacity (model columns); count per
gap_status value; count per expected_error_band value.

---

## PROMPT 6 — notebook 56, master report

You cannot run anything. Your only output is one new file:
`expanded_scope/dc_v2/06_weave/56_final_report.py`.

Same bootstrap. Read `13_build_report.py` first and copy its styling helpers
verbatim. Read PLAN.md. INPUT: dc2_weave (+ dc2_demand_base,
dc2_demand_chronic, dc2_capacity_provider for the input tabs). OUTPUT:
`medicare_demand_capacity_dc2.xlsx` at repo root via cfg.repo_path.

TABS in order:

1. Overview — plain words, no unexplained acronyms. States: scope = CP and
   ME members aged 60 and above; predictions are for calendar 2026 from
   models trained on 2024-2025; the three caveats as separate visible rows
   (capacity counts only visits delivered to our members, not the
   provider's whole practice; condition measures use each visit's main
   diagnosis only, so they undercount; rows with expected_error_band C
   should be read at submarket or state rollup, not individually);
   plan-type detail lives in the v1 compliance report, this report is
   county x specialty.
2. Gap 2026 (master) — one row per county x cms_specialty from dc2_weave,
   ALL columns, verbatim names as headers. Derivation row (italic grey,
   small) above every column, one plain sentence each; over gap_status:
   "UNDER = members need more visits than providers can deliver; OVER = the
   reverse; no middle label — read the gap and ratio columns for degree";
   over expected_error_band: "A = model missed by 25% or less on months it
   never saw; B = up to 50%; C = more — roll up before reading"; over
   capacity_potential_p75: "if every provider here delivered like the
   busiest quarter of their local peers"; over market_max_demand:
   "ceiling — every Medicare-eligible in the county, not just our members;
   context only, in no gap". Conditional color: gap_model_2026 red positive
   / green negative; expected_error_band A green / B gold / C red.
3. Answers — three sections, each a ranked table: (1) "Where are we short?"
   top 25 by gap_model_2026 descending, band A and B rows only, with a
   one-line note that C rows are excluded here and appear in the master
   tab; (2) "Where do we have excess?" top 25 by capacity_to_demand_ratio
   descending, A/B only; (3) "Watch list" — rows where gap_current_book and
   gap_model_2026 disagree in sign, A/B only: the current method and the
   model see different directions — these need human eyes.
4. Demand Inputs — per county rollup from dc2_demand_base + top-5
   prevalence HCC columns from dc2_demand_chronic (2025 values), headers
   verbatim, derivation rows.
5. Capacity Inputs — per county rollup from dc2_capacity_provider (2025),
   same conventions.
6. Worked Examples — largest county x specialty cell by 2024 visits, real
   numbers: pct_new_patients computation (numerator, denominator, result),
   one prev_hcc computation, gap_model_2026 computation. Three labeled rows
   each, one-sentence captions.
7. Data Dictionary — every dc2_weave column: verbatim name, plain-words
   meaning, source, derived-or-stored.
8. Methodology — plain words: the demand model paragraph, the capacity
   model paragraph, current-method paragraph, gap and ratio paragraph,
   expected_error_band paragraph (measured on unseen 2025 months,
   boundaries 25% and 50%), model quality numbers stated plainly, "gradient
   boosted trees (XGBoost)" named once with a one-line explanation. Then
   "Other model designs considered": (1) per-level forecasts with
   reconciliation — unnecessary once county became the single modeling
   level; (2) county-level top-down capacity model — built, tested, lost to
   the provider-level model in every county size band on held-out 2025
   data, dropped; (3) cluster-then-model (10-20 cluster models instead of
   one pooled model) — planned next step if per-segment explanation is
   requested, and the route to the cluster-average third estimate from the
   original design.

No invented display names. No jargon in prose. House header block.

---

## PROMPT 7 — notebook 57, fine-grain predictions report

You cannot run anything. Your only output is one new file:
`expanded_scope/dc_v2/06_weave/57_finegrain_report.py`.

Same bootstrap, same styling reuse from 13_build_report.py as notebook 56.
Read PLAN.md.

OUTPUT: `medicare_demand_capacity_finegrain.xlsx` at repo root.

PURPOSE: predictions at the lowest honest grain, each carrying an
expected-error column measured from actual past performance — not claimed,
measured.

BUILD:
1. Demand error rates: from dc2_demand_predictions validation rows
   (split_label='validation'), compute per county x specialty: mape_hist =
   AVG(ABS(pred_next_1m_xgb - actual_next_1m) / actual_next_1m) over rows
   with actual_next_1m >= 10; and mae_hist = AVG(ABS(pred_next_1m_xgb -
   actual_next_1m)) over all rows. Where a cell has no qualifying
   validation rows, fall back to the county-level average, then the
   error-band-level average; add error_basis column with value 'cell' /
   'county' / 'band' accordingly.
2. Tab "Demand by County-Specialty": future rows (2026 predictions):
   mbr_county_cd, specialty_ctg_cd, pred_next_1m_xgb, pred_next_12m_xgb,
   mae_hist, mape_hist, error_basis. Derivation row above each column; over
   mape_hist: "average percent miss of this same model on 2025 months it
   did not train on, for this county and specialty"; over error_basis:
   "where the error estimate comes from: this exact cell, this county
   overall, or counties with similar error levels".
3. Capacity error rates: same construction from dc2_capacity_predictions
   validation rows using bottom_up_next_1m vs actual_next_1m at county x
   specialty grain.
4. Tab "Capacity by County-Specialty": future rows: prvdr_county,
   specialty_ctg_cd, bottom_up_next_1m, bottom_up_next_12m, mae_hist,
   mape_hist, error_basis; same derivation convention.
5. Tab "Provider Level": from dc2_capacity_provider_future joined to
   dc2_capacity_provider (December 2025 row for inputs): epdb_dw_prvdr_id,
   specialty_ctg_cd, prvdr_county, provider_pred_next_1m,
   provider_pred_next_12m, panel_members, pct_new_patients, tenure_months,
   plus 2025 actual visits total. Derivation row over the two prediction
   columns: "model estimate of visits this provider will deliver (next
   month / calendar 2026)". Cap at top 5,000 providers by
   provider_pred_next_12m, note the cap in the title row. Error columns:
   county-level mape_hist from the capacity error table where available,
   error_basis stated, with derivation row stating provider-level error was
   not individually validated.
6. Tab "How to read the error columns": plain-words half page: what
   mae_hist and mape_hist mean, that they are measured on months the model
   never saw, that thin cells carry county- or band-level error because
   their own history is too thin, and one worked example with real numbers
   (largest county x specialty cell: its prediction, its historical error,
   the plausible range that implies).

SANITY PRINTS: row counts per tab; count of cells per error_basis value.

---

## FOLLOW-UP F1 — delete superseded 53 extract

You cannot run anything. Delete
expanded_scope/dc_v2/05_models/53_p75_baseline_extract.py. Your only output
is that deletion.

(The old dc2_p75_baseline BigQuery table is dropped manually when
convenient: DROP TABLE IF EXISTS
`anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.dc2_p75_baseline`;
nothing reads it anymore.)

---

## FOLLOW-UP F2 — 57 Provider Level merge-key dtype fix

You cannot run anything. Your only output is edits to one file:
expanded_scope/dc_v2/06_weave/57_finegrain_report.py.

The Provider Level tab merge fails: epdb_dw_prvdr_id is object dtype in one
frame and Int64 in the other. Fix: immediately after each of the two
provider frames is loaded (dc2_capacity_provider_future and
dc2_capacity_provider), normalize the key with the same expression:
df['epdb_dw_prvdr_id'] = pd.to_numeric(df['epdb_dw_prvdr_id'],
errors='coerce').astype('Int64'). Add one printed line per frame after the
cast: rows where the key is null (must be 0; if nonzero, print 5 sample raw
values before the cast so the bad format is visible). Do the same
normalization for any other merge keys shared by the two frames
(specialty_ctg_cd as str on both sides). No other changes.

---

## FOLLOW-UP F3 — SAFE_DIVIDE audit sweep (53 / 55 / 56 / 57)

You cannot run anything. Your only output is edits, where needed, to these
files: expanded_scope/dc_v2/05_models/53_baselines_and_ceilings.py,
expanded_scope/dc_v2/06_weave/55_weave.py,
expanded_scope/dc_v2/06_weave/56_final_report.py,
expanded_scope/dc_v2/06_weave/57_finegrain_report.py.

Audit every division. (1) In all BigQuery SQL strings: replace any bare
a / b with SAFE_DIVIDE(a, b), preserving any ROUND wrappers. (2) In all
pandas/numpy division: guard against divide-by-zero and inf — use np.where
on a nonzero denominator or .replace([np.inf, -np.inf], np.nan) immediately
after the division, whichever is minimal for that line. (3) Print, per
file, a one-line summary at the top of the edit response listing each
division you found and whether it was already safe or changed. Make no
changes to any division that is already guarded. No other changes of any
kind.
