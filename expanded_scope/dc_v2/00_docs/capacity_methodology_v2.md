# Provider Capacity & County Risk — Methodology (v2, frozen build spec)

Pipeline code: modules 60–72 in `expanded_scope/dc_v2/08_capacity_risk/`. Supersedes v1.1. Companion: `capacity_data_model_v2.md`.

**Plain-words summary:** count each provider's work and turn it into an hours ceiling; profile who they actually treat and how readily they take new patients of each type; split projected demand growth into the same patient types; pour the growth into providers proportionally (two passes); patients that can't be placed = the county's risk, by patient type.

---

## 1. Scope

- Individual practitioners only: **NPI Type 1**. Facilities (Type 2) excluded — a building has no workday; facility capacity stays in the compliance pipeline (beds per 1,000 rule).
- Horizon: **1 year**. Share-stickiness assumption is not defensible beyond that.
- States: dc_v2 scope (FL, OH, AZ, IL).
- Aetna share applied **once, at the end** — never inside layers.

## 2. Definitions

| Term | Definition |
|---|---|
| Segment | Patient type: new/returning × chronic/non-chronic × age band (60–74 / 75+). 8 cells, defined once in `ref_segment`. |
| Fractional active day | min(deflated hours ÷ 8, 1) per calendar day. 8 hrs = federal FTE day (HRSA, 40 hrs/week). |
| Ceiling | Provider's feasible annual clinical hours (Layer 1 output), low/high range. |
| Intake rate | New patients of a segment accepted per active month. |
| Blended rate | Provider intake rate credibility-weighted with cohort rate (§5). |
| Cell cap | Max new patients of one segment a provider absorbs in the horizon. |
| Open door | Provider with nonzero blended intake for ≥1 segment. |
| Unplaced demand | Growth patients no open-door provider with room could absorb = risk. |
| zero_utilization_flag | Contracted NPI, zero Aetna MA claims in window. |
| impossible_day | Provider-day >24 implied clinical hours (OIG construct). |

## 3. Layer 1 — Hours ceiling (carried from v1.1, condensed)

**Stage 0 (module 60)** Load MPFS Physician Time File → `ref_mpfs_time`. Intra-service minutes only. The internal claims source has no modifier column (limitation 11); minutes apply as loaded. The time file's own modifier rows are excluded at load (module 60 keeps blank-modifier rows only). Match key is **procedure code (CPT/HCPCS)**, never ICD. Materiality gate: unmapped volume per county × specialty must be too small to flip any gap direction. Fallback ladder for unmapped codes: code-family average → provider's own avg minutes → cohort avg. If a specialty's coverage is structurally poor, that specialty downgrades to a **visit-count ceiling** (same logic, visits instead of hours) — logged per specialty.

**Stage 1 (module 61)** Observed throughput per NPI from CMS FFS public file (annual, no dates) + internal Aetna MA claims (dated). **prvdr_county only.** NPI Type filter applied here.

**Stage 2 (module 62)** Hours = services × intra minutes × deflation (E/M 0.90 / procedures 0.75 / other 0.85 as anchors). **Stage 2b calibration (module 63)** solves final deflation, daily cap (bracket 6–12 hrs), benchmark percentile (85/90/95 rank-stability test), min cohort size (resampling), credibility constant k (§5) — all written to `cap_params`. Nothing downstream hard-codes a tuning number.

**Stage 3 (module 64)** Daily cap on dated (internal) claims; fractional days; `impossible_day` >24 hrs (<1% gate, OIG rarity); high-day flag = team billing signal. CMS side has no dates → FTE-days **estimated** (annual hours ÷ cohort median daily hours), always tagged `fte_days_src_cd = 'ESTIMATED'`.

**Stage 4 (module 65)** Cohort = specialty × county band (CMS SSA 5-way: Large Metro / Metro / Micro / Rural / CEAC); small cohorts fall back to state × specialty, logged. Ceiling_low = cohort high-percentile hours-per-day × own FTE-days; Ceiling_high = × cohort median FTE-days. Multi-county: allocated by service share, sums to 1.0 per NPI.

## 4. Layer 2 — Patient-mix matrix

**Stage 5 (module 66 cohort side, module 67 provider side)** For each provider × segment (from internal claims + CMS where derivable):
- Panel profile: share of current patients in each segment.
- Intake rate: new patients (12-month lookback definition, DD-series) accepted per active month, per segment.

Matrix kept deliberately small (8 cells) so cells stay fat. Axes added later only if data supports them.

## 5. Blending (credibility weighting / empirical Bayes shrinkage)

```
w            = n / (n + k)          n = provider's patient count in the cell
blended_rate = w × own_rate + (1 − w) × cohort_rate(segment, specialty, county band)
```
- High-volume cell → mostly own signal; thin cell → mostly cohort. Standard actuarial technique.
- k derived in calibration: value minimizing county-level reconciliation error (predicted vs actual new-patient totals).
- Every cell tagged `signal_src_cd`: OWN (w ≥ 0.5) / BORROWED (w < 0.5) — surfaces in all reports so provider-level claims are honest.
- Closed door: zero intake across **all** segments over the window → all cells forced to 0 for growth allocation.

## 6. Two-level caps

```
cell_cap(provider, segment) = blended_rate × 12 × horizon_factor(=1)
total constraint            = spare hours = ceiling_low − observed hours
                              (converted to patient count via segment avg first-year hours per patient)
```
Cells never sum past the total constraint: if Σ cell allocations would exceed spare hours, all cells scale down proportionally. One time budget, shared.

## 7. Layer 3 — Demand split & two-pass fill

**Stage 7 (module 68) — demand split.** Existing dc_v2 county × specialty forecast total is the anchor. Split by observed segment shares; growth applied at segment level (population projection by age/chronic mix, or dashboard scenario slider). Segment numbers must re-sum to the anchored total — the split invents no demand.

**Stage 8 (module 69) — fill.**
- Segment market share per provider = provider's current patients in segment ÷ county segment total; closed doors get 0; shares re-normalized over open doors.
- **Pass 1:** grown segment demand × share → provisional load per provider cell.
- **Cap:** load above cell cap or total constraint is returned.
- **Pass 2:** returned load re-split across providers with remaining room, proportional to remaining room.
- **Unplaced remainder = risk.** Deterministic; no ordering effects.

**Stage 9 (module 70) — Aetna layer.** aetna_share = SAFE_DIVIDE(aetna_ma_svcs, aetna_ma_svcs + cms_ffs_svcs), bounded [0,1], applied once to provider-level results; zero_utilization_flag forces willing capacity to 0.

**Stage 10 (module 71) — county risk.** county × specialty × segment: unplaced patients, % of grown demand unplaced, count of providers >100% utilized, risk rank. This is the deliverable grain (Danielle drill-down).

## 8. Validation

| # | Check | Pass condition |
|---|---|---|
| V1 | Unmapped-volume materiality | Cannot flip any county gap direction |
| V2 | Impossible-day rate | <1% pre-cap; 0 post-cap |
| V3 | Utilization distribution (observed ÷ ceiling_low) | Mass below 1.0, thin right tail |
| V4 | County reconciliation | Σ blended intake ≈ actual county new-patient totals (k tuned to this) |
| V5 | Segment split re-sum | Segment demand re-sums to anchored forecast exactly |
| V6 | Fill conservation | placed + unplaced = grown demand, per county × segment |
| V7 | HPSA cross-check | Risk counties overlap directionally with HRSA shortage counties |
| V8 | Sensitivity | Percentile 85/90/95 × cap bracket ends: county risk *rankings* stable, else report ranges |
| V9 | Reconciliation vs dc_v2 v1 capacity | Deltas >25% listed and explained |
| V10 | Share stability | Aetna share and segment shares stable across quarters; swings flagged |

## 9. Decision log

| ID | Decision | Rationale |
|---|---|---|
| CD-01 | Claims-anchored, not federal ratios | HSD/HPSA are demand-coverage tools; derivations not public |
| CD-02 | Intra-service minutes, deflated by code class | Documented survey-time inflation (RTI 31-min; urology 42.9%; CMS CY2026 −2.5%) |
| CD-03 | Daily cap derived in 6–12 bracket; >24 = impossible | Workweek surveys + time-motion evidence; OIG defines impossible |
| CD-04 | Ceiling = benchmark rate × FTE-days, low/high range | Schedule preference unobservable |
| CD-05 | Multi-county allocation by service share | No double counting |
| CD-06 | Aetna share = MA ÷ (MA + FFS), applied once at end | Only bounded computable proxy; per-cell application compounds error |
| CD-07 | `zero_utilization_flag` terminology | Neutral; zero claims may be new contracts |
| CD-08 | Capacity never feeds 42 CFR 422.116 tests | Separate constructs |
| CD-09 | CMS 2023 + internal 2025 vintages | Ratio-stability; retest on CMS 2024 file |
| CD-10 | All tuning numbers in `cap_params` from calibration | Single source of truth |
| CD-11 | NPI Type 1 only | Facilities have no workday; handled in compliance pipeline |
| CD-12 | 8-cell segment matrix v1 | Fat cells over false precision; axes added only with data support |
| CD-13 | Credibility blending with derived k | Thin cells borrow from cohort; actuarially standard; k tuned to reconciliation |
| CD-14 | OWN/BORROWED tag on every cell | Honest labeling of provider-level vs peer-based signal |
| CD-15 | Two-level caps (cell + total) | Cells share one time budget |
| CD-16 | Proportional two-pass fill, no solver | Deterministic, order-free, explainable; transportation solver parked |
| CD-17 | Demand split anchored to existing forecast | Split invents no demand; forecast stays validated |
| CD-18 | Sticky shares, 1-year horizon only | Testable against actual flows; not defensible longer |
| CD-19 | Visit-count ceiling fallback per specialty | Graceful degradation when time coverage poor |
| CD-20 | Segment chronic_flag = HCC_v24 mapping, 24m lookback, matching 46/48 exactly | One chronic definition across all layers; the data model's earlier AHRQ CCIR reference was an error and is corrected. |

## 10. Limitations (report verbatim)

1. Medicare-visible only: other insurers' MA volume (e.g., Humana) invisible → provider workload undercounted AND Aetna share overstated; net effect uncertain. Fix = MD-PPAS (parked, $600/yr).
2. Acceptances observed, rejections never: in shortage counties, suppressed demand makes closed doors look like absent demand → risk understated where it is highest. Mitigated by V4/V7; not solvable with owned data.
3. Sticky-share assumption: new patients distribute like existing ones (closed doors excluded); 1-year scope only.
4. Hospital-employed physicians billing under organization NPIs fall outside Type 1 scope → capacity undercounted in hospital-heavy counties.
5. MPFS times inflated; deflation calibrated on our data, not clinic-measured.
6. CMS PUF suppression (<11) undercounts low-volume providers.
7. Benchmark providers may themselves be under capacity → ceiling is a floor on truth.
8. Segment behavior fixed: mix shifts with population, per-segment visit behavior itself not modeled in v1.
9. Capacity is a range (low/high); ceiling_low used for risk (conservative — overstates risk rather than hiding it).
10. Vintage mismatch (CMS 2023 vs internal 2025).
11. The internal claims source has no modifier column. Lines where the doctor only interpreted a test vs ran the machine cannot be separated; some imaging/test lines carry full minutes they may not deserve. Absorbed by calibration, stated here.

## 11. Parked items

| Item | Trigger |
|---|---|
| MD-PPAS purchase | Leadership funding |
| Transportation-solver fill (distance-aware) | If proportional fill challenged |
| XGBoost intake model (odds from features) | If blending too coarse for Danielle's review |
| Finer segment axes | Data-support check after v1 |
| Multi-year horizon | Requires share-drift modeling |

## 12. Build checklist

**Module 60 `60_load_time_file.py`** — [ ] `ref_mpfs_time` loaded; [ ] code-class map in config; [ ] match-rate report by specialty (first truth check); [ ] `ref_segment` seeded (8 rows)

**Module 61 `61_observed.py`** — [ ] NPI Type 1 filter; [ ] prvdr_county only (mandatory review); [ ] SAFE_CAST suppression values; [ ] M1 NPI match rate reported

**Module 62 `62_hours.py`** — [ ] confirm no modifier handling (limitation 11); [ ] deflation from `cap_params`; [ ] fallback ladder for unmapped codes

**Module 63 `63_calibrate.py`** — [ ] deflation solved; [ ] daily cap elbow in 6–12; [ ] percentile rank-stability; [ ] min cohort n by resampling; [ ] credibility k by reconciliation error; [ ] all → `cap_params` with notes

**Module 64 `64_ceiling.py`** — [ ] caps + flags; [ ] impossible <1% gate (STOP if breached); [ ] fractional days; [ ] CMS FTE-days estimated + tagged

**Module 65 `65_provider_year.py`** — [ ] consolidated provider-year; [ ] fte_days_src_cd survives

**Module 66 `66_cohort_bench.py`** — [ ] benchmark rates + cohort intake rates per segment; [ ] fallbacks logged

**Module 67 `67_provider_segment.py`** — [ ] matrix built; [ ] blending applied; [ ] OWN/BORROWED tags; [ ] closed-door logic; [ ] two-level caps

**Module 68 `68_dem_segment_split.py`** — [ ] segment shares; [ ] growth applied; [ ] re-sum check to anchored forecast (V5)

**Module 69 `69_fill.py`** — [ ] pass 1 / cap / pass 2; [ ] conservation check (V6)

**Module 70 `70_willing.py`** — [ ] share bounded; [ ] zero_utilization forces 0; [ ] applied once

**Module 71 `71_county_risk.py`** — [ ] county × specialty × segment risk table + rank

**Module 72 `72_validate.py`** — [ ] V1–V10 into `cap_validation`; [ ] limitations §10 verbatim into report notes

**Every phase:** [ ] Claude Code prompt starts "You cannot run anything"; [ ] exactly one output; [ ] no report-back needing BQ execution; [ ] Deepan runs from office laptop, results pasted before next phase.
