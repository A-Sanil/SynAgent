from pydantic_ai import Agent
from pydantic_ai.models.google import GoogleModel

from ..pubchem import get_hazard_summary, get_hazard_summaries_batch

agent = Agent(
    GoogleModel("gemini-3-flash-preview"),
    output_type=str,
    system_prompt="""You are a PubChem hazard lookup agent.
Your job is to:
1. Look up compound hazard information from PubChem by SMILES.
2. Return structured hazard data: H-codes, signal words, red flags.
3. Do not invent hazard data; only report what PubChem returns.
4. If a compound is not found, clearly say so.""",
)


@agent.tool_plain
async def lookup_compound_hazard(smiles: str) -> dict:
    """
    Look up hazard information for a single compound by SMILES.
    Returns GHS classification, H-codes, and red-flag status.
    """
    summary = await get_hazard_summary(smiles)
    return {
        "smiles": summary.smiles,
        "cid": summary.cid,
        "found": summary.found,
        "iupac_name": summary.iupac_name,
        "hazard_statements": summary.hazard_statements,
        "red_flag": summary.red_flag,
    }


@agent.tool_plain
async def lookup_route_hazards(smiles_list: list[str]) -> dict:
    """
    Look up hazard information for a list of compounds (route molecules).
    Returns a batch summary with red flags for the entire route.
    """
    summaries = await get_hazard_summaries_batch(smiles_list)
    
    has_red_flag = any(s.red_flag for s in summaries)
    all_hazard_codes = []
    not_found = []
    
    for s in summaries:
        if not s.found:
            not_found.append(s.smiles)
        all_hazard_codes.extend(s.hazard_statements)
    
    return {
        "total_compounds": len(smiles_list),
        "compounds_found": sum(1 for s in summaries if s.found),
        "compounds_not_found": not_found,
        "all_hazard_codes": list(set(all_hazard_codes)),
        "route_has_red_flag": has_red_flag,
        "summary": f"Checked {len(smiles_list)} compounds. {sum(1 for s in summaries if s.found)} found. Red flag: {has_red_flag}",
    }
