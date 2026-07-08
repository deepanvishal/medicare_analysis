"""
eda_runner.py -- EDA for the demand/capacity/supply extension.   [PYTHON runner / read-only]

Answers the eight data questions from data_model_demand_capacity_gap.md. Column names
confirmed and hardcoded. Read-only: SELECT queries only, nothing created or modified.

Confirmed columns
  claims     : business_ln_cd (CP/ME), srv_start_dt (YYYY-MM-DD)
  membership : member_id, county_nm (CAPS), zip_cd, state_postal_cd, age_nbr,
               eff_dt (YYYY-MM-DD; latest row per member within eff year 2025)
  HCC map    : diagnosis_code (dots possibly removed), HCC_v24

OUTPUT : expanded_scope/eda_findings.md
Run    : python expanded_scope/eda_runner.py
"""

import os
import datetime
import config as cfg

DS     = f"{cfg.TABLE_PROJECT}.{cfg.DATASET}"
CLAIMS = f"{DS}.A870800_medicare_analysis_2025_claims"
MBR    = f"{DS}.mdcr_base_membership"
HCC    = f"{DS}.HCC_ICD_Mapping_2025"
FFS    = f"{DS}.cms_medicare_physician_ffs_2023"
PAR    = f"{DS}.{cfg.BASE_PREFIX}_{cfg.MS_INFIX}_provider_par_flag"

AGE_CASE = """CASE WHEN age_nbr BETWEEN 60 AND 64 THEN '60-64'
     WHEN age_nbr BETWEEN 65 AND 69 THEN '65-69'
     WHEN age_nbr BETWEEN 70 AND 74 THEN '70-74'
     WHEN age_nbr BETWEEN 75 AND 79 THEN '75-79'
     WHEN age_nbr >= 80 THEN '80+' END"""

# latest 2025 row per member
MBR_2025 = f"""(
  SELECT * FROM (
    SELECT m.*,
           ROW_NUMBER() OVER (PARTITION BY member_id ORDER BY eff_dt DESC) AS rn
    FROM `{MBR}` m
    WHERE EXTRACT(YEAR FROM eff_dt) = 2025
  ) WHERE rn = 1
)"""

print("eda_runner starting -- connecting to BigQuery (read-only)...", flush=True)
client = cfg.client()
print(f"connected. dataset = {DS}", flush=True)
out = []


def w(text=""):
    out.append(text)
    # echo each section header to the console so progress is visible while running
    if text.startswith("## "):
        print(f"\n{text}", flush=True)


def run(label, sql, max_rows=60):
    # print the query label before it runs, then the row count (or error) after it returns,
    # so a slow BigQuery call shows what it is waiting on
    print(f"  - {label} ...", end="", flush=True)
    w(f"**{label}**")
    w("```")
    try:
        rows = list(client.query(sql).result())
        print(f" ok ({len(rows)} rows)", flush=True)
        if not rows:
            w("(zero rows)")
        for i, r in enumerate(rows):
            if i >= max_rows:
                w(f"... ({len(rows) - max_rows} more rows truncated)")
                break
            w(str(dict(r)))
    except Exception as e:
        print(f" ERROR: {e}", flush=True)
        w(f"ERROR: {e}")
    w("```")
    w()


w("# EDA Findings — Demand / Capacity / Supply extension")
w(f"Generated {datetime.datetime.now():%Y-%m-%d %H:%M} · read-only · source: eda_runner.py")
w()

# ------------------------------------------------------------------ Q1 LOB
w("## Q1 · LOB mix (business_ln_cd)")
w()
run("LOB values and volume split",
    f"SELECT business_ln_cd, COUNT(*) AS claim_lines, COUNT(DISTINCT member_id) AS members "
    f"FROM `{CLAIMS}` GROUP BY 1 ORDER BY 2 DESC")
w("Expected: CP and ME. Total = CP+ME, Medicare = ME. Any other value needs a mapping decision.")
w()

# ------------------------------------------------------------------ Q2 membership monthly
w("## Q2 · Membership monthly counts (2025)")
w()
run("Members per month",
    f"SELECT EXTRACT(YEAR FROM eff_dt) AS yr, EXTRACT(MONTH FROM eff_dt) AS mo, "
    f"COUNT(*) AS row_count, COUNT(DISTINCT member_id) AS members "
    f"FROM `{MBR}` WHERE EXTRACT(YEAR FROM eff_dt) = 2025 "
    f"GROUP BY 1,2 ORDER BY 1,2", max_rows=15)
run("Dedup check: one row per member after latest-2025 rule",
    f"SELECT COUNT(*) AS rows_after_dedup, COUNT(DISTINCT member_id) AS members "
    f"FROM {MBR_2025}")
w("Watch for: coverage cliffs (a month with far fewer members) and rows_after_dedup != members.")
w()

# ------------------------------------------------------------------ Q3 member geography
w("## Q3 · Member geography coverage")
w()
run("Members per scope state, county coverage, null rates",
    f"SELECT state_postal_cd, COUNT(DISTINCT county_nm) AS counties, "
    f"COUNT(DISTINCT member_id) AS members, "
    f"COUNTIF(county_nm IS NULL) AS null_county_rows, "
    f"COUNTIF(zip_cd IS NULL) AS null_zip_rows, "
    f"COUNTIF(age_nbr IS NULL) AS null_age_rows "
    f"FROM {MBR_2025} "
    f"WHERE state_postal_cd IN ('FL','OH','AZ','IL') "
    f"GROUP BY 1 ORDER BY members DESC")
run("Members outside scope states (context)",
    f"SELECT COUNTIF(state_postal_cd IN ('FL','OH','AZ','IL')) AS in_scope, "
    f"COUNTIF(state_postal_cd NOT IN ('FL','OH','AZ','IL') OR state_postal_cd IS NULL) AS out_of_scope "
    f"FROM {MBR_2025}")
w("Expected county counts if full coverage: FL 67, OH 88, AZ 15, IL 102. "
  "High null_county with low null_zip = derive county from zip via ms_ref_zip_reference.")
w()

# ------------------------------------------------------------------ Q4 visit definition
w("## Q4 · Lines per visit (member x provider x day)")
w()
run("Lines per member x provider x day",
    f"SELECT ROUND(AVG(lines),1) AS avg_lines, APPROX_QUANTILES(lines, 4) AS quartiles, "
    f"MAX(lines) AS max_lines FROM ("
    f"SELECT member_id, srv_prvdr_id, srv_start_dt, COUNT(*) AS lines "
    f"FROM `{CLAIMS}` GROUP BY 1,2,3)")
run("Scale comparison",
    f"SELECT COUNT(*) AS claim_lines, COUNT(DISTINCT claim_line_id) AS distinct_claim_lines, "
    f"COUNT(DISTINCT CONCAT(member_id,'|',CAST(srv_prvdr_id AS STRING),'|',CAST(srv_start_dt AS STRING))) "
    f"AS member_provider_day_visits FROM `{CLAIMS}`")
w("avg near 1 = claim-line counting ~ visits; well above 1 = member x provider x day is the visit key.")
w()

# ------------------------------------------------------------------ Q5 morbidity
w("## Q5 · Morbidity distribution (HCC per member)")
w()
run("Dot-format check: sample dx codes from both sides",
    f"SELECT 'claims' AS side, pri_icd9_dx_cd AS code FROM `{CLAIMS}` "
    f"WHERE pri_icd9_dx_cd IS NOT NULL LIMIT 5")
run("(map side)",
    f"SELECT 'hcc_map' AS side, diagnosis_code AS code FROM `{HCC}` LIMIT 5")
run("Join hit rate",
    f"SELECT COUNT(DISTINCT c.member_id) AS members_with_hcc, "
    f"(SELECT COUNT(DISTINCT member_id) FROM `{CLAIMS}`) AS total_members "
    f"FROM `{CLAIMS}` c JOIN `{HCC}` h "
    f"ON REPLACE(c.pri_icd9_dx_cd, '.', '') = REPLACE(h.diagnosis_code, '.', '')")
run("HCC count distribution per member",
    f"WITH member_hcc AS ("
    f"SELECT c.member_id, COUNT(DISTINCT h.HCC_v24) AS hcc_count "
    f"FROM `{CLAIMS}` c JOIN `{HCC}` h "
    f"ON REPLACE(c.pri_icd9_dx_cd, '.', '') = REPLACE(h.diagnosis_code, '.', '') "
    f"GROUP BY 1) "
    f"SELECT hcc_count, COUNT(*) AS members FROM member_hcc GROUP BY 1 ORDER BY 1", max_rows=40)
w("Join is dot-insensitive (REPLACE both sides). Outcome: where do natural cuts sit for 2 vs 3 "
  "morbidity levels. Low join hit rate even dot-insensitive = deeper format problem, report samples.")
w()

# ------------------------------------------------------------------ Q6 thin cells
w("## Q6 · Cell thinness (state x specialty x age band)")
w()
run("Cell-size buckets by state",
    f"WITH cells AS ("
    f"SELECT LEFT(prvdr_submarket,2) AS state_cd, specialty_ctg_cd, {AGE_CASE} AS age_band, "
    f"COUNT(DISTINCT member_id) AS members "
    f"FROM `{CLAIMS}` WHERE age_nbr >= 60 GROUP BY 1,2,3) "
    f"SELECT state_cd, COUNTIF(members < 30) AS cells_lt_30, "
    f"COUNTIF(members >= 30 AND members < 100) AS cells_30_99, "
    f"COUNTIF(members >= 100) AS cells_gte_100, COUNT(*) AS total_cells "
    f"FROM cells GROUP BY 1 ORDER BY 1")
run("20 smallest AZ cells",
    f"SELECT specialty_ctg_cd, {AGE_CASE} AS age_band, COUNT(DISTINCT member_id) AS members "
    f"FROM `{CLAIMS}` WHERE age_nbr >= 60 AND LEFT(prvdr_submarket,2) = 'AZ' "
    f"GROUP BY 1,2 ORDER BY members ASC LIMIT 20")
w("Age-only cells; morbidity cuts each ~2-3x further. Most AZ cells >= 100 = per-state rates safe; "
  "many < 30 = national fallback needed for AZ thin cells.")
w()

# ------------------------------------------------------------------ Q7 FFS
w("## Q7 · FFS coverage and age feasibility")
w()
run("FFS coverage by scope state",
    f"SELECT rndrng_prvdr_state_abrvtn AS state, COUNT(*) AS provider_rows, "
    f"SUM(SAFE_CAST(tot_benes AS INT64)) AS total_benes, "
    f"SUM(SAFE_CAST(tot_srvcs AS INT64)) AS total_srvcs "
    f"FROM `{FFS}` WHERE rndrng_prvdr_state_abrvtn IN ('FL','OH','AZ','IL') "
    f"GROUP BY 1 ORDER BY 1")
run("Age-related columns in FFS schema",
    f"SELECT column_name, data_type "
    f"FROM `{DS}.INFORMATION_SCHEMA.COLUMNS` "
    f"WHERE table_name = 'cms_medicare_physician_ffs_2023' "
    f"AND LOWER(column_name) LIKE '%age%' ORDER BY ordinal_position")
w("Age columns exist = total-Medicare age-banded rate feasible. None = MA-proxy path runs, "
  "T3 rows labeled MA_PROXY. Flag any state with low provider_rows.")
w()

# ------------------------------------------------------------------ Q8 capacity
w("## Q8 · Capacity distributions")
w()
run("Provider annual volume percentiles per specialty (top 15 by provider count)",
    f"SELECT specialty_ctg_cd, COUNT(DISTINCT srv_prvdr_id) AS providers, "
    f"APPROX_QUANTILES(visits, 100)[OFFSET(25)] AS p25, "
    f"APPROX_QUANTILES(visits, 100)[OFFSET(50)] AS p50, "
    f"APPROX_QUANTILES(visits, 100)[OFFSET(75)] AS p75, "
    f"APPROX_QUANTILES(visits, 100)[OFFSET(95)] AS p95 "
    f"FROM (SELECT specialty_ctg_cd, srv_prvdr_id, COUNT(DISTINCT claim_line_id) AS visits "
    f"FROM `{CLAIMS}` GROUP BY 1,2) "
    f"GROUP BY 1 ORDER BY providers DESC LIMIT 15")
run("Senior-load distribution from par_flag (top 15 by provider count)",
    f"SELECT state_cd, cms_specialty, COUNT(DISTINCT provider_id) AS providers, "
    f"APPROX_QUANTILES(tot_benes, 100)[OFFSET(50)] AS p50_benes, "
    f"APPROX_QUANTILES(tot_benes, 100)[OFFSET(90)] AS p90_benes, "
    f"COUNTIF(tot_benes = 0) AS zero_benes_providers, "
    f"COUNTIF(aetna_par_flag = 1) AS active_providers "
    f"FROM `{PAR}` GROUP BY 1,2 ORDER BY providers DESC LIMIT 15")
w("Flag specialties with tiny p50 volume (volume-percentile capacity constant will not work) and "
  "cells where zero_benes_providers dominates (FFS match coverage problem).")
w()

# ------------------------------------------------------------------ manual item
w("## Manual item · CMS Geographic Variation county file (risk score)")
w()
w("Not run by this script. Download the county-level CMS Geographic Variation Public Use File "
  "(data.cms.gov), confirm county FIPS + average HCC risk score column, record file name / year / "
  "column name / county row counts for FL-OH-AZ-IL, save to Expanded_scope_medicare/. "
  "Do NOT load to BigQuery yet.")
w()

# cfg.repo_path uses config's __file__ (always defined, even in an interactive session,
# unlike this script's __file__). create the folder if missing; utf-8 for the ·/— chars
here = cfg.repo_path("expanded_scope")
os.makedirs(here, exist_ok=True)
path = os.path.join(here, "eda_findings.md")
with open(path, "w", encoding="utf-8") as f:
    f.write("\n".join(out))
print(f"\ndone. wrote {path} ({len(out)} lines)")
