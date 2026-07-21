import os

import pandas as pd
from dash import Dash, Input, Output, dash_table, dcc, html

# ============================== CONFIG ==================================
# All values in this section are fictional mock data; real model extracts
# replace this block in a later phase with no layout change.
COUNTY = "TEST_COUNTY_FL"
BASELINE_ENROLLMENT = 40000
BANDS = ["65-74", "75-84", "85+"]
BAND_SHARES = [0.55, 0.33, 0.12]

CONDITIONS = ["Diabetes", "CHF", "CKD", "COPD", "Osteoarthritis"]
PREVALENCE = [
    [0.25, 0.08, 0.10, 0.10, 0.30],
    [0.30, 0.14, 0.18, 0.14, 0.42],
    [0.33, 0.22, 0.30, 0.18, 0.52],
]

SPECIALTIES = ["Primary Care", "Cardiology", "Nephrology", "Orthopedics"]
VISIT_RATES = [
    [1.2, 0.4, 0.3, 0.0],
    [0.8, 2.6, 0.2, 0.0],
    [0.6, 0.3, 2.8, 0.0],
    [1.0, 0.3, 0.0, 0.0],
    [0.4, 0.0, 0.0, 1.8],
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


def band_enrollment(master_pct, band_pcts):
    return [max(0.0, e * (1 + (master_pct + band_pcts[i]) / 100.0))
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


BASELINE_BANDS = band_enrollment(0, [0, 0, 0])
BASELINE_DEMAND = demand_by_specialty(BASELINE_BANDS)


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
    "This dashboard simulates enrollment scenarios for one county. The master "
    "slider shifts total enrollment, spread across the demographic bands "
    "proportionally. The band sliders add demographic skew on top of the master "
    "- for example, what if the 85+ band grew faster than the rest.",
    "Every slider move recomputes demand through the same chain: band enrollment "
    "times condition prevalence gives members with each condition; condition "
    "visit rates translate that into annual visits per specialty. New demand is "
    "then routed to providers by their intake weight, and each provider's new "
    "load is compared against their capacity ceiling to flag headroom, at "
    "capacity, or over capacity.",
    "All data on this page is fictional. In a later phase, the models supply "
    "real coefficients and this page's layout does not change.",
]


def kpi_tile(tile_id, label):
    return html.Div(style={"flex": "1", "minWidth": "170px", "background": "#fcfcfb",
                           "border": "1px solid rgba(11,11,11,0.10)",
                           "borderRadius": "10px", "padding": "10px 14px"},
                    children=[
                        html.Div(label, style={"fontSize": "12px", "color": "#52514e"}),
                        html.Div(id=tile_id,
                                 style={"fontSize": "24px", "fontWeight": "650"}),
                    ])


def slider_block(slider_id, readout_id, label, mn, mx, marks):
    return html.Div(style={"marginBottom": "14px"}, children=[
        html.Label(label, style={"fontSize": "13px", "fontWeight": "600"}),
        dcc.Slider(id=slider_id, min=mn, max=mx, step=5, value=0, marks=marks,
                   tooltip={"placement": "bottom"}),
        html.Div(id=readout_id, style={"fontSize": "12px", "color": "#52514e"}),
    ])


app.layout = html.Div(
    style={"maxWidth": "1200px", "margin": "0 auto", "padding": "16px",
           "fontFamily": "system-ui, sans-serif"},
    children=[
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
                                  slider_block("s-master", "r-master",
                                               "Master: total enrollment (%)",
                                               -30, 50,
                                               {-30: "-30", 0: "0", 25: "+25", 50: "+50"}),
                                  slider_block("s-b0", "r-b0", "Band 65-74 (+pp)",
                                               -30, 100,
                                               {-30: "-30", 0: "0", 50: "+50", 100: "+100"}),
                                  slider_block("s-b1", "r-b1", "Band 75-84 (+pp)",
                                               -30, 100,
                                               {-30: "-30", 0: "0", 50: "+50", 100: "+100"}),
                                  slider_block("s-b2", "r-b2", "Band 85+ (+pp)",
                                               -30, 100,
                                               {-30: "-30", 0: "0", 50: "+50", 100: "+100"}),
                                  html.Div("Band sliders are additive on top of the "
                                           "master slider.",
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
                         html.H4("Demand by specialty"),
                         html.Div(style={"maxWidth": "420px",
                                         "marginBottom": "8px"},
                                  children=[
                                      dcc.Dropdown(id="dd-specialty",
                                                   options=SPECIALTIES,
                                                   value=SPECIALTIES, multi=True)]),
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
                                       "scenario_load", "ceiling",
                                       "utilization_pct", "status"]],
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


@app.callback(
    Output("r-master", "children"),
    Output("r-b0", "children"),
    Output("r-b1", "children"),
    Output("r-b2", "children"),
    Output("k-enroll", "children"),
    Output("k-visits", "children"),
    Output("k-at", "children"),
    Output("k-over", "children"),
    Output("tbl-enroll", "data"),
    Output("tbl-demand", "data"),
    Output("tbl-provider", "data"),
    Input("s-master", "value"),
    Input("s-b0", "value"),
    Input("s-b1", "value"),
    Input("s-b2", "value"),
    Input("dd-specialty", "value"),
)
def update(master, b0, b1, b2, selected_specialties):
    band_pcts = [b0 or 0, b1 or 0, b2 or 0]
    master = master or 0
    enrollment = band_enrollment(master, band_pcts)
    demand = demand_by_specialty(enrollment)
    deltas = [demand[s] - BASELINE_DEMAND[s] for s in range(len(SPECIALTIES))]

    readout_master = f"All bands {master:+d}%"
    readouts = [f"{BANDS[i]}: {fmt(enrollment[i])} members" for i in range(len(BANDS))]

    enroll_rows = []
    for i, band in enumerate(BANDS):
        base = BASELINE_BANDS[i]
        d = enrollment[i] - base
        enroll_rows.append({
            "band": band, "baseline": fmt(base), "scenario": fmt(enrollment[i]),
            "delta": f"{d:+,.0f}", "pct_change": pct(d / base)})

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
        load = p["current_annual_visits"] + deltas[s] * p["intake_weight"]
        utilization = load / p["capacity_ceiling"]
        status = status_of(utilization)
        if status == "AT CAPACITY":
            n_at += 1
        if status == "OVER CAPACITY":
            n_over += 1
        if p["specialty"] in selected:
            provider_rows.append({
                "provider": f'{p["id"]} {p["name"]}', "specialty": p["specialty"],
                "current_visits": fmt(p["current_annual_visits"]),
                "scenario_load": fmt(load), "ceiling": fmt(p["capacity_ceiling"]),
                "utilization_pct": f"{utilization:.0%}", "status": status})

    total_enroll = sum(enrollment)
    total_visits = sum(demand)
    return (readout_master, readouts[0], readouts[1], readouts[2],
            fmt(total_enroll), fmt(total_visits), str(n_at), str(n_over),
            enroll_rows, demand_rows, provider_rows)


@app.callback(
    Output("s-master", "value"),
    Output("s-b0", "value"),
    Output("s-b1", "value"),
    Output("s-b2", "value"),
    Input("btn-reset", "n_clicks"),
    prevent_initial_call=True,
)
def reset(_):
    return 0, 0, 0, 0


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8050, debug=False)
