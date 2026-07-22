"""
13 - visit-split EDA   [PYTHON / read-only BigQuery report]

WHAT  : EDA for the visit-splitting model (14): how multi-condition
        members' visits distribute across their conditions. Grain of
        study = member x year 2025 in scope; the member universe is the
        2025 membership spine, so members with no visits count in every
        denominator. Sections: (1) members by number of distinct 2025
        conditions (0,1,2,3,4,5+) with total visits and visits per
        member - the 0-condition group defines the base rate; (2) for
        the top 12 specialties by 2025 visit volume, visits per member
        for members with 0/1/2/3+ conditions; (3) members with EXACTLY
        one condition for the 15 highest-prevalence HCCs: visits per
        member by specialty (top 5 each) - the cleanest read of a
        condition's specialty signature and the natural anchor for the
        splitting model; (4) the 10 most common condition pairs among
        exactly-two-condition members: visits per member vs the sum of
        the two exactly-one-condition rates, printed as a ratio; (5)
        closing keep-drop-engineer decisions. Cells under 1,000 members
        are marked SMALL-CELL in the print.
SCOPE : R6 restated: age_nbr >= 60 and footprint states FL/OH/AZ/IL
        re-applied on the membership spine via ms_ref_county with LPAD
        defense; the CP/ME LOB rule binds inside md1_visits_base and
        md1_condition_flags (claims-built), as the membership extract
        carries no LOB column per the data dictionary.
R3    : Attribution = member-level study; the member-county join is
        scope re-assertion only and no geography is reported.
GRAIN : stdout report only; no tables created.
INPUTS: md1_visits_base, md1_condition_flags, md1_member_base (batch A2
        outputs), cfg.table("ref_county")
OUTPUT: stdout report only.
Run   : python model_and_dashboard_v1/04_models/13_visitsplit_eda.py
        Runnable once batch A2 tables exist; independent of 07 and 09
        and of the 10-12 growth trio.
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

VISITS = cfg.src("md1_visits_base")
CFLAGS = cfg.src("md1_condition_flags")
MBASE  = cfg.src("md1_member_base")
CTY    = cfg.table("ref_county")

FOOTPRINT = "('FL', 'OH', 'AZ', 'IL')"

STUDY_YEAR = 2025
SMALL_CELL_MEMBERS = 1000
TOP_SPECIALTIES = 12
TOP_HCCS = 15
TOP_PAIRS = 10

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

VIS = f"""vis AS (
  SELECT member_id, COUNT(*) AS visit_count
  FROM `{VISITS}`
  WHERE EXTRACT(YEAR FROM month) = {STUDY_YEAR}
  GROUP BY member_id
)"""


def fetch(client, sql):
    return [dict(r) for r in client.query(sql).result()]


def small_flag(member_count):
    return " [SMALL-CELL]" if member_count < SMALL_CELL_MEMBERS else ""


def median(values):
    vals = sorted(values)
    if not vals:
        return None
    mid = len(vals) // 2
    if len(vals) % 2 == 1:
        return vals[mid]
    return (vals[mid - 1] + vals[mid]) / 2.0


def main():
    client = cfg.client()
    studied_cells = []

    joins = fetch(client, f"""
        WITH {SPINE}, {CONDS}, {VIS}
        SELECT
          (SELECT COUNT(*) FROM spine) AS spine_members,
          (SELECT COUNT(*) FROM conds) AS cond_members,
          (SELECT COUNT(*) FROM conds c
           JOIN spine s ON c.member_id = s.member_id) AS cond_members_in_spine,
          (SELECT COUNT(*) FROM vis) AS visit_members,
          (SELECT COUNT(*) FROM vis v
           JOIN spine s ON v.member_id = s.member_id) AS visit_members_in_spine
        """)[0]
    print(f"=== join transparency (R7) ===")
    print(f"  {STUDY_YEAR} membership spine members : {joins['spine_members']:,}")
    print(f"  members with conditions : {joins['cond_members']:,} "
          f"({joins['cond_members_in_spine']:,} in spine)")
    print(f"  members with visits     : {joins['visit_members']:,} "
          f"({joins['visit_members_in_spine']:,} in spine)")

    print(f"\n=== 1. members by number of distinct {STUDY_YEAR} conditions ===")
    buckets = fetch(client, f"""
        WITH {SPINE}, {CONDS}, {VIS}
        SELECT
          CASE WHEN c.n_cond IS NULL OR c.n_cond = 0 THEN '0'
               WHEN c.n_cond >= 5 THEN '5+'
               ELSE CAST(c.n_cond AS STRING) END AS cond_bucket,
          COUNT(*) AS member_count,
          SUM(COALESCE(v.visit_count, 0)) AS total_visits,
          SAFE_DIVIDE(SUM(COALESCE(v.visit_count, 0)), COUNT(*))
            AS visits_per_member
        FROM spine s
        LEFT JOIN conds c ON s.member_id = c.member_id
        LEFT JOIN vis v ON s.member_id = v.member_id
        GROUP BY cond_bucket
        ORDER BY cond_bucket""")
    base_rate = None
    for r in buckets:
        if r["cond_bucket"] == "0":
            base_rate = float(r["visits_per_member"])
        line = (f"  conditions={r['cond_bucket']:>2}  "
                f"members={r['member_count']:>9,}  "
                f"visits={r['total_visits']:>10,}  "
                f"visits_per_member={float(r['visits_per_member']):6.2f}"
                f"{small_flag(r['member_count'])}")
        print(line)
        studied_cells.append({"label": f"bucket {r['cond_bucket']}",
                              "members": r["member_count"],
                              "flagged": "[SMALL-CELL]" in line})
    base_txt = f"{base_rate:.2f}" if base_rate is not None else "n/a"
    print(f"  base rate concept: the 0-condition group's "
          f"{base_txt} visits per member")

    print(f"\n=== 2. top {TOP_SPECIALTIES} specialties: visits per member by "
          f"condition count (0/1/2/3+) ===")
    spec_rows = fetch(client, f"""
        WITH {SPINE}, {CONDS},
        bucketed AS (
          SELECT s.member_id,
                 CASE WHEN c.n_cond IS NULL OR c.n_cond = 0 THEN '0'
                      WHEN c.n_cond >= 3 THEN '3+'
                      ELSE CAST(c.n_cond AS STRING) END AS cond_bucket
          FROM spine s
          LEFT JOIN conds c ON s.member_id = c.member_id
        ),
        bucket_members AS (
          SELECT cond_bucket, COUNT(*) AS member_count
          FROM bucketed GROUP BY cond_bucket
        ),
        sv AS (
          SELECT b.cond_bucket,
                 IFNULL(v.specialty_ctg_cd, '(NULL)') AS specialty_ctg_cd,
                 COUNT(*) AS visit_count
          FROM `{VISITS}` v
          JOIN bucketed b ON v.member_id = b.member_id
          WHERE EXTRACT(YEAR FROM v.month) = {STUDY_YEAR}
          GROUP BY b.cond_bucket, specialty_ctg_cd
        ),
        top_spec AS (
          SELECT specialty_ctg_cd, SUM(visit_count) AS spec_visits
          FROM sv GROUP BY specialty_ctg_cd
          ORDER BY spec_visits DESC
          LIMIT {TOP_SPECIALTIES}
        )
        SELECT sv.specialty_ctg_cd, ts.spec_visits, sv.cond_bucket,
               sv.visit_count, bm.member_count,
               SAFE_DIVIDE(sv.visit_count, bm.member_count) AS visits_per_member
        FROM sv
        JOIN top_spec ts ON sv.specialty_ctg_cd = ts.specialty_ctg_cd
        JOIN bucket_members bm ON sv.cond_bucket = bm.cond_bucket
        ORDER BY ts.spec_visits DESC, sv.cond_bucket""")
    matrix = {}
    for r in spec_rows:
        entry = matrix.setdefault(r["specialty_ctg_cd"],
                                  {"spec_visits": r["spec_visits"], "vpm": {}})
        entry["vpm"][r["cond_bucket"]] = float(r["visits_per_member"])
    print(f"  {'specialty':>12}  {'visits':>10}  "
          f"{'vpm_0':>7}  {'vpm_1':>7}  {'vpm_2':>7}  {'vpm_3+':>7}")
    for spec, e in sorted(matrix.items(),
                          key=lambda kv: -kv[1]["spec_visits"]):
        cells = "  ".join(f"{e['vpm'].get(b, 0.0):7.3f}"
                          for b in ("0", "1", "2", "3+"))
        print(f"  {spec:>12}  {e['spec_visits']:>10,}  {cells}")
    bucket_counts = {r["cond_bucket"]: r["member_count"] for r in spec_rows}
    counts_line = "  bucket members: " + "  ".join(
        f"{b}={bucket_counts.get(b, 0):,}{small_flag(bucket_counts.get(b, 0))}"
        for b in ("0", "1", "2", "3+"))
    print(counts_line)
    for b in ("0", "1", "2", "3+"):
        n = bucket_counts.get(b, 0)
        studied_cells.append({"label": f"section-2 bucket {b}",
                              "members": n,
                              "flagged": f"{b}={n:,} [SMALL-CELL]"
                                         in counts_line})
    print("  (rising columns left to right = the overlap problem: "
          "multi-condition members use every specialty more)")

    print(f"\n=== 3. exactly-one-condition members, top {TOP_HCCS} "
          f"highest-prevalence HCCs ===")
    solo_rates = fetch(client, f"""
        WITH {SPINE}, {CONDS}, {VIS},
        solo AS (
          SELECT f.member_id, CAST(f.HCC_v24 AS STRING) AS hcc,
                 ANY_VALUE(f.description) AS description
          FROM `{CFLAGS}` f
          JOIN conds c ON f.member_id = c.member_id AND c.n_cond = 1
          JOIN spine s ON f.member_id = s.member_id
          WHERE f.year = {STUDY_YEAR}
          GROUP BY f.member_id, hcc
        )
        SELECT so.hcc, ANY_VALUE(so.description) AS description,
               COUNT(*) AS cohort_members,
               SUM(COALESCE(v.visit_count, 0)) AS total_visits,
               SAFE_DIVIDE(SUM(COALESCE(v.visit_count, 0)), COUNT(*))
                 AS visits_per_member
        FROM solo so
        LEFT JOIN vis v ON so.member_id = v.member_id
        GROUP BY so.hcc""")
    solo_by_hcc = {r["hcc"]: r for r in solo_rates}

    sig_rows = fetch(client, f"""
        WITH {SPINE}, {CONDS},
        top_hcc AS (
          SELECT CAST(f.HCC_v24 AS STRING) AS hcc,
                 COUNT(DISTINCT f.member_id) AS prev_members
          FROM `{CFLAGS}` f
          JOIN spine s ON f.member_id = s.member_id
          WHERE f.year = {STUDY_YEAR}
          GROUP BY hcc
          ORDER BY prev_members DESC
          LIMIT {TOP_HCCS}
        ),
        solo AS (
          SELECT f.member_id, CAST(f.HCC_v24 AS STRING) AS hcc
          FROM `{CFLAGS}` f
          JOIN conds c ON f.member_id = c.member_id AND c.n_cond = 1
          JOIN spine s ON f.member_id = s.member_id
          WHERE f.year = {STUDY_YEAR}
          GROUP BY f.member_id, hcc
        ),
        spec AS (
          SELECT so.hcc,
                 IFNULL(v.specialty_ctg_cd, '(NULL)') AS specialty_ctg_cd,
                 COUNT(*) AS visit_count
          FROM `{VISITS}` v
          JOIN solo so ON v.member_id = so.member_id
          JOIN top_hcc t ON so.hcc = t.hcc
          WHERE EXTRACT(YEAR FROM v.month) = {STUDY_YEAR}
          GROUP BY so.hcc, specialty_ctg_cd
        )
        SELECT t.hcc, t.prev_members, sp.specialty_ctg_cd, sp.visit_count,
               ROW_NUMBER() OVER (PARTITION BY t.hcc
                                  ORDER BY sp.visit_count DESC) AS spec_rank
        FROM top_hcc t
        LEFT JOIN spec sp ON t.hcc = sp.hcc
        WHERE TRUE
        QUALIFY spec_rank <= 5
        ORDER BY t.prev_members DESC, spec_rank""")
    by_hcc = {}
    for r in sig_rows:
        by_hcc.setdefault(r["hcc"], {"prev": r["prev_members"],
                                     "specs": []})
        if r["specialty_ctg_cd"] is not None:
            by_hcc[r["hcc"]]["specs"].append(
                (r["specialty_ctg_cd"], r["visit_count"]))
    for hcc, e in sorted(by_hcc.items(), key=lambda kv: -kv[1]["prev"]):
        solo = solo_by_hcc.get(hcc)
        members = solo["cohort_members"] if solo else 0
        desc = (solo["description"] or "")[:38] if solo else "(no cohort)"
        head = (f"  HCC {hcc:>5}  {desc:<38}  prevalence={e['prev']:>8,}  "
                f"exactly_one={members:>7,}{small_flag(members)}")
        print(head)
        studied_cells.append({"label": f"solo HCC {hcc}",
                              "members": members,
                              "flagged": "[SMALL-CELL]" in head})
        if not solo or not e["specs"]:
            print("        (no exactly-one members or no visits)")
            continue
        parts = [f"{spec}:{visit_count / members:.2f}"
                 for spec, visit_count in e["specs"]]
        print(f"        overall_vpm={float(solo['visits_per_member']):.2f}  "
              f"top_specs " + "  ".join(parts))

    print(f"\n=== 4. top {TOP_PAIRS} condition pairs "
          f"(exactly-two-condition members) ===")
    pair_rows = fetch(client, f"""
        WITH {SPINE}, {CONDS}, {VIS},
        two AS (
          SELECT c.member_id
          FROM conds c
          JOIN spine s ON c.member_id = s.member_id
          WHERE c.n_cond = 2
        ),
        pair AS (
          SELECT a.member_id,
                 CAST(a.HCC_v24 AS STRING) AS hcc_a,
                 CAST(b.HCC_v24 AS STRING) AS hcc_b
          FROM `{CFLAGS}` a
          JOIN `{CFLAGS}` b
            ON a.member_id = b.member_id
            AND b.year = {STUDY_YEAR}
            AND CAST(a.HCC_v24 AS STRING) < CAST(b.HCC_v24 AS STRING)
          JOIN two t ON a.member_id = t.member_id
          WHERE a.year = {STUDY_YEAR}
        ),
        top_pairs AS (
          SELECT hcc_a, hcc_b, COUNT(*) AS pair_members
          FROM pair
          GROUP BY hcc_a, hcc_b
          ORDER BY pair_members DESC
          LIMIT {TOP_PAIRS}
        ),
        pv AS (
          SELECT p.hcc_a, p.hcc_b,
                 SUM(COALESCE(v.visit_count, 0)) AS total_visits
          FROM pair p
          JOIN top_pairs tp ON p.hcc_a = tp.hcc_a AND p.hcc_b = tp.hcc_b
          LEFT JOIN vis v ON p.member_id = v.member_id
          GROUP BY p.hcc_a, p.hcc_b
        )
        SELECT tp.hcc_a, tp.hcc_b, tp.pair_members,
               pv.total_visits,
               SAFE_DIVIDE(pv.total_visits, tp.pair_members)
                 AS visits_per_member
        FROM top_pairs tp
        JOIN pv ON tp.hcc_a = pv.hcc_a AND tp.hcc_b = pv.hcc_b
        ORDER BY tp.pair_members DESC""")
    ratios = []
    for r in pair_rows:
        a = solo_by_hcc.get(r["hcc_a"])
        b = solo_by_hcc.get(r["hcc_b"])
        pair_vpm = float(r["visits_per_member"])
        solo_sum = (float(a["visits_per_member"]) +
                    float(b["visits_per_member"])) if a and b else 0.0
        if a and b and solo_sum > 0:
            ratio = pair_vpm / solo_sum
            ratios.append(ratio)
            tag = "sub-additive" if ratio < 1 else "super-additive"
            line = (f"  {r['hcc_a']:>5} + {r['hcc_b']:>5}  "
                    f"members={r['pair_members']:>7,}  "
                    f"pair_vpm={pair_vpm:6.2f}  solo_sum={solo_sum:6.2f}  "
                    f"ratio={ratio:5.2f} ({tag})"
                    f"{small_flag(r['pair_members'])}")
        else:
            line = (f"  {r['hcc_a']:>5} + {r['hcc_b']:>5}  "
                    f"members={r['pair_members']:>7,}  "
                    f"pair_vpm={pair_vpm:6.2f}  solo_sum=n/a "
                    f"(solo anchor missing or zero)"
                    f"{small_flag(r['pair_members'])}")
        print(line)
        studied_cells.append({"label": f"pair {r['hcc_a']}+{r['hcc_b']}",
                              "members": r["pair_members"],
                              "flagged": "[SMALL-CELL]" in line})
    median_ratio = median(ratios)

    if median_ratio is None:
        pair_decision = "DROP"
        pair_note = ("no pair ratios could be computed this run, so the "
                     "first fit starts main-effects-only")
    elif median_ratio <= 1.05:
        pair_decision = "DROP"
        pair_note = (f"the measured median pair ratio {median_ratio:.2f} "
                     f"sits near or below 1 (sub-additive), so a "
                     f"main-effects model with a shared-deflation step is "
                     f"the simpler starting point")
    else:
        pair_decision = "REVISIT"
        pair_note = (f"the measured median pair ratio {median_ratio:.2f} is "
                     f"above 1 (super-additive), so 14 must test interaction "
                     f"terms rather than assume main effects")

    print("\n=== 5. closing decisions (keep / drop / engineer) ===")
    print(f"""
  PROBLEM : a member with N conditions has ONE set of visits; naive
            per-condition rates count those visits N times. Section 2
            shows every specialty's visits per member rising with
            condition count - the overlap is real, not additive.
  EVIDENCE: median pair ratio = {median_ratio if median_ratio is None
            else format(median_ratio, '.2f')} (pair visits vs sum of the
            two solo rates). Ratios under 1 mean sub-additive overlap:
            a plain sum of single-condition rates over-counts, so the
            splitting model must allocate, not add.
  KEEP    : condition indicator variables per member - candidate model:
            per-specialty regression of member visits on condition
            indicators, nonnegative least squares or Poisson, so each
            condition gets a nonnegative per-specialty visit
            contribution and allocations sum to observed visits.
  KEEP    : exactly-one-condition cohort signatures (section 3) as the
            anchor and the fallback allocation when a regression
            coefficient is unstable.
  ENGINEER: base rate = the 0-condition group's visits per member
            ({base_rate if base_rate is None else format(base_rate, '.2f')}),
            subtracted before splitting so conditions explain only
            visits above baseline.
  {pair_decision:<8}: pairwise interaction terms in the first fit -
            {pair_note};
            revisit in 15 if validation shows pair-specific bias.
  SMALL   : cells under {SMALL_CELL_MEMBERS:,} members are marked
            SMALL-CELL above and excluded from anchoring in 14.""")

    recon = fetch(client, f"""
        WITH {SPINE},
        base AS (
          SELECT IFNULL(specialty_ctg_cd, '(NULL)') AS specialty_ctg_cd,
                 COUNT(*) AS base_visits
          FROM `{VISITS}`
          WHERE EXTRACT(YEAR FROM month) = {STUDY_YEAR}
          GROUP BY specialty_ctg_cd
        ),
        studied AS (
          SELECT IFNULL(v.specialty_ctg_cd, '(NULL)') AS specialty_ctg_cd,
                 COUNT(*) AS studied_visits
          FROM `{VISITS}` v
          JOIN spine s ON v.member_id = s.member_id
          WHERE EXTRACT(YEAR FROM v.month) = {STUDY_YEAR}
          GROUP BY specialty_ctg_cd
        )
        SELECT b.specialty_ctg_cd, b.base_visits,
               COALESCE(st.studied_visits, 0) AS studied_visits
        FROM base b
        LEFT JOIN studied st ON b.specialty_ctg_cd = st.specialty_ctg_cd
        ORDER BY b.base_visits DESC""")
    base_total = sum(r["base_visits"] for r in recon)
    studied_total = sum(r["studied_visits"] for r in recon)
    total_diff = abs(studied_total - base_total) / base_total
    print(f"\nreconciliation: studied visits {studied_total:,} vs "
          f"md1_visits_base {STUDY_YEAR} total {base_total:,} "
          f"(diff {100 * total_diff:.3f}%)")
    assert total_diff <= 0.005, (
        f"GATE FAILED (R4): studied universe covers {studied_total:,} of "
        f"{base_total:,} {STUDY_YEAR} visits; diff {100 * total_diff:.3f}% "
        f"exceeds 0.5 percent")
    for r in recon[:TOP_SPECIALTIES]:
        diff = abs(r["studied_visits"] - r["base_visits"]) / r["base_visits"]
        assert diff <= 0.005, (
            f"GATE FAILED (R4): specialty {r['specialty_ctg_cd']} studied "
            f"{r['studied_visits']:,} vs base {r['base_visits']:,}; diff "
            f"{100 * diff:.3f}% exceeds 0.5 percent")
    for cell in studied_cells:
        assert cell["members"] >= SMALL_CELL_MEMBERS or cell["flagged"], (
            f"GATE FAILED (R2 spirit): studied cell {cell['label']} has "
            f"{cell['members']} members and was not marked SMALL-CELL")
    print(f"\nALL GATES PASSED (R4 total and top-{TOP_SPECIALTIES} specialty "
          f"reconciliation, R2-spirit small-cell marking on "
          f"{len(studied_cells)} studied cells)")


if __name__ == "__main__":
    main()
