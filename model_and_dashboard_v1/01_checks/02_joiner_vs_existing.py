"""
02 - joiner vs existing   [PYTHON / read-only BigQuery checks]

WHAT  : Are first-year members sicker or healthier than tenured members,
        same age bands? Output decides one sickness-rate table vs two.
        Tenure flag from membership: a member's first-ever eff month within
        2023-2025; JOINER = first month within the last 12 months of the
        comparison year 2025 (first month in 2025); TENURED = first month
        at least 24 months before (first month before 2024-01). This is an
        approximation bounded by the data window: LEFT-CENSORING means a
        member enrolled since 2019 looks identical to one enrolled since
        2023-01. Members first seen during 2024 sit in neither group.
        Conditions for 2025 come from claims diagnoses via
        HCC_ICD_Mapping_2025 with the ICD cleaning rule
        (UPPER(REPLACE(TRIM(code), '.', '')) both sides); condition =
        HCC_v24 value.
SCOPE : R6 restated in every query: age_nbr >= 60; business_ln_cd IN
        ('CP','ME') on claims (membership carries no LOB column, so age and
        footprint only there); member county in FL/OH/AZ/IL via
        ms_ref_county with LPAD defense on the county code.
GRAIN : stdout report only; no tables created.
INPUTS: anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_2025_claims
        anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_membership
        anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.HCC_ICD_Mapping_2025
        cfg.table("ref_county")
OUTPUT: stdout report only.
Run   : python model_and_dashboard_v1/01_checks/02_joiner_vs_existing.py
        Runnable in any order; 00 first is recommended. Each takes only
        BigQuery read access.
"""

import os
import sys


def _expanded_scope_dir():
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        repo = os.path.dirname(os.path.dirname(here))
        return os.path.join(repo, "expanded_scope")
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
HCC    = "anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.HCC_ICD_Mapping_2025"
CTY    = cfg.table("ref_county")

FOOTPRINT = "('FL', 'OH', 'AZ', 'IL')"

COHORT_CTES = f"""
    fp AS (
      SELECT county_fips FROM `{CTY}` WHERE state_cd IN {FOOTPRINT}
    ),
    mbr_2025 AS (
      SELECT m.member_id, MAX(m.age_nbr) AS age_nbr
      FROM `{MBRSHP}` m
      JOIN fp ON LPAD(TRIM(CAST(m.mbr_county_cd AS STRING)), 5, '0') = fp.county_fips
      WHERE CAST(m.eff_yr AS INT64) = 2025
        AND m.age_nbr >= 60
      GROUP BY m.member_id
    ),
    first_month AS (
      SELECT member_id,
             MIN(DATE(CAST(eff_yr AS INT64), CAST(eff_mo AS INT64), 1)) AS first_mo
      FROM `{MBRSHP}`
      WHERE CAST(eff_yr AS INT64) BETWEEN 2023 AND 2025
      GROUP BY member_id
    ),
    cohort AS (
      SELECT
        b.member_id,
        CASE WHEN b.age_nbr BETWEEN 60 AND 64 THEN '60-64'
             WHEN b.age_nbr BETWEEN 65 AND 74 THEN '65-74'
             WHEN b.age_nbr BETWEEN 75 AND 84 THEN '75-84'
             ELSE '85+' END AS age_band,
        CASE WHEN f.first_mo >= DATE '2025-01-01' THEN 'JOINER'
             WHEN f.first_mo < DATE '2024-01-01' THEN 'TENURED' END AS tenure_group
      FROM mbr_2025 b
      JOIN first_month f ON b.member_id = f.member_id
      WHERE (f.first_mo >= DATE '2025-01-01' OR f.first_mo < DATE '2024-01-01')
    ),
    member_hcc AS (
      SELECT c.member_id, h.HCC_v24
      FROM `{CLAIMS}` c
      JOIN fp ON LPAD(TRIM(CAST(c.mbr_county_cd AS STRING)), 5, '0') = fp.county_fips
      JOIN `{HCC}` h
        ON UPPER(REPLACE(TRIM(c.pri_icd9_dx_cd), '.', '')) = UPPER(TRIM(h.diagnosis_code))
      WHERE EXTRACT(YEAR FROM c.srv_start_dt) = 2025
        AND c.age_nbr >= 60
        AND c.business_ln_cd IN ('CP', 'ME')
        AND h.HCC_v24 IS NOT NULL
      GROUP BY c.member_id, h.HCC_v24
    )
"""

SQL_BANDS = f"""
    WITH {COHORT_CTES},
    per_member AS (
      SELECT member_id, COUNT(DISTINCT HCC_v24) AS n_conditions
      FROM member_hcc GROUP BY member_id
    )
    SELECT
      co.age_band,
      co.tenure_group,
      COUNT(*) AS members,
      SAFE_DIVIDE(COUNTIF(COALESCE(pm.n_conditions, 0) > 0), COUNT(*)) AS share_any_condition,
      SAFE_DIVIDE(SUM(COALESCE(pm.n_conditions, 0)), COUNT(*)) AS mean_conditions
    FROM cohort co
    LEFT JOIN per_member pm ON co.member_id = pm.member_id
    GROUP BY co.age_band, co.tenure_group
    ORDER BY co.age_band, co.tenure_group
"""

SQL_TOP10_7584 = f"""
    WITH {COHORT_CTES},
    band AS (
      SELECT member_id, tenure_group FROM cohort WHERE age_band = '75-84'
    ),
    group_sizes AS (
      SELECT tenure_group, COUNT(*) AS group_members FROM band GROUP BY tenure_group
    )
    SELECT
      b.tenure_group,
      mh.HCC_v24,
      COUNT(DISTINCT b.member_id) AS members_with_hcc,
      SAFE_DIVIDE(COUNT(DISTINCT b.member_id), ANY_VALUE(gs.group_members)) AS prevalence
    FROM band b
    JOIN member_hcc mh ON b.member_id = mh.member_id
    JOIN group_sizes gs ON b.tenure_group = gs.tenure_group
    GROUP BY b.tenure_group, mh.HCC_v24
    QUALIFY ROW_NUMBER() OVER (PARTITION BY b.tenure_group ORDER BY prevalence DESC) <= 10
    ORDER BY b.tenure_group, prevalence DESC
"""

BANDS = ["60-64", "65-74", "75-84", "85+"]


def main():
    client = cfg.client()

    print("=== per band x tenure group: members, share with any condition, "
          "mean conditions ===")
    rows = [dict(r) for r in client.query(SQL_BANDS).result()]
    for r in rows:
        print(f"  {r['age_band']:>5}  {r['tenure_group']:<8}  "
              f"members={r['members']:,}  "
              f"share_any={float(r['share_any_condition']):.4f}  "
              f"mean_conditions={float(r['mean_conditions']):.4f}")

    print("\n=== top 10 HCCs by prevalence, 75-84 band, joiners vs tenured ===")
    for r in client.query(SQL_TOP10_7584).result():
        d = dict(r)
        print(f"  {d['tenure_group']:<8}  HCC {d['HCC_v24']:<6}  "
              f"members={d['members_with_hcc']:,}  "
              f"prevalence={float(d['prevalence']):.4f}")

    stats = {(r["age_band"], r["tenure_group"]): r for r in rows}
    print("\n=== verdict: joiner-vs-tenured prevalence ratio per band ===")
    ratios = {}
    for band in BANDS:
        joiner = stats.get((band, "JOINER"))
        tenured = stats.get((band, "TENURED"))
        j = float(joiner["share_any_condition"]) if joiner else 0.0
        t = float(tenured["share_any_condition"]) if tenured else 0.0
        ratio = j / t if t else None
        ratios[band] = ratio
        print(f"  {band:>5}: ratio = {ratio:.4f}" if ratio is not None
              else f"  {band:>5}: ratio = undefined (empty tenured group)")
    similar = all(r is not None and 0.9 <= r <= 1.1 for r in ratios.values())
    print(f"\nVERDICT: {'SIMILAR' if similar else 'DIFFERENT'} "
          f"(SIMILAR = every band ratio within 0.9-1.1; SIMILAR supports one "
          f"sickness-rate table, DIFFERENT supports two)")

    for band in BANDS:
        for group in ("JOINER", "TENURED"):
            r = stats.get((band, group))
            n = r["members"] if r else 0
            assert n >= 10_000, (
                f"GATE FAILED (R2): band {band} group {group} has {n:,} members; "
                f"at least 10,000 required to trust the comparison")
    print("\nALL GATES PASSED (R2 group volumes)")


if __name__ == "__main__":
    main()
