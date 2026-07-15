# dc_v2 — Data Decisions Log

**Location:** `expanded_scope/dc_v2/00_docs/data_decisions.md`
One file, all decisions. Each decision gets a section: question, approach, decision rule, conclusion. Supporting analysis outputs (CSVs, charts) live in subfolders named per decision (dd_01/, dd_02/, ...).

---

## DD 01 — Defining New vs Returning Patient

**Folder:** `expanded_scope/dc_v2/07_data_decisions/dd_01_new_vs_returning/`
**Status:** Analysis planned, not yet run
**Owner:** Deepan

### Question

How do we decide if a patient visiting a provider is "new" or "returning", given our claims history is limited and gets cut off at some earlier date we haven't confirmed yet?
### Why it matters

New vs returning patient mix is a key input to provider capacity modeling. A wrong definition either overstates new patients (too short a memory) or is unusable (too long a memory for the data we have).

### The problem

- We do not know how far back our claims data goes. First step of the analysis confirms this.
- In the earliest months of data, almost every patient looks "new" simply because we can't see their earlier visits. This is fake newness, not real.
- Over time this effect fades. The question is: how fast, and under which definition?

### Approach

Instead of picking a lookback window upfront, compute the new-patient share under several candidate definitions and let the curves decide.

For every visit (member x provider x month), flag the patient as returning if they visited that same provider:

1. **Ever before** (any prior claim in our data)
2. **In the last 12 months**
3. **In the last 24 months**
4. **In the last 36 months**

Then compute, per month: `% of visiting patients that are new` under each definition.

### What we expect

- All four curves start high (~90% new) in the earliest months — this is the missing-history artifact.
- Each curve drops and flattens as real history accumulates.
- The month where a curve flattens is where that definition becomes trustworthy.

### Decision rule

Pick the definition that:

1. Flattens earliest (maximizes usable months for the time series), and
2. Still makes clinical/business sense (a patient gone 5 years is reasonably "new" again).

Note: the CPT/E&M billing convention uses 3 years. If our data can't support 36 months of lookback, we deliberately deviate and document the deviation here, with an estimate of how many patients get misclassified by the shorter window.

### Analysis plan (one script)

**Scope:** one state sample (FL) to start. Full run after the definition is picked.

**Steps:**

1. Profile the data window first: per provider — first claim month, last claim month, total months, largest gap. Also the overall min/max claim date. This tells us what we actually have.
2. Build member x provider x month visit table.
3. For each visit month, compute prior-visit flags under the four definitions.
4. Output table: `month | definition | new_patient_pct | visiting_patients | providers`.
5. Chart: four lines (one per definition), new % by month.

**Output files:**
- `dd_01_profile.csv` — data window profile
- `dd_01_new_pct_by_month.csv` — the four curves
- `dd_01_chart.png` — the visual
- This markdown updated with the conclusion once run.

### Downstream consequences

- The chosen definition sets the burn-in period (months consumed as memory before the series starts).
- Usable series length = total history minus burn-in. This decides which forecast method is realistic (shorter series = simpler model).
- Providers with too little history fall to the cluster-average estimate instead of their own forecast.

### Conclusion

_To be filled after the analysis runs._
-e 
---

## DD 02 — Demand Base: Membership Data or Not

**Folder:** `expanded_scope/dc_v2/07_data_decisions/dd_02_demand_base/`
**Status:** Noted, membership data to be brought in by Deepan
**Owner:** Deepan

### Question

What population number sits under the demand formula, and at what time grain?

### What we know

- Deepan has membership data at both monthly and yearly grain.
- Preference: keep it simpler — use **yearly membership**.
- The current codebase has no membership history at all — only a single current
  snapshot of county Medicare eligibles (CMS penetration file). That cannot move
  over time, which is why this decision exists.

### The binary decision

**If membership data is available (expected case):**

```
demand(geo, year, age_band, hcc_chronic_condition, specialty)
  = members(geo, year, age_band, hcc_chronic_condition)
  x visit_rate(age_band, hcc_chronic_condition, specialty)
```

- Members counted per year per geo, bucketed by age band and HCC chronic
  condition flag(s).
- Visit rate computed from claims: visits per member per year, within the same
  buckets.
- Time series is yearly. Fewer points than monthly, but cleaner and simpler.

**If membership data is NOT available (fallback):**

```
demand(geo, year, age_band, hcc_chronic_condition, specialty)
  = eligibles(county, year)
  x share(age_band, hcc_chronic_condition | geo)
  x visit_rate(age_band, hcc_chronic_condition, specialty)
```

- Eligibles from the CMS penetration file (county, yearly snapshots if multiple
  ingests exist).
- Age band and chronic condition mix estimated from claims/census, applied as
  shares on top of the eligible count.
- Weaker: the mix shares are modeled, not observed.

### Mandatory factors (no exceptions)

- Age band buckets
- HCC-based chronic conditions (definition comes from the hcc_chronic
  subproject)
- Specialty
- Geography

### Grain and reconciliation

Prediction grain is flexible — the model can predict at zip, county, or state,
whichever the data supports best. The rule is:

**Whatever level it is predicted at, all levels must reconcile to the same
grain.** Zip rolls up to county, county rolls up to state, and the totals must
agree. If predictions are made independently at multiple levels, a
reconciliation step forces them to a single consistent set of numbers.

### Consequences

- Yearly grain means the forecast estimate leans on simple trend models
  (linear / regression with year), not seasonal models — there is no
  within-year seasonality at yearly grain.
- The three-estimate structure (forecast / cluster-average / p75 baseline)
  still applies unchanged.

### Open items

- Deepan to confirm the membership table/source and how many years it covers.
- Confirm date of birth (for age bands) and zip exist in it.
- LOB filter assumed CP and ME — confirm still true for this source.

### Conclusion

_Pending: membership data confirmed available -> use the membership formula.
Fill in source table, years covered, and coverage stats once profiled._

---

## DD 03 — Prediction Model vs Forecast Model for Demand

**Folder:** `expanded_scope/dc_v2/07_data_decisions/dd_03_prediction_vs_forecast/`
**Status:** Analysis planned, blocked on demand history table
**Owner:** Deepan

### Question

Should demand be estimated with a forecast model (time series: trend,
seasonality) or a prediction model (features: age mix, chronic prevalence,
member count, geo, with year as one input)? One clear winner, applied
everywhere. No hybrid.

### Why it matters

At yearly grain a single zip has only ~3 data points — no forecast can be fit
per zip. A prediction model pools all zips and learns from thousands of rows.
But at coarser grains (county, state) a real time series may exist and a
forecast might win. Instead of arguing, we test.

### Approach

1. Build the demand history table (zip x period x age band x chronic flag,
   with member counts and visits).
2. Hold out the most recent full year.
3. Fit both approaches on the remaining history:
   - Forecast: time-series model on demand alone (linear trend / ARIMA-class
     if points allow).
   - Prediction: feature model (regression / XGBoost) using age mix, chronic
     prevalence, member count, geography, year.
4. Both predict the held-out year. Compare error (MAE / MAPE) overall and by
   zip size (thin vs dense zips) and by grain (zip, county, state).

### Decision rule

Lower held-out error wins. The winning approach becomes the primary estimate
for demand at all grains. The losing approach is dropped (the p75 baseline and
cluster-average estimates remain regardless, per the three-estimate structure).

### Prerequisite

Membership + claims demand history table must exist before this analysis can
run. Blocked until then.

### Related locked assumptions

- Chronic = member has at least one claim ICD that maps to an HCC.
- Lookback window for the chronic flag interacts with this analysis (24-month
  lookback discussed for zip-level); final lookback to be confirmed alongside
  this test.

### Conclusion

_To be filled after the analysis runs._

---

## DD 04 — Demand History Table Spec

**Folder:** `expanded_scope/dc_v2/07_data_decisions/dd_04_demand_history_table/`
**Status:** Spec stage — decisions listed, none made yet
**Owner:** Deepan

### What this table is

The single table that both DD 01 and DD 03 run against, and that all demand
models train on. It holds, for each geography and period: how many members
there were, split by age band and chronic condition, and how many visits they
made. Everything downstream (forecast test, prediction model, visit rates)
reads from this one table.

### Draft shape

```
zip | year | age_band | chronic_flag | chronic_condition | specialty | members | visits | visit_rate
```

One row per combination. County and state versions are rollups of this, and
must reconcile (zip totals = county totals = state totals).

### What needs to be decided

1. **Time grain: yearly or monthly.** Yearly is simpler (DD 02 preference)
   but gives ~3 points per zip — too few for a forecast. Monthly gives more
   points but more noise and more work. DD 03's test result may force this
   choice.
2. **Chronic condition column: one row per condition, or one flag per
   member.** Per condition means a member with two conditions sits on two
   rows — double counted if summed. Per member (chronic yes/no) is clean for
   counting but loses condition detail. Possible answer: two tables — one
   counting table (flag only), one condition-detail table.
3. **Chronic lookback window.** How far back we scan claims for an
   HCC-mapped ICD (12 / 24 months / all history). 24 months discussed for zip
   level. Interacts with grain choice.
4. **Age bands.** Which buckets (e.g. <65, 65-74, 75-84, 85+). Should match
   whatever the visit-rate pooling and CMS conventions use.
5. **Specialty in or out of this table.** Visits split by specialty
   multiplies row count heavily. Alternative: members table without
   specialty, visits table with it, joined at model time.
6. **Which members count.** LOB filter (assumed CP and ME), and whether a
   member active only part of the year counts as 1 or as a fraction
   (member-years).

### What it depends on

- **Membership data** (Deepan bringing in — yearly preferred, DD 02). Blocks
  member counts, age bands, zips.
- **HCC-to-ICD mapping** (present; assumption locked — any matching ICD =
  chronic). Blocks the chronic columns.
- **Claims history** for visits and for the chronic lookback. Actual years
  available still unconfirmed — the DD 01 profiling step reveals this.
- **hcc_chronic subproject** for which major conditions get their own
  condition label (vs lumped as "other chronic").

### Order of play

Membership data arrives -> profile it -> lock grain and member-counting rules
-> build the table -> run DD 01 curves and DD 03 test against it.

### Conclusion

_To be filled once the decisions above are made._

---

## DD 05 — Chronic Condition Feature List (hcc_chronic subproject)

**Folder:** `expanded_scope/dc_v2/01_hcc_chronic/`
**Status:** Design agreed, analysis not yet run
**Owner:** Deepan

### Question

Which chronic conditions become model features, and how is each one defined
(which ICD codes)?

### Locked assumptions

- Chronic = member has at least one claim ICD that maps to an HCC.
- Mapping source: CMS-HCC V24 ICD-to-HCC map (already in a BQ table).
- Cost measure: allowed amount.
- Sample for the analysis: 1 year of claims data.

### Approach — three modules

**h1 — Mapping coverage.** Join V24 map to claims diagnosis codes. Report:
% of distinct ICDs mapped, % of claim volume mapped, % of allowed dollars
mapped, % of members with at least one mapped code.

What h1 is used for:
1. Sanity gate — if only a small share of members have any HCC-mapped code,
   the "HCC = chronic" assumption is too narrow to carry the demand model,
   and we find out before building on it. A healthy result looks like a
   solid majority of members touched.
2. Denominator — h2 and h3 percentages (unmapped share, "top conditions =
   X% of allowed") are computed against h1's totals.
h1 runs first; nothing downstream starts until it passes.

**h2 — Unmapped review.** Top 100 unmapped ICDs by claim volume and by
allowed amount. Eyeball check: are they acute / symptom / screening codes
(fine to be unmapped), or real chronic conditions leaking through? This
validates or challenges the "HCC = chronic" assumption.

**h3 — Condition ranking and external comparison.** Two lists compared:

1. External anchor: the CMS Chronic Conditions Warehouse (CCW) predefined
   chronic condition set (~27 conditions) and published Medicare prevalence
   rankings (hypertension, hyperlipidemia, diabetes, ischemic heart disease,
   arthritis, CKD, depression, COPD, heart failure at the top).
2. Our data: HCCs ranked by member count, claim count, and allowed amount,
   with cumulative % columns to make the elbow visible.

Three buckets fall out:
- In both lists -> definite features.
- Big in our data, not in CCW -> judged case by case.
- Big in CCW, small/absent in our data -> flagged as a coverage gap.

### Known risk to check

Some highly prevalent conditions are not payment HCCs in V24 (hypertension
is the known example). Pure "HCC = chronic" will miss them. The h3 comparison
surfaces these; decision then is whether to patch them in using CCW ICD
definitions or stay pure V24. This is the main open call of the subproject.

### Decision rule

Final feature list = conditions surviving the h3 comparison, each defined by
an explicit ICD set. No preset top-N; the cut is made from the cumulative
frequency and allowed-amount distributions. Everything below the cut is
labeled "other chronic."

### Output

One workbook, three tabs (coverage / unmapped top-100 / ranking +
comparison), house style. Conclusions written back into this section.

### Feeds into

- DD 04 demand history table: the `chronic_condition` column carries exactly
  this feature list.
- Capacity modeling: patient condition mix uses the same list.

### Conclusion

_To be filled after the analysis runs._

---

## DD 06 — Yearly vs Monthly Membership Input

**Folder:** `expanded_scope/dc_v2/07_data_decisions/dd_06_membership_grain/`
**Status:** Baseline decided, upgrade TBD
**Owner:** Deepan

### Question

The forecast runs monthly but membership numbers may only exist yearly.
What number goes into each month?

### Decision (baseline)

Yearly membership is the baseline: each year's number is used for all 12 of
its months. For future months, the last known year's number is used.

### Open

Deepan to check whether a month-level membership number is available. If
yes, and it is clean, switch the input to monthly. Until confirmed, build
everything on yearly.

### Why it matters

If the monthly forecast sees a flat membership line all year, membership
contributes no within-year signal — seasonality must come from the visits
themselves. Acceptable for MVP.

### Conclusion

_Pending monthly availability check._
