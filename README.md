# SynAgent

**Agent-driven retrosynthesis route validation, evaluation, and optimization.**

An agentic framework that integrates with [SynLlama](https://github.com/THGLab/SynLlama) to validate synthetic pathways, assess building block availability and cost, compute route hazard scores, and lookup experimental precedent.

<div align="center">
  <img src="assets/synagent.png" width="66%">
</div>

## Overview

SynAgent takes retrosynthesis predictions from SynLlama and provides a comprehensive evaluation pipeline:

1. **Validation** — checks reaction SMILES/SMARTS, building blocks, and intermediate molecules for chemical correctness
2. **Availability & Pricing** — searches ChemSpace for building block cost and procurement status
3. **Hazard Assessment** — fetches GHS hazard data from PubChem and computes route-level safety scores
4. **Precedent Lookup** — queries the Open Reaction Database for experimental evidence

All agents run **in parallel** for fast route evaluation. The system avoids burning LLM tokens by using direct API calls for chemistry data.

## Quick Start

### Installation

```sh
# Clone the repo
git clone https://github.com/yourusername/SynAgent.git
cd SynAgent

# Setup virtual environment
python3 -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows

# Install dependencies
pip install -r requirements.txt

# (Optional) If you have uv installed
uv sync
```

### Environment Setup

Create a `.env` file in the root directory:

```bash
CHEMSPACE_API_KEY=your_chemspace_api_key_here
GOOGLE_API_KEY=your_google_gemini_api_key_here
```

### Verify Installation

```bash
synagent --help
```

## Usage

### 1. Validate a Route (JSON response from SynLlama)

```bash
synagent eval data/synllama_output.csv --output_dir data/
```

This validates each JSON response in a CSV file. Outputs:
- `synllama-raw-valid.csv` — rows with valid routes
- `synllama-raw-failed.csv` — rows with validation errors

### 2. Run Route Evaluation

```bash
synagent run data/routes.csv master --output output.jsonl
```

This processes each row through the master orchestrator agent. Results are written as JSONL.

### 3. Serve an Agent via API

```bash
synagent serve master --host 127.0.0.1 --port 8000
```

Start a web server for interactive agent queries. Post JSON to `http://localhost:8000/run`.

## Workflow: SynLlama → SynAgent

### Step 1: Generate routes with SynLlama

```python
from synllama import SynLlamaModel

model = SynLlamaModel.from_pretrained("synllama-7b")
routes = model.retrosynthesis(target_smiles="CCO")
# Returns: reactions, building blocks, scores
```

### Step 2: Format for SynAgent

Convert SynLlama output to SynAgent schema (JSON with `reactions` and `building_blocks`):

```json
{
  "building_blocks": ["CCO", "CC(=O)O"],
  "reactions": [
    {
      "reaction_number": 1,
      "reaction_template": "[C:1][OH].[C:2][C:3](=[O])>>[C:1][O][C:2][C:3]",
      "reactants": ["CCO", "CC(=O)O"],
      "product": "CCOC(=O)C"
    }
  ]
}
```

### Step 3: Validate and evaluate

```bash
# Validate route chemistry
synagent eval routes.csv

# Run full evaluation (validation + pricing + hazard + precedent)
synagent run routes.csv master --output results.jsonl
```

## Architecture

### Agents

| Agent | Purpose | Data Source |
|-------|---------|-------------|
| **validation** | Checks SMILES validity, reaction correctness | RDKit (local) |
| **chemspace** | Looks up building block pricing and availability | ChemSpace API |
| **optimization** | Fetches GHS hazard codes, computes route safety | PubChem API + local scoring |
| **ord** | Finds experimental precedent for reactions | ORD (Open Reaction Database) |
| **master** | Orchestrates all agents in parallel | Coordinates above |

### Data Flow

```
SynLlama Output
    ↓
[SMILES, Reactions]
    ↓
    ├─→ [validation agent]     → Chemical correctness
    ├─→ [chemspace agent]      → Building block prices
    ├─→ [optimization agent]   → Hazard scores (PubChem)
    └─→ [ord agent]            → Experimental evidence
    ↓
[Merged Report]
```

### Key Design Decisions

- **Async / Parallel execution**: All agents run concurrently via `asyncio.gather()`
- **Zero LLM tokens for chemistry data**: PubChem and ChemSpace are queried directly via HTTP
- **Structured outputs**: Each agent returns JSON with hazard codes, prices, and confidence scores
- **Pydantic schemas**: Strict type validation for all data models

## Configuration

### Required Environment Variables

- `CHEMSPACE_API_KEY` — API key for ChemSpace compound search
- `GOOGLE_API_KEY` — API key for Google Gemini models

### Optional Settings

Edit `pyproject.toml` or override at runtime:
- Python version: ≥ 3.13
- Gemini model: defaults to `gemini-3-flash-preview`
- Hazard gamma: weight on worst-compound hazard (0 to 1, default 0.6)

## Examples

### Full Route Evaluation

Input: a CSV with a `response` column containing JSON route data.

```python
import asyncio
from synagent.agents import get_agent

async def main():
    agent = get_agent("master")
    result = await agent.run("""
    Please fully evaluate this synthetic route:
    target: CCO
    reactions: [...]
    building_blocks: [...]
    """)
    print(result.output)

asyncio.run(main())
```

### Direct Hazard Scoring

```python
from synagent.agents.optimization import score_route_hazard

smiles_list = ["CCO", "CC(=O)O", "CCOC(=O)C"]
result = await score_route_hazard(
    smiles_list=smiles_list,
    compound_names=["ethanol", "acetic acid", "ethyl acetate"],
    gamma=0.6
)
print(f"Route hazard: {result['route_hazard']}")
print(f"Red flags: {result['has_red_flag']}")
```

## Data Models

### SynLlama Input Format

```python
class SynLlamaReaction(BaseModel):
    reaction_number: int
    reaction_template: str  # SMARTS
    reactants: List[str]    # SMILES
    product: str            # SMILES

class SynLlamaFormat(BaseModel):
    reactions: List[SynLlamaReaction]
    building_blocks: List[str]
```

### SynAgent Output Format

```python
class ReactionResult(BaseModel):
    reaction_number: int
    reaction_template: str
    reactant_smiles: List[str]
    expected_product: str
    actual_products: List[str]
    status: Literal["passed", "failed"]
    failure_mode: str | None

class SynLlamaReport(BaseModel):
    reactions: List[ReactionResult]
    building_blocks: List[...]
    all_building_blocks_valid: bool
    all_reactions_passed: bool
```

## Testing

Validate chemistry on a sample route:

```bash
synagent eval data/test_routes.csv
```

Check for errors:
- `json` — malformed JSON response
- `smiles` — invalid SMILES strings
- `reactant` — reactants don't match template
- `reaction` — reaction produced no products
- `product` — expected product not formed

## Performance & Token Efficiency

| Task | LLM Tokens | Data Lookups |
|------|-----------|--------------|
| Validation | ~100 | RDKit only |
| PubChem hazard | ~50 | 1 HTTP call per compound |
| ChemSpace pricing | ~50 | 1 HTTP call per SMILES |
| Full route eval | ~200 | All in parallel |

**vs. naive LLM approach**: ~5000 tokens to manually describe all chemistry.

## Troubleshooting

### "CHEMSPACE_API_KEY is missing"
Set environment variable or pass `api_key` explicitly:
```python
mgr = ChemspaceTokenManager(api_key="...")
```

### "No PubChem data found for SMILES"
The compound may not be in PubChem. Optimization agent will flag this and return empty hazard codes.

### "Reaction produced no products"
The provided reactants do not satisfy the reaction SMARTS. Check SMILES validity and template.

## Contributing

Contributions welcome! Areas of improvement:
- Additional hazard scoring methods (NIOSH, ECHA)
- Integration with USPTO patent data
- Human-in-the-loop approval workflows
- Cost model refinement

## Citation

If you use SynAgent with SynLlama, please cite:

```bibtex
@software{synagent2024,
  title={SynAgent: Agent-Driven Retrosynthesis Route Validation},
  author={Your Name},
  year={2024},
  url={https://github.com/yourusername/SynAgent}
}

@article{synllama2024,
  title={SynLlama: Retrosynthesis Prediction with Fine-Tuned Large Language Models},
  author={THGLab},
  year={2024},
  url={https://github.com/THGLab/SynLlama}
}
```

## License

MIT

## Contact

Questions or issues? Open a GitHub issue or contact the maintainers.
