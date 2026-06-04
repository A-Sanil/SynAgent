"""
DRFP + XGBoost yield prediction baseline — Phase 1 prototype.

Loads reactions from the SynAgent USB database (ord_full.db + patent_pipeline.db),
reuses pre-computed DRFP fingerprints when available (reaction_fp column from
precalc_fingerprints.py), falls back to live encoding for any gaps.

Usage:
    python yield_prediction/drfp_xgb_baseline.py

    # Specific DB paths:
    python yield_prediction/drfp_xgb_baseline.py --db D:/SynAgent/db/ord_full.db

    # Include patent DB:
    python yield_prediction/drfp_xgb_baseline.py --extra_db D:/SynAgent/db/patent_pipeline.db

    # Skip Optuna hyperparameter search (faster):
    python yield_prediction/drfp_xgb_baseline.py --no_optuna

Outputs:
    - Console: MAE / RMSE / R² on held-out test set
    - D:/SynAgent/models/xgb_drfp_baseline.json  (trained model)
    - D:/SynAgent/models/xgb_drfp_results.json   (metrics + params)
"""

import argparse
import json
import os
import sqlite3
import time

import numpy as np
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import train_test_split
from xgboost import XGBRegressor

# ── Defaults ──────────────────────────────────────────────────────────────────
DEFAULT_DB    = r"D:\SynAgent\db\ord_full.db"
EXTRA_DB      = r"D:\SynAgent\db\patent_pipeline.db"
MODEL_DIR     = r"D:\SynAgent\models"
FP_SIZE       = 2048      # must match precalc_fingerprints.py
MIN_YIELD     = 5.0
MAX_YIELD     = 100.0
RANDOM_SEED   = 42


# ── Fingerprint loading ────────────────────────────────────────────────────────

def _blob_to_fp(blob: bytes) -> np.ndarray:
    """Unpack a stored reaction_fp blob back to a binary numpy array."""
    return np.unpackbits(np.frombuffer(blob, dtype=np.uint8))[:FP_SIZE].astype(np.float32)


def load_from_db(db_path: str) -> tuple[np.ndarray, np.ndarray]:
    """
    Load fingerprints + yields from one SQLite database.

    Priority:
      1. Pre-computed reaction_fp blobs (fast — no RDKit/DRFP needed).
      2. Live DrfpEncoder encoding for rows where reaction_fp IS NULL.

    Returns (X, y) arrays, or (None, None) if the file doesn't exist.
    """
    if not os.path.exists(db_path):
        print(f"  [SKIP] not found: {db_path}")
        return None, None

    conn = sqlite3.connect(db_path)
    cur  = conn.cursor()

    # Check if reaction_fp column exists
    cols = {row[1] for row in cur.execute("PRAGMA table_info(reactions)")}
    has_fp_col = "reaction_fp" in cols

    if has_fp_col:
        # Load pre-computed blobs (fast path)
        cur.execute(
            "SELECT reaction_fp, yield_percent FROM reactions "
            "WHERE reaction_fp IS NOT NULL "
            f"  AND yield_percent BETWEEN {MIN_YIELD} AND {MAX_YIELD}"
        )
        pre_rows = cur.fetchall()
        print(f"  {db_path}: {len(pre_rows):,} pre-computed fingerprints")

        # Also check if any rows need live encoding
        cur.execute(
            "SELECT reaction_smarts, yield_percent FROM reactions "
            "WHERE reaction_fp IS NULL "
            "  AND reaction_smarts IS NOT NULL "
            "  AND reaction_smarts LIKE '%>>%' "
            f"  AND yield_percent BETWEEN {MIN_YIELD} AND {MAX_YIELD}"
        )
        live_rows = cur.fetchall()
    else:
        pre_rows  = []
        cur.execute(
            "SELECT reaction_smarts, yield_percent FROM reactions "
            "WHERE reaction_smarts IS NOT NULL "
            "  AND reaction_smarts LIKE '%>>%' "
            f"  AND yield_percent BETWEEN {MIN_YIELD} AND {MAX_YIELD}"
        )
        live_rows = cur.fetchall()

    conn.close()

    fps_list:   list[np.ndarray] = []
    yields_list: list[float]     = []

    # Pre-computed blobs
    for blob, y in pre_rows:
        fp = _blob_to_fp(blob)
        if fp.shape[0] == FP_SIZE:
            fps_list.append(fp)
            yields_list.append(float(y))

    # Live encoding fallback
    if live_rows:
        print(f"  {len(live_rows):,} reactions need live DRFP encoding — importing drfp...")
        from drfp import DrfpEncoder
        smarts = [r[0] for r in live_rows]
        yields = [float(r[1]) for r in live_rows]
        CHUNK  = 1000
        for i in range(0, len(smarts), CHUNK):
            batch = smarts[i:i + CHUNK]
            try:
                encoded = DrfpEncoder.encode(batch, n_folded_length=FP_SIZE)
                fps_list.extend(encoded.astype(np.float32))
                yields_list.extend(yields[i:i + CHUNK])
            except Exception as e:
                print(f"    Chunk {i}–{i+CHUNK} failed ({e}), skipping")
            if (i // CHUNK) % 10 == 0:
                print(f"    {min(i+CHUNK, len(smarts)):,}/{len(smarts):,} encoded")

    if not fps_list:
        print(f"  [WARN] No usable reactions in {db_path}")
        return None, None

    X = np.stack(fps_list, axis=0)
    y = np.array(yields_list, dtype=np.float32)
    return X, y


# ── XGBoost training ──────────────────────────────────────────────────────────

def train_xgb(X_train, y_train, X_val, y_val,
              use_optuna: bool, n_jobs: int) -> XGBRegressor:
    """Train XGBRegressor, optionally with Optuna hyperparameter search."""

    if use_optuna:
        try:
            import optuna
            optuna.logging.set_verbosity(optuna.logging.WARNING)
        except ImportError:
            print("  Optuna not installed — falling back to default hyperparams.")
            use_optuna = False

    if use_optuna:
        def objective(trial):
            params = {
                "n_estimators":      trial.suggest_int("n_estimators", 200, 1000),
                "max_depth":         trial.suggest_int("max_depth", 3, 10),
                "learning_rate":     trial.suggest_float("learning_rate", 0.01, 0.3, log=True),
                "subsample":         trial.suggest_float("subsample", 0.6, 1.0),
                "colsample_bytree":  trial.suggest_float("colsample_bytree", 0.5, 1.0),
                "min_child_weight":  trial.suggest_int("min_child_weight", 1, 10),
                "reg_lambda":        trial.suggest_float("reg_lambda", 1e-3, 10.0, log=True),
                "random_state":      RANDOM_SEED,
                "n_jobs":            n_jobs,
                "tree_method":       "hist",
            }
            m = XGBRegressor(**params)
            m.fit(X_train, y_train,
                  eval_set=[(X_val, y_val)],
                  verbose=False)
            preds = m.predict(X_val).clip(0, 100)
            return mean_absolute_error(y_val, preds)

        study = optuna.create_study(direction="minimize",
                                    sampler=optuna.samplers.TPESampler(seed=RANDOM_SEED))
        study.optimize(objective, n_trials=50, show_progress_bar=True)
        best = study.best_params
        print(f"  Best Optuna params: {best}")
        model = XGBRegressor(**best, random_state=RANDOM_SEED, n_jobs=n_jobs,
                             tree_method="hist")
    else:
        model = XGBRegressor(
            n_estimators=500,
            max_depth=6,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=RANDOM_SEED,
            n_jobs=n_jobs,
            tree_method="hist",
        )

    model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        verbose=50,
    )
    return model


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="DRFP + XGBoost yield baseline")
    parser.add_argument("--db",        default=DEFAULT_DB,
                        help="Primary SQLite DB (ord_full.db)")
    parser.add_argument("--extra_db",  default=None,
                        help="Optional second DB (patent_pipeline.db)")
    parser.add_argument("--model_dir", default=MODEL_DIR,
                        help="Directory to save model + results")
    parser.add_argument("--no_optuna", action="store_true",
                        help="Skip Optuna, use default XGBoost hyperparams")
    parser.add_argument("--n_jobs",    type=int, default=-1,
                        help="XGBoost CPU threads (-1 = all)")
    args = parser.parse_args()

    print("=" * 60)
    print("  DRFP + XGBoost Yield Prediction — Phase 1 Baseline")
    print("=" * 60)

    # ── Load data ─────────────────────────────────────────────────────────────
    print(f"\nLoading primary DB: {args.db}")
    X, y = load_from_db(args.db)

    extra_db = args.extra_db or (EXTRA_DB if os.path.exists(EXTRA_DB) else None)
    if extra_db:
        print(f"\nLoading extra DB: {extra_db}")
        X2, y2 = load_from_db(extra_db)
        if X2 is not None:
            X = np.concatenate([X, X2], axis=0) if X is not None else X2
            y = np.concatenate([y, y2], axis=0) if y is not None else y2

    if X is None or len(X) == 0:
        print("\nNo data loaded — check DB paths.")
        return

    print(f"\nTotal reactions: {len(X):,}  |  FP shape: {X.shape}")
    print(f"Yield range: {y.min():.1f}% – {y.max():.1f}%  |  mean: {y.mean():.1f}%")

    # ── Split ─────────────────────────────────────────────────────────────────
    X_tmp,  X_test, y_tmp,  y_test  = train_test_split(
        X, y, test_size=0.10, random_state=RANDOM_SEED)
    X_train, X_val, y_train, y_val  = train_test_split(
        X_tmp, y_tmp, test_size=0.111, random_state=RANDOM_SEED)  # ~10% of total

    print(f"\nTrain: {len(X_train):,}  Val: {len(X_val):,}  Test: {len(X_test):,}")

    # ── Train ─────────────────────────────────────────────────────────────────
    print(f"\nTraining XGBoost  (Optuna={'off' if args.no_optuna else 'on, 50 trials'}) ...")
    t0    = time.time()
    model = train_xgb(X_train, y_train, X_val, y_val,
                      use_optuna=not args.no_optuna,
                      n_jobs=args.n_jobs)
    elapsed = time.time() - t0

    # ── Evaluate ──────────────────────────────────────────────────────────────
    preds = model.predict(X_test).clip(0, 100)
    mae   = mean_absolute_error(y_test, preds)
    rmse  = mean_squared_error(y_test, preds) ** 0.5
    r2    = r2_score(y_test, preds)

    print(f"\n{'='*60}")
    print(f"  FINAL TEST RESULTS  ({len(X_test):,} held-out reactions)")
    print(f"{'='*60}")
    print(f"  MAE  : {mae:.2f}%")
    print(f"  RMSE : {rmse:.2f}%")
    print(f"  R²   : {r2:.4f}")
    print(f"  Time : {elapsed:.0f}s")

    # ── Save ──────────────────────────────────────────────────────────────────
    os.makedirs(args.model_dir, exist_ok=True)
    model_path   = os.path.join(args.model_dir, "xgb_drfp_baseline.json")
    results_path = os.path.join(args.model_dir, "xgb_drfp_results.json")

    model.save_model(model_path)
    print(f"\n  Model saved  : {model_path}")

    results = {
        "n_train": int(len(X_train)),
        "n_val":   int(len(X_val)),
        "n_test":  int(len(X_test)),
        "mae":     round(float(mae),  4),
        "rmse":    round(float(rmse), 4),
        "r2":      round(float(r2),   4),
        "elapsed_s": round(elapsed, 1),
        "fp_size": FP_SIZE,
        "db":      args.db,
    }
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"  Results saved: {results_path}")


if __name__ == "__main__":
    main()
