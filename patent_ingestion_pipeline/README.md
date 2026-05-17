# Patent Ingestion Pipeline

Separate research pipeline for collecting raw patent documents, extracting chemistry with a local LLM, and building a searchable synthesis database.

This project is intentionally separate from SynAgent.

## What it does

1. Collects raw patent pages and documents with Scrapling.
2. Preserves the raw HTML/text so extraction can be repeated later.
3. Sends the raw content to a local parser on the Berkeley Savio cluster, such as Qwen served through vLLM.
4. Normalizes the extracted reactions into a SQLite database for exact lookup and downstream search.

## Why Scrapling

Scrapling is the ingestion layer here because it gives you:

- `Fetcher` for fast static pages
- `DynamicFetcher` for JavaScript-heavy pages
- `StealthyFetcher` for protected pages
- `Spider` for multi-page crawls with pause/resume
- `extract` CLI commands for quick terminal-based collection

For patent work, I would start with `Fetcher` for simple patent pages, escalate to `DynamicFetcher` when the content is rendered client-side, and use `StealthyFetcher` only when the site blocks normal requests.

## Pipeline shape

```text
Patent URL list
    -> Scrapling collection
    -> Raw HTML / text archive
    -> Local Qwen parse on Savio
    -> Structured JSON records
    -> SQLite search database
```

## Suggested data model

- `raw_documents` - source URL, fetch time, raw HTML/text, metadata
- `patents` - patent id, title, abstract, tags, source metadata
- `reactions` - reaction SMARTS, reactants, products, yield, conditions, mechanism
- `search_terms` - product SMILES, tags, keywords, patent identifiers

## Savio cluster workflow

Use this project in two stages:

1. **Collect raw documents** on a login or compute node that has network access.
2. **Parse and normalize** the saved documents with your local LLM job on Savio.

A good Slurm pattern is:

```bash
#!/bin/bash
#SBATCH --job-name=patent-ingest
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=04:00:00
#SBATCH --output=logs/%x-%j.out

source ~/miniconda3/etc/profile.d/conda.sh
conda activate patent-pipeline
cd /global/home/$USER/patent_ingestion_pipeline
python -m patent_pipeline.cli collect --url-list data/urls.txt --db data/patents.db
```

Then run a second job for parsing:

```bash
#!/bin/bash
#SBATCH --job-name=patent-parse
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=12:00:00
#SBATCH --output=logs/%x-%j.out

source ~/miniconda3/etc/profile.d/conda.sh
conda activate qwen-env
cd /global/home/$USER/patent_ingestion_pipeline
python -m patent_pipeline.cli parse --db data/patents.db --model qwen
```

## API parser setup

The parser expects a Qwen service running on the Berkeley Savio cluster, exposed through a vLLM OpenAI-compatible endpoint on port 8000.

Set the environment variables once on the login node or inside your Slurm job:

```bash
export PATENT_LLM_BASE_URL=http://<NODE_IP>:8000
export PATENT_LLM_MODEL=qwen
```

Then parse the collected documents:

```bash
patent-pipeline parse --db data/patent_pipeline.db --base-url "$PATENT_LLM_BASE_URL" --model "$PATENT_LLM_MODEL"
```

If your vLLM server is protected, pass `--api-key` or export `PATENT_LLM_API_KEY`.

## Quick start

```bash
cd patent_ingestion_pipeline
python -m venv .venv
source .venv/bin/activate
pip install -e .
patent-pipeline --help

## Enhancements included

- PDF and table extraction (`collector_pdf.py`) using `pdfplumber` and `camelot`.
- Chemistry NER and SMILES normalization (`chem_ner.py`) using optional `chemdataextractor`, `opsin`, and `rdkit`.
- Auto-enqueueing of collected raw documents and a queue worker CLI (`run_worker`).
- Simple FastAPI human-in-the-loop UI with auth and inline reaction edits.

Install additional dependencies for OCR/table/chem parsing:

```bash
pip install pdfplumber camelot-py[cv] chemdataextractor opsin rdkit-pypi pytesseract
```

Notes: RDKit and some packages may require conda or platform-specific installs.
```

## Notes

- Keep raw documents and parsed records separate.
- Re-run the parser when you improve prompts or upgrade Qwen.
- Use the database as the source of truth for exact synthesis data.
