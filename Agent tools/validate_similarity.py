"""
Holdout validation using DRFP (Differential Reaction Fingerprint) for yield prediction.

Loads reactions with yield + DRFP blobs, splits 80/20, predicts yield for each
test reaction via similarity-weighted k-NN, then reports RMSE, MAE, R², CI coverage.

Usage:
    python "Agent tools/validate_similarity.py"

Requires precalc_fingerprints.py to have been run first (reaction_fp column).
"""

import sqlite3
import random
import math
import numpy as np
from scipy import stats

DB_PATH      = r"D:\SynAgent\db\patent_pipeline.db"
TEST_FRAC    = 0.20
RANDOM_SEED  = 42
K            = 5
MIN_SIM      = 0.5    # only evaluate where a close analog exists
MIN_YIELD    = 20.0   # ignore low-yield reactions
FP_SIZE      = 2048


def blob_to_drfp(blob: bytes) -> np.ndarray | None:
    try:
        return np.unpackbits(np.frombuffer(blob, dtype=np.uint8))[:FP_SIZE].astype(np.uint8)
    except Exception:
        return None


def tanimoto(a: np.ndarray, b: np.ndarray) -> float:
    intersection = np.sum(a & b)
    union        = np.sum(a | b)
    return float(intersection / union) if union > 0 else 0.0


def load_reactions(conn) -> list[dict]:
    cur = conn.cursor()
    cur.execute(
        f"SELECT reaction_id, reaction_fp, yield_percent FROM reactions "
        f"WHERE reaction_fp IS NOT NULL AND yield_percent BETWEEN {MIN_YIELD} AND 100"
    )
    out = []
    for rid, blob, yld in cur.fetchall():
        fp = blob_to_drfp(blob)
        if fp is not None:
            out.append({"id": rid, "fp": fp, "yield": float(yld)})
    return out


def predict(query: dict, pool: list[dict]) -> dict | None:
    scores = sorted(
        [(tanimoto(query["fp"], r["fp"]), r["yield"]) for r in pool],
        reverse=True,
    )
    top = scores[:K]
    if not top:
        return None

    sims   = np.array([s for s, _ in top])
    yields = np.array([y for _, y in top])
    # Fall back to simple mean if all similarities are zero
    w_mean = float(np.average(yields, weights=sims)) if sims.sum() > 0 else float(yields.mean())

    ci_low = ci_high = None
    if len(top) >= 2 and sims.sum() > 0:
        w_var  = float(np.average((yields - w_mean) ** 2, weights=sims))
        se     = math.sqrt(w_var) / math.sqrt(len(top))
        t_crit = stats.t.ppf(0.975, df=len(top) - 1)
        ci_low  = max(0.0,   w_mean - t_crit * se)
        ci_high = min(100.0, w_mean + t_crit * se)

    return {
        "predicted": w_mean,
        "ci_low":    ci_low,
        "ci_high":   ci_high,
        "top_sim":   float(sims[0]),
        "mean_sim":  float(sims.mean()),
    }


def rmse(errors):          return math.sqrt(sum(e**2 for e in errors) / len(errors))
def mae(errors):           return sum(abs(e) for e in errors) / len(errors)
def r2(actual, predicted):
    mu     = sum(actual) / len(actual)
    ss_tot = sum((a - mu)**2 for a in actual)
    ss_res = sum((a - p)**2 for a, p in zip(actual, predicted))
    return 1 - ss_res / ss_tot if ss_tot > 0 else 0.0


def main():
    print(f"Loading from {DB_PATH} ...")
    conn      = sqlite3.connect(DB_PATH)
    reactions = load_reactions(conn)
    conn.close()
    print(f"Loaded {len(reactions)} reactions (yield {MIN_YIELD}-100%, with DRFP)")

    random.seed(RANDOM_SEED)
    random.shuffle(reactions)
    split     = int(len(reactions) * (1 - TEST_FRAC))
    train_set = reactions[:split]
    test_set  = reactions[split:]
    print(f"Train: {len(train_set)}   Test: {len(test_set)}")
    print(f"Settings: k={K}  min_similarity={MIN_SIM}\n")

    results, skipped = [], 0
    for i, query in enumerate(test_set, 1):
        pred = predict(query, train_set)
        if pred is None or pred["top_sim"] < MIN_SIM:
            skipped += 1
            continue
        results.append({
            "id":        query["id"],
            "actual":    query["yield"],
            "predicted": pred["predicted"],
            "error":     pred["predicted"] - query["yield"],
            "top_sim":   pred["top_sim"],
            "ci_low":    pred["ci_low"],
            "ci_high":   pred["ci_high"],
        })
        if i % 100 == 0:
            print(f"  {i}/{len(test_set)} done...")

    print(f"  Skipped {skipped} with no close analog (top_sim < {MIN_SIM})")

    if not results:
        print("No predictions — check reaction_fp column.")
        return

    errs  = [r["error"]     for r in results]
    acts  = [r["actual"]    for r in results]
    preds = [r["predicted"] for r in results]
    ci_ws = [r["ci_high"] - r["ci_low"] for r in results if r["ci_high"] is not None]

    print(f"\n{'='*58}")
    print(f"  DRFP HOLDOUT VALIDATION — {len(results)} test reactions")
    print(f"{'='*58}")
    print(f"  RMSE:           {rmse(errs):.1f}%")
    print(f"  MAE:            {mae(errs):.1f}%")
    print(f"  R²:             {r2(acts, preds):.3f}")
    if ci_ws:
        print(f"  Avg CI width:   ±{sum(ci_ws)/len(ci_ws)/2:.1f}%")

    # Similarity tier breakdown
    tiers = [
        ("High  (>0.7)",  [r for r in results if r["top_sim"] >  0.7]),
        ("Mid (0.5–0.7)", [r for r in results if 0.5 <= r["top_sim"] <= 0.7]),
    ]
    print("\nBreakdown by top-match similarity:")
    for label, subset in tiers:
        if not subset:
            print(f"  {label}: no data")
            continue
        e = [r["error"] for r in subset]
        a = [r["actual"] for r in subset]
        p = [r["predicted"] for r in subset]
        w = [r["ci_high"] - r["ci_low"] for r in subset if r["ci_high"] is not None]
        ci_str = f"  CI width: ±{sum(w)/len(w)/2:.1f}%" if w else ""
        print(f"  {label} (n={len(subset)}): RMSE {rmse(e):.1f}%  MAE {mae(e):.1f}%  R² {r2(a,p):.3f}{ci_str}")

    # CI coverage
    covered   = [r for r in results if r["ci_low"] is not None and r["ci_low"] <= r["actual"] <= r["ci_high"]]
    ci_denom  = [r for r in results if r["ci_low"] is not None]
    if ci_denom:
        print(f"\nCI coverage: {len(covered)/len(ci_denom)*100:.1f}%  (target ~95%)")

    # Worst misses
    worst = sorted(results, key=lambda r: abs(r["error"]), reverse=True)[:5]
    print("\nTop 5 worst predictions:")
    for r in worst:
        print(f"  actual={r['actual']:.1f}%  pred={r['predicted']:.1f}%  "
              f"err={r['error']:+.1f}%  top_sim={r['top_sim']:.3f}  [{r['id'][:28]}]")

    print("\nDone.")


if __name__ == "__main__":
    main()
