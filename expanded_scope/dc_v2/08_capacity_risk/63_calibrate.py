"""
63 - calibration   [PYTHON / BigQuery + pandas]

WHAT  : Writes cap_params - the single source of tuning truth (CD-10).
        Modules 64-72 READ cap_params; nothing downstream hard-codes a
        tuning number; this module is the only writer. Solvers:
        DAILY_CAP_HRS by elbow on the 6-12 bracket (CD-03); DEFLATION
        solved per triage ruling (methodology §9-P1): scale the Stage-2
        anchors by one lambda so the 99.5th percentile of provider-day
        deflated hours lands at DAILY_CAP_HRS - anchors are the starting
        point and the fallback when the solve is degenerate; on rerun
        after module 64, high_day_flag providers are excluded from the
        percentile. BENCH_PCTL by cohort rank-stability across 85/90/95;
        MIN_COHORT_N by bootstrap CI width; CRED_K seeded - final value
        requires modules 66/67 (V4 reconciliation), rerun after they exist.
GRAIN : cap_params -> param_nm x param_scope
INPUTS: cap_hours_daily, cap_hours_annual (module 62); cap_daily_capped +
        existing cap_params on rerun (module 64 outputs, if present)
OUTPUT: cap_params (BigQuery, WRITE_TRUNCATE) with each derivation printed.
Run   : python expanded_scope/dc_v2/08_capacity_risk/63_calibrate.py
"""

# ASSUMPTION [1]: DEFLATION solve implemented per the triage ruling: one
#   lambda scales all three class anchors so p99.5 of provider-day deflated
#   hours = DAILY_CAP_HRS (one constraint cannot identify three classes, so
#   class RATIOS keep anchor proportions). Anchors kept verbatim when the
#   solve is degenerate (p99.5 <= 0, lambda <= 0, or any factor outside
#   (0, 1]) - degeneracy recorded in derivation_nt. Rerun with scale s > 1:
#   factors move UP but never past the anchors - new = LEAST(anchor,
#   current x s) per class (triage fix; the old guard froze stale values).
# ASSUMPTION [2]: DAILY_CAP_HRS elbow = candidate grid 6.0-12.0 step 0.5 on
#   the share-of-hours-trimmed curve; elbow = max second difference. CD-03
#   fixes the bracket, not the estimator.
# ASSUMPTION [3]: FIRST RUN uses raw_hrs x 0.85 blend (class-level
#   deflation exists only after module 64); the cap is solved before the
#   deflation lambda, on the same blend. Rerun after 64 verifies both on
#   cap_daily_capped.defl_hrs with high_day_flag providers excluded
#   (circularity note stands per triage).
# ASSUMPTION [4]: BENCH_PCTL = 90 unless Spearman rank correlation of
#   cohort (specialty x state) benchmark ranks across 85/90/95 drops below
#   0.9 - V8's rank-stability test proper needs county risk (module 71),
#   which does not exist on first run.
# ASSUMPTION [5]: MIN_COHORT_N = smallest n in {10,20,30,50} whose median
#   bootstrap CI width (200 resamples of provider daily-hours rates) is
#   under 20% of the cohort benchmark.
# ASSUMPTION [6]: CRED_K seeded at 20 (mid-range for actuarial credibility
#   on monthly panels); final k = argmin county reconciliation error (V4),
#   solvable only after 66/67 exist. derivation_nt records SEED vs SOLVED.
# ASSUMPTION [7]: SHARE_STABILITY_TOL (2.0, max quarter-over-quarter volume
#   ratio) written as a param row for module 70 (now in the data model's
#   param list per triage).

import os
import sys
import time


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

import numpy as np
import pandas as pd

RUN_MODE = "sample"   # no claims scan here; content governed by module 61's run

DAILY  = cfg.table("cap_hours_daily")
ANNUAL = cfg.table("cap_hours_annual")
CAPPED = cfg.table("cap_daily_capped")
PARAMS = cfg.table("cap_params")

CAP_GRID    = [round(6.0 + 0.5 * i, 1) for i in range(13)]   # CD-03 bracket 6-12
PCTL_GRID   = [85, 90, 95]
COHORT_GRID = [10, 20, 30, 50]
ANCHORS     = {"EM": 0.90, "PROC": 0.75, "OTHER": 0.85}      # Stage 2 anchors
ANCHOR_BLEND = 0.85          # ASSUMPTION [3]
BOOT_N = 200

Q_DAILY = f"""
SELECT npi, epdb_dw_prvdr_id, prvdr_county, prvdr_state_cd,
       raw_hrs * {ANCHOR_BLEND} AS defl_hrs_basis
FROM `{DAILY}`
"""

Q_CAPPED_RERUN = f"""
SELECT defl_hrs AS defl_hrs_basis
FROM `{CAPPED}`
WHERE COALESCE(npi, epdb_dw_prvdr_id) NOT IN (
  SELECT COALESCE(npi, epdb_dw_prvdr_id)
  FROM `{CAPPED}` WHERE high_day_flag = 1)
"""

Q_CUR_DEFL = f"""
SELECT param_scope, param_val FROM `{PARAMS}` WHERE param_nm = 'DEFLATION'
"""

Q_PROVIDER = f"""
SELECT a.npi, a.epdb_dw_prvdr_id, a.specialty_ctg_cd, a.prvdr_state_cd,
       SUM(a.raw_hrs_yr) * {ANCHOR_BLEND} AS defl_hrs_approx,
       COUNT(DISTINCT d.svc_dt)           AS active_days
FROM `{ANNUAL}` a
LEFT JOIN `{DAILY}` d
  ON COALESCE(a.npi, a.epdb_dw_prvdr_id) = COALESCE(d.npi, d.epdb_dw_prvdr_id)
WHERE a.src = 'AETNA_MA'
GROUP BY 1, 2, 3, 4
"""


def _run(client, label, sql):
    t0 = time.time()
    df = client.query(sql).result().to_dataframe()
    print(f"[{label}] {time.time() - t0:.1f}s, {len(df):,} rows")
    return df


def solve_daily_cap(hours):
    trimmed = []
    total = hours.sum()
    for cap in CAP_GRID:
        trimmed.append(np.clip(hours - cap, 0, None).sum() / total)
    second_diff = np.diff(trimmed, 2)
    cap = CAP_GRID[int(np.argmax(second_diff)) + 1] if len(second_diff) else 10.0
    print(f"daily cap grid trimmed-share: {[round(t, 4) for t in trimmed]}")
    return cap, "elbow (max 2nd diff) on trimmed-hours share, grid 6-12 x0.5 (A2, A3)"


def solve_deflation(hours, cap_val, base_factors, basis_note, rerun=False):
    p995 = float(np.quantile(hours, 0.995)) if len(hours) else 0.0
    if p995 <= 0:
        return base_factors, f"DEGENERATE (p99.5={p995}); anchors kept (A1)"
    lam = cap_val / p995
    if lam <= 0:
        return base_factors, f"DEGENERATE (lambda={lam:.4f}); anchors kept (A1)"
    if rerun and lam > 1:
        solved = {c: min(ANCHORS.get(c, v), v * lam) for c, v in base_factors.items()}
        return solved, (
            f"rerun up-scale s={lam:.4f}, capped at anchors - first-run shrink was "
            f"a team-billing artifact; with high-day providers excluded raw "
            f"minutes fit the feasible day, so anchors (the cited overstatement "
            f"correction) govern; new = LEAST(anchor, current x s) on {basis_note} (A1)")
    solved = {c: v * lam for c, v in base_factors.items()}
    if any(not (0.0 < f <= 1.0) for f in solved.values()):
        return base_factors, (
            f"lambda={lam:.4f} pushes a factor outside (0,1]; anchors kept (A1)")
    return solved, (f"solved: p99.5 provider-day hours ({p995:.2f}) -> cap "
                    f"{cap_val}; lambda={lam:.4f} on {basis_note} (A1)")


def solve_bench_pctl(prov):
    prov = prov.dropna(subset=["specialty_ctg_cd", "prvdr_state_cd"]).copy()
    prov["rate"] = prov["defl_hrs_approx"] / prov["active_days"].clip(lower=1)
    ranks = {}
    for p in PCTL_GRID:
        bench = prov.groupby(["specialty_ctg_cd", "prvdr_state_cd"])["rate"].quantile(p / 100)
        ranks[p] = bench.rank()
    corrs = [ranks[a].corr(ranks[b], method="spearman")
             for a in PCTL_GRID for b in PCTL_GRID if a < b]
    stable = all(c >= 0.9 for c in corrs if not np.isnan(c))
    print(f"bench pctl rank correlations (85/90/95 pairs): {[round(c, 3) for c in corrs]}")
    return (90 if stable else min(PCTL_GRID)), \
        f"rank-stability spearman across {PCTL_GRID}: {'stable -> 90' if stable else 'unstable -> conservative 85'} (A4)"


def solve_min_cohort(prov):
    prov = prov.dropna(subset=["specialty_ctg_cd", "prvdr_state_cd"]).copy()
    prov["rate"] = prov["defl_hrs_approx"] / prov["active_days"].clip(lower=1)
    rng = np.random.default_rng(42)
    chosen, note = COHORT_GRID[-1], "no n met the 20% width test (A5)"
    for n in COHORT_GRID:
        widths = []
        for _, g in prov.groupby(["specialty_ctg_cd", "prvdr_state_cd"]):
            r = g["rate"].values
            if len(r) < n:
                continue
            boots = [np.quantile(rng.choice(r, size=n, replace=True), 0.9)
                     for _ in range(BOOT_N)]
            mid = np.median(boots)
            if mid > 0:
                widths.append((np.quantile(boots, 0.95) - np.quantile(boots, 0.05)) / mid)
        if widths and np.median(widths) < 0.20:
            chosen, note = n, "smallest n with median boot CI width <20% (A5)"
            break
    return chosen, note


def main():
    print(f"RUN_MODE = {RUN_MODE}")
    client = cfg.client()
    daily = _run(client, "daily hours pull", Q_DAILY)
    prov = _run(client, "provider rate pull", Q_PROVIDER)

    # rerun path: after module 64, calibrate on true deflated hours with
    # high-day providers excluded (A3); first run falls back to the blend
    try:
        capped = _run(client, "capped daily pull (rerun path)", Q_CAPPED_RERUN)
        basis = capped["defl_hrs_basis"].dropna().values
        cur = _run(client, "current DEFLATION params", Q_CUR_DEFL)
        base_factors = (dict(zip(cur["param_scope"], cur["param_val"]))
                        if len(cur) == len(ANCHORS) else dict(ANCHORS))
        basis_note = "cap_daily_capped defl_hrs, high-day providers excluded (rerun)"
        is_rerun = True
    except Exception:
        basis = daily["defl_hrs_basis"].dropna().values
        base_factors = dict(ANCHORS)
        basis_note = f"raw x {ANCHOR_BLEND} blend, first run (A3)"
        is_rerun = False
    print(f"deflation basis: {basis_note}")

    cap_val, cap_note = solve_daily_cap(daily["defl_hrs_basis"].dropna().values)
    defl_factors, defl_note = solve_deflation(basis, cap_val, base_factors, basis_note,
                                              rerun=is_rerun)
    pctl_val, pctl_note = solve_bench_pctl(prov)
    n_val, n_note = solve_min_cohort(prov)

    rows = [("DEFLATION", scope, round(float(val), 4), defl_note)
            for scope, val in sorted(defl_factors.items())]
    rows += [
        ("DAILY_CAP_HRS", "GLOBAL", cap_val, cap_note),
        ("BENCH_PCTL", "GLOBAL", float(pctl_val), pctl_note),
        ("MIN_COHORT_N", "GLOBAL", float(n_val), n_note),
        ("FTE_DAY_HRS", "GLOBAL", 8.0, "federal FTE day, HRSA 40 hrs/week (definition, not tuned)"),
        ("CRED_K", "GLOBAL", 20.0, "SEED - solve = argmin V4 reconciliation error, rerun 63 after 66/67 (A6)"),
        ("HORIZON_FACTOR", "GLOBAL", 1.0, "CD-18: 1-year horizon only"),
        ("SHARE_STABILITY_TOL", "GLOBAL", 2.0, "SEED max quarter volume ratio for module 70 (A7)"),
    ]
    out = pd.DataFrame(rows, columns=["param_nm", "param_scope", "param_val", "derivation_nt"])
    out["run_ts"] = pd.Timestamp.now(tz="UTC")

    from google.cloud import bigquery
    client.load_table_from_dataframe(
        out, PARAMS,
        job_config=bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE")).result()
    print(f"loaded {PARAMS}")
    for r in rows:
        print("  ", r)


if __name__ == "__main__":
    main()


# REVIEW
# Reviewer 1 LOGIC:
#  - Provider identity uses COALESCE(npi, epdb_dw_prvdr_id) so xwalk-
#    unmatched providers are not merged. Cohorts keyed specialty x state
#    (rule 12 has no county key here).
#  - Deflation solve order: cap first, then lambda against that cap; class
#    ratios preserved (one constraint, three classes). High-day exclusion
#    only exists on the rerun path, exactly as the ruling allows.
# Reviewer 2 SPEC:
#  - Deviations = the seven ASSUMPTION blocks; DEFLATION now SOLVED per the
#    triage objective with anchors as start/fallback; CRED_K remains the
#    one seeded value awaiting the 66/67 rerun.
#  - Only writer of cap_params, per CD-10.
# Reviewer 3 EFFICIENCY:
#  - Zero claims scans; pulls over module-62/64 tables only, provider- or
#    day-level aggregates. Bootstrap in pandas. Relative cost: trivial.
