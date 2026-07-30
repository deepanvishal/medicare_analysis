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

## Prompt M60c — zero-minute rule

You cannot run anything.

Two edits to capacity_methodology_v2.md, Stage 0:

Edit 1 — replace the fallback-ladder sentence with: "Unmatched procedure
codes get ZERO minutes by default — module 60 evidence shows unmatched
volume is dominated by quality-reporting F-codes, supplies, drugs, and
blank codes, none of which consume physician time. The
family-average/provider-average fallback applies ONLY to unmatched codes
that are 5-digit numeric CPT in clinical ranges; everything else is zero."

Edit 2 — the leftover stale sentence "The time file's own modifier rows
are excluded at load (module 60 keeps blank-modifier rows only)" — replace
with: "The time file has no modifier column; all rows load."

Also append to the Limitations list: "12. About 5% of claim lines carry a
blank or unusable procedure code and contribute zero minutes; counted in
the V1 materiality check."

Change nothing else. Output: the edited file. Append this prompt to the
pack under "## Prompt M60c — zero-minute rule".

## Prompt M61-62 batch

You cannot run anything. No BigQuery execution. Deepan runs everything.
Read first: 00_docs/PLAN.md, capacity_methodology_v2.md,
capacity_data_model_v2.md.

STANDING RULES for this and all future 08_capacity_risk scripts:
R1. Internal claims come ONLY from A870800_medicare_analysis_2025_claims.
    Never EMIS_CLAIM_LINE.
R2. Every script gets RUN_MODE = 'sample' | 'full' at top. sample filters
    internal claims to MOD(ABS(FARM_FINGERPRINT(CAST(member_id AS
    STRING))), 100) = 0 (1% of members) and prints elapsed seconds per
    query. Deepan times sample before running full.
R3. Before finishing each script, run three adversarial self-reviews and
    write findings as a REVIEW block comment at the bottom:
    - Reviewer 1 LOGIC: attribution columns (prvdr_county for capacity),
      join keys verified against actual repo code, no fabricated columns.
    - Reviewer 2 SPEC: every column and rule traced to the two spec docs;
      deviations listed explicitly.
    - Reviewer 3 EFFICIENCY: scan count over the claims table (must be 1
      per script), no CROSS JOINs, no row-explosion joins; estimated
      relative cost note.
    Any unresolved concern from any reviewer = STOP and ask instead of
    writing the script.

DOC MICRO-AMENDMENTS first (do these before the scripts):
- capacity_data_model_v2.md, cap_observed_detail: internal AETNA_MA rows
  are DAY grain — period_type 'DAY', period_start = svc_start_dt. Update
  the period_type Notes. Reason: one claims scan serves both modules.
- capacity_methodology_v2.md Module 62 checklist: replace "[ ] fallback
  ladder for unmapped codes" with "[ ] zero-minute default; clinical-CPT
  fallback only".

SCRIPT 1 — 61_observed.py -> table cap_observed_detail
- Internal side: one scan of A870800_medicare_analysis_2025_claims,
  srv_start_dt in 2024-01-01..2025-12-31. Group to epdb_dw_prvdr_id x
  prcdr_cd x prvdr_county x specialty_ctg_cd x svc_start_dt. svc_cnt =
  visit definition from 48 (distinct member x provider x date collapses to
  the day row); mbr_cnt = distinct members. Attach npi via
  xwalk_pin_npi_all — READ the repo's existing use of that xwalk to get
  the true join keys; if ambiguous, STOP and ask. Keep BOTH keys
  (epdb_dw_prvdr_id AND npi; npi NULL when unmatched, count printed).
  src='AETNA_MA', period_type='DAY'.
- CMS side: from cms_medicare_physician_ffs_2023 WHERE rndrng_prvdr_ent_cd
  = 'I' AND state in the FL/OH/AZ/IL footprint. Derive prvdr_county in the
  SAME NAME FORMAT as internal rows: zip5 -> county via the repo's zip
  xwalk pattern, then fips -> county name via ref_county. READ ref_county's
  actual columns first; if the fips-to-name mapping is not derivable, STOP
  and ask. One row per npi x year: src='CMS_FFS', period_type='YEAR',
  period_start=2023-01-01, svc_cnt=med_tot_srvcs, hcpcs_cd=NULL,
  mbr_cnt=tot_benes.
- Sanity prints: row counts by src; npi match rate internal side; distinct
  counties by src; sample of 5 county names per src side-by-side (visual
  format check).

SCRIPT 2 — 62_hours.py -> tables cap_hours_daily, cap_hours_annual
- Reads ONLY cap_observed_detail + ref_mpfs_time. No claims scan.
- RAW minutes only in this module (deflation happens in module 64 after
  calibration): line_mins = svc_cnt x intra_mins; unmatched hcpcs = 0
  minutes per the Stage 0 zero-minute rule; blank/NULL hcpcs = 0.
- cap_hours_daily (internal rows only): npi x prvdr_county x svc_dt with
  raw_hrs, mapped_svc_cnt, unmapped_svc_cnt. Column defl_hrs created but
  NULL (filled by module 64).
- cap_hours_annual (both sources): internal = sum of daily; CMS =
  med_tot_srvcs x provider's own avg raw mins per matched internal service
  (SAFE_DIVIDE of the provider's internal raw minutes over matched
  services); cohort (specialty x state) average when the provider has no
  matched internal rows; store which path via avg_mins_src_cd
  'OWN'/'COHORT'. Include avg_mins_per_svc and ceiling_unit_cd='HOURS'.
- Sanity prints: total raw hours by src; % CMS rows on COHORT fallback;
  top 10 providers by raw annual hours with their specialty (eyeball
  check for absurd values).

Outputs: the two .py files plus the two doc micro-edits. Append this full
prompt to the pack under "## Prompt M61-62 batch".

## Prompt M61b — doc catch-up

You cannot run anything.

Doc catch-up for the module 61/62 REVIEW deviations — edit
capacity_data_model_v2.md:

1. cap_observed_detail: add rows for epdb_dw_prvdr_id (key part, internal
   Aetna id; npi NULL when xwalk-unmatched) and prvdr_state_cd (internal:
   UPPER(LEFT(prvdr_submarket,2)); CMS: rndrng_prvdr_state_abrvtn). Key
   note: grouping is by BOTH provider keys.
2. cap_hours_annual: add period_yr (2023 CMS / 2024 / 2025 internal —
   vintages never mixed), rename defl_hrs_yr note to "NULL until module
   64", add raw_hrs_yr and avg_mins_src_cd ('OWN'/'COHORT'/NULL).
3. Add one line under Cross-cutting rules: "CMS-only providers (no
   internal rows) carry NULL hours and no specialty_ctg_cd — acceptable
   because zero Aetna volume means zero willing capacity under Stage 9
   rules; revisit only for total-market analyses (decision CD-21)."

And capacity_methodology_v2.md decision log, append:
| CD-21 | CMS-only providers keep NULL hours in v1 | Zero Aetna share
zeroes their contribution regardless; specialty mapping deferred. |

Change nothing else. Append to pack under "## Prompt M61b — doc catch-up".

## Prompt M62b — state everywhere

You cannot run anything.

Edit 1 — capacity_data_model_v2.md, batch state-column amendment: add
prvdr_state_cd to cap_hours_daily, cap_provider_year, cap_cohort_bench,
cap_provider_segment, cap_willing; add mbr_state_cd (derived from
mbr_submarket per the locked fact) to dem_segment_split and
cap_county_risk; cap_fill_result gets both (demand side mbr_state_cd,
provider side prvdr_state_cd). Note each as "rule 12 — county never
stands alone".

Edit 2 — 62_hours.py: add prvdr_state_cd to cap_hours_daily (carried from
cap_observed_detail), include it in the table write and grain comment.

Change nothing else. Append to pack under "## Prompt M62b — state
everywhere".

## Prompt M63-72 drafts

Yes — add ind_src_cd ('CMS_I'/'ASSUMED') to cap_provider_year in the data
model. And here is the drafts prompt you were missing:

You cannot run anything. No BigQuery execution. Deepan runs everything.
Standing rules R1-R3 apply. Rule 12 (county never without state), CD-22,
CD-23, CD-24 apply as already logged in the docs.

Task: write ALL remaining pipeline scripts as DRAFTS, one file each, in
expanded_scope/dc_v2/08_capacity_risk/:

63_calibrate.py        -> cap_params
64_ceiling.py          -> cap_daily_capped
65_provider_year.py    -> cap_provider_year
66_cohort_bench.py     -> cap_cohort_bench
67_provider_segment.py -> cap_provider_segment
68_dem_segment_split.py-> dem_segment_split
69_fill.py             -> cap_fill_result
70_willing.py          -> cap_willing
71_county_risk.py      -> cap_county_risk
72_validate.py         -> cap_validation

Source of truth: capacity_methodology_v2.md (stages, formulas, gates) and
capacity_data_model_v2.md (every table, column, key, note). Implement
exactly what the specs say. Where a spec point references live dc_v2
inputs (48's panel features for 67, 50's forecast anchor for 68, the
DD-01 new/returning rule, the contract flag source for 70), READ the
actual repo code first and use real table and column names.

DRAFT DISCIPLINE (replaces stop-and-ask for this batch only): when a fact
cannot be verified from repo files or the specs, do NOT guess silently and
do NOT stop — write a clearly marked block at the top of that script:
# ASSUMPTION [n]: <what was assumed, why, and what would falsify it>
and keep going. Values that belong to cap_params are READ from cap_params,
never hard-coded (module 63 is the only writer of cap_params).

Every script: house docstring, config pattern, RUN_MODE where anything
claims-derived is scanned, sanity prints per its spec section, REVIEW
block with the three adversarial reviews (R3).

Final output: the ten .py files, plus a single summary listing every
ASSUMPTION block across all scripts, grouped by script. Append this
prompt to the pack under "## Prompt M63-72 drafts".

## Prompt M63-72 triage

Assumption triage results — apply these, then Deepan runs.

FIX 1 (code, 70_willing.py) — 70-A1 is wrong per CD-23: willing capacity
must use ABSORBING capacity, not bare spare. willing_spare_hrs =
(spare_hrs + team_uplift_hrs) x aetna_share. Update the data model formula
note too (the doc was stale, CD-23 governs).

FIX 2 (code, 63_calibrate.py) — 63-A1: the solve objective DOES exist,
methodology §9-P1: solve deflation per code class so the 99.5th percentile
of provider-day hours (excluding high_day_flag providers) lands at
DAILY_CAP_HRS. Implement that solve; keep anchors as the starting point
and as fallback if the solve is degenerate. 63-A3's circularity note
stands: first run uses the blend, rerun after 64 verifies.

RULING (68-A1): specialty_ctg_cd grain is CORRECT for dem_segment_split —
the anchor lives at ctg grain and rule 6 bridges exactly once at the fill
join. Amend the data model's cms_specialty reference on that table to
specialty_ctg_cd, and note cap_fill_result/cap_county_risk carry the
bridged cms_specialty from the fill join onward.

DOC AMENDMENTS (all four you listed): SHARE_STABILITY_TOL into the
cap_params param list; the 68 specialty ruling above; cap_fill_result
extra columns (epdb_dw_prvdr_id, specialty_ctg_cd, signal_src_cd);
cap_provider_year single-year note ("capacity base year = 2025; 2024
retained in cap_hours_annual for intake windows and stability checks").

APPROVED AS-IS (log nothing, change nothing): all remaining assumptions,
including 65-A1 (2025 base year — correct), 64-A4 (raw>24 — correct),
67-A1 (panel_cnt weight, v1), 69-A1 (same-county fill; add one line to
methodology Limitations: "14. Fill places patients within their own
county only; cross-county access understated — conservative."), V7/V8
stubs, V9 name-only join with its caution note.

Append to pack under "## Prompt M63-72 triage". Confirm edits, then
Deepan begins the run streak.
