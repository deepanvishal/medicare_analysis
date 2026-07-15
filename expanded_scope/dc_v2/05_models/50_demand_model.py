"""
50 - demand model   [PYTHON / pandas + scikit-learn + xgboost]

WHAT  : Trains the demand prediction models on dc2_demand_base joined to the
        wide chronic prevalence table (one prev_hcc_{code} column per
        HCC_v24; missing prevalence = 0). Two targets: target_next_1m and
        target_next_12m. Two models per target: LinearRegression (one-hot
        categoricals) and XGBRegressor (label-encoded categoricals, seed 42,
        n_estimators=500, learning_rate=0.05, max_depth=6, early stopping on
        the 10 percent time-ordered tail of train). Time-based splits, no
        shuffling. No hyperparameter search.
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

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from xgboost import XGBRegressor

np.random.seed(42)

BASE    = cfg.src("dc2_demand_base")
CHRONIC = cfg.src("dc2_demand_chronic")
OUT_TBL = cfg.src("dc2_demand_predictions")

OUT_DIR = cfg.repo_path("expanded_scope", "dc_v2", "05_models")
METRICS_CSV    = os.path.join(OUT_DIR, "50_demand_metrics.csv")
IMPORTANCE_CSV = os.path.join(OUT_DIR, "50_demand_feature_importance.csv")

NUM_FEATURES = ["members", "mbr_lt65", "mbr_65_74", "mbr_75_84", "mbr_85p",
                "pct_new_patients", "month_of_year", "year", "month_index"]
CAT_FEATURES = ["mbr_county_cd", "specialty_ctg_cd"]

A_TRAIN_END = pd.Timestamp("2025-05-01")
A_VAL_START = pd.Timestamp("2025-06-01")
A_VAL_END   = pd.Timestamp("2025-11-01")
B_TRAIN_END = pd.Timestamp("2024-11-01")
B_VAL_MONTH = pd.Timestamp("2024-12-01")
FUTURE_MONTH = pd.Timestamp("2025-12-01")

XGB_PARAMS = dict(n_estimators=500, learning_rate=0.05, max_depth=6,
                  random_state=42, early_stopping_rounds=50)


def _hcc_col(code):
    try:
        return f"prev_hcc_{int(float(code))}"
    except (TypeError, ValueError):
        return f"prev_hcc_{str(code).strip()}"


def load():
    client = cfg.client()
    base = client.query(f"SELECT * FROM `{BASE}`").result().to_dataframe()
    chronic = client.query(
        f"SELECT mbr_county_cd, month, HCC_v24, prevalence FROM `{CHRONIC}`"
    ).result().to_dataframe()
    base["month"] = pd.to_datetime(base["month"])
    chronic["month"] = pd.to_datetime(chronic["month"])
    chronic["hcc_col"] = chronic["HCC_v24"].map(_hcc_col)
    wide = chronic.pivot_table(index=["mbr_county_cd", "month"], columns="hcc_col",
                               values="prevalence", aggfunc="max").reset_index()
    df = base.merge(wide, on=["mbr_county_cd", "month"], how="left")
    prev_cols = sorted(c for c in df.columns if c.startswith("prev_hcc_"))
    df[prev_cols] = df[prev_cols].fillna(0)
    df = df.sort_values(["month", "mbr_county_cd", "specialty_ctg_cd"]).reset_index(drop=True)
    return df, prev_cols


def design_matrices(df, prev_cols):
    num = df[NUM_FEATURES + prev_cols].astype(float).fillna(0)
    dummies = pd.get_dummies(df[CAT_FEATURES].astype(str), dtype=float)
    x_lin = pd.concat([num, dummies], axis=1)
    x_xgb = num.copy()
    for c in CAT_FEATURES:
        x_xgb[c] = df[c].astype("category").cat.codes
    return x_lin, x_xgb


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
    x_lin, x_xgb = design_matrices(df, prev_cols)
    months = df["month"]

    county_2024 = (df[df["year"] == 2024].groupby("mbr_county_cd")["visits"].sum())
    df["county_band"] = df["mbr_county_cd"].map(county_2024).fillna(0).map(size_band)

    tasks = {
        "next_1m": {"target": "target_next_1m",
                    "train": months <= A_TRAIN_END,
                    "val": (months >= A_VAL_START) & (months <= A_VAL_END)},
        "next_12m": {"target": "target_next_12m",
                     "train": months <= B_TRAIN_END,
                     "val": months == B_VAL_MONTH},
    }

    metrics, importances = [], []
    preds = df[["mbr_county_cd", "specialty_ctg_cd", "month"]].copy()
    preds["actual_next_1m"] = df["target_next_1m"]
    preds["actual_next_12m"] = df["target_next_12m"]

    for task, spec in tasks.items():
        y = df[spec["target"]].astype(float)
        fit_mask = spec["train"] & y.notna()

        lin = LinearRegression()
        lin.fit(x_lin[fit_mask], y[fit_mask])
        pred_lin = pd.Series(lin.predict(x_lin), index=df.index)

        xgb = fit_xgb(x_xgb[fit_mask], y[fit_mask], months[fit_mask])
        pred_xgb = pd.Series(xgb.predict(x_xgb), index=df.index)

        preds[f"pred_{task}_linear"] = pred_lin
        preds[f"pred_{task}_xgb"] = pred_xgb

        val = spec["val"] & y.notna()
        metrics.append(eval_rows(task, "linear", y[val], pred_lin[val], df.loc[val, "county_band"]))
        metrics.append(eval_rows(task, "xgb", y[val], pred_xgb[val], df.loc[val, "county_band"]))

        gain = xgb.get_booster().get_score(importance_type="gain")
        imp = (pd.DataFrame({"feature": list(gain.keys()), "gain": list(gain.values())})
               .sort_values("gain", ascending=False).head(30))
        imp.insert(0, "target", task)
        importances.append(imp)

    metrics_df = pd.DataFrame(metrics)
    print(metrics_df.to_string(index=False))
    metrics_df.to_csv(METRICS_CSV, index=False)
    pd.concat(importances, ignore_index=True).to_csv(IMPORTANCE_CSV, index=False)

    preds["split_label"] = np.where(
        preds["month"] == FUTURE_MONTH, "future",
        np.where((preds["month"] >= A_VAL_START) & (preds["month"] <= A_VAL_END),
                 "validation", "train"))
    preds["month"] = preds["month"].dt.date

    from google.cloud import bigquery
    job_config = bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE")
    cfg.client().load_table_from_dataframe(preds, OUT_TBL, job_config=job_config).result()
    print(f"wrote {len(preds)} rows -> {OUT_TBL}")
    print(f"wrote {METRICS_CSV}")
    print(f"wrote {IMPORTANCE_CSV}")


if __name__ == "__main__":
    main()
