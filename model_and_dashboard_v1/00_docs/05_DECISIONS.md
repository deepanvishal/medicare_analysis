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

## D08 — [date]

Closes D07.
Decision: Demand is computed directly from age-based visit rates - members
per age band times visits per band per specialty. The condition view stays
on the dashboard as a context display, computed as members times sickness
rates, but it does not feed the demand number.
Reason: Simpler and faster to build, fewer estimated quantities, avoids
the multi-condition visit-splitting problem entirely. Accepted trade-off:
the condition numbers are context rather than the driver, and no sickness
lever can move demand in this version.
Alternatives considered: Routing demand through condition prevalence and
per-condition visit rates; rejected for this version because it requires
an additional visit-splitting model to avoid double counting members with
multiple conditions.

## D09 — [date]

Supersedes and reverses D08; closes D07 with the INSIDE decision.
Decision: Demand is computed through conditions: demand per specialty =
sum over age bands and conditions of members[band] x
sickness_rate[band, condition] x visits[condition, specialty]. The
condition counts shown on the dashboard are a true ingredient of the
demand number, not a side display.
Reason: The causal story (demographics change conditions, conditions
change demand) must be real math the tool can defend, and it enables a
future sicker-population lever. Accepted cost: one additional model - the
visit-splitting model - that allocates a multi-condition member's visits
across their conditions so per-condition visit rates do not double count;
it follows the full EDA, build, validation, generalization rigor.
Alternatives considered: Computing demand directly from age-band visit
rates with conditions as context only (the D08 design); rejected because
the condition display would not drive demand and no sickness lever would
be possible.

## D10 — [date]

Correction entry.
Decision: The claims provider id fact is corrected from srv_prvdr_id to
epdb_dw_prvdr_id (INT64) after schema re-verification via
INFORMATION_SCHEMA. Scripts 01, 04, 09 and the data dictionary were
updated in this commit, and those scripts now carry schema asserts
guarding the column.
Reason: The earlier recording was a misreading during review; the
re-verified schema is authoritative.
Alternatives considered: None; a factual correction.

## D11 — [date]

Decision: Slider defaults are 0 for every county and band.
md1_growth_defaults is repurposed as a context table: its yoy is shown
next to each slider as "last year: +X%", never as the starting
position.
Reason: The executed notebook 12 backtest returned verdict REVIEW -
shrunken-method overall MAE 18.46 points vs 12.12 for zero-growth, and
the calibration is inverted (top predicted decile +29.29 predicted vs
-6.59 actual). Enrollment growth is made at AEP (annual enrollment
period): choices made October-December all take effect January 1, and
AEP outcomes swing year to year with plan design and competition. With
only three Decembers of history, last year's change points the wrong
way. Zero-growth beats everything.
Alternatives considered: Shipping the shrunken defaults; rejected
because the backtest calibration is inverted.

## D12 — [date]

Decision: Demand visit counting joins md1_ref_specialty_demand (one CMS
specialty per aetna code, built by notebook 05b); the compliance
crosswalk is never joined for visit counting.
Problem (recorded verbatim): The 43-row ref_specialty_crosswalk is
deliberately one-to-many on aetna_cd (e.g. WHOS maps to Acute Inpatient
Hospitals AND Outpatient Infusion/Chemo; VVRH maps to four therapy
specialties). Correct for compliance counting, where one provider
satisfies several standards; wrong for demand counting, where it clones
visits. Notebooks 14/15 inherited the fan-out; 15's twin numbers and
inflated reconstruction errors follow. Fix: a demand-only mapping with
exactly one CMS specialty per aetna code.
Primary-pick policy: WHOS -> Acute Inpatient Hospitals; VVRH ->
Physical Therapy; C -> Cardiology; CS -> Cardiothoracic Surgery;
WBHF -> Outpatient Behavioral Health; VVMH -> Clinical Psychology.
Any residual multi-map fails the 05b build loudly, listing the
offending codes and names; never auto-picked.
Residual closed: VVMH (Clinical Psychology AND Clinical Social Work in
the crosswalk - the last uncovered fan-out, which failed the first 05b
run by design) was picked as Clinical Psychology, the broader clinical
service line for the demand axis; VVMH is Mental Health Professional, a
shared proxy for both. Clinical Social Work drops from the demand axis;
compliance reporting keeps it. The policy now covers all six
multi-mapped codes in the 43-row crosswalk.
Consequence: the CMS specialty names not picked by the policy leave the
demand axis entirely (05b prints the dropped names at build time);
compliance reporting keeps them via the untouched one-to-many
crosswalk.
Alternatives considered: keeping the fan-out and deduplicating visits
downstream in every consumer; rejected because each consumer would need
the same dedupe and the rate-table grain would stay ambiguous.

## D13 — [date]

Decision: Accept the visit-split model with a county calibration layer.
Context: post-dedupe validation (executed 15) returned reconstruction
WAPE 27.27 percent, p90 57.8 - verdict REVIEW. Root cause: the split
model's rates are national; county-level utilization varies with local
practice patterns the model never sees. Anchor check remains advisory;
stability PASS.
Resolution: per county x specialty factor = actual 2025 bridged visits
/ model-predicted 2025 visits, so the assembled chain reproduces 2025
actuals exactly at baseline while the rate table shapes slider deltas.
Standard post-stratification; planned as notebook 19's reconcile step,
pulled forward into 08 (md1_county_calibration, beside the shipping
rate table md1_visit_rates). Protections: cells under 500 actual visits
shrink toward the state x specialty factor with weight n/(n+500); the
final factor clamps to 0.1..3.0 with the clamped count printed. The
demand formula becomes members x sickness rates x visit rates x county
calibration factor.
Alternatives considered: rejecting the model and fitting per-county
rates; rejected - county-level fits would be data-starved exactly where
calibration is weakest, and the national fit plus post-stratification
keeps slider deltas condition-driven.

## D14 — [date]

Decision: Ship v0 capacity now; the modeled ceiling (16-18 full trio)
follows later.
Reason: The MVP deadline requires capacity now. v0 ceiling = observed
data, not a model: per provider, annual ceiling = max observed monthly
visits (2024-2025) x 12; monthly ceiling = that max month. Explicitly
labeled v0 on every deliverable; replaced by the model in a later
phase. Providers are routed within county x specialty by new-patient
share (intake_weight in md1_capacity_v0).
Alternatives considered: waiting for the modeled ceilings; rejected for
the MVP timeline. Cross-provider percentile ceilings; rejected - a
provider's own observed peak is more defensible than a percentile cut
(consistent with D05's rejection of percentile cutoffs).
