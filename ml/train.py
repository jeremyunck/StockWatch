"""
train.py — Build features from snapshots, train a buy-signal model,
validate it walk-forward, and save the model artifact.

Model: gradient-boosted trees (XGBoost). For tabular financial
features this beats neural nets at this data scale, trains in
seconds on a Pi, and exposes feature importances.

"Model weights" for a GBT = the serialized tree ensemble, saved as
a versioned .json file (+ a sidecar .meta.json with the feature
list, label rule, and validation metrics). Save a new version each
time you retrain on grown data; never overwrite.

Label rule (v1): label = 1 if intraday high hit entry*1.05 within
5 trading days, else 0.

Usage:
    python train.py --db state.db --out-dir models
"""

import argparse
import json
import sqlite3
from datetime import datetime, timezone

import numpy as np
import pandas as pd

# Features are computed from raw snapshot columns so the recipe can
# change without re-pulling data. All are scale-free (% distances /
# ratios) so the model transfers across tickers and price levels.
FEATURE_NAMES = [
    "rsi14",
    "macd_hist",
    "px_vs_sma50",     # (entry/sma50 - 1)
    "px_vs_sma200",
    "px_vs_ema20",
    "bb_pctb",         # (entry - bb_lower)/(bb_upper - bb_lower)
    "atr_pct",         # atr14 / entry
    "vol_ratio",       # entry-day volume proxy vs 20d avg -> here vol_avg used as ratio base
    "ret_5d",
    "ret_20d",
    "dist_52w_high",   # (entry/hi_52w - 1)
    "dist_52w_low",    # (entry/lo_52w - 1)
    "news_count_24h",
]


def load_labeled(conn):
    df = pd.read_sql_query(
        """
        SELECT s.*, l.label
        FROM snapshots s
        JOIN labels l ON l.snapshot_id = s.snapshot_id
        WHERE l.label IS NOT NULL
        ORDER BY s.captured_at ASC
        """,
        conn,
    )
    return df


def build_features(df):
    f = pd.DataFrame(index=df.index)
    p = df["entry_price"]
    f["rsi14"] = df["rsi14"]
    f["macd_hist"] = df["macd_hist"]
    f["px_vs_sma50"] = p / df["sma50"] - 1.0
    f["px_vs_sma200"] = p / df["sma200"] - 1.0
    f["px_vs_ema20"] = p / df["ema20"] - 1.0
    width = (df["bb_upper"] - df["bb_lower"]).replace(0, np.nan)
    f["bb_pctb"] = (p - df["bb_lower"]) / width
    f["atr_pct"] = df["atr14"] / p
    f["vol_ratio"] = df["vol_avg20"] / df["vol_avg20"].replace(0, np.nan)  # placeholder=1
    f["ret_5d"] = df["ret_5d"]
    f["ret_20d"] = df["ret_20d"]
    f["dist_52w_high"] = p / df["hi_52w"] - 1.0
    f["dist_52w_low"] = p / df["lo_52w"] - 1.0
    f["news_count_24h"] = df["news_count_24h"].fillna(0)
    return f[FEATURE_NAMES]


def walk_forward_splits(n, k=4):
    """
    Expanding-window walk-forward: train on [0:cut], test on the next
    chunk, roll forward. NEVER random-split time series — that leaks
    the future into the past.
    """
    fold = n // (k + 1)
    if fold < 1:
        return
    for i in range(1, k + 1):
        train_end = fold * i
        test_end = fold * (i + 1) if i < k else n
        if train_end < 1 or test_end <= train_end:
            continue
        yield np.arange(0, train_end), np.arange(train_end, test_end)


def evaluate(model, X, y):
    from sklearn.metrics import precision_score, recall_score, roc_auc_score
    proba = model.predict_proba(X)[:, 1]
    pred = (proba >= 0.5).astype(int)
    out = {
        "n": int(len(y)),
        "buy_rate_actual": float(y.mean()),
        "precision_buy": float(precision_score(y, pred, zero_division=0)),
        "recall_buy": float(recall_score(y, pred, zero_division=0)),
    }
    if len(np.unique(y)) > 1:
        out["auc"] = float(roc_auc_score(y, proba))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--out-dir", default="models")
    ap.add_argument("--min-rows", type=int, default=200,
                    help="refuse to train on too little data")
    args = ap.parse_args()

    import os
    os.makedirs(args.out_dir, exist_ok=True)

    conn = sqlite3.connect(args.db)
    df = load_labeled(conn)
    conn.close()

    if len(df) < args.min_rows:
        print(f"Only {len(df)} labeled rows; need >= {args.min_rows}. "
              f"Keep collecting — training aborted.")
        return

    X = build_features(df).replace([np.inf, -np.inf], np.nan)
    y = df["label"].astype(int).values

    import xgboost as xgb

    # Class imbalance: most weeks don't pop 5%, so positives are rare.
    pos = max(int(y.sum()), 1)
    neg = max(int((1 - y).sum()), 1)
    scale_pos_weight = neg / pos

    base_params = dict(
        n_estimators=300,
        max_depth=4,
        learning_rate=0.03,
        subsample=0.8,
        colsample_bytree=0.8,
        scale_pos_weight=scale_pos_weight,
        eval_metric="logloss",
        n_jobs=2,            # Pi-friendly
    )

    # Walk-forward validation
    fold_metrics = []
    for tr, te in walk_forward_splits(len(df), k=4):
        m = xgb.XGBClassifier(**base_params)
        m.fit(X.iloc[tr], y[tr])
        fold_metrics.append(evaluate(m, X.iloc[te], y[te]))

    # Baseline to beat: "always predict buy"
    baseline_precision = float(y.mean())  # precision of always-buy == base rate

    # Final model trained on ALL labeled data for deployment
    final = xgb.XGBClassifier(**base_params)
    final.fit(X, y)

    importances = dict(sorted(
        zip(FEATURE_NAMES, (float(v) for v in final.feature_importances_)),
        key=lambda kv: kv[1], reverse=True,
    ))

    version = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    model_path = os.path.join(args.out_dir, f"buysignal_{version}.json")
    meta_path = os.path.join(args.out_dir, f"buysignal_{version}.meta.json")
    final.get_booster().save_model(model_path)

    meta = {
        "version": version,
        "label_rule": "high >= entry*1.05 within 5 trading days",
        "features": FEATURE_NAMES,
        "n_train_rows": int(len(df)),
        "class_balance": {"buy": int(y.sum()), "no_buy": int(neg)},
        "scale_pos_weight": scale_pos_weight,
        "walk_forward_folds": fold_metrics,
        "baseline_always_buy_precision": baseline_precision,
        "feature_importances": importances,
        "xgb_params": base_params,
    }
    with open(meta_path, "w") as fh:
        json.dump(meta, fh, indent=2)

    avg_prec = np.mean([m["precision_buy"] for m in fold_metrics]) if fold_metrics else 0
    print(f"Saved model: {model_path}")
    print(f"Saved meta:  {meta_path}")
    print(f"Walk-forward avg precision(buy): {avg_prec:.3f}  vs  "
          f"always-buy baseline: {baseline_precision:.3f}")
    if avg_prec <= baseline_precision:
        print("WARNING: model does not beat the naive always-buy baseline. "
              "Do not trust its signals yet.")


if __name__ == "__main__":
    main()
