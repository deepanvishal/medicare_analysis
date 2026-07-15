"""
55 - weave   [PYTHON / pandas + BigQuery]

WHAT  : Joins the 2026 model estimates (future rows, feature month 2025-12)
        from demand and capacity predictions with the v1 p75 baseline into
        one decision table at county x cms_specialty. Demand carries only
        the xgb columns (linear lost the contest). The specialty bridge
        (specialty_ctg_cd -> cms_specialty via ref_specialty_crosswalk,
        joined on aetna_cd exactly as in 36_dc_gap.py) happens HERE and
        only here. Demand (member county) and capacity (provider county)
        join FULL OUTER so one-sided counties survive with NULLs. Gaps are
        NaN-tolerant pandas arithmetic. trust_band per county from 2024
        capacity visits: small <10k, mid 10k-100k, large >100k.
        plan_type split deferred; v1 gap table remains the plan-type source.
GRAIN : county x cms_specialty.
INPUTS: dc2_demand_predictions, dc2_capacity_predictions, dc2_p75_baseline,
        cfg.base("ref_specialty_crosswalk"), dc2_capacity_county
OUTPUT: dc2_weave (BigQuery table) with sanity prints.
Run   : python expanded_scope/dc_v2/06_weave/55_weave.py
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

import numpy as np
import pandas as pd

DEM_PRED  = cfg.src("dc2_demand_predictions")
CAP_PRED  = cfg.src("dc2_capacity_predictions")
P75       = cfg.src("dc2_p75_baseline")
XWALK     = cfg.base("ref_specialty_crosswalk")
CAP_CNTY  = cfg.src("dc2_capacity_county")
OUT_TBL   = cfg.src("dc2_weave")

OUT_COLS = ["county", "cms_specialty",
            "demand_next_12m_xgb", "demand_next_1m_xgb",
            "capacity_next_12m_bottom_up", "capacity_next_1m_bottom_up",
            "gap_next_12m", "demand_p75_annual", "capacity_p75_annual",
            "gap_p75", "trust_band"]


def size_band(v):
    if v < 10_000:
        return "small"
    if v > 100_000:
        return "large"
    return "mid"


def bridge(df, xwalk, key_cols, measure_cols, label):
    m = df.merge(xwalk, left_on="specialty_ctg_cd", right_on="aetna_cd", how="left")
    leaked = m["cms_specialty"].isna()
    leak_rows = int(leaked.sum())
    leak_vol = float(m.loc[leaked, measure_cols[0]].sum())
    print(f"{label} bridge leakage: {leak_rows:,} rows, "
          f"{leak_vol:,.0f} {measure_cols[0]} volume with no cms_specialty match")
    kept = m.loc[~leaked]
    return (kept.groupby(key_cols + ["cms_specialty"], as_index=False)[measure_cols].sum())


def main():
    client = cfg.client()
    q = lambda sql: client.query(sql).result().to_dataframe()

    dem = q(f"SELECT mbr_county_cd, specialty_ctg_cd, pred_next_1m_xgb, pred_next_12m_xgb "
            f"FROM `{DEM_PRED}` WHERE split_label = 'future'")
    cap = q(f"SELECT prvdr_county, specialty_ctg_cd, bottom_up_next_1m, bottom_up_next_12m "
            f"FROM `{CAP_PRED}` WHERE split_label = 'future'")
    p75 = q(f"SELECT county_fips, cms_specialty, demand_p75_annual, capacity_p75_annual "
            f"FROM `{P75}`")
    xwalk = q(f"SELECT aetna_cd, cms_specialty FROM `{XWALK}`")
    cnty_2024 = q(f"SELECT prvdr_county, SUM(visits) AS visits_2024 "
                  f"FROM `{CAP_CNTY}` WHERE year = 2024 GROUP BY 1")

    dem = dem.rename(columns={"mbr_county_cd": "county"})
    cap = cap.rename(columns={"prvdr_county": "county"})

    dem_b = bridge(dem, xwalk, ["county"],
                   ["pred_next_12m_xgb", "pred_next_1m_xgb"], "demand")
    cap_b = bridge(cap, xwalk, ["county"],
                   ["bottom_up_next_12m", "bottom_up_next_1m"], "capacity")

    weave = dem_b.merge(cap_b, on=["county", "cms_specialty"], how="outer")
    weave = weave.merge(p75.rename(columns={"county_fips": "county"}),
                        on=["county", "cms_specialty"], how="left")

    weave = weave.rename(columns={
        "pred_next_12m_xgb": "demand_next_12m_xgb",
        "pred_next_1m_xgb": "demand_next_1m_xgb",
        "bottom_up_next_12m": "capacity_next_12m_bottom_up",
        "bottom_up_next_1m": "capacity_next_1m_bottom_up",
    })
    weave["gap_next_12m"] = weave["demand_next_12m_xgb"] - weave["capacity_next_12m_bottom_up"]
    weave["gap_p75"] = weave["demand_p75_annual"] - weave["capacity_p75_annual"]

    band_map = cnty_2024.set_index("prvdr_county")["visits_2024"]
    weave["trust_band"] = weave["county"].map(band_map).fillna(0).map(size_band)

    weave = weave[OUT_COLS].sort_values(["county", "cms_specialty"]).reset_index(drop=True)

    print(f"weave rows: {len(weave):,}")
    has_dem = weave["demand_next_12m_xgb"].notna()
    has_cap = weave["capacity_next_12m_bottom_up"].notna()
    d_only = weave.loc[has_dem & ~has_cap, "county"].nunique()
    c_only = weave.loc[~has_dem & has_cap, "county"].nunique()
    both = weave.loc[has_dem & has_cap, "county"].nunique()
    print(f"counties with demand only: {d_only:,} / capacity only: {c_only:,} / both: {both:,}")
    print(f"SUM demand_next_12m_xgb: {weave['demand_next_12m_xgb'].sum():,.0f}  vs  "
          f"SUM capacity_next_12m_bottom_up: {weave['capacity_next_12m_bottom_up'].sum():,.0f}")

    from google.cloud import bigquery
    job_config = bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE")
    client.load_table_from_dataframe(weave, OUT_TBL, job_config=job_config).result()
    print(f"wrote {len(weave):,} rows -> {OUT_TBL}")


if __name__ == "__main__":
    main()
