"""
10 - growth EDA   [PYTHON / read-only BigQuery report]

WHAT  : EDA behind the expected-growth slider defaults built in 11.
        Sections: (1) statewide monthly enrollment 2023-2025 per state,
        level and month-over-month percent change, January effects
        marked; (2) county-size distribution at 2025-12 in deciles and
        the count of counties under 1,000 members (these get shrunken
        defaults); (3) statewide per-band 2024 vs 2025 average members
        and yoy percent; (4) county x band December-to-December yoy
        dispersion (p10/p25/p50/p75/p90) with the count of cells beyond
        plus-minus 20 percent; (5) December 2024 to January 2025 percent
        drop per state; (6) closing keep-drop-engineer decisions.
SCOPE : R6 restated: footprint filter state_cd IN (FL, OH, AZ, IL)
        re-applied in every query. Age is structural here: every band in
        md1_enrollment_history is 60+ by construction; the membership
        extract carries no LOB column per the data dictionary, so no LOB
        re-check is possible on this input.
R3    : Attribution = MEMBER county (demand side). Enrollment is counted
        where members live; provider geography never enters.
GRAIN : stdout report only; no tables created.
INPUTS: md1_enrollment_history (built by notebook 06) - the only input.
OUTPUT: stdout report only.
Run   : python model_and_dashboard_v1/04_models/10_growth_eda.py
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

VOLATILE_PCT = 20.0
SMALL_COUNTY_MEMBERS = 1000


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


def main():
    client = cfg.client()

    print("=== 1. statewide monthly enrollment 2023-2025 "
          "(level, MoM pct, January marked) ===")
    series = fetch(client, f"""
        SELECT state_cd, month, SUM(members) AS members
        FROM `{ENR}`
        WHERE state_cd IN {FOOTPRINT}
        GROUP BY state_cd, month
        ORDER BY state_cd, month""")
    by_state = {}
    for r in series:
        by_state.setdefault(r["state_cd"], []).append(r)
    for st in sorted(by_state):
        print(f"\n  -- {st} --")
        prev = None
        for r in by_state[st]:
            mom = (100.0 * (r["members"] / prev - 1)) if prev else None
            mom_txt = f"{mom:+7.2f}%" if mom is not None else "      - "
            jan = "  <-- JAN" if r["month"].month == 1 else ""
            print(f"  {r['month']}  members={r['members']:>9,}  mom={mom_txt}{jan}")
            prev = r["members"]

    print("\n=== 2. county-size distribution at 2025-12 (deciles) ===")
    counties = fetch(client, f"""
        SELECT mbr_county_cd, ANY_VALUE(state_cd) AS state_cd,
               SUM(members) AS members
        FROM `{ENR}`
        WHERE state_cd IN {FOOTPRINT}
          AND month = DATE '2025-12-01'
        GROUP BY mbr_county_cd
        ORDER BY members DESC""")
    sizes = [r["members"] for r in counties]
    small = [r for r in counties if r["members"] < SMALL_COUNTY_MEMBERS]
    print(f"  counties with 2025-12 members: {len(counties)}")
    print(f"  min={min(sizes):,}  max={max(sizes):,}")
    for p in (10, 20, 30, 40, 50, 60, 70, 80, 90):
        print(f"  p{p:<2} = {percentile(sizes, p):>10,.0f}")
    print(f"  counties under {SMALL_COUNTY_MEMBERS:,} members: {len(small)} "
          f"(these lean on the state x band signal via shrinkage in 11)")

    print("\n=== 3. per-band trajectories statewide: 2024 vs 2025 average members ===")
    bands = fetch(client, f"""
        WITH bm AS (
          SELECT age_band, month, SUM(members) AS members
          FROM `{ENR}`
          WHERE state_cd IN {FOOTPRINT}
          GROUP BY age_band, month
        )
        SELECT age_band,
               AVG(IF(EXTRACT(YEAR FROM month) = 2024, members, NULL)) AS avg_2024,
               AVG(IF(EXTRACT(YEAR FROM month) = 2025, members, NULL)) AS avg_2025
        FROM bm
        GROUP BY age_band
        ORDER BY age_band""")
    for r in bands:
        yoy = 100.0 * (r["avg_2025"] / r["avg_2024"] - 1)
        print(f"  {r['age_band']:>6}  avg2024={r['avg_2024']:>10,.0f}  "
              f"avg2025={r['avg_2025']:>10,.0f}  yoy={yoy:+6.2f}%")

    print("\n=== 4. county x band Dec2024 -> Dec2025 yoy dispersion ===")
    cells = fetch(client, f"""
        SELECT mbr_county_cd, age_band,
               MAX(IF(month = DATE '2024-12-01', members, NULL)) AS m_dec24,
               MAX(IF(month = DATE '2025-12-01', members, NULL)) AS m_dec25
        FROM `{ENR}`
        WHERE state_cd IN {FOOTPRINT}
          AND month IN (DATE '2024-12-01', DATE '2025-12-01')
        GROUP BY mbr_county_cd, age_band""")
    changes = []
    for r in cells:
        if r["m_dec24"] is None:
            continue
        m25 = r["m_dec25"] if r["m_dec25"] is not None else 0
        changes.append(100.0 * (m25 / r["m_dec24"] - 1))
    volatile = [c for c in changes if abs(c) > VOLATILE_PCT]
    print(f"  county x band cells with Dec 2024 presence: {len(changes)}")
    for p in (10, 25, 50, 75, 90):
        print(f"  p{p:<2} = {percentile(changes, p):+8.2f}%")
    print(f"  cells beyond +-{VOLATILE_PCT:.0f}%: {len(volatile)} "
          f"({100.0 * len(volatile) / len(changes):.1f}% - volatile cells; "
          f"raw cell yoy alone is not a safe default)")

    print("\n=== 5. January discontinuity: Dec 2024 -> Jan 2025 per state ===")
    jan = fetch(client, f"""
        SELECT state_cd,
               SUM(IF(month = DATE '2024-12-01', members, 0)) AS dec_2024,
               SUM(IF(month = DATE '2025-01-01', members, 0)) AS jan_2025
        FROM `{ENR}`
        WHERE state_cd IN {FOOTPRINT}
          AND month IN (DATE '2024-12-01', DATE '2025-01-01')
        GROUP BY state_cd
        ORDER BY state_cd""")
    for r in jan:
        drop = 100.0 * (r["jan_2025"] / r["dec_2024"] - 1)
        print(f"  {r['state_cd']}  dec2024={r['dec_2024']:>9,}  "
              f"jan2025={r['jan_2025']:>9,}  change={drop:+6.2f}%  (plan-year churn)")

    print("\n=== 6. closing decisions (keep / drop / engineer) ===")
    print(f"""
  SIGNAL  : December-to-December yoy per county x band. December is the
            stable pre-churn read of a plan year; the year-apart pair
            nets out the January reset instead of averaging through it.
  KEEP    : cell-level Dec-to-Dec yoy (county x band) - the signal.
  KEEP    : state x band Dec-to-Dec yoy - the shrinkage target.
  DROP    : month-over-month trend - REJECTED because the January
            plan-year churn dominates it (section 5 shows the Dec->Jan
            break per state); a slope fit through that cliff is noise.
  DROP    : gender split (members_f / members_m) - not a slider
            dimension; the dashboard slides county x band totals.
  ENGINEER: shrinkage w = n / (n + k) toward the state x band yoy -
            NEEDED because {len(small)} counties sit under
            {SMALL_COUNTY_MEMBERS:,} members and section 4 shows
            {len(volatile)} of {len(changes)} cells beyond
            +-{VOLATILE_PCT:.0f}%; small cells must lean on the state.
  ENGINEER: clamp the final default to -20..+30 percent so no county
            starts a simulation at an implausible slider position.
  HONESTY : defaults may be flat or negative in some counties; this is a
            slider default, not a forecast product.""")

    months = fetch(client, f"""
        SELECT state_cd,
               COUNT(DISTINCT month) AS months_present,
               MIN(month) AS first_month,
               MAX(month) AS last_month
        FROM `{ENR}`
        WHERE state_cd IN {FOOTPRINT}
        GROUP BY state_cd
        ORDER BY state_cd""")
    assert len(months) == 4, (
        f"GATE FAILED (R6): expected 4 footprint states, found "
        f"{[r['state_cd'] for r in months]}")
    for r in months:
        assert r["months_present"] == 36, (
            f"GATE FAILED (R1): state {r['state_cd']} has "
            f"{r['months_present']} months, expected 36")
        assert str(r["first_month"]) == "2023-01-01" and \
            str(r["last_month"]) == "2025-12-01", (
            f"GATE FAILED (R1): state {r['state_cd']} window "
            f"{r['first_month']}..{r['last_month']}, expected "
            f"2023-01-01..2025-12-01")
    print("\nALL GATES PASSED (R1 36-month series per state, R6 four states)")


if __name__ == "__main__":
    main()
