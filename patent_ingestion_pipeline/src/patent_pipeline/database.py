"""SQLite persistence for the patent ingestion pipeline."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import PatentRecord, RawDocument, ReactionRecord


class PatentDatabase:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row
        self.initialize()

    def initialize(self) -> None:
        with self.connection:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS raw_documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_url TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    title TEXT,
                    fetched_at TEXT NOT NULL,
                    content_type TEXT,
                    raw_text TEXT,
                    raw_html TEXT,
                    metadata_json TEXT
                );

                CREATE TABLE IF NOT EXISTS patents (
                    patent_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    abstract TEXT,
                    source_url TEXT,
                    publication_date TEXT,
                    inventors_json TEXT,
                    assignee TEXT,
                    domain_tags_json TEXT,
                    target_terms_json TEXT,
                    reviewed INTEGER DEFAULT 0,
                    raw_text TEXT,
                    metadata_json TEXT
                );

                CREATE TABLE IF NOT EXISTS reactions (
                    reaction_id TEXT PRIMARY KEY,
                    patent_id TEXT NOT NULL,
                    reaction_smarts TEXT,
                    reactant_smiles_json TEXT,
                    product_smiles TEXT,
                    confidence REAL,
                    yield_percent REAL,
                    temperature_celsius REAL,
                    solvent TEXT,
                    catalyst TEXT,
                    time_hours REAL,
                    mechanism_text TEXT,
                    notes TEXT,
                    metadata_json TEXT,
                    FOREIGN KEY(patent_id) REFERENCES patents(patent_id)
                );

                CREATE INDEX IF NOT EXISTS idx_reactions_product_smiles ON reactions(product_smiles);
                CREATE INDEX IF NOT EXISTS idx_reactions_patent_id ON reactions(patent_id);
                CREATE INDEX IF NOT EXISTS idx_raw_documents_source_url ON raw_documents(source_url);
                """
            )

        # Create parse queue and FTS5 virtual tables where available
        with self.connection:
            # queue: raw_document -> pending parse
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS parse_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    raw_document_id INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT,
                    FOREIGN KEY(raw_document_id) REFERENCES raw_documents(id)
                );
                """
            )

            # active learning / correction logs for human-in-the-loop
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS active_learning (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    reaction_id TEXT,
                    patent_id TEXT,
                    field TEXT,
                    old_value TEXT,
                    new_value TEXT,
                    user TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                );
                """
            )

            # Try to create FTS5 virtual tables; fallback to plain helper tables if FTS5 unavailable
            try:
                self.connection.execute("CREATE VIRTUAL TABLE IF NOT EXISTS patents_fts USING fts5(patent_id, title, abstract, raw_text);")
                self.connection.execute("CREATE VIRTUAL TABLE IF NOT EXISTS reactions_fts USING fts5(reaction_id, patent_id, reaction_text);")
            except sqlite3.OperationalError:
                self.connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS patents_fts (
                        patent_id TEXT PRIMARY KEY,
                        title TEXT,
                        abstract TEXT,
                        raw_text TEXT
                    );
                    """
                )
                self.connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS reactions_fts (
                        reaction_id TEXT PRIMARY KEY,
                        patent_id TEXT,
                        reaction_text TEXT
                    );
                    """
                )

    def add_raw_document(self, document: RawDocument) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO raw_documents (
                    source_url, source_type, title, fetched_at,
                    content_type, raw_text, raw_html, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document.source_url,
                    document.source_type,
                    document.title,
                    document.fetched_at.isoformat(),
                    document.content_type,
                    document.raw_text,
                    document.raw_html,
                    json.dumps(document.metadata),
                ),
            )

    def list_raw_documents(self) -> list[sqlite3.Row]:
        cursor = self.connection.execute(
            """
            SELECT source_url, source_type, title, fetched_at, content_type, raw_text, raw_html, metadata_json
            FROM raw_documents
            ORDER BY id
            """
        )
        return cursor.fetchall()

    def upsert_patent(self, record: PatentRecord) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO patents (
                    patent_id, title, abstract, source_url, publication_date,
                    inventors_json, assignee, domain_tags_json, target_terms_json,
                    raw_text, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(patent_id) DO UPDATE SET
                    title = excluded.title,
                    abstract = excluded.abstract,
                    source_url = excluded.source_url,
                    publication_date = excluded.publication_date,
                    inventors_json = excluded.inventors_json,
                    assignee = excluded.assignee,
                    domain_tags_json = excluded.domain_tags_json,
                    target_terms_json = excluded.target_terms_json,
                    raw_text = excluded.raw_text,
                    metadata_json = excluded.metadata_json
                """,
                (
                    record.patent_id,
                    record.title,
                    record.abstract,
                    record.source_url,
                    record.publication_date,
                    json.dumps(record.inventors),
                    record.assignee,
                    json.dumps(record.domain_tags),
                    json.dumps(record.target_terms),
                    record.raw_text,
                    json.dumps(record.metadata),
                ),
            )

            self.connection.execute("DELETE FROM reactions WHERE patent_id = ?", (record.patent_id,))
            for reaction in record.reactions:
                self._insert_reaction(reaction)

        # Maintain simple full-text indexes for quick search and review
        try:
            self._index_patent_fts(record)
            for reaction in record.reactions:
                self._index_reaction_fts(reaction)
        except Exception:
            # Keep database upsert robust even if indexing fails
            pass

    def _insert_reaction(self, reaction: ReactionRecord) -> None:
        # Insert with optional confidence column if the schema includes it.
        cols = [
            'reaction_id', 'patent_id', 'reaction_smarts', 'reactant_smiles_json',
            'product_smiles', 'yield_percent', 'temperature_celsius', 'solvent',
            'catalyst', 'time_hours', 'mechanism_text', 'notes', 'metadata_json'
        ]
        vals = [
            reaction.reaction_id,
            reaction.patent_id,
            reaction.reaction_smarts,
            json.dumps(reaction.reactant_smiles),
            reaction.product_smiles,
            reaction.yield_percent,
            reaction.temperature_celsius,
            reaction.solvent,
            reaction.catalyst,
            reaction.time_hours,
            reaction.mechanism_text,
            reaction.notes,
            json.dumps(reaction.metadata),
        ]
        # include confidence if present in schema or reaction.metadata
        include_conf = False
        try:
            cur = self.connection.execute("PRAGMA table_info(reactions)").fetchall()
            cols_present = [c[1] for c in cur]
            if 'confidence' in cols_present:
                include_conf = True
        except Exception:
            include_conf = False
        if include_conf:
            cols.insert(-2, 'confidence')
            vals.insert(-2, reaction.metadata.get('confidence'))

        placeholders = ','.join(['?'] * len(cols))
        q = f"INSERT INTO reactions ({', '.join(cols)}) VALUES ({placeholders})"
        self.connection.execute(q, tuple(vals))

    def _index_patent_fts(self, record: PatentRecord) -> None:
        """Insert or replace a lightweight FTS row for a patent."""
        self.connection.execute(
            """
            INSERT INTO patents_fts (patent_id, title, abstract, raw_text)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(patent_id) DO UPDATE SET
                title = excluded.title,
                abstract = excluded.abstract,
                raw_text = excluded.raw_text
            """,
            (record.patent_id, record.title, record.abstract, record.raw_text),
        )

    def _index_reaction_fts(self, reaction: ReactionRecord) -> None:
        """Create or update a lightweight FTS row for a reaction by concatenating key fields."""
        combined = " \n ".join(filter(None, [
            reaction.reaction_smarts or "",
            ";".join(reaction.reactant_smiles or []),
            reaction.product_smiles or "",
            str(reaction.yield_percent) if reaction.yield_percent is not None else "",
            reaction.mechanism_text or "",
            reaction.notes or "",
        ]))
        self.connection.execute(
            """
            INSERT INTO reactions_fts (reaction_id, patent_id, reaction_text)
            VALUES (?, ?, ?)
            ON CONFLICT(reaction_id) DO UPDATE SET
                patent_id = excluded.patent_id,
                reaction_text = excluded.reaction_text
            """,
            (reaction.reaction_id, reaction.patent_id, combined),
        )

    def search_text(self, query: str, limit: int = 50) -> dict[str, list[sqlite3.Row]]:
        """Search patents and reactions by text.

        If SQLite FTS5 is available, prefer MATCH queries for speed; otherwise fall back to LIKE.
        """
        q = query.strip()
        # Try FTS5 MATCH first
        try:
            patents = self.connection.execute(
                """
                SELECT p.* FROM patents p JOIN patents_fts f ON p.patent_id = f.patent_id
                WHERE f MATCH ?
                LIMIT ?
                """,
                (q, limit),
            ).fetchall()
            reactions = self.connection.execute(
                """
                SELECT r.* FROM reactions r JOIN reactions_fts f ON r.reaction_id = f.reaction_id
                WHERE f MATCH ?
                LIMIT ?
                """,
                (q, limit),
            ).fetchall()
            return {"patents": patents, "reactions": reactions}
        except sqlite3.OperationalError:
            # Fallback: simple LIKE over helper tables
            q_like = f"%{q}%"
            patents = self.connection.execute(
                """
                SELECT p.* FROM patents p JOIN patents_fts f ON p.patent_id = f.patent_id
                WHERE f.title LIKE ? OR f.abstract LIKE ? OR f.raw_text LIKE ?
                LIMIT ?
                """,
                (q_like, q_like, q_like, limit),
            ).fetchall()
            reactions = self.connection.execute(
                """
                SELECT r.* FROM reactions r JOIN reactions_fts f ON r.reaction_id = f.reaction_id
                WHERE f.reaction_text LIKE ?
                LIMIT ?
                """,
                (q_like, limit),
            ).fetchall()
            return {"patents": patents, "reactions": reactions}

    # ---------------- Queue helpers ----------------
    def enqueue_raw_document(self, raw_document_id: int) -> int:
        """Add a raw document to the parse queue; returns queue id."""
        with self.connection:
            cur = self.connection.execute(
                "INSERT INTO parse_queue (raw_document_id, status, created_at) VALUES (?, 'pending', datetime('now'))",
                (raw_document_id,),
            )
            return cur.lastrowid

    def get_next_queue_item(self) -> sqlite3.Row | None:
        """Fetch next pending queue item (atomic lock via status update)."""
        with self.connection:
            row = self.connection.execute(
                "SELECT id, raw_document_id, status, attempts FROM parse_queue WHERE status = 'pending' ORDER BY id LIMIT 1"
            ).fetchone()
            if not row:
                return None
            # mark as running
            self.connection.execute(
                "UPDATE parse_queue SET status = 'running', updated_at = datetime('now') WHERE id = ?",
                (row["id"],),
            )
            return row

    def update_queue_status(self, queue_id: int, status: str, attempts: int | None = None) -> None:
        with self.connection:
            if attempts is None:
                self.connection.execute("UPDATE parse_queue SET status = ?, updated_at = datetime('now') WHERE id = ?", (status, queue_id))
            else:
                self.connection.execute(
                    "UPDATE parse_queue SET status = ?, attempts = ?, updated_at = datetime('now') WHERE id = ?",
                    (status, attempts, queue_id),
                )

    def mark_patent_reviewed(self, patent_id: str, reviewed: bool = True) -> None:
        with self.connection:
            self.connection.execute("UPDATE patents SET reviewed = ? WHERE patent_id = ?", (1 if reviewed else 0, patent_id))

    # ---------------- Optional semantic index (requires sentence-transformers + faiss) ----------------
    def build_semantic_index(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        try:
            from sentence_transformers import SentenceTransformer
            import numpy as np
            import faiss
        except Exception:
            raise RuntimeError("sentence-transformers and faiss are required for semantic indexing")

        model = SentenceTransformer(model_name)
        rows = self.connection.execute("SELECT patent_id, title, abstract, raw_text FROM patents").fetchall()
        texts = [f"{r['title'] or ''} {r['abstract'] or ''} {r['raw_text'] or ''}" for r in rows]
        ids = [r['patent_id'] for r in rows]
        if not texts:
            return
        embeddings = model.encode(texts, convert_to_numpy=True)
        dim = embeddings.shape[1]
        index = faiss.IndexFlatL2(dim)
        index.add(embeddings)
        # store index and mapping on disk beside DB
        import pickle
        idx_path = str(self.db_path) + ".faiss.index"
        faiss.write_index(index, idx_path)
        with open(str(self.db_path) + ".faiss.ids", "wb") as f:
            pickle.dump(ids, f)

    def semantic_search(self, query: str, top_k: int = 10, model_name: str = "all-MiniLM-L6-v2") -> list[sqlite3.Row]:
        try:
            from sentence_transformers import SentenceTransformer
            import numpy as np
            import faiss
            import pickle
        except Exception:
            raise RuntimeError("sentence-transformers and faiss are required for semantic search")

        idx_path = str(self.db_path) + ".faiss.index"
        ids_path = str(self.db_path) + ".faiss.ids"
        index = faiss.read_index(idx_path)
        with open(ids_path, "rb") as f:
            ids = pickle.load(f)
        model = SentenceTransformer(model_name)
        q_emb = model.encode([query], convert_to_numpy=True)
        D, I = index.search(q_emb, top_k)
        results = []
        for pos in I[0]:
            if pos < 0 or pos >= len(ids):
                continue
            pid = ids[pos]
            row = self.connection.execute("SELECT * FROM patents WHERE patent_id = ?", (pid,)).fetchone()
            if row:
                results.append(row)
        return results

    def search_by_smiles(self, smiles: str) -> list[sqlite3.Row]:
        cursor = self.connection.execute(
            """
            SELECT *
            FROM reactions
            WHERE LOWER(product_smiles) = LOWER(?)
               OR LOWER(reactant_smiles_json) LIKE LOWER(?)
            ORDER BY patent_id
            """,
            (smiles, f'%{smiles}%'),
        )
        return cursor.fetchall()

    def close(self) -> None:
        self.connection.close()
