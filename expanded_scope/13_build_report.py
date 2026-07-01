"""
13 - Build the combined multi-state compliance workbook.   [PYTHON / pandas + openpyxl]

WHAT : Query ms_fact_gap_analysis and build ONE Excel workbook covering all scope
       states, with state_cd on every tab so it filters. Tabs:
         Overview      - scope + compliance headline numbers
         Compliance    - the full fact table (county x specialty x plan)
         By State      - compliant / non-compliant counts + % per state x plan
         Non-Compliant - the actionable failures
         County Risk   - # non-compliant specialties per county
WHY  : Final deliverable. Replaces the FL-only Step4 report. One workbook, filter by state.
INPUT: ms_fact_gap_analysis   (must exist -- run 01-11 first)
OUTPUT: medicare_supply_demand_ms.xlsx  (repo root)
NOTE : No hardcoded county list -- everything is driven by the query.
Run  : python expanded_scope/13_build_report.py     (needs: pip install db-dtypes)
"""

import datetime
import pandas as pd
import config as cfg

OUT_XLSX = cfg.repo_path("medicare_supply_demand_ms.xlsx")
FACT = cfg.table("fact_gap_analysis")


def load():
    q = f"SELECT * FROM `{FACT}` ORDER BY state_cd, county_name, cms_specialty, plan_type"
    return cfg.client().query(q).to_dataframe()


def build(df):
    overview = pd.DataFrame({
        "metric": [
            "Generated", "Scope states", "Counties", "Specialties", "Plan types",
            "Total rows (county x specialty x plan)",
            "COMPLIANT rows", "NON-COMPLIANT rows", "% compliant",
        ],
        "value": [
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            ", ".join(sorted(df.state_cd.unique())),
            df.county_fips.nunique(),
            df.cms_specialty.nunique(),
            ", ".join(sorted(df.plan_type.unique())),
            len(df),
            int((df.compliance_status == "COMPLIANT").sum()),
            int((df.compliance_status == "NON-COMPLIANT").sum()),
            f"{100 * (df.compliance_status == 'COMPLIANT').mean():.1f}%",
        ],
    })

    by_state = (
        df.groupby(["state_cd", "plan_type", "compliance_status"]).size()
          .unstack("compliance_status", fill_value=0).reset_index()
    )
    for col in ("COMPLIANT", "NON-COMPLIANT"):
        if col not in by_state.columns:
            by_state[col] = 0
    by_state["total"] = by_state["COMPLIANT"] + by_state["NON-COMPLIANT"]
    by_state["pct_compliant"] = (100 * by_state["COMPLIANT"] / by_state["total"]).round(1)

    non_compliant = df[df.compliance_status == "NON-COMPLIANT"].copy()

    county_risk = (
        non_compliant.groupby(["state_cd", "county_name", "county_type", "plan_type"])
                     .agg(non_compliant_specialties=("cms_specialty", "nunique"))
                     .reset_index()
                     .sort_values(["state_cd", "non_compliant_specialties"], ascending=[True, False])
    )

    return {
        "Overview": overview,
        "Compliance": df,
        "By State": by_state,
        "Non-Compliant": non_compliant,
        "County Risk": county_risk,
    }


def write(tabs):
    with pd.ExcelWriter(OUT_XLSX, engine="openpyxl") as xw:
        for name, frame in tabs.items():
            frame.to_excel(xw, sheet_name=name, index=False)
            ws = xw.sheets[name]
            ws.freeze_panes = "A2"                 # keep header visible
            if ws.max_row > 1:
                ws.auto_filter.ref = ws.dimensions  # filterable (incl. state_cd)
    print(f"wrote {OUT_XLSX}")
    for name, frame in tabs.items():
        print(f"  tab '{name}': {len(frame)} rows")


def main():
    df = load()
    if df.empty:
        raise SystemExit("ms_fact_gap_analysis returned 0 rows -- run 01-11 first.")
    tabs = build(df)
    write(tabs)


if __name__ == "__main__":
    main()
