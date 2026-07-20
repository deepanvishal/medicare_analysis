# model_and_dashboard_v1 — Project Overview

A what-if scenario dashboard for Medicare Advantage supply-demand analysis.
Separate from all prior work in this repo. Nothing here references, imports, or
reuses code from `expanded_scope/` or root-level scripts.

## The concept

Dashboard shows 2025 actuals for a county: enrollment and claims counts by
specialty. A what-if layer lets the user simulate enrollment changes:

- A master slider moves total county enrollment up or down. Baseline behavior:
  the change spreads across demographic bands proportionally.
- Per-demographic override sliders let the user manually shift one band
  (e.g., what if 85+ grew faster than the rest).

The simulation cascade:

```
slider input (enrollment change by demographic band)
  -> demographic-to-disease prevalence rates
  -> disease-to-specialty utilization rates
  = new demand per specialty
  -> routed to providers via intake/affinity weights
  = new load per provider
  -> compared against per-provider capacity ceilings
  = providers at capacity / over capacity / with headroom
```

## The framework (target state, not this phase)

- Model A (demand): predicts utilization per county x demographic x specialty
  from enrollment, disease/HCC prevalence, historical utilization.
- Model B (capacity): predicts each provider's absorbable volume, monthly and
  yearly. Capacity is Aetna-relative by explicit assumption. Architecture is
  parked and will be designed separately.
- Model C (routing): allocates new demand to providers using historical
  new-patient intake rates, age-group affinity, and specialty claim share.
- Simulation layer: no model runs live. All models export precomputed rate
  tables. The dashboard executes only arithmetic on those coefficients.

## Current phase: minimal mock test

`mock_dashboard.html` validates mechanics and UX only. No real data, no
BigQuery, no models. All coefficients hardcoded:

- 1 fictional county (Meridian County)
- 3 demographic bands: 65-74, 75-84, 85+
- 4 specialties: Primary Care, Cardiology, Nephrology, Orthopedics
- 10 mock providers with intake weights and capacity ceilings
- Master enrollment slider plus per-demographic override sliders
- Outputs: specialty demand deltas; provider table with at-capacity and
  over-capacity flags

## How to open

Double-click `mock_dashboard.html` (or open it in any browser). No installs,
no server, no data connections — everything runs as local arithmetic in the
page.

## Where the mock coefficients live

One clearly marked `CONFIG` block at the top of the `<script>` section in
`mock_dashboard.html`: baseline enrollment per band, prevalence rates
(band x condition), utilization rates (condition x specialty), provider
intake weights and capacity ceilings. In a later phase, real model extracts
replace that single block; the dashboard mechanics do not change.

## File map

| File | Role |
|------|------|
| `README.md` | this overview |
| `mock_dashboard.html` | phase-1 mock dashboard (self-contained) |

## Phase gate

If the cascade feels right in the mock, real extracts replace the mock
coefficients in a later phase. Models A/B/C are designed separately and only
ever ship coefficient tables to this dashboard.
