from rdkit import Chem


def structural_validity(smiles: str) -> dict:
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return {"valid": False, "reason": "SMILES parse failed", "mol": None}

    try:
        Chem.SanitizeMol(mol)
    except Exception:
        return {"valid": False, "reason": "sanitization failed — bad valence", "mol": None}

    frags = Chem.GetMolFrags(mol)
    if len(frags) > 2:
        return {"valid": False, "reason": f"{len(frags)} disconnected fragments", "mol": None}

    heavy_atoms = mol.GetNumHeavyAtoms()
    if heavy_atoms < 2:
        return {"valid": False, "reason": "too few atoms", "mol": None}
    if heavy_atoms > 100:
        return {"valid": False, "reason": f"{heavy_atoms} heavy atoms — too large", "mol": None}

    return {"valid": True, "mol": mol, "heavy_atoms": heavy_atoms, "reason": None}
