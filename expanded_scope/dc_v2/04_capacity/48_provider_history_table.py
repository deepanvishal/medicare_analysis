"""
48 - provider history table   [PYTHON runner / BigQuery DDL]

WHAT  : Builds the two capacity-side history tables. Visits attributed to the
        PROVIDER's county (prvdr_county / prvdr_submarket - never
        mbr_county_cd). A visit = one distinct member_id x epdb_dw_prvdr_id
        x srv_start_dt. Footprint filter: prvdr_submarket IS NOT NULL.
        Months in 2023 are lookback memory only (new-patient 12m window,
        panel 12m window, chronic 24m window, tenure). Dx join:
        UPPER(REPLACE(TRIM(pri_icd9_dx_cd), '.', '')) =
        UPPER(TRIM(diagnosis_code)); mapped means HCC_v24 IS NOT NULL.
SCOPE : CP and ME members aged 60+; under-60 members and their claims are
        excluded from every number in this table.
GRAIN : dc2_capacity_provider -> epdb_dw_prvdr_id x specialty_ctg_cd x month
                                 (month DATE, first of month; 2024-2025)
        dc2_capacity_county   -> prvdr_county x specialty_ctg_cd x month
                                 (rollup of the provider table; 2024-2025)
INPUTS: anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_2025_claims
        anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_membership
        anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.HCC_ICD_Mapping_2025
OUTPUT: dc2_capacity_provider + dc2_capacity_county (BigQuery tables) with
        sanity checks printed to stdout. No files written.
Run   : python expanded_scope/dc_v2/04_capacity/48_provider_history_table.py
"""

import os
import sys


def _expanded_scope_dir():
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        return os.path.dirname(os.path.dirname(here))
    except NameError:
        probe = os.getcwd()
        for _ in range(6):
            if os.path.isfile(os.path.join(probe, "config.py")):
                return probe
            cand = os.path.join(probe, "expanded_scope")
            if os.path.isfile(os.path.join(cand, "config.py")):
                return cand
            probe = os.path.dirname(probe)
        raise FileNotFoundError(
            "config.py not found - run from the repo root or any folder inside it")


sys.path.insert(0, _expanded_scope_dir())
import config as cfg

CLAIMS = "anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_2025_claims"
MBRSHP = "anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_membership"
MAP    = "anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.HCC_ICD_Mapping_2025"

OUT_PROVIDER = cfg.src("dc2_capacity_provider")
OUT_COUNTY   = cfg.src("dc2_capacity_county")
DEMAND_BASE  = cfg.src("dc2_demand_base")

VISIT_KEY = ("CONCAT(CAST(c.member_id AS STRING), '|', CAST(c.epdb_dw_prvdr_id AS STRING), "
             "'|', CAST(c.srv_start_dt AS STRING))")

DDL_PROVIDER = f"""
CREATE OR REPLACE TABLE `{OUT_PROVIDER}`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
WITH claims_f AS (
  SELECT
    member_id,
    epdb_dw_prvdr_id,
    srv_start_dt,
    DATE_TRUNC(srv_start_dt, MONTH) AS month,
    specialty_ctg_cd,
    prvdr_county,
    mbr_county_cd,
    age_nbr,
    pri_icd9_dx_cd
  FROM `{CLAIMS}`
  WHERE prvdr_submarket IS NOT NULL
    AND age_nbr >= 60
),
pair_months AS (
  SELECT DISTINCT member_id, epdb_dw_prvdr_id, month
  FROM claims_f
),
pair_new AS (
  SELECT
    member_id,
    epdb_dw_prvdr_id,
    month,
    COALESCE(
      LAG(month) OVER (PARTITION BY member_id, epdb_dw_prvdr_id ORDER BY month)
        < DATE_SUB(month, INTERVAL 12 MONTH),
      TRUE) AS is_new
  FROM pair_months
),
cell_visits AS (
  SELECT
    c.epdb_dw_prvdr_id,
    c.specialty_ctg_cd,
    c.month,
    COUNT(DISTINCT {VISIT_KEY}) AS visits,
    COUNT(DISTINCT IF(pn.is_new, {VISIT_KEY}, NULL)) AS new_visits,
    COUNT(DISTINCT c.mbr_county_cd) AS distinct_mbr_counties
  FROM claims_f c
  JOIN pair_new pn
    ON c.member_id = pn.member_id
    AND c.epdb_dw_prvdr_id = pn.epdb_dw_prvdr_id
    AND c.month = pn.month
  WHERE EXTRACT(YEAR FROM c.month) IN (2024, 2025)
  GROUP BY 1, 2, 3
),
county_pick AS (
  SELECT epdb_dw_prvdr_id, month, prvdr_county
  FROM (
    SELECT epdb_dw_prvdr_id, month, prvdr_county, COUNT(*) AS n
    FROM claims_f
    GROUP BY 1, 2, 3
  )
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY epdb_dw_prvdr_id, month
    ORDER BY n DESC, prvdr_county ASC) = 1
),
targets AS (
  SELECT
    b.epdb_dw_prvdr_id,
    b.specialty_ctg_cd,
    b.month,
    CASE WHEN DATE_ADD(b.month, INTERVAL 1 MONTH) <= DATE '2025-12-01'
         THEN COALESCE(SUM(IF(f.month = DATE_ADD(b.month, INTERVAL 1 MONTH), f.visits, 0)), 0)
         ELSE NULL END AS target_next_1m,
    CASE WHEN DATE_ADD(b.month, INTERVAL 12 MONTH) <= DATE '2025-12-01'
         THEN COALESCE(SUM(IF(f.month BETWEEN DATE_ADD(b.month, INTERVAL 1 MONTH)
                                          AND DATE_ADD(b.month, INTERVAL 12 MONTH), f.visits, 0)), 0)
         ELSE NULL END AS target_next_12m
  FROM cell_visits b
  LEFT JOIN cell_visits f
    ON b.epdb_dw_prvdr_id = f.epdb_dw_prvdr_id
    AND b.specialty_ctg_cd = f.specialty_ctg_cd
    AND f.month > b.month
    AND f.month <= DATE_ADD(b.month, INTERVAL 12 MONTH)
  GROUP BY 1, 2, 3
),
months AS (
  SELECT month
  FROM UNNEST(GENERATE_DATE_ARRAY(DATE '2024-01-01', DATE '2025-12-01', INTERVAL 1 MONTH)) AS month
),
mbr_prvdr AS (
  SELECT epdb_dw_prvdr_id, member_id, month AS claim_month, MAX(age_nbr) AS age_m
  FROM claims_f
  GROUP BY 1, 2, 3
),
panel_base AS (
  SELECT
    m.month,
    v.epdb_dw_prvdr_id,
    v.member_id,
    MAX(v.age_m) AS age_w
  FROM months m
  JOIN mbr_prvdr v
    ON v.claim_month BETWEEN DATE_SUB(m.month, INTERVAL 11 MONTH) AND m.month
  GROUP BY 1, 2, 3
),
member_mapped AS (
  SELECT DISTINCT c.member_id, c.month AS claim_month
  FROM claims_f c
  JOIN `{MAP}` h
    ON UPPER(REPLACE(TRIM(c.pri_icd9_dx_cd), '.', '')) = UPPER(TRIM(h.diagnosis_code))
  WHERE h.HCC_v24 IS NOT NULL
),
chronic_members AS (
  SELECT DISTINCT m.month, mm.member_id
  FROM months m
  JOIN member_mapped mm
    ON mm.claim_month BETWEEN DATE_SUB(m.month, INTERVAL 23 MONTH) AND m.month
),
panel_agg AS (
  SELECT
    pb.month,
    pb.epdb_dw_prvdr_id,
    COUNT(DISTINCT pb.member_id)                                        AS panel_members,
    COUNT(DISTINCT IF(pb.age_w BETWEEN 60 AND 64, pb.member_id, NULL))  AS panel_60_64,
    COUNT(DISTINCT IF(pb.age_w BETWEEN 65 AND 74, pb.member_id, NULL))  AS panel_65_74,
    COUNT(DISTINCT IF(pb.age_w BETWEEN 75 AND 84, pb.member_id, NULL))  AS panel_75_84,
    COUNT(DISTINCT IF(pb.age_w >= 85, pb.member_id, NULL))              AS panel_85p,
    COUNT(DISTINCT IF(ch.member_id IS NOT NULL, pb.member_id, NULL))    AS panel_chronic_members
  FROM panel_base pb
  LEFT JOIN chronic_members ch
    ON ch.month = pb.month AND ch.member_id = pb.member_id
  GROUP BY 1, 2
),
first_claim AS (
  SELECT epdb_dw_prvdr_id, MIN(month) AS first_month
  FROM claims_f
  GROUP BY 1
)
SELECT
  v.epdb_dw_prvdr_id,
  v.specialty_ctg_cd,
  v.month,
  cp.prvdr_county,
  v.visits,
  t.target_next_1m,
  t.target_next_12m,
  p.panel_members,
  p.panel_60_64,
  p.panel_65_74,
  p.panel_75_84,
  p.panel_85p,
  p.panel_chronic_members,
  SAFE_DIVIDE(v.new_visits, v.visits) AS pct_new_patients,
  v.distinct_mbr_counties,
  DATE_DIFF(v.month, fc.first_month, MONTH) AS tenure_months,
  EXTRACT(MONTH FROM v.month) AS month_of_year,
  EXTRACT(YEAR FROM v.month) AS year,
  DATE_DIFF(v.month, DATE '2024-01-01', MONTH) + 1 AS month_index
FROM cell_visits v
JOIN targets t
  ON v.epdb_dw_prvdr_id = t.epdb_dw_prvdr_id
  AND v.specialty_ctg_cd = t.specialty_ctg_cd
  AND v.month = t.month
JOIN county_pick cp
  ON v.epdb_dw_prvdr_id = cp.epdb_dw_prvdr_id
  AND v.month = cp.month
JOIN panel_agg p
  ON v.epdb_dw_prvdr_id = p.epdb_dw_prvdr_id
  AND v.month = p.month
JOIN first_claim fc
  ON v.epdb_dw_prvdr_id = fc.epdb_dw_prvdr_id
"""

DDL_COUNTY = f"""
CREATE OR REPLACE TABLE `{OUT_COUNTY}`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
SELECT
  prvdr_county,
  specialty_ctg_cd,
  month,
  SUM(visits) AS visits,
  CASE WHEN DATE_ADD(month, INTERVAL 1 MONTH) <= DATE '2025-12-01'
       THEN SUM(target_next_1m) ELSE NULL END AS target_next_1m,
  CASE WHEN DATE_ADD(month, INTERVAL 12 MONTH) <= DATE '2025-12-01'
       THEN SUM(target_next_12m) ELSE NULL END AS target_next_12m,
  COUNT(DISTINCT IF(visits > 0, epdb_dw_prvdr_id, NULL)) AS providers,
  SAFE_DIVIDE(SUM(pct_new_patients * visits), SUM(visits)) AS pct_new_patients,
  EXTRACT(MONTH FROM month) AS month_of_year,
  EXTRACT(YEAR FROM month) AS year,
  DATE_DIFF(month, DATE '2024-01-01', MONTH) + 1 AS month_index
FROM `{OUT_PROVIDER}`
GROUP BY 1, 2, 3
"""

CHECKS_PROVIDER = {
    "row count dc2_capacity_provider":
        f"SELECT COUNT(*) AS row_count FROM `{OUT_PROVIDER}`",
    "rows where panel_members < distinct members visiting that month (must be 0)":
        f"WITH monthly AS ("
        f"  SELECT epdb_dw_prvdr_id, DATE_TRUNC(srv_start_dt, MONTH) AS month, "
        f"  COUNT(DISTINCT member_id) AS month_members "
        f"  FROM `{CLAIMS}` WHERE prvdr_submarket IS NOT NULL AND age_nbr >= 60 "
        f"  AND EXTRACT(YEAR FROM srv_start_dt) IN (2024, 2025) GROUP BY 1, 2) "
        f"SELECT COUNT(*) AS bad_rows FROM ("
        f"  SELECT DISTINCT epdb_dw_prvdr_id, month, panel_members FROM `{OUT_PROVIDER}`) t "
        f"JOIN monthly m ON t.epdb_dw_prvdr_id = m.epdb_dw_prvdr_id AND t.month = m.month "
        f"WHERE t.panel_members < m.month_members",
}

CHECKS_COUNTY = {
    "row count dc2_capacity_county":
        f"SELECT COUNT(*) AS row_count FROM `{OUT_COUNTY}`",
    "SUM(visits) 2024: provider table vs county table (must match)":
        f"SELECT "
        f"(SELECT SUM(visits) FROM `{OUT_PROVIDER}` WHERE year = 2024) AS provider_visits_2024, "
        f"(SELECT SUM(visits) FROM `{OUT_COUNTY}` WHERE year = 2024) AS county_visits_2024",
    "SUM(visits) 2024: capacity vs dc2_demand_base -- cross-county + footprint scope difference (expected nonzero)":
        f"SELECT "
        f"(SELECT SUM(visits) FROM `{OUT_PROVIDER}` WHERE year = 2024) AS capacity_visits_2024, "
        f"(SELECT SUM(visits) FROM `{DEMAND_BASE}` WHERE year = 2024) AS demand_visits_2024, "
        f"(SELECT SUM(visits) FROM `{OUT_PROVIDER}` WHERE year = 2024) - "
        f"(SELECT SUM(visits) FROM `{DEMAND_BASE}` WHERE year = 2024) AS scope_difference",
}


def main():
    cfg.run_ddl(DDL_PROVIDER, CHECKS_PROVIDER)
    cfg.run_ddl(DDL_COUNTY, CHECKS_COUNTY)


if __name__ == "__main__":
    main()
