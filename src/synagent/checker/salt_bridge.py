from rdkit import Chem

# Functional groups that can form a salt bridge with GLU237 in GID4 (CACHE 8)
_SALT_BRIDGE_SMARTS = {
    "primary_amine": "[NH2;!$(NC=O);!$(NS=O)]",
    "secondary_amine": "[NH1;!$(NC=O);!$(NS=O);!$(Nc)]",
    "guanidinium": "[NH]C(=[NH])[NH2]",
    "piperidine": "C1CCNCC1",
    "piperazine": "C1CNCCN1",
    "imidazoline": "C1=NCCN1",
}


def salt_bridge_check(mol: Chem.Mol) -> dict:
    groups_found = []
    for name, smarts in _SALT_BRIDGE_SMARTS.items():
        pattern = Chem.MolFromSmarts(smarts)
        if pattern and mol.HasSubstructMatch(pattern):
            groups_found.append(name)

    return {
        "has_salt_bridge_group": len(groups_found) > 0,
        "groups_found": groups_found,
    }
