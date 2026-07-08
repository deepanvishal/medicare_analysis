# Demand vs Capacity Report — Storyline

**Scope:** Florida, Ohio, Arizona, Illinois · Aetna Medicare Advantage · claims year 2025
**Workbook:** `medicare_demand_capacity_ms.xlsx` (11 tabs, built by `38_dc_report.py`)
**Numbers:** every `[S#]` below is read off the matching workbook tab (the queries live in
`38_dc_report.py`; the module CHECKS print the same numbers at build time).

---

## The one-paragraph story

The ask was: estimate the utilization the Medicare population will drive (demographics and
morbidity), estimate the effective capacity of the contracted MA network, and surface where
beneficiaries are at risk. Compliance (42 CFR 422.116) answers a different question — enough
providers, close enough — so we added **three numbers for every county x specialty x plan**:
**Demand** (population x rate, per age band x morbidity level), **Capacity** (per-provider
typical volume x active flag x room left in the panel), and **Gap** (MA demand minus capacity,
like-for-like). The headline is the quadrant compliance cannot see: cells that are **COMPLIANT
but strained** — demand above capacity — **[S1] per state**. Those are counties where the
network passes the CMS floor and can still not absorb its own members.

---

## Tab-by-tab story

### 1. Overview *(static)*
**Answers:** what was asked, what the three numbers are, and which rules are locked.
**How built:** static text; no query.
**Numbers [S1]:** the compliant-but-strained count per state (read from Tab 7) belongs in the
verbal walkthrough of this tab: `FL __ · OH __ · AZ __ · IL __`.
**Pattern to call out:** compliance is a floor, not a capacity statement.
**Insight:** the deck's frame — everything after this tab exists to fill in three numbers.
**Caveat to say out loud:** every rate is MA_PROXY (built from Aetna ME claims, applied to the
whole population) and county morbidity is the state mix until the CMS county risk score loads.

### 2. Demand Method
**Answers:** where the demand number comes from and why it must be cell-level.
**How built:** `ms_dc_rate` — visits per member per year at state x specialty x age band x
morbidity (LOW 0 / MEDIUM 1-2 / HIGH 3+ HCC_v24 from the primary diagnosis); three small
tables + charts: the age gradient, the morbidity gradient, the specialty spread.
**Numbers [S2]:** the FL top-specialty MEDIUM rate by age band (chart 1); LOW vs HIGH rate for
the top-5 FL specialties at 70-74 (chart 2).
**Pattern to call out:** rates rise monotonically with age; HIGH-morbidity members use a
multiple of LOW members' care; specialties differ by an order of magnitude.
**Insight:** a single blended rate would misprice demand everywhere — the cell structure is not
optional.
**Caveat to say out loud:** morbidity uses the primary diagnosis only (HCC undercount) and thin
cells (< 30 members) borrow the 4-state pooled rate.

### 3. Demand by County
**Answers:** how many visits each county x specialty will pull, market vs Aetna-member.
**How built:** `ms_dc_demand` — eligibles-in-band x blended rate (market) and member cells x
resolved rate (MA); state rollup chart on top of the full table.
**Numbers [S3]:** market vs MA demand totals per state (top chart).
**Pattern to call out:** market demand is a multiple of MA demand in every state — the gap
between the two lines is the growth headroom.
**Insight:** the market number sizes the opportunity; the MA number is what the network must
absorb today.
**Caveat to say out loud:** market demand applies the state morbidity mix to every county;
`rate_basis` is MA_PROXY on every row.

### 4. Capacity Method
**Answers:** how contracted providers become deliverable visits.
**How built:** `ms_dc_provider_capacity` — provider_slots = typical_annual_capacity (p75 of
observed 2025 ME visits, pooled when a state x specialty has under 20 providers) x active_flag
(paid Aetna claim 2024-2025) x (1 - senior_saturation) (percentile of the provider's CMS FFS
Medicare panel); worked example 2000 -> 400 slots.
**Numbers [S4]:** the contracted -> active -> slot-equivalents funnel per state; the imputed
saturation share per state.
**Pattern to call out:** the funnel narrows hard — contracted counts flatter the network;
effective slots are a fraction.
**Insight:** capacity is a provider-level property; county capacity is just its sum, which is
why one busy provider cannot be counted twice across counties at full weight.
**Caveat to say out loud:** 26-33 percent of provider rows carry an imputed median saturation
(no CMS FFS match); capacity is on the Aetna-observed ruler, comparable to MA demand only.

### 5. Capacity by County
**Answers:** the deliverable visit volume per county x specialty x plan.
**How built:** `ms_dc_capacity` — the provider table summed; full filterable grid.
**Numbers [S5]:** top capacity cells (sort the table by capacity_visits).
**Pattern to call out:** capacity concentrates in metro counties; many rural cells hold a
handful of active providers.
**Insight:** the supply-side twin of Tab 3 — same grain, same unit, so the subtraction in Tab 6
is legitimate.
**Caveat to say out loud:** facility and ancillary specialties carry visit-slot numbers that do
not describe them (see Tab 7's facility note).

### 6. Gap Report
**Answers:** the three numbers side by side on the compliance grid, with status.
**How built:** `ms_dc_gap` — the compliance fact LEFT JOINed to demand (bridged via
`ref_specialty_crosswalk`) and capacity; gap = ma_demand - capacity; DESERT / BALANCED /
OVERSUPPLY color-coded; `risk_flag` = COMPLIANT and gap > 0.
**Numbers [S6]:** gap_status mix per state; count of NO_DEMAND_MAPPING cells (crosswalk misses).
**Pattern to call out:** compliance status and gap status disagree constantly — that
disagreement is the whole finding.
**Insight:** a cell can pass CMS and still be a desert; a NON-COMPLIANT cell can sit in
OVERSUPPLY (compliance failed on distance, not volume).
**Caveat to say out loud:** DESERT (gap > 20 percent of MA demand) and OVERSUPPLY (capacity >
150 percent) are first-pass thresholds — rank by gap size, do not treat the flag as a verdict.

### 7. Deserts & Risk
**Answers:** the action list — where demand exceeds capacity, and the compliant-but-strained
headline.
**How built:** `ms_dc_gap` filtered and pivoted: the 2x2 quadrant (compliant x capacity-ok) per
state, the strained count, top practitioner deserts, facility rows separated out.
**Numbers [S7]:** the quadrant counts per state; top-15 practitioner deserts by gap.
**Pattern to call out:** the strained quadrant is not empty in any state; deserts cluster in
the same counties the compliance report already flags, plus a set it does not.
**Insight:** this is the recruitment list ranked by visit shortfall, not by provider count
shortfall — a different (better) priority order than compliance gaps alone.
**Caveat to say out loud:** facility/ancillary rows are listed for completeness, not ranked —
slot capacity from practitioner visit volume does not describe a hospital or an ASC.

### 8. Forecast Example
**Answers:** what the gap table looks like projected forward one year.
**How built:** `ms_dc_forecast_example` — for each state's compliant cell closest to tipping
(smallest capacity surplus): 12 months of observed-2025 seasonality x 3 percent annual growth
vs flat capacity; line chart per state with the crossover months counted.
**Numbers [S8]:** the four example cells and their crossover month counts.
**Pattern to call out:** seasonality alone pushes some months over capacity before the growth
trend does.
**Insight:** annual averages hide months of strain — a cell can be BALANCED on the year and
over capacity every winter.
**Caveat to say out loud:** a one-time illustrative projection — 3 percent growth is a stated
placeholder, capacity held flat, and one year of claims allows no holdout validation. Not a
model.

### 9. Book Utilization
**Answers:** what the book actually delivered in 2025, Medicare vs Commercial, by provider
county.
**How built:** `ms_dc_book_utilization` — distinct member x provider x day visits at provider
county x specialty x lob (CP / ME / TOTAL) x age band.
**Numbers [S9]:** ME share of TOTAL visits per state.
**Pattern to call out:** the 60+ book skews ME almost everywhere; delivery concentrates in
metro provider counties regardless of member home.
**Insight:** context tab — it grounds the modeled numbers in what the network demonstrably
delivered.
**Caveat to say out loud:** visits are keyed to the PROVIDER's county (where care was
delivered); member home county is not attributable in the claims.

### 10. Data Dictionary *(static)*
**Answers:** what every reported column means and which dc_ table it comes from.
**How built:** static rows consistent with the module docstrings.
**Insight:** lets a reviewer audit any number back to its table without opening the code.
**Caveat to say out loud:** none — this tab exists so the caveats elsewhere are checkable.

### 11. Methodology *(static)*
**Answers:** the locked rules and the exact model sentences, plus what is parked for v2.
**How built:** static kv rows quoting the module docstrings verbatim (capacity knobs, gap
definition, forecast disclaimer) and the caveats (MA_PROXY, county morbidity pending).
**Numbers:** none.
**Pattern to call out:** the v2 parking lot is short and concrete: CMS county risk score,
total-Medicare rates via FFS apportionment, threshold calibration, TIN-vs-individual grain.
**Insight:** everything contestable is written down — the model can be argued with, which is
the point.
**Caveat to say out loud:** the parked items are ordered; the county risk score is first
because it moves county-level demand directly.

---

## Anticipated questions

**Q1. Why does the gap use MA demand and not market demand?**
Because the two sides must be on the same ruler. Capacity is built from *observed Aetna ME
visits* (p75 typical volume) — it measures what providers deliver to Aetna members, not their
total practice throughput. MA demand is built from the same member population and the same
visit definition, so `ma_demand - capacity` is like-for-like. Subtracting capacity from
*market* demand would compare a whole-population number against an Aetna-only ruler and
overstate every desert. Market demand still appears — as `market_opportunity_ratio` — for
sizing, not subtraction.

**Q2. Why is every rate labeled MA_PROXY?**
The utilization rates are estimated from Aetna ME claims (the only member-level claims we
hold) and then applied to the entire county eligible population. That assumes non-Aetna
Medicare beneficiaries use care like Aetna MA members do. The honest label for that assumption
is MA_PROXY, carried on every row; replacing it with a true total-Medicare rate (CMS FFS
apportionment) is the first v2 upgrade.

**Q3. Why do facility specialties show near-zero capacity?**
The capacity model is a practitioner model: p75 of per-provider visit volume x panel headroom.
Hospitals, ASCs, dialysis and therapy facilities do not have "typical annual visits per
provider" in any meaningful sense — their rows compute, but the number does not describe
them. They are split out on Tab 7 and listed for completeness, not ranked for recruitment.
Facility adequacy stays with the compliance tests (counts and beds).

**Q4. How were the DESERT and OVERSUPPLY thresholds chosen?**
They were not calibrated; they are stated first-pass cuts. DESERT = gap larger than 20 percent
of MA demand (a shortfall too big to be rate noise); OVERSUPPLY = capacity above 150 percent of
MA demand (clear headroom). The workbook says this on the Gap tab and the methodology parks
threshold calibration for v2. The defensible use today is ranking by gap size, with the flags
as reading aids.

**Q5. Is the forecast a model?**
No. It is a one-time illustrative projection on four hand-picked cells (the compliant cell
closest to tipping per state): observed 2025 seasonality, a flat 3 percent annual growth
placeholder, capacity held constant. One year of claims means there is no holdout to validate
against, so it is presented as "what the shape of strain looks like," not as a prediction. A
validated forecast needs a second claims year and the penetration YoY trend.
