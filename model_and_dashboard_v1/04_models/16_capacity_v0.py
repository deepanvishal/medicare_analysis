"""
16 - capacity v0   [PYTHON runner / BigQuery DDL]

WHAT  : md1_capacity_v0 - the v0 provider capacity table (D14). v0
        ceiling = OBSERVED DATA, NOT A MODEL: per provider, annual
        ceiling = max observed monthly visits (2024-2025) x 12; monthly
        ceiling = that max month. Explicitly labeled v0 on every
        deliverable; replaced by the modeled 16-18 trio in a later
        phase. Providers are routed within state x county x specialty
        by new-patient share: intake_weight = the provider's share of
        2025 new-patient visits within their prvdr_state x
        prvdr_county_clean x cms_specialty cell (weights sum to 1 per
        cell; SAFE_DIVIDE; null when the cell has zero new-patient
        visits - counted). prvdr_state is derived from the modal
        prvdr_submarket's leading two-letter token ("FL South" -> "FL",
        dictionary trap 16); a bare county name pools same-named
        counties across states (trap 25), which the state key prevents.
        Providers with null or unparseable submarket carry prvdr_state
        = 'UNK', are counted in the print, and are excluded from
        routing (intake_weight null; D15 residual).
        cms_specialty comes from md1_ref_specialty_demand joined on the
        provider's modal specialty_ctg_cd (one-to-one post-D12, so no
        fan-out); providers whose modal code is unbridged are excluded
        and counted in the print. headroom_annual_v0 = ceiling_annual_v0
        - visits_2025 (never negative by construction: visits_2025 is a
        sum of 12 months each at most monthly_max).
SCOPE : R6 inherited: md1_visits_base and md1_provider_profile were
        built under the full R6 filter (age 60+, CP/ME, footprint);
        this notebook derives from them without re-widening scope.
R3    : Attribution = PROVIDER county (capacity side): routing cells
        use prvdr_county_clean from md1_provider_profile. Member
        geography never enters.
GRAIN : epdb_dw_prvdr_id.
INPUTS: md1_visits_base, md1_provider_profile (09),
        md1_ref_specialty_demand (05b - never the compliance
        crosswalk, per D12)
OUTPUT: md1_capacity_v0 (BigQuery table).
Run   : python model_and_dashboard_v1/04_models/16_capacity_v0.py
        Run after 05b and 09; independent of 07, 08 and the 10-15
        model notebooks.
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

VISITS  = cfg.src("md1_visits_base")
PROFILE = cfg.src("md1_provider_profile")
DMAP    = cfg.src("md1_ref_specialty_demand")
OUT     = cfg.src("md1_capacity_v0")

DDL = f"""
CREATE OR REPLACE TABLE `{OUT}`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
WITH mapped AS (
  SELECT
    p.epdb_dw_prvdr_id,
    dm.cms_specialty,
    p.prvdr_county_clean,
    CASE
      WHEN REGEXP_CONTAINS(UPPER(TRIM(p.prvdr_submarket)),
                           r'^[A-Z]{{2}}(\\s|$)')
      THEN UPPER(SUBSTR(TRIM(p.prvdr_submarket), 1, 2))
      ELSE 'UNK'
    END AS prvdr_state,
    p.visits_2025,
    p.new_patient_share_2025
  FROM `{PROFILE}` p
  JOIN `{DMAP}` dm
    ON TRIM(CAST(p.specialty_ctg_cd AS STRING)) = TRIM(CAST(dm.aetna_cd AS STRING))
),
monthly AS (
  SELECT epdb_dw_prvdr_id, month, COUNT(*) AS month_visits
  FROM `{VISITS}`
  WHERE EXTRACT(YEAR FROM month) IN (2024, 2025)
  GROUP BY epdb_dw_prvdr_id, month
),
peak AS (
  SELECT epdb_dw_prvdr_id, MAX(month_visits) AS monthly_max
  FROM monthly
  GROUP BY epdb_dw_prvdr_id
),
newpat AS (
  SELECT epdb_dw_prvdr_id, COUNT(*) AS new_visits_2025
  FROM `{VISITS}`
  WHERE is_new_patient
    AND EXTRACT(YEAR FROM month) = 2025
  GROUP BY epdb_dw_prvdr_id
)
SELECT
  m.epdb_dw_prvdr_id,
  m.cms_specialty,
  m.prvdr_county_clean,
  m.prvdr_state,
  m.visits_2025,
  pk.monthly_max,
  pk.monthly_max * 12 AS ceiling_annual_v0,
  pk.monthly_max AS ceiling_monthly_v0,
  m.new_patient_share_2025,
  SAFE_DIVIDE(
    IF(m.prvdr_state = 'UNK', NULL, COALESCE(np.new_visits_2025, 0)),
    SUM(IF(m.prvdr_state = 'UNK', NULL, COALESCE(np.new_visits_2025, 0)))
      OVER (PARTITION BY m.prvdr_state, m.prvdr_county_clean,
            m.cms_specialty))
    AS intake_weight,
  pk.monthly_max * 12 - m.visits_2025 AS headroom_annual_v0
FROM mapped m
JOIN peak pk ON m.epdb_dw_prvdr_id = pk.epdb_dw_prvdr_id
LEFT JOIN newpat np ON m.epdb_dw_prvdr_id = np.epdb_dw_prvdr_id
"""


def q(client, label, sql):
    print(f"\n=== {label} ===")
    rows = [dict(r) for r in client.query(sql).result()]
    for r in rows[:40]:
        print("  ", r)
    if len(rows) > 40:
        print(f"  ... ({len(rows) - 40} more rows)")
    return rows


def main():
    client = cfg.client()
    client.query(DDL).result()
    print(f"table created: {OUT}")

    coverage = q(client, "profile coverage and unbridged exclusions (R7)", f"""
        SELECT
          (SELECT COUNT(*) FROM `{PROFILE}`) AS profile_providers,
          (SELECT COUNT(*) FROM `{OUT}`) AS capacity_providers,
          (SELECT COUNT(DISTINCT p.specialty_ctg_cd)
           FROM `{PROFILE}` p
           LEFT JOIN `{DMAP}` dm
             ON TRIM(CAST(p.specialty_ctg_cd AS STRING))
                = TRIM(CAST(dm.aetna_cd AS STRING))
           WHERE dm.aetna_cd IS NULL) AS unbridged_modal_codes
        """)[0]
    excluded = coverage["profile_providers"] - coverage["capacity_providers"]
    print(f"\nproviders excluded (unbridged modal specialty code): "
          f"{excluded:,} across {coverage['unbridged_modal_codes']} codes "
          f"(v0 capacity covers bridged demand specialties only)")

    keys = q(client, "keys, ceiling floor, null intake weights, UNK state "
                     "(R2)", f"""
        SELECT COUNT(*) AS row_count,
               COUNT(DISTINCT epdb_dw_prvdr_id) AS distinct_keys,
               COUNTIF(epdb_dw_prvdr_id IS NULL) AS null_keys,
               COUNTIF(prvdr_state IS NULL) AS null_state,
               COUNTIF(prvdr_state = 'UNK') AS unk_state_providers,
               STRING_AGG(DISTINCT prvdr_state, ',' ORDER BY prvdr_state)
                 AS distinct_states,
               COUNTIF(ceiling_annual_v0 < visits_2025) AS ceiling_below_actual,
               COUNTIF(intake_weight IS NULL) AS null_intake_weights
        FROM `{OUT}`""")[0]
    print(f"\ndistinct prvdr_state values: {keys['distinct_states']}")
    print(f"providers with prvdr_state = 'UNK' (null or unparseable "
          f"submarket - excluded from routing): "
          f"{keys['unk_state_providers']:,}")
    print(f"null intake_weight rows (UNK state, or cell has zero 2025 "
          f"new-patient visits): {keys['null_intake_weights']:,}")

    weights = q(client, "intake weights sum to 1 per state x county x "
                        "specialty cell (R4)", f"""
        SELECT COUNTIF(ABS(weight_sum - 1) > 0.001) AS bad_cells,
               COUNT(*) AS cells_with_new_visits
        FROM (
          SELECT prvdr_state, prvdr_county_clean, cms_specialty,
                 SUM(intake_weight) AS weight_sum
          FROM `{OUT}`
          WHERE intake_weight IS NOT NULL
          GROUP BY prvdr_state, prvdr_county_clean, cms_specialty
        )""")[0]

    assert keys["row_count"] == keys["distinct_keys"] and \
        keys["null_keys"] == 0, (
        f"GATE FAILED (R2): epdb_dw_prvdr_id not unique or null: {keys}")
    assert keys["null_state"] == 0, (
        f"GATE FAILED (R2): {keys['null_state']} providers with null "
        f"prvdr_state - derivation must yield a token or 'UNK'")
    assert keys["ceiling_below_actual"] == 0, (
        f"GATE FAILED (R2): {keys['ceiling_below_actual']} providers with "
        f"ceiling_annual_v0 below visits_2025 - construction broken")
    assert weights["bad_cells"] == 0, (
        f"GATE FAILED (R4): {weights['bad_cells']} of "
        f"{weights['cells_with_new_visits']} state x county x specialty "
        f"cells whose intake weights do not sum to 1 within 0.001")
    print(f"\nALL GATES PASSED (R2 key + state + ceiling floor, R4 intake "
          f"weights over {weights['cells_with_new_visits']:,} cells)")


if __name__ == "__main__":
    main()
