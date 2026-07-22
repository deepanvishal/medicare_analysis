"""
12 - growth validation   [PYTHON / read-only BigQuery report]

WHAT  : Backtests the 11 method one year earlier: defaults computed from
        the Dec 2023 -> Dec 2024 signal (same shrinkage K and clamps as
        11) are compared against the actual Dec 2024 -> Dec 2025 change.
        Mirroring 11's construction, the state x band shrinkage target
        is computed over ALL cells present in Dec 2023 with absent Dec
        2024 counted as 0; scored cells are those with nonzero members
        in BOTH Dec 2023 and Dec 2024, and the actual uses
        members_2025_dec with absent cells counted as 0. All numbers
        are PERCENTAGE POINTS. Sections: (1) MAE and
        median absolute error per band and overall; (2) the same MAE for
        a zero-growth default and a state-band-average-only default
        (both unclamped), side by side, with a PASS/REVIEW verdict -
        PASS if the shrunken method's overall MAE is no worse than the
        best baseline plus 0.5 points; (3) calibration table of
        predicted-default deciles vs mean actual change; (4)
        leave-one-state-out repeat of the MAE comparison, where the
        held-out state's cells shrink toward the band yoy pooled from
        the other three states. Closed-form arithmetic, no sampling and
        no stochastic fit, so R8 needs no seed.
SCOPE : R6 restated: footprint filter state_cd IN (FL, OH, AZ, IL)
        re-applied. Age is structural (all bands 60+ by construction);
        the membership extract carries no LOB column per the data
        dictionary.
R3    : Attribution = MEMBER county (demand side).
GRAIN : stdout report only; no tables created.
INPUTS: md1_enrollment_history (built by notebook 06) - the only input.
OUTPUT: stdout report only.
Run   : python model_and_dashboard_v1/04_models/12_growth_validation.py
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

FOOTPRINT = "('FL', 'OH', 'AZ', 'IL')"

SHRINKAGE_K = 500
CLAMP_LO = -20.0
CLAMP_HI = 30.0
VERDICT_TOLERANCE = 0.5


def fetch(client, sql):
    return [dict(r) for r in client.query(sql).result()]


def clamp(x):
    return max(CLAMP_LO, min(CLAMP_HI, x))


def pct_change(new, old):
    return 100.0 * (new / old - 1)


def mae(errors):
    return sum(abs(e) for e in errors) / len(errors) if errors else None


def median(values):
    vals = sorted(values)
    if not vals:
        return None
    mid = len(vals) // 2
    if len(vals) % 2 == 1:
        return vals[mid]
    return (vals[mid - 1] + vals[mid]) / 2.0


def band_targets(cells):
    sums = {}
    for c in cells:
        key = (c["state_cd"], c["age_band"])
        agg = sums.setdefault(key, [0, 0])
        agg[0] += c["m_dec23"]
        agg[1] += c["m_dec24"]
    return {k: pct_change(v[1], v[0]) for k, v in sums.items()}


def pooled_band_targets(cells, held_out_state):
    sums = {}
    for c in cells:
        if c["state_cd"] == held_out_state:
            continue
        agg = sums.setdefault(c["age_band"], [0, 0])
        agg[0] += c["m_dec23"]
        agg[1] += c["m_dec24"]
    return {k: pct_change(v[1], v[0]) for k, v in sums.items()}


def predict(cell, target):
    signal = pct_change(cell["m_dec24"], cell["m_dec23"])
    n = cell["m_dec23"]
    w = n / (n + SHRINKAGE_K)
    return clamp(w * signal + (1 - w) * target)


def main():
    client = cfg.client()

    raw = fetch(client, f"""
        SELECT mbr_county_cd, ANY_VALUE(state_cd) AS state_cd, age_band,
               MAX(IF(month = DATE '2023-12-01', members, NULL)) AS m_dec23,
               MAX(IF(month = DATE '2024-12-01', members, NULL)) AS m_dec24,
               MAX(IF(month = DATE '2025-12-01', members, NULL)) AS m_dec25
        FROM `{ENR}`
        WHERE state_cd IN {FOOTPRINT}
          AND month IN (DATE '2023-12-01', DATE '2024-12-01',
                        DATE '2025-12-01')
        GROUP BY mbr_county_cd, age_band""")
    target_cells = [dict(r, m_dec24=(r["m_dec24"] or 0)) for r in raw
                    if r["m_dec23"]]
    universe = [r for r in raw if r["m_dec23"] and r["m_dec24"]]
    print(f"backtest universe: {len(universe)} county x band cells with "
          f"nonzero members in Dec 2023 and Dec 2024 (of {len(raw)} cells "
          f"overall); state x band targets built over all "
          f"{len(target_cells)} Dec 2023 cells with absent Dec 2024 counted "
          f"as 0, mirroring 11")

    targets = band_targets(target_cells)
    for c in universe:
        key = (c["state_cd"], c["age_band"])
        assert key in targets, (
            f"GATE FAILED (R2): no state x band backtest target for {key}")
        c["predicted"] = predict(c, targets[key])
        c["baseline_state"] = targets[key]
        m25 = c["m_dec25"] if c["m_dec25"] is not None else 0
        c["actual"] = pct_change(m25, c["m_dec24"])

    print("\n=== 1. method error per band and overall (percentage points) ===")
    bands = sorted({c["age_band"] for c in universe})
    for band in bands + ["OVERALL"]:
        cells = universe if band == "OVERALL" else \
            [c for c in universe if c["age_band"] == band]
        errs = [c["predicted"] - c["actual"] for c in cells]
        print(f"  {band:>8}  n={len(cells):>5}  mae={mae(errs):6.2f}  "
              f"median_ae={median([abs(e) for e in errs]):6.2f}")

    print("\n=== 2. method vs baselines (MAE, percentage points) ===")
    method_by_band = {}
    zero_by_band = {}
    state_by_band = {}
    for band in bands + ["OVERALL"]:
        cells = universe if band == "OVERALL" else \
            [c for c in universe if c["age_band"] == band]
        method_by_band[band] = mae([c["predicted"] - c["actual"] for c in cells])
        zero_by_band[band] = mae([0.0 - c["actual"] for c in cells])
        state_by_band[band] = mae([c["baseline_state"] - c["actual"]
                                   for c in cells])
        print(f"  {band:>8}  shrunken={method_by_band[band]:6.2f}  "
              f"zero_growth={zero_by_band[band]:6.2f}  "
              f"state_band_only={state_by_band[band]:6.2f}")
    best_baseline = min(zero_by_band["OVERALL"], state_by_band["OVERALL"])
    verdict = "PASS" if method_by_band["OVERALL"] <= \
        best_baseline + VERDICT_TOLERANCE else "REVIEW"
    print(f"\n  VERDICT: {verdict} (shrunken overall MAE "
          f"{method_by_band['OVERALL']:.2f} vs best baseline "
          f"{best_baseline:.2f} + {VERDICT_TOLERANCE} tolerance)")

    print("\n=== 3. calibration: predicted-default deciles vs mean actual ===")
    ranked = sorted(universe, key=lambda c: c["predicted"])
    for d in range(10):
        lo = d * len(ranked) // 10
        hi = (d + 1) * len(ranked) // 10
        chunk = ranked[lo:hi]
        if not chunk:
            continue
        mean_pred = sum(c["predicted"] for c in chunk) / len(chunk)
        mean_act = sum(c["actual"] for c in chunk) / len(chunk)
        print(f"  decile {d + 1:>2}  n={len(chunk):>5}  "
              f"mean_predicted={mean_pred:+7.2f}  mean_actual={mean_act:+7.2f}")

    print("\n=== 4. generalization: leave-one-state-out MAE comparison ===")
    for st in sorted({c["state_cd"] for c in universe}):
        held = [c for c in universe if c["state_cd"] == st]
        pooled = pooled_band_targets(target_cells, st)
        m_errs, z_errs, p_errs = [], [], []
        for c in held:
            assert c["age_band"] in pooled, (
                f"GATE FAILED (R2): no pooled target for band "
                f"{c['age_band']} with {st} held out")
            pred = predict(c, pooled[c["age_band"]])
            m_errs.append(pred - c["actual"])
            z_errs.append(0.0 - c["actual"])
            p_errs.append(pooled[c["age_band"]] - c["actual"])
        print(f"  held_out={st}  n={len(held):>5}  shrunken={mae(m_errs):6.2f}  "
              f"zero_growth={mae(z_errs):6.2f}  pooled_band_only={mae(p_errs):6.2f}")

    nulls = [c for c in universe if c["predicted"] is None]
    assert not nulls, (
        f"GATE FAILED (R2): {len(nulls)} cells with Dec 2023 and Dec 2024 "
        f"presence have null predictions")
    assert verdict in ("PASS", "REVIEW"), (
        f"GATE FAILED: verdict was not computed: {verdict}")
    print(f"\nALL GATES PASSED (verdict computed: {verdict}; R2 no null "
          f"predictions in the backtest universe)")


if __name__ == "__main__":
    main()
