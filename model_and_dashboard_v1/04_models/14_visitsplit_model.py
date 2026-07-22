"""
14 - visit-split model   [PYTHON runner / BigQuery stats + local NNLS]

WHAT  : Fits the visit-splitting model per notebook 13's closing
        decisions (sub-additive overlap, main effects only, base rate
        subtracted, single-condition anchors as sanity) and writes the
        rate table md1_visitsplit_rates.
FORMULA: for each CMS specialty s, member 2025 bridged visits
        y_m = base_s + sum over conditions c of beta[c, s] *
        indicator(member m has c), fitted with nonnegative least
        squares; the beta matrix is the visits[condition, specialty]
        rate table and base_s is the base rate.
METHOD: (1) 2025 in-scope members (md1_member_base spine) and their
        2025 visits from md1_visits_base restricted to BRIDGED
        specialties: join the 43-row crosswalk on aetna_cd (which holds
        specialty_ctg_cd values, dictionary trap 15) with TRIM(CAST())
        both sides, aggregating specialty_ctg_cd to cms_specialty; the
        crosswalk fans out, so a visit can count toward more than one
        CMS specialty. Unbridged visits are OUT OF SCOPE - the recorded
        37.6 percent share is intentional mapping policy (dictionary
        trap 23), restated here, not re-investigated. (2) Conditions =
        2025 HCC_v24 with at least MIN_CONDITION_MEMBERS members;
        rarer ones pool into one OTHER_CONDITION indicator. (3) Fit via
        sufficient statistics computed IN BigQuery with three queries
        (condition counts, pairwise co-occurrence = X-transpose-X,
        visit sums by condition x specialty = X-transpose-y plus
        per-specialty y-squared sums); no member-level frame is
        downloaded. Locally: XtX is stabilized with a tiny ridge,
        Cholesky-factored (G = L L-transpose), and
        scipy.optimize.nnls(L-transpose, solve(L, Xty)) solves the
        ridge-stabilized normal equations under beta >= 0 per specialty
        - NNLS on the member-level problem up to the tiny ridge penalty
        (exact equivalence would require factoring XtX itself, which
        collinearity can make impossible). The intercept column is the
        base rate and stays nonnegative like the rest. NNLS is
        deterministic - no sampling, no RNG - so R8 needs no seed.
        (4) Reconciliation: deflation_factor = observed total /
        predicted total per specialty; coef_deflated = coef_raw *
        deflation_factor is the shipping value.
SCOPE : R6 restated: age_nbr >= 60 and footprint states FL/OH/AZ/IL
        re-applied on the membership spine via ms_ref_county with LPAD
        defense; the CP/ME LOB rule binds inside md1_visits_base and
        md1_condition_flags (claims-built), as the membership extract
        carries no LOB column per the data dictionary.
R3    : Attribution = member-level fit; the member-county join is scope
        re-assertion only and no geography is stored.
GRAIN : cms_specialty x condition, where condition includes BASE_RATE
        and OTHER_CONDITION rows.
INPUTS: md1_member_base, md1_visits_base, md1_condition_flags (batch A2
        outputs), cfg.base("ref_specialty_crosswalk"),
        cfg.table("ref_county")
OUTPUT: md1_visitsplit_rates (BigQuery table).
Run   : python model_and_dashboard_v1/04_models/14_visitsplit_model.py
        Requires scipy locally (pip install scipy); BigQuery does only
        aggregation. Run after batch A2 tables and notebook 13;
        independent of 07, 09 and the 10-12 growth trio.
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

import numpy as np
from scipy.optimize import nnls

VISITS = cfg.src("md1_visits_base")
CFLAGS = cfg.src("md1_condition_flags")
MBASE  = cfg.src("md1_member_base")
XWALK  = cfg.base("ref_specialty_crosswalk")
CTY    = cfg.table("ref_county")
OUT    = cfg.src("md1_visitsplit_rates")

FOOTPRINT = "('FL', 'OH', 'AZ', 'IL')"

STUDY_YEAR = 2025
MIN_CONDITION_MEMBERS = 5000
MIN_SPECIALTY_VISITS = 50000
RIDGE_SCALE = 1e-8
ANCHOR_HCCS = 15
SMALL_CELL_MEMBERS = 1000

SPINE = f"""spine AS (
  SELECT DISTINCT mb.member_id
  FROM `{MBASE}` mb
  JOIN `{CTY}` rc
    ON LPAD(TRIM(CAST(mb.mbr_county_cd AS STRING)), 5, '0') = rc.county_fips
  WHERE rc.state_cd IN {FOOTPRINT}
    AND mb.age_nbr >= 60
    AND EXTRACT(YEAR FROM mb.month) = {STUDY_YEAR}
)"""

CONDS = f"""conds AS (
  SELECT member_id, COUNT(DISTINCT HCC_v24) AS n_cond
  FROM `{CFLAGS}`
  WHERE year = {STUDY_YEAR}
  GROUP BY member_id
)"""


def fetch(client, sql):
    return [dict(r) for r in client.query(sql).result()]


def mc_sql(kept_sql):
    return f"""mc AS (
  SELECT DISTINCT f.member_id,
    CASE WHEN CAST(f.HCC_v24 AS STRING) IN ({kept_sql})
         THEN CAST(f.HCC_v24 AS STRING)
         ELSE 'OTHER_CONDITION' END AS condition
  FROM `{CFLAGS}` f
  JOIN spine s ON f.member_id = s.member_id
  WHERE f.year = {STUDY_YEAR}
)"""


def msv_sql():
    return f"""msv AS (
  SELECT v.member_id, cw.cms_specialty, COUNT(*) AS visit_count
  FROM `{VISITS}` v
  JOIN `{XWALK}` cw
    ON TRIM(CAST(v.specialty_ctg_cd AS STRING)) = TRIM(CAST(cw.aetna_cd AS STRING))
  JOIN spine s ON v.member_id = s.member_id
  WHERE EXTRACT(YEAR FROM v.month) = {STUDY_YEAR}
  GROUP BY v.member_id, cw.cms_specialty
)"""


def main():
    client = cfg.client()

    cond_counts_raw = fetch(client, f"""
        WITH {SPINE}
        SELECT CAST(f.HCC_v24 AS STRING) AS condition,
               COUNT(DISTINCT f.member_id) AS member_count
        FROM `{CFLAGS}` f
        JOIN spine s ON f.member_id = s.member_id
        WHERE f.year = {STUDY_YEAR}
        GROUP BY condition
        UNION ALL
        SELECT '_SPINE_TOTAL', COUNT(*) FROM spine""")
    n_members = next(r["member_count"] for r in cond_counts_raw
                     if r["condition"] == "_SPINE_TOTAL")
    hcc_counts = {r["condition"]: r["member_count"] for r in cond_counts_raw
                  if r["condition"] != "_SPINE_TOTAL"}
    kept = sorted(c for c, n in hcc_counts.items()
                  if n >= MIN_CONDITION_MEMBERS)
    pooled = sorted(c for c in hcc_counts if c not in kept)
    assert kept, (
        "GATE FAILED (R2): no condition reaches MIN_CONDITION_MEMBERS; "
        "nothing to fit")
    print(f"spine members: {n_members:,}")
    print(f"kept conditions (>= {MIN_CONDITION_MEMBERS:,} members): "
          f"{len(kept)}; pooled into OTHER_CONDITION: {len(pooled)}")

    kept_sql = ", ".join(f"'{c}'" for c in kept)

    pair_rows = fetch(client, f"""
        WITH {SPINE}, {mc_sql(kept_sql)}
        SELECT a.condition AS cond_a, b.condition AS cond_b,
               COUNT(DISTINCT a.member_id) AS pair_members
        FROM mc a
        JOIN mc b ON a.member_id = b.member_id
        GROUP BY cond_a, cond_b""")

    xty_rows = fetch(client, f"""
        WITH {SPINE}, {mc_sql(kept_sql)}, {msv_sql()}
        SELECT m.condition, sv.cms_specialty,
               SUM(sv.visit_count) AS visit_sum,
               CAST(NULL AS INT64) AS visit_sq_sum
        FROM msv sv
        JOIN mc m ON sv.member_id = m.member_id
        GROUP BY m.condition, sv.cms_specialty
        UNION ALL
        SELECT '_INTERCEPT_', cms_specialty,
               SUM(visit_count) AS visit_sum,
               SUM(visit_count * visit_count) AS visit_sq_sum
        FROM msv
        GROUP BY cms_specialty
        UNION ALL
        SELECT '_XWALK_SPEC_', cms_specialty,
               0 AS visit_sum, CAST(NULL AS INT64) AS visit_sq_sum
        FROM (SELECT DISTINCT cms_specialty FROM `{XWALK}`)""")

    indicators = kept + ["OTHER_CONDITION"]
    p = len(indicators) + 1
    idx = {c: i for i, c in enumerate(indicators)}
    xtx = np.zeros((p, p))
    for r in pair_rows:
        xtx[idx[r["cond_a"]], idx[r["cond_b"]]] = r["pair_members"]
    for i in range(p - 1):
        xtx[p - 1, i] = xtx[i, p - 1] = xtx[i, i]
    xtx[p - 1, p - 1] = n_members
    colsums = xtx[p - 1, :].copy()

    observed, ysq, xty = {}, {}, {}
    all_bridge_specs = set()
    for r in xty_rows:
        spec = r["cms_specialty"]
        if r["condition"] == "_XWALK_SPEC_":
            all_bridge_specs.add(spec)
        elif r["condition"] == "_INTERCEPT_":
            observed[spec] = r["visit_sum"]
            ysq[spec] = r["visit_sq_sum"]
        else:
            xty.setdefault(spec, {})[r["condition"]] = r["visit_sum"]

    fitted_specs = sorted(s for s, tot in observed.items()
                          if tot >= MIN_SPECIALTY_VISITS)
    excluded = sorted((s, observed[s]) for s in observed
                      if s not in fitted_specs)
    print(f"\nfitted specialties (>= {MIN_SPECIALTY_VISITS:,} bridged "
          f"{STUDY_YEAR} visits): {len(fitted_specs)}")
    for s, tot in excluded:
        print(f"  excluded (too few bridged visits): {s} ({tot:,})")
    for s in sorted(all_bridge_specs - set(observed)):
        print(f"  excluded (zero bridged {STUDY_YEAR} visits): {s}")

    ridge = RIDGE_SCALE * (np.trace(xtx) / p)
    chol = np.linalg.cholesky(xtx + ridge * np.eye(p))

    out_rows = []
    fit_stats = {}
    degenerate = []
    for spec in fitted_specs:
        b = np.zeros(p)
        for cond, v in xty.get(spec, {}).items():
            b[idx[cond]] = v
        b[p - 1] = observed[spec]
        beta, _ = nnls(chol.T, np.linalg.solve(chol, b))
        predicted = float(colsums @ beta)
        if predicted <= 0:
            degenerate.append(spec)
            print(f"  degenerate fit (predicted total 0), excluded: {spec}")
            continue
        deflation = observed[spec] / predicted
        ss_res = float(ysq[spec] - 2 * b @ beta + beta @ xtx @ beta)
        ss_tot = float(ysq[spec] - observed[spec] ** 2 / n_members)
        r2 = (1 - ss_res / ss_tot) if ss_tot > 1e-9 else None
        fit_stats[spec] = {"beta": beta, "deflation": deflation, "r2": r2}
        out_rows.append({
            "cms_specialty": spec,
            "condition": "BASE_RATE",
            "members_with_condition": int(n_members),
            "coef_raw": float(beta[p - 1]),
            "coef_deflated": float(beta[p - 1] * deflation),
            "deflation_factor": float(deflation),
            "r2": None if r2 is None else float(r2),
            "n_conditions_kept": len(kept),
        })
        for cond in indicators:
            out_rows.append({
                "cms_specialty": spec,
                "condition": cond,
                "members_with_condition": int(xtx[idx[cond], idx[cond]]),
                "coef_raw": float(beta[idx[cond]]),
                "coef_deflated": float(beta[idx[cond]] * deflation),
                "deflation_factor": float(deflation),
                "r2": None if r2 is None else float(r2),
                "n_conditions_kept": len(kept),
            })
    shipped_specs = [s for s in fitted_specs if s in fit_stats]
    print(f"\nrate table rows: {len(out_rows)} "
          f"({len(shipped_specs)} specialties x {p} conditions incl "
          f"BASE_RATE and OTHER_CONDITION)")
    for s in shipped_specs:
        st = fit_stats[s]
        r2_txt = "n/a" if st["r2"] is None else f"{st['r2']:.3f}"
        print(f"  {s:<40} deflation={st['deflation']:.4f}  r2={r2_txt}")

    from google.cloud import bigquery
    schema = [
        bigquery.SchemaField("cms_specialty", "STRING"),
        bigquery.SchemaField("condition", "STRING"),
        bigquery.SchemaField("members_with_condition", "INT64"),
        bigquery.SchemaField("coef_raw", "FLOAT64"),
        bigquery.SchemaField("coef_deflated", "FLOAT64"),
        bigquery.SchemaField("deflation_factor", "FLOAT64"),
        bigquery.SchemaField("r2", "FLOAT64"),
        bigquery.SchemaField("n_conditions_kept", "INT64"),
    ]
    job_config = bigquery.LoadJobConfig(
        schema=schema, write_disposition="WRITE_TRUNCATE")
    client.load_table_from_json(out_rows, OUT, job_config=job_config).result()
    client.query(f"""
        ALTER TABLE `{OUT}`
        SET OPTIONS (labels = [("owner", "deepan_thulasi_aetna_com")])
        """).result()
    print(f"\ntable created: {OUT}")

    anchors = fetch(client, f"""
        WITH {SPINE}, {CONDS},
        top_hcc AS (
          SELECT CAST(f.HCC_v24 AS STRING) AS hcc,
                 COUNT(DISTINCT f.member_id) AS prev_members
          FROM `{CFLAGS}` f
          JOIN spine s ON f.member_id = s.member_id
          WHERE f.year = {STUDY_YEAR}
          GROUP BY hcc
          ORDER BY prev_members DESC
          LIMIT {ANCHOR_HCCS}
        ),
        solo AS (
          SELECT f.member_id, CAST(f.HCC_v24 AS STRING) AS hcc
          FROM `{CFLAGS}` f
          JOIN conds c ON f.member_id = c.member_id AND c.n_cond = 1
          JOIN spine s ON f.member_id = s.member_id
          WHERE f.year = {STUDY_YEAR}
          GROUP BY f.member_id, hcc
        ),
        cohort AS (
          SELECT so.hcc, COUNT(*) AS cohort_members
          FROM solo so
          JOIN top_hcc t ON so.hcc = t.hcc
          GROUP BY so.hcc
        ),
        sv AS (
          SELECT so.hcc, cw.cms_specialty, COUNT(*) AS visit_count
          FROM `{VISITS}` v
          JOIN `{XWALK}` cw
            ON TRIM(CAST(v.specialty_ctg_cd AS STRING)) = TRIM(CAST(cw.aetna_cd AS STRING))
          JOIN solo so ON v.member_id = so.member_id
          JOIN top_hcc t ON so.hcc = t.hcc
          WHERE EXTRACT(YEAR FROM v.month) = {STUDY_YEAR}
          GROUP BY so.hcc, cw.cms_specialty
        )
        SELECT c.hcc, c.cohort_members, sv.cms_specialty,
               SAFE_DIVIDE(sv.visit_count, c.cohort_members) AS solo_rate
        FROM cohort c
        JOIN sv ON c.hcc = sv.hcc
        WHERE TRUE
        QUALIFY ROW_NUMBER() OVER (PARTITION BY c.hcc
                                   ORDER BY sv.visit_count DESC) = 1
        ORDER BY c.cohort_members DESC""")
    coef_map = {(r["cms_specialty"], r["condition"]): r["coef_deflated"]
                for r in out_rows}
    print(f"\n=== anchor sanity: {ANCHOR_HCCS} single-condition cohorts "
          f"(informational) ===")
    solo_vals, coef_vals = [], []
    for r in anchors:
        key = (r["cms_specialty"], r["hcc"])
        coef = coef_map.get(key)
        solo_rate = float(r["solo_rate"])
        if r["cohort_members"] < SMALL_CELL_MEMBERS:
            print(f"  HCC {r['hcc']:>5}  n={r['cohort_members']:>7,}  "
                  f"[SMALL-CELL] excluded from anchoring per notebook 13's "
                  f"recorded decision")
            continue
        if coef is None:
            print(f"  HCC {r['hcc']:>5}  n={r['cohort_members']:>7,}  "
                  f"top_spec={r['cms_specialty']:<30}  "
                  f"solo_rate={solo_rate:6.2f}  coef=n/a "
                  f"(condition pooled or specialty not fitted)")
            continue
        solo_vals.append(solo_rate)
        coef_vals.append(coef)
        print(f"  HCC {r['hcc']:>5}  n={r['cohort_members']:>7,}  "
              f"top_spec={r['cms_specialty']:<30}  "
              f"solo_rate={solo_rate:6.2f}  coef_deflated={coef:6.2f}")
    if len(solo_vals) >= 2:
        pearson = float(np.corrcoef(solo_vals, coef_vals)[0, 1])
        print(f"  Pearson correlation across {len(solo_vals)} anchors: "
              f"{pearson:.3f}")
    else:
        print("  Pearson correlation: n/a (fewer than 2 comparable anchors)")

    for r in out_rows:
        assert r["coef_raw"] >= 0 and r["coef_deflated"] >= 0, (
            f"GATE FAILED (R2): negative coefficient for "
            f"{r['cms_specialty']} x {r['condition']}: {r}")
    recon = fetch(client, f"""
        SELECT cms_specialty,
               COUNT(*) AS row_count,
               SUM(coef_deflated * members_with_condition) AS predicted_total
        FROM `{OUT}`
        GROUP BY cms_specialty""")
    recon_map = {r["cms_specialty"]: r for r in recon}
    assert set(recon_map) == set(shipped_specs), (
        f"GATE FAILED (R2): loaded table specialties do not match the "
        f"shipped set: {sorted(set(recon_map) ^ set(shipped_specs))}")
    for spec in shipped_specs:
        r = recon_map[spec]
        assert r["row_count"] == p, (
            f"GATE FAILED (R2): {spec} has {r['row_count']} rows in the "
            f"loaded table, expected {p} (BASE_RATE + {p - 1} conditions)")
        predicted = float(r["predicted_total"])
        diff = abs(predicted - observed[spec]) / observed[spec]
        assert diff <= 0.005, (
            f"GATE FAILED (R4): {spec} loaded-table deflated predicted "
            f"total {predicted:,.0f} vs observed {observed[spec]:,} "
            f"differs by {100 * diff:.3f}% (over 0.5 percent)")
    seen_keys = {(r["cms_specialty"], r["condition"]) for r in out_rows}
    assert len(seen_keys) == len(out_rows), (
        f"GATE FAILED (R2): rate table key not unique locally: "
        f"{len(out_rows)} rows, {len(seen_keys)} distinct keys")
    keys = fetch(client, f"""
        SELECT COUNT(*) AS row_count,
               COUNT(DISTINCT CONCAT(cms_specialty, '|', condition))
                 AS distinct_keys,
               COUNTIF(cms_specialty IS NULL OR condition IS NULL)
                 AS null_keys
        FROM `{OUT}`""")[0]
    assert keys["row_count"] == len(out_rows) and \
        keys["row_count"] == keys["distinct_keys"] and \
        keys["null_keys"] == 0, (
        f"GATE FAILED (R2): loaded table keys do not match: {keys}")
    if degenerate:
        print(f"\ndegenerate specialties excluded from the table: "
              f"{degenerate}")
    print("\nALL GATES PASSED (R2 nonnegative coefficients + unique key + "
          "full row set per specialty, R4 loaded-table deflated totals "
          "within 0.5 percent; minimum specialty volume enforced by the "
          "fitted-specialty filter and printed above)")


if __name__ == "__main__":
    main()
