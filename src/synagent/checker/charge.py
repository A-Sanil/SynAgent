from rdkit import Chem


def charge_check(mol: Chem.Mol) -> dict:
    formal_charge = Chem.GetFormalCharge(mol)

    charged_atoms = [
        {"idx": a.GetIdx(), "symbol": a.GetSymbol(), "charge": a.GetFormalCharge()}
        for a in mol.GetAtoms()
        if a.GetFormalCharge() != 0
    ]

    if abs(formal_charge) > 2:
        return {
            "pass": False,
            "formal_charge": formal_charge,
            "charged_atoms": len(charged_atoms),
            "reason": f"total charge {formal_charge} — reward hacking",
        }

    if len(charged_atoms) > 3:
        return {
            "pass": False,
            "formal_charge": formal_charge,
            "charged_atoms": len(charged_atoms),
            "reason": f"{len(charged_atoms)} charged atoms — unstable",
        }

    for ca in charged_atoms:
        atom = mol.GetAtomWithIdx(ca["idx"])
        for neighbor in atom.GetNeighbors():
            n_charge = neighbor.GetFormalCharge()
            if n_charge != 0 and (n_charge * ca["charge"] > 0):
                return {
                    "pass": False,
                    "formal_charge": formal_charge,
                    "charged_atoms": len(charged_atoms),
                    "reason": "adjacent same-sign charges — unstable",
                }

    heavy = mol.GetNumHeavyAtoms()
    if heavy > 0 and len(charged_atoms) / heavy > 0.15:
        return {
            "pass": False,
            "formal_charge": formal_charge,
            "charged_atoms": len(charged_atoms),
            "reason": "charge density too high",
        }

    return {"pass": True, "formal_charge": formal_charge, "charged_atoms": len(charged_atoms), "reason": None}
