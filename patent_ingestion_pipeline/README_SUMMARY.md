# Patent Ingestion Pipeline — Quick Summary

## What is it?

An automated system that:
1. **Collects** patent documents from web + PDFs
2. **Parses** reactions using Qwen LLM (on Savio GPU)
3. **Validates** chemistry (SMILES, conditions)
4. **Scores confidence** (0.0–1.0 per reaction)
5. **Provides UI** for human review & correction
6. **Logs corrections** for feedback loops & retraining

## How to Use (Quick Start)

### Setup
```bash
cd patent_ingestion_pipeline
python -m pip install fastapi uvicorn jinja2 httpx requests scrapling
python -m patent_pipeline.cli init_db
```

### Collect & Parse
```bash
# Terminal 1: Web UI
python -m uvicorn patent_pipeline.webui:app --host 127.0.0.1 --port 8001 --reload

# Terminal 2: Worker (collects & parses)
python -m patent_pipeline.cli run_worker --base-url http://127.0.0.1:8000 --model qwen

# (Another terminal) Collect patents
python -m patent_pipeline.cli collect "https://example.com/patent/123"
python -m patent_pipeline.cli enqueue_all
```

### Review & Edit
- Open browser: `http://127.0.0.1:8001/`
- Click patent → edit reactions in modal → corrections logged

## Architecture in 30 Seconds

```
Web/PDFs ──► Scrapling ──► Raw Docs ──► Parse Queue ──► Qwen LLM (Savio)
                                                              │
                                                              ▼
                                                    Chem NER + SMILES Verify
                                                    (RDKit + PubChem)
                                                              │
                                                              ▼
                                                    SQLite DB + FTS5
                                                              │
                                                              ▼
                                                    Web UI (FastAPI)
                                                              │
                                                              ▼
                                                    Human Review & Corrections
                                                    (Active Learning Logs)
```

## What's Ready

✅ **Web UI** — review queue, patent detail, modal editing, batch review  
✅ **CLI** — collect, enqueue, run_worker commands  
✅ **Database** — SQLite with FTS5 indexing, parse queue, active learning logs  
✅ **Chemistry** — SMILES extraction, heuristic NER (yields, temps, catalysts)  
✅ **Validation** — multi-pass verification (LLM → NER → canonicalize → score)  
✅ **Savio Integration** — vLLM job ready, SSH tunnel setup  

## What's Partial / Pending

⚠️ **RDKit** — optional (Windows friendly fallback to PubChem)  
⚠️ **vLLM Endpoint** — job submitted, awaiting completion  
🔴 **End-to-end test** — need live vLLM to confirm full parse flow  

## Core Components

| Component | Role |
|-----------|------|
| **Scrapling** | Web collection + URL fetch |
| **collector_pdf.py** | PDF download + text/table extraction |
| **llm_parser.py** | Qwen LLM interface (vLLM endpoint) |
| **chem_ner.py** | Chemistry extraction + SMILES validation |
| **pubchem.py** | PubChem REST API helpers |
| **database.py** | SQLite persistence, FTS5, queue, active learning |
| **webui.py** | FastAPI web server (review UI) |
| **cli.py** | Command-line interface |

## Database Schema (Key Tables)

- **patents** — title, abstract, inventors, domain_tags, reviewed, raw_text
- **reactions** — product_smiles, **confidence** (0.0–1.0), yield, temp, catalyst, notes, mechanism
- **raw_documents** — source_url, fetched_at, raw_text, metadata
- **parse_queue** — raw_doc_id, status (pending/running/done), attempts
- **active_learning** — reaction_id, field, old_value, new_value, user, timestamp

## Configuration (.env)

```
PATENT_LLM_BASE_URL=http://127.0.0.1:8000    # Local or SSH tunnel
PATENT_LLM_MODEL=qwen                        # Model name
PATENT_LLM_API_KEY=                          # Optional API key
PATENT_UI_DISABLE_AUTH=1                     # Set to allow all access (dev mode)
```

## Deployment on Savio

1. Submit vLLM job: `sbatch ~/run_qwen.slurm`
2. Open SSH tunnel: `ssh -L 8000:compute_node:8000 user@savio.lbl.gov`
3. Update `.env` with `PATENT_LLM_BASE_URL=http://127.0.0.1:8000`
4. Run worker: `python -m patent_pipeline.cli run_worker --base-url http://127.0.0.1:8000`

## Next Steps

1. **Confirm vLLM** — check if Savio job is ready, test endpoint
2. **Run end-to-end** — 1 URL → enqueue → parse → view in UI
3. **Install chem libs** — RDKit, OPSIN for offline validation (optional)
4. **Add tests** — pytest suite with fixtures
5. **Scale workers** — Redis queue or multiprocessing for multiple parse jobs
6. **Implement retraining** — use active_learning logs to fine-tune Qwen

## Files to Review

- **ARCHITECTURE.md** — Full technical design
- **PRESENTATION.html** — Slide deck (open in browser, arrow keys to navigate)
- **src/patent_pipeline/webui.py** — Web server code
- **src/patent_pipeline/llm_parser.py** — LLM interface
- **src/patent_pipeline/database.py** — SQLite + queue logic
- **src/patent_pipeline/chem_ner.py** — Chemistry validation

## Questions?

- **How do I access the UI remotely?** Use SSH tunnel to port 8001
- **What if RDKit isn't available?** Falls back gracefully to heuristics + PubChem
- **Can I export corrections?** Yes, query `active_learning` table → export CSV/JSON
- **How do I scale?** Use Redis queue + multiple workers, or multiprocessing

---

**Status:** MVP ✓ | Ready for Testing ✓ | Production-Ready (pending vLLM e2e)
