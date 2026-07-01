# Multi-State Network Adequacy Report — Storyline

**Scope:** Florida, Ohio, Arizona, Illinois · Aetna Medicare Advantage (MA-HMO, MA-PPO) · Plan Year 2026
**Regulation:** 42 CFR 422.116 · CMS 2026 HSD Reference File (12-17-2025)
**Workbook:** `medicare_supply_demand_ms.xlsx` (16 tabs)
**Numbers:** every `[S#]` below is filled by the matching query in `report_storyline.sql`.

---

## The one-paragraph story

For **every county × CMS specialty × plan type**, we ask the two questions CMS asks in 42 CFR 422.116:
1. **Access (Test 1):** do enough of the county's Medicare members have an in-network provider *within the CMS distance*?
2. **Count (Test 2):** are there *enough distinct in-network providers* within that distance (beds, for hospitals)?

A cell is **COMPLIANT only if both pass**. The report rolls this single fact table up four ways — by specialty, by county, by state, and by submarket — and adds a supply/participation view (who is contracted, who is actually seeing patients, who is in Original Medicare). Headline result: **[S1] — overall `pct_compliant`, and per state.**

---

## Methodology — how a cell is decided (`ms_fact_gap_analysis`)

| Number (column) | Source table | What it is |
|---|---|---|
| `required_provider_count` | `ms_ref_hsd_required_counts` | CMS-published minimum providers for that county+specialty (the `required_count` field from the HSD file). Beds for Acute Inpatient. |
| `compliance_threshold` | `ms_ref_county` | 0.90 for Large Metro/Metro, 0.85 for Micro/Rural/CEAC. |
| `max_distance_miles` | `ms_ref_time_distance` | The county's own max distance for that specialty, taken from the HSD *Time & Distance* tabs. |
| `total_county_population` | `ms_stg_beneficiaries` | The county's Medicare eligibles (from `cms_medicare_penetration`) spread to zips by ACS-2018 zip population share (`zip_medicare_eligibles`). |
| `population_with_access` | `ms_fact_zip_access` | Same eligibles, but only for zips that have ≥1 in-network provider of that specialty within `max_distance_miles`. |
| `pct_covered` | derived | `population_with_access / total_county_population`. |
| `actual_count` | `ms_stg_providers_multi_specialty` | Distinct in-network providers within `max_distance_miles` of the county's member zips. For **Acute Inpatient**, `SUM(hosp_list_cmi.Beds)` instead. |
| `access_compliant` | derived | `pct_covered >= compliance_threshold` (Test 1). |
| `count_compliant` | derived | `actual_count >= required_provider_count` (Test 2). |
| `compliance_status` | derived | `COMPLIANT` iff both are true. |

**Provider location note (say this plainly):** `actual_count` uses the county resolved from the provider's zip — the `zip_code` in `mdcr_base_provider_mdcr_ntwk` (carried as `additional_zip`), mapped to a county in `ms_ref_zip_reference`. This is a *different* field from the `county_nm` carried on the provider record from `mbr_with_zip` (shown as `aetna_county_nm`). Only the zip-derived county is used in the math.

## Boundaries / assumptions (state these before any number)

- **Straight-line distance** between zip centroids — not drive time. Rural/CEAC counties are understated (look better than reality).
- **Member population is ACS-2018 all-ages zip population** scaled to county Medicare eligibles — not per-member addresses.
- **No telehealth credit** (no telehealth flag in the data) — some borderline NON-COMPLIANT cells might pass with it.
- **Acute Inpatient** uses `hosp_list_cmi.Beds`; a state showing **0 beds** means `hosp_list_cmi` does not cover it — treat those rows as "no data," not "no beds."
- **A county with no providers is included and scored NON-COMPLIANT** (0% access, 0 count).

---

## Tab-by-tab story

### 1. Project Overview
**Answers:** what is in scope and how compliant are we overall.
**Numbers `[S1]`:** counties per state (FL 67 / OH 88 / AZ 15 / IL 102), 43 specialties × 2 plans, and `pct_compliant` overall and per state → `FL __ · OH __ · AZ __ · IL __ · ALL __`.
**Pattern to call out:** which states are strongest/weakest.
**Insight:** sets the frame — the rest of the workbook explains *where* and *why* the non-compliant share sits.
**Ties to purpose:** it's the executive headline the four rollups drill into.

### 2. County Mapping
**Answers:** do the county names line up across sources (so joins are trustworthy).
**How built:** per county (`ms_ref_county`), the county name vs the `aetna_county_nm` (`mbr_with_zip.county_nm`) of providers whose zip lands in that county.
**Numbers `[S2]`:** counties, and counties where the two names differ, per state.
**Pattern:** most differences are spelling/format (`St. Lucie` vs `ST LUCIE`); a county can show several `aetna_county_nm` values because providers from several counties have a zip there.
**Insight:** confirms the join key is sound — compliance joins on `county_fips`, not on the name, so name differences don't move results.
**Ties to purpose:** a data-quality gate; it tells the manager the geography is reconciled.

### 3. County Type Validation
**Answers:** does our county classification match CMS's.
**How built:** a Census-derived `county_type` (population + density) compared to `ms_ref_county.county_type` (the HSD `COUNTY DESIGNATION`).
**Numbers `[S3]`:** MATCH vs MISMATCH per state.
**Pattern:** most MATCH; mismatches are borderline metro/micro counties.
**Insight:** we use the **HSD** designation (authoritative) — this tab shows *how often* the simpler Census rule would have disagreed, i.e., the risk we avoided by not deriving it ourselves.
**Ties to purpose:** justifies the `compliance_threshold` and `max_distance_miles` each county was held to.

### 4. Compliance Report — the core
**Answers:** for every county × specialty × plan, pass or fail, and by how much.
**How built:** every column in the Methodology table above, one row per cell.
**Numbers `[S4a]` (by plan), `[S4b]` (which test fails):** per state, compliant vs non-compliant by plan; and the split of failures into **access-only**, **count-only**, **both**.
**Pattern:** MA-HMO vs MA-PPO usually track closely; note whether failures are driven by *access* (too far) or *count* (too few) — they imply different fixes.
**Insight:** access-only failures → distance/geography problem; count-only → recruit more providers; both → a genuine gap.
**Ties to purpose:** this is the compliance answer; every other tab is a lens on it.

### 5. Summary by Specialty
**Answers:** which specialties are the network's weak spots.
**How built:** for each `state_cd × cms_specialty × plan_type`, count of COMPLIANT vs NON-COMPLIANT counties and `pct_compliant`.
**Numbers `[S5]`:** the 10 lowest-`pct_compliant` specialties per state.
**Pattern:** low-supply specialties (e.g., Endocrinology, Neurosurgery, some facility types) tend to sit at the bottom in the same states.
**Insight:** a short, ranked recruitment list — the specialties failing across many counties.
**Ties to purpose:** turns the grid into "fix these specialties first."

### 6. Summary by County
**Answers:** which counties are worst off across specialties.
**How built:** for each `state_cd × county_name × plan_type`, share of the 43 specialties compliant.
**Numbers `[S6a]` (worst counties), `[S6b]` (empty counties):** the 10 lowest counties per state, and **the count of counties with zero providers anywhere** (all NON-COMPLIANT).
**Pattern:** rural/CEAC counties dominate the bottom; the empty counties are automatic failures.
**Insight:** `[S6b]` is important for the manager — these counties fail purely because there is nothing there, and they are candidates for a CMS exception filing rather than recruitment.
**Ties to purpose:** the geographic priority list.

### 7. Data Dictionary *(static)*
**Answers:** what every column on the Compliance tab means and where it comes from.
**Ties to purpose:** lets the manager audit any number back to its source table — no separate lookup needed.

### 8. CMS Rules *(static, from `ms_ref_time_distance`)*
**Answers:** the 42 CFR 422.116 time/distance standard per specialty × county type.
**How built:** the base (minimum) `max_time_min`/`max_distance_miles` per specialty × county_type.
**Insight:** the yardstick behind Test 1 — same nationwide, so it needs no per-state story; note that individual counties may be *relaxed* above this base (captured per-county in the fact table).
**Ties to purpose:** shows the rule the whole report is measured against.

### 9. Methodology *(static)*
**Answers:** the data sources and the key decisions/assumptions (the Boundaries list above).
**Ties to purpose:** pre-empts "how did you compute this" — every source table and assumption in one place.

### 10. W3 Data Inventory
**Answers:** the supply funnel — contracted → participating → in Original Medicare — per county.
**How built (`ms_week3_data_inventory` / `ms_provider_par_flag`):**
- `ma_contracted_providers` = distinct providers in the Aetna MA network.
- `aetna_participating_providers` = had a paid claim in 2024-2025 (`aetna_par_flag=1`, from `mdcr_base_claim.allowed_amt > 0`).
- `cms_medicare_providers` = participate in Original Medicare (`original_medicare_flag = 'Y'`, from `cms_medicare_physician_ffs_2023`).
**Numbers `[S7]`:** distinct providers per state at each funnel stage.
**Pattern:** contracted ≥ participating; the participating/contracted ratio is the "actually seeing patients" rate.
**Insight:** a low participating rate flags a network that looks adequate on paper but is quiet in practice.
**Caveat to say out loud:** these are **county-level** counts — **do not sum across counties** (a provider in two counties counts in each). `[S7]` uses `COUNT(DISTINCT provider_id)` at state level, which is the correct de-duplicated number.
**Ties to purpose:** separates "contracted" from "real capacity."

### 11. W3 Par Flags
**Answers:** the participation breakdown behind the funnel.
**How built (`ms_provider_par_flag.participation_status`):** each provider is classed as `ACTIVE BOTH`, `AETNA ACTIVE - …`, or `CONTRACTED NOT ACTIVE - …` from the combination of a paid Aetna claim and the Original-Medicare flag.
**Numbers `[S8]`:** distinct providers by `participation_status`, per state.
**Pattern:** the size of `ACTIVE BOTH` vs `CONTRACTED NOT ACTIVE` tells you how much of the network is truly engaged.
**Insight:** `CONTRACTED NOT ACTIVE - IN ORIGINAL MEDICARE` providers are the best re-engagement targets (they see Medicare patients, just not Aetna's).
**Ties to purpose:** the "quality of network" view.

### 12–16. Submarket (Compliance · Summary · Inventory · Par · Opportunity)
**Answers:** the same views rolled up to Aetna submarket (an internal business grouping — **not** a CMS unit).
**How built:** each county is assigned its **one dominant submarket** — the submarket used by the most providers whose zip lands in that county (`ms_stg_providers.submarket`), scoped to the county's own state. The fact/inventory/par tables roll up to `state × submarket`.
**Numbers `[S9]`:** per state × submarket, counties and `pct_compliant`; **`[S10]`:** the coverage gap.
**Pattern:** submarkets containing rural counties trail; **Opportunity** ranks submarkets by `network_gap = cms_available − aetna_contracted` (recruitment headroom).
**Insight — and an important caveat for the manager:** the submarket is read *from providers*, so a **county with no providers has no submarket and is dropped from these five tabs** (`[S10]` = how many counties, per state). That means the submarket tabs **look more compliant than the state totals**, because the empty (always-failing) counties are missing. Fix = a county→submarket reference covering all counties.
**Ties to purpose:** operational rollup for the teams that own each submarket — with the stated gap.

---

## Anticipated questions & answers

**Q. Why is a county with no providers still in the report — and is it compliant?**
A. It's included and scored **NON-COMPLIANT** (0% access, 0 providers → both tests fail). Right answer: no providers = doesn't meet the minimum. It shows on the county and state tabs; it's the one thing the submarket tabs currently miss (`[S10]`).

**Q. A provider works in several counties — are they double-counted?**
A. No in the compliance count: Test 2 is `COUNT(DISTINCT provider_id)` grouped by the **member's** county, so a provider counts once per county they can reach. They *do* appear in multiple counties in the W3 inventory (by design) — that's why we say don't sum inventory across counties.

**Q. Why do the submarket tabs look better than the state totals?**
A. `[S10]` — empty counties have no provider, so no submarket, so they drop out of the submarket rollup. They stay in the state/county totals. The delta = those dropped counties.

**Q. Access says fail but count says pass (or vice versa) — what does that mean?**
A. `[S4b]`. Access-only fail = providers exist but are **too far** (geography/drive-time). Count-only fail = providers are close but **too few**. Both = a real gap. Different fixes.

**Q. Distance — straight line or drive time?**
A. Straight-line between zip centroids. It *understates* travel in rural/CEAC counties, so those pass more easily than drive-time would allow — a conservative-for-us assumption to flag.

**Q. Is telehealth counted?**
A. No — no telehealth flag in the data. Some borderline NON-COMPLIANT cells could pass with the 10% telehealth credit.

**Q. The Acute Inpatient row shows 0 for a state — no hospitals?**
A. It means `hosp_list_cmi` (the bed source) doesn't cover that state, not that there are no beds. Treat as "no data" until a bed source for that state is loaded.

**Q. `aetna_county_nm` differs from the county on the Compliance tab — which is right?**
A. They are two different fields (`mbr_with_zip.county_nm` vs the county from the provider's `mdcr_base_provider_mdcr_ntwk` zip). Compliance uses the **zip-derived** county throughout; `aetna_county_nm` is shown for reference only and never enters the math.
