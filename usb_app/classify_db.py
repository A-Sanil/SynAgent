"""
classify_db.py — Tag reactions in patent_pipeline.db with SynLlama reaction classes.

Uses RDKit product substructure matching to assign each reaction to one of 12
categories aligned with SynLlama's 115 reaction templates.

Usage:
    python usb_app/classify_db.py [path/to/patent_pipeline.db]

Adds a `reaction_class` TEXT column to the reactions table and populates it.
"""
import sqlite3
import sys
import json
from pathlib import Path

try:
    from rdkit import Chem
    from rdkit.Chem import AllChem
    HAS_RDKIT = True
except ImportError:
    HAS_RDKIT = False
    print("RDKit not available — cannot classify reactions.")
    sys.exit(1)

# ── Reaction categories aligned with SynLlama 115 templates ──────────────────
# Each entry: (display_name, [product SMARTS patterns])
# A reaction matches the category if ANY pattern matches its product.
CLASSES = [
    ("Amide Formation", [
        "[NX3][C](=[OX1])[#6]",           # generic amide
        "[NX3][C](=[OX1])[NX3]",           # urea also, but amide first
        "[N]1[C](=[O])[#6][#6][C](=[O])1", # imide/diketopiperazine
        "[N]1[C](=[O])[#6][C](=[O])1",     # maleimide-like
        "C1(=O)NCC(=O)1",                  # lactam 5-ring
        "C1(=O)NCCC(=O)1",                 # lactam 6-ring
        "[N]1CCCCC1=O",                    # caprolactam-like
    ]),
    ("N-Alkylation & N-Arylation", [
        "[NX3;H0]([#6])[#6]",              # tertiary amine (broad)
        "[nX3]([#6])[#6]",                 # N-substituted aromatic N
    ]),
    ("Suzuki & Cross-Coupling", [
        "c-c",                              # biaryl (Suzuki product)
        "[#6]-[#6]",                        # C-C coupling (broad fallback)
    ]),
    ("Reductive Amination", [
        "[NX3;H1,H0]([#6])[CH2][#6]",     # secondary amine from aldehyde
        "[NX3;H2][CH2][#6]",               # primary amine product
    ]),
    ("Urea & Thiourea Formation", [
        "[NX3][C](=[OX1])[NX3]",           # urea
        "[NX3][C](=[SX1])[NX3]",           # thiourea
        "[NX3][C](=[NX2])[NX3]",           # guanidine
    ]),
    ("Sulfonamide Formation", [
        "[NX3][S](=[OX1])(=[OX1])[#6]",   # sulfonamide
        "[NX3][S](=[OX1])(=[OX1])c",       # aryl sulfonamide
    ]),
    ("Thiazole & Thiadiazole", [
        "c1cncs1",                          # thiazole core
        "c1nccs1",                          # isothiazole core
        "c1nnsn1",                          # thiadiazole (1,2,3)
        "c1ncns1",                          # thiadiazole (1,3,4)
        "[s]1cc[n][n]1",                    # thiadiazole variant
        "C1=CSC(=N1)",                      # dihydrothiazole
        "C1NC(=S)NC1=O",                    # thiohydantoin
        "C1(=O)CSC(=N1)",                   # thiazolinone
    ]),
    ("Pyrazole & Triazole & Tetrazole", [
        "c1cn[nH]c1",                       # pyrazole
        "c1cnn[nH]1",                       # 1H-triazole (1,2,3)
        "c1cnnn1",                           # triazole 1,2,4 (N-sub)
        "c1nn[nH]n1",                        # tetrazole
        "c1nnn[nH]1",                        # tetrazole isomer
        "c1cn[n]c1",                         # N-sub pyrazole
        "n1nncc1",                           # triazole aromatic
    ]),
    ("Oxazole & Isoxazole & Oxadiazole", [
        "c1cnco1",                           # oxazole
        "c1ccno1",                           # isoxazole
        "c1noc1",                            # 4-ring oxazole variant
        "c1nnco1",                           # 1,2,3-oxadiazole
        "c1ncno1",                           # 1,2,4-oxadiazole
        "c1nonc1",                           # 1,2,5-oxadiazole
        "C1=NOCC1",                          # isoxazoline
        "C1NOC(=O)1",                        # oxazolidinone
    ]),
    ("Pyridine, Quinoline & Dihydropyridine", [
        "c1ccncc1",                          # pyridine
        "c1ccnc2ccccc12",                    # quinoline
        "c1ccc2ncccc2c1",                    # isoquinoline
        "C1CC(=O)NC=C1",                     # dihydropyridine/lactam
        "C1=CNCC(=O)C1",                     # dihydropyridinone
        "c1cncc1",                           # pyrimidine-like
        "c1ncccn1",                          # pyrimidine
        "c1nccnc1",                          # pyrazine
    ]),
    ("Benzimidazole, Indole & Purine", [
        "c1nc2ccccc2[nH]1",                 # benzimidazole
        "c1ccc2[nH]ccc2c1",                 # indole
        "c1cc2ccnc2[nH]1",                  # azaindole
        "c1nc2[nH]cnc2n1",                  # purine core
        "c1cnc2nc[nH]c2n1",                 # adenine-like
        "c1nc2ncnc(N)c2n1",                 # purine with amine
        "c1ccc2[nH]cc(C)c2c1",             # methylindole
    ]),
    ("Functional Group Interconversion", [
        "[Cl][C](=[O])",                    # acid chloride
        "[Br][#6]",                         # alkyl bromide
        "[Cl][#6]",                         # alkyl chloride
        "[N]#[C]",                          # nitrile
        "[OH][#6]",                         # alcohol (deprotection)
        "[NX3;H2][#6]",                     # free amine (deprotection)
        "O=C[OH]",                          # carboxylic acid
    ]),
]

CLASS_NAMES = [c[0] for c in CLASSES]


def classify_smiles(smiles: str) -> str | None:
    """Return the first matching reaction class for a product SMILES, or None."""
    if not smiles:
        return None
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    for name, patterns in CLASSES:
        for pat in patterns:
            qmol = Chem.MolFromSmarts(pat)
            if qmol and mol.HasSubstructMatch(qmol):
                return name
    return "Other"


def add_column_if_missing(conn: sqlite3.Connection, col: str, col_type: str = "TEXT"):
    cols = [r[1] for r in conn.execute("PRAGMA table_info(reactions)").fetchall()]
    if col not in cols:
        conn.execute(f"ALTER TABLE reactions ADD COLUMN {col} {col_type}")
        conn.commit()
        print(f"Added column: {col}")


def run(db_path: str):
    print(f"Database: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    add_column_if_missing(conn, "reaction_class")

    rows = conn.execute(
        "SELECT reaction_id, product_smiles FROM reactions WHERE reaction_class IS NULL"
    ).fetchall()

    print(f"Classifying {len(rows)} reactions...")
    counts = {}
    batch = []

    for i, row in enumerate(rows):
        rc = classify_smiles(row["product_smiles"])
        counts[rc] = counts.get(rc, 0) + 1
        batch.append((rc, row["reaction_id"]))

        if len(batch) >= 500:
            conn.executemany(
                "UPDATE reactions SET reaction_class=? WHERE reaction_id=?", batch
            )
            conn.commit()
            batch = []
            print(f"  {i+1}/{len(rows)} done...", end="\r")

    if batch:
        conn.executemany(
            "UPDATE reactions SET reaction_class=? WHERE reaction_id=?", batch
        )
        conn.commit()

    print(f"\nDone. Distribution:")
    for cls, n in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {cls or 'None':45s} {n:6d}")

    conn.close()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        db = sys.argv[1]
    else:
        # Auto-discover
        candidates = [
            r"D:\SynAgent\db\patent_pipeline.db",
            r"E:\SynAgent\db\patent_pipeline.db",
        ]
        for c in candidates:
            if Path(c).exists():
                db = c
                break
        else:
            print("No database found. Pass path as argument.")
            sys.exit(1)

    run(db)
