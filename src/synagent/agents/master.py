from __future__ import annotations

import asyncio

from dotenv import load_dotenv
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.google import GoogleModel, GoogleModelSettings

from .validation import agent as validation_agent
from .optimization import agent as optimization_agent
from .chemspace import agent as chemspace_agent
from .ordtool import agent as ord_agent

from ..chemspacetool import ChemspaceDeps
from ..tokenmanager import ChemspaceTokenManager

load_dotenv()

MASTER_RPOMPT = """You are the master retrosynthesis workflow agent.

You coordinate four specialist agents:

1. validation agent
   - checks whether reaction SMILES, reaction SMARTS, reactants, products,
     and building blocks are chemically valid.

2. chemspace agent
   - searches ChemSpace for building block price and availability.

3. optimization agent
   - fetches hazard data from PubChem for each compound
   - calculates route hazard score from GHS codes.

4. ord agent
   - looks up experimental precedent from the Open Reaction Database.

Your job is not to do all calculations yourself.
Your job is to decide which specialist agent should be called, call it,
then combine the results into a clear final report.

General workflow:
1. If the user gives a reaction pathway, call the validation agent first.
2. If the user asks for price or availability, call the ChemSpace agent.
3. If the user asks for hazard assessment, call the optimization agent
   (it internally fetches PubChem data and scores).
4. If the user asks for experimental precedent, call the ord agent.
5. If the user asks for a full route evaluation, call validation, ChemSpace,
   optimization, and ord in parallel, then summarize all outputs together.

Do not invent prices.
Do not invent ChemSpace results.
Do not invent hazard codes—the optimization agent fetches real data from PubChem.
Do not invent ORD results.
If information is missing, clearly say what is missing.""".strip()

model = GoogleModel("gemini-3-flash-preview")
agent = Agent(
    model,
    output_type=str,
    system_prompt=MASTER_RPOMPT,
    deps_type=ChemspaceDeps,

)

def _ensure_model(subagent:Agent) -> Agent:
    if subagent.model is None:
        subagent.model = model
    return subagent

@agent.tool_plain
async def call_validation_agent(user_input: str) -> str:
    """
    call the validation agent to validate reaction pathway information.
    """
    subagent = _ensure_model(validation_agent)
    result = await subagent.run(user_input)
    return str(result.output)


@agent.tool_plain
async def call_chemspace_agent(user_input: str) -> str:
    """
    call the Chemspace agent to search price or availability of compounds
    """
    subagent = _ensure_model(chemspace_agent)

    mgr = ChemspaceTokenManager()
    deps = ChemspaceDeps(mgr=mgr)

    result = await subagent.run(user_input, deps=deps)
    return str(result.output)

@agent.tool_plain
async def call_optimization_agent(user_input:str) -> str:
    """
    Call the optimization agent to calculate route hazard score.
    """
    subagent = _ensure_model(optimization_agent)
    result = await subagent.run(user_input)
    return str(result.output)

@agent.tool_plain
async def call_reaction_agent(user_input:str) -> str:
    """
    Call the reaction pathway agent to lookup experimental precedent from ORD.
    """
    subagent = _ensure_model(ord_agent)
    result = await subagent.run(user_input)
    return str(result.output)

@agent.tool_plain
async def full_route_evaluation(
    pathway_input: str,
    chemspace_query: str,
    hazard_input: str,
    reaction_input: str,
) -> str:
    validation_task = asyncio.create_task(call_validation_agent(pathway_input))
    chemspace_task = asyncio.create_task(call_chemspace_agent(chemspace_query))
    optimization_task = asyncio.create_task(call_optimization_agent(hazard_input))
    reaction_task = asyncio.create_task(call_reaction_agent(reaction_input))

    validation_result, chemspace_result, optimization_result, reaction_result = await asyncio.gather(
        validation_task,
        chemspace_task,
        optimization_task,
        reaction_task,
    )

    return f"""
FULL ROUTE EVALUATION

1. VALIDATION RESULT
{validation_result}

2. CHEMSPACE PRICE / AVAILABILITY RESULT
{chemspace_result}

3. HAZARD OPTIMIZATION RESULT (with PubChem data)
{optimization_result}

4. REACTION EVIDENCE RESULT
{reaction_result}
""".strip()


    
