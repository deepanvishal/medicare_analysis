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

## Prompt M58b — extract rebuild (prcdr_cd)

You cannot run anything. No BigQuery execution. Deepan runs everything.

Open expanded_scope/test_sql.sql. In the statement that creates
A870800_medicare_analysis_2025_claims, in the final SELECT's "-- claim
core" section, add one line
, a.prcdr_cd
directly after the line
, a.pri_icd9_dx_cd

Change nothing else in the file. Deepan will run the rebuild himself.

Also append to 00_docs/prompt_pack_capacity_risk.md

## Prompt M59

Status updates closing your open questions:

Q1 CLOSED: prcdr_cd added to the extract; Deepan rebuilt
A870800_medicare_analysis_2025_claims and confirms procedure codes are
populated. Modifier column: checked — EMIS_CLAIM_LINE has none. Modules
60-72 proceed without modifier handling; this becomes a documented
limitation in a later doc-correction prompt.

Q2 CLOSED: cms_medicare_physician_ffs_2023 does not exist in BigQuery. Your
grain analysis was right and DD 09's "NPI x HCPCS" claim is wrong — a
doc-correction prompt follows later. Decision: we use the CMS by-Provider
SUMMARY file (one row per NPI, ~1.26M rows), already downloaded. CMS-side
hours will use average minutes per service, not code-level minutes.

Bookkeeping: in prompt_pack_capacity_risk.md, rename your heading
"## Prompt M59 — extract rebuild (prcdr_cd)" to "## Prompt M58b — extract
rebuild (prcdr_cd)". M59 is reserved for the task below.

New task — module 59:

You cannot run anything. No BigQuery execution. Deepan runs everything.

Read first: expanded_scope/dc_v2/00_docs/PLAN.md, capacity_methodology_v2.md,
capacity_data_model_v2.md, data_decisions.md DD 09.

Create expanded_scope/dc_v2/08_capacity_risk/59_load_cms_provider_2023.py
One script, house docstring style copied from 48 (WHAT/GRAIN/INPUTS/OUTPUT/
Run), config pattern cfg.client()/cfg.table()/cfg.run_ddl().

INPUT FILE (hardcode):
expanded_scope/dc_v2/08_capacity_risk/inputs/Medicare_Physician_Other_Practitioners_by_Provider_2023.csv
Grain: one row per rndrng_npi. Expected rows ~1,259,343 — assert within 1%
of this after read, stop with a clear message if not.

FIRST ACTION: read only the CSV header row and print it. Compare against
KEEP_COLUMNS below; if any listed column is absent, STOP with a message
listing the missing names. Do not guess substitutes.

KEEP_COLUMNS (mark each TODO VERIFY against the printed header; drop all
other columns at read time for memory):
  rndrng_npi
  rndrng_prvdr_ent_cd          (I = individual, O = organization)
  rndrng_prvdr_type
  rndrng_prvdr_state_abrvtn
  rndrng_prvdr_zip5
  rndrng_prvdr_mdcr_prtcptg_ind
  tot_srvcs
  tot_benes
  tot_med_srvcs
  tot_drug_srvcs
  tot_mdcr_pymt_amt
  bene_avg_age
  bene_age_lt_65_cnt
  bene_age_65_74_cnt
  bene_age_75_84_cnt
  bene_age_gt_84_cnt
  bene_avg_risk_scre
PLUS every column whose name starts with bene_cc_ (keep all; names vary by
vintage).

LOAD RULES:
- All numeric columns: SAFE numeric coercion; CMS suppression markers
  ('*', '#', blank) become NULL, never 0.
- Add load_ts TIMESTAMP and src_file STRING (the filename) columns.
- Write BigQuery table cms_medicare_physician_ffs_2023 via cfg pattern,
  WRITE_TRUNCATE. This intentionally reuses the table name already
  referenced by 12_provider_par_flag.py and CLAUDE.md — that table does not
  currently exist; this load makes those references valid.

SANITY PRINTS after load: row count; % ent_cd = 'I'; % participation flag
populated; NULL rate of tot_med_srvcs; distinct rndrng_prvdr_type count;
sum(tot_med_srvcs) vs sum(tot_srvcs).

ALSO: create expanded_scope/dc_v2/08_capacity_risk/inputs/.gitignore
containing two lines: *.csv and *.xlsx — raw inputs never get committed.

Outputs: the .py file and the .gitignore. Append this prompt verbatim to
00_docs/prompt_pack_capacity_risk.md under "## Prompt M59".
