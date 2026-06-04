from rdkit import Chem


_SMARTS_FLAGS = {
    "peroxide": "[OX2][OX2]",
    "azide_chain": "[N-]=[N+]=[N]",
    "acyl_halide": "[CX3](=O)[F,Cl,Br,I]",
    "michael_acceptor": "[C]=[C][C](=O)",
    "aldehyde": "[CH1](=O)",
    "epoxide": "C1OC1",
}

_HARD_FAILS = {"peroxide", "azide_chain", "acyl_halide"}


def stability_check(mol: Chem.Mol) -> dict:
    flags = []
    for name, smarts in _SMARTS_FLAGS.items():
        pattern = Chem.MolFromSmarts(smarts)
        if pattern and mol.HasSubstructMatch(pattern):
            flags.append(name)

    nitro = Chem.MolFromSmarts("[N+](=O)[O-]")
    if nitro:
        count = len(mol.GetSubstructMatches(nitro))
        if count > 2:
            flags.append(f"nitro_groups_{count}")

    hard_flag_hits = [f for f in flags if f in _HARD_FAILS]
    return {
        "pass": len(hard_flag_hits) == 0,
        "flags": flags,
        "hard_fails": hard_flag_hits,
    }
