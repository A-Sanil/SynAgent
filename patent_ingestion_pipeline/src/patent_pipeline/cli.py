"""Command-line interface for the separate patent ingestion pipeline."""

from __future__ import annotations

from pathlib import Path

import typer

from .database import PatentDatabase
from .llm_parser import QwenLLMParser
from .pipeline import IngestionPipeline, QwenParserStub
import uvicorn
from dotenv import load_dotenv

# load .env so CLI commands pick up PATENT_LLM_* and PATENT_UI_*
load_dotenv()

from .webui import app as webui_app

app = typer.Typer(help="Separate patent ingestion pipeline")


@app.command()
def init_db(db_path: Path = Path("data/patent_pipeline.db")) -> None:
    db = PatentDatabase(db_path)
    db.close()
    typer.echo(f"Initialized database at {db_path}")


@app.command()
def collect(url: str, db_path: Path = Path("data/patent_pipeline.db")) -> None:
    db = PatentDatabase(db_path)
    pipeline = IngestionPipeline(database=db, parser=QwenParserStub())
    document = pipeline.collect_url(url)
    db.close()
    typer.echo(f"Collected raw document from: {document.source_url}")


@app.command()
def collect_many(urls_file: Path, db_path: Path = Path("data/patent_pipeline.db")) -> None:
    urls = [line.strip() for line in urls_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    db = PatentDatabase(db_path)
    pipeline = IngestionPipeline(database=db, parser=QwenParserStub())
    documents = pipeline.collect_many(urls)
    db.close()
    typer.echo(f"Collected {len(documents)} raw documents")


@app.command()
def parse(
    db_path: Path = Path("data/patent_pipeline.db"),
    base_url: str = typer.Option("http://127.0.0.1:8000", help="Base URL for the vLLM server"),
    model: str = typer.Option("qwen", help="Model name exposed by vLLM"),
    api_key: str | None = typer.Option(None, help="Optional bearer token for the API"),
) -> None:
    db = PatentDatabase(db_path)
    pipeline = IngestionPipeline(
        database=db,
        parser=QwenLLMParser(base_url=base_url, model=model, api_key=api_key),
    )
    records = pipeline.parse_all()
    db.close()
    typer.echo(f"Parsed {len(records)} patent records")


@app.command()
def runserver(db_path: Path = Path("data/patent_pipeline.db"), host: str = "127.0.0.1", port: int = 8001) -> None:
    """Run a small web UI for human review (FastAPI + Jinja2)."""
    # Mount the database path via environment or runserver args if needed.
    uvicorn.run(webui_app, host=host, port=port)


@app.command()
def enqueue_all(db_path: Path = Path("data/patent_pipeline.db")) -> None:
    """Enqueue all raw documents that are not yet queued."""
    db = PatentDatabase(db_path)
    cur = db.connection.execute("SELECT id FROM raw_documents ORDER BY id")
    count = 0
    for r in cur.fetchall():
        try:
            db.enqueue_raw_document(int(r["id"]))
            count += 1
        except Exception:
            pass
    db.close()
    typer.echo(f"Enqueued {count} raw documents")


@app.command()
def run_worker(db_path: Path = Path("data/patent_pipeline.db"), base_url: str = "http://127.0.0.1:8000", model: str = "qwen", interval: float = 2.0) -> None:
    """Run a simple worker that processes the parse queue continuously."""
    db = PatentDatabase(db_path)
    pipeline = IngestionPipeline(database=db, parser=QwenLLMParser(base_url=base_url, model=model))
    import time

    typer.echo("Starting worker; press Ctrl+C to stop")
    try:
        while True:
            processed = pipeline.process_queue_once()
            if processed == 0:
                time.sleep(interval)
    except KeyboardInterrupt:
        typer.echo("Worker stopped")
