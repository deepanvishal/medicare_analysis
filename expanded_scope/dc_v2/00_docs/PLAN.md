# dc_v2 — PLAN.md

READ THIS FIRST. Then read data_decisions.md, model_decisions.md, and
master_notebook_plan.md before writing any code in this folder.

## What this is

Version 2 of the demand-capacity analysis. Replaces the 30–38 heuristic
pipeline with modeled demand and capacity. 30–38 stays live only to produce
the p75 baseline. New modules number from 40. See master_notebook_plan.md
for the full 17-notebook map.

## Rules for Claude Code (non-negotiable)

1. You cannot run anything. No BigQuery execution, no reporting back of
   query results. Deepan runs everything.
2. One output per prompt. Exactly what the prompt asks, nothing more.
3. Zero interpretation. If codes/columns/filters are not specified in the
   prompt, stop and ask — do not approximate.
4. Never fabricate query results or data values.
5. Use column names exactly as defined. No invented jargon.
6. Read existing code before editing. Never guess column names.
7. Work only from live files. Never surface trashbin/ or deprecated files.

## Locked technical facts (do not rediscover these)

- Config pattern: config.py with cfg.client(), cfg.table(), cfg.run_ddl().
  Billing project anbc-dev-prv-nc-ds; tables in anbc-hcb-dev.
- LOB values are CP and ME (not MA).
- eff_df is the membership date column (not eff_dt).
- AZ census FIPS need LPAD (no leading zeros in BQ public data).
- State from prvdr_submarket requires UPPER(LEFT(...,2)).
- mdcr_tin_par_flag is TIN-level; use mdcr_base_claim for provider-level par.
- SAFE_CAST for CMS numeric columns (suppressed values stored as '*' or '#').
- Excel house style follows 13_build_report.py.

## Locked design decisions (summary — details in the other docs)

- Chronic = member has >=1 claim ICD mapping to a CMS-HCC V24 code. The
  final condition feature list comes from notebook 42 (DD 05).
- Demand = visits attributed to the MEMBER's county. Capacity = visits
  attributed to the PROVIDER's county. Same claims, two lenses. Picking the
  wrong county column destroys the model. Every table-build script states
  which county column it uses.
- Capacity = Aetna-realized throughput only. Non-Aetna volume out of scope.
  Caveat stated on every capacity output tab.
- Models run at specialty_ctg_cd. Bridge to cms_specialty happens ONCE, in
  the weave (notebook 55), via ref_specialty_crosswalk (43-row).
- Grains: forecast = county x specialty_ctg_cd x month; predictive =
  provider x specialty_ctg_cd x month; weave = county x cms_specialty x
  plan_type.
- Three estimates side by side for demand and capacity: forecast model,
  cluster average, p75 baseline (from 30–38).
- Membership input is yearly; each year's number fills its 12 months
  (DD 06). Monthly upgrade TBD.
- New vs returning patient definition comes from notebook 44 (DD 01) —
  candidate lookbacks ever/12m/24m/36m, decided by where the curves flatten.
- Unmet demand estimation is OUT OF SCOPE for MVP. Demand = observed visits.
- CMS risk is rules-based from the CMS-HCC model. No modeling.

## Folder map

```
dc_v2/
  00_docs/
    PLAN.md                  this file — read first
    data_decisions.md        DD log — every questionable data call
    model_decisions.md       MD log — targets, inputs, model choices
    master_notebook_plan.md  17 notebooks, status tracked here
  01_hcc_chronic/            40 coverage, 41 unmapped, 42 ranking
  02_foundation/             43 claims profile, 44 new/returning, 45 membership
  03_demand/                 46 history table, 47 QA
  04_capacity/               48 provider table, 49 QA
  05_models/                 50 forecast, 51 predictive, 52 clusters, 53 p75
  06_weave/                  54 risk, 55 weave, 56 report
  07_data_decisions/         per-decision analysis outputs (dd_01/, dd_02/...)
```

## Status discipline

After a notebook is built and its run reviewed, update its row in
master_notebook_plan.md and fill the matching Conclusion section in
data_decisions.md or model_decisions.md. A notebook is not done until its
decision is written down.
