"""
55 - weave   [PYTHON / pandas + BigQuery]

WHAT  : Joins the 2026 model estimates (future rows, feature month 2025-12)
        with the cohort-clean baselines into one decision table at state_cd
        x county_fips x cms_specialty. Demand carries xgb columns only;
        capacity carries bottom_up columns only; linear and top_down columns
        appear nowhere.
        COUNTY NORMALIZATION (v1 convention: county joins key on county_fips
        via ref_county): the demand side maps mbr_county_cd (LPAD to 5-char
        FIPS) to ref_county; the capacity side maps prvdr_county name to
        ref_county.county_name with UPPER on both sides. Every weave join
        keys on state_cd + county_fips; county_name is a display column from
        ref_county. Footprint guard: only state_cd in FL/OH/AZ/IL survives
        on either side; dropped rows are counted and sampled.
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
        avg_hcc_conditions_per_member: December 2025, SAFE
        SUM(members_with_hcc) over county members.
GRAIN : state_cd x county_fips x cms_specialty.
INPUTS: dc2_demand_predictions, dc2_capacity_predictions,
        dc2_capacity_provider_future, dc2_baselines, dc2_demand_base,
        dc2_capacity_county, dc2_demand_chronic,
        cfg.base("ref_specialty_crosswalk"), cfg.table("ref_county"),
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
DEM_BASE  = cfg.src("dc2_demand_base")
CAP_CNTY  = cfg.src("dc2_capacity_county")
DEM_CHR   = cfg.src("dc2_demand_chronic")
XWALK     = cfg.base("ref_specialty_crosswalk")
REF_CTY   = cfg.table("ref_county")
MBRSHP    = "anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_membership"
OUT_TBL   = cfg.src("dc2_weave")

FOOTPRINT = ("FL", "OH", "AZ", "IL")

OUT_COLS = ["state_cd", "county_fips", "county_name", "cms_specialty",
            "demand_current_book", "capacity_current", "gap_current_book",
            "demand_visits_2025_actual", "demand_next_12m_xgb",
            "capacity_visits_2025_actual", "capacity_next_12m_bottom_up",
            "gap_model_2026", "capacity_to_demand_ratio",
            "capacity_potential_p75", "gap_status",
            "expected_error_pct", "expected_error_band",
            "pct_medicare_age_members", "avg_hcc_conditions_per_member",
            "market_max_demand"]

CTY_KEYS = ["state_cd", "county_fips"]


def norm_member_county(df, ref, raw_col, label):
    """mbr_county_cd -> 5-char FIPS -> ref_county. Prints dropped rows."""
    df = df.copy()
    df["county_fips"] = df[raw_col].astype(str).str.strip().str.zfill(5)
    df = df.merge(ref[["county_fips", "state_cd"]], on="county_fips", how="left")
    dropped = df["state_cd"].isna() | ~df["state_cd"].isin(FOOTPRINT)
    n = int(dropped.sum())
    print(f"{label}: dropped {n:,} rows outside FL/OH/AZ/IL footprint")
    if n:
        print(f"{label}: sample dropped county values: "
              f"{df.loc[dropped, raw_col].astype(str).drop_duplicates().head(5).tolist()}")
    return df.loc[~dropped].drop(columns=[raw_col])


def norm_provider_county(df, ref, raw_col, label):
    """prvdr_county name -> ref_county.county_name, UPPER both sides.
    Prints dropped rows and cross-state name collisions."""
    df = df.copy()
    df["_name_u"] = df[raw_col].astype(str).str.strip().str.upper()
    ref_n = ref[["county_fips", "state_cd", "_name_u"]]
    collisions = ref_n.groupby("_name_u")["state_cd"].nunique()
    n_coll = int((collisions > 1).sum())
    m = df.merge(ref_n, on="_name_u", how="left")
    dropped = m["state_cd"].isna() | ~m["state_cd"].isin(FOOTPRINT)
    n = int(dropped.sum())
    print(f"{label}: dropped {n:,} rows outside FL/OH/AZ/IL footprint")
    if n:
        print(f"{label}: sample dropped county values: "
              f"{m.loc[dropped, raw_col].astype(str).drop_duplicates().head(5).tolist()}")
    if n_coll:
        print(f"{label}: WARNING - {n_coll} county names exist in more than one footprint "
              f"state; name-matched rows fan out across those states")
    return m.loc[~dropped].drop(columns=[raw_col, "_name_u"])


def bridge(df, xwalk, measure_cols, label):
    m = df.merge(xwalk, left_on="specialty_ctg_cd", right_on="aetna_cd", how="left")
    leaked = m["cms_specialty"].isna()
    leak_rows = int(leaked.sum())
    leak_vol = float(m.loc[leaked, measure_cols[0]].sum())
    print(f"{label} bridge leakage: {leak_rows:,} rows, "
          f"{leak_vol:,.0f} {measure_cols[0]} volume with no cms_specialty match")
    kept = m.loc[~leaked]
    return kept.groupby(CTY_KEYS + ["cms_specialty"], as_index=False)[measure_cols].sum()


def main():
    client = cfg.client()
    q = lambda sql: client.query(sql).result().to_dataframe()

    ref = q(f"SELECT county_fips, state_cd, county_name FROM `{REF_CTY}`")
    ref["_name_u"] = ref["county_name"].astype(str).str.strip().str.upper()

    dem = q(f"SELECT mbr_county_cd, specialty_ctg_cd, "
            f"pred_next_1m_xgb, pred_next_12m_xgb "
            f"FROM `{DEM_PRED}` WHERE split_label = 'future'")
    cap = q(f"SELECT prvdr_county, specialty_ctg_cd, "
            f"bottom_up_next_1m, bottom_up_next_12m "
            f"FROM `{CAP_PRED}` WHERE split_label = 'future'")
    prov_fut = q(f"SELECT epdb_dw_prvdr_id, specialty_ctg_cd, prvdr_county, "
                 f"provider_pred_next_12m FROM `{PROV_FUT}`")
    base = q(f"SELECT * FROM `{BASELINES}`")
    xwalk = q(f"SELECT aetna_cd, cms_specialty FROM `{XWALK}`")
    dem_act = q(f"SELECT mbr_county_cd, specialty_ctg_cd, SUM(visits) AS demand_visits_2025_actual "
                f"FROM `{DEM_BASE}` WHERE year = 2025 GROUP BY 1, 2")
    cap_act = q(f"SELECT prvdr_county, specialty_ctg_cd, "
                f"SUM(visits) AS capacity_visits_2025_actual "
                f"FROM `{CAP_CNTY}` WHERE year = 2025 GROUP BY 1, 2")
    hcc = q(f"SELECT mbr_county_cd, "
            f"SAFE_DIVIDE(SUM(members_with_hcc), MAX(members)) AS avg_hcc_conditions_per_member "
            f"FROM `{DEM_CHR}` WHERE month = DATE '2025-12-01' GROUP BY 1")
    err = q(f"SELECT mbr_county_cd, "
            f"AVG(SAFE_DIVIDE(ABS(pred_next_1m_xgb - actual_next_1m), actual_next_1m)) "
            f"AS expected_error_pct "
            f"FROM `{DEM_PRED}` WHERE split_label = 'validation' AND actual_next_1m >= 10 "
            f"GROUP BY 1")
    age_mix = q(f"SELECT mbr_county_cd, "
                f"SAFE_DIVIDE(COUNT(DISTINCT IF(age_nbr >= 65, member_id, NULL)), "
                f"COUNT(DISTINCT member_id)) AS pct_medicare_age_members "
                f"FROM `{MBRSHP}` WHERE CAST(eff_yr AS INT64) = 2025 "
                f"AND CAST(eff_mo AS INT64) = 12 GROUP BY 1")

    dem = norm_member_county(dem, ref, "mbr_county_cd", "demand predictions")
    cap = norm_provider_county(cap, ref, "prvdr_county", "capacity predictions")
    prov_fut = norm_provider_county(prov_fut, ref, "prvdr_county", "provider future")
    dem_act = norm_member_county(dem_act, ref, "mbr_county_cd", "demand 2025 actuals")
    cap_act = norm_provider_county(cap_act, ref, "prvdr_county", "capacity 2025 actuals")
    hcc = norm_member_county(hcc, ref, "mbr_county_cd", "hcc conditions")
    err = norm_member_county(err, ref, "mbr_county_cd", "expected error")
    age_mix = norm_member_county(age_mix, ref, "mbr_county_cd", "age mix")

    dem_b = bridge(dem, xwalk, ["pred_next_12m_xgb", "pred_next_1m_xgb"], "demand")
    cap_b = bridge(cap, xwalk, ["bottom_up_next_12m", "bottom_up_next_1m"], "capacity")
    dem_act_b = bridge(dem_act, xwalk, ["demand_visits_2025_actual"], "demand actuals")
    cap_act_b = bridge(cap_act, xwalk, ["capacity_visits_2025_actual"], "capacity actuals")

    weave = dem_b.merge(cap_b, on=CTY_KEYS + ["cms_specialty"], how="outer")
    weave = weave.merge(base[["state_cd", "county_fips", "cms_specialty",
                              "demand_current_book", "capacity_current",
                              "gap_current_book", "market_max_demand"]],
                        on=CTY_KEYS + ["cms_specialty"], how="left")
    weave = weave.merge(dem_act_b, on=CTY_KEYS + ["cms_specialty"], how="left")
    weave = weave.merge(cap_act_b, on=CTY_KEYS + ["cms_specialty"], how="left")

    prov_m = prov_fut.merge(xwalk, left_on="specialty_ctg_cd", right_on="aetna_cd", how="left")
    prov_m = prov_m.loc[prov_m["cms_specialty"].notna()]
    pot = (prov_m.groupby(CTY_KEYS + ["cms_specialty"])
           .agg(p75=("provider_pred_next_12m", lambda s: s.quantile(0.75)),
                providers=("epdb_dw_prvdr_id", "nunique"))
           .reset_index())
    pot["capacity_potential_p75"] = pot["p75"] * pot["providers"]
    weave = weave.merge(pot[CTY_KEYS + ["cms_specialty", "capacity_potential_p75"]],
                        on=CTY_KEYS + ["cms_specialty"], how="left")

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

    weave = weave.merge(err[CTY_KEYS + ["expected_error_pct"]], on=CTY_KEYS, how="left")
    weave["expected_error_band"] = np.where(
        weave["expected_error_pct"].isna(), "C",
        np.where(weave["expected_error_pct"] <= 0.25, "A",
                 np.where(weave["expected_error_pct"] <= 0.50, "B", "C")))

    weave = weave.merge(age_mix[CTY_KEYS + ["pct_medicare_age_members"]],
                        on=CTY_KEYS, how="left")
    weave = weave.merge(hcc[CTY_KEYS + ["avg_hcc_conditions_per_member"]],
                        on=CTY_KEYS, how="left")

    name_map = ref.set_index("county_fips")["county_name"]
    weave["county_name"] = weave["county_fips"].map(name_map)

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
    print("column population summary:")
    total = len(weave)
    print(f"{'column':<34}{'non_null':>12}{'pct':>8}")
    for c in OUT_COLS:
        nn = int(weave[c].notna().sum())
        print(f"{c:<34}{nn:>12,}{(nn / total if total else 0):>8.1%}")
    h = weave["avg_hcc_conditions_per_member"]
    print(f"avg_hcc_conditions_per_member min/avg/max: "
          f"{h.min():.3f} / {h.mean():.3f} / {h.max():.3f}")

    from google.cloud import bigquery
    job_config = bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE")
    client.load_table_from_dataframe(weave, OUT_TBL, job_config=job_config).result()
    print(f"wrote {len(weave):,} rows -> {OUT_TBL}")


if __name__ == "__main__":
    main()
