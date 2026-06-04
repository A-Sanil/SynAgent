from rdkit import Chem
from rdkit.Chem.FilterCatalog import FilterCatalog, FilterCatalogParams


_PROBLEM_SMARTS = {
    "rhodanine": "O=C1CSC(=S)N1",
    "catechol": "c1cc(O)c(O)cc1",
    "hydrazine": "[NH][NH]",
    "quinone": "O=C1C=CC(=O)C=C1",
}

_PAINS_CATALOG: FilterCatalog | None = None


def _get_pains_catalog() -> FilterCatalog:
    global _PAINS_CATALOG
    if _PAINS_CATALOG is None:
        params = FilterCatalogParams()
        params.AddCatalog(FilterCatalogParams.FilterCatalogs.PAINS)
        _PAINS_CATALOG = FilterCatalog(params)
    return _PAINS_CATALOG


def reactive_group_check(mol: Chem.Mol) -> dict:
    catalog = _get_pains_catalog()
    pains_hits = [e.GetDescription() for e in catalog.GetMatches(mol)]

    other_flags = []
    for name, smarts in _PROBLEM_SMARTS.items():
        pattern = Chem.MolFromSmarts(smarts)
        if pattern and mol.HasSubstructMatch(pattern):
            other_flags.append(name)

    return {
        "pass": len(pains_hits) == 0 and len(other_flags) == 0,
        "pains_hits": pains_hits,
        "other_flags": other_flags,
    }
