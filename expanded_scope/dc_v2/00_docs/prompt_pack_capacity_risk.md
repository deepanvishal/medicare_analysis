# Capacity Risk (modules 60–72) — Prompt Pack

Paste each prompt into Claude Code separately, in order. Deepan runs every
script from the office laptop and pastes results back before the next
prompt. Spec: capacity_methodology_v2.md + capacity_data_model_v2.md.

STATUS: no build prompts applied yet. Doc-restructure prompts 1–7 applied
[date to fill].

RUN ORDER: 60 -> 61 -> 62 -> 63 -> 64 (GATE: impossible <1%) -> 65 -> 66
-> 67 ; 68 after 63 ; -> 69 -> 70 -> 71 -> 72.
Module 60 GATE: time-file match rate report reviewed before 61.

Build prompts are appended below as they are written. Do not invent them.

## Prompt M58

You cannot run anything. No BigQuery execution. Deepan runs everything.

Read first: expanded_scope/dc_v2/00_docs/PLAN.md. Then open test_sql.sql at
the repo location referenced by earlier work (the script that builds
A870800_medicare_analysis_2025_claims) and copy the EXACT source table path
from its FROM clause — the raw claims-line table the extract reads. Do not
guess or shorten the path.

Task: create expanded_scope/dc_v2/08_capacity_risk/58_check_source_columns.sql

Content: one query against that source table's INFORMATION_SCHEMA.COLUMNS
returning column_name and data_type for any column whose lowercase name
contains prcdr, proc, hcpcs, cpt, or mod, ordered by ordinal_position. Add
a short header comment stating: purpose (find the procedure-code and
modifier columns for the extract rebuild feeding modules 60-61), the source
table path used and which file it was copied from, and "Run manually in
BigQuery console; paste results back."

If test_sql.sql cannot be located or its FROM clause is ambiguous (multiple
source tables), STOP and list what you found instead of picking one.

One output: the .sql file. Append this prompt verbatim to
00_docs/prompt_pack_capacity_risk.md under "## Prompt M58".
