"""
Downloads the full Open Reaction Database from HuggingFace and parses it
into a SQLite database on the USB drive.

Steps:
  1. Downloads ~1.18 GB of .pb.gz files from HuggingFace to a local cache
  2. Parses every reaction: extracts SMILES, yield, temperature, solvent,
     catalyst, time
  3. Writes everything into D:\\SynAgent\\db\\ord_full.db

Usage:
    python "Agent tools/download_ord.py"

Requires:
    pip install ord-schema huggingface_hub
"""

import sqlite3
import json
import os
import glob
from pathlib import Path

from huggingface_hub import snapshot_download
from ord_schema.proto import dataset_pb2
from ord_schema.message_helpers import load_message
from google.protobuf.json_format import MessageToDict

# ── Config ────────────────────────────────────────────────────────────────────
CACHE_DIR = r"D:\SynAgent\ord_raw"       # where .pb.gz files land
DB_PATH   = r"D:\SynAgent\db\ord_full.db"
BATCH     = 1000                          # commit every N reactions

# ── DB schema — same columns as existing patent_pipeline.db ──────────────────
CREATE_SQL = """
CREATE TABLE IF NOT EXISTS reactions (
    reaction_id          TEXT PRIMARY KEY,
    source               TEXT DEFAULT 'ord',
    reaction_smarts      TEXT,
    reactant_smiles_json TEXT,
    product_smiles       TEXT,
    confidence           REAL,
    yield_percent        REAL,
    temperature_celsius  REAL,
    solvent              TEXT,
    catalyst             TEXT,
    time_hours           REAL,
    notes                TEXT,
    metadata_json        TEXT
);
CREATE INDEX IF NOT EXISTS idx_yield    ON reactions(yield_percent);
CREATE INDEX IF NOT EXISTS idx_product  ON reactions(product_smiles);
"""


# ── ORD field extractors ──────────────────────────────────────────────────────

def get_smiles(identifiers: list, preferred_type: str = "SMILES") -> str | None:
    for ident in identifiers:
        if ident.get("type") == preferred_type:
            return ident.get("value")
    return None


def extract_reaction(rxn_dict: dict) -> dict | None:
    """Pull the fields we care about from a single ORD reaction dict."""
    rid = rxn_dict.get("reaction_id", "")
    if not rid:
        return None

    # ── Reactant SMILES ────────────────────────────────────────────────────
    # inputs is map<string, ReactionInput>; each component is a Compound
    reactants = []
    for inp in rxn_dict.get("inputs", {}).values():
        for comp in inp.get("components", []):
            role = comp.get("reaction_role", "")
            ids  = comp.get("identifiers", [])
            smi  = get_smiles(ids)
            if smi and role in ("REACTANT", "REAGENT", "UNSPECIFIED", ""):
                reactants.append(smi)

    # ── Product SMILES + Yield ─────────────────────────────────────────────
    # In current ORD proto, ReactionOutcome.products is repeated ProductCompound.
    # ProductCompound has a nested 'compound' field (Compound) that holds identifiers.
    # Older schema versions put identifiers directly on ProductCompound — handle both.
    product_smiles = None
    yield_pct      = None

    for outcome in rxn_dict.get("outcomes", []):
        for product in outcome.get("products", []):
            # Try direct identifiers first (old schema), then nested compound (new schema)
            identifiers = product.get("identifiers", [])
            if not identifiers:
                identifiers = product.get("compound", {}).get("identifiers", [])

            if product_smiles is None:
                product_smiles = get_smiles(identifiers)

            # Measurements live directly on ProductCompound in both schema versions
            for meas in product.get("measurements", []):
                if meas.get("type") == "YIELD":
                    pct_obj = meas.get("percentage", {})
                    # percentage is a Percentage message: {"value": float, "precision": float}
                    if isinstance(pct_obj, dict):
                        pct = pct_obj.get("value")
                    else:
                        pct = pct_obj  # occasionally serialised as bare float
                    if pct is not None:
                        try:
                            pct = float(pct)
                            if 0 <= pct <= 100:
                                yield_pct = pct
                        except (TypeError, ValueError):
                            pass

    if not product_smiles:
        return None

    # ── Reaction SMARTS ────────────────────────────────────────────────────
    react_part      = ".".join(reactants) if reactants else ""
    reaction_smarts = f"{react_part}>>{product_smiles}" if react_part else None

    # ── Conditions ─────────────────────────────────────────────────────────
    conds = rxn_dict.get("conditions", {})

    temp_c = None
    temp   = conds.get("temperature", {})
    setpt  = temp.get("setpoint", {})
    if setpt:
        val  = setpt.get("value")
        unit = setpt.get("units", "CELSIUS")
        if val is not None:
            if unit == "FAHRENHEIT":
                temp_c = (float(val) - 32) * 5 / 9
            elif unit == "KELVIN":
                temp_c = float(val) - 273.15
            else:
                temp_c = float(val)

    solvent_names = []
    for comp in conds.get("solvents", []):
        smi = get_smiles(comp.get("identifiers", []))
        if smi:
            solvent_names.append(smi)
        else:
            for ident in comp.get("identifiers", []):
                if ident.get("type") == "NAME":
                    solvent_names.append(ident.get("value", ""))
                    break

    catalyst_names = []
    for inp in rxn_dict.get("inputs", {}).values():
        for comp in inp.get("components", []):
            if comp.get("reaction_role") == "CATALYST":
                smi = get_smiles(comp.get("identifiers", []))
                if smi:
                    catalyst_names.append(smi)
                else:
                    for ident in comp.get("identifiers", []):
                        if ident.get("type") == "NAME":
                            catalyst_names.append(ident.get("value", ""))
                            break

    time_h = None
    outcomes = rxn_dict.get("outcomes", [])
    if outcomes:
        timing = outcomes[0].get("reaction_time", {})
        if timing:
            t_val  = timing.get("value")
            t_unit = timing.get("units", "HOUR")
            if t_val is not None:
                try:
                    if t_unit == "MINUTE":
                        time_h = float(t_val) / 60
                    elif t_unit == "SECOND":
                        time_h = float(t_val) / 3600
                    elif t_unit == "DAY":
                        time_h = float(t_val) * 24
                    else:
                        time_h = float(t_val)
                except (TypeError, ValueError):
                    pass

    return {
        "reaction_id":          rid,
        "source":               "ord",
        "reaction_smarts":      reaction_smarts,
        "reactant_smiles_json": json.dumps(reactants),
        "product_smiles":       product_smiles,
        "confidence":           1.0,
        "yield_percent":        yield_pct,
        "temperature_celsius":  temp_c,
        "solvent":              ", ".join(solvent_names) or None,
        "catalyst":             ", ".join(catalyst_names) or None,
        "time_hours":           time_h,
        "notes":                None,
        "metadata_json":        None,
    }


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    # 1. Download (skip if files already present)
    pb_check = glob.glob(os.path.join(CACHE_DIR, "**", "*.pb.gz"), recursive=True)
    if pb_check:
        print(f"Step 1: Found {len(pb_check)} existing .pb.gz files in {CACHE_DIR} — skipping download.\n")
    else:
        print("Step 1: Downloading ORD from HuggingFace (~1.18 GB) ...")
        print(f"  Cache: {CACHE_DIR}")
        snapshot_download(
            repo_id="open-reaction-database/ord-data",
            repo_type="dataset",
            local_dir=CACHE_DIR,
            ignore_patterns=["*.md", "*.txt", "scripts/*"],
        )
        print("  Download complete.\n")

    # 2. Init DB
    print(f"Step 2: Initialising DB at {DB_PATH} ...")
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(CREATE_SQL)
    conn.commit()

    # 3. Parse all .pb.gz files
    pb_files = sorted(glob.glob(os.path.join(CACHE_DIR, "**", "*.pb.gz"), recursive=True))
    print(f"Step 3: Parsing {len(pb_files)} .pb.gz files ...\n")

    total = inserted = skipped = duplicate = 0
    _printed_sample = False  # print first reaction dict once for debugging

    for file_idx, pb_path in enumerate(pb_files, 1):
        try:
            dataset = load_message(pb_path, dataset_pb2.Dataset)
        except Exception as e:
            print(f"  [SKIP file] {Path(pb_path).name}: {e}")
            continue

        batch_rows = []
        for rxn in dataset.reactions:
            total += 1
            try:
                # NOTE: 'including_default_value_fields' was removed in protobuf 4.x
                # Use only 'preserving_proto_field_name' to keep snake_case keys
                rxn_dict = MessageToDict(
                    rxn,
                    preserving_proto_field_name=True,
                )

                # Print first reaction's top-level keys + first outcome/product
                # so we can sanity-check the structure in case of issues
                if not _printed_sample:
                    _printed_sample = True
                    print("  [DEBUG] First reaction top-level keys:", list(rxn_dict.keys()))
                    outcomes = rxn_dict.get("outcomes", [])
                    if outcomes and outcomes[0].get("products"):
                        prod0 = outcomes[0]["products"][0]
                        print("  [DEBUG] First product keys:", list(prod0.keys()))
                        if "compound" in prod0:
                            print("  [DEBUG] Nested compound keys:", list(prod0["compound"].keys()))
                    print()

                row = extract_reaction(rxn_dict)
                if row is None:
                    skipped += 1
                    continue
                batch_rows.append(row)
            except Exception as exc:
                skipped += 1

        if batch_rows:
            try:
                conn.executemany(
                    """INSERT OR IGNORE INTO reactions
                       (reaction_id, source, reaction_smarts, reactant_smiles_json,
                        product_smiles, confidence, yield_percent, temperature_celsius,
                        solvent, catalyst, time_hours, notes, metadata_json)
                       VALUES
                       (:reaction_id, :source, :reaction_smarts, :reactant_smiles_json,
                        :product_smiles, :confidence, :yield_percent, :temperature_celsius,
                        :solvent, :catalyst, :time_hours, :notes, :metadata_json)""",
                    batch_rows,
                )
                newly = conn.execute("SELECT changes()").fetchone()[0]
                inserted  += newly
                duplicate += len(batch_rows) - newly
                conn.commit()
            except Exception as e:
                print(f"  [DB error] {e}")

        print(
            f"  [{file_idx}/{len(pb_files)}] {Path(pb_path).name} — "
            f"total: {total:,}  inserted: {inserted:,}  skipped: {skipped:,}  dupes: {duplicate:,}"
        )

    conn.close()

    # 4. Summary
    print(f"\n{'='*60}")
    print(f"  Done.")
    print(f"  Total reactions parsed:  {total:,}")
    print(f"  Inserted into DB:        {inserted:,}")
    print(f"  Skipped (no product):    {skipped:,}")
    print(f"  Duplicates (OR IGNORE):  {duplicate:,}")
    print(f"  DB saved to: {DB_PATH}")
    print(f"{'='*60}")

    # Quick yield coverage check
    conn = sqlite3.connect(DB_PATH)
    cur  = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM reactions WHERE yield_percent BETWEEN 0 AND 100")
    with_yield = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM reactions")
    total_rows = cur.fetchone()[0]
    conn.close()
    print(f"\n  With valid yield (0-100%): {with_yield:,} / {total_rows:,} reactions")


if __name__ == "__main__":
    main()
