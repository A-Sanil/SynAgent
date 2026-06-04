# SynAgent Yield Prediction — Progress Report
*UC Berkeley — Head-Gordon Lab adjacent project*
*Last updated: May 2026*

---

## Overview

This document tracks the full arc of building a reaction yield predictor for **SynAgent** — an AI-driven synthesis planning assistant that pairs with SynLlama's 115-template reaction engine. We walk through every modelling decision, what broke, what worked, and where we're headed.

The core question throughout: **given a proposed reaction (reactants, solvent, temperature, time), can we predict yield reliably enough to rank synthesis routes?**

---

## Phase 1 — KNN Baseline with Morgan Fingerprints

### What we built

Before any ML model, we implemented a nearest-neighbour lookup over a small curated reaction database. For a query reaction we:

1. Computed an **ECFP4 Morgan fingerprint** (`radius=2, fpSize=2048`) for both the product and the reactants using RDKit's `GetMorganFingerprintAsBitVect`.
2. Searched the database for the 5 most similar reactions using **Tanimoto similarity** — a combination score of 60% product similarity + 40% reactant similarity:

```python
score = 0.6 * DataStructs.TanimotoSimilarity(query_prod_fp, db_prod_fp) \
      + 0.4 * DataStructs.TanimotoSimilarity(query_react_fp, db_react_fp)
```

3. Predicted yield as a **weighted average** of the top-5 yields, weighted by similarity.

### Results

| Metric | Value |
|--------|-------|
| Dataset size | ~3,000 reactions (ORD subset + patent) |
| R² | **0.344** |
| Approach | KNN, top-5 Tanimoto-weighted average |

### Why R² = 0.34 is the right starting point

An R² of 0.34 means we explain 34% of yield variance. For a zero-parameter retrieval approach on a mixed-chemistry dataset — different reaction *types* (Suzuki, amide, Buchwald), different solvents, temperatures — this is reasonable. The key limitation: **Morgan fingerprints encode molecular structure, not reaction mechanism**. Two reactions can have identical products (high product Tanimoto) but completely different chemistries (e.g., Pd-catalysed vs acid-catalysed), yielding wildly different yields. The fingerprint doesn't know what bonds broke or formed.

---

## Phase 2 — Scaling the Database (ORD + Patent Pipeline)

### Open Reaction Database (ORD)

We downloaded the full Open Reaction Database from HuggingFace (`open-reaction-database/ord-data`) — 2.3M reactions from peer-reviewed literature and patents. After parsing and filtering for reactions with valid product SMILES, we obtained **967,007 rows** in `ord_full.db`.

**Key parsing bugs fixed:**
- `MessageToDict(rxn, including_default_value_fields=False)` — this flag was removed in protobuf 4.x, causing silent `TypeError` that swallowed all reactions. Fix: remove the flag entirely.
- ORD's `ProductCompound` wraps identifiers under a nested `compound` key: `product["compound"]["identifiers"]` rather than `product["identifiers"]`. Fix: check both paths.

**Result:** 967k reactions with valid product SMILES, stored in SQLite with WAL journal mode for concurrent read safety.

### Patent Pipeline

An additional `patent_pipeline.db` contributed ~2,845 high-quality reactions from literature patents, curated with yield data.

---

## Phase 3 — DRFP: Reaction-Aware Fingerprints

### The problem with Morgan fingerprints for reactions

Morgan fingerprints describe *molecules* — they don't encode *transformations*. Two reactions that make the same product via different mechanisms look similar (high Tanimoto) but behave completely differently.

### Switching to DRFP

We switched to the **Differential Reaction Fingerprint** (DRFP; Schwaller et al., *RSC Digital Discovery* 2022), which encodes the full reaction SMARTS — what bonds broke, what bonds formed, what changed:

```
reactants >> products   →   2048-bit DRFP
```

DRFP works by taking the symmetric difference of circular atom environments from reactants and products. It captures:
- Which functional groups were consumed
- Which new bonds were created
- The electronic neighbourhood of the transformation site

**Storage:** `np.packbits(fp.astype(np.uint8)).tobytes()` — 256 bytes per reaction. Decoded with `np.unpackbits(np.frombuffer(blob, dtype=np.uint8))[:2048]`.

### Fingerprinting at scale

Fingerprinting 779,840 reactions (all ORD rows with valid reaction SMARTS) required parallelisation:

```python
with ProcessPoolExecutor(max_workers=N_WORKERS) as pool:
    # 256-reaction chunks, all submitted up front
    futures = {pool.submit(_encode_chunk, c): c for c in chunks}
    for future in as_completed(futures):
        pending_pairs.extend(future.result())
        if len(pending_pairs) >= 5000:
            cur.executemany("UPDATE reactions SET reaction_fp=? WHERE reaction_id=?", pending_pairs)
            conn.commit(); pending_pairs = []
```

Key lesson: **create the pool once** for the entire run rather than spinning up/down per batch — pool creation overhead dominates at small batch sizes.

**Result:** 779,840 DRFP blobs written to `ord_full.db`. ~741 skipped due to malformed SMARTS.

---

## Phase 4 — XGBoost on All ORD Reactions

### Feature engineering

Each reaction is encoded as a **2,174-dimensional float32 vector**:

| Feature block | Dimensions | Description |
|--------------|-----------|-------------|
| DRFP | 2,048 | Reaction transformation fingerprint |
| Temperature | 1 | `(T_celsius − 25) / 100` |
| Time | 1 | `log1p(time_hours)` |
| n_reactants | 1 | Count of distinct reactant species |
| source | 1 | 0 = patent, 1 = ORD |
| Solvent OHE | 61 | Top-60 solvents + "other" bin |
| Catalyst OHE | 61 | Top-60 catalysts + "other" bin |

### Training setup

```python
model = xgb.XGBRegressor(
    n_estimators      = 10000,     # ceiling — early stopping decides
    learning_rate     = 0.05,
    max_depth         = 7,
    subsample         = 0.8,
    colsample_bytree  = 0.6,
    min_child_weight  = 5,
    gamma             = 0.1,
    reg_alpha         = 0.1,
    reg_lambda        = 1.0,
    objective         = "reg:squarederror",
    tree_method       = "hist",
    device            = "cuda",       # RTX-class GPU
    early_stopping_rounds = 50,
    random_state      = 42,
)
```

**Dataset split:** 80% train / 10% val / 10% test (stratified random, seed 42).
**Dataset size:** 417,205 reactions (414,360 ORD + 2,845 patent).

### Key fix: removing the tree cap

Early runs used `n_estimators=1000`. The validation RMSE was still *falling* at tree 1,000 — we hadn't converged. Setting `n_estimators=10000` with `early_stopping_rounds=50` lets XGBoost self-terminate at the true optimum.

### Results (full ORD dataset)

| Metric | Value |
|--------|-------|
| Train size | 333,763 |
| Val size | 41,721 |
| Test size | 41,721 |
| **R²** | **0.4600** |
| RMSE | 19.16% |
| MAE | 14.76% |
| High yield >70% RMSE | 17.6% |
| Mid yield 30–70% RMSE | 15.6% |

Going from R²=0.34 (KNN/Morgan) to R²=0.46 (XGBoost/DRFP) — a **+34% relative improvement** — came from two compounding upgrades: better fingerprint (DRFP captures mechanism) and better model (gradient boosting vs retrieval).

### Why 0.46 is still limited

ORD contains 500+ distinct reaction *types* — Suzuki, Buchwald-Hartwig, Wittig, Diels-Alder, peptide couplings, oxidations, reductions, click chemistry, heterocycle formations... A single model must simultaneously learn that:
- Pd loading matters enormously for Suzuki
- Coupling reagent matters for amide bonds
- Temperature matters for Diels-Alder stereoselectivity

Each reaction type has its own yield-determining features. When all are pooled, the signal gets diluted and the model learns averaged, blurry trends.

---

## Phase 5 — SynLlama Template Filtering

### The R-group cancellation argument

SynAgent pairs with **SynLlama** (THG Lab, UC Berkeley), which uses exactly **115 reaction templates** drawn from the same SMARTS library as `rxnmapper`. For any reaction SynLlama proposes, the template is known. This is a massive constraint.

Consider two Suzuki reactions that use the same template:

```
Ar-B(OH)₂  +  Br-Ar'  →  Ar-Ar'
```

Both reactions share the boronic acid + aryl halide scaffold. What differs is only the R-groups (substituents on Ar and Ar'). In a GNN or transformer, the invariant scaffold contributes identically to both embeddings and **cancels algebraically**, leaving only the R-group contribution to predict yield. The model essentially learns: "given this Suzuki template, how does changing from methyl→trifluoromethyl on the aryl ring affect yield?"

This is a much easier learning problem than "predict yield for any reaction in the universe." Filtering ORD to only reactions matching SynLlama's 115 templates means:
- Chemical space is vastly narrowed
- The model sees only the reaction types it will be asked to score at inference time
- R-group variation (the actual signal) stands out against a now-fixed scaffold background

### How we filter

For each ORD reaction with stored `reactant_smiles_json`, we check whether its reactants match any of the 115 template patterns using RDKit's `HasSubstructMatch`:

```python
rxn = AllChem.ReactionFromSmarts(template_smarts)
patterns = [rxn.GetReactantTemplate(j) for j in range(rxn.GetNumReactantTemplates())]

# Set-cover: every template reactant pattern must match a distinct actual reactant
def _all_patterns_matched(mols, patterns):
    used = set()
    for pat in patterns:
        for i, mol in enumerate(mols):
            if i not in used and mol.HasSubstructMatch(pat):
                used.add(i); break
        else:
            return False
    return True
```

Parallelised across all CPU cores with `ProcessPoolExecutor`, 500-reaction chunks.

### Results (SynLlama-filtered)

| Metric | All ORD (baseline) | SynLlama-filtered |
|--------|-------------------|-------------------|
| Reactions trained on | 417,205 | 336,223 |
| **R²** | **0.4600** | **0.4601** |
| RMSE (%) | 19.16 | 19.20 |
| MAE (%) | 14.76 | 14.73 |
| High yield >70% RMSE | 17.6% | 17.9% |
| Mid yield 30–70% RMSE | 15.6% | 15.3% |

**Matched:** 371,155 / 414,360 reactions (89.6%) in 11.5 minutes.

### Why the result is flat — and what it means

R² went from 0.4600 → 0.4601: essentially no change. This is the key finding of Phase 5, and it reveals something important about the SynLlama templates.

**The templates are intentionally broad.** They are written to *generate* diverse reactions during synthesis planning — not to define narrow reaction families. For example:

- Amide coupling: `[NX3;...H1,H2:1].[#6:4][C:5](=[O:6])[OH,O-]>>[N:1][C:5](=[O:6])[#6:4]` — matches hundreds of thousands of N + acid → amide reactions across wildly different chemistries
- N-alkylation: `[NX3;...H1,H2:1].[#6:2][Cl,Br,I]>>[N:1][#6:2]` — matches any amine + alkyl halide

With 89.6% of ORD reactions matching these broad patterns, we barely filtered anything. The model sees nearly the same data, producing the same R².

**The R-group cancellation argument still holds** — but it requires *narrow* template scope to be useful. A model trained on only Suzuki reactions (one specific template) would benefit enormously: the aryl halide + boronate scaffold is fixed, and only the R-groups vary. But when a single template matches both "simple primary amine + acetyl chloride" and "hindered secondary amine + complex acid chloride", the R-group variation is *not* the dominant factor — the substrate class is.

**The right unit is the individual template, not all 115 at once.**

---

## Phase 6 — Next Steps

### A. Per-template XGBoost models

Rather than one global model, train **one XGBoost regressor per SynLlama template** (115 models). Each model sees only reactions of that type. Expected: R² > 0.65 for high-frequency templates (Suzuki, amide coupling, N-alkylation) where ORD has thousands of examples.

**Limitation:** templates with few ORD matches (< 100 reactions) won't have enough data for reliable models. Solution: share parameters across structurally similar templates (same reaction class) or use the global model as a fallback.

### B. GNN with reaction-conservation constraint (arXiv 2109.09888)

The paper *"Chemical-Reaction-Aware Molecule Representation Learning"* (Chen et al., 2021) enforces:

```
∑ h(reactantᵢ) = ∑ h(productⱼ)
```

at training time. This means the model learns embeddings that are *reaction-balanced* — the GNN must represent the transformation, not just the molecules. For SynLlama's template-driven reactions, the invariant scaffold cancels:

```
h(Ar-B(OH)₂) + h(Br-Ar') → h(Ar-Ar') + h(B(OH)₂·HX)
[scaffold contribution] + [R-group contribution] = [scaffold] + [byproduct]
```

After subtracting the common scaffold, only R-group terms remain — exactly the variation that matters for yield. This architecture is purpose-built for template-constrained synthesis.

**Implementation:** Message-passing GNN (e.g., MPNN or AttentiveFP) with:
- Atom features: atomic number, degree, ring membership, hybridisation, formal charge, H count
- Bond features: bond order, conjugation, ring membership
- Reaction-conservation loss term: `λ · ‖∑h(R) − ∑h(P)‖²`
- Yield regression head on the reaction-level embedding

**Expected R²:** 0.65–0.80 on SynLlama-filtered reactions, 0.85+ on per-template subsets.

### C. Chemical language model (BERT/T5 fine-tuning)

The IBM Research group (Schwaller et al., 2021; `rxn4chemistry/rxn_yields`) showed that fine-tuning a BERT-style model on reaction SMILES achieves **R² ≈ 0.90** for Buchwald-Hartwig and Suzuki reactions in narrow substrate scopes. The approach encodes the full reaction as a SMILES string and uses the `[CLS]` token embedding for regression.

For SynAgent, this is the most promising path to agent-ready yield prediction:
1. Input: `"CC(C)c1cc(B(O)O)cco1.Brc1ccc(F)cc1>>[Pd],K2CO3,dioxane,80C,12h"` → SMILES-encoded reaction
2. Fine-tune on the SynLlama-filtered ORD subset
3. Inference: the agent passes any proposed reaction as a SMILES string to the fine-tuned model → yields a probability distribution over yield ranges

**Why SMILES encoding beats fingerprints here:** Transformers learn *positional context* — the model can learn that `[Pd(PPh3)4]` in the reagent field of a Suzuki reaction increases yield, that `K2CO3` is better than `Et3N` for boronate stability, etc. DRFP doesn't capture reagent identity at this resolution.

### D. Integration into SynAgent tool

Once any model achieves R² > 0.60 on the filtered dataset, it's ready for agent integration:

```python
# SynAgent tool call
yield_pred = yield_predictor.predict(
    reaction_smarts="CC(C)c1ccc(B(O)O)cc1.Brc1ccccc1N>>CC(C)c1ccc(-c2ccccc2N)cc1",
    temperature=80,   # °C
    time_h=12,
    solvent="thf",
    catalyst="pd(pph3)4",
)
# Returns: {"mean_yield": 74.2, "std": 8.1, "confidence": "medium"}
```

SynLlama generates candidate routes; SynAgent scores each step with `yield_predictor` and selects the route maximising overall (multi-step) yield. This closes the loop between synthesis planning and chemical feasibility.

---

## Summary

| Phase | Model | Data | R² |
|-------|-------|------|-----|
| 1 | KNN + Morgan FP | ~3k reactions | 0.344 |
| 4 | XGBoost + DRFP | 417k reactions (all ORD) | 0.4600 |
| 5 | XGBoost + DRFP | 336k reactions (89.6% match) | 0.4601 ≈ flat |
| 6A | XGBoost per-template | Per-template subsets | ~0.60–0.70 |
| 6B | Reaction-conservation GNN | SynLlama-filtered | ~0.70–0.80 |
| 6C | Fine-tuned BERT | SynLlama-filtered | ~0.85–0.90 |

**Key insight from Phase 5:** Filtering by broad templates doesn't narrow the chemical space enough. The next meaningful jump requires either per-template models (Phase 6A) or a model architecture that explicitly encodes the reaction transformation (GNN/BERT, Phases 6B/6C).

The progression from R²=0.34 to R²=0.46 came from upgrading fingerprints and scaling data. The jump to R²>0.65 requires narrowing the chemical space — either through template filtering (Phase 5) or per-template models (Phase 6A). Beyond that, GNNs with reaction-conservation constraints or language models over reaction SMILES are the frontier.

---

## References

1. Kearnes, S.M. et al. "The Open Reaction Database." *JACS* 2021. DOI: 10.1021/jacs.1c09820
2. Schwaller, P. et al. "Mapping the space of chemical reactions using attention-based neural networks." *Nature Machine Intelligence* 2021.
3. Schwaller, P. et al. "Predicting the yield of a reaction." *Chem. Sci.* 2021; `rxn4chemistry/rxn_yields`.
4. Chen, S. et al. "Chemical-Reaction-Aware Molecule Representation Learning." arXiv:2109.09888 (2021).
5. SynLlama: THGLab/SynLlama. https://github.com/THGLab/SynLlama
