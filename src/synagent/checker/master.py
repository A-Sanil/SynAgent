from .charge import charge_check
from .drug_likeness import drug_likeness
from .reactive import reactive_group_check
from .sa_score import synthetic_accessibility
from .salt_bridge import salt_bridge_check
from .stability import stability_check
from .stereo import stereochemistry_check
from .structural import structural_validity


def check_molecule_full(smiles: str) -> dict:
    result: dict = {"smiles": smiles, "pass": False, "checks": {}, "kill_reason": None}

    sv = structural_validity(smiles)
    result["checks"]["structural"] = sv
    if not sv["valid"]:
        result["kill_reason"] = sv["reason"]
        return result

    mol = sv["mol"]

    cc = charge_check(mol)
    result["checks"]["charge"] = cc
    if not cc["pass"]:
        result["kill_reason"] = cc["reason"]
        return result

    sc = stability_check(mol)
    result["checks"]["stability"] = sc
    if not sc["pass"]:
        result["kill_reason"] = f"unstable: {sc['hard_fails']}"
        return result

    rc = reactive_group_check(mol)
    result["checks"]["reactive"] = rc
    if not rc["pass"]:
        result["kill_reason"] = f"PAINS: {rc['pains_hits'] + rc['other_flags']}"
        return result

    dl = drug_likeness(mol)
    result["checks"]["drug_likeness"] = dl
    if not dl["pass"]:
        result["kill_reason"] = f"drug-likeness: {dl['violations']}"
        return result

    sa = synthetic_accessibility(mol)
    result["checks"]["sa_score"] = sa
    if not sa["pass"]:
        result["kill_reason"] = f"SA score {sa['sa_score']} — too hard to synthesize"
        return result

    st = stereochemistry_check(mol)
    result["checks"]["stereo"] = st
    if not st["pass"]:
        result["kill_reason"] = f"stereo: {st['flags']}"
        return result

    # Advisory only — don't kill on missing salt bridge group
    sb = salt_bridge_check(mol)
    result["checks"]["salt_bridge"] = sb

    result["pass"] = True
    return result


def check_batch(smiles_list: list[str]) -> dict:
    results = [check_molecule_full(smi) for smi in smiles_list]

    passed = [r for r in results if r["pass"]]
    failed = [r for r in results if not r["pass"]]

    kill_reasons: dict[str, int] = {}
    for r in failed:
        reason = r["kill_reason"] or "unknown"
        category = reason.split(":")[0] if ":" in reason else reason
        kill_reasons[category] = kill_reasons.get(category, 0) + 1

    return {
        "total": len(results),
        "passed": len(passed),
        "failed": len(failed),
        "pass_rate": len(passed) / max(len(results), 1),
        "kill_reasons": kill_reasons,
        "passed_smiles": [r["smiles"] for r in passed],
        "results": results,
    }
