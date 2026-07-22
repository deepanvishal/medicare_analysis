"""
15 - visit-split validation   [PYTHON / read-only BigQuery report]

WHAT  : Validation of the 14 visit-splitting fit (md1_visitsplit_rates).
        Context from the executed 14 run: 56 kept conditions, 39 fitted
        specialties, deflation mostly 1.0 (a few 0.78-0.89 where
        nonnegativity binds), r2 high for condition-driven specialties
        (Primary Care 0.42, Cardiology 0.39, Nephrology 0.35) and near
        zero for lifestyle/mechanical ones (Chiropractic, Allergy,
        Plastic) - expected. The 14 anchor print compared solo TOTAL
        rates against INCREMENTAL coefficients, an apples-to-oranges
        construction; section 1 here computes the corrected comparison
        (solo rate vs base_rate + coef_deflated).
        Sections: (1) corrected anchor check with Pearson and a
        PASS/REVIEW verdict at 0.7; (2) aggregate reconstruction -
        predicted 2025 visits per county x fitted specialty from county
        member counts and condition counts times the rate table,
        compared to actual bridged visits, with overall weighted APE,
        p50/p75/p90/p95 across cells with at least 1,000 actual visits,
        and the 10 worst cells (PASS if WAPE under 10 percent and p90
        under 25); (3) volume-weighted mean signed percent error by
        specialty (top 15 by volume) and by state; (4) refit-free
        split-half stability - section 2's WAPE recomputed for members
        with even vs odd FARM_FINGERPRINT(member_id) parity (PASS if the
        two differ by under 3 points); (5) base-rate share of predicted
        visits per specialty, informational; (6) closing verdicts and
        the go/no-go for notebook 08 (go if sections 2 and 4 PASS; the
        anchor check is advisory). Everything is deterministic - the
        split uses a hash, not RNG - so R8 needs no seed. All BigQuery
        work is aggregation; no member-level frame is downloaded.
        Prediction exposure assigns each member the county of their last
        observed 2025 month; actuals use each visit's own member county
        (both demand-side attributions; movers create small noise).
SCOPE : R6 restated ASYMMETRICALLY by design: the prediction exposure
        re-applies age_nbr >= 60 and the FL/OH/AZ/IL footprint on the
        membership spine (ms_ref_county join with LPAD defense); the
        actuals leg re-applies only the footprint on each visit's
        member county, because the R4 gate must tie the cells back to
        the raw md1_visits_base bridged total - age and the CP/ME LOB
        rule are inherited from that table's own R6 build filter. The
        residual scope gap (non-spine members' visits in footprint
        counties vs spine members' visits in unjoinable counties) stays
        inside the measured error rather than being filtered away, and
        the excluded-visit channels are printed and decomposed in the
        R4 gate.
R3    : Attribution = MEMBER county (demand side) on both the exposure
        counts and the actuals; provider geography never enters.
GRAIN : stdout report only; no tables created.
INPUTS: md1_visitsplit_rates (built by 14), md1_condition_flags,
        md1_member_base, md1_visits_base (batch A2 outputs),
        cfg.base("ref_specialty_crosswalk"), cfg.table("ref_county")
OUTPUT: stdout report only.
Run   : python model_and_dashboard_v1/04_models/15_visitsplit_validation.py
        Requires numpy locally (installed with scipy, per the 14
        dependency). Run after 14; independent of 07,
        09 and the 10-12 growth trio. Notebook 08 waits on this
        notebook's go/no-go.
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

RATES  = cfg.src("md1_visitsplit_rates")
VISITS = cfg.src("md1_visits_base")
CFLAGS = cfg.src("md1_condition_flags")
MBASE  = cfg.src("md1_member_base")
XWALK  = cfg.base("ref_specialty_crosswalk")
CTY    = cfg.table("ref_county")

FOOTPRINT = "('FL', 'OH', 'AZ', 'IL')"

STUDY_YEAR = 2025
ANCHOR_HCCS = 15
SMALL_CELL_MEMBERS = 1000
CELL_MIN_VISITS = 1000
ANCHOR_PASS_CORR = 0.7
WAPE_PASS = 10.0
P90_PASS = 25.0
SPLIT_PASS_POINTS = 3.0

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


def percentile(values, p):
    vals = sorted(values)
    if not vals:
        return None
    k = (len(vals) - 1) * p / 100.0
    f = int(k)
    c = min(f + 1, len(vals) - 1)
    return vals[f] + (vals[c] - vals[f]) * (k - f)


def wape(pred, act):
    denom = float(act.sum())
    return 100.0 * float(np.abs(pred - act).sum()) / denom if denom else None


def main():
    client = cfg.client()

    rate_rows = fetch(client, f"SELECT * FROM `{RATES}`")
    specs = sorted({r["cms_specialty"] for r in rate_rows})
    kept = sorted({r["condition"] for r in rate_rows}
                  - {"BASE_RATE", "OTHER_CONDITION"})
    indicators = kept + ["OTHER_CONDITION"]
    p = len(indicators) + 1
    sidx = {s: j for j, s in enumerate(specs)}
    iidx = {c: i for i, c in enumerate(indicators)}
    coef = np.zeros((p, len(specs)))
    rows_per_spec = {}
    for r in rate_rows:
        assert r["coef_deflated"] is not None, (
            f"GATE FAILED (R2): null coef_deflated in md1_visitsplit_rates "
            f"for {r['cms_specialty']} x {r['condition']}")
        j = sidx[r["cms_specialty"]]
        rows_per_spec[r["cms_specialty"]] = \
            rows_per_spec.get(r["cms_specialty"], 0) + 1
        if r["condition"] == "BASE_RATE":
            coef[p - 1, j] = float(r["coef_deflated"])
        else:
            coef[iidx[r["condition"]], j] = float(r["coef_deflated"])
    short_specs = sorted(s for s in specs if rows_per_spec.get(s, 0) != p)
    assert not short_specs, (
        f"GATE FAILED (R2): specialties without a full {p}-row coefficient "
        f"set in md1_visitsplit_rates: {short_specs}")
    print(f"rate table: {len(specs)} fitted specialties, {len(kept)} kept "
          f"conditions + OTHER_CONDITION + BASE_RATE")

    kept_sql = ", ".join(f"'{c}'" for c in kept)

    print(f"\n=== 1. corrected anchor check (solo rate vs base_rate + "
          f"coef_deflated) ===")
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
    pairs = []
    for r in anchors:
        if r["cohort_members"] < SMALL_CELL_MEMBERS:
            print(f"  HCC {r['hcc']:>5}  n={r['cohort_members']:>7,}  "
                  f"[SMALL-CELL] excluded per notebook 13's decision")
            continue
        spec = r["cms_specialty"]
        solo_rate = float(r["solo_rate"])
        if spec not in sidx or r["hcc"] not in iidx:
            print(f"  HCC {r['hcc']:>5}  n={r['cohort_members']:>7,}  "
                  f"top_spec={spec:<30}  solo_rate={solo_rate:6.2f}  "
                  f"predicted=n/a (pooled condition or unfitted specialty)")
            continue
        predicted = float(coef[p - 1, sidx[spec]] +
                          coef[iidx[r["hcc"]], sidx[spec]])
        pairs.append({"hcc": r["hcc"], "spec": spec, "solo": solo_rate,
                      "pred": predicted,
                      "abs_diff": abs(solo_rate - predicted)})
        print(f"  HCC {r['hcc']:>5}  n={r['cohort_members']:>7,}  "
              f"top_spec={spec:<30}  solo_rate={solo_rate:6.2f}  "
              f"base+coef={predicted:6.2f}")
    anchor_corr = None
    if len(pairs) >= 2:
        anchor_corr = float(np.corrcoef([x["solo"] for x in pairs],
                                        [x["pred"] for x in pairs])[0, 1])
    anchor_verdict = "PASS" if anchor_corr is not None and \
        anchor_corr >= ANCHOR_PASS_CORR else "REVIEW"
    print(f"  Pearson correlation across {len(pairs)} anchors: "
          f"{'n/a' if anchor_corr is None else format(anchor_corr, '.3f')}")
    print(f"  ANCHOR VERDICT: {anchor_verdict} "
          f"(threshold {ANCHOR_PASS_CORR})")
    if anchor_verdict == "REVIEW":
        for x in sorted(pairs, key=lambda v: -v["abs_diff"])[:3]:
            print(f"    worst mismatch: HCC {x['hcc']} x {x['spec']} "
                  f"solo={x['solo']:.2f} vs predicted={x['pred']:.2f}")

    exposure = fetch(client, f"""
        WITH spine_cty AS (
          SELECT mb.member_id,
                 LPAD(TRIM(CAST(mb.mbr_county_cd AS STRING)), 5, '0')
                   AS county_fips,
                 rc.state_cd,
                 MOD(ABS(FARM_FINGERPRINT(CAST(mb.member_id AS STRING))), 2)
                   AS half
          FROM `{MBASE}` mb
          JOIN `{CTY}` rc
            ON LPAD(TRIM(CAST(mb.mbr_county_cd AS STRING)), 5, '0')
               = rc.county_fips
          WHERE rc.state_cd IN {FOOTPRINT}
            AND mb.age_nbr >= 60
            AND EXTRACT(YEAR FROM mb.month) = {STUDY_YEAR}
          QUALIFY ROW_NUMBER() OVER (PARTITION BY mb.member_id
                                     ORDER BY mb.month DESC) = 1
        ),
        mc AS (
          SELECT DISTINCT f.member_id,
            CASE WHEN CAST(f.HCC_v24 AS STRING) IN ({kept_sql})
                 THEN CAST(f.HCC_v24 AS STRING)
                 ELSE 'OTHER_CONDITION' END AS indicator
          FROM `{CFLAGS}` f
          JOIN spine_cty s ON f.member_id = s.member_id
          WHERE f.year = {STUDY_YEAR}
        )
        SELECT s.county_fips, s.state_cd, s.half, m.indicator,
               COUNT(DISTINCT s.member_id) AS member_count
        FROM spine_cty s
        JOIN mc m ON s.member_id = m.member_id
        GROUP BY s.county_fips, s.state_cd, s.half, m.indicator
        UNION ALL
        SELECT county_fips, state_cd, half, '_MEMBERS_', COUNT(*)
        FROM spine_cty
        GROUP BY county_fips, state_cd, half""")

    actual_rows = fetch(client, f"""
        WITH bridged AS (
          SELECT v.member_id,
                 LPAD(TRIM(CAST(v.mbr_county_cd AS STRING)), 5, '0')
                   AS county_fips,
                 cw.cms_specialty
          FROM `{VISITS}` v
          JOIN `{XWALK}` cw
            ON TRIM(CAST(v.specialty_ctg_cd AS STRING)) = TRIM(CAST(cw.aetna_cd AS STRING))
          WHERE EXTRACT(YEAR FROM v.month) = {STUDY_YEAR}
        )
        SELECT b.county_fips, rc.state_cd,
               MOD(ABS(FARM_FINGERPRINT(CAST(b.member_id AS STRING))), 2)
                 AS half,
               b.cms_specialty, COUNT(*) AS visit_count
        FROM bridged b
        JOIN `{CTY}` rc ON b.county_fips = rc.county_fips
        WHERE rc.state_cd IN {FOOTPRINT}
        GROUP BY b.county_fips, rc.state_cd, half, b.cms_specialty
        UNION ALL
        SELECT '_TOTAL_', '_ALL_', -1, '_ALL_', COUNT(*)
        FROM bridged
        UNION ALL
        SELECT '_TOTAL_NO_COUNTY_', '_ALL_', -1, '_ALL_', COUNT(*)
        FROM bridged b
        LEFT JOIN `{CTY}` rc ON b.county_fips = rc.county_fips
        WHERE rc.county_fips IS NULL
           OR rc.state_cd NOT IN {FOOTPRINT}""")

    total_bridged = next(r["visit_count"] for r in actual_rows
                         if r["county_fips"] == "_TOTAL_")
    no_county_visits = next(r["visit_count"] for r in actual_rows
                            if r["county_fips"] == "_TOTAL_NO_COUNTY_")
    county_state = {}
    x_half = {}
    for r in exposure:
        key = (r["county_fips"], r["half"])
        county_state[r["county_fips"]] = r["state_cd"]
        vec = x_half.setdefault(key, np.zeros(p))
        if r["indicator"] == "_MEMBERS_":
            vec[p - 1] = r["member_count"]
        else:
            vec[iidx[r["indicator"]]] = r["member_count"]
    a_half = {}
    unfitted_visits = 0
    for r in actual_rows:
        if r["county_fips"] in ("_TOTAL_", "_TOTAL_NO_COUNTY_"):
            continue
        county_state.setdefault(r["county_fips"], r["state_cd"])
        if r["cms_specialty"] not in sidx:
            unfitted_visits += r["visit_count"]
            continue
        key = (r["county_fips"], r["half"])
        vec = a_half.setdefault(key, np.zeros(len(specs)))
        vec[sidx[r["cms_specialty"]]] = r["visit_count"]

    counties = sorted(county_state)
    n_cty = len(counties)
    cidx = {c: i for i, c in enumerate(counties)}
    x_even = np.zeros((n_cty, p))
    x_odd = np.zeros((n_cty, p))
    a_even = np.zeros((n_cty, len(specs)))
    a_odd = np.zeros((n_cty, len(specs)))
    for (cty, half), vec in x_half.items():
        (x_even if half == 0 else x_odd)[cidx[cty]] = vec
    for (cty, half), vec in a_half.items():
        (a_even if half == 0 else a_odd)[cidx[cty]] = vec
    pred_even = x_even @ coef
    pred_odd = x_odd @ coef
    pred = pred_even + pred_odd
    act = a_even + a_odd

    print(f"\n=== 2. aggregate reconstruction: county x fitted specialty, "
          f"{STUDY_YEAR} ===")
    print(f"  counties: {n_cty}; cells: {n_cty * len(specs):,}; "
          f"actual bridged visits in cells: {act.sum():,.0f}")
    print(f"  excluded from cells (R7): unfitted-specialty visits "
          f"{unfitted_visits:,}; county-join/footprint failures "
          f"{no_county_visits:,}")
    overall_wape = wape(pred, act)
    apes = []
    named = []
    for i, cty in enumerate(counties):
        for s, j in sidx.items():
            a = act[i, j]
            if a >= CELL_MIN_VISITS:
                ape = 100.0 * abs(pred[i, j] - a) / a
                apes.append(ape)
                named.append((ape, cty, county_state[cty], s, a,
                              pred[i, j]))
    print(f"  overall weighted APE: "
          f"{'n/a' if overall_wape is None else format(overall_wape, '.2f') + '%'}")
    print(f"  cell APE distribution (cells with >= {CELL_MIN_VISITS:,} "
          f"actual visits, n={len(apes):,}):")
    for q in (50, 75, 90, 95):
        v = percentile(apes, q)
        print(f"    p{q} = {'n/a' if v is None else format(v, '6.2f') + '%'}")
    p90 = percentile(apes, 90)
    print(f"  10 worst cells:")
    for ape, cty, st, s, a, pr in sorted(named, reverse=True)[:10]:
        print(f"    {cty} ({st})  {s:<35}  actual={a:>9,.0f}  "
              f"pred={pr:>10,.0f}  ape={ape:6.1f}%")
    recon_verdict = "PASS" if overall_wape is not None and \
        overall_wape < WAPE_PASS and p90 is not None and p90 < P90_PASS \
        else "REVIEW"
    print(f"  RECONSTRUCTION VERDICT: {recon_verdict} "
          f"(WAPE under {WAPE_PASS:.0f}% and p90 under {P90_PASS:.0f}%)")

    print(f"\n=== 3. where the error lives (volume-weighted mean signed "
          f"percent error) ===")
    spec_actual = act.sum(axis=0)
    spec_pred = pred.sum(axis=0)
    top_specs = sorted(sidx, key=lambda s: -spec_actual[sidx[s]])[:15]
    for s in top_specs:
        j = sidx[s]
        signed = 100.0 * (spec_pred[j] - spec_actual[j]) / spec_actual[j] \
            if spec_actual[j] else None
        txt = "n/a" if signed is None else f"{signed:+6.2f}%"
        print(f"  spec  {s:<35}  actual={spec_actual[j]:>11,.0f}  "
              f"signed_err={txt}")
    states = sorted({st for st in county_state.values()})
    for st in states:
        rows_in = [cidx[c] for c in counties if county_state[c] == st]
        a_st = act[rows_in].sum()
        p_st = pred[rows_in].sum()
        signed = 100.0 * (p_st - a_st) / a_st if a_st else None
        txt = "n/a" if signed is None else f"{signed:+6.2f}%"
        print(f"  state {st:<35}  actual={a_st:>11,.0f}  signed_err={txt}")

    print(f"\n=== 4. stability: split-half WAPE (FARM_FINGERPRINT parity, "
          f"refit-free) ===")
    wape_even = wape(pred_even, a_even)
    wape_odd = wape(pred_odd, a_odd)
    if wape_even is None or wape_odd is None:
        split_verdict = "REVIEW"
        print("  even/odd half WAPE: n/a (a half has zero actual visits)")
    else:
        split_diff = abs(wape_even - wape_odd)
        print(f"  even half WAPE: {wape_even:.2f}%")
        print(f"  odd half WAPE : {wape_odd:.2f}%")
        print(f"  difference    : {split_diff:.2f} points")
        split_verdict = "PASS" if split_diff < SPLIT_PASS_POINTS else "REVIEW"
    print(f"  STABILITY VERDICT: {split_verdict} "
          f"(threshold {SPLIT_PASS_POINTS} points)")

    print(f"\n=== 5. base-rate share of predicted visits (top 15 by "
          f"volume, informational) ===")
    n_total = x_even[:, p - 1].sum() + x_odd[:, p - 1].sum()
    for s in top_specs:
        j = sidx[s]
        base_visits = n_total * coef[p - 1, j]
        share = 100.0 * base_visits / spec_pred[j] if spec_pred[j] else None
        base_txt = "  n/a" if share is None else f"{share:5.1f}%"
        cond_txt = "  n/a" if share is None else f"{100 - share:5.1f}%"
        print(f"  {s:<35}  predicted={spec_pred[j]:>11,.0f}  "
              f"base_rate_share={base_txt}  condition_share={cond_txt}")

    covered = {s for s in sidx if spec_actual[sidx[s]] > 0}
    missing = sorted(set(specs) - covered)
    assert not missing, (
        f"GATE FAILED (R1): fitted specialties absent from the section 2 "
        f"actuals: {missing}")
    assert np.isfinite(pred).all() and np.isfinite(coef).all(), (
        "GATE FAILED (R2): null or non-finite predictions in the "
        "reconstruction matrix")
    assert total_bridged > 0, (
        f"GATE FAILED (R4): md1_visits_base has no bridged {STUDY_YEAR} "
        f"visits")
    cell_total = float(act.sum())
    residual = total_bridged - cell_total - unfitted_visits - no_county_visits
    r4_gap = (total_bridged - cell_total) / total_bridged
    assert r4_gap <= 0.005, (
        f"GATE FAILED (R4): section 2 cells cover {cell_total:,.0f} of the "
        f"md1_visits_base bridged {STUDY_YEAR} total {total_bridged:,} "
        f"(gap {100 * r4_gap:.3f}%, over 0.5 percent). Decomposition: "
        f"unfitted-specialty visits {unfitted_visits:,}; "
        f"county-join/footprint failures {no_county_visits:,}; "
        f"residual {residual:,.0f}")
    print(f"\nALL GATES PASSED (R1 all fitted specialties covered, R2 "
          f"finite predictions + full coefficient sets, R4 cells cover the "
          f"bridged total {total_bridged:,} within 0.5 percent - gap "
          f"{100 * r4_gap:.3f}%: unfitted specs {unfitted_visits:,}, "
          f"county failures {no_county_visits:,})")

    print(f"\n=== 6. closing ===")
    print(f"  anchor check   : {anchor_verdict} (advisory)")
    print(f"  reconstruction : {recon_verdict}")
    print(f"  stability      : {split_verdict}")
    go = recon_verdict == "PASS" and split_verdict == "PASS"
    print(f"  NOTEBOOK 08    : {'GO' if go else 'NO-GO'} "
          f"(go if sections 2 and 4 PASS; anchor is advisory; gates "
          f"above already passed)")


if __name__ == "__main__":
    main()
