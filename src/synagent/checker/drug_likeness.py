from rdkit import Chem
from rdkit.Chem import Descriptors, rdMolDescriptors


def drug_likeness(mol: Chem.Mol) -> dict:
    props = {
        "mw": Descriptors.MolWt(mol),
        "logp": Descriptors.MolLogP(mol),
        "hbd": rdMolDescriptors.CalcNumHBD(mol),
        "hba": rdMolDescriptors.CalcNumHBA(mol),
        "rotatable_bonds": rdMolDescriptors.CalcNumRotatableBonds(mol),
        "tpsa": rdMolDescriptors.CalcTPSA(mol),
        "fsp3": rdMolDescriptors.CalcFractionCSP3(mol),
        "heavy_atoms": mol.GetNumHeavyAtoms(),
        "rings": rdMolDescriptors.CalcNumRings(mol),
        "aromatic_rings": rdMolDescriptors.CalcNumAromaticRings(mol),
    }

    violations = []
    if props["mw"] > 550:
        violations.append(f"MW {props['mw']:.0f} > 550")
    if props["mw"] < 200:
        violations.append(f"MW {props['mw']:.0f} < 200")
    if props["logp"] > 5:
        violations.append(f"LogP {props['logp']:.1f} > 5")
    if props["hbd"] > 5:
        violations.append(f"HBD {props['hbd']} > 5")
    if props["hba"] > 10:
        violations.append(f"HBA {props['hba']} > 10")
    if props["rotatable_bonds"] > 10:
        violations.append(f"RotBonds {props['rotatable_bonds']} > 10")
    if props["tpsa"] > 140:
        violations.append(f"TPSA {props['tpsa']:.0f} > 140")
    if props["tpsa"] < 20:
        violations.append(f"TPSA {props['tpsa']:.0f} < 20")
    if props["fsp3"] < 0.2:
        violations.append(f"Fsp3 {props['fsp3']:.2f} < 0.2 — too flat")

    return {
        "pass": len(violations) <= 1,
        "properties": props,
        "violations": violations,
    }
