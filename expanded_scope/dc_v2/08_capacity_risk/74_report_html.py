"""
74 - capacity report HTML (export lane)   [PYTHON / plotly-inlined single file]

WHAT  : ONE self-contained HTML deliverable from the same BQ tables as
        module 73 (74a outputs + cap_* + dem_segment_split). plotly.js is
        inlined from the local plotly package, all data baked in as JSON -
        the file opens by double-click, no server, works offline. NO map
        anywhere; NO sliders - the scenario is frozen at G_BASE with
        G_MINUS2/G_PLUS2 shown as a sensitivity band note. Styling mirrors
        whatif_dashboard_v2.py house styles (card #fcfcfb, border
        rgba(11,11,11,0.10), radius 10px, system-ui font, banner #fab219,
        stage-box colors) so the two deliverables read as one family.
SECTIONS: 1 Header, 2 Demand, 3 County Risk, 4 County Deep-Dive (dropdown,
        all counties precomputed into the baked JSON), 5 Methodology (flow
        stage boxes + collapsible assumptions/glossary/limitations),
        6 Data Quality footer. Sticky nav across all sections.
INPUTS: cap_growth_measured, cap_scenario_input, cap_scenario_results,
        cap_county_drivers, cap_action_lists, cap_hours_annual,
        cap_daily_capped, cap_provider_year, cap_provider_segment,
        cap_cohort_bench, cap_observed_detail, cap_willing, cap_params,
        dem_segment_split, ms_ref_county, ref_specialty_crosswalk,
        00_docs/capacity_methodology_v2.md (limitations verbatim)
OUTPUT: expanded_scope/dc_v2/08_capacity_risk/outputs/capacity_report.html
Run   : python expanded_scope/dc_v2/08_capacity_risk/74_report_html.py
"""

# ASSUMPTION [1]: plotly.js comes from the installed plotly package
#   (plotly.offline.get_plotlyjs()) - already a dashboard dependency; the
#   script stops with a clear message if the package is absent. This is
#   the only way to inline the library without a network fetch.
# ASSUMPTION [2]: JSON keys are compressed (documented in the JS header
#   comment) to keep the single file reasonable; action lists are capped
#   at 15 rows each per county (module 73 A2 parity) and the demand table
#   renders at most 2000 rows at once (filter by state to narrow).
# ASSUMPTION [3]: the "Unattributed" toggle on the County Risk section
#   adds each county's unattributed (NULL specialty/segment) growth and
#   unplaced onto the row totals; driver, mix and provider columns stay
#   attributed-only because those breakdowns do not exist for NULL cells.
# ASSUMPTION [4]: the deep-dive "driver bars (4 causes)" show the three
#   unplaced-visit causes plus PAPER_NETWORK as a count context bar,
#   labeled as a count (providers), not visits - mirroring 74a's context
#   column semantics.
# ASSUMPTION [5]: flow wording = the dashboard's CAP_FLOW_STAGES (8 steps,
#   DASH-1) verbatim and FLOW_STAGES 2-7 renumbered 1-6 for the demand
#   side, with the slider stage replaced by the measured-growth note
#   (module 73 A4 parity).

import datetime
import json
import os
import re
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

import pandas as pd

RUN_MODE = "sample"   # no claims scan; reads pipeline tables only

GROWTH = cfg.table("cap_growth_measured")
IN_T   = cfg.table("cap_scenario_input")
RES    = cfg.table("cap_scenario_results")
DRV    = cfg.table("cap_county_drivers")
ACT    = cfg.table("cap_action_lists")
PY     = cfg.table("cap_provider_year")
WILL   = cfg.table("cap_willing")
BENCH  = cfg.table("cap_cohort_bench")
ANNUAL = cfg.table("cap_hours_annual")
CAPPED = cfg.table("cap_daily_capped")
OBS    = cfg.table("cap_observed_detail")
MATRIX = cfg.table("cap_provider_segment")
PARAMS = cfg.table("cap_params")
DEM    = cfg.table("dem_segment_split")
CTY    = cfg.table("ref_county")
XWALK  = cfg.base("ref_specialty_crosswalk")

OUT_HTML = cfg.repo_path("expanded_scope", "dc_v2", "08_capacity_risk",
                         "outputs", "capacity_report.html")
METHOD_MD = cfg.repo_path("expanded_scope", "dc_v2", "00_docs",
                          "capacity_methodology_v2.md")

RUN_DATE = datetime.date.today().isoformat()
BANNER = (f"Frozen scenario: measured enrollment growth (G_BASE), run "
          f"{RUN_DATE}. Sensitivity = G_MINUS2/G_PLUS2 shown as a band. "
          f"Rankings do not change within this file.")
METHOD_SENTENCE = (
    "Growth = measured enrollment (distinct members per state, 2025 vs "
    "2024) poured through the module-69 two-lane fill; whatever no "
    "open-door provider with room could absorb is the county's risk.")

LIST_ROWS = 15
SEGMENTS_8 = ["NEW_CHR_60_74", "NEW_CHR_75P", "NEW_NONCHR_60_74",
              "NEW_NONCHR_75P", "RET_CHR_60_74", "RET_CHR_75P",
              "RET_NONCHR_60_74", "RET_NONCHR_75P"]
UNBRIDGED = "Unattributed (unbridged)"

# flow wording copied verbatim from whatif_dashboard_v2.py (A5)
DEMAND_FLOW_NOTE = (
    "In the dashboard, stage 1 is the growth slider (your assumption). In "
    "this frozen export the growth assumption is MEASURED: distinct "
    "enrolled members per state, 2024 vs 2025, g = members_2025 / "
    "members_2024 - 1, applied at G_BASE with +/-2pt sensitivity.")
DEMAND_FLOW = [
    ["New member counts per age group.", "data",
     "Real enrollment: members per county per age group, counted from "
     "membership records (Dec 2025). The slider applies to these counts. "
     "Example: 1,000 members aged 85+, slider +10% = 100 new members."],
    ["Condition mix — people, not visits yet.", "data",
     "From 2025 claims we counted, per county and age group, the share of "
     "members with each condition — the prevalence. New members x "
     "prevalence = new members with each condition. Example: 100 x 30% = "
     "30 new CKD members. This is the condition table."],
    ["Visits per specialty — what each condition adds.", "model",
     "The one fitted model. One row per member (1.58M, 2025): 56 yes/no "
     "condition flags -> visit counts per specialty (a visit = one member, "
     "one provider, one day). A regression per specialty gives the base "
     "visits everyone makes plus a coefficient per condition — those "
     "coefficients are the visit rates. Example: 30 CKD members x 2.8 = "
     "84 nephrology visits — the model's job ends at per-member visits; "
     "everything after is addition and a sharing rule."],
    ["Local care pattern — anchor to this county's reality.", "data",
     "The formula uses national patterns; counties have local habits. Per "
     "county and specialty: actual 2025 visits divided by the formula's "
     "2025 number = the factor. Baseline matches reality exactly, and "
     "every slider change is resized to local behavior. Example: factor "
     "0.9 -> 84 becomes 76."],
    ["County demand per specialty — add everyone up.", "box",
     "Each member's predicted visits are summed to the county total per "
     "specialty. No model here — just addition. This is the number on "
     "the demand charts."],
    ["Providers — who absorbs the change.", "box",
     "A sharing rule, not a model: each provider takes a share of the new "
     "demand equal to their share of last year's new patients in that "
     "county and specialty (intake weight). That load is checked against "
     "their ceiling — currently v0: busiest observed month x 12. Modeling "
     "who patients actually choose is a future improvement, listed in "
     "assumptions."],
]
CAP_FLOW = [
    ["Observed work — every visit we can see.", "data",
     "Aetna MA claims (day grain) plus the CMS FFS public file (annual, "
     "one row per provider) — module 61."],
    ["Minutes per service.", "data",
     "The CMS physician work-time file gives intra-service minutes per "
     "procedure code; unmatched codes get zero minutes by design — "
     "modules 60/62."],
    ["Calibration — every tuning number solved, none hard-coded.", "model",
     "Deflation, the daily cap, benchmark percentile, cohort sizes and "
     "the blending constant are solved and stored in one table (cap_params) "
     "— module 63."],
    ["The feasible day.", "model",
     "Hours are deflated, capped at the calibrated daily maximum; "
     "over-cap hours are kept separately as team uplift — module 64."],
    ["Ceiling — what a full year could hold.", "model",
     "Peer benchmark rate x the provider's own working days = ceiling; "
     "spare = ceiling minus observed — module 65."],
    ["Patient-type matrix.", "model",
     "8 patient types (new/returning x chronic x age). Each provider's "
     "intake per type, credibility-blended with peers; OWN vs BORROWED "
     "tagged — modules 66/67."],
    ["Two-lane fill.", "box",
     "Growth demand splits by patient type; facilities keep their share; "
     "NEW patients deal against intake caps, RETURNING against remaining "
     "room — modules 68/69."],
    ["County risk — who cannot be placed.", "box",
     "Whatever no provider could absorb = unplaced = the risk number, by "
     "county, specialty and patient type — modules 70/71, validated by 72."],
]
GLOSSARY = [
    ["segment / patient type", "One of 8 cells: new/returning x "
     "chronic/non-chronic x age band (60-74 / 75+), defined in ref_segment."],
    ["ceiling", "A provider's feasible annual clinical hours (low/high "
     "range); ceiling_low is used for risk - conservative."],
    ["spare hours", "ceiling_low minus observed capped hours, floor 0."],
    ["team uplift", "Hours trimmed by the daily cap - team-billing signal; "
     "absorbing capacity for the fill = spare + uplift (CD-23)."],
    ["open door", "Provider with nonzero blended intake for at least one "
     "segment."],
    ["unplaced demand", "Growth patients no open-door provider with room "
     "could absorb = the county's risk number."],
    ["facility absorbed", "Growth kept by facility/org billers at their "
     "historical share - no ceiling construct applies (CD-24)."],
    ["OWN / BORROWED", "Whether a provider's intake rate is mostly their "
     "own signal or borrowed from the peer cohort (credibility blend)."],
    ["zero-utilization / paper network", "Contracted provider with zero "
     "Aetna MA claims in the window (CD-07); zero claims may be new "
     "contracts."],
    ["measured growth", "Distinct enrolled members per state 2025 vs 2024 "
     "from the membership extract; the export lane's frozen growth rate."],
    ["G_MINUS2 / G_BASE / G_PLUS2", "The frozen scenarios: measured rate "
     "minus 2 points / as measured / plus 2 points, floor 0."],
]
ASSUMPTIONS_TXT = [
    ["Frozen scenarios", "Growth = measured enrollment rate per state "
     "(floor 0 after the +/-2pt shifts); segment mix, intake rates, Aetna "
     "shares and local patterns are frozen at their observed values."],
    ["Sticky shares", "New patients distribute like existing ones (closed "
     "doors excluded); defensible for a 1-year horizon only."],
    ["Same-county fill", "Patients are placed within their own county "
     "only; cross-county access is understated - conservative."],
    ["Aetna share", "Applied once, at the end (CD-06); never inside "
     "layers."],
]


def limitations_from_doc():
    with open(METHOD_MD, "r", encoding="utf-8") as f:
        text = f.read()
    m = re.search(r"## 10\. Limitations.*?\n(.*?)(?:\n## )", text, re.S)
    if not m:
        raise SystemExit(f"STOP -- limitations section not found in {METHOD_MD}")
    items = re.findall(r"^\d+\.\s.*$", m.group(1), re.M)
    if len(items) != 15:
        raise SystemExit(f"STOP -- expected 15 limitations, found {len(items)}")
    return items


def _q(client, label, sql):
    t0 = time.time()
    df = client.query(sql).result().to_dataframe()
    print(f"[{label}] {len(df):,} rows, {time.time() - t0:.1f}s")
    return df


def _f(v, nd=1):
    return None if v is None or pd.isna(v) else round(float(v), nd)


def load(client):
    d = {}
    d["growth"] = _q(client, "growth", f"""
        SELECT state_cd, members_2024, members_2025, g_state
        FROM `{GROWTH}` ORDER BY state_cd""")
    d["demand"] = _q(client, "demand cells", f"""
        WITH x AS (SELECT aetna_cd, MIN(cms_specialty) AS cms_specialty
                   FROM `{XWALK}` GROUP BY 1)
        SELECT d.mbr_state_cd, d.mbr_county_cd, x.cms_specialty,
               SUM(d.segment_demand) AS baseline_visits,
               SUM(d.growth_demand)  AS growth_visits
        FROM `{IN_T}` d LEFT JOIN x ON d.specialty_ctg_cd = x.aetna_cd
        WHERE d.scenario_cd = 'G_BASE'
        GROUP BY 1, 2, 3""")
    d["scen_county"] = _q(client, "scenario county rollup", f"""
        SELECT scenario_cd, mbr_state_cd, mbr_county_cd,
               (cms_specialty IS NOT NULL AND segment_cd IS NOT NULL)
                 AS attributed,
               SUM(growth_demand)         AS growth,
               SUM(placed_cnt)            AS placed,
               SUM(facility_absorbed_cnt) AS facility,
               SUM(unplaced_cnt)          AS unplaced
        FROM `{RES}` WHERE row_type_cd = 'CELL'
        GROUP BY 1, 2, 3, 4""")
    d["cells"] = _q(client, "G_BASE cells", f"""
        SELECT mbr_state_cd, mbr_county_cd, cms_specialty, segment_cd,
               SUM(growth_demand)         AS growth,
               SUM(placed_cnt)            AS placed,
               SUM(facility_absorbed_cnt) AS facility,
               SUM(unplaced_cnt)          AS unplaced
        FROM `{RES}` WHERE row_type_cd = 'CELL' AND scenario_cd = 'G_BASE'
        GROUP BY 1, 2, 3, 4""")
    d["open"] = _q(client, "open providers per county", f"""
        SELECT mbr_state_cd, mbr_county_cd,
               COUNT(DISTINCT IF(seg_market_share > 0,
                     COALESCE(npi, epdb_dw_prvdr_id), NULL)) AS providers_open
        FROM `{RES}` WHERE row_type_cd = 'ALLOC' AND scenario_cd = 'G_BASE'
        GROUP BY 1, 2""")
    d["drivers"] = _q(client, "drivers", f"SELECT * FROM `{DRV}`")
    d["actions"] = _q(client, "action lists", f"SELECT * FROM `{ACT}`")
    d["cty"] = _q(client, "county names", f"""
        SELECT DISTINCT LPAD(TRIM(CAST(county_fips AS STRING)), 5, '0')
                 AS fips, county_name, state_cd
        FROM `{CTY}`""")
    d["quality_rows"] = _q(client, "table row counts", " UNION ALL ".join(
        f"SELECT '{name}' AS table_name, '{module}' AS produced_by, "
        f"COUNT(*) AS row_count FROM `{ref}`"
        for name, module, ref in [
            ("cap_observed_detail", "61", OBS),
            ("cap_hours_annual", "62", ANNUAL),
            ("cap_daily_capped", "64", CAPPED),
            ("cap_provider_year", "65", PY),
            ("cap_cohort_bench", "66", BENCH),
            ("cap_provider_segment", "67", MATRIX),
            ("dem_segment_split", "68", DEM),
            ("cap_willing", "70", WILL),
            ("cap_growth_measured", "74a", GROWTH),
            ("cap_scenario_input", "74a", IN_T),
            ("cap_scenario_results", "74a", RES),
            ("cap_county_drivers", "74a", DRV),
            ("cap_action_lists", "74a", ACT),
            ("cap_params", "63", PARAMS),
        ]))
    d["quality_stats"] = _q(client, "quality stats", f"""
        SELECT 'time-match % (internal services with MPFS minutes)' AS metric,
               SAFE_DIVIDE(SUM(mapped_svc_cnt),
                 SUM(mapped_svc_cnt) + SUM(unmapped_svc_cnt)) AS value
        FROM `{ANNUAL}` WHERE src = 'AETNA_MA'
        UNION ALL
        SELECT 'impossible-day % (raw > 24h, pre-cap)',
               AVG(impossible_day_flag) FROM `{CAPPED}`
        UNION ALL
        SELECT 'borrowed % of G_BASE placed volume (CD-14)',
               SAFE_DIVIDE(SUM(IF(signal_src_cd = 'BORROWED', placed_cnt, 0)),
                           SUM(placed_cnt))
        FROM `{RES}` WHERE row_type_cd = 'ALLOC' AND scenario_cd = 'G_BASE'
        UNION ALL
        SELECT 'borrowed % of matrix cells',
               COUNTIF(signal_src_cd = 'BORROWED') / COUNT(*) FROM `{MATRIX}`
        UNION ALL
        SELECT 'force-normalized providers (limitation 15)',
               CAST(COUNT(DISTINCT IF(alloc_forced_flag = 1,
                    COALESCE(npi, epdb_dw_prvdr_id), NULL)) AS FLOAT64)
        FROM `{PY}`
        UNION ALL
        SELECT 'fallback-cohort % (bench rolled to state x specialty)',
               AVG(fallback_flag) FROM `{BENCH}`
        UNION ALL
        SELECT 'CMS rows on cohort avg-minutes fallback %',
               SAFE_DIVIDE(COUNTIF(avg_mins_src_cd = 'COHORT'),
                           COUNTIF(avg_mins_src_cd IS NOT NULL))
        FROM `{ANNUAL}` WHERE src = 'CMS_FFS'""")
    return d


def build_payload(d, limitations):
    name_by_fips = {r.fips: r.county_name for r in d["cty"].itertuples()}

    def cname(cty):
        return name_by_fips.get(str(cty).zfill(5), str(cty))

    growth = [[r.state_cd, int(r.members_2024 or 0), int(r.members_2025 or 0),
               _f(r.g_state, 4)] for r in d["growth"].itertuples()]
    g_overall = next((g[3] for g in growth if g[0] == "ALL_FOOTPRINT"), None)

    # demand rows: [state, county, specialty, baseline, growth]
    demand = []
    for r in d["demand"].itertuples():
        demand.append([
            r.mbr_state_cd or "(none)", cname(r.mbr_county_cd),
            r.cms_specialty if pd.notna(r.cms_specialty) else UNBRIDGED,
            _f(r.baseline_visits, 0), _f(r.growth_visits, 0)])

    sc = d["scen_county"]
    base = sc[(sc["scenario_cd"] == "G_BASE") & sc["attributed"]]
    minus = sc[(sc["scenario_cd"] == "G_MINUS2") & sc["attributed"]] \
        .set_index(["mbr_state_cd", "mbr_county_cd"])["unplaced"]
    plus = sc[(sc["scenario_cd"] == "G_PLUS2") & sc["attributed"]] \
        .set_index(["mbr_state_cd", "mbr_county_cd"])["unplaced"]
    unattr = sc[(sc["scenario_cd"] == "G_BASE") & (~sc["attributed"])] \
        .set_index(["mbr_state_cd", "mbr_county_cd"])[["growth", "unplaced"]]

    cells = d["cells"]
    attr = cells[cells["cms_specialty"].notna() & cells["segment_cd"].notna()]
    seg = attr.groupby(["mbr_state_cd", "mbr_county_cd", "segment_cd"])[
        ["growth", "placed", "unplaced"]].sum()
    spec = attr.groupby(["mbr_state_cd", "mbr_county_cd", "cms_specialty"])[
        ["growth", "unplaced"]].sum()

    drv = d["drivers"]
    drv_c = drv.groupby(["mbr_state_cd", "mbr_county_cd"])[
        ["unplaced_no_providers", "unplaced_doors_closed",
         "unplaced_at_capacity"]].sum()
    paper = drv.groupby(["mbr_state_cd", "mbr_county_cd"])[
        "paper_network_cnt"].max()

    act = d["actions"].copy()
    act["_key"] = list(zip(act["prvdr_state_cd"],
                           act["prvdr_county"].astype(str).str.upper()
                           .str.strip()))
    open_p = d["open"].set_index(["mbr_state_cd", "mbr_county_cd"])[
        "providers_open"]

    def action_rows(key, list_cd, sort_col):
        sub = act[(act["list_cd"] == list_cd) & (act["_key"] == key)] \
            .sort_values(sort_col, ascending=False, na_position="last")
        total = len(sub)
        rows = [[str(a.npi) if pd.notna(a.npi) else "",
                 str(a.epdb_dw_prvdr_id) if pd.notna(a.epdb_dw_prvdr_id)
                 else "",
                 a.specialty_ctg_cd if pd.notna(a.specialty_ctg_cd) else "",
                 _f(a.absorbing_hrs, 0), _f(a.used_hrs_g_base, 0),
                 _f(a.remaining_room_hrs, 0)]
                for a in sub.head(LIST_ROWS).itertuples()]
        return {"n": total, "rows": rows}

    counties = {}
    for r in base.itertuples():
        st, cty = r.mbr_state_cd, r.mbr_county_cd
        g_v = float(r.growth or 0)
        u_v = float(r.unplaced or 0)
        try:
            segs = seg.xs((st, cty), level=[0, 1])
        except KeyError:
            segs = pd.DataFrame(columns=["growth", "placed", "unplaced"])
        try:
            specs = spec.xs((st, cty), level=[0, 1]) \
                .sort_values("unplaced", ascending=False)
        except KeyError:
            specs = pd.DataFrame(columns=["growth", "unplaced"])
        seg_tot = float(segs["unplaced"].sum()) if len(segs) else 0.0
        chr_u = float(segs.loc[[s for s in segs.index if "_CHR_" in s],
                               "unplaced"].sum()) if len(segs) else 0.0
        p75_u = float(segs.loc[[s for s in segs.index if s.endswith("75P")],
                               "unplaced"].sum()) if len(segs) else 0.0
        new_u = float(segs.loc[[s for s in segs.index if s.startswith("NEW")],
                               "unplaced"].sum()) if len(segs) else 0.0
        if (st, cty) in drv_c.index:
            dr = drv_c.loc[(st, cty)]
            causes = [float(dr["unplaced_no_providers"] or 0),
                      float(dr["unplaced_doors_closed"] or 0),
                      float(dr["unplaced_at_capacity"] or 0)]
        else:
            causes = [0.0, 0.0, 0.0]
        dominant = ["NO_PROVIDERS", "DOORS_CLOSED", "AT_CAPACITY"][
            causes.index(max(causes))] if u_v > 0 else "-"
        name = cname(cty)
        key = (st, str(name).upper().strip())
        ua = unattr.loc[(st, cty)] if (st, cty) in unattr.index else None
        counties[str(cty)] = {
            "n": name, "s": st,
            "g": _f(g_v, 0), "f": _f(r.facility, 0), "p": _f(r.placed, 0),
            "u": _f(u_v, 0),
            "um": _f(minus.get((st, cty), 0), 0),
            "up": _f(plus.get((st, cty), 0), 0),
            "ug": _f(ua["growth"], 0) if ua is not None else 0,
            "uu": _f(ua["unplaced"], 0) if ua is not None else 0,
            "dd": dominant,
            "ts": (specs.index[0] if len(specs)
                   and float(specs["unplaced"].iloc[0]) > 0 else "-"),
            "tu": (_f(specs["unplaced"].iloc[0], 0) if len(specs) else 0),
            "pc": _f(chr_u / seg_tot, 3) if seg_tot else 0,
            "p7": _f(p75_u / seg_tot, 3) if seg_tot else 0,
            "pn": _f(new_u / seg_tot, 3) if seg_tot else 0,
            "po": int(open_p.get((st, cty), 0) or 0),
            "pa": int((act[(act["list_cd"] == "AT_CAPACITY")
                           & (act["_key"] == key)]).shape[0]),
            "pz": int((act[(act["list_cd"] == "ZERO_CLAIM")
                           & (act["_key"] == key)]).shape[0]),
            "paper": int(paper.get((st, cty), 0) or 0),
            "drv": causes,
            "spec": [[i, _f(row["growth"], 0), _f(row["unplaced"], 0)]
                     for i, row in specs.iterrows()],
            "seg": [[s] + ([_f(segs.loc[s, "growth"], 0),
                            _f(segs.loc[s, "placed"], 0),
                            _f(segs.loc[s, "unplaced"], 0)]
                           if s in segs.index else [0, 0, 0])
                    for s in SEGMENTS_8],
            "lists": {
                "top": action_rows(key, "TOP_ROOM", "remaining_room_hrs"),
                "atc": action_rows(key, "AT_CAPACITY", "used_hrs_g_base"),
                "zc": action_rows(key, "ZERO_CLAIM", "absorbing_hrs"),
            },
        }
    # within-state rank by attributed unplaced (module 73 A1 parity)
    by_state = {}
    for fips, c in counties.items():
        by_state.setdefault(c["s"], []).append(fips)
    for st, lst in by_state.items():
        lst.sort(key=lambda f_: -(counties[f_]["u"] or 0))
        for i, f_ in enumerate(lst, start=1):
            counties[f_]["rk"] = i

    kpi = {
        "g": g_overall,
        "baseline": _f(sum(x[3] or 0 for x in demand), 0),
        "growth": _f(sum(x[4] or 0 for x in demand), 0),
        "unplaced": _f(sum((c["u"] or 0) for c in counties.values()), 0),
    }
    quality = {
        "rows": [[r.table_name, r.produced_by, int(r.row_count)]
                 for r in d["quality_rows"].sort_values("table_name")
                 .itertuples()],
        "stats": [[r.metric, _f(r.value, 4)]
                  for r in d["quality_stats"].itertuples()],
        "note": ("Run mode is not stamped in the tables: check the "
                 "prompt-pack STATUS line; sample-mode numbers are 1% of "
                 "members on every claims-derived table."),
    }
    return {
        "run": RUN_DATE, "banner": BANNER, "method": METHOD_SENTENCE,
        "growth": growth, "kpi": kpi, "demand": demand,
        "counties": counties,
        "states": sorted(by_state.keys()),
        "flows": {"demand": DEMAND_FLOW, "cap": CAP_FLOW,
                  "note": DEMAND_FLOW_NOTE},
        "glossary": GLOSSARY, "assumptions": ASSUMPTIONS_TXT,
        "limitations": limitations, "quality": quality,
        "listRows": LIST_ROWS,
    }


# ---------------------------------------------------------------- template
TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Capacity Risk Report — Frozen Export</title>
<script>__PLOTLY__</script>
<style>
  body { font-family: system-ui, sans-serif; margin: 0; background: #ffffff;
         color: #0b0b0b; }
  .wrap { max-width: 1200px; margin: 0 auto; padding: 16px; }
  .banner { background: #fab219; color: #0b0b0b; font-weight: 700;
            text-align: center; padding: 8px; border-radius: 8px;
            margin-bottom: 8px; letter-spacing: 0.02em; }
  nav { position: sticky; top: 0; z-index: 50; background: #fcfcfb;
        border-bottom: 1px solid rgba(11,11,11,0.10); padding: 8px 16px; }
  nav a { margin-right: 18px; font-size: 13px; color: #2a78d6;
          text-decoration: none; font-weight: 600; }
  .card { background: #fcfcfb; border: 1px solid rgba(11,11,11,0.10);
          border-radius: 10px; padding: 10px 14px; }
  .tiles { display: flex; gap: 12px; flex-wrap: wrap; margin: 14px 0; }
  .tile { flex: 1; min-width: 170px; background: #fcfcfb;
          border: 1px solid rgba(11,11,11,0.10); border-radius: 10px;
          padding: 10px 14px; }
  .tile .lbl { font-size: 12px; color: #52514e; }
  .tile .val { font-size: 24px; font-weight: 650; }
  h2 { margin: 26px 0 8px; }
  .note { font-size: 12px; color: #52514e; }
  .small { font-size: 11px; color: #898781; }
  table.data { border-collapse: collapse; font-size: 13px; width: 100%; }
  table.data th { font-weight: 600; text-align: left; padding: 6px 10px;
                  border-bottom: 2px solid rgba(11,11,11,0.2);
                  cursor: pointer; white-space: nowrap; }
  table.data td { padding: 5px 10px;
                  border-bottom: 1px solid rgba(11,11,11,0.08); }
  table.data td.num { text-align: right; font-variant-numeric: tabular-nums; }
  select, input[type=checkbox] { font-size: 13px; }
  .controls { display: flex; gap: 16px; align-items: center;
              flex-wrap: wrap; margin: 8px 0; font-size: 13px; }
  .stage { display: flex; gap: 16px; margin-bottom: 4px; }
  .stage .box { flex: 0 0 40%; border: 1px solid rgba(11,11,11,0.12);
                border-radius: 10px; padding: 12px 16px; font-weight: 650;
                font-size: 14px; }
  .stage .txt { flex: 1; font-size: 13px; color: #3c3b38;
                padding: 10px 4px; }
  .box.data { background: #e8f4e8; } .box.model { background: #e6eefb; }
  .box.box { background: #f4f4f2; }
  .arrow { margin: 2px 0 6px 18%; font-size: 20px; color: #898781; }
  details { margin: 8px 0; }
  details summary { font-weight: 600; cursor: pointer; }
  .cols { display: flex; gap: 24px; flex-wrap: wrap; }
  .cols > div { flex: 1; min-width: 460px; }
  .headline { background: #fff2cc; border-radius: 10px; padding: 10px 14px;
              font-weight: 600; font-size: 14px; margin: 10px 0; }
  .chips span { display: inline-block; background: #f4f4f2;
                border: 1px solid rgba(11,11,11,0.10); border-radius: 14px;
                padding: 3px 10px; margin: 2px 6px 2px 0; font-size: 12px; }
  footer { margin-top: 40px; border-top: 1px solid rgba(11,11,11,0.10);
           padding-top: 14px; }
</style>
</head>
<body>
<nav><div class="wrap" style="padding:0">
  <a href="#top">Header</a><a href="#demand">Demand</a>
  <a href="#risk">County Risk</a><a href="#deepdive">Deep-Dive</a>
  <a href="#methodology">Methodology</a><a href="#quality">Data Quality</a>
</div></nav>
<div class="wrap" id="top">
  <h1 style="margin-bottom:2px">Capacity Risk Report — Frozen Export</h1>
  <div class="note" id="hdr-method"></div>
  <div class="chips" id="hdr-growth" style="margin:8px 0"></div>
  <div class="banner" id="hdr-banner"></div>

  <h2 id="demand">Demand</h2>
  <div class="tiles" id="demand-tiles"></div>
  <div class="controls">
    <label>State <select id="demand-state"></select></label>
    <span class="small" id="demand-note"></span>
  </div>
  <div class="cols">
    <div><div class="card"><div id="demand-table"></div></div></div>
    <div><div class="card"><div id="demand-chart"></div></div></div>
  </div>

  <h2 id="risk">County Risk</h2>
  <div class="controls">
    <label>State <select id="risk-state"></select></label>
    <label>Sort
      <select id="risk-sort">
        <option value="u">unplaced visits</option>
        <option value="pa">providers at capacity</option>
      </select></label>
    <label><input type="checkbox" id="risk-unattr"> include Unattributed
      (NULL specialty/patient-type) volume</label>
    <span class="small" id="risk-note"></span>
  </div>
  <div class="card"><div id="risk-table"></div></div>

  <h2 id="deepdive">County Deep-Dive</h2>
  <div class="controls">
    <label>County <select id="dd-county"></select></label>
  </div>
  <div class="headline" id="dd-headline"></div>
  <div class="cols">
    <div><div class="card"><div id="dd-drivers"></div></div></div>
    <div><div class="card"><div id="dd-spec"></div></div></div>
  </div>
  <div class="card" style="margin-top:14px"><div id="dd-seg"></div></div>
  <div class="note" id="dd-mix" style="margin:10px 0"></div>
  <div class="cols" id="dd-lists"></div>

  <h2 id="methodology">Methodology</h2>
  <div class="cols">
    <div>
      <h3>How the demand numbers flow (6 stages)</h3>
      <div class="note" id="flow-note" style="margin-bottom:10px"></div>
      <div id="flow-demand"></div>
    </div>
    <div>
      <h3>How the capacity numbers flow (8 steps, v2)</h3>
      <div id="flow-cap"></div>
    </div>
  </div>
  <details><summary>Assumptions</summary><div id="meth-assumptions"></div>
  </details>
  <details><summary>Glossary</summary><div id="meth-glossary"></div>
  </details>
  <details><summary>Limitations (verbatim, 1-15)</summary>
    <div id="meth-limitations"></div></details>

  <footer id="quality">
    <h2>Data Quality</h2>
    <div class="note" id="q-note"></div>
    <div class="cols" style="margin-top:10px">
      <div><div class="card"><div id="q-rows"></div></div></div>
      <div><div class="card"><div id="q-stats"></div></div></div>
    </div>
    <div class="small" style="margin:14px 0" id="q-foot"></div>
  </footer>
</div>
<script>
/* Baked data. Key map: counties[fips] = {n name, s state, g growth,
   f facility, p placed, u unplaced, um/up unplaced at G_MINUS2/G_PLUS2,
   ug/uu unattributed growth/unplaced, rk rank-in-state, dd dominant
   driver, ts/tu top specialty + its unplaced, pc/p7/pn chronic/75+/new
   share of gap, po/pa/pz providers open/at-capacity/zero-claim, paper
   contracted-zero-claim count, drv [3 causes], spec [[name,growth,
   unplaced]], seg [[segment,growth,placed,unplaced]x8], lists {top,atc,zc:
   {n,rows[[npi,epdb,spec,abs,used,rem]]}} } */
const DATA = __DATA__;
const PCFG = {displayModeBar: false, responsive: true};
const RED = "#b91c1c", GREEN = "#0ca30c", GRAY = "#c3c2b7";
const fmt = v => (v == null) ? "-" : Number(v).toLocaleString(
  "en-US", {maximumFractionDigits: 0});
const pct = v => (v == null) ? "-" : (100 * v).toFixed(1) + "%";

function el(id) { return document.getElementById(id); }
function table(rows, headers, numCols, onSort) {
  let h = "<table class='data'><thead><tr>";
  headers.forEach((x, i) => {
    h += `<th data-i="${i}">${x}</th>`;
  });
  h += "</tr></thead><tbody>";
  rows.forEach(r => {
    h += "<tr>";
    r.forEach((v, i) => {
      const num = numCols.includes(i);
      h += `<td class="${num ? 'num' : ''}">${num ? fmt(v) : (v ?? '-')}` +
           `</td>`;
    });
    h += "</tr>";
  });
  return h + "</tbody></table>";
}

/* ---- header ---- */
el("hdr-method").textContent = DATA.method + "  Run " + DATA.run + ".";
el("hdr-banner").textContent = DATA.banner;
el("hdr-growth").innerHTML = DATA.growth.map(g =>
  `<span><b>${g[0]}</b> ${g[3] == null ? "n/a" :
   (100 * g[3]).toFixed(2) + "%"} (members ${fmt(g[1])} -> ${fmt(g[2])})` +
  `</span>`).join("");

/* ---- demand ---- */
const tiles = [
  ["Measured growth (footprint)", DATA.kpi.g == null ? "n/a" :
   (100 * DATA.kpi.g).toFixed(2) + "%"],
  ["Baseline visits", fmt(DATA.kpi.baseline)],
  ["Growth visits (G_BASE)", fmt(DATA.kpi.growth)],
  ["Unplaced (risk, attributed)", fmt(DATA.kpi.unplaced)],
];
el("demand-tiles").innerHTML = tiles.map(t =>
  `<div class="tile"><div class="lbl">${t[0]}</div>` +
  `<div class="val">${t[1]}</div></div>`).join("");

const DEMAND_MAX = 2000;
let demandSort = {col: 4, asc: false};
function renderDemand() {
  const st = el("demand-state").value;
  let rows = DATA.demand.filter(r => st === "All" || r[0] === st);
  rows = rows.slice().sort((a, b) => {
    const va = a[demandSort.col], vb = b[demandSort.col];
    const cmp = (typeof va === "string")
      ? String(va).localeCompare(String(vb))
      : ((va || 0) - (vb || 0));
    return demandSort.asc ? cmp : -cmp;
  });
  const shown = rows.slice(0, DEMAND_MAX);
  el("demand-note").textContent =
    `showing ${shown.length.toLocaleString()} of ` +
    `${rows.length.toLocaleString()} rows` +
    (rows.length > DEMAND_MAX ? " (filter by state to narrow)" : "") +
    "; click headers to sort";
  const withDelta = shown.map(r => [r[0], r[1], r[2], r[3], r[4],
    r[3] ? pct(r[4] / r[3]) : "-"]);
  el("demand-table").innerHTML = table(withDelta,
    ["state", "county", "specialty", "baseline", "growth (G_BASE)",
     "delta %"], [3, 4]);
  el("demand-table").querySelectorAll("th").forEach(th => {
    th.onclick = () => {
      const i = +th.dataset.i;
      demandSort = {col: i,
                    asc: demandSort.col === i ? !demandSort.asc : false};
      renderDemand();
    };
  });
  const byCty = {};
  rows.forEach(r => { byCty[r[0] + " " + r[1]] =
    (byCty[r[0] + " " + r[1]] || 0) + (r[4] || 0); });
  const top = Object.entries(byCty).sort((a, b) => b[1] - a[1]).slice(0, 15)
    .reverse();
  Plotly.newPlot("demand-chart", [{type: "bar", orientation: "h",
    y: top.map(t => t[0]), x: top.map(t => t[1]), marker: {color: GREEN},
    hovertemplate: "%{y}: %{x:,.0f} growth visits<extra></extra>"}],
    {title: {text: "Top 15 counties by growth visits", font: {size: 14}},
     margin: {l: 160, r: 20, t: 40, b: 30}, height: 460,
     paper_bgcolor: "#fcfcfb", plot_bgcolor: "#fcfcfb"}, PCFG);
}

/* ---- county risk ---- */
function renderRisk() {
  const st = el("risk-state").value;
  const sortKey = el("risk-sort").value;
  const unattr = el("risk-unattr").checked;
  let items = Object.entries(DATA.counties)
    .filter(([f, c]) => st === "All" || c.s === st);
  let uCells = 0, uVol = 0;
  items.forEach(([f, c]) => { uVol += (c.uu || 0);
    if ((c.ug || 0) > 0 || (c.uu || 0) > 0) uCells += 1; });
  const val = ([f, c]) => sortKey === "pa" ? (c.pa || 0)
    : ((c.u || 0) + (unattr ? (c.uu || 0) : 0));
  items.sort((a, b) => val(b) - val(a));
  const rows = items.map(([f, c]) => {
    const g = (c.g || 0) + (unattr ? (c.ug || 0) : 0);
    const u = (c.u || 0) + (unattr ? (c.uu || 0) : 0);
    return [c.s, c.n, g, c.f, c.p, u, g > 0 ? pct(u / g) : "-", c.rk,
            c.um, c.up, c.dd, c.ts, c.tu, pct(c.pc), pct(c.p7), pct(c.pn),
            c.po, c.pa, c.pz];
  });
  el("risk-note").textContent = unattr
    ? `Unattributed volume INCLUDED in growth/unplaced ` +
      `(${uCells} counties, ${fmt(uVol)} unplaced); driver and mix ` +
      `columns stay attributed-only`
    : `Unattributed volume excluded: ${uCells} counties carry ` +
      `${fmt(uVol)} unplaced with NULL specialty/patient-type`;
  el("risk-table").innerHTML = table(rows,
    ["state", "county", "growth", "facility", "placed", "unplaced",
     "unplaced %", "rank in state", "unplaced G_MINUS2", "unplaced G_PLUS2",
     "dominant driver", "top specialty", "its unplaced", "% chronic",
     "% 75+", "% new", "open", "at capacity", "zero-claim"],
    [2, 3, 4, 5, 8, 9, 12, 16, 17, 18]);
}

/* ---- deep-dive ---- */
function listBlock(title, lst) {
  let h = `<div><div class="card"><b>${title}</b> ` +
    `<span class="small">showing ${lst.rows.length} of ${lst.n}</span>`;
  h += table(lst.rows, ["npi", "epdb id", "specialty", "absorbing hrs",
    "used hrs", "remaining hrs"], [3, 4, 5]);
  return h + "</div></div>";
}
function renderCounty() {
  const f = el("dd-county").value;
  const c = DATA.counties[f];
  if (!c) return;
  el("dd-headline").textContent =
    `${c.s} — ${c.n}: growth ${fmt(c.g)} -> facility absorbed ${fmt(c.f)}` +
    ` -> placed ${fmt(c.p)} -> unplaced ${fmt(c.u)}` +
    ` (${c.g > 0 ? pct(c.u / c.g) : "-"} of growth). ` +
    `Sensitivity band: ${fmt(c.um)} (G_MINUS2) .. ${fmt(c.up)} (G_PLUS2).` +
    ` Rank ${c.rk} in ${c.s}.`;
  Plotly.newPlot("dd-drivers", [{type: "bar",
    x: ["NO_PROVIDERS", "DOORS_CLOSED", "AT_CAPACITY",
        "PAPER_NETWORK (count)"],
    y: [c.drv[0], c.drv[1], c.drv[2], c.paper],
    marker: {color: [RED, RED, RED, GRAY]},
    hovertemplate: "%{x}: %{y:,.0f}<extra></extra>"}],
    {title: {text: "Unplaced by cause (+ paper-network context)",
             font: {size: 14}},
     margin: {l: 50, r: 20, t: 40, b: 60}, height: 320,
     paper_bgcolor: "#fcfcfb", plot_bgcolor: "#fcfcfb"}, PCFG);
  const sp = c.spec.slice(0, 12).reverse();
  Plotly.newPlot("dd-spec", [{type: "bar", orientation: "h",
    y: sp.map(s => s[0]), x: sp.map(s => s[2]), marker: {color: RED},
    hovertemplate: "%{y}: %{x:,.0f} unplaced<extra></extra>"}],
    {title: {text: "Unplaced by specialty (worst first)", font: {size: 14}},
     margin: {l: 170, r: 20, t: 40, b: 30}, height: 320,
     paper_bgcolor: "#fcfcfb", plot_bgcolor: "#fcfcfb"}, PCFG);
  Plotly.newPlot("dd-seg", [
    {type: "bar", name: "growth", x: c.seg.map(s => s[0]),
     y: c.seg.map(s => s[1]), marker: {color: GRAY}},
    {type: "bar", name: "placed", x: c.seg.map(s => s[0]),
     y: c.seg.map(s => s[2]), marker: {color: GREEN}},
    {type: "bar", name: "unplaced", x: c.seg.map(s => s[0]),
     y: c.seg.map(s => s[3]), marker: {color: RED}}],
    {barmode: "group", title: {text: "8 patient-type buckets",
     font: {size: 14}}, margin: {l: 50, r: 20, t: 40, b: 90}, height: 340,
     legend: {orientation: "h"}, paper_bgcolor: "#fcfcfb",
     plot_bgcolor: "#fcfcfb"}, PCFG);
  el("dd-mix").textContent =
    `Chronic-mix context: ${pct(c.pc)} of this county's gap is chronic ` +
    `patients, ${pct(c.p7)} is 75+, ${pct(c.pn)} is new patients. ` +
    `Providers: ${c.po} open, ${c.pa} at capacity, ${c.pz} contracted ` +
    `zero-claim.`;
  el("dd-lists").innerHTML =
    listBlock("Top providers by remaining room", c.lists.top) +
    listBlock("At-capacity providers", c.lists.atc) +
    listBlock("Contracted zero-claim providers", c.lists.zc);
}

/* ---- methodology ---- */
function stageBoxes(target, flow) {
  let h = "";
  flow.forEach((s, i) => {
    h += `<div class="stage"><div class="box ${s[1]}">${i + 1}. ${s[0]}` +
         `</div><div class="txt">${s[2]}</div></div>`;
    if (i < flow.length - 1) h += `<div class="arrow">&#8595;</div>`;
  });
  el(target).innerHTML = h;
}
el("flow-note").textContent = DATA.flows.note;
stageBoxes("flow-demand", DATA.flows.demand);
stageBoxes("flow-cap", DATA.flows.cap);
el("meth-assumptions").innerHTML = DATA.assumptions.map(a =>
  `<p style="font-size:13px"><b>${a[0]}:</b> ${a[1]}</p>`).join("");
el("meth-glossary").innerHTML = DATA.glossary.map(g =>
  `<p style="font-size:13px"><b>${g[0]}</b> — ${g[1]}</p>`).join("");
el("meth-limitations").innerHTML = DATA.limitations.map(l =>
  `<p style="font-size:13px">${l}</p>`).join("");

/* ---- data quality ---- */
el("q-note").textContent = DATA.quality.note;
el("q-rows").innerHTML = table(DATA.quality.rows,
  ["table", "module", "rows"], [2]);
el("q-stats").innerHTML = table(
  DATA.quality.stats.map(s => [s[0], s[1] == null ? "-" : s[1]]),
  ["metric", "value"], []);
el("q-foot").textContent = "Frozen export (modules 74a/73/74). The " +
  "dashboard remains the interactive forecast/slider path. " + DATA.banner;

/* ---- wiring ---- */
function fillStates(sel) {
  el(sel).innerHTML = ["All"].concat(DATA.states)
    .map(s => `<option>${s}</option>`).join("");
}
fillStates("demand-state"); fillStates("risk-state");
el("demand-state").onchange = renderDemand;
el("risk-state").onchange = renderRisk;
el("risk-sort").onchange = renderRisk;
el("risk-unattr").onchange = renderRisk;
const ddSel = el("dd-county");
ddSel.innerHTML = Object.entries(DATA.counties)
  .sort((a, b) => (a[1].s + a[1].n).localeCompare(b[1].s + b[1].n))
  .map(([f, c]) => `<option value="${f}">${c.s} — ${c.n}</option>`).join("");
ddSel.onchange = renderCounty;
const worst = Object.entries(DATA.counties)
  .sort((a, b) => (b[1].u || 0) - (a[1].u || 0))[0];
if (worst) ddSel.value = worst[0];
renderDemand(); renderRisk(); renderCounty();
</script>
</body>
</html>
"""


def main():
    print(f"RUN_MODE = {RUN_MODE} (reads tables only; mode governed by "
          f"upstream runs)")
    try:
        from plotly.offline import get_plotlyjs
    except ImportError:
        raise SystemExit(
            "STOP -- the plotly package is required to inline plotly.js "
            "(pip install plotly; it is already a dashboard dependency)")
    limitations = limitations_from_doc()
    client = cfg.client()
    d = load(client)
    if d["growth"].empty:
        raise SystemExit("STOP -- cap_growth_measured is empty; run 74a first")
    payload = build_payload(d, limitations)
    blob = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    html = TEMPLATE.replace("__PLOTLY__", get_plotlyjs()) \
                   .replace("__DATA__", blob)
    os.makedirs(os.path.dirname(OUT_HTML), exist_ok=True)
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    size_mb = os.path.getsize(OUT_HTML) / 1e6
    print(f"report written: {OUT_HTML} ({size_mb:.1f} MB; data JSON "
          f"{len(blob) / 1e6:.1f} MB; counties {len(payload['counties']):,}; "
          f"demand rows {len(payload['demand']):,})")
    if size_mb > 25:
        print("WARNING: file exceeds 25 MB - consider trimming the demand "
              "table or action lists (A2)")


if __name__ == "__main__":
    main()


# REVIEW
# Reviewer 1 LOGIC:
#  - No fill recomputation and no sliders: every number is a frozen
#    aggregate of the 74a tables; the sensitivity band is read from the
#    G_MINUS2/G_PLUS2 CELL rollups, never derived in the page.
#  - NO map: navigation is the ranked table + the county dropdown; the
#    deep-dive is fully precomputed into the baked JSON (spec).
#  - County keys mirror module 73 (fips + state on the demand side, name +
#    state for provider/action lists via ms_ref_county; rule 12).
#  - Self-containment: plotly.js inlined at build time, zero external
#    fetches in the page; opens file:// offline.
# Reviewer 2 SPEC:
#  - Six sections with sticky nav; KPI tiles in dashboard style; sortable
#    demand table; risk table columns match Excel tab 3 with the
#    unplaced/providers-at-capacity sort toggle and the visible
#    Unattributed toggle; methodology stage boxes mirror the dashboard's
#    colors and wording; quality footer matches Excel tab 9. Deviations =
#    five ASSUMPTION blocks (A2 row caps, A3 toggle semantics, A4 paper
#    bar as count).
#  - Hover values on every chart (hovertemplates); displayModeBar false.
# Reviewer 3 EFFICIENCY:
#  - Zero claims scans; the same ~11 aggregate reads as module 73 minus
#    the provider roster. JSON keys compressed; action lists capped at 15
#    rows per list per county; demand DOM render capped at 2000 rows with
#    a visible note. File size printed with a >25 MB warning.
