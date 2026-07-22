# 05_DECISIONS — model_and_dashboard_v1

Format: numbered decisions, newest at the bottom, never edited after the
fact; corrections become new entries. Each entry: number, date placeholder,
decision, reason, alternatives considered.

## D01 — [date]

Decision: Two deliverables: Excel report and Dash simulation.
Reason: One static, reviewable artifact and one interactive what-if tool
serve different audiences from the same numbers.
Alternatives considered: A single deliverable of either kind.

## D02 — [date]

Decision: Fresh pipeline: no assumption carried from prior dashboards or
pipelines; checks re-verify everything; dc_v2 tables usable as read-only
sources.
Reason: Prior pipelines carried untested assumptions; re-verification makes
every input defensible.
Alternatives considered: Extending dc_v2 in place; trusting its tables
without checks.

## D03 — [date]

Decision: Demand architecture: rate tables times user-set enrollment; rates
frozen during simulation; chained structure chosen over separate or
byproduct designs; dc_v2 demand model retained as external referee.
Reason: The chain is auditable arithmetic the dashboard can run live, and
the retained ML model gives an independent cross-check without entering the
simulation.
Alternatives considered: Separate per-output models; demand as a byproduct
of a provider model; running an ML model live inside the dashboard.

## D04 — [date]

Decision: Capacity is Aetna-relative by assumption, stated on all
deliverables.
Reason: Only Aetna claims are visible; a provider's non-Aetna workload is
unknown.
Alternatives considered: Estimating total practice capacity from external
benchmarks.

## D05 — [date]

Decision: Provider ceilings come from a model, monthly and yearly, not from
percentile cutoffs.
Reason: Percentile cutoffs proved crude in prior work; a model can use
panel mix and history per provider.
Alternatives considered: p75-style percentile ceilings; fixed per-specialty
constants.

## D06 — [date]

Decision: Model rigor: EDA, model, validation-EDA, generalization checks
for every model.
Reason: Every model must be explainable and shown to generalize before its
outputs are used downstream.
Alternatives considered: Build-and-ship without documented EDA or holdout.

## D07 — [date]

Decision: OPEN: conditions inside vs beside the demand math. Blocks
notebooks 08 and 13-15.
Reason: The two designs trade calibration simplicity against condition-mix
feedback; evidence from the check notebooks is needed first.
Alternatives considered: Deciding now without the joiner-vs-existing
evidence.
