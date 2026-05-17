from pydantic_ai import Agent
from pydantic_ai.models.google import GoogleModel

agent = Agent(
    GoogleModel("gemini-3-flash-preview"),
    output_type=str,
    system_prompt="""You are an ORD evidence lookup agent.
Use ORD reaction data to return experimental precedent, yields, conditions, and confidence for each reaction step.""",
)

@agent.tool_plain
async def query_ord_reaction(reaction_smarts: str, reactant_smiles: list[str]) -> dict[str, str]:
    return {
        "status": "not implemented",
        "reaction_smarts": reaction_smarts,
        "reactant_smiles": reactant_smiles,
    }
