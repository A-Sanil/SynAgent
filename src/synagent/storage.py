import os
import sqlite3
import json
import time
from pathlib import Path
from typing import Any, Iterable


class SQLiteStorage:
    def __init__(self, db_path: str | None = None):
        self.db_path = Path(db_path or os.environ.get("STORAGE_PATH", "./data/storage.db")).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._ensure_table()

    def _ensure_table(self):
        cur = self.conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at REAL,
                source TEXT,
                path TEXT,
                offset INTEGER,
                input TEXT,
                response TEXT,
                extra TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS file_state (
                path TEXT PRIMARY KEY,
                size INTEGER NOT NULL,
                mtime REAL NOT NULL,
                processed_at REAL NOT NULL
            )
            """
        )
        self.conn.commit()

    def insert_record(self, source: str, path: str | None, offset: int | None, input_text: str | None, response_obj: Any, extra: Any | None = None) -> int:
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO records (created_at, source, path, offset, input, response, extra) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                time.time(),
                source,
                path,
                offset,
                input_text,
                json.dumps(response_obj),
                json.dumps(extra) if extra is not None else None,
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def should_process_file(self, path: str, size: int, mtime: float) -> bool:
        cur = self.conn.cursor()
        cur.execute("SELECT size, mtime FROM file_state WHERE path = ?", (path,))
        row = cur.fetchone()
        if row is None:
            return True
        return int(row[0]) != int(size) or float(row[1]) != float(mtime)

    def mark_file_processed(self, path: str, size: int, mtime: float) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO file_state (path, size, mtime, processed_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                size = excluded.size,
                mtime = excluded.mtime,
                processed_at = excluded.processed_at
            """,
            (path, int(size), float(mtime), time.time()),
        )
        self.conn.commit()

    def query_all(self) -> Iterable[dict]:
        cur = self.conn.cursor()
        cur.execute("SELECT id, created_at, source, path, offset, input, response, extra FROM records ORDER BY id DESC")
        for row in cur.fetchall():
            yield {
                "id": row[0],
                "created_at": row[1],
                "source": row[2],
                "path": row[3],
                "offset": row[4],
                "input": row[5],
                "response": json.loads(row[6]) if row[6] else None,
                "extra": json.loads(row[7]) if row[7] else None,
            }

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass
