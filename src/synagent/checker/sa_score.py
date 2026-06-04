from rdkit import Chem


def _load_sascorer():
    try:
        from rdkit.Chem.rdMolDescriptors import CalcSAScore  # type: ignore[attr-defined]
        return CalcSAScore
    except (AttributeError, ImportError):
        pass
    try:
        import importlib, os, sys
        from rdkit import RDConfig
        contrib = os.path.join(RDConfig.RDContribDir, "SA_Score")
        if contrib not in sys.path:
            sys.path.insert(0, contrib)
        sascorer = importlib.import_module("sascorer")
        return sascorer.calculateScore
    except Exception as exc:
        raise ImportError(f"SA score unavailable: {exc}") from exc


_sa_fn = None


def synthetic_accessibility(mol: Chem.Mol) -> dict:
    global _sa_fn
    if _sa_fn is None:
        _sa_fn = _load_sascorer()

    sa_score = _sa_fn(mol)
    return {
        "pass": sa_score <= 6.0,
        "sa_score": round(sa_score, 2),
        "difficulty": (
            "easy" if sa_score < 3 else
            "moderate" if sa_score < 5 else
            "hard" if sa_score < 7 else
            "very hard"
        ),
    }
