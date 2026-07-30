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

## Prompt M59c — stale modifier cleanup

You cannot run anything.

Cleanup of the stale spots you flagged, plus the duplicate:

Edit 1 — data_decisions.md DD 09 item 1: collapse the duplicated "no
service dates" so it's stated once.

Edit 2 — capacity_methodology_v2.md Stage 0: replace the "-TC -> 0 min /
-26 -> full" modifier rule sentence with: "The internal claims source has
no modifier column (limitation 11); minutes apply as loaded. The time
file's own modifier rows are excluded at load (module 60 keeps
blank-modifier rows only)."

Edit 3 — capacity_methodology_v2.md, module 62 checklist: replace the
"[ ] modifier rules" line with "[ ] confirm no modifier handling
(limitation 11)".

Edit 4 — capacity_data_model_v2.md, cap_observed_detail: remove the
modifier_grp_cd column row, and change the key definition so hcpcs_cd is
part of the key for AETNA_MA rows only — state it as: key = npi x
prvdr_county x src x period_start, plus hcpcs_cd for AETNA_MA rows;
CMS_FFS rows have hcpcs_cd = NULL and one row per npi x county x year.

Change nothing else. Output: the edited files only. Append this prompt to
the prompt pack under "## Prompt M59c — stale modifier cleanup".

## Prompt M60

You cannot run anything. No BigQuery execution. Deepan runs everything.

Read first: 00_docs/PLAN.md, capacity_methodology_v2.md (Stage 0),
capacity_data_model_v2.md (ref_mpfs_time, ref_segment).

Create expanded_scope/dc_v2/08_capacity_risk/60_load_time_file.py
House docstring style from 48, config pattern like module 59.

INPUT (hardcode):
expanded_scope/dc_v2/08_capacity_risk/inputs/CMS-1807-F_Work_Time_16OCT24.xlsx
tab 'Work Time'.

FIRST ACTION: read and print the tab's header row. Map columns via a
COLUMN_MAP dict marked TODO VERIFY for: hcpcs code, modifier, the three
pre-service time columns, intra-service time, and the post-service time
columns. If any mapping target is absent from the real header, STOP and
print the header.

LOAD RULES:
- Keep only rows where the modifier column is blank/null. Print the count
  of hcpcs codes that appear ONLY with a modifier value (excluded codes).
- pre_mins = sum of the three pre-service columns, SAFE numeric each.
- intra_mins = intra-service column, SAFE numeric.
- post_mins = sum of post-service time columns present, SAFE numeric.
- code_class_cd: hcpcs starting '99' -> 'EM'; numeric hcpcs between 10021
  and 69990 -> 'PROC'; else 'OTHER'.
- code_family_cd = first 3 characters of hcpcs.
- mpfs_cy = 2025.
- Write ref_mpfs_time via cfg pattern, WRITE_TRUNCATE.

SEED ref_segment, exactly 8 rows (segment_cd, new_flag, chronic_flag,
age_band_cd, segment_nm):
NEW_CHR_60_74,1,1,60_74,New chronic 60-74
NEW_CHR_75P,1,1,75P,New chronic 75+
NEW_NONCHR_60_74,1,0,60_74,New non-chronic 60-74
NEW_NONCHR_75P,1,0,75P,New non-chronic 75+
RET_CHR_60_74,0,1,60_74,Returning chronic 60-74
RET_CHR_75P,0,1,75P,Returning chronic 75+
RET_NONCHR_60_74,0,0,60_74,Returning non-chronic 60-74
RET_NONCHR_75P,0,0,75P,Returning non-chronic 75+

MATCH-RATE REPORT (the module 60 gate), one BigQuery query the script runs
and prints: join A870800_medicare_analysis_2025_claims.prcdr_cd (UPPER,
TRIM) to ref_mpfs_time.hcpcs_cd. Print: total claim lines, matched lines,
match % overall, match % by specialty_ctg_cd (sorted worst first), and top
25 unmatched prcdr_cd values by line count. No CMS-side match query — the
CMS table has no procedure detail.

SANITY PRINTS: ref_mpfs_time row count; % rows intra_mins > 0;
code_class_cd distribution; ref_segment count (must be 8).

Outputs: the .py file. Append this prompt verbatim to
00_docs/prompt_pack_capacity_risk.md under "## Prompt M60".

## Prompt M60b — table prefix rule

You cannot run anything.

Decision: ref_mpfs_time and ref_segment stay PREFIXED via cfg.table(), as
module 60 built them. Make it binding:

Edit capacity_data_model_v2.md — in the Cross-cutting rules section,
append: "All cap_/ref_ tables in modules 59-72 are created and read via
cfg.table() (house prefix). A bare table name in any 59-72 script is a
defect."

Change nothing else. Output: the edited file. Append this prompt to the
pack under "## Prompt M60b — table prefix rule".
