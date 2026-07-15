"""
56 - final report   [PYTHON / pandas + openpyxl]

WHAT  : The dc_v2 decision workbook, built from dc2_weave plus the model
        input tables. House style copied from 13_build_report.py (Arial,
        DARK_BLUE/MID_BLUE/LIGHT_BLUE scheme, cell/fill/thin helpers).
        Every data tab uses the actual table column names as headers, with
        a derivation row (italic, small, grey) above the header explaining
        each derived column in one plain sentence; stored columns get
        "as stored". Predictions are for calendar 2026, from models trained
        on 2024-2025 history.
        plan_type split deferred; v1 gap table remains the plan-type source.
GRAIN : tabs at county x cms_specialty (Gap 2026), county (input tabs),
        cms_specialty and state (summaries).
INPUTS: dc2_weave, dc2_demand_base, dc2_demand_chronic,
        dc2_capacity_provider, cfg.base("ref_specialty_crosswalk")
OUTPUT: medicare_demand_capacity_dc2.xlsx (repo root)
Run   : python expanded_scope/dc_v2/06_weave/56_final_report.py
"""

import os
import sys


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
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.formatting.rule import CellIsRule

OUT_XLSX = cfg.repo_path("medicare_demand_capacity_dc2.xlsx")

WEAVE     = cfg.src("dc2_weave")
DEM_BASE  = cfg.src("dc2_demand_base")
DEM_CHR   = cfg.src("dc2_demand_chronic")
CAP_PROV  = cfg.src("dc2_capacity_provider")
XWALK     = cfg.base("ref_specialty_crosswalk")

DARK_BLUE, MID_BLUE, LIGHT_BLUE = "1F3864", "2E75B6", "D6E4F0"
GREY, DARK_GREY, WHITE = "F2F2F2", "595959", "FFFFFF"
LIGHT_GREEN, LIGHT_RED = "E2EFDA", "FFE0E0"

STATE_BY_FIPS = {v: k for k, v in cfg.STATES.items()}


# ---------- styling helpers (from 13_build_report.py) ----------
def fill(hx):
    return PatternFill("solid", fgColor=hx)


def thin():
    s = Side(style="thin", color="CCCCCC")
    return Border(left=s, right=s, top=s, bottom=s)


def cell(ws, ref, value, bold=False, color="000000", bg=None, size=10,
         h_align="left", wrap=True, bdr=False, italic=False, num=None):
    c = ws[ref]
    c.value = value
    c.font = Font(name="Arial", bold=bold, color=color, size=size, italic=italic)
    if bg:
        c.fill = fill(bg)
    c.alignment = Alignment(horizontal=h_align, vertical="center", wrap_text=wrap)
    if bdr:
        c.border = thin()
    if num:
        c.number_format = num
    return c


def section_header(ws, row, c0, c1, text, bg=MID_BLUE):
    ws.merge_cells(f"{get_column_letter(c0)}{row}:{get_column_letter(c1)}{row}")
    cell(ws, f"{get_column_letter(c0)}{row}", text, bold=True, color=WHITE, bg=bg, size=11)
    ws.row_dimensions[row].height = 20
    return row + 1


def kv(ws, row, label, value, h=18):
    ws.merge_cells(f"B{row}:C{row}")
    cell(ws, f"B{row}", label, bold=True, size=10, bg=GREY, bdr=True)
    ws.merge_cells(f"D{row}:H{row}")
    cell(ws, f"D{row}", value, size=10, bg=WHITE, bdr=True, wrap=True)
    ws.row_dimensions[row].height = h
    return row + 1


def blank(ws, row, h=6):
    ws.row_dimensions[row].height = h
    return row + 1


def title(ws, text, sub=None, ncols=8):
    ws.merge_cells(f"A1:{get_column_letter(ncols)}1")
    cell(ws, "A1", text, bold=True, color=WHITE, bg=DARK_BLUE, size=16)
    ws.row_dimensions[1].height = 34
    if sub:
        ws.merge_cells(f"A2:{get_column_letter(ncols)}2")
        cell(ws, "A2", sub, italic=True, color=DARK_BLUE, size=10, bg=LIGHT_BLUE)
        ws.row_dimensions[2].height = 18


def _int(v, d=0):
    try:
        return d if v is None or pd.isna(v) else int(float(v))
    except (TypeError, ValueError):
        return d


def _float(v, d=0.0):
    try:
        return d if v is None or pd.isna(v) else float(v)
    except (TypeError, ValueError):
        return d


def derived_table(ws, df, cols, r0, filters=True):
    """cols = list of (df_key, derivation_sentence, width, number_format, align).
    Headers are the df_key column names verbatim; the derivation row sits above them."""
    for i, (key, derivation, w, _, _) in enumerate(cols):
        col = get_column_letter(i + 1)
        cell(ws, f"{col}{r0}", derivation, italic=True, size=8, color=DARK_GREY,
             bg=GREY, h_align="center", bdr=True)
        cell(ws, f"{col}{r0 + 1}", key, bold=True, color=WHITE, bg=DARK_BLUE, size=9,
             h_align="center", bdr=True)
        ws.column_dimensions[col].width = w
    ws.row_dimensions[r0].height = 42
    ws.row_dimensions[r0 + 1].height = 24
    hdr = r0 + 1
    for ridx, (_, row) in enumerate(df.iterrows(), start=hdr + 1):
        bg = GREY if ridx % 2 == 0 else WHITE
        for i, (key, _, _, num, align) in enumerate(cols):
            v = row.get(key)
            if hasattr(v, "item"):
                v = v.item()
            if num and v is not None and pd.notna(v):
                v = _float(v) if ("%" in num or "." in num) else _int(v)
            elif v is None or pd.isna(v):
                v = None
            cell(ws, f"{get_column_letter(i + 1)}{ridx}", v, bg=bg, size=9, bdr=True,
                 num=num, h_align=align)
    if filters:
        ws.auto_filter.ref = f"A{hdr}:{get_column_letter(len(cols))}{hdr + len(df)}"
    ws.freeze_panes = f"A{hdr + 1}"
    return hdr


AS_STORED = "as stored"


# ---------- data ----------
def load():
    client = cfg.client()
    q = lambda sql: client.query(sql).result().to_dataframe()
    d = {}
    d["weave"] = q(f"SELECT * FROM `{WEAVE}` ORDER BY county, cms_specialty")

    d["dem_dec"] = q(f"""
        SELECT mbr_county_cd, MAX(members) AS members, MAX(mbr_lt65) AS mbr_lt65,
               MAX(mbr_65_74) AS mbr_65_74, MAX(mbr_75_84) AS mbr_75_84,
               MAX(mbr_85p) AS mbr_85p
        FROM `{DEM_BASE}` WHERE month = DATE '2025-12-01' GROUP BY 1""")
    d["dem_yr"] = q(f"""
        SELECT mbr_county_cd, SUM(visits) AS visits,
               AVG(pct_new_patients) AS pct_new_patients
        FROM `{DEM_BASE}` WHERE year = 2025 GROUP BY 1""")
    d["top5_prev"] = q(f"""
        WITH top5 AS (
          SELECT HCC_v24 FROM `{DEM_CHR}` GROUP BY 1
          ORDER BY AVG(prevalence) DESC LIMIT 5
        )
        SELECT mbr_county_cd, HCC_v24, prevalence
        FROM `{DEM_CHR}`
        WHERE month = DATE '2025-12-01' AND HCC_v24 IN (SELECT HCC_v24 FROM top5)""")

    d["cap_inputs"] = q(f"""
        SELECT prvdr_county,
               COUNT(DISTINCT IF(year = 2025, epdb_dw_prvdr_id, NULL)) AS providers,
               SUM(IF(month = DATE '2025-12-01', panel_members, 0)) AS panel_members,
               SUM(IF(month = DATE '2025-12-01', panel_lt65, 0)) AS panel_lt65,
               SUM(IF(month = DATE '2025-12-01', panel_65_74, 0)) AS panel_65_74,
               SUM(IF(month = DATE '2025-12-01', panel_75_84, 0)) AS panel_75_84,
               SUM(IF(month = DATE '2025-12-01', panel_85p, 0)) AS panel_85p,
               SUM(IF(month = DATE '2025-12-01', panel_chronic_members, 0)) AS panel_chronic_members,
               AVG(IF(year = 2025, pct_new_patients, NULL)) AS pct_new_patients,
               APPROX_QUANTILES(IF(month = DATE '2025-12-01', tenure_months, NULL), 100)[OFFSET(50)] AS tenure_months,
               SUM(IF(year = 2025, visits, 0)) AS visits
        FROM `{CAP_PROV}` GROUP BY 1 ORDER BY visits DESC""")

    top_cell = q(f"SELECT mbr_county_cd, specialty_ctg_cd FROM `{DEM_BASE}` "
                 f"WHERE year = 2024 GROUP BY 1, 2 ORDER BY SUM(visits) DESC LIMIT 1")
    d["we_county"] = str(top_cell["mbr_county_cd"].iloc[0])
    d["we_spec"] = str(top_cell["specialty_ctg_cd"].iloc[0])
    d["we_month_row"] = q(f"SELECT month, visits, pct_new_patients FROM `{DEM_BASE}` "
                          f"WHERE mbr_county_cd = '{d['we_county']}' "
                          f"AND specialty_ctg_cd = '{d['we_spec']}' AND year = 2024 "
                          f"ORDER BY month DESC LIMIT 1")
    d["we_prev_row"] = q(f"SELECT HCC_v24, members_with_hcc, members, prevalence "
                         f"FROM `{DEM_CHR}` WHERE mbr_county_cd = '{d['we_county']}' "
                         f"AND month = DATE '2024-12-01' ORDER BY prevalence DESC LIMIT 1")
    d["we_gap_row"] = q(f"SELECT county, cms_specialty, demand_next_12m_xgb, "
                        f"capacity_next_12m_bottom_up, gap_next_12m FROM `{WEAVE}` "
                        f"WHERE county = '{d['we_county']}' AND cms_specialty IN "
                        f"(SELECT cms_specialty FROM `{XWALK}` WHERE aetna_cd = '{d['we_spec']}') "
                        f"AND gap_next_12m IS NOT NULL "
                        f"ORDER BY ABS(gap_next_12m) DESC LIMIT 1")
    if d["we_gap_row"].empty:
        d["we_gap_row"] = q(f"SELECT county, cms_specialty, demand_next_12m_xgb, "
                            f"capacity_next_12m_bottom_up, gap_next_12m FROM `{WEAVE}` "
                            f"WHERE county = '{d['we_county']}' AND gap_next_12m IS NOT NULL "
                            f"ORDER BY ABS(gap_next_12m) DESC LIMIT 1")
    return d


# ---------- tab 1: Overview ----------
def build_overview(wb):
    ws = wb.create_sheet("Overview")
    for col, w in {"A": 3, "B": 26, "C": 8, "D": 20, "E": 20, "F": 20, "G": 20, "H": 20}.items():
        ws.column_dimensions[col].width = w
    title(ws, "Medicare Demand vs Capacity - dc_v2",
          "Modeled 2026 estimates | Aetna Medicare | FL OH AZ IL footprint")
    r = 4
    r = section_header(ws, r, 2, 8, "WHAT THIS WORKBOOK IS")
    r = kv(ws, r, "In plain words",
           "This workbook estimates, for every county and medical specialty in our footprint, "
           "how many visits our Medicare members will need in 2026 and how many visits our "
           "contracted providers will deliver. The difference between the two is the gap. The "
           "need estimate comes from a model that learned from two years of our own visit "
           "history, member counts, ages, and health conditions. The delivery estimate comes "
           "from a model that learned each provider's own history and then adds the providers "
           "up by county. The previous method's numbers are shown next to the new ones so the "
           "two can be compared. Predictions are for calendar 2026, produced by models trained "
           "on 2024-2025 history.", h=110)
    r = blank(ws, r)
    r = section_header(ws, r, 2, 8, "THREE CAVEATS - READ BEFORE THE NUMBERS")
    r = kv(ws, r, "Caveat 1",
           "Capacity counts only visits delivered to Aetna members, not the provider's full "
           "practice.", h=26)
    r = kv(ws, r, "Caveat 2",
           "Chronic condition and sickness measures use each visit's main diagnosis only, so "
           "they undercount.", h=26)
    r = kv(ws, r, "Caveat 3",
           "Numbers for counties labeled trust_band = small are too thin to read individually "
           "- use submarket or state rollups there.", h=26)
    ws.freeze_panes = "A3"
    return 0


# ---------- tab 2: Gap 2026 ----------
GAP_COLS = [
    ("county", AS_STORED, 12, None, "left"),
    ("cms_specialty", AS_STORED, 26, None, "left"),
    ("demand_next_12m_xgb",
     "model estimate of visits members in this county will need in 2026",
     16, "#,##0", "right"),
    ("demand_next_1m_xgb",
     "model estimate of visits members in this county will need in January 2026",
     16, "#,##0", "right"),
    ("capacity_next_12m_bottom_up",
     "sum of per-provider model estimates of visits providers in this county will deliver in 2026",
     18, "#,##0", "right"),
    ("capacity_next_1m_bottom_up",
     "sum of per-provider model estimates for January 2026",
     18, "#,##0", "right"),
    ("gap_next_12m",
     "demand_next_12m_xgb minus capacity_next_12m_bottom_up; positive = shortage",
     14, "#,##0", "right"),
    ("demand_p75_annual",
     "same demand computed with the previous method, for comparison",
     15, "#,##0", "right"),
    ("capacity_p75_annual",
     "same capacity computed with the previous method, for comparison",
     15, "#,##0", "right"),
    ("gap_p75",
     "same gap computed with the previous method, for comparison",
     13, "#,##0", "right"),
    ("trust_band",
     "county size label; small = do not read individual numbers",
     11, None, "center"),
]


def build_gap(wb, weave):
    ws = wb.create_sheet("Gap 2026")
    title(ws, "Gap 2026", "one row per county x cms_specialty from dc2_weave",
          ncols=len(GAP_COLS))
    hdr = derived_table(ws, weave, GAP_COLS, 4)
    gap_col = get_column_letter([k for k, *_ in GAP_COLS].index("gap_next_12m") + 1)
    rng = f"{gap_col}{hdr + 1}:{gap_col}{hdr + len(weave)}"
    ws.conditional_formatting.add(rng, CellIsRule(operator="greaterThan", formula=["0"],
                                                  fill=fill(LIGHT_RED)))
    ws.conditional_formatting.add(rng, CellIsRule(operator="lessThan", formula=["0"],
                                                  fill=fill(LIGHT_GREEN)))
    return len(weave)


# ---------- tab 3: Demand Inputs ----------
def build_demand_inputs(wb, d):
    ws = wb.create_sheet("Demand Inputs")
    prev = d["top5_prev"].copy()
    prev["hcc_col"] = prev["HCC_v24"].map(
        lambda c: f"prev_hcc_{int(float(c))}" if str(c).replace('.', '').isdigit()
        else f"prev_hcc_{str(c).strip()}")
    prev_wide = prev.pivot_table(index="mbr_county_cd", columns="hcc_col",
                                 values="prevalence", aggfunc="max").reset_index()
    df = (d["dem_dec"].merge(d["dem_yr"], on="mbr_county_cd", how="outer")
          .merge(prev_wide, on="mbr_county_cd", how="left")
          .sort_values("visits", ascending=False).reset_index(drop=True))
    prev_cols = sorted(c for c in df.columns if c.startswith("prev_hcc_"))
    cols = [
        ("mbr_county_cd", AS_STORED, 12, None, "left"),
        ("members", "distinct members in the county in December 2025", 11, "#,##0", "right"),
        ("mbr_lt65", "members under 65 in December 2025", 10, "#,##0", "right"),
        ("mbr_65_74", "members 65-74 in December 2025", 10, "#,##0", "right"),
        ("mbr_75_84", "members 75-84 in December 2025", 10, "#,##0", "right"),
        ("mbr_85p", "members 85 and over in December 2025", 10, "#,##0", "right"),
        ("pct_new_patients",
         "share of 2025 visits where the member had not seen that provider in the previous 12 months",
         13, "0.0%", "right"),
        ("visits", "total 2025 visits by members of this county", 12, "#,##0", "right"),
    ] + [(c, "share of the county's members with this condition in the trailing 24 months "
             "(December 2025)", 12, "0.0%", "right") for c in prev_cols]
    title(ws, "Demand Inputs", "what the demand model consumed, rolled up per county",
          ncols=len(cols))
    derived_table(ws, df, cols, 4)
    return len(df)


# ---------- tab 4: Capacity Inputs ----------
def build_capacity_inputs(wb, d):
    ws = wb.create_sheet("Capacity Inputs")
    df = d["cap_inputs"]
    cols = [
        ("prvdr_county", AS_STORED, 16, None, "left"),
        ("providers", "distinct providers with 2025 visits in this county", 11, "#,##0", "right"),
        ("panel_members", "sum of provider panel sizes, December 2025", 13, "#,##0", "right"),
        ("panel_lt65", "panel members under 65, December 2025", 11, "#,##0", "right"),
        ("panel_65_74", "panel members 65-74, December 2025", 11, "#,##0", "right"),
        ("panel_75_84", "panel members 75-84, December 2025", 11, "#,##0", "right"),
        ("panel_85p", "panel members 85 and over, December 2025", 11, "#,##0", "right"),
        ("panel_chronic_members",
         "panel members with at least one chronic condition, December 2025",
         14, "#,##0", "right"),
        ("pct_new_patients",
         "share of 2025 visits where the member had not seen that provider in the previous 12 months",
         13, "0.0%", "right"),
        ("tenure_months", "median months since the provider's first claim, December 2025",
         12, "#,##0", "right"),
        ("visits", "total 2025 visits delivered in this county", 12, "#,##0", "right"),
    ]
    title(ws, "Capacity Inputs", "what the capacity model consumed, rolled up per county",
          ncols=len(cols))
    derived_table(ws, df, cols, 4)
    return len(df)


# ---------- tab 5: Worked Examples ----------
def _example(ws, r, heading, caption, rows):
    r = section_header(ws, r, 2, 8, heading)
    ws.merge_cells(f"B{r}:H{r}")
    cell(ws, f"B{r}", caption, italic=True, size=9, color=DARK_GREY)
    r += 1
    for label, value in rows:
        r = kv(ws, r, label, value)
    return blank(ws, r)


def build_worked_examples(wb, d):
    ws = wb.create_sheet("Worked Examples")
    for col, w in {"A": 3, "B": 30, "C": 8, "D": 20, "E": 20, "F": 20, "G": 20, "H": 20}.items():
        ws.column_dimensions[col].width = w
    title(ws, "Worked Examples",
          f"largest county x specialty cell by 2024 visits: county {d['we_county']}, "
          f"specialty {d['we_spec']} - real numbers from the tables")
    r = 4
    n = 0

    if len(d["we_month_row"]):
        m = d["we_month_row"].iloc[0]
        visits = float(m["visits"])
        pct = float(m["pct_new_patients"]) if pd.notna(m["pct_new_patients"]) else 0.0
        new_visits = round(pct * visits)
        r = _example(ws, r, "EXAMPLE A - one month's pct_new_patients",
                     f"For {m['month']}, the share of visits made by members who had not seen "
                     f"that provider in the previous 12 months.",
                     [("numerator (new visits)", f"{new_visits:,.0f}"),
                      ("denominator (total visits)", f"{visits:,.0f}"),
                      ("result (pct_new_patients)",
                       f"{new_visits:,.0f} / {visits:,.0f} = {pct:.4f}")])
        n += 1

    if len(d["we_prev_row"]):
        p = d["we_prev_row"].iloc[0]
        r = _example(ws, r, "EXAMPLE B - one prev_hcc value",
                     f"For December 2024, condition {p['HCC_v24']}: the share of the county's "
                     f"members with that condition in the trailing 24 months.",
                     [("numerator (members with the condition)",
                       f"{float(p['members_with_hcc']):,.0f}"),
                      ("denominator (county members)", f"{float(p['members']):,.0f}"),
                      ("result (prevalence)",
                       f"{float(p['members_with_hcc']):,.0f} / {float(p['members']):,.0f} "
                       f"= {float(p['prevalence']):.4f}")])
        n += 1

    if len(d["we_gap_row"]):
        g = d["we_gap_row"].iloc[0]
        dem = float(g["demand_next_12m_xgb"]) if pd.notna(g["demand_next_12m_xgb"]) else 0.0
        cap = float(g["capacity_next_12m_bottom_up"]) if pd.notna(g["capacity_next_12m_bottom_up"]) else 0.0
        gap = float(g["gap_next_12m"]) if pd.notna(g["gap_next_12m"]) else dem - cap
        r = _example(ws, r, "EXAMPLE C - how gap_next_12m was computed",
                     f"County {g['county']}, specialty {g['cms_specialty']}: demand estimate "
                     f"minus capacity estimate; positive = shortage.",
                     [("numerator (demand_next_12m_xgb)", f"{dem:,.0f}"),
                      ("denominator (capacity_next_12m_bottom_up)", f"{cap:,.0f}"),
                      ("result (gap_next_12m)", f"{dem:,.0f} - {cap:,.0f} = {gap:,.0f}")])
        n += 1
    ws.freeze_panes = "A3"
    return n


# ---------- tabs 6-7: summaries ----------
SUM_MEASURES = ["demand_next_12m_xgb", "capacity_next_12m_bottom_up", "gap_next_12m",
                "demand_p75_annual", "capacity_p75_annual", "gap_p75"]
SUM_DERIVATIONS = {
    "demand_next_12m_xgb": "sum of the model's 2026 demand estimates",
    "capacity_next_12m_bottom_up": "sum of the model's 2026 capacity estimates",
    "gap_next_12m": "summed demand minus summed capacity; positive = shortage",
    "demand_p75_annual": "same demand, previous method",
    "capacity_p75_annual": "same capacity, previous method",
    "gap_p75": "same gap, previous method",
}


def _summary_cols(first_key, first_width):
    return ([(first_key, AS_STORED if first_key != "state" else
              "first two digits of county mapped to the state code", first_width, None, "left")]
            + [(m, SUM_DERIVATIONS[m], 16, "#,##0", "right") for m in SUM_MEASURES])


def build_summary_specialty(wb, weave):
    ws = wb.create_sheet("Summary by cms_specialty")
    grp = weave.groupby("cms_specialty", as_index=False)[SUM_MEASURES].sum(min_count=1)
    grp = grp.sort_values("gap_next_12m", ascending=False).reset_index(drop=True)
    cols = _summary_cols("cms_specialty", 28)
    title(ws, "Summary by cms_specialty", "summed across counties per specialty",
          ncols=len(cols))
    derived_table(ws, grp, cols, 4)
    return len(grp)


def build_summary_state(wb, weave):
    ws = wb.create_sheet("Summary by State")
    df = weave.copy()
    df["state"] = df["county"].astype(str).str[:2].map(STATE_BY_FIPS).fillna("OTHER")
    grp = df.groupby("state", as_index=False)[SUM_MEASURES].sum(min_count=1)
    grp = grp.sort_values("state").reset_index(drop=True)
    cols = _summary_cols("state", 10)
    title(ws, "Summary by State", "summed per state (state from the county code prefix)",
          ncols=len(cols))
    derived_table(ws, grp, cols, 4)
    return len(grp)


# ---------- tab 8: Data Dictionary ----------
DICT_ROWS = [
    ("county", "member or provider county code; the join key of the table", "dc2_weave", "stored"),
    ("cms_specialty", "CMS specialty name after the one-time bridge from claims specialty codes", "dc2_weave", "stored"),
    ("demand_next_12m_xgb", "model estimate of visits the county's members will need in 2026", "dc2_demand_predictions (future rows)", "derived"),
    ("demand_next_1m_xgb", "model estimate of visits needed in January 2026", "dc2_demand_predictions (future rows)", "derived"),
    ("capacity_next_12m_bottom_up", "sum of per-provider model estimates of visits delivered in 2026", "dc2_capacity_predictions (future rows)", "derived"),
    ("capacity_next_1m_bottom_up", "sum of per-provider estimates for January 2026", "dc2_capacity_predictions (future rows)", "derived"),
    ("gap_next_12m", "demand_next_12m_xgb minus capacity_next_12m_bottom_up; positive = shortage", "computed in 55_weave.py", "derived"),
    ("demand_p75_annual", "demand from the previous method, for comparison", "dc2_p75_baseline (v1 pipeline 30-38)", "stored"),
    ("capacity_p75_annual", "capacity from the previous method, for comparison", "dc2_p75_baseline (v1 pipeline 30-38)", "stored"),
    ("gap_p75", "demand_p75_annual minus capacity_p75_annual", "computed in 55_weave.py", "derived"),
    ("trust_band", "county size label from 2024 visits: small under 10k, mid 10k-100k, large over 100k", "dc2_capacity_county", "derived"),
]


def build_dictionary(wb):
    ws = wb.create_sheet("Data Dictionary")
    dd = pd.DataFrame(DICT_ROWS, columns=["column", "meaning", "source", "derived_or_stored"])
    cols = [("column", "column name, verbatim", 28, None, "left"),
            ("meaning", "plain-words meaning", 66, None, "left"),
            ("source", "source table", 38, None, "left"),
            ("derived_or_stored", "derived here or stored upstream", 16, None, "center")]
    title(ws, "Data Dictionary", "every dc2_weave column", ncols=len(cols))
    derived_table(ws, dd, cols, 4)
    return len(dd)


# ---------- tab 9: Methodology ----------
def build_methodology(wb):
    ws = wb.create_sheet("Methodology")
    for col, w in {"A": 3, "B": 30, "C": 8, "D": 20, "E": 20, "F": 20, "G": 20, "H": 20}.items():
        ws.column_dimensions[col].width = w
    title(ws, "Methodology", "plain words, one paragraph per step")
    r = 4
    r = section_header(ws, r, 2, 8, "THE STEPS")
    r = kv(ws, r, "The demand model",
           "One model, trained on every county and specialty together, using 2024-2025 "
           "history: how many members lived in each county, their ages, how common each "
           "health condition was, the share of first-time patients, and the calendar. It "
           "produces an estimate of visits needed per county and specialty for 2026.", h=64)
    r = kv(ws, r, "The capacity model",
           "One model, trained on every provider together, using each provider's own "
           "history: how many members they saw, the ages and conditions of those members, "
           "how many were new to them, and how long the provider has been in our data. Its "
           "per-provider 2026 estimates are added up to the county level.", h=64)
    r = kv(ws, r, "The previous-method columns",
           "demand_p75_annual, capacity_p75_annual and gap_p75 carry the earlier "
           "rule-of-thumb method (a busy provider's typical volume, trimmed by how full "
           "their patient panel already is). They are shown so the new estimates can be "
           "compared against what was reported before.", h=52)
    r = kv(ws, r, "The gap",
           "gap_next_12m = demand estimate minus capacity estimate for the same county and "
           "specialty. Positive means members are expected to need more visits than the "
           "contracted network is expected to deliver.", h=40)
    r = kv(ws, r, "trust_band",
           "A county size label based on 2024 visit volume: small (under 10 thousand), mid "
           "(10 thousand to 100 thousand), large (over 100 thousand). Small counties have "
           "too little history for their individual numbers to be reliable.", h=40)
    r = kv(ws, r, "Model quality, measured",
           "Checked on 2025 months the models never trained on. The demand annual model "
           "missed by about 655 visits per county-specialty on average. The capacity model "
           "missed by about 18 percent in large counties and about 40 percent in mid "
           "counties; small-county numbers are unreliable and should be read only in "
           "rollups. Both models are gradient boosted trees (XGBoost) - a method that "
           "builds many small decision rules and adds them together.", h=76)
    r = blank(ws, r)
    r = section_header(ws, r, 2, 8, "HOW THE MODELS WORK")
    r = kv(ws, r, "Demand model",
           "One model for all counties and specialties; it learns from member counts, ages, "
           "condition rates, and calendar patterns, and produces a 2026 estimate per county "
           "and specialty.", h=40)
    r = kv(ws, r, "Capacity model",
           "One model for all providers; it learns from panel size, panel ages, panel "
           "conditions, new-patient share, and time in data; per-provider estimates are "
           "summed to county.", h=40)
    r = blank(ws, r)
    r = section_header(ws, r, 2, 8, "OTHER MODEL DESIGNS CONSIDERED")
    r = kv(ws, r, "Separate forecast per geography level with reconciliation",
           "Build numbers at zip, county, and state independently, then force them to "
           "agree. Not chosen: county-only modeling made the agreement step unnecessary.", h=40)
    r = kv(ws, r, "County-level top-down capacity model",
           "Built and tested; lost to the provider-level model on 2025 held-out data in "
           "every county size band (misses roughly 2 to 10 times larger). Kept in the "
           "prediction table for reference, dropped from this report.", h=40)
    r = kv(ws, r, "Cluster-then-model",
           "Group similar counties or providers into roughly 10-20 clusters and fit one "
           "small model per cluster instead of one pooled model. Not chosen this round for "
           "time; noted as the planned approach if per-segment explanations are requested, "
           "and as the cluster-average third estimate in the original design.", h=52)
    ws.freeze_panes = "A3"
    return 0


# ---------- assemble ----------
def main():
    d = load()
    wb = Workbook()
    wb.remove(wb.active)
    counts = {}
    counts["Overview"] = build_overview(wb)
    counts["Gap 2026"] = build_gap(wb, d["weave"])
    counts["Demand Inputs"] = build_demand_inputs(wb, d)
    counts["Capacity Inputs"] = build_capacity_inputs(wb, d)
    counts["Worked Examples"] = build_worked_examples(wb, d)
    counts["Summary by cms_specialty"] = build_summary_specialty(wb, d["weave"])
    counts["Summary by State"] = build_summary_state(wb, d["weave"])
    counts["Data Dictionary"] = build_dictionary(wb)
    counts["Methodology"] = build_methodology(wb)
    wb.save(OUT_XLSX)
    print(f"wrote {OUT_XLSX}")
    for tab, n in counts.items():
        print(f"  {tab}: {n} rows")


if __name__ == "__main__":
    main()
