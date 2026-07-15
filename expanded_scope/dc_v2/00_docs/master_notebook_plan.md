# dc_v2 — Master Notebook Plan

**Location:** `expanded_scope/dc_v2/00_docs/master_notebook_plan.md`
**Numbering:** continues from 40 (30–38 stays live for the p75 baseline only)
**Effort unit:** working days per notebook = design + Claude Code prompt +
run + review cycles. Ranges assume no data surprises; surprises become new
data-decision entries and add time.

---

## Section A — HCC Chronic Subproject (`hcc_chronic/`)

| # | Notebook | Does | Decision reached | Inputs needed | Effort |
|---|---|---|---|---|---|
| 40 | h1_mapping_coverage | Join V24 map to 1yr claims dx. Output: % ICDs mapped, % claim volume, % allowed, % members touched | Sanity gate: is "HCC = chronic" wide enough to build on | claims (1yr), V24 HCC-ICD table | 1–2 |
| 41 | h2_unmapped_review | Top 100 unmapped ICDs by volume and allowed | Validates assumption; flags real chronic leaking through | output of 40 | 1 |
| 42 | h3_condition_ranking | Rank HCCs by members/claims/allowed with cumulative %. Compare to CMS CCW published list | FINAL chronic condition feature list + V24 patch call (hypertension gap) — closes DD 05 | output of 40, CCW reference list | 2–3 |

Section total: **3 notebooks, 4–6 days**

## Section B — Foundation Data Checks (`data_decisions/`)

| # | Notebook | Does | Decision reached | Inputs needed | Effort |
|---|---|---|---|---|---|
| 43 | claims_window_profile | Per provider: first/last claim month, total months, largest gap. Overall min/max date | Actual history depth; forecast/model/cluster tiering rule | claims (full history) | 1–2 |
| 44 | new_returning_curves | New-patient % by month under 4 lookbacks (ever / 12m / 24m / 36m) | Final new/returning definition + burn-in months — closes DD 01 | claims (full), output of 43 | 2 |
| 45 | membership_profile | Load yearly membership. Per year: member count, % with usable age, % with usable zip, sanity of year-over-year curve | Confirms demand formula path; monthly upgrade yes/no — closes DD 02, DD 06 | yearly membership (Deepan) | 1–2 |

Section total: **3 notebooks, 4–6 days**

## Section C — Demand Data Build

| # | Notebook | Does | Decision reached | Inputs needed | Effort |
|---|---|---|---|---|---|
| 46 | demand_history_table | Build county x specialty_ctg_cd x month: visits (MEMBER county attribution) + all inputs (members, age mix, chronic prevalence, new %, provider count, penetration) | None — build. Attribution check mandatory | claims, membership, DD 05 list, DD 01 rule, penetration, county ref | 3–4 |
| 47 | demand_table_qa | Totals tie to raw claims; county sums = state; no double counting; attribution spot-check (sample members) | Table certified fit for modeling | output of 46 | 1–2 |

Section total: **2 notebooks, 4–6 days**

## Section D — Capacity Data Build

| # | Notebook | Does | Decision reached | Inputs needed | Effort |
|---|---|---|---|---|---|
| 48 | provider_history_table | Build provider x specialty_ctg_cd x month: visits delivered (PROVIDER county) + features (panel size, age/chronic mix, new %, par, tenure, geo spread, density) | None — build. Attribution check mandatory | claims, provider ref, par flags, DD 05 list, DD 01 rule | 3–4 |
| 49 | capacity_table_qa | Same certification as 47, provider side. Cross-check: total visits here = total visits in 46 (same claims, two lenses) | Table certified; demand/capacity totals reconcile | outputs of 46, 48 | 1 |

Section total: **2 notebooks, 4–5 days**

## Section E — Models

| # | Notebook | Does | Decision reached | Inputs needed | Effort |
|---|---|---|---|---|---|
| 50 | demand_forecast_model | Forecast per county x specialty series with external inputs. Hold out last period, validate. Simple model first, heavier only if it fails | Working demand forecast + honest error numbers (MD 01 conclusion) | certified 46 | 3–5 |
| 51 | provider_predictive_model | One feature model across all providers predicting monthly visits. Held-out validation. Serves demand-at-provider AND capacity | Working provider model + error numbers (MD 02 conclusion) | certified 48 | 3–5 |
| 52 | cluster_average_estimates | Cluster providers (and counties) on the same features; cluster average = second estimate. Also sustained-peak measure per provider | Cluster design decisions (how many, what stat) — MD 03 | certified 46, 48 | 2–3 |
| 53 | p75_baseline_extract | Pull the current-methodology numbers from live 30–38 pipeline into dc_v2 format | None — baseline for comparison | 30–38 outputs | 1 |

Section total: **4 notebooks, 9–14 days**

## Section F — Risk, Weave, Report

| # | Notebook | Does | Decision reached | Inputs needed | Effort |
|---|---|---|---|---|---|
| 54 | cms_risk_scores | Compute risk scores straight from CMS-HCC rules on our members. No model | None — rules-based, cite the rulebook | membership, claims dx, V24 model coefficients | 2–3 |
| 55 | weave | Bridge specialty_ctg_cd -> cms_specialty (once, here). Roll provider model to county. Three estimates side by side for D and C, gap per estimate, risk attached. County x cms_specialty x plan_type | Unmapped-specialty leakage disposition (drop vs Other) — final open DD | outputs of 50–54, 43-row crosswalk | 3–4 |
| 56 | final_report | House-style workbook: gap tabs per estimate, drill-down, caveats stated on tabs | None — deliverable | output of 55 | 2–3 |

Section total: **3 notebooks, 7–10 days**

---

## Totals

| Section | Notebooks | Days |
|---|---|---|
| A — HCC chronic | 3 | 4–6 |
| B — Foundation checks | 3 | 4–6 |
| C — Demand data | 2 | 4–6 |
| D — Capacity data | 2 | 4–5 |
| E — Models | 4 | 9–14 |
| F — Risk, weave, report | 3 | 7–10 |
| **Total** | **17** | **32–47 working days** |

Roughly 6–9 working weeks solo. Sections A and B run in parallel (A needs
only claims + V24; B needs claims + incoming membership), which pulls the
calendar to ~5–7 weeks if nothing blocks.

## Order of play

```
A (40–42) ──┐
            ├──> C (46–47) ──> E:50, E:52 ──┐
B (43–45) ──┤                               ├──> F (54–56)
            └──> D (48–49) ──> E:51, E:52 ──┘
E:53 anytime after 30–38 runs
```

## Blockers right now

1. Yearly membership data (Deepan) — blocks 45, 46
2. Claims history extension to full depth — blocks 43, 44
3. V24 table location/name confirmation — blocks 40
