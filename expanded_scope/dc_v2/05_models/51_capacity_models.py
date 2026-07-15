"""
51 - capacity models   [PYTHON / pandas + scikit-learn + xgboost]

WHAT  : Trains the two capacity models. MODEL 1 (bottom-up): XGBoost at
        provider grain on dc2_capacity_provider, predictions aggregated to
        prvdr_county x specialty_ctg_cd x month. MODEL 2 (top-down):
        LinearRegression + XGBoost at county grain on dc2_capacity_county.
        Same targets and time splits as notebook 50 (target_next_1m: train
        2024-01..2025-05, validate 2025-06..2025-11; target_next_12m: train
        2024-01..2024-11, validate 2024-12). XGBoost fixed seed 42,
        n_estimators=500, learning_rate=0.05, max_depth=6, early stopping on
        the 10 percent time-ordered tail of train. divergence_pct =
        (bottom_up - top_down_xgb) / top_down_xgb, divide-by-zero -> NULL.
        No hyperparameter search.
GRAIN : predictions at prvdr_county x specialty_ctg_cd x month.
INPUTS: dc2_capacity_provider + dc2_capacity_county (BigQuery, cfg dataset)
OUTPUT: BigQuery table dc2_capacity_predictions (split_label train/
        validation/future; feature month 2025-12 scored = 2026 capacity);
        expanded_scope/dc_v2/05_models/51_capacity_metrics.csv;
        expanded_scope/dc_v2/05_models/51_capacity_feature_importance.csv;
        expanded_scope/dc_v2/05_models/51_divergence_summary.csv.
Run   : python expanded_scope/dc_v2/05_models/51_capacity_models.py
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
from sklearn.linear_model import LinearRegression
from xgboost import XGBRegressor

np.random.seed(42)

PROV_TBL = cfg.src("dc2_capacity_provider")
CNTY_TBL = cfg.src("dc2_capacity_county")
OUT_TBL  = cfg.src("dc2_capacity_predictions")

OUT_DIR = cfg.repo_path("expanded_scope", "dc_v2", "05_models")
METRICS_CSV    = os.path.join(OUT_DIR, "51_capacity_metrics.csv")
IMPORTANCE_CSV = os.path.join(OUT_DIR, "51_capacity_feature_importance.csv")
DIVERGENCE_CSV = os.path.join(OUT_DIR, "51_divergence_summary.csv")

PROV_NUM = ["panel_members", "panel_lt65", "panel_65_74", "panel_75_84", "panel_85p",
            "panel_chronic_members", "pct_new_patients", "distinct_mbr_counties",
            "tenure_months", "month_of_year", "year", "month_index"]
CNTY_NUM = ["providers", "pct_new_patients", "month_of_year", "year", "month_index"]
CAT_FEATURES = ["specialty_ctg_cd", "prvdr_county"]

A_TRAIN_END = pd.Timestamp("2025-05-01")
A_VAL_START = pd.Timestamp("2025-06-01")
A_VAL_END   = pd.Timestamp("2025-11-01")
B_TRAIN_END = pd.Timestamp("2024-11-01")
B_VAL_MONTH = pd.Timestamp("2024-12-01")
FUTURE_MONTH = pd.Timestamp("2025-12-01")

XGB_PARAMS = dict(n_estimators=500, learning_rate=0.05, max_depth=6,
                  random_state=42, early_stopping_rounds=50)

KEYS = ["prvdr_county", "specialty_ctg_cd", "month"]


def load(table):
    df = cfg.client().query(f"SELECT * FROM `{table}`").result().to_dataframe()
    df["month"] = pd.to_datetime(df["month"])
    return df.sort_values(["month"] + [c for c in KEYS[:2] if c in df.columns]).reset_index(drop=True)


def task_masks(months):
    return {
        "next_1m": {"target": "target_next_1m",
                    "train": months <= A_TRAIN_END,
                    "val": (months >= A_VAL_START) & (months <= A_VAL_END)},
        "next_12m": {"target": "target_next_12m",
                     "train": months <= B_TRAIN_END,
                     "val": months == B_VAL_MONTH},
    }


def fit_xgb(x, y, months):
    order = np.argsort(months.values, kind="stable")
    x_o, y_o = x.iloc[order], y.iloc[order]
    cut = int(len(x_o) * 0.9)
    if cut <= 0 or cut >= len(x_o):
        model = XGBRegressor(**{k: v for k, v in XGB_PARAMS.items()
                                if k != "early_stopping_rounds"})
        model.fit(x_o, y_o, verbose=False)
        return model
    model = XGBRegressor(**XGB_PARAMS)
    model.fit(x_o.iloc[:cut], y_o.iloc[:cut],
              eval_set=[(x_o.iloc[cut:], y_o.iloc[cut:])], verbose=False)
    return model


def size_band(v):
    if v < 10_000:
        return "small"
    if v > 100_000:
        return "large"
    return "mid"


def eval_rows(label_model, label_target, actual, pred, bands):
    ok = actual.notna()
    actual, pred, bands = actual[ok], pred[ok], bands[ok]
    err = (pred - actual).abs()
    ge10 = actual >= 10
    row = {
        "model": label_model,
        "target": label_target,
        "n_val": int(ok.sum()),
        "mae_overall": float(err.mean()),
        "mape_ge10": float((err[ge10] / actual[ge10]).mean()) if ge10.any() else None,
    }
    for b in ("small", "mid", "large"):
        m = bands == b
        row[f"mae_{b}"] = float(err[m].mean()) if m.any() else None
    return row


def top_gain(model, label_model, label_target):
    gain = model.get_booster().get_score(importance_type="gain")
    imp = (pd.DataFrame({"feature": list(gain.keys()), "gain": list(gain.values())})
           .sort_values("gain", ascending=False).head(30))
    imp.insert(0, "target", label_target)
    imp.insert(0, "model", label_model)
    return imp


def main():
    prov = load(PROV_TBL)
    cnty = load(CNTY_TBL)

    county_2024 = cnty[cnty["year"] == 2024].groupby("prvdr_county")["visits"].sum()
    prov["county_band"] = prov["prvdr_county"].map(county_2024).fillna(0).map(size_band)
    cnty["county_band"] = cnty["prvdr_county"].map(county_2024).fillna(0).map(size_band)

    metrics, importances = [], []

    prov_num = prov[PROV_NUM].astype(float).fillna(0)
    x_prov = prov_num.copy()
    for c in CAT_FEATURES:
        x_prov[c] = prov[c].astype("category").cat.codes
    prov_tasks = task_masks(prov["month"])
    bu_frames = {}
    for task, spec in prov_tasks.items():
        y = prov[spec["target"]].astype(float)
        fit_mask = spec["train"] & y.notna()
        xgb = fit_xgb(x_prov[fit_mask], y[fit_mask], prov.loc[fit_mask, "month"])
        pred = pd.Series(xgb.predict(x_prov), index=prov.index)
        val = spec["val"] & y.notna()
        metrics.append(eval_rows("provider_xgb_bottom_up", task, y[val], pred[val],
                                 prov.loc[val, "county_band"]))
        importances.append(top_gain(xgb, "provider_xgb_bottom_up", task))
        score_mask = spec["val"] | (prov["month"] == FUTURE_MONTH)
        agg = (prov.loc[score_mask, KEYS].assign(pred=pred[score_mask])
               .groupby(KEYS, as_index=False)["pred"].sum()
               .rename(columns={"pred": f"bottom_up_{task}"}))
        bu_frames[task] = agg

    cnty_num = cnty[CNTY_NUM].astype(float).fillna(0)
    dummies = pd.get_dummies(cnty[CAT_FEATURES].astype(str), dtype=float)
    x_cnty_lin = pd.concat([cnty_num, dummies], axis=1)
    x_cnty_xgb = cnty_num.copy()
    for c in CAT_FEATURES:
        x_cnty_xgb[c] = cnty[c].astype("category").cat.codes

    preds = cnty[KEYS].copy()
    preds["actual_next_1m"] = cnty["target_next_1m"]
    preds["actual_next_12m"] = cnty["target_next_12m"]
    cnty_tasks = task_masks(cnty["month"])
    for task, spec in cnty_tasks.items():
        y = cnty[spec["target"]].astype(float)
        fit_mask = spec["train"] & y.notna()

        lin = LinearRegression()
        lin.fit(x_cnty_lin[fit_mask], y[fit_mask])
        pred_lin = pd.Series(lin.predict(x_cnty_lin), index=cnty.index)

        xgb = fit_xgb(x_cnty_xgb[fit_mask], y[fit_mask], cnty.loc[fit_mask, "month"])
        pred_xgb = pd.Series(xgb.predict(x_cnty_xgb), index=cnty.index)

        preds[f"top_down_{task}_linear"] = pred_lin
        preds[f"top_down_{task}_xgb"] = pred_xgb

        val = spec["val"] & y.notna()
        metrics.append(eval_rows("county_linear_top_down", task, y[val], pred_lin[val],
                                 cnty.loc[val, "county_band"]))
        metrics.append(eval_rows("county_xgb_top_down", task, y[val], pred_xgb[val],
                                 cnty.loc[val, "county_band"]))
        importances.append(top_gain(xgb, "county_xgb_top_down", task))

    for task, agg in bu_frames.items():
        preds = preds.merge(agg, on=KEYS, how="left")
        bu = preds[f"bottom_up_{task}"]
        td = preds[f"top_down_{task}_xgb"]
        preds[f"divergence_pct_{task}"] = np.where(
            bu.notna() & td.notna() & (td != 0), (bu - td) / td, np.nan)

    preds["split_label"] = np.where(
        preds["month"] == FUTURE_MONTH, "future",
        np.where((preds["month"] >= A_VAL_START) & (preds["month"] <= A_VAL_END),
                 "validation", "train"))

    metrics_df = pd.DataFrame(metrics)
    print(metrics_df.to_string(index=False))
    metrics_df.to_csv(METRICS_CSV, index=False)
    pd.concat(importances, ignore_index=True).to_csv(IMPORTANCE_CSV, index=False)

    div_rows = []
    preds["county_band"] = preds["prvdr_county"].map(county_2024).fillna(0).map(size_band)
    val_months = {"next_1m": (preds["month"] >= A_VAL_START) & (preds["month"] <= A_VAL_END),
                  "next_12m": preds["month"] == B_VAL_MONTH}
    for task, mask in val_months.items():
        sub = preds.loc[mask & preds[f"divergence_pct_{task}"].notna()]
        absd = sub[f"divergence_pct_{task}"].abs()
        for band in ("small", "mid", "large"):
            m = sub["county_band"] == band
            div_rows.append({
                "target": task,
                "county_size_band": band,
                "cells_le_010": int((absd[m] <= 0.10).sum()),
                "cells_010_025": int(((absd[m] > 0.10) & (absd[m] <= 0.25)).sum()),
                "cells_gt_025": int((absd[m] > 0.25).sum()),
            })
    pd.DataFrame(div_rows).to_csv(DIVERGENCE_CSV, index=False)

    out = preds.drop(columns=["county_band"]).copy()
    out["month"] = out["month"].dt.date
    from google.cloud import bigquery
    job_config = bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE")
    cfg.client().load_table_from_dataframe(out, OUT_TBL, job_config=job_config).result()
    print(f"wrote {len(out)} rows -> {OUT_TBL}")
    print(f"wrote {METRICS_CSV}")
    print(f"wrote {IMPORTANCE_CSV}")
    print(f"wrote {DIVERGENCE_CSV}")


if __name__ == "__main__":
    main()
