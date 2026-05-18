import json
import os
import time
from typing import List
from pathlib import Path

import polars as pl
import typer
from tqdm import tqdm
from dotenv import load_dotenv

load_dotenv()
from synagent.gemini_client import GeminiClient
from synagent.storage import SQLiteStorage

app = typer.Typer()


def _process_tree(root: Path, storage_path: str | None, file_types: List[str]) -> None:
    print(f"Starting traversal at {root}")
    storage = SQLiteStorage(db_path=storage_path)
    client = GeminiClient()

    for dirpath, dirnames, filenames in os.walk(root):
        for fname in filenames:
            path = Path(dirpath) / fname
            suffix = path.suffix.lower()
            if suffix not in file_types:
                continue

            try:
                stat = path.stat()
            except Exception:
                continue

            if not storage.should_process_file(str(path), stat.st_size, stat.st_mtime):
                continue

            source_label = str(path.name)
            try:
                if suffix == ".txt":
                    with path.open("r", encoding="utf-8", errors="ignore") as fh:
                        for i, line in enumerate(fh, start=1):
                            line = line.strip()
                            if not line:
                                continue
                            resp = client.generate(line)
                            storage.insert_record(source_label, str(path), i, line, resp)

                elif suffix == ".jsonl":
                    with path.open("r", encoding="utf-8", errors="ignore") as fh:
                        for i, line in enumerate(fh, start=1):
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                obj = json.loads(line)
                                prompt = json.dumps(obj)
                            except Exception:
                                prompt = line
                            resp = client.generate(prompt)
                            storage.insert_record(source_label, str(path), i, prompt, resp)

                elif suffix == ".csv":
                    try:
                        df = pl.read_csv(path)
                        for i, row in enumerate(df.rows(named=True), start=1):
                            prompt = ", ".join(str(v) for v in row.values())
                            resp = client.generate(prompt)
                            storage.insert_record(source_label, str(path), i, prompt, resp)
                    except Exception:
                        with path.open("r", encoding="utf-8", errors="ignore") as fh:
                            for i, line in enumerate(fh, start=1):
                                line = line.strip()
                                if not line:
                                    continue
                                resp = client.generate(line)
                                storage.insert_record(source_label, str(path), i, line, resp)

                storage.mark_file_processed(str(path), stat.st_size, stat.st_mtime)

            except Exception as exc:
                storage.insert_record(source_label, str(path), None, None, {"error": str(exc)})

    print(f"Traversal complete. Stored to {storage.db_path}")


@app.command(name="eval")
def eval(file: Path, output_dir: str = "data/", sampling: str = "all"):
    from synagent.validate import (
        ProductsError,
        ReactantError,
        ReactionError,
        SmilesError,
        validate,
    )

    df = pl.read_csv(file.resolve())
    if sampling == "all":
        pass
    else:
        df = df.filter(pl.col("sampling_params") == sampling)

    valid = []
    failed = []

    ntotal: int = df.height
    errors = {
        "json": 0,
        "smiles": 0,
        "reactant": 0,
        "reaction": 0,
        "product": 0,
        "other": 0,
    }
    for row in df.rows(named=True):
        success = False
        try:
            validate(row["response"])
            success = True
        except json.JSONDecodeError:
            errors["json"] += 1
        except SmilesError:
            errors["smiles"] += 1
        except ReactantError:
            errors["reactant"] += 1
        except ReactionError:
            errors["reaction"] += 1
        except ProductsError:
            errors["product"] += 1
        except Exception:
            errors["other"] += 1

        if success:
            valid.append(row)
        else:
            failed.append(row)

    total_errors = sum(errors.values())
    print(
        f"Valid percentatge: {ntotal - total_errors}/{ntotal}, {(ntotal - total_errors) / ntotal * 100:3.2f}%"
    )
    for err, val in errors.items():
        print(
            f"{err}: {val}/{total_errors}, {val / total_errors * 100:3.2f}% of errors"
        )

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    fail_df = pl.DataFrame(failed)
    fail_df.write_csv(output / "synllama-raw-failed.csv")
    valid_df = pl.DataFrame(valid)
    valid_df.write_csv(output / "synllama-raw-valid.csv")


@app.command(name="run")
def run(file: Path, agent_name: str, output: Path | None = None):
    from synagent.agents import get_agent
    from synagent.chemspacetool import ChemspaceDeps
    from synagent.tokenmanager import ChemspaceTokenManager

    if output is None:
        output = file.with_name("output").with_suffix(".jsonl")

    df = pl.read_csv(file.resolve())
    agent = get_agent(agent_name)

    deps = None
    if agent_name.lower() == "chemspace":
        mgr = ChemspaceTokenManager()
        deps = ChemspaceDeps(mgr=mgr)

    for row in tqdm(df.rows(named=True)):
        prompt = ",".join(str(v) for v in row.values())

        if deps is not None:
            result = agent.run_sync(prompt, deps=deps)
        else:
            result = agent.run_sync(prompt)

        json_bytes = result.all_messages_json()
        with output.open("ab") as f:
            f.write(json_bytes + b"\n")


@app.command(name="serve")
def serve(
    agent_name: str,
    host: str = "127.0.0.1",
    port: int = 8000,
):
    import uvicorn
    from synagent.agents import get_agent
    from synagent.chemspacetool import ChemspaceDeps
    from synagent.tokenmanager import ChemspaceTokenManager

    agent = get_agent(agent_name)

    if agent_name.lower() in {"chemspace", "master"}:
        mgr = ChemspaceTokenManager()
        deps = ChemspaceDeps(mgr=mgr)
        uvicorn.run(agent.to_web(deps=deps), host=host, port=port)
    else:
        uvicorn.run(agent.to_web(), host=host, port=port)


def main() -> None:
    app()


@app.command(name="ingest")
def ingest(file: Path, storage_path: str | None = None, source: str = "file"):
    """Ingest a text file line-by-line, send each line to Gemini, and store results in SQLite.

    Configure `GEMINI_API_KEY` and `GEMINI_API_URL` in your environment or .env file.
    """
    storage = SQLiteStorage(db_path=storage_path)
    client = GeminiClient()

    with file.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                resp = client.generate(line)
            except Exception as exc:
                resp = {"error": str(exc)}

            storage.insert_record(source, {"input": line, "response": resp})

    print(f"Ingest complete. Stored to {storage.db_path}")


@app.command(name="traverse")
def traverse(
    root: Path = Path("D:\\"),
    storage_path: str | None = None,
    file_types: List[str] = [".txt", ".jsonl", ".csv"],
):
    """Recursively traverse `root` (e.g., a USB mount like D:\\) and ingest supported files.

    For each supported file type:
    - .txt : ingest line-by-line
    - .jsonl : ingest each JSON object line
    - .csv : read rows and join columns into a prompt
    """
    _process_tree(root, storage_path, file_types)


@app.command(name="watch")
def watch(
    root: Path = Path("D:\\"),
    storage_path: str | None = None,
    interval: int = 15,
    file_types: List[str] = [".txt", ".jsonl", ".csv"],
):
    """Continuously watch a folder and re-run traversal on new or changed files."""
    print(f"Watching {root} every {interval}s")
    while True:
        _process_tree(root, storage_path, file_types)
        time.sleep(interval)


if __name__ == "__main__":
    main()