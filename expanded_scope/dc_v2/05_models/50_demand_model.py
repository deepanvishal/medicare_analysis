"""
50 - demand model   [PYTHON / pandas + scikit-learn + xgboost]

WHAT  : Trains the demand prediction models on dc2_demand_base joined to the
        wide chronic prevalence table (one prev_hcc_{code} column per
        HCC_v24; missing prevalence = 0). Two targets: target_next_1m and
        target_next_12m. Two models per target: LinearRegression (sparse
        one-hot categoricals) and XGBRegressor (label-encoded categoricals,
        seed 42, n_estimators=500, learning_rate=0.05, max_depth=6, early
        stopping on the 10 percent time-ordered tail of train). Time-based
        splits, no shuffling. No hyperparameter search. Memory: downcast
        dtypes at load, sparse linear design matrix, chunked scoring
        (200,000 rows), del + gc between paths. Progress logged per stage.
GRAIN : predictions at mbr_county_cd x specialty_ctg_cd x feature month.
INPUTS: dc2_demand_base + dc2_demand_chronic (BigQuery, cfg dataset)
OUTPUT: BigQuery table dc2_demand_predictions (split_label train/validation/
        future; feature month 2025-12 scored = the 2026 predictions);
        expanded_scope/dc_v2/05_models/50_demand_metrics.csv;
        expanded_scope/dc_v2/05_models/50_demand_feature_importance.csv.
Run   : python expanded_scope/dc_v2/05_models/50_demand_model.py
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

BASE    = cfg.src("dc2_demand_base")
CHRONIC = cfg.src("dc2_demand_chronic")
OUT_TBL = cfg.src("dc2_demand_predictions")

OUT_DIR = cfg.repo_path("expanded_scope", "dc_v2", "05_models")
METRICS_CSV    = os.path.join(OUT_DIR, "50_demand_metrics.csv")
IMPORTANCE_CSV = os.path.join(OUT_DIR, "50_demand_feature_importance.csv")

NUM_FEATURES = ["members", "mbr_age_60_64", "mbr_age_65_74", "mbr_age_75_84", "mbr_age_85p",
                "pct_new_patients", "month_of_year", "year", "month_index"]
CAT_FEATURES = ["mbr_county_cd", "specialty_ctg_cd"]

COUNT_COLS    = ["visits", "members", "mbr_age_60_64", "mbr_age_65_74", "mbr_age_75_84",
                 "mbr_age_85p"]
TARGET_COLS   = ["target_next_1m", "target_next_12m"]
CALENDAR_COLS = ["month_of_year", "year", "month_index"]

A_TRAIN_END = pd.Timestamp("2025-05-01")
A_VAL_START = pd.Timestamp("2025-06-01")
A_VAL_END   = pd.Timestamp("2025-11-01")
B_TRAIN_END = pd.Timestamp("2024-11-01")
B_VAL_MONTH = pd.Timestamp("2024-12-01")
FUTURE_MONTH = pd.Timestamp("2025-12-01")

XGB_PARAMS = dict(n_estimators=500, learning_rate=0.05, max_depth=6,
                  random_state=42, early_stopping_rounds=50)
CHUNK = 200_000


def log(msg):
    print(f"[{datetime.datetime.now():%H:%M:%S}] {msg}", flush=True)


def _mb(df):
    return df.memory_usage(deep=True).sum() / 1_048_576


def _hcc_col(code):
    try:
        return f"prev_hcc_{int(float(code))}"
    except (TypeError, ValueError):
        return f"prev_hcc_{str(code).strip()}"


def load():
    client = cfg.client()

    log("loading dc2_demand_base from BigQuery")
    base = client.query(f"SELECT * FROM `{BASE}`").result().to_dataframe()
    base["month"] = pd.to_datetime(base["month"])
    for c in COUNT_COLS:
        base[c] = base[c].fillna(0).astype("int32")
    for c in TARGET_COLS:
        base[c] = base[c].astype("float32")
    base["pct_new_patients"] = base["pct_new_patients"].astype("float32")
    for c in CALENDAR_COLS:
        base[c] = base[c].astype("int16")
    log(f"loaded dc2_demand_base: {len(base):,} rows, {_mb(base):,.0f} MB")

    log("loading dc2_demand_chronic from BigQuery")
    chronic = client.query(
        f"SELECT mbr_county_cd, month, HCC_v24, prevalence FROM `{CHRONIC}`"
    ).result().to_dataframe()
    chronic["month"] = pd.to_datetime(chronic["month"])
    chronic["prevalence"] = chronic["prevalence"].astype("float32")
    log(f"loaded dc2_demand_chronic: {len(chronic):,} rows, {_mb(chronic):,.0f} MB")

    log("pivoting chronic to wide (float32)")
    chronic["hcc_col"] = chronic["HCC_v24"].map(_hcc_col)
    wide = chronic.pivot_table(index=["mbr_county_cd", "month"], columns="hcc_col",
                               values="prevalence", aggfunc="max").astype("float32").reset_index()
    del chronic
    gc.collect()
    log(f"pivot done: {len(wide):,} rows, {wide.shape[1] - 2} prev_hcc_ columns")

    log("merging base with chronic wide")
    df = base.merge(wide, on=["mbr_county_cd", "month"], how="left")
    del base, wide
    gc.collect()
    prev_cols = sorted(c for c in df.columns if c.startswith("prev_hcc_"))
    df[prev_cols] = df[prev_cols].fillna(0).astype("float32")
    for c in CAT_FEATURES:
        df[c] = df[c].astype("category")
    df = df.sort_values(["month", "mbr_county_cd", "specialty_ctg_cd"]).reset_index(drop=True)
    gc.collect()
    log(f"merged modeling frame: {len(df):,} rows, {_mb(df):,.0f} MB")
    return df, prev_cols


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


def predict_chunks(model, x, label):
    n = x.shape[0]
    total = (n + CHUNK - 1) // CHUNK
    parts = []
    for i in range(total):
        log(f"scoring {label}: chunk {i + 1} of {total}")
        lo, hi = i * CHUNK, min((i + 1) * CHUNK, n)
        xi = x[lo:hi] if not hasattr(x, "iloc") else x.iloc[lo:hi]
        parts.append(model.predict(xi))
    return np.concatenate(parts).astype("float32")


def size_band(v):
    if v < 10_000:
        return "small"
    if v > 100_000:
        return "large"
    return "mid"


def eval_rows(label_target, label_model, actual, pred, bands):
    ok = actual.notna()
    actual, pred, bands = actual[ok], pred[ok], bands[ok]
    err = (pred - actual).abs()
    ge10 = actual >= 10
    row = {
        "target": label_target,
        "model": label_model,
        "n_val": int(ok.sum()),
        "mae_overall": float(err.mean()),
        "mape_ge10": float((err[ge10] / actual[ge10]).mean()) if ge10.any() else None,
    }
    for b in ("small", "mid", "large"):
        m = bands == b
        row[f"mae_{b}"] = float(err[m].mean()) if m.any() else None
    return row


def main():
    df, prev_cols = load()
    months = df["month"]

    county_2024 = df[df["year"] == 2024].groupby("mbr_county_cd", observed=True)["visits"].sum()
    county_band = df["mbr_county_cd"].map(county_2024).fillna(0).map(size_band)

    tasks = {
        "next_1m": {"target": "target_next_1m",
                    "train": months <= A_TRAIN_END,
                    "val": (months >= A_VAL_START) & (months <= A_VAL_END)},
        "next_12m": {"target": "target_next_12m",
                     "train": months <= B_TRAIN_END,
                     "val": months == B_VAL_MONTH},
    }

    num_cols = NUM_FEATURES + prev_cols
    metrics, importances, pred_store = [], [], {}

    log("building sparse linear design matrix")
    x_num = sparse.csr_matrix(
        np.nan_to_num(df[num_cols].to_numpy(dtype="float32"), nan=0.0))
    ohe = OneHotEncoder(sparse_output=True, handle_unknown="ignore")
    x_cat = ohe.fit_transform(df[CAT_FEATURES].astype(str))
    x_lin = sparse.hstack([x_num, x_cat], format="csr")
    del x_num, x_cat
    gc.collect()
    log(f"linear matrix ready: {x_lin.shape[0]:,} x {x_lin.shape[1]:,} (sparse)")

    for task, spec in tasks.items():
        y = df[spec["target"]].to_numpy(dtype="float32")
        fit_idx = np.where((spec["train"] & df[spec["target"]].notna()).to_numpy())[0]
        log(f"fitting LinearRegression, target {task}, {len(fit_idx):,} x {x_lin.shape[1]:,}")
        lin = LinearRegression()
        lin.fit(x_lin[fit_idx], y[fit_idx])
        log(f"fit done, target {task} (linear)")
        pred_store[(task, "linear")] = predict_chunks(lin, x_lin, f"linear {task}")

    del x_lin
    gc.collect()

    log("building XGBoost feature frame (float32/int32, column-referenced)")
    xgb_data = {c: df[c].fillna(0) for c in num_cols}
    for c in CAT_FEATURES:
        xgb_data[c] = df[c].cat.codes.astype("int32")
    x_xgb = pd.DataFrame(xgb_data)
    del xgb_data
    gc.collect()
    log(f"xgb frame ready: {x_xgb.shape[0]:,} x {x_xgb.shape[1]:,}")

    for task, spec in tasks.items():
        y = df[spec["target"]].to_numpy(dtype="float32")
        fit_mask = (spec["train"] & df[spec["target"]].notna()).to_numpy()
        fit_idx = np.where(fit_mask)[0]
        log(f"fitting XGBoost, target {task}, {len(fit_idx):,} x {x_xgb.shape[1]:,}")
        xgb = fit_xgb(x_xgb.iloc[fit_idx], y[fit_idx], months.iloc[fit_idx])
        log(f"fit done, target {task} (xgb)")
        pred_store[(task, "xgb")] = predict_chunks(xgb, x_xgb, f"xgb {task}")

        gain = xgb.get_booster().get_score(importance_type="gain")
        imp = (pd.DataFrame({"feature": list(gain.keys()), "gain": list(gain.values())})
               .sort_values("gain", ascending=False).head(30))
        imp.insert(0, "target", task)
        importances.append(imp)

    for task, spec in tasks.items():
        log(f"validation scoring, target {task}")
        y_ser = df[spec["target"]].astype("float32")
        val = spec["val"] & y_ser.notna()
        for model_name in ("linear", "xgb"):
            pred = pd.Series(pred_store[(task, model_name)], index=df.index)
            metrics.append(eval_rows(task, model_name, y_ser[val], pred[val], county_band[val]))

    metrics_df = pd.DataFrame(metrics)
    print(metrics_df.to_string(index=False))
    log(f"writing {METRICS_CSV}")
    metrics_df.to_csv(METRICS_CSV, index=False)
    log(f"writing {IMPORTANCE_CSV}")
    pd.concat(importances, ignore_index=True).to_csv(IMPORTANCE_CSV, index=False)

    log("assembling prediction output frame")
    out = pd.DataFrame({
        "mbr_county_cd": df["mbr_county_cd"].astype(str),
        "specialty_ctg_cd": df["specialty_ctg_cd"].astype(str),
        "month": df["month"].dt.date,
        "actual_next_1m": df["target_next_1m"],
        "pred_next_1m_linear": pred_store[("next_1m", "linear")],
        "pred_next_1m_xgb": pred_store[("next_1m", "xgb")],
        "actual_next_12m": df["target_next_12m"],
        "pred_next_12m_linear": pred_store[("next_12m", "linear")],
        "pred_next_12m_xgb": pred_store[("next_12m", "xgb")],
        "split_label": np.where(
            months == FUTURE_MONTH, "future",
            np.where((months >= A_VAL_START) & (months <= A_VAL_END),
                     "validation", "train")),
    })

    log(f"writing {len(out):,} rows -> {OUT_TBL}")
    from google.cloud import bigquery
    job_config = bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE")
    cfg.client().load_table_from_dataframe(out, OUT_TBL, job_config=job_config).result()
    log(f"BigQuery write done: {OUT_TBL}")


if __name__ == "__main__":
    main()
