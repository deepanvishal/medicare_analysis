"""
07 - sickness rates   [PYTHON runner / BigQuery DDL]

WHAT  : Prevalence per county x age band x tenure group x year x
        condition. tenure_group in (ALL, JOINER, TENURED): a member's
        flags are evaluated at their LAST OBSERVED month of the year -
        JOINER = is_first_year true there; TENURED = tenure_months at
        least 24 there; ALL = everyone in scope. A member appears in ALL
        plus at most one of the other two; mid-tenure members (12-23
        months) appear only in ALL. WHY BOTH: notebook 02's SIMILAR
        verdict selects the ALL rows downstream, DIFFERENT selects the
        split rows; this table serves both outcomes without a rebuild.
        County and age band are also taken at the last observed month, so
        each member lands in exactly one cell per year per group.
        Denominator = distinct members present at any point that year in
        that cell; numerator = members among them holding the condition
        that year (md1_condition_flags). Small cells: rows with members
        below 30 keep their counts but prevalence is set null - rates on
        tiny denominators are unstable and would poison downstream
        reconciliation (R4 protection).
SCOPE : R6 restated: member county re-checked against FL/OH/AZ/IL via
        ms_ref_county with LPAD defense (age and LOB scope are inherited
        from the md1 foundation tables and re-asserted by band non-null).
R3    : Attribution = MEMBER county (demand side), carried from
        md1_member_base.
GRAIN : mbr_county_cd x age_band x tenure_group x year x HCC_v24.
INPUTS: md1_member_base, md1_condition_flags (batch A2 outputs),
        cfg.table("ref_county")
OUTPUT: md1_sickness_rates (BigQuery table).
Run   : python model_and_dashboard_v1/03_rates/07_sickness_rates.py
        Run after batch A2 tables are built; independent of notebook 09.
        Writes exactly one table and prints its sanity block.
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

MEMBER_BASE = cfg.src("md1_member_base")
COND_FLAGS  = cfg.src("md1_condition_flags")
CTY         = cfg.table("ref_county")
OUT         = cfg.src("md1_sickness_rates")

FOOTPRINT = "('FL', 'OH', 'AZ', 'IL')"

MEMBER_GROUP_CTES = f"""
    member_year AS (
      SELECT
        b.member_id,
        EXTRACT(YEAR FROM b.month) AS year,
        b.mbr_county_cd,
        b.age_band,
        b.is_first_year,
        b.tenure_months
      FROM `{MEMBER_BASE}` b
      JOIN `{CTY}` rc
        ON LPAD(TRIM(CAST(b.mbr_county_cd AS STRING)), 5, '0') = rc.county_fips
      WHERE rc.state_cd IN {FOOTPRINT}
        AND b.age_band IS NOT NULL
        AND EXTRACT(YEAR FROM b.month) IN (2024, 2025)
      QUALIFY ROW_NUMBER() OVER (
        PARTITION BY b.member_id, EXTRACT(YEAR FROM b.month)
        ORDER BY b.month DESC) = 1
    ),
    member_groups AS (
      SELECT member_id, year, mbr_county_cd, age_band, 'ALL' AS tenure_group
      FROM member_year
      UNION ALL
      SELECT member_id, year, mbr_county_cd, age_band, 'JOINER'
      FROM member_year WHERE is_first_year
      UNION ALL
      SELECT member_id, year, mbr_county_cd, age_band, 'TENURED'
      FROM member_year WHERE tenure_months >= 24
    )
"""

DDL = f"""
CREATE OR REPLACE TABLE `{OUT}`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
WITH {MEMBER_GROUP_CTES},
denom AS (
  SELECT mbr_county_cd, age_band, tenure_group, year,
         COUNT(DISTINCT member_id) AS members
  FROM member_groups
  GROUP BY mbr_county_cd, age_band, tenure_group, year
),
numer AS (
  SELECT
    g.mbr_county_cd,
    g.age_band,
    g.tenure_group,
    g.year,
    f.HCC_v24,
    ANY_VALUE(f.description) AS description,
    CASE MAX(CASE f.chronic_label
               WHEN 'CHRONIC' THEN 3
               WHEN 'NOT_CHRONIC' THEN 2
               WHEN 'NO_DETERMINATION' THEN 1
               ELSE NULL END)
      WHEN 3 THEN 'CHRONIC'
      WHEN 2 THEN 'NOT_CHRONIC'
      WHEN 1 THEN 'NO_DETERMINATION'
      ELSE NULL END AS chronic_label,
    COUNT(DISTINCT g.member_id) AS members_with_condition
  FROM member_groups g
  JOIN `{COND_FLAGS}` f
    ON g.member_id = f.member_id AND g.year = f.year
  GROUP BY g.mbr_county_cd, g.age_band, g.tenure_group, g.year, f.HCC_v24
)
SELECT
  n.mbr_county_cd,
  n.age_band,
  n.tenure_group,
  n.year,
  n.HCC_v24,
  n.description,
  n.chronic_label,
  d.members,
  n.members_with_condition,
  IF(d.members < 30, NULL,
     SAFE_DIVIDE(n.members_with_condition, d.members)) AS prevalence
FROM numer n
JOIN denom d
  ON n.mbr_county_cd = d.mbr_county_cd
  AND n.age_band = d.age_band
  AND n.tenure_group = d.tenure_group
  AND n.year = d.year
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

    keys = q(client, "row count, key uniqueness, prevalence bounds (R2)", f"""
        SELECT COUNT(*) AS row_count,
               COUNT(DISTINCT CONCAT(CAST(mbr_county_cd AS STRING), '|', age_band, '|',
                                     tenure_group, '|', CAST(year AS STRING), '|',
                                     CAST(HCC_v24 AS STRING))) AS distinct_keys,
               COUNTIF(prevalence IS NOT NULL
                       AND (prevalence < 0 OR prevalence > 1)) AS out_of_bounds,
               COUNTIF(prevalence IS NULL) AS small_cell_rows
        FROM `{OUT}`""")[0]
    print(f"\nsmall-cell rows with null prevalence (members < 30): "
          f"{keys['small_cell_rows']:,}")

    states = q(client, "footprint states present (R6)", f"""
        SELECT rc.state_cd, COUNT(DISTINCT t.mbr_county_cd) AS counties
        FROM `{OUT}` t
        JOIN `{CTY}` rc
          ON LPAD(TRIM(CAST(t.mbr_county_cd AS STRING)), 5, '0') = rc.county_fips
        GROUP BY rc.state_cd ORDER BY rc.state_cd""")

    spots = q(client, "R4 spot check: top-5 conditions, ALL 2025, table vs inline", f"""
        WITH {MEMBER_GROUP_CTES},
        top5 AS (
          SELECT HCC_v24
          FROM `{OUT}`
          WHERE tenure_group = 'ALL' AND year = 2025
          GROUP BY HCC_v24
          ORDER BY SUM(members_with_condition) DESC
          LIMIT 5
        ),
        tbl AS (
          SELECT HCC_v24,
                 SAFE_DIVIDE(SUM(members_with_condition), SUM(members)) AS tbl_prevalence
          FROM `{OUT}`
          WHERE tenure_group = 'ALL' AND year = 2025
            AND HCC_v24 IN (SELECT HCC_v24 FROM top5)
          GROUP BY HCC_v24
        ),
        inline AS (
          SELECT f.HCC_v24,
                 SAFE_DIVIDE(COUNT(DISTINCT f.member_id),
                             (SELECT COUNT(DISTINCT member_id) FROM member_year
                              WHERE year = 2025)) AS inline_prevalence
          FROM member_year my
          JOIN `{COND_FLAGS}` f
            ON my.member_id = f.member_id AND f.year = 2025
          WHERE my.year = 2025
            AND f.HCC_v24 IN (SELECT HCC_v24 FROM top5)
          GROUP BY f.HCC_v24
        )
        SELECT t.HCC_v24, t.tbl_prevalence, i.inline_prevalence,
               ABS(t.tbl_prevalence - i.inline_prevalence) AS diff
        FROM tbl t
        JOIN inline i ON t.HCC_v24 = i.HCC_v24
        ORDER BY t.HCC_v24""")

    assert keys["row_count"] == keys["distinct_keys"], (
        f"GATE FAILED (R2): key not unique at county x band x group x year x "
        f"condition: {keys}")
    assert keys["out_of_bounds"] == 0, (
        f"GATE FAILED (R2): {keys['out_of_bounds']} rows with prevalence outside 0-1")
    found_states = {r["state_cd"] for r in states}
    assert found_states == {"FL", "OH", "AZ", "IL"}, (
        f"GATE FAILED (R6): footprint states present are {sorted(found_states)}")
    for r in spots:
        assert r["diff"] is not None and float(r["diff"]) <= 0.005, (
            f"GATE FAILED (R4): condition {r['HCC_v24']} table prevalence "
            f"{r['tbl_prevalence']} vs inline {r['inline_prevalence']} differ by "
            f"more than 0.5 percent")
    print("\nALL GATES PASSED (R2 key + bounds, R4 spot check, R6 states)")


if __name__ == "__main__":
    main()
