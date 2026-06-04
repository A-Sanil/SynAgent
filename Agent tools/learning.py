import sqlite3
import numpy as np
from scipy import stats
from rdkit import Chem
from rdkit.Chem import DataStructs
from rdkit.Chem import rdFingerprintGenerator
from pydantic_ai import Agent
from dotenv import load_dotenv

load_dotenv()

DB_PATH = r"D:\SynAgent\db\patent_pipeline.db"

SYSTEM_PROMPT = """
You are a reaction synthesis assistant. Your goal is to help predict reaction yields
by finding chemically similar reactions from a patent database.

When a user provides a reaction (reactants + product), you will:
1. Ask for their preferred similarity weights (default: 60% product, 40% reactant)
2. Ask how many neighbor reactions to retrieve (default: 5)
3. Ask for a minimum similarity threshold (default: 0.3)
4. Ask whether to include reactions with no yield data (default: no)

Then call find_similar_reactions with those parameters and interpret the results,
including the predicted yield and confidence interval.
""".strip()

agent = Agent(
    model="google-gla:gemini-2.0-flash",
    system_prompt=SYSTEM_PROMPT,
    output_type=str,
)


_morgan_gen = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=2048)


def _smiles_to_fp(smiles: str):
    """Compute Morgan ECFP4 fingerprint from SMILES — used only for the query molecule."""
    try:
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return None
        return _morgan_gen.GetFingerprint(mol)
    except Exception:
        return None


def _blob_to_fp(blob: bytes):
    """Deserialize a stored fingerprint blob back into an RDKit bit vector."""
    try:
        return DataStructs.CreateFromBinaryText(blob)
    except Exception:
        return None


@agent.tool_plain
def find_similar_reactions(
    product_smiles: str,
    reactant_smiles: str,
    k: int = 5,
    product_weight: float = 0.6,
    reactant_weight: float = 0.4,
    min_similarity: float = 0.3,
    include_no_yield: bool = False,
) -> str:
    """
    Find the k most similar reactions in the database to a query reaction from SynLlama.

    Loads pre-calculated Morgan ECFP4 fingerprint blobs from the database and computes
    Tanimoto similarity against the query. Combines product and reactant similarity scores
    with user-specified weights, returning top-k matches with weighted yield prediction
    and 95% confidence interval.

    Run precalc_fingerprints.py first to populate the product_fp and reactant_fp columns.

    Args:
        product_smiles: SMILES of the target product molecule (from SynLlama output)
        reactant_smiles: SMILES of the main reactant/substrate (from SynLlama output)
        k: number of nearest neighbor reactions to return
        product_weight: weight applied to product similarity score (0.0 to 1.0)
        reactant_weight: weight applied to reactant similarity score (0.0 to 1.0)
        min_similarity: minimum combined similarity threshold to include a result
        include_no_yield: if True, include reactions without yield data in results
    """
    query_prod_fp = _smiles_to_fp(product_smiles)
    query_react_fp = _smiles_to_fp(reactant_smiles)

    if query_prod_fp is None:
        return "Error: could not parse product SMILES — check the SMILES string."

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    if include_no_yield:
        cur.execute(
            "SELECT reaction_id, patent_id, product_fp, reactant_fp, yield_percent "
            "FROM reactions WHERE product_fp IS NOT NULL"
        )
    else:
        cur.execute(
            "SELECT reaction_id, patent_id, product_fp, reactant_fp, yield_percent "
            "FROM reactions WHERE product_fp IS NOT NULL AND yield_percent BETWEEN 0 AND 100"
        )

    rows = cur.fetchall()
    conn.close()

    if not rows:
        return "No fingerprint data found. Run precalc_fingerprints.py first."

    results = []
    for reaction_id, patent_id, prod_blob, react_blob, yield_pct in rows:
        prod_fp = _blob_to_fp(prod_blob)
        if prod_fp is None:
            continue

        prod_sim = DataStructs.TanimotoSimilarity(query_prod_fp, prod_fp)

        react_sim = 0.0
        if query_react_fp is not None and react_blob:
            react_fp = _blob_to_fp(react_blob)
            if react_fp is not None:
                react_sim = DataStructs.TanimotoSimilarity(query_react_fp, react_fp)

        combined = product_weight * prod_sim + reactant_weight * react_sim

        if combined >= min_similarity:
            results.append({
                "reaction_id": reaction_id,
                "patent_id": patent_id,
                "product_sim": prod_sim,
                "reactant_sim": react_sim,
                "combined_sim": combined,
                "yield_percent": yield_pct,
            })

    results.sort(key=lambda x: x["combined_sim"], reverse=True)
    top_k = results[:k]

    if not top_k:
        return f"No reactions found with combined similarity >= {min_similarity}."

    # Weighted yield prediction + 95% CI
    yield_hits = [r for r in top_k if r["yield_percent"] is not None]
    predicted_yield = None
    ci_low = ci_high = None

    if yield_hits:
        sims = np.array([r["combined_sim"] for r in yield_hits])
        yields = np.array([r["yield_percent"] for r in yield_hits])
        predicted_yield = float(np.average(yields, weights=sims))

        if len(yield_hits) >= 2:
            weighted_var = float(np.average((yields - predicted_yield) ** 2, weights=sims))
            se = np.sqrt(weighted_var) / np.sqrt(len(yield_hits))
            t_crit = stats.t.ppf(0.975, df=len(yield_hits) - 1)
            ci_low = max(0.0, predicted_yield - t_crit * se)
            ci_high = min(100.0, predicted_yield + t_crit * se)

    # Format output
    lines = [
        f"Top {len(top_k)} similar reactions",
        f"Weights — product: {product_weight}, reactant: {reactant_weight} | min similarity: {min_similarity}",
        "",
    ]

    for i, r in enumerate(top_k, 1):
        yield_str = f"{r['yield_percent']:.1f}%" if r["yield_percent"] is not None else "N/A"
        lines.append(
            f"{i}. {r['reaction_id']} | Patent: {r['patent_id']}\n"
            f"   Product sim: {r['product_sim']:.3f} | Reactant sim: {r['reactant_sim']:.3f} "
            f"| Combined: {r['combined_sim']:.3f} | Yield: {yield_str}"
        )

    lines.append("")
    if predicted_yield is not None:
        lines.append(f"Predicted yield: {predicted_yield:.1f}%")
        if ci_low is not None:
            lines.append(f"95% CI: [{ci_low:.1f}%, {ci_high:.1f}%]")
        else:
            lines.append("95% CI: insufficient data (only 1 match with yield)")
    else:
        lines.append("Predicted yield: N/A — no matches with yield data in top results")

    return "\n".join(lines)


app = agent.to_web(
    models=[
        "google-gla:gemini-2.0-flash",
        "anthropic:claude-sonnet-4-6",
        "openai:gpt-4o",
    ]
)
