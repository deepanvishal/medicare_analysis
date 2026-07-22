# 04_VALIDATION_RULES — model_and_dashboard_v1

These rules are enforced as hard assertions at the end of every notebook; a
notebook that fails its gate stops the pipeline; rules live here,
enforcement lives in code.

R1 Row-count continuity: every derived table states its expected
relationship to its source (equal, subset with stated filter, or
aggregation whose totals reconcile) and asserts it.

R2 Key integrity: grain columns are non-null and unique at the stated
grain; assert both.

R3 Attribution: demand-side tables use member county; capacity-side tables
use provider county; every table-building script states which one it uses
in its header and never mixes them.

R4 Reconciliation: every rate table must, when multiplied back against its
base population, reproduce the source totals within 0.5 percent; assert.

R5 Suppressed values: CMS-sourced numeric columns may contain '*' or '#';
always SAFE_CAST and count how many rows were lost to suppression.

R6 Scope: members aged 60+, LOB in (CP, ME), footprint states FL, OH, AZ,
IL; every notebook restates and re-applies scope rather than trusting an
upstream filter.

R7 No silent drops: every join reports rows in vs rows out; unexplained
loss above 1 percent fails the gate.

R8 Frozen seeds: any sampling or model fitting sets an explicit random
seed.
