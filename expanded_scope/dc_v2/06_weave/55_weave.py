"""
55 - weave   [PYTHON / pandas + BigQuery]

WHAT  : Joins the 2026 model estimates (future rows, feature month 2025-12)
        with the cohort-clean baselines into one decision table at county x
        cms_specialty. Demand carries xgb columns only; capacity carries
        bottom_up columns only; linear and top_down columns appear nowhere.
        The specialty bridge (specialty_ctg_cd -> cms_specialty via
        cfg.base("ref_specialty_crosswalk"), joined on aetna_cd exactly as
        in 36_dc_gap.py) happens HERE and only here.
        capacity_potential_p75 = per county x cms_specialty, the 75th
        percentile of provider_pred_next_12m times the provider count.
        gap_status: UNDER where gap_model_2026 > 0 else OVER; NULL where
        either side missing. expected_error_band: county-level MAPE of
        pred_next_1m_xgb on validation rows with actual >= 10; A <= 0.25,
        B <= 0.50, C otherwise (C with NULL pct where no qualifying rows).
        pct_medicare_age_members: December 2025 members 65+ over ALL
        members (no age filter on the denominator).
GRAIN : county_fips x cms_specialty.
INPUTS: dc2_demand_predictions, dc2_capacity_predictions,
        dc2_capacity_provider_future, dc2_baselines,
        cfg.base("ref_specialty_crosswalk"), dc2_capacity_county,
        A870800_medicare_analysis_membership
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
PROV_FUT  = cfg.src("dc2_capacity_provider_future")
BASELINES = cfg.src("dc2_baselines")
XWALK     = cfg.base("ref_specialty_crosswalk")
CAP_CNTY  = cfg.src("dc2_capacity_county")
MBRSHP    = "anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_membership"
OUT_TBL   = cfg.src("dc2_weave")

OUT_COLS = ["state_cd", "county_fips", "cms_specialty",
            "demand_current_book", "capacity_current", "gap_current_book",
            "demand_next_12m_xgb", "capacity_next_12m_bottom_up", "gap_model_2026",
            "capacity_to_demand_ratio", "capacity_potential_p75", "gap_status",
            "expected_error_pct", "expected_error_band", "pct_medicare_age_members",
            "market_max_demand"]


def bridge(df, xwalk, measure_cols, label):
    m = df.merge(xwalk, left_on="specialty_ctg_cd", right_on="aetna_cd", how="left")
    leaked = m["cms_specialty"].isna()
    leak_rows = int(leaked.sum())
    leak_vol = float(m.loc[leaked, measure_cols[0]].sum())
    print(f"{label} bridge leakage: {leak_rows:,} rows, "
          f"{leak_vol:,.0f} {measure_cols[0]} volume with no cms_specialty match")
    kept = m.loc[~leaked]
    return kept.groupby(["county_fips", "cms_specialty"], as_index=False)[measure_cols].sum()


def main():
    client = cfg.client()
    q = lambda sql: client.query(sql).result().to_dataframe()

    dem = q(f"SELECT mbr_county_cd AS county_fips, specialty_ctg_cd, "
            f"pred_next_1m_xgb, pred_next_12m_xgb "
            f"FROM `{DEM_PRED}` WHERE split_label = 'future'")
    cap = q(f"SELECT prvdr_county AS county_fips, specialty_ctg_cd, "
            f"bottom_up_next_1m, bottom_up_next_12m "
            f"FROM `{CAP_PRED}` WHERE split_label = 'future'")
    prov_fut = q(f"SELECT epdb_dw_prvdr_id, specialty_ctg_cd, "
                 f"prvdr_county AS county_fips, provider_pred_next_12m "
                 f"FROM `{PROV_FUT}`")
    base = q(f"SELECT * FROM `{BASELINES}`")
    xwalk = q(f"SELECT aetna_cd, cms_specialty FROM `{XWALK}`")
    err = q(f"SELECT mbr_county_cd AS county_fips, "
            f"AVG(ABS(pred_next_1m_xgb - actual_next_1m) / actual_next_1m) AS expected_error_pct "
            f"FROM `{DEM_PRED}` WHERE split_label = 'validation' AND actual_next_1m >= 10 "
            f"GROUP BY 1")
    age_mix = q(f"SELECT mbr_county_cd AS county_fips, "
                f"SAFE_DIVIDE(COUNT(DISTINCT IF(age_nbr >= 65, member_id, NULL)), "
                f"COUNT(DISTINCT member_id)) AS pct_medicare_age_members "
                f"FROM `{MBRSHP}` WHERE CAST(eff_yr AS INT64) = 2025 "
                f"AND CAST(eff_mo AS INT64) = 12 GROUP BY 1")

    dem_b = bridge(dem, xwalk, ["pred_next_12m_xgb", "pred_next_1m_xgb"], "demand")
    cap_b = bridge(cap, xwalk, ["bottom_up_next_12m", "bottom_up_next_1m"], "capacity")

    weave = dem_b.merge(cap_b, on=["county_fips", "cms_specialty"], how="outer")
    weave = weave.merge(base, on=["county_fips", "cms_specialty"], how="left")

    prov_m = prov_fut.merge(xwalk, left_on="specialty_ctg_cd", right_on="aetna_cd", how="left")
    prov_m = prov_m.loc[prov_m["cms_specialty"].notna()]
    pot = (prov_m.groupby(["county_fips", "cms_specialty"])
           .agg(p75=("provider_pred_next_12m", lambda s: s.quantile(0.75)),
                providers=("epdb_dw_prvdr_id", "nunique"))
           .reset_index())
    pot["capacity_potential_p75"] = pot["p75"] * pot["providers"]
    weave = weave.merge(pot[["county_fips", "cms_specialty", "capacity_potential_p75"]],
                        on=["county_fips", "cms_specialty"], how="left")

    weave = weave.rename(columns={
        "pred_next_12m_xgb": "demand_next_12m_xgb",
        "bottom_up_next_12m": "capacity_next_12m_bottom_up",
    })
    weave["gap_model_2026"] = (weave["demand_next_12m_xgb"]
                               - weave["capacity_next_12m_bottom_up"])
    weave["capacity_to_demand_ratio"] = np.where(
        weave["demand_next_12m_xgb"].notna() & (weave["demand_next_12m_xgb"] != 0)
        & weave["capacity_next_12m_bottom_up"].notna(),
        weave["capacity_next_12m_bottom_up"] / weave["demand_next_12m_xgb"], np.nan)
    weave["gap_status"] = np.where(
        weave["gap_model_2026"].isna(), None,
        np.where(weave["gap_model_2026"] > 0, "UNDER", "OVER"))

    weave = weave.merge(err, on="county_fips", how="left")
    weave["expected_error_band"] = np.where(
        weave["expected_error_pct"].isna(), "C",
        np.where(weave["expected_error_pct"] <= 0.25, "A",
                 np.where(weave["expected_error_pct"] <= 0.50, "B", "C")))

    weave = weave.merge(age_mix, on="county_fips", how="left")

    weave = weave[OUT_COLS].sort_values(["state_cd", "county_fips", "cms_specialty"],
                                        na_position="last").reset_index(drop=True)

    print(f"weave rows: {len(weave):,}")
    has_dem = weave["demand_next_12m_xgb"].notna()
    has_cap = weave["capacity_next_12m_bottom_up"].notna()
    print(f"counties with demand only: {weave.loc[has_dem & ~has_cap, 'county_fips'].nunique():,} "
          f"/ capacity only: {weave.loc[~has_dem & has_cap, 'county_fips'].nunique():,} "
          f"/ both: {weave.loc[has_dem & has_cap, 'county_fips'].nunique():,}")
    print(f"SUM demand_next_12m_xgb: {weave['demand_next_12m_xgb'].sum():,.0f}  vs  "
          f"SUM capacity_next_12m_bottom_up: {weave['capacity_next_12m_bottom_up'].sum():,.0f}")
    print("gap_status counts:")
    print(weave["gap_status"].value_counts(dropna=False).to_string())
    print("expected_error_band counts:")
    print(weave["expected_error_band"].value_counts(dropna=False).to_string())

    from google.cloud import bigquery
    job_config = bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE")
    client.load_table_from_dataframe(weave, OUT_TBL, job_config=job_config).result()
    print(f"wrote {len(weave):,} rows -> {OUT_TBL}")


if __name__ == "__main__":
    main()
