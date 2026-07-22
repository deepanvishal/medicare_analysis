"""
05b - demand specialty mapping   [PYTHON runner / BigQuery DDL]

WHAT  : md1_ref_specialty_demand - the DEMAND-ONLY specialty mapping
        with exactly one CMS specialty per aetna code. Source = the
        43-row compliance crosswalk (dictionary 2.6, join semantics per
        trap 15), which is deliberately one-to-many on aetna_cd: one
        provider can satisfy several adequacy standards (WHOS spans
        Acute Inpatient Hospitals AND Outpatient Infusion/Chemo; VVRH
        spans four therapy standards). Correct for compliance counting;
        WRONG for demand counting, where a fanned-out join clones
        visits (D12). This table applies the recorded primary-pick
        policy below; if any aetna code still maps to more than one CMS
        specialty afterward, the build FAILS and prints the offending
        codes and names - it never auto-picks. CMS specialties dropped
        by the policy leave the demand axis; compliance reporting keeps
        them via the untouched crosswalk.
SCOPE : Mapping table only; no member or claims scope applies here (R6
        binds inside the consumers, 14 and 15).
R3    : Not applicable - no geography in this table.
GRAIN : aetna_cd (one row per code).
INPUTS: cfg.base("ref_specialty_crosswalk") - read only, never modified.
OUTPUT: md1_ref_specialty_demand (BigQuery table).
Run   : python model_and_dashboard_v1/02_foundation/05b_ref_specialty_demand.py
        Runs before notebooks 14 and 15, which join this table for all
        demand visit counting. The compliance pipeline, its crosswalk
        loader, and every ms_ table stay untouched.
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

XWALK = cfg.base("ref_specialty_crosswalk")
OUT   = cfg.src("md1_ref_specialty_demand")

# Primary-pick policy (D12): the one CMS specialty a demand visit counts
# toward. Rationale per code:
#   WHOS - hospital code spanning the inpatient and infusion standards;
#          the visit volume is inpatient hospital care.
#   VVRH - rehab code spanning four therapy standards; physical therapy
#          carries the dominant therapy volume.
#   C    - medical cardiology, not the surgical standard the code also
#          satisfies for adequacy.
#   CS   - the dedicated surgical code keeps the surgical specialty.
#   WBHF - behavioral facility code; outpatient behavioral health is
#          where the visits happen.
#   VVMH - Mental Health Professional, a shared proxy for both mental
#          health service lines; Clinical Psychology chosen as the
#          broader clinical service line for the demand axis; Clinical
#          Social Work drops.
PRIMARY_PICK = {
    "WHOS": "Acute Inpatient Hospitals",
    "VVRH": "Physical Therapy",
    "C": "Cardiology",
    "CS": "Cardiothoracic Surgery",
    "WBHF": "Outpatient Behavioral Health",
    "VVMH": "Clinical Psychology",
}

CODES_SQL = ", ".join(f"'{c}'" for c in sorted(PRIMARY_PICK))
PICK_PRED = " OR ".join(
    f"(aetna_cd = '{c}' AND cms_specialty = '{s}')"
    for c, s in sorted(PRIMARY_PICK.items()))

DDL = f"""
CREATE OR REPLACE TABLE `{OUT}`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
WITH src AS (
  SELECT TRIM(CAST(aetna_cd AS STRING)) AS aetna_cd,
         TRIM(CAST(cms_specialty AS STRING)) AS cms_specialty
  FROM `{XWALK}`
)
SELECT aetna_cd, cms_specialty
FROM src
WHERE aetna_cd NOT IN ({CODES_SQL})
   OR {PICK_PRED}
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

    preflight = q(client, "PRE-FLIGHT: residual multi-maps after policy "
                          "(must be empty before the table is created)", f"""
        WITH src AS (
          SELECT TRIM(CAST(aetna_cd AS STRING)) AS aetna_cd,
                 TRIM(CAST(cms_specialty AS STRING)) AS cms_specialty
          FROM `{XWALK}`
        ),
        picked AS (
          SELECT aetna_cd, cms_specialty
          FROM src
          WHERE aetna_cd NOT IN ({CODES_SQL})
             OR {PICK_PRED}
        )
        SELECT aetna_cd, COUNT(*) AS row_count,
               STRING_AGG(cms_specialty, ' | ' ORDER BY cms_specialty)
                 AS cms_names
        FROM picked
        GROUP BY aetna_cd
        HAVING COUNT(*) > 1
        ORDER BY aetna_cd""")
    assert not preflight, (
        f"GATE FAILED (R2): aetna codes still multi-mapped after the "
        f"primary-pick policy - table NOT created; extend PRIMARY_PICK "
        f"with a deliberate pick, never auto-pick: "
        f"{[(r['aetna_cd'], r['cms_names']) for r in preflight]}")

    client.query(DDL).result()
    print(f"table created: {OUT}")

    counts = q(client, "rows in / rows out / distinct counts (R1/R2)", f"""
        SELECT
          (SELECT COUNT(*) FROM `{XWALK}`) AS rows_in,
          (SELECT COUNT(DISTINCT TRIM(CAST(aetna_cd AS STRING)))
           FROM `{XWALK}`) AS distinct_codes_in,
          (SELECT COUNTIF(aetna_cd IS NULL
                          OR TRIM(CAST(aetna_cd AS STRING)) = '')
           FROM `{XWALK}`) AS null_codes_in,
          (SELECT COUNT(*) FROM `{OUT}`) AS rows_out,
          (SELECT COUNT(DISTINCT aetna_cd) FROM `{OUT}`) AS distinct_codes_out,
          (SELECT COUNT(DISTINCT cms_specialty) FROM `{OUT}`) AS distinct_cms
        """)[0]

    dropped = q(client, "CMS names dropped from the demand axis per policy", f"""
        WITH src AS (
          SELECT TRIM(CAST(aetna_cd AS STRING)) AS aetna_cd,
                 TRIM(CAST(cms_specialty AS STRING)) AS cms_specialty
          FROM `{XWALK}`
        )
        SELECT aetna_cd, cms_specialty
        FROM src
        WHERE aetna_cd IN ({CODES_SQL})
          AND NOT ({PICK_PRED})
        ORDER BY aetna_cd, cms_specialty""")

    picks = q(client, "policy codes as landed in the demand table", f"""
        SELECT aetna_cd, cms_specialty
        FROM `{OUT}`
        WHERE aetna_cd IN ({CODES_SQL})
        ORDER BY aetna_cd""")

    multi = q(client, "residual multi-mapped codes (must be empty)", f"""
        SELECT aetna_cd, COUNT(*) AS row_count,
               STRING_AGG(cms_specialty, ' | ' ORDER BY cms_specialty)
                 AS cms_names
        FROM `{OUT}`
        GROUP BY aetna_cd
        HAVING COUNT(*) > 1
        ORDER BY aetna_cd""")

    print(f"\nrows in: {counts['rows_in']}  rows out: {counts['rows_out']}")
    print(f"distinct aetna codes: {counts['distinct_codes_in']} in -> "
          f"{counts['distinct_codes_out']} out")
    print(f"final distinct CMS specialty count on the demand axis: "
          f"{counts['distinct_cms']}")
    for r in dropped:
        print(f"dropped from demand axis (compliance keeps it): "
              f"{r['aetna_cd']} -> {r['cms_specialty']}")

    assert not multi, (
        f"GATE FAILED (R2): aetna codes still multi-mapped after the "
        f"primary-pick policy - extend PRIMARY_PICK, never auto-pick: "
        f"{[(r['aetna_cd'], r['cms_names']) for r in multi]}")
    assert counts["rows_out"] == counts["distinct_codes_out"], (
        f"GATE FAILED (R2): aetna_cd not unique in the demand mapping: "
        f"{counts}")
    assert counts["distinct_codes_in"] == counts["distinct_codes_out"], (
        f"GATE FAILED (R1): aetna codes lost between crosswalk and demand "
        f"mapping: {counts['distinct_codes_in']} in vs "
        f"{counts['distinct_codes_out']} out - a PRIMARY_PICK name likely "
        f"does not match the crosswalk spelling")
    assert counts["null_codes_in"] == 0, (
        f"GATE FAILED (R7): {counts['null_codes_in']} crosswalk rows with "
        f"null or empty aetna_cd would be silently dropped by the policy "
        f"filter")
    assert counts["rows_in"] == counts["rows_out"] + len(dropped), (
        f"GATE FAILED (R1): row identity broken - rows_in "
        f"{counts['rows_in']} != rows_out {counts['rows_out']} + dropped "
        f"{len(dropped)}; something besides the policy filtered rows")
    landed = {r["aetna_cd"]: r["cms_specialty"] for r in picks}
    for code, spec in sorted(PRIMARY_PICK.items()):
        assert landed.get(code) == spec, (
            f"GATE FAILED (R1): policy code {code} landed as "
            f"{landed.get(code)!r}, expected {spec!r} - check the "
            f"crosswalk spelling")
    print("\nALL GATES PASSED (R2 aetna_cd unique + no residual "
          "multi-maps, R1 no code lost + policy picks landed)")


if __name__ == "__main__":
    main()
