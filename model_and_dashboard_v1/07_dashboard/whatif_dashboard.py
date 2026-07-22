"""
whatif_dashboard.py - MVP wired to real extracts   [Plotly Dash]

Loads the seven parquet extracts + manifest.json written by
21_dashboard_extracts.py from ./extracts/ (relative to this file) once
at startup; no queries and no file reads after startup. Demand cascade
per the extract contract (03_DELIVERABLE_DASH.md):
  demand[spec] = calibration_factor[county, spec] x (
      sum over bands of members[band] x BASE_RATE[spec]
    + sum over bands x conditions of members[band] x
      prevalence[county, band, condition] x visit_rate[condition, spec])
Prevalence rows missing for a county x band x condition contribute
zero. Sliders default to 0 per D11 (baseline = calibrated 2025-consistent
values); each band slider carries a "last year: +X%" context label from
growth_context (county, falling back to the state mean, else n/a).
Capacity is v0 observed-peak per D14 and labeled as such. Providers are
routed within county x specialty by intake_weight; null-weight providers
are excluded from routing and footnoted. County-name bridge: the
enrollment extract carries county codes only, so if no name column is
present the provider table degrades to footprint-wide top providers
with an explicit footnote (routing then approximate).
Run: python model_and_dashboard_v1/07_dashboard/whatif_dashboard.py
Deps: dash, plotly, pandas, pyarrow.
"""

import json
import os

import pandas as pd
import plotly.graph_objects as go
from dash import Dash, Input, Output, State, ctx, dash_table, dcc, html, no_update
from dash.exceptions import PreventUpdate

BANDS = ["60-64", "65-74", "75-84", "85p"]
MASTER_DEFAULT = 0
BAND_DEFAULTS = [0, 0, 0, 0]
TOP_PROVIDERS_SHOWN = 50
TOP_PROVIDERS_STORED_PER_SPEC = 50

GRAY = "#c3c2b7"
GREEN = "#0ca30c"


def _extract_dir():
    try:
        here = os.path.dirname(os.path.abspath(__file__))
    except NameError:
        here = os.getcwd()
    cand = os.path.join(here, "extracts")
    if os.path.isdir(cand):
        return cand
    probe = here
    for _ in range(6):
        cand = os.path.join(probe, "model_and_dashboard_v1", "07_dashboard",
                            "extracts")
        if os.path.isdir(cand):
            return cand
        probe = os.path.dirname(probe)
    raise FileNotFoundError(
        "extracts folder not found - run 21_dashboard_extracts.py first")


def load_extracts():
    d = _extract_dir()
    names = ["enrollment", "growth_context", "sickness_rates", "visit_rates",
             "county_calibration", "providers", "conditions_meta"]
    frames = {n: pd.read_parquet(os.path.join(d, f"{n}.parquet"))
              for n in names}
    with open(os.path.join(d, "manifest.json"), "r", encoding="utf-8") as f:
        manifest = json.load(f)
    return frames, manifest


FRAMES, MANIFEST = load_extracts()

ENR = FRAMES["enrollment"]
GROWTH = FRAMES["growth_context"]
SICK = FRAMES["sickness_rates"]
RATES = FRAMES["visit_rates"]
CAL = FRAMES["county_calibration"]
PROV = FRAMES["providers"]
META = FRAMES["conditions_meta"]

COUNTY_STATE = dict(zip(ENR["mbr_county_cd"], ENR["state_cd"]))
COUNTY_MEMBERS = {
    cty: dict(zip(g["age_band"], g["members"]))
    for cty, g in ENR.groupby("mbr_county_cd")}
_county_totals = ENR.groupby("mbr_county_cd")["members"].sum()
DEFAULT_COUNTY = str(_county_totals.idxmax())
COUNTY_OPTIONS = [
    {"label": f"{COUNTY_STATE[c]} - {c}", "value": c}
    for c in sorted(COUNTY_STATE, key=lambda c: (COUNTY_STATE[c], c))]

GROWTH_LOOKUP = {(r.mbr_county_cd, r.age_band): float(r.last_year_yoy_pct)
                 for r in GROWTH.itertuples()
                 if pd.notna(r.last_year_yoy_pct)}
_state_growth = GROWTH.dropna(subset=["last_year_yoy_pct"]) \
    .groupby(["state_cd", "age_band"])["last_year_yoy_pct"].mean()
STATE_GROWTH = {k: float(v) for k, v in _state_growth.items()}

SPECIALTIES = sorted(RATES["cms_specialty"].unique())
BASE_RATE = {r.cms_specialty: float(r.visit_rate)
             for r in RATES.itertuples() if r.condition == "BASE_RATE"}
RATE_LOOKUP = {}
for r in RATES.itertuples():
    if r.condition in ("BASE_RATE", "OTHER_CONDITION"):
        continue
    if float(r.visit_rate) > 0:
        RATE_LOOKUP.setdefault(str(r.condition), {})[r.cms_specialty] = \
            float(r.visit_rate)

CAL_LOOKUP = {(r.mbr_county_cd, r.cms_specialty): float(r.calibration_factor)
              for r in CAL.itertuples()}

DESC = dict(zip(META["condition"].astype(str), META["description"]))

ENR_NAME_COL = next((c for c in ("county_name", "census_county_nm")
                     if c in ENR.columns), None)
NAME_BRIDGE = ENR_NAME_COL is not None
COUNTY_NAME = dict(zip(ENR["mbr_county_cd"], ENR[ENR_NAME_COL])) \
    if NAME_BRIDGE else {}


def growth_label(county, band):
    v = GROWTH_LOOKUP.get((county, band))
    if v is None:
        v = STATE_GROWTH.get((COUNTY_STATE.get(county), band))
    return f"last year: {v:+.1f}%" if v is not None else "last year: n/a"


def compute_demand(members_by_band, prev, cal):
    total_members = sum(members_by_band)
    cond_members = {c: sum(members_by_band[b] * prev[c][b]
                           for b in range(len(BANDS)))
                    for c in prev}
    demand = {}
    for s in SPECIALTIES:
        visits = total_members * BASE_RATE.get(s, 0.0)
        for c, n in cond_members.items():
            rate = RATE_LOOKUP.get(c, {}).get(s)
            if rate:
                visits += n * rate
        demand[s] = cal.get(s, 1.0) * visits
    return demand, cond_members


def ranks_desc(values_by_key):
    order = sorted(values_by_key, key=lambda k: -values_by_key[k])
    return order, {k: pos + 1 for pos, k in enumerate(order)}


def county_payload(county):
    members = [int(COUNTY_MEMBERS.get(county, {}).get(b, 0)) for b in BANDS]
    labels = [growth_label(county, b) for b in BANDS]
    master_label = growth_label(county, "ALL_BANDS")

    slice_df = SICK[SICK["mbr_county_cd"] == county]
    prev = {}
    for r in slice_df.itertuples():
        cond = str(r.condition)
        if cond not in prev:
            prev[cond] = [0.0] * len(BANDS)
        if r.age_band in BANDS:
            prev[cond][BANDS.index(r.age_band)] = float(r.prevalence)

    cal = {s: CAL_LOOKUP.get((county, s), 1.0) for s in SPECIALTIES}
    base_demand, base_conditions = compute_demand(members, prev, cal)
    _, base_ranks = ranks_desc(base_conditions)

    if NAME_BRIDGE:
        name = str(COUNTY_NAME.get(county, "")).upper()
        prov = PROV[PROV["prvdr_county_clean"] == name]
        prov_mode = f"providers filtered to county {county} ({name})"
    else:
        prov = PROV
        prov_mode = ("county-name bridge missing from the enrollment "
                     "extract: provider list is footprint-wide and routing "
                     "is approximate until a name column ships in 21")
    prov = prov.sort_values("visits_2025", ascending=False)
    prov = prov.groupby("cms_specialty", sort=False) \
               .head(TOP_PROVIDERS_STORED_PER_SPEC)
    providers = []
    for r in prov.itertuples():
        w = getattr(r, "intake_weight")
        providers.append({
            "id": str(r.epdb_dw_prvdr_id),
            "spec": r.cms_specialty,
            "visits": int(r.visits_2025),
            "ceiling": int(r.ceiling_annual_v0),
            "weight": None if pd.isna(w) else float(w),
        })

    return {
        "county": county,
        "members": members,
        "labels": labels,
        "master_label": master_label,
        "prev": prev,
        "cal": cal,
        "base_demand": base_demand,
        "base_conditions": base_conditions,
        "base_ranks": base_ranks,
        "providers": providers,
        "prov_mode": prov_mode,
    }


def fmt(n):
    return f"{n:,.0f}"


def pct(n):
    return f"{n:+.1%}"


def status_of(utilization):
    if utilization > 1.00:
        return "OVER CAPACITY"
    if utilization >= 0.90:
        return "AT CAPACITY"
    return "HEADROOM"


def cond_label(cond):
    desc = str(DESC.get(cond, "")).strip()
    return f"{desc[:40]} ({cond})" if desc else cond


TABLE_STYLE = {
    "style_table": {"overflowX": "auto"},
    "style_cell": {"fontFamily": "system-ui, sans-serif", "fontSize": "13px",
                   "padding": "6px 10px", "textAlign": "left"},
    "style_header": {"fontWeight": "600"},
}

PROXY_PREFIX = os.environ.get("DASH_PROXY_PREFIX")
if PROXY_PREFIX:
    app = Dash(__name__, requests_pathname_prefix=PROXY_PREFIX)
else:
    app = Dash(__name__)

SCOPE_TEXT = [
    "Demographic changes move condition counts, and condition counts move "
    "specialty demand - that chain is the actual arithmetic of this page, "
    "not an illustration.",
    "Demand is calibrated toward 2025 actual visit levels per county and "
    "specialty. With all sliders at 0 the page sits near - not exactly at - "
    "2025 actuals: the calibration factors were fitted on a chain that "
    "includes an OTHER_CONDITION term this page cannot carry (no prevalence "
    "exists for it), so baseline runs below actuals by that share. Slider "
    "deltas are shaped by the fitted condition-to-specialty rates.",
    "Capacity is v0: each provider's ceiling is their own highest observed "
    "month (2024-2025) times 12 - observed data, not a model - and it is "
    "Aetna-relative: only Aetna claims are visible, so a provider busy with "
    "other payers may look like they have room here when they do not.",
    "Routing assumes future patients choose providers the way past patients "
    "did: new demand flows in proportion to historical new-patient shares. "
    "Prevalence, visit rates and calibration factors are frozen during "
    "simulation; the sliders move enrollment only.",
    "This tool is directional, not a precise forecaster.",
]

MANIFEST_LINE = (f"extracts built {MANIFEST.get('built_at', 'n/a')} - "
                 f"{MANIFEST.get('note', '')}")


def kpi_tile(tile_id, label):
    return html.Div(style={"flex": "1", "minWidth": "170px",
                           "background": "#fcfcfb",
                           "border": "1px solid rgba(11,11,11,0.10)",
                           "borderRadius": "10px", "padding": "10px 14px"},
                    children=[
                        html.Div(label, style={"fontSize": "12px",
                                               "color": "#52514e"}),
                        html.Div(id=tile_id,
                                 style={"fontSize": "24px",
                                        "fontWeight": "650"}),
                    ])


def slider_block(slider_id, readout_id, info_id, label, mn, mx, marks):
    return html.Div(style={"marginBottom": "16px"}, children=[
        html.Div(style={"display": "flex", "justifyContent": "space-between",
                        "alignItems": "baseline"},
                 children=[
                     html.Label(label, style={"fontSize": "13px",
                                              "fontWeight": "600"}),
                     html.Span(id=info_id,
                               style={"fontSize": "11px", "color": "#2a78d6"}),
                 ]),
        dcc.Slider(id=slider_id, min=mn, max=mx, step=5, value=0,
                   marks=marks, tooltip={"placement": "bottom"}),
        html.Div(id=readout_id, style={"fontSize": "12px",
                                       "color": "#52514e"}),
    ])


app.layout = html.Div(
    style={"maxWidth": "1200px", "margin": "0 auto", "padding": "16px",
           "fontFamily": "system-ui, sans-serif"},
    children=[
        dcc.Store(id="store-sync", data=None),
        dcc.Store(id="store-county", data=None),
        html.Div("MVP - demand calibrated to 2025 actuals; capacity v0 "
                 "(observed peak). All simulation outputs are directional.",
                 style={"background": "#fab219", "color": "#0b0b0b",
                        "fontWeight": "700", "textAlign": "center",
                        "padding": "8px", "borderRadius": "8px",
                        "marginBottom": "4px", "letterSpacing": "0.02em"}),
        html.Div(MANIFEST_LINE,
                 style={"fontSize": "11px", "color": "#52514e",
                        "textAlign": "center", "marginBottom": "12px"}),
        html.Div(style={"display": "flex", "gap": "16px",
                        "alignItems": "center", "marginBottom": "12px"},
                 children=[
                     html.Div(style={"minWidth": "300px"}, children=[
                         dcc.Dropdown(id="dd-county", options=COUNTY_OPTIONS,
                                      value=DEFAULT_COUNTY, clearable=False)]),
                     html.Button("Reset", id="btn-reset", n_clicks=0,
                                 style={"padding": "6px 18px",
                                        "fontSize": "13px",
                                        "cursor": "pointer"}),
                 ]),
        html.Details(open=True, style={"marginBottom": "14px"}, children=[
            html.Summary("Scope", style={"fontWeight": "600",
                                         "cursor": "pointer"}),
            html.Div([html.P(t) for t in SCOPE_TEXT],
                     style={"fontSize": "13px", "color": "#52514e",
                            "maxWidth": "820px"}),
        ]),
        html.Div(style={"display": "flex", "gap": "20px",
                        "alignItems": "flex-start"},
                 children=[
                     html.Div(style={"flex": "0 0 30%",
                                     "background": "#fcfcfb",
                                     "border": "1px solid rgba(11,11,11,0.10)",
                                     "borderRadius": "10px",
                                     "padding": "14px"},
                              children=[
                                  slider_block(
                                      "s-master", "r-master", "i-master",
                                      "Master: total enrollment (%)",
                                      -30, 50,
                                      {-30: "-30", 0: "0", 25: "+25",
                                       50: "+50"}),
                                  slider_block(
                                      "s-b0", "r-b0", "i-b0",
                                      "Band 60-64 (%)", -30, 100,
                                      {-30: "-30", 0: "0", 50: "+50",
                                       100: "+100"}),
                                  slider_block(
                                      "s-b1", "r-b1", "i-b1",
                                      "Band 65-74 (%)", -30, 100,
                                      {-30: "-30", 0: "0", 50: "+50",
                                       100: "+100"}),
                                  slider_block(
                                      "s-b2", "r-b2", "i-b2",
                                      "Band 75-84 (%)", -30, 100,
                                      {-30: "-30", 0: "0", 50: "+50",
                                       100: "+100"}),
                                  slider_block(
                                      "s-b3", "r-b3", "i-b3",
                                      "Band 85+ (%)", -30, 100,
                                      {-30: "-30", 0: "0", 50: "+50",
                                       100: "+100"}),
                                  html.Div("Moving the master sets every band "
                                           "to its value; band sliders then "
                                           "adjust one band at a time.",
                                           style={"fontSize": "12px",
                                                  "color": "#898781"}),
                                  html.Div("Defaults are 0 (D11): last year's "
                                           "change is shown as context, never "
                                           "as a starting position.",
                                           style={"fontSize": "12px",
                                                  "color": "#898781"}),
                              ]),
                     html.Div(style={"flex": "1"}, children=[
                         html.Div(style={"display": "flex", "gap": "12px",
                                         "flexWrap": "wrap",
                                         "marginBottom": "14px"},
                                  children=[
                                      kpi_tile("k-enroll",
                                               "Scenario total enrollment"),
                                      kpi_tile("k-visits",
                                               "Scenario total annual visits"),
                                      kpi_tile("k-at", "Providers at capacity"),
                                      kpi_tile("k-over",
                                               "Providers over capacity"),
                                  ]),
                         html.H4("Enrollment by band"),
                         dash_table.DataTable(
                             id="tbl-enroll",
                             columns=[{"name": c, "id": c} for c in
                                      ["band", "baseline", "scenario",
                                       "delta", "pct_change"]],
                             **TABLE_STYLE),
                         dcc.RadioItems(id="rad-topn",
                                        options=[{"label": "Top 10",
                                                  "value": 10},
                                                 {"label": "Top 20",
                                                  "value": 20}],
                                        value=10, inline=True,
                                        style={"fontSize": "13px",
                                               "margin": "14px 0 6px"},
                                        inputStyle={"marginRight": "4px"},
                                        labelStyle={"marginRight": "16px"}),
                         html.Div(style={"display": "flex", "gap": "16px",
                                         "alignItems": "flex-start"},
                                  children=[
                                      html.Div(style={"flex": "1",
                                                      "minWidth": "0"},
                                               children=[
                                                   dcc.Graph(
                                                       id="fig-specialty",
                                                       config={"displayModeBar":
                                                               False})]),
                                      html.Div(style={"flex": "1",
                                                      "minWidth": "0"},
                                               children=[
                                                   dcc.Graph(
                                                       id="fig-condition",
                                                       config={"displayModeBar":
                                                               False})]),
                                  ]),
                         html.H4("Condition mix"),
                         html.Div("Members can have multiple conditions; "
                                  "column does not sum to enrollment.",
                                  style={"fontSize": "12px",
                                         "color": "#898781",
                                         "marginBottom": "6px"}),
                         dash_table.DataTable(
                             id="tbl-condition",
                             columns=[{"name": c, "id": c} for c in
                                      ["rank", "movement", "condition",
                                       "baseline_members", "scenario_members",
                                       "delta", "pct_change"]],
                             **TABLE_STYLE),
                         html.H4(f"Demand by specialty "
                                 f"({len(SPECIALTIES)} fitted)"),
                         html.Div(style={"maxWidth": "520px",
                                         "marginBottom": "8px"},
                                  children=[
                                      dcc.Dropdown(id="dd-specialty",
                                                   options=SPECIALTIES,
                                                   value=SPECIALTIES,
                                                   multi=True)]),
                         dash_table.DataTable(
                             id="tbl-demand",
                             columns=[{"name": c, "id": c} for c in
                                      ["specialty", "baseline_visits",
                                       "scenario_visits", "delta",
                                       "pct_change"]],
                             **TABLE_STYLE),
                         html.H4("Provider load vs capacity (v0 ceilings: "
                                 "observed peak month x 12)"),
                         html.Div(id="lbl-provider-note",
                                  style={"fontSize": "12px",
                                         "color": "#898781",
                                         "marginBottom": "6px"}),
                         dash_table.DataTable(
                             id="tbl-provider",
                             columns=[{"name": c, "id": c} for c in
                                      ["provider", "specialty",
                                       "current_visits", "estimated_max",
                                       "new_demand_coming", "room_left",
                                       "status"]],
                             style_data_conditional=[
                                 {"if": {"filter_query":
                                         '{status} = "HEADROOM"',
                                         "column_id": "status"},
                                  "color": "#006300",
                                  "backgroundColor":
                                      "rgba(12,163,12,0.12)",
                                  "fontWeight": "600"},
                                 {"if": {"filter_query":
                                         '{status} = "AT CAPACITY"',
                                         "column_id": "status"},
                                  "color": "#8a5a00",
                                  "backgroundColor":
                                      "rgba(250,178,25,0.18)",
                                  "fontWeight": "600"},
                                 {"if": {"filter_query":
                                         '{status} = "OVER CAPACITY"',
                                         "column_id": "status"},
                                  "color": "#d03b3b",
                                  "backgroundColor":
                                      "rgba(208,59,59,0.14)",
                                  "fontWeight": "600"},
                             ],
                             **TABLE_STYLE),
                     ]),
                 ]),
        html.Details(open=False, style={"marginTop": "18px"}, children=[
            html.Summary("Extract manifest",
                         style={"fontWeight": "600", "cursor": "pointer"}),
            dash_table.DataTable(
                columns=[{"name": c, "id": c} for c in
                         ["extract", "row_count", "source"]],
                data=[{"extract": k, "row_count": v.get("row_count"),
                       "source": v.get("source")}
                      for k, v in MANIFEST.get("extracts", {}).items()],
                **TABLE_STYLE),
        ]),
    ])


def build_hbar_figure(title, items):
    fig = go.Figure()
    n = len(items)
    y = list(range(n))
    names, base_seg, grow_seg, labels, dash_x, dash_y = [], [], [], [], [], []
    max_x = 1.0
    for i, (name, base, scen) in enumerate(items):
        delta = scen - base
        names.append(name)
        if scen >= base:
            base_seg.append(base)
            grow_seg.append(delta)
        else:
            base_seg.append(scen)
            grow_seg.append(0)
            dash_x.extend([base, base, None])
            dash_y.extend([i - 0.35, i + 0.35, None])
        labels.append(f"{fmt(scen)} ({delta:+,.0f})")
        max_x = max(max_x, base, scen)
    fig.add_trace(go.Bar(y=y, x=base_seg, orientation="h",
                         marker_color=GRAY, name="Baseline"))
    fig.add_trace(go.Bar(y=y, x=grow_seg, orientation="h",
                         marker_color=GREEN, name="Growth"))
    if dash_x:
        fig.add_trace(go.Scatter(x=dash_x, y=dash_y, mode="lines",
                                 line={"color": "#52514e", "dash": "dash",
                                       "width": 2},
                                 name="Baseline level"))
    for i, (name, base, scen) in enumerate(items):
        fig.add_annotation(x=max(base, scen), y=i, text=labels[i],
                           showarrow=False, xanchor="left", xshift=6,
                           font={"size": 11})
    fig.update_layout(barmode="stack",
                      height=90 + 34 * max(n, 1),
                      margin={"l": 10, "r": 10, "t": 40, "b": 30},
                      title={"text": title, "font": {"size": 14}},
                      yaxis={"tickvals": y, "ticktext": names,
                             "autorange": "reversed"},
                      xaxis={"range": [0, max_x * 1.28]},
                      legend={"orientation": "h", "y": -0.18},
                      plot_bgcolor="#fcfcfb", paper_bgcolor="#fcfcfb")
    return fig


@app.callback(
    Output("store-county", "data"),
    Output("i-master", "children"),
    Output("i-b0", "children"),
    Output("i-b1", "children"),
    Output("i-b2", "children"),
    Output("i-b3", "children"),
    Input("dd-county", "value"),
)
def select_county(county):
    payload = county_payload(county or DEFAULT_COUNTY)
    return (payload, payload["master_label"], payload["labels"][0],
            payload["labels"][1], payload["labels"][2], payload["labels"][3])


@app.callback(
    Output("s-master", "value"),
    Output("s-b0", "value"),
    Output("s-b1", "value"),
    Output("s-b2", "value"),
    Output("s-b3", "value"),
    Output("store-sync", "data"),
    Output("r-master", "children"),
    Output("r-b0", "children"),
    Output("r-b1", "children"),
    Output("r-b2", "children"),
    Output("r-b3", "children"),
    Output("k-enroll", "children"),
    Output("k-visits", "children"),
    Output("k-at", "children"),
    Output("k-over", "children"),
    Output("tbl-enroll", "data"),
    Output("tbl-condition", "data"),
    Output("fig-specialty", "figure"),
    Output("fig-condition", "figure"),
    Output("tbl-demand", "data"),
    Output("tbl-provider", "data"),
    Output("lbl-provider-note", "children"),
    Input("s-master", "value"),
    Input("s-b0", "value"),
    Input("s-b1", "value"),
    Input("s-b2", "value"),
    Input("s-b3", "value"),
    Input("btn-reset", "n_clicks"),
    Input("dd-specialty", "value"),
    Input("rad-topn", "value"),
    Input("store-county", "data"),
    State("store-sync", "data"),
)
def update(master, b0, b1, b2, b3, _reset_clicks, selected_specialties,
           top_n, payload, sync_token):
    if not payload:
        raise PreventUpdate
    trigger = ctx.triggered_id
    master = master if master is not None else MASTER_DEFAULT
    bands = [v if v is not None else BAND_DEFAULTS[i]
             for i, v in enumerate([b0, b1, b2, b3])]

    slider_out = [no_update] * 5
    store_out = no_update
    if trigger == "btn-reset":
        bands = list(BAND_DEFAULTS)
        slider_out = [MASTER_DEFAULT] + list(BAND_DEFAULTS)
        store_out = MASTER_DEFAULT if master != MASTER_DEFAULT else None
    elif trigger == "s-master":
        if sync_token is not None and master == sync_token:
            store_out = None
        else:
            bands = [master] * len(BANDS)
            slider_out = [no_update] + [master] * len(BANDS)
            store_out = None

    base_members = payload["members"]
    prev = payload["prev"]
    cal = payload["cal"]
    base_demand = payload["base_demand"]
    base_conditions = payload["base_conditions"]
    base_ranks = payload["base_ranks"]

    members = [max(0.0, base_members[i] * (1 + bands[i] / 100.0))
               for i in range(len(BANDS))]
    demand, cond_now = compute_demand(members, prev, cal)
    deltas = {s: demand[s] - base_demand.get(s, 0.0) for s in SPECIALTIES}

    total_enroll = sum(members)
    total_base = sum(base_members)
    readout_master = (
        f"Total: {fmt(total_enroll)} members "
        f"({pct((total_enroll - total_base) / total_base) if total_base else 'n/a'} vs baseline)")
    band_names = ["60-64", "65-74", "75-84", "85+"]
    readouts = [f"{band_names[i]}: {bands[i]:+d}% -> {fmt(members[i])} members"
                for i in range(len(BANDS))]

    enroll_rows = []
    for i, band in enumerate(band_names):
        base = base_members[i]
        d = members[i] - base
        enroll_rows.append({
            "band": band, "baseline": fmt(base), "scenario": fmt(members[i]),
            "delta": f"{d:+,.0f}",
            "pct_change": pct(d / base) if base else "n/a"})

    n_show = top_n if top_n is not None else 10
    order, cur_ranks = ranks_desc(cond_now)
    condition_rows = []
    for pos, c in enumerate(order[:n_show]):
        base_c = base_conditions.get(c, 0.0)
        d = cond_now[c] - base_c
        moved = base_ranks.get(c, cur_ranks[c]) - cur_ranks[c]
        if moved > 0:
            movement = f"^{moved}"
        elif moved < 0:
            movement = f"v{-moved}"
        else:
            movement = "-"
        condition_rows.append({
            "rank": pos + 1,
            "movement": movement,
            "condition": cond_label(c),
            "baseline_members": fmt(base_c),
            "scenario_members": fmt(cond_now[c]),
            "delta": f"{d:+,.0f}",
            "pct_change": pct(d / base_c) if base_c else "n/a"})
    rest = order[n_show:]
    if rest:
        rest_base = sum(base_conditions.get(c, 0.0) for c in rest)
        rest_now = sum(cond_now[c] for c in rest)
        condition_rows.append({
            "rank": "", "movement": "",
            "condition": f"Other ({len(rest)} conditions)",
            "baseline_members": fmt(rest_base),
            "scenario_members": fmt(rest_now),
            "delta": f"{rest_now - rest_base:+,.0f}",
            "pct_change": ""})

    selected = set(selected_specialties or [])
    demand_rows = []
    for s in SPECIALTIES:
        if s not in selected:
            continue
        base_s = base_demand.get(s, 0.0)
        demand_rows.append({
            "specialty": s, "baseline_visits": fmt(base_s),
            "scenario_visits": fmt(demand[s]),
            "delta": f"{deltas[s]:+,.0f}",
            "pct_change": pct(deltas[s] / base_s) if base_s else "n/a"})

    n_at = 0
    n_over = 0
    no_weight = 0
    provider_rows = []
    for p in payload["providers"]:
        w = p["weight"]
        if w is None:
            no_weight += 1
            new_demand = None
        else:
            new_demand = deltas.get(p["spec"], 0.0) * w
        load = p["visits"] + (new_demand or 0.0)
        utilization = load / p["ceiling"] if p["ceiling"] else 0.0
        status = status_of(utilization)
        if status == "AT CAPACITY":
            n_at += 1
        if status == "OVER CAPACITY":
            n_over += 1
        if p["spec"] in selected:
            room = p["ceiling"] - load
            provider_rows.append({
                "_visits": p["visits"],
                "provider": p["id"], "specialty": p["spec"],
                "current_visits": fmt(p["visits"]),
                "estimated_max": f"~{fmt(p['ceiling'])}",
                "new_demand_coming": (f"{new_demand:+,.0f}"
                                      if new_demand is not None else "-"),
                "room_left": (fmt(room) if room >= 0
                              else f"short by {fmt(-room)}"),
                "status": status})
    total_in_selection = len(provider_rows)
    provider_rows = sorted(provider_rows,
                           key=lambda r: -r["_visits"])[:TOP_PROVIDERS_SHOWN]
    for r in provider_rows:
        r.pop("_visits", None)
    note = (f"showing top {len(provider_rows)} of {total_in_selection} "
            f"providers in selection by 2025 visits; {no_weight} carry no "
            f"intake weight (zero 2025 new-patient visits in their cell - "
            f"excluded from routing); {payload['prov_mode']}")

    spec_items = sorted(
        ((s, base_demand.get(s, 0.0), demand[s])
         for s in SPECIALTIES if s in selected),
        key=lambda t: -t[2])[:n_show]
    cond_items = [(cond_label(c), base_conditions.get(c, 0.0), cond_now[c])
                  for c in order[:n_show]]
    fig_specialty = build_hbar_figure(
        f"Specialty demand - top {n_show}", spec_items)
    fig_condition = build_hbar_figure(
        f"Condition members - top {n_show}", cond_items)

    return (slider_out[0], slider_out[1], slider_out[2], slider_out[3],
            slider_out[4], store_out,
            readout_master, readouts[0], readouts[1], readouts[2],
            readouts[3],
            fmt(total_enroll), fmt(sum(demand.values())), str(n_at),
            str(n_over),
            enroll_rows, condition_rows, fig_specialty, fig_condition,
            demand_rows, provider_rows, note)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8050, debug=False)
