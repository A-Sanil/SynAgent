from rdkit import Chem
from rdkit.Chem import FindMolChiralCenters


def stereochemistry_check(mol: Chem.Mol) -> dict:
    centers = FindMolChiralCenters(mol, includeUnassigned=True)
    defined = sum(1 for _, tag in centers if tag in ("R", "S"))
    undefined = sum(1 for _, tag in centers if tag == "?")

    flags = []
    if undefined > 2:
        flags.append(f"{undefined} undefined stereocenters — synthesis control issue")
    if defined + undefined > 4:
        flags.append(f"{defined + undefined} total stereocenters — complex synthesis")

    return {
        "pass": undefined <= 2,
        "defined_stereo": defined,
        "undefined_stereo": undefined,
        "total_stereo": defined + undefined,
        "flags": flags,
    }
