"""Command-line interface for the separate patent ingestion pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from dotenv import load_dotenv

from .config import detect_usb_data_dir, get_db_path, init_storage, resolve_data_dir
from .database import PatentDatabase
from .llm_parser import GeminiLLMParser
from .pipeline import IngestionPipeline, RawDocumentParserStub

load_dotenv()

app = typer.Typer(help="Separate patent ingestion pipeline")


def _open_db(db_path: Optional[Path], data_dir: Optional[Path]) -> PatentDatabase:
    if db_path is not None:
        return PatentDatabase(db_path=db_path, data_dir=data_dir)
    return PatentDatabase(data_dir=data_dir)


@app.command("init-db")
def init_db(
    db_path: Optional[Path] = typer.Option(None, help="Explicit SQLite file path"),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir", help="USB or local data root"),
) -> None:
    """Initialize SQLite schema under the data directory."""
    base = init_storage(data_dir)
    db = _open_db(db_path, base)
    path = db.db_path
    db.close()
    typer.echo(f"Initialized database at {path}")


@app.command("init-usb")
def init_usb(
    drive: Optional[Path] = typer.Option(
        None,
        help="USB root (e.g. E:\\). Defaults to PATENT_DATA_DIR or auto-detected removable drive.",
    ),
) -> None:
    """Create synagent_patent_data on a USB drive and print the path to set in .env."""
    if drive is not None:
        base = init_storage(drive / "synagent_patent_data")
    else:
        detected = detect_usb_data_dir()
        if detected is not None:
            base = init_storage(detected)
        else:
            removable_root = typer.prompt("No USB auto-detected. Enter drive root (e.g. E:\\)")
            base = init_storage(Path(removable_root) / "synagent_patent_data")
    db = PatentDatabase(data_dir=base)
    db.close()
    typer.echo(f"USB storage ready at: {base}")
    typer.echo(f"Add to .env: PATENT_DATA_DIR={base}")


@app.command()
def status(data_dir: Optional[Path] = typer.Option(None, "--data-dir")) -> None:
    """Show resolved storage paths (useful before launch)."""
    base = resolve_data_dir(data_dir)
    db_path = get_db_path(base)
    typer.echo(f"data_dir: {base}")
    typer.echo(f"database: {db_path}")
    typer.echo(f"raw:      {base / 'raw'}")
    typer.echo(f"parsed:   {base / 'parsed'}")


@app.command()
def collect(
    url: str,
    db_path: Optional[Path] = typer.Option(None, "--db"),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir"),
) -> None:
    db = _open_db(db_path, data_dir)
    pipeline = IngestionPipeline(database=db, parser=RawDocumentParserStub())
    document = pipeline.collect_url(url)
    db.close()
    typer.echo(f"Collected raw document from: {document.source_url}")


@app.command("collect-many")
def collect_many(
    urls_file: Path,
    db_path: Optional[Path] = typer.Option(None, "--db"),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir"),
) -> None:
    urls = [line.strip() for line in urls_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    db = _open_db(db_path, data_dir)
    pipeline = IngestionPipeline(database=db, parser=RawDocumentParserStub())
    documents = pipeline.collect_many(urls)
    db.close()
    typer.echo(f"Collected {len(documents)} raw documents")


@app.command("collect-search")
def collect_search(
    query: str,
    limit: int = 10,
    db_path: Optional[Path] = typer.Option(None, "--db"),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir"),
) -> None:
    """Search Google Patents for US results via Scrapling and collect the pages."""
    db = _open_db(db_path, data_dir)
    pipeline = IngestionPipeline(database=db, parser=RawDocumentParserStub())
    urls = pipeline.search_google_patents(query=query, limit=limit)
    if not urls:
        db.close()
        typer.echo(f"No patent URLs found for query: {query}")
        raise typer.Exit(code=1)
    documents = pipeline.collect_many(urls)
    db.close()
    typer.echo(f"Collected {len(documents)} patent pages for query: {query}")


@app.command()
def parse(
    db_path: Optional[Path] = typer.Option(None, "--db"),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir"),
    api_key: str | None = typer.Option(None, help="Google Gemini API key (falls back to GEMINI_API_KEY)"),
    model: str = typer.Option("gemini-2.0-flash", help="Gemini model name"),
    parser: str = typer.Option("gemini", help="Parser backend: gemini"),
) -> None:
    db = _open_db(db_path, data_dir)
    if parser.lower() != "gemini":
        raise typer.BadParameter("Only the Gemini parser is supported now.")
    pipeline = IngestionPipeline(database=db, parser=GeminiLLMParser(api_key=api_key, model=model))
    records = pipeline.parse_all()
    db.close()
    typer.echo(f"Parsed {len(records)} patent records")


@app.command()
def runserver(
    host: str = "127.0.0.1",
    port: int = 8001,
    data_dir: Optional[Path] = typer.Option(None, "--data-dir"),
) -> None:
    """Run the human-in-the-loop review UI."""
    import os

    import uvicorn

    if data_dir is not None:
        os.environ["PATENT_DATA_DIR"] = str(resolve_data_dir(data_dir))
    from .webui import app as webui_app

    typer.echo(f"Review UI: http://{host}:{port}/")
    typer.echo(f"Data dir:  {resolve_data_dir()}")
    uvicorn.run(webui_app, host=host, port=port)


@app.command("enqueue-all")
def enqueue_all(
    db_path: Optional[Path] = typer.Option(None, "--db"),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir"),
) -> None:
    """Enqueue all raw documents that are not yet queued."""
    db = _open_db(db_path, data_dir)
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


@app.command("run-worker")
def run_worker(
    db_path: Optional[Path] = typer.Option(None, "--db"),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir"),
    model: str = "gemini-2.0-flash",
    api_key: str | None = typer.Option(None, help="Google Gemini API key"),
    interval: float = 2.0,
) -> None:
    """Process the parse queue continuously."""
    import time

    db = _open_db(db_path, data_dir)
    pipeline = IngestionPipeline(database=db, parser=GeminiLLMParser(api_key=api_key, model=model))
    typer.echo(f"Worker using database: {db.db_path}")
    typer.echo("Starting worker; press Ctrl+C to stop")
    try:
        while True:
            processed = pipeline.process_queue_once()
            if processed == 0:
                time.sleep(interval)
    except KeyboardInterrupt:
        typer.echo("Worker stopped")
    finally:
        db.close()


@app.command("run-full-pipeline")
def run_full_pipeline(
    query: str,
    limit: int = 10,
    db_path: Optional[Path] = typer.Option(None, "--db"),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir"),
    parser: str = typer.Option("gemini", help="Parser backend: gemini"),
    api_key: str | None = typer.Option(None, help="Google Gemini API key"),
    model: str = "gemini-2.0-flash",
) -> None:
    """Collect US patent pages by query, parse them, and populate the review database."""
    db = _open_db(db_path, data_dir)
    collect_pipeline = IngestionPipeline(database=db, parser=RawDocumentParserStub())
    urls = collect_pipeline.search_google_patents(query=query, limit=limit)
    if not urls:
        db.close()
        typer.echo(f"No patent URLs found for query: {query}")
        raise typer.Exit(code=1)
    for url in urls:
        collect_pipeline.collect_url(url)
    db.close()

    db = _open_db(db_path, data_dir)
    if parser.lower() != "gemini":
        raise typer.BadParameter("Only the Gemini parser is supported now.")
    parse_pipeline = IngestionPipeline(database=db, parser=GeminiLLMParser(api_key=api_key, model=model))
    records = parse_pipeline.parse_all()
    db.close()
    typer.echo(f"Collected and parsed {len(records)} patent records from {len(urls)} URLs")


if __name__ == "__main__":
    app()
