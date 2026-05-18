"""ORD / Reaction Database lookup agent.

Queries the SQLite reaction database built by the patent ingestion pipeline.
Searches by reaction SMARTS similarity, reactant/product SMILES, or keyword.
Falls back gracefully if the DB is not available.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Optional

from pydantic_ai import Agent
from pydantic_ai.models.google import GoogleModel

# ── DB path resolution ─────────────────────────────────────────────────────
def _find_db() -> Optional[Path]:
    """Find the reaction database — USB first, then local data dir."""
    candidates = [
        # Explicit env var (D:\SynAgent or ./data)
        os.environ.get("PATENT_DATA_DIR"),
        # Common USB paths
        r"D:\SynAgent",
        r"E:\SynAgent",
        r"F:\SynAgent",
        # Local dev fallback
        Path(__file__).parent.parent.parent.parent.parent
        / "patent_ingestion_pipeline" / "data",
    ]
    for c in candidates:
        if c is None:
            continue
        db = Path(c) / "db" / "patent_pipeline.db"
        if db.exists():
            return db
    return None


def _query_reactions(
    reaction_smarts: str = "",
    reactant_smiles: Optional[list[str]] = None,
    product_smiles: str = "",
    keyword: str = "",
    limit: int = 5,
) -> list[dict]:
    """Query the reaction database and return matching rows."""
    db_path = _find_db()
    if db_path is None:
        return []

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    results = []

    try:
        # Build a keyword string from all available inputs for FTS search
        terms = " ".join(filter(None, [
            reaction_smarts[:80] if reaction_smarts else "",
            product_smiles[:80] if product_smiles else "",
            keyword,
            *((reactant_smiles or [])[:2]),
        ])).strip()

        if terms:
            # Try FTS5 search first (fast keyword match)
            try:
                rows = conn.execute(
                    """
                    SELECT r.*
                    FROM reactions r
                    JOIN reactions_fts fts ON fts.reaction_id = r.reaction_id
                    WHERE reactions_fts MATCH ?
                    ORDER BY r.confidence DESC
                    LIMIT ?
                    """,
                    (terms, limit),
                ).fetchall()
                results = [dict(row) for row in rows]
            except sqlite3.OperationalError:
                pass

        # Fallback: substring search on product_smiles or reaction_smarts
        if not results and (product_smiles or reaction_smarts):
            search_val = f"%{(product_smiles or reaction_smarts)[:40]}%"
            rows = conn.execute(
                """
                SELECT * FROM reactions
                WHERE product_smiles LIKE ?
                   OR reaction_smarts LIKE ?
                ORDER BY confidence DESC
                LIMIT ?
                """,
                (search_val, search_val, limit),
            ).fetchall()
            results = [dict(row) for row in rows]

        # Last resort: return highest-confidence reactions for the query
        if not results:
            rows = conn.execute(
                "SELECT * FROM reactions ORDER BY confidence DESC LIMIT ?",
                (limit,),
            ).fetchall()
            results = [dict(row) for row in rows]

    finally:
        conn.close()

    return results


def _format_results(rows: list[dict]) -> str:
    """Format DB rows into a readable precedent summary."""
    if not rows:
        return "No matching reactions found in the database."

    lines = [f"Found {len(rows)} reaction(s) in the database:\n"]
    for i, row in enumerate(rows, 1):
        source = row.get("source", "unknown")
        conf = row.get("confidence") or 0.0
        source_label = "ORD (expert-curated)" if source == "ord" else f"LLM-extracted (conf={conf:.2f})"

        lines.append(f"  [{i}] Source: {source_label}")
        if row.get("reaction_smarts"):
            lines.append(f"      SMARTS: {row['reaction_smarts'][:120]}")
        if row.get("product_smiles"):
            lines.append(f"      Product: {row['product_smiles'][:80]}")
        if row.get("yield_percent") is not None:
            lines.append(f"      Yield: {row['yield_percent']:.1f}%")
        if row.get("temperature_celsius") is not None:
            lines.append(f"      Temperature: {row['temperature_celsius']:.0f} C")
        if row.get("solvent"):
            lines.append(f"      Solvent: {row['solvent']}")
        if row.get("catalyst"):
            lines.append(f"      Catalyst: {row['catalyst']}")
        if row.get("notes"):
            lines.append(f"      Notes: {str(row['notes'])[:200]}")
        lines.append("")

    return "\n".join(lines)


# ── Agent definition ───────────────────────────────────────────────────────
_model_name = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

agent = Agent(
    GoogleModel(_model_name),
    output_type=str,
    system_prompt="""You are an ORD (Open Reaction Database) evidence lookup agent.

Your job is to search the local reaction database for experimental precedent that
supports or challenges proposed synthesis routes.

When given a reaction SMARTS, reactant SMILES, or product SMILES:
1. Search the database for matching or similar reactions
2. Report the experimental conditions: yield, temperature, solvent, catalyst
3. Note whether the data comes from expert-curated ORD (high confidence) or
   LLM-extracted patent text (moderate confidence)
4. Summarize whether the proposed reaction has strong experimental support,
   weak support, or no precedent in the database

Be concise and factual. Do not invent data not present in the database results.""",
)


@agent.tool_plain
async def query_ord_reaction(
    reaction_smarts: str,
    reactant_smiles: list[str],
    product_smiles: str = "",
    keyword: str = "",
) -> str:
    """Search the reaction database for experimental precedent.

    Args:
        reaction_smarts: Reaction SMARTS string to search for
        reactant_smiles: List of reactant SMILES
        product_smiles: Product SMILES (optional, improves search)
        keyword: Free-text keyword (e.g. 'Suzuki palladium ethanol')
    """
    db_path = _find_db()
    if db_path is None:
        return (
            "Reaction database not available. "
            "Set PATENT_DATA_DIR to the USB path (e.g. D:\\SynAgent) "
            "or run the patent ingestion pipeline to build the database."
        )

    rows = _query_reactions(
        reaction_smarts=reaction_smarts,
        reactant_smiles=reactant_smiles,
        product_smiles=product_smiles,
        keyword=keyword,
        limit=5,
    )

    summary = _format_results(rows)

    # Add DB stats
    try:
        conn = sqlite3.connect(str(db_path))
        total = conn.execute("SELECT COUNT(*) FROM reactions").fetchone()[0]
        ord_count = conn.execute(
            "SELECT COUNT(*) FROM reactions WHERE source='ord'"
        ).fetchone()[0]
        patent_count = conn.execute(
            "SELECT COUNT(*) FROM reactions WHERE source='patent'"
        ).fetchone()[0]
        conn.close()
        summary += (
            f"\n[DB: {total:,} total reactions — "
            f"{ord_count:,} ORD expert-curated, "
            f"{patent_count:,} LLM-extracted from patents]"
        )
    except Exception:
        pass

    return summary


@agent.tool_plain
async def get_database_stats() -> str:
    """Return statistics about the reaction database."""
    db_path = _find_db()
    if db_path is None:
        return "Reaction database not found."
    try:
        conn = sqlite3.connect(str(db_path))
        total = conn.execute("SELECT COUNT(*) FROM reactions").fetchone()[0]
        ord_c = conn.execute(
            "SELECT COUNT(*) FROM reactions WHERE source='ord'"
        ).fetchone()[0]
        pat_c = conn.execute(
            "SELECT COUNT(*) FROM reactions WHERE source='patent'"
        ).fetchone()[0]
        high_conf = conn.execute(
            "SELECT COUNT(*) FROM reactions WHERE confidence >= 0.6"
        ).fetchone()[0]
        conn.close()
        return (
            f"Reaction database at {db_path}:\n"
            f"  Total reactions: {total:,}\n"
            f"  ORD expert-curated: {ord_c:,} (confidence=1.0)\n"
            f"  LLM-extracted from patents: {pat_c:,}\n"
            f"  High-confidence (>=0.6): {high_conf:,}"
        )
    except Exception as e:
        return f"Error reading database: {e}"
