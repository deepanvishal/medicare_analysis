import os

import pandas as pd
import plotly.graph_objects as go
from dash import Dash, Input, Output, State, ctx, dash_table, dcc, html, no_update

# ============================== CONFIG ==================================
# All values in this section are fictional mock data; real model extracts
# replace this block in a later phase with no layout change.
COUNTY = "TEST_COUNTY_FL"
BASELINE_ENROLLMENT = 40000
BANDS = ["65-74", "75-84", "85+"]
BAND_SHARES = [0.55, 0.33, 0.12]

MASTER_DEFAULT = 3
BAND_DEFAULTS = [2, 3, 6]

CONDITIONS = ["Diabetes", "CHF", "CKD", "COPD", "Osteoarthritis",
              "Hypertension", "Atrial Fibrillation", "Dementia", "Depression",
              "Stroke History", "Cancer History", "Obesity", "Asthma",
              "Chronic Pain", "Anemia"]
PREVALENCE = [
    [0.25, 0.08, 0.10, 0.10, 0.30, 0.45, 0.05, 0.02, 0.14,
     0.04, 0.10, 0.28, 0.08, 0.18, 0.08],
    [0.30, 0.14, 0.18, 0.14, 0.42, 0.58, 0.10, 0.08, 0.15,
     0.08, 0.15, 0.24, 0.08, 0.22, 0.14],
    [0.33, 0.22, 0.30, 0.18, 0.52, 0.65, 0.18, 0.18, 0.17,
     0.14, 0.19, 0.18, 0.07, 0.26, 0.24],
]

SPECIALTIES = ["Primary Care", "Cardiology", "Nephrology", "Orthopedics"]
VISIT_RATES = [
    [1.2, 0.4, 0.3, 0.0],
    [0.8, 2.6, 0.2, 0.0],
    [0.6, 0.3, 2.8, 0.0],
    [1.0, 0.3, 0.0, 0.0],
    [0.4, 0.0, 0.0, 1.8],
    [0.8, 0.6, 0.1, 0.0],
    [0.4, 1.8, 0.0, 0.0],
    [1.0, 0.0, 0.0, 0.0],
    [0.8, 0.0, 0.0, 0.0],
    [0.5, 0.9, 0.0, 0.0],
    [0.6, 0.0, 0.0, 0.0],
    [0.5, 0.0, 0.0, 0.3],
    [0.7, 0.0, 0.0, 0.0],
    [0.6, 0.0, 0.0, 0.9],
    [0.4, 0.0, 0.3, 0.0],
]
BASE_PCP_RATE = 2.0

PROVIDERS = [
    {"id": "P01", "name": "Lakeside Primary Group", "specialty": "Primary Care",
     "intake_weight": 0.35, "current_annual_visits": 26000, "capacity_ceiling": 30500},
    {"id": "P02", "name": "Oak Street Family Med", "specialty": "Primary Care",
     "intake_weight": 0.30, "current_annual_visits": 22500, "capacity_ceiling": 27000},
    {"id": "P03", "name": "Community Health Assoc", "specialty": "Primary Care",
     "intake_weight": 0.20, "current_annual_visits": 15200, "capacity_ceiling": 17500},
    {"id": "P04", "name": "Meridian Family Care", "specialty": "Primary Care",
     "intake_weight": 0.15, "current_annual_visits": 11000, "capacity_ceiling": 14500},
    {"id": "P05", "name": "Meridian Heart Center", "specialty": "Cardiology",
     "intake_weight": 0.45, "current_annual_visits": 12400, "capacity_ceiling": 13000},
    {"id": "P06", "name": "Riverside Cardiology", "specialty": "Cardiology",
     "intake_weight": 0.35, "current_annual_visits": 9300, "capacity_ceiling": 11800},
    {"id": "P07", "name": "Pulse Cardiovascular", "specialty": "Cardiology",
     "intake_weight": 0.20, "current_annual_visits": 5600, "capacity_ceiling": 6400},
    {"id": "P08", "name": "Kidney Care Partners", "specialty": "Nephrology",
     "intake_weight": 1.00, "current_annual_visits": 13900, "capacity_ceiling": 13600},
    {"id": "P09", "name": "Summit Orthopedics", "specialty": "Orthopedics",
     "intake_weight": 0.60, "current_annual_visits": 15500, "capacity_ceiling": 16400},
    {"id": "P10", "name": "Motion Ortho Clinic", "specialty": "Orthopedics",
     "intake_weight": 0.40, "current_annual_visits": 10200, "capacity_ceiling": 12600},
]
# ============================ end CONFIG ================================

BASE_BAND_ENROLLMENT = [BASELINE_ENROLLMENT * s for s in BAND_SHARES]

GRAY = "#c3c2b7"
GREEN = "#0ca30c"


def band_enrollment(band_pcts):
    return [max(0.0, e * (1 + band_pcts[i] / 100.0))
            for i, e in enumerate(BASE_BAND_ENROLLMENT)]


def demand_by_specialty(enrollment):
    demand = [0.0] * len(SPECIALTIES)
    for b in range(len(BANDS)):
        for c in range(len(CONDITIONS)):
            affected = enrollment[b] * PREVALENCE[b][c]
            for s in range(len(SPECIALTIES)):
                demand[s] += affected * VISIT_RATES[c][s]
        demand[SPECIALTIES.index("Primary Care")] += enrollment[b] * BASE_PCP_RATE
    return demand


def condition_members(enrollment):
    return [sum(enrollment[b] * PREVALENCE[b][c] for b in range(len(BANDS)))
            for c in range(len(CONDITIONS))]


def ranks_desc(values):
    order = sorted(range(len(values)), key=lambda i: -values[i])
    ranks = [0] * len(values)
    for pos, i in enumerate(order):
        ranks[i] = pos + 1
    return order, ranks


BASELINE_BANDS = band_enrollment([0, 0, 0])
BASELINE_DEMAND = demand_by_specialty(BASELINE_BANDS)
BASELINE_CONDITIONS = condition_members(BASELINE_BANDS)
_, BASELINE_COND_RANKS = ranks_desc(BASELINE_CONDITIONS)


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


PREV_TABLE = pd.DataFrame(
    [[BANDS[b]] + [f"{PREVALENCE[b][c]:.2f}" for c in range(len(CONDITIONS))]
     for b in range(len(BANDS))],
    columns=["age_band"] + CONDITIONS)

RATE_TABLE = pd.DataFrame(
    [[CONDITIONS[c]] + [f"{VISIT_RATES[c][s]:.1f}" for s in range(len(SPECIALTIES))]
     for c in range(len(CONDITIONS))]
    + [["Base rate (all members)", f"{BASE_PCP_RATE:.1f}", "0.0", "0.0", "0.0"]],
    columns=["condition"] + SPECIALTIES)

PROVIDER_CONFIG_TABLE = pd.DataFrame(PROVIDERS)

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
    "This tool is directional, not a precise forecaster. It exists to show which "
    "way demand and provider load move under an enrollment scenario, and roughly "
    "by how much - not to predict exact visit counts.",
    "Capacity is Aetna-relative: only Aetna claims are visible, so a provider who "
    "is busy with other payers may look like they have room here when they do not.",
    "Routing assumes future patients choose providers the way past patients did - "
    "new demand flows to each provider in proportion to their historical intake. "
    "Prevalence and visit rates are frozen during simulation; the sliders move "
    "enrollment only.",
    "All numbers on this page are fictional mock values. In a later phase, the "
    "models supply real coefficients and this page's layout does not change.",
]


def expected_marks(base_marks, default_value):
    marks = dict(base_marks)
    marks[default_value] = {"label": "expected",
                            "style": {"color": "#2a78d6", "fontWeight": "600"}}
    return marks


def kpi_tile(tile_id, label):
    return html.Div(style={"flex": "1", "minWidth": "170px", "background": "#fcfcfb",
                           "border": "1px solid rgba(11,11,11,0.10)",
                           "borderRadius": "10px", "padding": "10px 14px"},
                    children=[
                        html.Div(label, style={"fontSize": "12px", "color": "#52514e"}),
                        html.Div(id=tile_id,
                                 style={"fontSize": "24px", "fontWeight": "650"}),
                    ])


def slider_block(slider_id, readout_id, label, mn, mx, marks, default_value):
    return html.Div(style={"marginBottom": "16px"}, children=[
        html.Label(label, style={"fontSize": "13px", "fontWeight": "600"}),
        dcc.Slider(id=slider_id, min=mn, max=mx, step=5, value=default_value,
                   marks=marks, tooltip={"placement": "bottom"}),
        html.Div(id=readout_id, style={"fontSize": "12px", "color": "#52514e"}),
    ])


app.layout = html.Div(
    style={"maxWidth": "1200px", "margin": "0 auto", "padding": "16px",
           "fontFamily": "system-ui, sans-serif"},
    children=[
        dcc.Store(id="store-sync", data=None),
        html.Div("MOCK DATA - phase 1 mechanics test",
                 style={"background": "#fab219", "color": "#0b0b0b",
                        "fontWeight": "700", "textAlign": "center",
                        "padding": "8px", "borderRadius": "8px",
                        "marginBottom": "14px", "letterSpacing": "0.04em"}),
        html.Div(style={"display": "flex", "gap": "16px", "alignItems": "center",
                        "marginBottom": "12px"},
                 children=[
                     html.Div(style={"minWidth": "260px"}, children=[
                         dcc.Dropdown(id="dd-county", options=[COUNTY],
                                      value=COUNTY, clearable=False)]),
                     html.Button("Reset", id="btn-reset", n_clicks=0,
                                 style={"padding": "6px 18px", "fontSize": "13px",
                                        "cursor": "pointer"}),
                 ]),
        html.Details(open=True, style={"marginBottom": "14px"}, children=[
            html.Summary("Scope", style={"fontWeight": "600", "cursor": "pointer"}),
            html.Div([html.P(t) for t in SCOPE_TEXT],
                     style={"fontSize": "13px", "color": "#52514e",
                            "maxWidth": "820px"}),
        ]),
        html.Div(style={"display": "flex", "gap": "20px", "alignItems": "flex-start"},
                 children=[
                     html.Div(style={"flex": "0 0 30%", "background": "#fcfcfb",
                                     "border": "1px solid rgba(11,11,11,0.10)",
                                     "borderRadius": "10px", "padding": "14px"},
                              children=[
                                  slider_block(
                                      "s-master", "r-master",
                                      "Master: total enrollment (%)", -30, 50,
                                      expected_marks({-30: "-30", 0: "0", 25: "+25",
                                                      50: "+50"}, MASTER_DEFAULT),
                                      MASTER_DEFAULT),
                                  slider_block(
                                      "s-b0", "r-b0", "Band 65-74 (%)", -30, 100,
                                      expected_marks({-30: "-30", 0: "0", 50: "+50",
                                                      100: "+100"}, BAND_DEFAULTS[0]),
                                      BAND_DEFAULTS[0]),
                                  slider_block(
                                      "s-b1", "r-b1", "Band 75-84 (%)", -30, 100,
                                      expected_marks({-30: "-30", 0: "0", 50: "+50",
                                                      100: "+100"}, BAND_DEFAULTS[1]),
                                      BAND_DEFAULTS[1]),
                                  slider_block(
                                      "s-b2", "r-b2", "Band 85+ (%)", -30, 100,
                                      expected_marks({-30: "-30", 0: "0", 50: "+50",
                                                      100: "+100"}, BAND_DEFAULTS[2]),
                                      BAND_DEFAULTS[2]),
                                  html.Div("Moving the master sets every band to its "
                                           "value; band sliders then adjust one band "
                                           "at a time.",
                                           style={"fontSize": "12px",
                                                  "color": "#898781"}),
                                  html.Div("Defaults are mocked forecast values.",
                                           style={"fontSize": "12px",
                                                  "color": "#898781"}),
                              ]),
                     html.Div(style={"flex": "1"}, children=[
                         html.Div(style={"display": "flex", "gap": "12px",
                                         "flexWrap": "wrap", "marginBottom": "14px"},
                                  children=[
                                      kpi_tile("k-enroll", "Scenario total enrollment"),
                                      kpi_tile("k-visits", "Scenario total annual visits"),
                                      kpi_tile("k-at", "Providers at capacity"),
                                      kpi_tile("k-over", "Providers over capacity"),
                                  ]),
                         html.H4("Enrollment by band"),
                         dash_table.DataTable(
                             id="tbl-enroll",
                             columns=[{"name": c, "id": c} for c in
                                      ["band", "baseline", "scenario", "delta",
                                       "pct_change"]],
                             **TABLE_STYLE),
                         html.H4("Condition mix"),
                         dcc.RadioItems(id="rad-topn",
                                        options=[{"label": "Top 10", "value": 10},
                                                 {"label": "Top 20", "value": 20}],
                                        value=10, inline=True,
                                        style={"fontSize": "13px",
                                               "marginBottom": "6px"},
                                        inputStyle={"marginRight": "4px"},
                                        labelStyle={"marginRight": "16px"}),
                         html.Div("Members can have multiple conditions; column does "
                                  "not sum to enrollment.",
                                  style={"fontSize": "12px", "color": "#898781",
                                         "marginBottom": "6px"}),
                         dash_table.DataTable(
                             id="tbl-condition",
                             columns=[{"name": c, "id": c} for c in
                                      ["rank", "movement", "condition",
                                       "baseline_members", "scenario_members",
                                       "delta", "pct_change"]],
                             **TABLE_STYLE),
                         html.H4("Demand by specialty"),
                         html.Div(style={"maxWidth": "420px",
                                         "marginBottom": "8px"},
                                  children=[
                                      dcc.Dropdown(id="dd-specialty",
                                                   options=SPECIALTIES,
                                                   value=SPECIALTIES, multi=True)]),
                         dcc.Graph(id="fig-demand",
                                   config={"displayModeBar": False}),
                         dash_table.DataTable(
                             id="tbl-demand",
                             columns=[{"name": c, "id": c} for c in
                                      ["specialty", "baseline_visits",
                                       "scenario_visits", "delta", "pct_change"]],
                             **TABLE_STYLE),
                         html.H4("Provider load vs capacity"),
                         dash_table.DataTable(
                             id="tbl-provider",
                             columns=[{"name": c, "id": c} for c in
                                      ["provider", "specialty", "current_visits",
                                       "estimated_max", "new_demand_coming",
                                       "room_left", "status"]],
                             style_data_conditional=[
                                 {"if": {"filter_query": '{status} = "HEADROOM"',
                                         "column_id": "status"},
                                  "color": "#006300",
                                  "backgroundColor": "rgba(12,163,12,0.12)",
                                  "fontWeight": "600"},
                                 {"if": {"filter_query": '{status} = "AT CAPACITY"',
                                         "column_id": "status"},
                                  "color": "#8a5a00",
                                  "backgroundColor": "rgba(250,178,25,0.18)",
                                  "fontWeight": "600"},
                                 {"if": {"filter_query": '{status} = "OVER CAPACITY"',
                                         "column_id": "status"},
                                  "color": "#d03b3b",
                                  "backgroundColor": "rgba(208,59,59,0.14)",
                                  "fontWeight": "600"},
                             ],
                             **TABLE_STYLE),
                     ]),
                 ]),
        html.Details(open=False, style={"marginTop": "18px"}, children=[
            html.Summary("Mock coefficients",
                         style={"fontWeight": "600", "cursor": "pointer"}),
            html.H4("Prevalence rates (band x condition)"),
            dash_table.DataTable(
                columns=[{"name": c, "id": c} for c in PREV_TABLE.columns],
                data=PREV_TABLE.to_dict("records"), **TABLE_STYLE),
            html.H4("Annual visit rates (condition x specialty)"),
            dash_table.DataTable(
                columns=[{"name": c, "id": c} for c in RATE_TABLE.columns],
                data=RATE_TABLE.to_dict("records"), **TABLE_STYLE),
            html.H4("Provider config"),
            dash_table.DataTable(
                columns=[{"name": c, "id": c} for c in PROVIDER_CONFIG_TABLE.columns],
                data=PROVIDER_CONFIG_TABLE.to_dict("records"), **TABLE_STYLE),
        ]),
    ])


def build_demand_figure(selected, demand):
    fig = go.Figure()
    names, base_seg, grow_seg, labels, label_y, dash_x, dash_y = [], [], [], [], [], [], []
    idx = 0
    for s, name in enumerate(SPECIALTIES):
        if name not in selected:
            continue
        base = BASELINE_DEMAND[s]
        scen = demand[s]
        delta = scen - base
        names.append(name)
        if scen >= base:
            base_seg.append(base)
            grow_seg.append(delta)
        else:
            base_seg.append(scen)
            grow_seg.append(0)
            dash_x.extend([idx - 0.35, idx + 0.35, None])
            dash_y.extend([base, base, None])
        labels.append(f"{fmt(scen)} ({delta:+,.0f})")
        label_y.append(max(scen, base))
        idx += 1
    x = list(range(len(names)))
    fig.add_trace(go.Bar(x=x, y=base_seg, marker_color=GRAY, name="Baseline"))
    fig.add_trace(go.Bar(x=x, y=grow_seg, marker_color=GREEN, name="Growth"))
    if dash_x:
        fig.add_trace(go.Scatter(x=dash_x, y=dash_y, mode="lines",
                                 line={"color": "#52514e", "dash": "dash",
                                       "width": 2},
                                 name="Baseline level"))
    for i, lbl in enumerate(labels):
        fig.add_annotation(x=x[i], y=label_y[i], text=lbl, showarrow=False,
                           yshift=12, font={"size": 11})
    fig.update_layout(barmode="stack", height=320,
                      margin={"l": 40, "r": 10, "t": 10, "b": 30},
                      xaxis={"tickvals": x, "ticktext": names},
                      yaxis={"title": "Annual visits"},
                      legend={"orientation": "h", "y": 1.12},
                      plot_bgcolor="#fcfcfb", paper_bgcolor="#fcfcfb")
    return fig


@app.callback(
    Output("s-master", "value"),
    Output("s-b0", "value"),
    Output("s-b1", "value"),
    Output("s-b2", "value"),
    Output("store-sync", "data"),
    Output("r-master", "children"),
    Output("r-b0", "children"),
    Output("r-b1", "children"),
    Output("r-b2", "children"),
    Output("k-enroll", "children"),
    Output("k-visits", "children"),
    Output("k-at", "children"),
    Output("k-over", "children"),
    Output("tbl-enroll", "data"),
    Output("tbl-condition", "data"),
    Output("fig-demand", "figure"),
    Output("tbl-demand", "data"),
    Output("tbl-provider", "data"),
    Input("s-master", "value"),
    Input("s-b0", "value"),
    Input("s-b1", "value"),
    Input("s-b2", "value"),
    Input("btn-reset", "n_clicks"),
    Input("dd-specialty", "value"),
    Input("rad-topn", "value"),
    State("store-sync", "data"),
)
def update(master, b0, b1, b2, _reset_clicks, selected_specialties, top_n, sync_token):
    trigger = ctx.triggered_id
    master = master if master is not None else MASTER_DEFAULT
    bands = [b0 if b0 is not None else BAND_DEFAULTS[0],
             b1 if b1 is not None else BAND_DEFAULTS[1],
             b2 if b2 is not None else BAND_DEFAULTS[2]]

    slider_out = [no_update, no_update, no_update, no_update]
    store_out = no_update
    if trigger == "btn-reset":
        bands = list(BAND_DEFAULTS)
        slider_out = [MASTER_DEFAULT] + list(BAND_DEFAULTS)
        store_out = MASTER_DEFAULT if master != MASTER_DEFAULT else None
    elif trigger == "s-master":
        if sync_token is not None and master == sync_token:
            store_out = None
        else:
            bands = [master, master, master]
            slider_out = [no_update, master, master, master]
            store_out = None

    enrollment = band_enrollment(bands)
    demand = demand_by_specialty(enrollment)
    deltas = [demand[s] - BASELINE_DEMAND[s] for s in range(len(SPECIALTIES))]

    total_enroll = sum(enrollment)
    total_base = sum(BASELINE_BANDS)
    readout_master = (f"Total: {fmt(total_enroll)} members "
                      f"({pct((total_enroll - total_base) / total_base)} vs baseline)")
    readouts = [f"{BANDS[i]}: {bands[i]:+d}% -> {fmt(enrollment[i])} members"
                for i in range(len(BANDS))]

    enroll_rows = []
    for i, band in enumerate(BANDS):
        base = BASELINE_BANDS[i]
        d = enrollment[i] - base
        enroll_rows.append({
            "band": band, "baseline": fmt(base), "scenario": fmt(enrollment[i]),
            "delta": f"{d:+,.0f}", "pct_change": pct(d / base)})

    cond_now = condition_members(enrollment)
    n_show = top_n if top_n is not None else 10
    order, cur_ranks = ranks_desc(cond_now)
    condition_rows = []
    for pos, c in enumerate(order[:n_show]):
        d = cond_now[c] - BASELINE_CONDITIONS[c]
        moved = BASELINE_COND_RANKS[c] - cur_ranks[c]
        if moved > 0:
            movement = f"^{moved}"
        elif moved < 0:
            movement = f"v{-moved}"
        else:
            movement = "-"
        condition_rows.append({
            "rank": pos + 1,
            "movement": movement,
            "condition": CONDITIONS[c],
            "baseline_members": fmt(BASELINE_CONDITIONS[c]),
            "scenario_members": fmt(cond_now[c]),
            "delta": f"{d:+,.0f}",
            "pct_change": pct(d / BASELINE_CONDITIONS[c])})
    rest = order[n_show:]
    if rest:
        rest_base = sum(BASELINE_CONDITIONS[c] for c in rest)
        rest_now = sum(cond_now[c] for c in rest)
        condition_rows.append({
            "rank": "", "movement": "",
            "condition": f"Other ({len(rest)} conditions)",
            "baseline_members": fmt(rest_base),
            "scenario_members": fmt(rest_now),
            "delta": f"{rest_now - rest_base:+,.0f}",
            "pct_change": ""})

    selected = selected_specialties or []
    demand_rows = []
    for s, name in enumerate(SPECIALTIES):
        if name not in selected:
            continue
        demand_rows.append({
            "specialty": name, "baseline_visits": fmt(BASELINE_DEMAND[s]),
            "scenario_visits": fmt(demand[s]), "delta": f"{deltas[s]:+,.0f}",
            "pct_change": pct(deltas[s] / BASELINE_DEMAND[s])})

    n_at = 0
    n_over = 0
    provider_rows = []
    for p in PROVIDERS:
        s = SPECIALTIES.index(p["specialty"])
        new_demand = deltas[s] * p["intake_weight"]
        load = p["current_annual_visits"] + new_demand
        utilization = load / p["capacity_ceiling"]
        status = status_of(utilization)
        if status == "AT CAPACITY":
            n_at += 1
        if status == "OVER CAPACITY":
            n_over += 1
        room = p["capacity_ceiling"] - load
        if p["specialty"] in selected:
            provider_rows.append({
                "provider": f'{p["id"]} {p["name"]}', "specialty": p["specialty"],
                "current_visits": fmt(p["current_annual_visits"]),
                "estimated_max": f'~{fmt(p["capacity_ceiling"])}',
                "new_demand_coming": f"{new_demand:+,.0f}",
                "room_left": fmt(room) if room >= 0 else f"short by {fmt(-room)}",
                "status": status})

    figure = build_demand_figure(selected, demand)
    return (slider_out[0], slider_out[1], slider_out[2], slider_out[3], store_out,
            readout_master, readouts[0], readouts[1], readouts[2],
            fmt(total_enroll), fmt(sum(demand)), str(n_at), str(n_over),
            enroll_rows, condition_rows, figure, demand_rows, provider_rows)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8050, debug=False)
