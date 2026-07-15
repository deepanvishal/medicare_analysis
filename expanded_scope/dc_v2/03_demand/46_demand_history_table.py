"""
46 - demand history table   [PYTHON runner / BigQuery DDL]

WHAT  : Builds the two demand-side history tables. Visits attributed to the
        MEMBER's county (mbr_county_cd - never prvdr_county). A visit = one
        distinct member_id x epdb_dw_prvdr_id x srv_start_dt. Footprint
        filter: member county in FL/OH/AZ/IL via ref_county (see FOOTPRINT
        below). Months in 2023 are lookback
        memory only (new-patient 12m window, chronic 24m window).
        New patient in month M = the member x provider pair has no visit in
        the 12 months before M. Dx join:
        UPPER(REPLACE(TRIM(pri_icd9_dx_cd), '.', '')) =
        UPPER(TRIM(diagnosis_code)); mapped means HCC_v24 IS NOT NULL.
SCOPE : CP and ME members aged 60+; under-60 members and their claims are
        excluded from every number in this table.
FOOTPRINT: member county restricted to FL/OH/AZ/IL via ref_county; submarket
        no longer used as the scope filter.
GRAIN : dc2_demand_base    -> mbr_county_cd x specialty_ctg_cd x month
                              (month DATE, first of month; 2024-2025)
        dc2_demand_chronic -> mbr_county_cd x month x HCC_v24 (2024-2025)
INPUTS: anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_2025_claims
        anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_membership
        anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.HCC_ICD_Mapping_2025
OUTPUT: dc2_demand_base + dc2_demand_chronic (BigQuery tables) with
        row-count sanity checks printed to stdout. No files written.
Run   : python expanded_scope/dc_v2/03_demand/46_demand_history_table.py
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
CTY    = cfg.table("ref_county")

FOOTPRINT_JOIN = "LPAD(TRIM(CAST({alias}.mbr_county_cd AS STRING)), 5, '0') = rc.county_fips"
FOOTPRINT_STATES = "('FL', 'OH', 'AZ', 'IL')"

OUT_BASE    = cfg.src("dc2_demand_base")
OUT_CHRONIC = cfg.src("dc2_demand_chronic")

VISIT_KEY = ("CONCAT(CAST(c.member_id AS STRING), '|', CAST(c.epdb_dw_prvdr_id AS STRING), "
             "'|', CAST(c.srv_start_dt AS STRING))")

MEMBER_COUNTS = f"""
  SELECT
    m.mbr_county_cd,
    DATE(CAST(m.eff_yr AS INT64), CAST(m.eff_mo AS INT64), 1) AS month,
    COUNT(DISTINCT m.member_id)                                        AS members,
    COUNT(DISTINCT IF(m.age_nbr BETWEEN 60 AND 64, m.member_id, NULL)) AS mbr_age_60_64,
    COUNT(DISTINCT IF(m.age_nbr BETWEEN 65 AND 74, m.member_id, NULL)) AS mbr_age_65_74,
    COUNT(DISTINCT IF(m.age_nbr BETWEEN 75 AND 84, m.member_id, NULL)) AS mbr_age_75_84,
    COUNT(DISTINCT IF(m.age_nbr >= 85, m.member_id, NULL))             AS mbr_age_85p
  FROM `{MBRSHP}` m
  JOIN `{CTY}` rc
    ON {FOOTPRINT_JOIN.format(alias='m')}
  WHERE rc.state_cd IN {FOOTPRINT_STATES}
    AND m.age_nbr >= 60
    AND CAST(m.eff_yr AS INT64) IN (2024, 2025)
  GROUP BY 1, 2
"""

DDL_BASE = f"""
CREATE OR REPLACE TABLE `{OUT_BASE}`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
WITH claims_f AS (
  SELECT
    c.member_id,
    c.epdb_dw_prvdr_id,
    c.srv_start_dt,
    DATE_TRUNC(c.srv_start_dt, MONTH) AS month,
    c.mbr_county_cd,
    c.specialty_ctg_cd
  FROM `{CLAIMS}` c
  JOIN `{CTY}` rc
    ON {FOOTPRINT_JOIN.format(alias='c')}
  WHERE rc.state_cd IN {FOOTPRINT_STATES}
    AND c.age_nbr >= 60
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
    c.mbr_county_cd,
    c.specialty_ctg_cd,
    c.month,
    COUNT(DISTINCT {VISIT_KEY}) AS visits,
    COUNT(DISTINCT IF(pn.is_new, {VISIT_KEY}, NULL)) AS new_visits
  FROM claims_f c
  JOIN pair_new pn
    ON c.member_id = pn.member_id
    AND c.epdb_dw_prvdr_id = pn.epdb_dw_prvdr_id
    AND c.month = pn.month
  WHERE EXTRACT(YEAR FROM c.month) IN (2024, 2025)
  GROUP BY 1, 2, 3
),
targets AS (
  SELECT
    b.mbr_county_cd,
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
    ON b.mbr_county_cd = f.mbr_county_cd
    AND b.specialty_ctg_cd = f.specialty_ctg_cd
    AND f.month > b.month
    AND f.month <= DATE_ADD(b.month, INTERVAL 12 MONTH)
  GROUP BY 1, 2, 3
),
member_counts AS (
{MEMBER_COUNTS}
)
SELECT
  v.mbr_county_cd,
  v.specialty_ctg_cd,
  v.month,
  v.visits,
  t.target_next_1m,
  t.target_next_12m,
  m.members,
  m.mbr_age_60_64,
  m.mbr_age_65_74,
  m.mbr_age_75_84,
  m.mbr_age_85p,
  SAFE_DIVIDE(v.new_visits, v.visits) AS pct_new_patients,
  EXTRACT(MONTH FROM v.month) AS month_of_year,
  EXTRACT(YEAR FROM v.month) AS year,
  DATE_DIFF(v.month, DATE '2024-01-01', MONTH) + 1 AS month_index
FROM cell_visits v
JOIN targets t
  ON v.mbr_county_cd = t.mbr_county_cd
  AND v.specialty_ctg_cd = t.specialty_ctg_cd
  AND v.month = t.month
LEFT JOIN member_counts m
  ON v.mbr_county_cd = m.mbr_county_cd
  AND v.month = m.month
"""

DDL_CHRONIC = f"""
CREATE OR REPLACE TABLE `{OUT_CHRONIC}`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
WITH mapped_claims AS (
  SELECT DISTINCT
    c.member_id,
    c.mbr_county_cd,
    DATE_TRUNC(c.srv_start_dt, MONTH) AS claim_month,
    h.HCC_v24
  FROM `{CLAIMS}` c
  JOIN `{CTY}` rc
    ON {FOOTPRINT_JOIN.format(alias='c')}
  JOIN `{MAP}` h
    ON UPPER(REPLACE(TRIM(c.pri_icd9_dx_cd), '.', '')) = UPPER(TRIM(h.diagnosis_code))
  WHERE rc.state_cd IN {FOOTPRINT_STATES}
    AND c.age_nbr >= 60
    AND h.HCC_v24 IS NOT NULL
),
months AS (
  SELECT month
  FROM UNNEST(GENERATE_DATE_ARRAY(DATE '2024-01-01', DATE '2025-12-01', INTERVAL 1 MONTH)) AS month
),
numer AS (
  SELECT
    mc.mbr_county_cd,
    m.month,
    mc.HCC_v24,
    COUNT(DISTINCT mc.member_id) AS members_with_hcc
  FROM months m
  JOIN mapped_claims mc
    ON mc.claim_month <= m.month
    AND mc.claim_month >= DATE_SUB(m.month, INTERVAL 23 MONTH)
  GROUP BY 1, 2, 3
),
member_counts AS (
{MEMBER_COUNTS}
)
SELECT
  n.mbr_county_cd,
  n.month,
  n.HCC_v24,
  n.members_with_hcc,
  mc.members,
  ROUND(SAFE_DIVIDE(n.members_with_hcc, mc.members), 4) AS prevalence
FROM numer n
LEFT JOIN member_counts mc
  ON n.mbr_county_cd = mc.mbr_county_cd
  AND n.month = mc.month
"""

CHECKS_BASE = {
    "row count dc2_demand_base":
        f"SELECT COUNT(*) AS row_count FROM `{OUT_BASE}`",
    "total visits 2024 in table 1":
        f"SELECT SUM(visits) AS table_visits_2024 FROM `{OUT_BASE}` WHERE year = 2024",
    "distinct-visit count 2024 direct from claims (must match line above)":
        f"SELECT COUNT(DISTINCT CONCAT(CAST(c.mbr_county_cd AS STRING), '|', "
        f"CAST(c.specialty_ctg_cd AS STRING), '|', CAST(c.member_id AS STRING), '|', "
        f"CAST(c.epdb_dw_prvdr_id AS STRING), '|', CAST(c.srv_start_dt AS STRING))) "
        f"AS direct_visits_2024 FROM `{CLAIMS}` c "
        f"JOIN `{CTY}` rc ON {FOOTPRINT_JOIN.format(alias='c')} "
        f"WHERE rc.state_cd IN {FOOTPRINT_STATES} AND c.age_nbr >= 60 "
        f"AND EXTRACT(YEAR FROM c.srv_start_dt) = 2024",
    "cells with pct_new_patients > 1 (must be 0)":
        f"SELECT COUNT(*) AS bad_cells FROM `{OUT_BASE}` WHERE pct_new_patients > 1",
}

CHECKS_CHRONIC = {
    "row count dc2_demand_chronic":
        f"SELECT COUNT(*) AS row_count FROM `{OUT_CHRONIC}`",
}


def main():
    cfg.run_ddl(DDL_BASE, CHECKS_BASE)
    cfg.run_ddl(DDL_CHRONIC, CHECKS_CHRONIC)


if __name__ == "__main__":
    main()
