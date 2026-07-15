# dc_v2 — Model Target and Input Data Spec

**Location:** `expanded_scope/dc_v2/00_docs/model_decisions.md` (MD 01)
**Status:** Draft for review. No code until approved.

---

## 1. Definitions first — what the numbers mean

One raw fact feeds everything: a claim line = one member saw one provider on
one date, with diagnosis codes and an allowed amount. Demand and capacity are
two readings of the same visits:

- **Demand** = visits attributed to the MEMBER side (where members live, how
  old they are, what conditions they have). Theory: demand = members x visits
  per member, where the visit rate depends on age and chronic condition.
- **Capacity** = visits attributed to the PROVIDER side (who delivered them).
  Theory: capacity = what a provider CAN deliver, which is at least what they
  DID deliver.

### CRITICAL — county attribution rule (build-time trap)

Because both sides count the SAME Aetna claims, at total level demand equals
capacity by construction. All geographic signal lives in attribution:

- Demand tables: county/zip = the MEMBER's residence county, from membership.
  Never the provider's.
- Capacity tables: county/zip = the PROVIDER's practice county, from the
  provider reference. Never the member's.

Example: Leon County member sees a Duval County provider. That one visit is
Leon demand AND Duval capacity. The county-level difference between demand
and capacity is exactly these cross-county flows (patient import/export) —
that mismatch is the signal we are building for.

If either data-creation step picks the wrong county source, demand and
capacity become identical at county level, the gap collapses to zero
everywhere, and the model is worthless. Every table-build script must state
which county column it uses and why, and this is a mandatory review check.

### Scope of capacity — Aetna-realized only (signed off)

Capacity = what the provider operates WITH AETNA. Non-Aetna volume is
invisible and out of scope by design. Consequence to state in all outputs:
our capacity is Aetna-realized throughput, not the provider's clinical
maximum. If membership grows in a county, local providers may absorb more
than history shows (unseen non-Aetna slack). Goes in caveats on every
capacity output, stated on the tab, not buried.

### Demand target — honest limitation

Claims only show visits that happened. Unmet demand (member wanted care,
could not get it) is invisible. So the modeled quantity is strictly
"realized demand." We state this openly. Partial mitigation: the model
learns visit rates per age x chronic bucket from well-served areas and
applies them everywhere — an underserved county then shows predicted demand
above its observed visits. That difference is our estimate of unmet demand.

**Demand target = visits, by member geography x specialty_ctg_cd x month.**

### Capacity target — the definition decision

Delivered visits understate capacity (a provider could take more). Three
candidate definitions, from weakest to strongest:

1. Delivered visits as-is (floor, not capacity).
2. Provider's own sustained peak: e.g. the top decile of their own monthly
   visit counts over the history. "What they proved they can do."
3. Model-estimated potential: predict expected delivery from provider
   features; providers delivering far below similar peers are flagged as
   under-used capacity.

Recommendation: target = delivered visits for TRAINING (it is the only
observed truth), and capacity is REPORTED as the model's prediction plus the
sustained-peak measure side by side. The p75 heuristic stays as baseline
comparison only. This choice is MD 02, needs sign-off.

**Capacity target = visits delivered, by provider x specialty_ctg_cd x month.**

---

## 2. Grain map (locked earlier)

| Model | Grain | Target |
|---|---|---|
| Demand forecast | county x specialty_ctg_cd x month (state = rollup check) | visits (member side) |
| Demand predictive | provider x specialty_ctg_cd x month | visits demanded from provider |
| Capacity predictive | provider x specialty_ctg_cd x month | visits delivered |
| Capacity forecast | county x specialty_ctg_cd x month | visits delivered, rolled to provider county |
| Weave | county x cms_specialty x plan_type | all measures bridged once |

---

## 3. Input data inventory — everything discussed to date

### Sources we have or expect

| # | Source | Grain | Feeds | Status |
|---|---|---|---|---|
| 1 | Claims (visits, dx ICDs, allowed amt, NPI, member id, dates) | claim line | both targets, chronic flags, new/returning | have; history depth unconfirmed |
| 2 | Membership (age/DOB, zip, LOB) | member x year (monthly exists, yearly preferred) | demand denominator, age mix | Deepan bringing |
| 3 | V24 HCC-to-ICD map | ICD | chronic flags | in BQ |
| 4 | Chronic condition feature list | condition | model features | pending DD 05 run |
| 5 | New/returning definition | member x provider | feature both sides | pending DD 01 run |
| 6 | ref_specialty_crosswalk (43-row, ctg_cd to cms) | specialty | weave bridge only | have |
| 7 | cms_medicare_penetration | county x year | eligibles context, MA share | have; annual |
| 8 | Census ACS (income, density, population) | county / zip | slow features | have; static |
| 9 | Provider reference (RPDB etc: specialty, location, par) | provider | capacity features | have |
| 10 | mdcr_base_claim par flags | provider | par feature | have |
| 11 | ref_zip_reference / county classification | zip / county | geo mapping, county type | have |
| 12 | CMS-HCC risk model rules | member | risk scores (rules, no model) | have framework |

### Demand FORECAST input table (county x specialty_ctg_cd x month)

| column | source | note |
|---|---|---|
| visits (TARGET) | claims | member county attribution |
| members | membership | exog; yearly value repeated across months if monthly absent |
| age mix (share 65-74 / 75-84 / 85+ / <65) | membership | exog |
| chronic prevalence per DD 05 condition | claims x V24 | exog |
| pct_new_patients | claims (DD 01 rule) | exog |
| provider_count in county | provider ref | exog |
| ma_penetration | penetration file | exog, annual |
| month_of_year | derived | seasonality |
| county_type | county classification | series metadata |

Rule for yearly inputs: a year's number is used for all 12 of its months.
For future months, the last known year's number is used. If monthly
membership turns out to be available, we switch (DD 06). Any input we cannot
supply a value for in future months cannot be used by the forecast.

### Demand / capacity PREDICTIVE input table (provider x specialty_ctg_cd x month)

| column | source | note |
|---|---|---|
| visits (TARGET) | claims | delivered by provider |
| specialty_ctg_cd | provider ref / claims | id |
| panel size (distinct members, trailing 12m) | claims | feature |
| panel age mix | claims x membership | feature |
| panel chronic mix per DD 05 condition | claims x V24 | feature |
| pct_new_patients (DD 01 rule) | claims | feature |
| par status | mdcr_base_claim | feature |
| tenure (months since first claim in data) | claims | feature; left-truncated, flag it |
| patient geo spread (distinct zips, share outside home county) | claims | feature |
| county_type of provider | county classification | feature |
| local density (providers per 1k eligibles in county) | provider ref + penetration | feature |
| month_of_year, year | derived | time features |

Same table serves demand-at-provider and capacity — the difference is
interpretation and downstream attribution, not columns.

---

## 4. Theory checks before any column is used

Rule: every input must have a stated reason it plausibly drives visits.
Applied to the lists above:

- Age, chronic mix -> sicker and older people visit more. Sound.
- New patient share -> new patients consume longer slots; high share means
  growing panel. Sound.
- Provider count / density -> supply constrains realized demand. Sound, but
  this makes demand partially supply-driven — must be stated when
  interpreting (this is exactly the unmet-demand blind spot).
- Income / density -> access and behavior differences. Weak-moderate; keep
  only if the model shows lift.
- Allowed amount -> NOT a demand input (it measures price, not volume);
  used only in DD 05 for ranking conditions by cost.

Any future column must pass the same one-line justification or it stays out.

---

## 5. Sign-offs

1. SIGNED: Capacity = Aetna-realized throughput, modeled from provider
   features; sustained-peak reported alongside; p75 as baseline only (MD 02).
2. SIGNED: County attribution rule — member county for demand, provider
   county for capacity. Mandatory check at every table-build step.
3. DECIDED — NO (deferred): Unmet demand estimation is out of scope for MVP.
   Demand = observed visits only. The "visits people needed but didn't get"
   question is parked for a later phase. Do not build for it now.
4. DECIDED — yearly baseline, monthly TBD: Yearly membership numbers are the
   baseline input. If a month-level membership number turns out to be
   available, we may switch. Until then, each year's number is used for all
   its months. Logged as DD 06.
