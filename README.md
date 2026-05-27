# Chemical Reaction DB & Pipeline

**Python · RDKit · SQLite · XGBoost · LLM · Open Reaction Database**

A full-stack cheminformatics pipeline spanning patent scraping, reaction database construction, two-stage SMILES validation, and an ML yield predictor trained on 414,000+ reactions.

<div align="center">
  <img src="assets/synagent.png" width="60%">
</div>

---

## Highlights

- **414,000+ reactions** ingested from the Open Reaction Database (ORD) via HuggingFace streaming
- **Async patent crawler** scraping Google Patents with LLM-structured JSON parsing for chemistry extraction
- **Two-stage SMILES validation** — RDKit canonicalization + PubChem cross-referencing with confidence scoring, auto-queuing low-confidence records for active learning review
- **XGBoost yield predictor** (v2): MAE **13.76%**, R² **0.499**, trained on DRFP reaction fingerprints with GPU-accelerated Optuna hyperparameter search
- **Reaction-type clustering** — k=20 KMeans on PCA-compressed fingerprints; best specialist cluster achieves MAE **4.5%**, R² **0.931**
- **Conformal prediction** — calibrated 80/90/95% coverage intervals with finite-sample guarantees

---

## Tech Stack

| Layer | Tools |
|-------|-------|
| Data ingestion | HuggingFace `datasets`, async `aiohttp`, SQLite |
| Chemistry | RDKit, DRFP (Differential Reaction Fingerprints), PubChem API |
| ML | XGBoost (GPU/CUDA), scikit-learn, Optuna (30-trial × 5-fold CV) |
| Validation | LLM-structured JSON prompts, PubChem confidence scoring |
| Agents | Google Gemini, asyncio, Pydantic |
| Visualization | Matplotlib (dark-theme result charts) |

---

## What This Does

### 1. Data Pipeline

```
Google Patents (async crawler)
        +
Open Reaction Database (HuggingFace streaming)
        |
        v
[LLM-structured JSON parsing]   <- extracts SMILES, conditions, yields
        |
        v
[Two-stage validation]
  Stage 1: RDKit canonicalization  -> flags invalid SMILES
  Stage 2: PubChem cross-reference -> confidence scoring
  Low-confidence records -> active learning review queue
        |
        v
[SQLite: ord_full.db]  -- 414,667 reactions with yield
  + reaction_fp column  -- pre-computed DRFP blobs (2048-bit)
```

### 2. Yield Prediction

Reactions are encoded as **DRFP (Differential Reaction Fingerprints)** — 2048-bit binary vectors representing the structural transformation as the symmetric difference of circular atom-environment n-grams. Combined with reaction conditions (temperature, time, catalyst, solvent) into a 2174-feature vector fed to XGBoost.

**Training setup:**
- 80/10/10 train/val/test split (331k / 41k / 41k), seed=42
- Optuna: 30 trials × 5-fold CV on 50k subsample for hyperparameter search
- GPU training (CUDA, XGBoost hist method)
- Manual split-conformal prediction for calibrated uncertainty intervals

**Results:**

| Model | Trees | MAE | R² |
|-------|-------|-----|-----|
| v1 | 10,000 | 14.78% | 0.467 |
| **v2** | **20,000** | **13.76%** | **0.499** |

| Yield range | MAE | RMSE |
|-------------|-----|------|
| High (70–100%) | 12.9% | 16.7% |
| Mid (30–70%) | **11.5%** | 15.3% |
| Low (5–30%) | 22.1% | 28.6% |

### 3. Route Evaluation Agents

Parallel agent system for evaluating retrosynthesis routes from [SynLlama](https://github.com/THGLab/SynLlama):

| Agent | What it does |
|-------|-------------|
| **validation** | RDKit SMILES + reaction SMARTS correctness check |
| **chemspace** | Building block pricing and availability (ChemSpace API) |
| **hazard** | GHS hazard codes per compound, route-level safety score (PubChem) |
| **precedent** | Experimental evidence lookup in ORD |
| **master** | Orchestrates all agents via `asyncio.gather()` |

---

## Repository Structure

```
.
+-- Agent tools/
|   +-- download_ord.py          # ORD bulk ingest via HuggingFace streaming
|   +-- precalc_fingerprints.py  # Pre-compute DRFP blobs into SQLite
|   +-- train_xgboost.py         # Train yield predictor (Optuna + GPU)
|   +-- extend_xgboost.py        # Continue training from checkpoint
|   +-- reaction_clusters.py     # Cluster-specialized models (k=20)
|   +-- validate_similarity.py   # Fingerprint validation utilities
|
+-- patent_ingestion_pipeline/
|   +-- src/patent_pipeline/
|       +-- collector_pdf.py     # Async patent PDF crawler
|       +-- chem_ner.py          # LLM chemistry extraction (structured JSON)
|       +-- pubchem.py           # PubChem confidence scoring
|
+-- src/synagent/
|   +-- agents/                  # Parallel route evaluation agents
|   +-- models.py                # Pydantic schemas
|   +-- chemspacetool.py         # ChemSpace API client
|
+-- data/                        # Reaction templates, sample outputs
+-- requirements.txt
+-- pyproject.toml
```

---

## Setup

```bash
git clone https://github.com/A-Sanil/SynAgent-Database-and-Scraping-Pipeline.git
cd SynAgent-Database-and-Scraping-Pipeline

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.sample .env  # add CHEMSPACE_API_KEY, GOOGLE_API_KEY
```

### Run Yield Predictor

```bash
# Step 1: Build fingerprints (one-time)
python "Agent tools/precalc_fingerprints.py" --db path/to/ord_full.db

# Step 2: Train with GPU + Optuna
python "Agent tools/train_xgboost.py" --db path/to/ord_full.db --gpu --optuna

# Step 3: Extend training from checkpoint
python "Agent tools/extend_xgboost.py" --gpu --extra_rounds 10000

# Step 4: Reaction clustering analysis
python "Agent tools/reaction_clusters.py" --db path/to/ord_full.db --k 20
```

### Run Route Evaluation

```bash
synagent eval data/synllama_output.csv   # validate chemistry
synagent run  data/routes.csv master     # full evaluation (all agents)
synagent serve master --port 8000        # serve as API
```

---

## Hyperparameters (Optuna Best)

```json
{
  "learning_rate": 0.01594,
  "max_depth": 10,
  "subsample": 0.676,
  "colsample_bytree": 0.631,
  "min_child_weight": 4,
  "gamma": 0.633,
  "reg_alpha": 0.000914,
  "reg_lambda": 0.531
}
```

---

## Environment Variables

| Variable | Required for |
|----------|-------------|
| `CHEMSPACE_API_KEY` | Building block pricing agent |
| `GOOGLE_API_KEY` | LLM-based chemistry extraction + route evaluation |

---

## License

MIT
