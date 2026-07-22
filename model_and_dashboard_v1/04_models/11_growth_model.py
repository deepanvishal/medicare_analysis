"""
11 - growth model   [PYTHON runner / BigQuery DDL]

WHAT  : Expected-growth defaults per county x age band, the values the
        dashboard sliders start at. All growth columns are PERCENTAGE
        POINTS (5.0 means +5 percent).
METHOD: cell_yoy       = 100 * (members Dec 2025 / members Dec 2024 - 1)
        state_band_yoy = 100 * (sum Dec 2025 / sum Dec 2024 - 1) per
                         state x band over the same cells
        weight_w       = n / (n + K), n = members Dec 2024 in the cell,
                         K = SHRINKAGE_K
        default_pct    = clamp(w * cell_yoy + (1 - w) * state_band_yoy,
                               CLAMP_LO, CLAMP_HI)
        ALL_BANDS row  = per county, the Dec-2024-member-weighted mean of
                         its band default_pct values (its cell_yoy is the
                         county-total yoy; state_band_yoy, weight_w and
                         clamped are NULL - not defined at county level).
        Cells present in Dec 2024 but gone by Dec 2025 keep a row with
        members_2025_dec = 0 (cell_yoy -100; the shrunken default
        clamps only when the cell is large enough to overpower the
        state target). Closed-form
        arithmetic, no sampling and no stochastic fit, so R8 needs no
        seed.
SCOPE : R6 restated: footprint filter state_cd IN (FL, OH, AZ, IL)
        re-applied. Age is structural (all bands 60+ by construction);
        the membership extract carries no LOB column per the data
        dictionary.
R3    : Attribution = MEMBER county (demand side).
GRAIN : mbr_county_cd x age_band, where age_band includes the four bands
        plus one ALL_BANDS row per county.
INPUTS: md1_enrollment_history (built by notebook 06) - the only input.
OUTPUT: md1_growth_defaults (BigQuery table).
Run   : python model_and_dashboard_v1/04_models/11_growth_model.py
        Runnable once md1_enrollment_history exists; independent of 07
        and 09. The trio is sequential: 10 before 11 before 12.
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

ENR = cfg.src("md1_enrollment_history")
OUT = cfg.src("md1_growth_defaults")

FOOTPRINT = "('FL', 'OH', 'AZ', 'IL')"

SHRINKAGE_K = 500
CLAMP_LO = -20.0
CLAMP_HI = 30.0

DDL = f"""
CREATE OR REPLACE TABLE `{OUT}`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
WITH dec24 AS (
  SELECT mbr_county_cd, state_cd, age_band, members AS members_2024_dec
  FROM `{ENR}`
  WHERE state_cd IN {FOOTPRINT}
    AND month = DATE '2024-12-01'
),
dec25 AS (
  SELECT mbr_county_cd, age_band, members AS members_2025_dec
  FROM `{ENR}`
  WHERE state_cd IN {FOOTPRINT}
    AND month = DATE '2025-12-01'
),
cells AS (
  SELECT
    d24.mbr_county_cd,
    d24.state_cd,
    d24.age_band,
    d24.members_2024_dec,
    COALESCE(d25.members_2025_dec, 0) AS members_2025_dec
  FROM dec24 d24
  LEFT JOIN dec25 d25
    ON d24.mbr_county_cd = d25.mbr_county_cd
    AND d24.age_band = d25.age_band
),
state_band AS (
  SELECT
    state_cd,
    age_band,
    100 * (SAFE_DIVIDE(SUM(members_2025_dec), SUM(members_2024_dec)) - 1)
      AS state_band_yoy
  FROM cells
  GROUP BY state_cd, age_band
),
shrunk AS (
  SELECT
    c.mbr_county_cd,
    c.state_cd,
    c.age_band,
    c.members_2024_dec,
    c.members_2025_dec,
    100 * (SAFE_DIVIDE(c.members_2025_dec, c.members_2024_dec) - 1) AS cell_yoy,
    s.state_band_yoy,
    SAFE_DIVIDE(c.members_2024_dec, c.members_2024_dec + {SHRINKAGE_K}) AS weight_w
  FROM cells c
  JOIN state_band s
    ON c.state_cd = s.state_cd
    AND c.age_band = s.age_band
),
band_rows AS (
  SELECT
    mbr_county_cd,
    state_cd,
    age_band,
    members_2024_dec,
    members_2025_dec,
    cell_yoy,
    state_band_yoy,
    weight_w,
    LEAST(GREATEST(weight_w * cell_yoy + (1 - weight_w) * state_band_yoy,
                   {CLAMP_LO}), {CLAMP_HI}) AS default_pct,
    (weight_w * cell_yoy + (1 - weight_w) * state_band_yoy < {CLAMP_LO}
     OR weight_w * cell_yoy + (1 - weight_w) * state_band_yoy > {CLAMP_HI})
      AS clamped
  FROM shrunk
),
county_rows AS (
  SELECT
    mbr_county_cd,
    ANY_VALUE(state_cd) AS state_cd,
    'ALL_BANDS' AS age_band,
    SUM(members_2024_dec) AS members_2024_dec,
    SUM(members_2025_dec) AS members_2025_dec,
    100 * (SAFE_DIVIDE(SUM(members_2025_dec), SUM(members_2024_dec)) - 1)
      AS cell_yoy,
    CAST(NULL AS FLOAT64) AS state_band_yoy,
    CAST(NULL AS FLOAT64) AS weight_w,
    SAFE_DIVIDE(SUM(default_pct * members_2024_dec), SUM(members_2024_dec))
      AS default_pct,
    CAST(NULL AS BOOL) AS clamped
  FROM band_rows
  GROUP BY mbr_county_cd
)
SELECT * FROM band_rows
UNION ALL
SELECT * FROM county_rows
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

    keys = q(client, "row count, key uniqueness, clamp range (R2)", f"""
        SELECT COUNT(*) AS row_count,
               COUNT(DISTINCT CONCAT(CAST(mbr_county_cd AS STRING), '|',
                                     age_band)) AS distinct_keys,
               COUNTIF(mbr_county_cd IS NULL OR age_band IS NULL) AS null_keys,
               COUNTIF(default_pct IS NULL) AS null_default,
               COUNTIF(default_pct < {CLAMP_LO} - 0.000001
                       OR default_pct > {CLAMP_HI} + 0.000001) AS out_of_range,
               COUNTIF(COALESCE(clamped, FALSE)) AS clamped_cells
        FROM `{OUT}`""")[0]
    print(f"\nclamped cells: {keys['clamped_cells']:,}")

    coverage = q(client, "Dec 2024 cell coverage (R1)", f"""
        SELECT
          (SELECT COUNT(*) FROM `{ENR}`
           WHERE state_cd IN {FOOTPRINT}
             AND month = DATE '2024-12-01') AS source_cells,
          (SELECT COUNT(*) FROM `{OUT}`
           WHERE age_band != 'ALL_BANDS') AS table_band_rows,
          (SELECT COUNT(DISTINCT mbr_county_cd) FROM `{OUT}`
           WHERE age_band = 'ALL_BANDS') AS county_rows""")[0]

    weighted = q(client, "ALL_BANDS vs weighted band mean (R4)", f"""
        WITH bands AS (
          SELECT mbr_county_cd,
                 SAFE_DIVIDE(SUM(default_pct * members_2024_dec),
                             SUM(members_2024_dec)) AS recomputed
          FROM `{OUT}`
          WHERE age_band != 'ALL_BANDS'
          GROUP BY mbr_county_cd
        )
        SELECT COUNTIF(ABS(a.default_pct - b.recomputed) > 0.1) AS bad_counties,
               MAX(ABS(a.default_pct - b.recomputed)) AS max_abs_diff
        FROM `{OUT}` a
        JOIN bands b ON a.mbr_county_cd = b.mbr_county_cd
        WHERE a.age_band = 'ALL_BANDS'""")[0]

    assert keys["row_count"] == keys["distinct_keys"] and keys["null_keys"] == 0, (
        f"GATE FAILED (R2): key not unique or null at county x band: {keys}")
    assert keys["null_default"] == 0 and keys["out_of_range"] == 0, (
        f"GATE FAILED (R2): default_pct null or outside "
        f"{CLAMP_LO}..{CLAMP_HI}: {keys}")
    assert coverage["source_cells"] == coverage["table_band_rows"], (
        f"GATE FAILED (R1): Dec 2024 has {coverage['source_cells']} county x "
        f"band cells but the table has {coverage['table_band_rows']} band rows")
    assert weighted["bad_counties"] == 0, (
        f"GATE FAILED (R4): {weighted['bad_counties']} ALL_BANDS rows deviate "
        f"from the weighted band mean by more than 0.1 points "
        f"(max {weighted['max_abs_diff']})")
    print("\nALL GATES PASSED (R2 key + clamp range, R1 Dec 2024 coverage, "
          "R4 ALL_BANDS weighted mean)")


if __name__ == "__main__":
    main()
