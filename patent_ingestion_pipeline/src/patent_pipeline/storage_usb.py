"""USB-backed storage helper for parsed JSON and SQLite DB.

Usage:
  - Set environment variable `PATENT_DATA_DIR` or pass `path` to functions.
  - Call `init_usb_structure(path)` to create folders: raw, parsed, db.
  - Use `get_db_connection(path)` to get a sqlite3.Connection configured with safe PRAGMAs.
  - Use `save_parsed_json(patent_id, data, path)` to write parsed JSON files.

Notes:
  - Prefer NTFS for Windows USB drives to ensure proper file locking.
  - Use WAL mode and `PRAGMA synchronous=FULL` for safety.
"""

from __future__ import annotations
import os
import json
import sqlite3
from datetime import datetime
from typing import Any, Dict, Optional

DEFAULT_STRUCTURE = {
    "raw": "raw",
    "parsed": "parsed",
    "db": "db",
}


def resolve_data_dir(path: Optional[str] = None) -> str:
    path = path or os.environ.get("PATENT_DATA_DIR") or os.path.abspath("./patent_data")
    return os.path.abspath(path)


def init_usb_structure(path: Optional[str] = None) -> str:
    """Create directory layout on the USB drive (or local path).

    Returns the absolute path to the data directory.
    """
    base = resolve_data_dir(path)
    os.makedirs(base, exist_ok=True)
    for sub in DEFAULT_STRUCTURE.values():
        os.makedirs(os.path.join(base, sub), exist_ok=True)
    return base


def get_parsed_dir(path: Optional[str] = None) -> str:
    base = resolve_data_dir(path)
    return os.path.join(base, DEFAULT_STRUCTURE["parsed"])


def get_raw_dir(path: Optional[str] = None) -> str:
    base = resolve_data_dir(path)
    return os.path.join(base, DEFAULT_STRUCTURE["raw"])


def get_db_path(path: Optional[str] = None) -> str:
    base = resolve_data_dir(path)
    return os.path.join(base, DEFAULT_STRUCTURE["db"], "patents.sqlite3")


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    # Use WAL for better concurrency and crash resiliency
    cur.execute("PRAGMA journal_mode=WAL;")
    cur.execute("PRAGMA synchronous=FULL;")
    cur.execute("PRAGMA foreign_keys=ON;")
    conn.commit()


def get_db_connection(path: Optional[str] = None) -> sqlite3.Connection:
    db_path = get_db_path(path)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
    _apply_pragmas(conn)
    return conn


def save_parsed_json(patent_id: str, data: Dict[str, Any], path: Optional[str] = None) -> str:
    parsed_dir = get_parsed_dir(path)
    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    filename = f"patent_{patent_id}_{timestamp}.json"
    filepath = os.path.join(parsed_dir, filename)
    # Write atomically: write to temp then rename
    temp_path = filepath + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp_path, filepath)
    return filepath


def example_db_schema(conn: sqlite3.Connection) -> None:
    """Create minimal schema for patents/reactions and raw_documents.

    This schema is intentionally small; adapt to your needs.
    """
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS raw_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_url TEXT,
            filename TEXT,
            fetched_at TEXT,
            content_type TEXT,
            metadata TEXT
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS patents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patent_id TEXT UNIQUE,
            title TEXT,
            abstract TEXT,
            source_url TEXT,
            publication_date TEXT,
            metadata TEXT
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS reactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patent_id TEXT,
            reaction_index INTEGER,
            product_smiles TEXT,
            reactant_smiles TEXT,
            yield_percent REAL,
            temperature_celsius REAL,
            solvent TEXT,
            catalyst TEXT,
            confidence REAL,
            parsed_json_path TEXT,
            notes TEXT,
            FOREIGN KEY (patent_id) REFERENCES patents(patent_id) ON DELETE CASCADE
        );
        """
    )
    conn.commit()


def insert_parsed_meta(conn: sqlite3.Connection, patent: Dict[str, Any], parsed_path: str) -> None:
    cur = conn.cursor()
    # Upsert patent
    cur.execute(
        """
        INSERT INTO patents (patent_id, title, abstract, source_url, publication_date, metadata)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(patent_id) DO UPDATE SET
          title=excluded.title,
          abstract=excluded.abstract,
          source_url=excluded.source_url,
          publication_date=excluded.publication_date,
          metadata=excluded.metadata;
        """,
        (
            patent.get("patent_id"),
            patent.get("title"),
            patent.get("abstract"),
            patent.get("source_url"),
            patent.get("publication_date"),
            json.dumps(patent.get("metadata", {})),
        ),
    )
    # Insert reactions metadata
    reactions = patent.get("reactions", [])
    for idx, r in enumerate(reactions):
        cur.execute(
            """
            INSERT INTO reactions (patent_id, reaction_index, product_smiles, reactant_smiles,
                                   yield_percent, temperature_celsius, solvent, catalyst,
                                   confidence, parsed_json_path, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                patent.get("patent_id"),
                idx,
                r.get("product_smiles"),
                r.get("reactant_smiles"),
                r.get("yield_percent"),
                r.get("temperature_celsius"),
                r.get("solvent"),
                r.get("catalyst"),
                r.get("metadata", {}).get("confidence"),
                parsed_path,
                r.get("notes"),
            ),
        )
    conn.commit()
