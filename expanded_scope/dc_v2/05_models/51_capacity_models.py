"""
51 - capacity models   [PYTHON / pandas + scikit-learn + xgboost]

WHAT  : Trains the two capacity models. MODEL 1 (bottom-up): XGBoost at
        provider grain on dc2_capacity_provider, predictions aggregated to
        prvdr_county x specialty_ctg_cd x month. MODEL 2 (top-down):
        LinearRegression (sparse one-hot) + XGBoost at county grain on
        dc2_capacity_county. Same targets and time splits as notebook 50
        (target_next_1m: train 2024-01..2025-05, validate 2025-06..2025-11;
        target_next_12m: train 2024-01..2024-11, validate 2024-12). XGBoost
        fixed seed 42, n_estimators=500, learning_rate=0.05, max_depth=6,
        early stopping on the 10 percent time-ordered tail of train.
        divergence_pct = (bottom_up - top_down_xgb) / top_down_xgb,
        divide-by-zero -> NULL. No hyperparameter search. Memory: downcast
        dtypes at load, provider scoring in 500,000-row chunks with county
        aggregation from the chunked scores, provider frame freed before
        county modeling. Progress logged per stage.
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

import gc
import datetime
import numpy as np
import pandas as pd
from scipy import sparse
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import OneHotEncoder
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

PROV_COUNTS = ["visits", "panel_members", "panel_lt65", "panel_65_74", "panel_75_84",
               "panel_85p", "panel_chronic_members", "distinct_mbr_counties", "tenure_months"]
CNTY_COUNTS = ["visits", "providers"]
TARGET_COLS = ["target_next_1m", "target_next_12m"]
CALENDAR_COLS = ["month_of_year", "year", "month_index"]

A_TRAIN_END = pd.Timestamp("2025-05-01")
A_VAL_START = pd.Timestamp("2025-06-01")
A_VAL_END   = pd.Timestamp("2025-11-01")
B_TRAIN_END = pd.Timestamp("2024-11-01")
B_VAL_MONTH = pd.Timestamp("2024-12-01")
FUTURE_MONTH = pd.Timestamp("2025-12-01")

XGB_PARAMS = dict(n_estimators=500, learning_rate=0.05, max_depth=6,
                  random_state=42, early_stopping_rounds=50)
PROV_CHUNK = 500_000

KEYS = ["prvdr_county", "specialty_ctg_cd", "month"]


def log(msg):
    print(f"[{datetime.datetime.now():%H:%M:%S}] {msg}", flush=True)


def _mb(df):
    return df.memory_usage(deep=True).sum() / 1_048_576


def load(table, count_cols, name):
    log(f"loading {name} from BigQuery")
    df = cfg.client().query(f"SELECT * FROM `{table}`").result().to_dataframe()
    df["month"] = pd.to_datetime(df["month"])
    for c in count_cols:
        df[c] = df[c].fillna(0).astype("int32")
    for c in TARGET_COLS:
        df[c] = df[c].astype("float32")
    df["pct_new_patients"] = df["pct_new_patients"].astype("float32")
    for c in CALENDAR_COLS:
        df[c] = df[c].astype("int16")
    for c in CAT_FEATURES:
        df[c] = df[c].astype("category")
    df = df.sort_values(["month"]).reset_index(drop=True)
    log(f"loaded {name}: {len(df):,} rows, {_mb(df):,.0f} MB")
    return df


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
    x_o, y_o = x.iloc[order], y[order]
    cut = int(len(x_o) * 0.9)
    if cut <= 0 or cut >= len(x_o):
        model = XGBRegressor(**{k: v for k, v in XGB_PARAMS.items()
                                if k != "early_stopping_rounds"})
        model.fit(x_o, y_o, verbose=False)
        return model
    model = XGBRegressor(**XGB_PARAMS)
    model.fit(x_o.iloc[:cut], y_o[:cut],
              eval_set=[(x_o.iloc[cut:], y_o[cut:])], verbose=False)
    return model


def predict_chunks(model, x, chunk, label):
    n = x.shape[0]
    total = (n + chunk - 1) // chunk
    parts = []
    for i in range(total):
        log(f"scoring {label}: chunk {i + 1} of {total}")
        lo, hi = i * chunk, min((i + 1) * chunk, n)
        xi = x[lo:hi] if not hasattr(x, "iloc") else x.iloc[lo:hi]
        parts.append(model.predict(xi))
    return np.concatenate(parts).astype("float32")


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
    cnty = load(CNTY_TBL, CNTY_COUNTS, "dc2_capacity_county")
    county_2024 = cnty[cnty["year"] == 2024].groupby("prvdr_county", observed=True)["visits"].sum()

    metrics, importances = [], []

    prov = load(PROV_TBL, PROV_COUNTS, "dc2_capacity_provider")
    prov_band = prov["prvdr_county"].map(county_2024).fillna(0).map(size_band)

    log("building provider XGBoost feature frame (float32/int32, column-referenced)")
    prov_data = {c: prov[c].fillna(0) for c in PROV_NUM}
    for c in CAT_FEATURES:
        prov_data[c] = prov[c].cat.codes.astype("int32")
    x_prov = pd.DataFrame(prov_data)
    del prov_data
    gc.collect()
    log(f"provider frame ready: {x_prov.shape[0]:,} x {x_prov.shape[1]:,}")

    prov_tasks = task_masks(prov["month"])
    bu_frames = {}
    for task, spec in prov_tasks.items():
        y = prov[spec["target"]].to_numpy(dtype="float32")
        y_ser = prov[spec["target"]].astype("float32")
        fit_idx = np.where((spec["train"] & y_ser.notna()).to_numpy())[0]
        log(f"fitting provider XGBoost, target {task}, {len(fit_idx):,} x {x_prov.shape[1]:,}")
        xgb = fit_xgb(x_prov.iloc[fit_idx], y[fit_idx], prov["month"].iloc[fit_idx])
        log(f"fit done, provider xgb target {task}")
        importances.append(top_gain(xgb, "provider_xgb_bottom_up", task))

        score_mask = (spec["val"] | (prov["month"] == FUTURE_MONTH)).to_numpy()
        score_idx = np.where(score_mask)[0]
        log(f"provider scoring, target {task}: {len(score_idx):,} rows in "
            f"chunks of {PROV_CHUNK:,}")
        pred_scored = predict_chunks(xgb, x_prov.iloc[score_idx], PROV_CHUNK,
                                     f"provider xgb {task}")

        log(f"validation scoring, provider xgb target {task}")
        scored_pred = pd.Series(pred_scored, index=prov.index[score_idx])
        val = spec["val"] & y_ser.notna()
        val_idx = prov.index[val]
        metrics.append(eval_rows("provider_xgb_bottom_up", task,
                                 y_ser[val], scored_pred.reindex(val_idx),
                                 prov_band[val]))

        log(f"aggregating provider predictions to county, target {task}")
        agg = (prov.loc[score_idx, KEYS].assign(pred=pred_scored)
               .groupby(KEYS, as_index=False, observed=True)["pred"].sum()
               .rename(columns={"pred": f"bottom_up_{task}"}))
        bu_frames[task] = agg
        del pred_scored, scored_pred, agg
        gc.collect()

    log("freeing provider frame before county modeling")
    del prov, x_prov, prov_band, prov_tasks
    gc.collect()

    log("building county design matrices")
    x_cnty_num = sparse.csr_matrix(
        np.nan_to_num(cnty[CNTY_NUM].to_numpy(dtype="float32"), nan=0.0))
    ohe = OneHotEncoder(sparse_output=True, handle_unknown="ignore")
    x_cnty_cat = ohe.fit_transform(cnty[CAT_FEATURES].astype(str))
    x_cnty_lin = sparse.hstack([x_cnty_num, x_cnty_cat], format="csr")
    del x_cnty_num, x_cnty_cat
    gc.collect()
    cnty_data = {c: cnty[c].fillna(0) for c in CNTY_NUM}
    for c in CAT_FEATURES:
        cnty_data[c] = cnty[c].cat.codes.astype("int32")
    x_cnty_xgb = pd.DataFrame(cnty_data)
    del cnty_data
    gc.collect()
    log(f"county matrices ready: linear {x_cnty_lin.shape[0]:,} x {x_cnty_lin.shape[1]:,} "
        f"(sparse), xgb {x_cnty_xgb.shape[0]:,} x {x_cnty_xgb.shape[1]:,}")

    preds = cnty[KEYS].copy()
    preds["actual_next_1m"] = cnty["target_next_1m"]
    preds["actual_next_12m"] = cnty["target_next_12m"]
    cnty_band = cnty["prvdr_county"].map(county_2024).fillna(0).map(size_band)
    cnty_tasks = task_masks(cnty["month"])
    for task, spec in cnty_tasks.items():
        y = cnty[spec["target"]].to_numpy(dtype="float32")
        y_ser = cnty[spec["target"]].astype("float32")
        fit_idx = np.where((spec["train"] & y_ser.notna()).to_numpy())[0]

        log(f"fitting county LinearRegression, target {task}, "
            f"{len(fit_idx):,} x {x_cnty_lin.shape[1]:,}")
        lin = LinearRegression()
        lin.fit(x_cnty_lin[fit_idx], y[fit_idx])
        log(f"fit done, county linear target {task}")
        pred_lin = pd.Series(lin.predict(x_cnty_lin).astype("float32"), index=cnty.index)

        log(f"fitting county XGBoost, target {task}, "
            f"{len(fit_idx):,} x {x_cnty_xgb.shape[1]:,}")
        xgb = fit_xgb(x_cnty_xgb.iloc[fit_idx], y[fit_idx], cnty["month"].iloc[fit_idx])
        log(f"fit done, county xgb target {task}")
        pred_xgb = pd.Series(xgb.predict(x_cnty_xgb).astype("float32"), index=cnty.index)

        preds[f"top_down_{task}_linear"] = pred_lin
        preds[f"top_down_{task}_xgb"] = pred_xgb

        log(f"validation scoring, county models target {task}")
        val = spec["val"] & y_ser.notna()
        metrics.append(eval_rows("county_linear_top_down", task, y_ser[val],
                                 pred_lin[val], cnty_band[val]))
        metrics.append(eval_rows("county_xgb_top_down", task, y_ser[val],
                                 pred_xgb[val], cnty_band[val]))
        importances.append(top_gain(xgb, "county_xgb_top_down", task))

    log("computing divergence")
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
    log(f"writing {METRICS_CSV}")
    metrics_df.to_csv(METRICS_CSV, index=False)
    log(f"writing {IMPORTANCE_CSV}")
    pd.concat(importances, ignore_index=True).to_csv(IMPORTANCE_CSV, index=False)

    log("building divergence summary (validation months)")
    div_rows = []
    band_col = preds["prvdr_county"].map(county_2024).fillna(0).map(size_band)
    val_months = {"next_1m": (preds["month"] >= A_VAL_START) & (preds["month"] <= A_VAL_END),
                  "next_12m": preds["month"] == B_VAL_MONTH}
    for task, mask in val_months.items():
        sub_mask = mask & preds[f"divergence_pct_{task}"].notna()
        absd = preds.loc[sub_mask, f"divergence_pct_{task}"].abs()
        sub_band = band_col[sub_mask]
        for band in ("small", "mid", "large"):
            m = sub_band == band
            div_rows.append({
                "target": task,
                "county_size_band": band,
                "cells_le_010": int((absd[m] <= 0.10).sum()),
                "cells_010_025": int(((absd[m] > 0.10) & (absd[m] <= 0.25)).sum()),
                "cells_gt_025": int((absd[m] > 0.25).sum()),
            })
    log(f"writing {DIVERGENCE_CSV}")
    pd.DataFrame(div_rows).to_csv(DIVERGENCE_CSV, index=False)

    log("assembling prediction output frame")
    out = preds.copy()
    out["prvdr_county"] = out["prvdr_county"].astype(str)
    out["specialty_ctg_cd"] = out["specialty_ctg_cd"].astype(str)
    out["month"] = out["month"].dt.date

    log(f"writing {len(out):,} rows -> {OUT_TBL}")
    from google.cloud import bigquery
    job_config = bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE")
    cfg.client().load_table_from_dataframe(out, OUT_TBL, job_config=job_config).result()
    log(f"BigQuery write done: {OUT_TBL}")


if __name__ == "__main__":
    main()
