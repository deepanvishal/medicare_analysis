import os

import numpy as np
import pandas as pd
from dash import Dash, Input, Output, dash_table, dcc, html

SPECIALTIES = ["Primary Care", "Cardiology", "Nephrology", "Orthopedics"]
AGE_BANDS = ["65-74", "75-84", "85+"]
MONTHS = ["2025-01", "2025-02", "2025-03", "2025-04", "2025-05"]


# All values are fictional mock data; step 2 replaces only this function body with a BigQuery query.
def load_data() -> pd.DataFrame:
    rng = np.random.default_rng(42)
    n = len(SPECIALTIES) * len(MONTHS) * len(AGE_BANDS)
    specialty = [s for s in SPECIALTIES for _ in range(len(MONTHS) * len(AGE_BANDS))]
    claim_month = [m for _ in SPECIALTIES for m in MONTHS for _ in range(len(AGE_BANDS))]
    age_band = [b for _ in SPECIALTIES for _ in MONTHS for b in AGE_BANDS]
    return pd.DataFrame({
        "county": ["TEST_COUNTY_FL"] * n,
        "specialty": specialty,
        "age_band": age_band,
        "claim_month": claim_month,
        "claim_count": rng.integers(50, 901, n),
        "member_count": rng.integers(100, 2001, n),
    })


DF = load_data()

PROXY_PREFIX = os.environ.get("DASH_PROXY_PREFIX")
if PROXY_PREFIX:
    app = Dash(__name__, requests_pathname_prefix=PROXY_PREFIX)
else:
    app = Dash(__name__)

app.layout = html.Div(
    style={"maxWidth": "1000px", "margin": "0 auto",
           "fontFamily": "system-ui, sans-serif", "padding": "16px"},
    children=[
        html.H2("Dash Data Test — Step 1 (pandas mock)"),
        html.Div(
            style={"display": "flex", "gap": "24px", "flexWrap": "wrap",
                   "alignItems": "flex-end", "marginBottom": "12px"},
            children=[
                html.Div(style={"minWidth": "280px", "flex": "1"}, children=[
                    html.Label("Specialty"),
                    dcc.Dropdown(id="dd-specialty", options=SPECIALTIES,
                                 value=SPECIALTIES, multi=True),
                ]),
                html.Div(style={"minWidth": "220px", "flex": "1"}, children=[
                    html.Label("Age band"),
                    dcc.Dropdown(id="dd-age", options=AGE_BANDS,
                                 value=AGE_BANDS, multi=True),
                ]),
                html.Div(style={"minWidth": "260px", "flex": "1"}, children=[
                    html.Label("Max rows displayed"),
                    dcc.Slider(id="sl-max", min=1, max=60, step=1, value=20,
                               marks={1: "1", 20: "20", 40: "40", 60: "60"},
                               tooltip={"placement": "bottom"}),
                ]),
            ]),
        html.Div(id="row-info", style={"margin": "8px 0", "color": "#52514e"}),
        dash_table.DataTable(
            id="tbl-rows",
            columns=[{"name": c, "id": c} for c in DF.columns],
            style_table={"overflowX": "auto"},
            style_cell={"fontFamily": "system-ui, sans-serif", "fontSize": "13px",
                        "padding": "6px 10px", "textAlign": "left"},
            style_header={"fontWeight": "600"},
        ),
        html.H4("claim_count by specialty (filtered selection)"),
        dash_table.DataTable(
            id="tbl-summary",
            columns=[{"name": "specialty", "id": "specialty"},
                     {"name": "claim_count", "id": "claim_count"}],
            style_cell={"fontFamily": "system-ui, sans-serif", "fontSize": "13px",
                        "padding": "6px 10px", "textAlign": "left"},
            style_header={"fontWeight": "600"},
        ),
    ])


@app.callback(
    Output("row-info", "children"),
    Output("tbl-rows", "data"),
    Output("tbl-summary", "data"),
    Input("dd-specialty", "value"),
    Input("dd-age", "value"),
    Input("sl-max", "value"),
)
def update(selected_specialties, selected_age_bands, max_rows):
    filtered = DF[DF["specialty"].isin(selected_specialties or [])
                  & DF["age_band"].isin(selected_age_bands or [])]
    shown = filtered.head(max_rows)
    summary = (filtered.groupby("specialty", as_index=False)["claim_count"].sum()
               .sort_values("claim_count", ascending=False))
    info = f"Matching rows: {len(filtered)} | Displayed rows: {len(shown)}"
    return info, shown.to_dict("records"), summary.to_dict("records")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8050, debug=False)
