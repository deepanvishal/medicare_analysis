"""
eda_runner.py -- EDA for the demand/capacity/supply extension.   [PYTHON runner / read-only]

Answers the eight open questions from data_model_demand_capacity_gap.md (Q1-Q7 here;
Q8, the CMS Geographic Variation county risk-score file, is a manual download noted
in the output). Read-only: SELECT queries only, no tables created or modified.

Resolves column names at runtime: pulls INFORMATION_SCHEMA first (Q0), then picks
each needed column from a candidate list. Every resolution or failure is recorded
in the output so nothing is silently guessed.

OUTPUT : expanded_scope/eda_findings.md
Run    : python expanded_scope/eda_runner.py
"""

import datetime
import config as cfg

DS = f"{cfg.TABLE_PROJECT}.{cfg.DATASET}"

CLAIMS_NAME = "A870800_medicare_analysis_2025_claims"
MBR_NAME    = "mdcr_base_membership"
HCC_NAME    = "HCC_ICD_Mapping_2025"
FFS_NAME    = "cms_medicare_physician_ffs_2023"
PAR_NAME    = f"{cfg.BASE_PREFIX}_{cfg.MS_INFIX}_provider_par_flag"

CLAIMS = f"{DS}.{CLAIMS_NAME}"
MBR    = f"{DS}.{MBR_NAME}"
HCC    = f"{DS}.{HCC_NAME}"
FFS    = f"{DS}.{FFS_NAME}"
PAR    = f"{DS}.{PAR_NAME}"

AGE_CASE = """CASE WHEN age_nbr BETWEEN 60 AND 64 THEN '60-64'
     WHEN age_nbr BETWEEN 65 AND 69 THEN '65-69'
     WHEN age_nbr BETWEEN 70 AND 74 THEN '70-74'
     WHEN age_nbr BETWEEN 75 AND 79 THEN '75-79'
     WHEN age_nbr >= 80 THEN '80+' END"""

client = cfg.client()
out_lines = []


def w(text=""):
    out_lines.append(text)


def run(label, sql, max_rows=60):
    w(f"**{label}**")
    w("```")
    try:
        rows = list(client.query(sql).result())
        if not rows:
            w("(zero rows)")
        for i, r in enumerate(rows):
            if i >= max_rows:
                w(f"... ({len(rows) - max_rows} more rows truncated)")
                break
            w(str(dict(r)))
        w("```")
        w()
        return rows
    except Exception as e:
        w(f"ERROR: {e}")
        w("```")
        w()
        return None


def schema(table_name):
    sql = f"""
    SELECT column_name, data_type
    FROM `{DS}.INFORMATION_SCHEMA.COLUMNS`
    WHERE table_name = '{table_name}'
    ORDER BY ordinal_position
    """
    try:
        return {r["column_name"]: r["data_type"] for r in client.query(sql).result()}
    except Exception as e:
        w(f"ERROR fetching schema for {table_name}: {e}")
        return {}


def pick(cols, candidates, purpose):
    lower = {c.lower(): c for c in cols}
    for cand in candidates:
        if cand.lower() in lower:
            real = lower[cand.lower()]
            w(f"- {purpose}: resolved to `{real}`")
            return real
    fuzzy = [c for c in cols if any(k in c.lower() for k in candidates[0].split("_"))]
    w(f"- {purpose}: NOT RESOLVED. Candidates tried: {candidates}. Fuzzy matches in table: {fuzzy}")
    return None


w("# EDA Findings — Demand / Capacity / Supply extension")
w(f"Generated {datetime.datetime.now():%Y-%m-%d %H:%M} · read-only · source: eda_runner.py")
w()

# ---------------------------------------------------------------- Q0 schemas
w("## Q0 · Schemas")
w()
schemas = {}
for name in (CLAIMS_NAME, MBR_NAME, HCC_NAME, FFS_NAME):
    schemas[name] = schema(name)
    w(f"**{name}** ({len(schemas[name])} columns)")
    w("```")
    for c, t in schemas[name].items():
        w(f"{c} : {t}")
    w("```")
    w()

claims_cols = schemas[CLAIMS_NAME]
mbr_cols    = schemas[MBR_NAME]
hcc_cols    = schemas[HCC_NAME]
ffs_cols    = schemas[FFS_NAME]

w("**Column resolution**")
lob_col   = pick(claims_cols, ["lob", "lob_cd", "line_of_business", "lob_desc"], "claims LOB")
date_col  = pick(claims_cols, ["srv_start_dt", "srv_dt", "service_dt", "clm_srv_dt", "srv_start_date"], "claims service date")
hcc_icd   = pick(hcc_cols, ["icd_cd", "icd10_cd", "diagnosis_code", "dx_cd", "icd_code"], "HCC map ICD code")
hcc_code  = pick(hcc_cols, ["hcc_cd", "hcc", "hcc_code", "cms_hcc"], "HCC map HCC code")
mbr_id    = pick(mbr_cols, ["member_id", "mbr_id", "indv_id"], "membership member id")
mbr_cnty  = pick(mbr_cols, ["county_nm", "county", "county_cd", "county_fips", "cnty_nm", "cnty_cd"], "membership county")
mbr_zip   = pick(mbr_cols, ["zip_cd", "zip_code", "zip", "postal_cd"], "membership zip")
mbr_state = pick(mbr_cols, ["state_cd", "state", "st_cd"], "membership state")
mbr_age   = pick(mbr_cols, ["age_nbr", "age", "dob", "birth_dt", "date_of_birth"], "membership age/DOB")
mbr_month = pick(mbr_cols, ["month", "mbr_month", "snapshot_month", "yr_mo", "month_dt", "eff_month", "period"], "membership month key")
mbr_plan  = pick(mbr_cols, ["prod_type", "plan_type", "product", "lob"], "membership plan/product")
ffs_state = pick(ffs_cols, ["rndrng_prvdr_state_abrvtn", "state"], "FFS state")
ffs_age   = pick(ffs_cols, ["bene_age", "bene_avg_age", "age_band", "bene_age_lt_65_cnt"], "FFS age column")
w()

# ---------------------------------------------------------------- Q1 LOB
w("## Q1 · LOB values in claims")
w()
if lob_col:
    run("LOB distribution",
        f"SELECT {lob_col} AS lob, COUNT(*) AS claim_lines, COUNT(DISTINCT member_id) AS members "
        f"FROM `{CLAIMS}` GROUP BY 1 ORDER BY 2 DESC")
    w("Outcome key: exactly CP+MA = assumption holds; more values = list needs a mapping decision; "
      "no LOB column = fall back to membership-presence split.")
else:
    w("No LOB-like column found. Total-vs-MA split falls back to the membership-presence method.")
w()

# ---------------------------------------------------------------- Q2 membership
w("## Q2 · mdcr_base_membership contents")
w()
if mbr_month:
    run("2b monthly grain",
        f"SELECT {mbr_month} AS month_key, COUNT(*) AS row_count, "
        f"COUNT(DISTINCT {mbr_id or 'member_id'}) AS members "
        f"FROM `{MBR}` GROUP BY 1 ORDER BY 1", max_rows=30)
else:
    run("2b grain fallback (total rows vs distinct members)",
        f"SELECT COUNT(*) AS row_count, COUNT(DISTINCT {mbr_id or 'member_id'}) AS members FROM `{MBR}`")

geo = mbr_cnty or mbr_zip
if geo and mbr_state:
    run("2c county/zip coverage by state",
        f"SELECT {mbr_state} AS state, COUNT(DISTINCT {geo}) AS geo_values, "
        f"COUNT(DISTINCT {mbr_id or 'member_id'}) AS members "
        f"FROM `{MBR}` GROUP BY 1 ORDER BY 3 DESC", max_rows=60)
elif geo:
    run("2c geo coverage (no state column)",
        f"SELECT COUNT(DISTINCT {geo}) AS geo_values FROM `{MBR}`")
else:
    w("2c: no county or zip column resolved. MA demand denominator only buildable at state level "
      "(or not at all if no state column). LOUD FLAG.")
    w()

null_checks = [c for c in (mbr_id, geo, mbr_age, mbr_month, mbr_plan) if c]
if null_checks:
    exprs = ", ".join(f"COUNTIF({c} IS NULL) AS null_{c}" for c in null_checks)
    run("2d null rates", f"SELECT COUNT(*) AS total_rows, {exprs} FROM `{MBR}`")
if mbr_plan:
    run("2e plan/product values",
        f"SELECT {mbr_plan} AS plan_value, COUNT(DISTINCT {mbr_id or 'member_id'}) AS members "
        f"FROM `{MBR}` GROUP BY 1 ORDER BY 2 DESC", max_rows=30)
w("Outcome key: member+county+age+month present with low nulls = clean denominator; "
  "county missing = state-level MA demand only; age missing = age from claims only.")
w()

# ---------------------------------------------------------------- Q3 visits
w("## Q3 · Visit definition (claim lines -> visits)")
w()
if date_col:
    run("3a lines per member x provider x day",
        f"SELECT ROUND(AVG(lines),1) AS avg_lines, APPROX_QUANTILES(lines, 4) AS quartiles, "
        f"MAX(lines) AS max_lines FROM ("
        f"  SELECT member_id, srv_prvdr_id, {date_col}, COUNT(*) AS lines"
        f"  FROM `{CLAIMS}` GROUP BY 1,2,3)")
    run("3b scale comparison",
        f"SELECT COUNT(*) AS claim_lines, COUNT(DISTINCT claim_line_id) AS distinct_claim_lines, "
        f"COUNT(DISTINCT CONCAT(member_id,'|',CAST(srv_prvdr_id AS STRING),'|',CAST({date_col} AS STRING))) "
        f"AS member_provider_day_visits FROM `{CLAIMS}`")
    w("Outcome key: avg lines near 1 = claim-line counting is fine as visits; "
      "well above 1 = member x provider x day is the visit key.")
else:
    run("3b scale comparison (no date column -- month/day grain not derivable)",
        f"SELECT COUNT(*) AS claim_lines, COUNT(DISTINCT claim_line_id) AS distinct_claim_lines, "
        f"COUNT(DISTINCT CONCAT(member_id,'|',CAST(srv_prvdr_id AS STRING))) AS member_provider_pairs "
        f"FROM `{CLAIMS}`")
    w("No service-date column resolved: day-grain visits not derivable. LOUD FLAG.")
w()

# ---------------------------------------------------------------- Q4 morbidity
w("## Q4 · Morbidity distribution (HCC per member)")
w()
if hcc_icd and hcc_code:
    run("HCC count distribution",
        f"WITH member_hcc AS ("
        f"  SELECT c.member_id, COUNT(DISTINCT h.{hcc_code}) AS hcc_count"
        f"  FROM `{CLAIMS}` c JOIN `{HCC}` h ON c.pri_icd9_dx_cd = h.{hcc_icd}"
        f"  GROUP BY c.member_id)"
        f"SELECT hcc_count, COUNT(*) AS members FROM member_hcc GROUP BY 1 ORDER BY 1", max_rows=40)
    run("Members with zero HCC join hits",
        f"SELECT (SELECT COUNT(DISTINCT member_id) FROM `{CLAIMS}`) - "
        f"(SELECT COUNT(DISTINCT c.member_id) FROM `{CLAIMS}` c "
        f" JOIN `{HCC}` h ON c.pri_icd9_dx_cd = h.{hcc_icd}) AS members_no_hcc")
    w("Outcome key: report where natural cuts sit for 2 vs 3 morbidity levels. "
      "Distribution only; the cut is decided in the module doc.")
else:
    w("HCC join columns not resolved from schema. Record real column names from Q0 and rerun.")
w()

# ---------------------------------------------------------------- Q5 thin cells
w("## Q5 · Thin cells (state x specialty x age band)")
w()
run("Cell-size buckets by state",
    f"WITH cells AS ("
    f"  SELECT LEFT(prvdr_submarket,2) AS state_cd, specialty_ctg_cd, {AGE_CASE} AS age_band,"
    f"         COUNT(DISTINCT member_id) AS members"
    f"  FROM `{CLAIMS}` WHERE age_nbr >= 60 GROUP BY 1,2,3)"
    f"SELECT state_cd,"
    f"       COUNTIF(members < 30)  AS cells_lt_30,"
    f"       COUNTIF(members >= 30 AND members < 100) AS cells_30_99,"
    f"       COUNTIF(members >= 100) AS cells_gte_100,"
    f"       COUNT(*) AS total_cells"
    f" FROM cells GROUP BY 1 ORDER BY 1")
run("20 smallest AZ cells",
    f"SELECT LEFT(prvdr_submarket,2) AS state_cd, specialty_ctg_cd, {AGE_CASE} AS age_band,"
    f"       COUNT(DISTINCT member_id) AS members"
    f" FROM `{CLAIMS}` WHERE age_nbr >= 60 AND LEFT(prvdr_submarket,2) = 'AZ'"
    f" GROUP BY 1,2,3 ORDER BY members ASC LIMIT 20")
w("Note: age-only cells; adding morbidity cuts each ~2-3x further. "
  "Outcome key: most AZ cells >= 100 = per-state rates safe; many < 30 = national fallback needed for AZ.")
w()

# ---------------------------------------------------------------- Q6 FFS
w("## Q6 · FFS feasibility (total-Medicare rate)")
w()
age_like = [c for c in ffs_cols if "age" in c.lower()]
w(f"FFS columns containing 'age': {age_like if age_like else 'NONE'}")
w()
if ffs_state:
    run("FFS coverage by scope state",
        f"SELECT {ffs_state} AS state, COUNT(*) AS provider_rows, "
        f"SUM(SAFE_CAST(tot_benes AS INT64)) AS total_benes, "
        f"SUM(SAFE_CAST(tot_srvcs AS INT64)) AS total_srvcs "
        f"FROM `{FFS}` WHERE {ffs_state} IN ('FL','OH','AZ','IL') GROUP BY 1 ORDER BY 1")
w("Outcome key: age columns exist = total-Medicare age-banded rate feasible; "
  "none = MA-proxy path runs, T3 rows labeled MA_PROXY. Flag any state with low provider_rows.")
w()

# ---------------------------------------------------------------- Q7 capacity
w("## Q7 · Capacity distributions")
w()
run("7a provider annual volume percentiles per specialty (top 15 by provider count)",
    f"SELECT specialty_ctg_cd, COUNT(DISTINCT srv_prvdr_id) AS providers,"
    f"       APPROX_QUANTILES(visits, 100)[OFFSET(25)] AS p25,"
    f"       APPROX_QUANTILES(visits, 100)[OFFSET(50)] AS p50,"
    f"       APPROX_QUANTILES(visits, 100)[OFFSET(75)] AS p75,"
    f"       APPROX_QUANTILES(visits, 100)[OFFSET(95)] AS p95"
    f" FROM (SELECT specialty_ctg_cd, srv_prvdr_id, COUNT(DISTINCT claim_line_id) AS visits"
    f"       FROM `{CLAIMS}` GROUP BY 1,2)"
    f" GROUP BY 1 ORDER BY providers DESC LIMIT 15")
run("7b senior-load distribution from par_flag (top 15 by provider count)",
    f"SELECT state_cd, cms_specialty, COUNT(DISTINCT provider_id) AS providers,"
    f"       APPROX_QUANTILES(tot_benes, 100)[OFFSET(50)] AS p50_benes,"
    f"       APPROX_QUANTILES(tot_benes, 100)[OFFSET(90)] AS p90_benes,"
    f"       COUNTIF(tot_benes = 0) AS zero_benes_providers,"
    f"       COUNTIF(aetna_par_flag = 1) AS active_providers"
    f" FROM `{PAR}` GROUP BY 1,2 ORDER BY providers DESC LIMIT 15")
w("Outcome key: flag specialties with tiny p50 volume (volume-percentile capacity constant will "
  "not work there) and cells where zero_benes_providers dominates (FFS match coverage problem).")
w()

# ---------------------------------------------------------------- Q8 manual
w("## Q8 · County risk score source (MANUAL TASK — not run by this script)")
w()
w("1. Download the CMS Geographic Variation Public Use File, county level, latest year, from "
  "data.cms.gov (search: 'Medicare Geographic Variation - by National, State & County').")
w("2. Confirm it contains county FIPS (or state+county) and an average HCC risk score column.")
w("3. Record: exact file name, year, risk-score column name, county row counts for FL/OH/AZ/IL "
  "(expect 67/88/15/102; report actual).")
w("4. Save the file to Expanded_scope_medicare/. Do NOT load to BigQuery yet.")
w("5. If not found, record exactly what was searched and found instead.")
w()

path = cfg.repo_path("expanded_scope", "eda_findings.md")
with open(path, "w") as f:
    f.write("\n".join(out_lines))
print(f"wrote {path}")
