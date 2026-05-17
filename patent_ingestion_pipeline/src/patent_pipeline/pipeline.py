"""Scrapling-based collection pipeline with a local parser hook."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from .database import PatentDatabase
from .models import PatentRecord, RawDocument, ReactionRecord

try:
    from scrapling import Fetcher
except ImportError:  # pragma: no cover
    Fetcher = None


class PatentParser(Protocol):
    def parse(self, document: RawDocument) -> PatentRecord: ...


@dataclass(slots=True)
class QwenParserStub:
    """Placeholder for the Savio-hosted Qwen parser."""

    def parse(self, document: RawDocument) -> PatentRecord:
        title = document.title or document.source_url
        return PatentRecord(
            patent_id=document.metadata.get("patent_id", title),
            title=title,
            abstract=document.raw_text[:1000] if document.raw_text else None,
            source_url=document.source_url,
            raw_text=document.raw_text,
            metadata={**document.metadata, "parser": "qwen_stub"},
        )


class IngestionPipeline:
    def __init__(self, database: PatentDatabase, parser: PatentParser):
        self.database = database
        self.parser = parser

    def fetch_raw_document(self, url: str, source_type: str = "patent_html") -> RawDocument:
        if Fetcher is None:
            raise ImportError("scrapling is required to collect raw patent pages")

        # special-case PDF URLs: prefer PDF collector
        if url.lower().endswith(".pdf"):
            try:
                from .collector_pdf import collect_pdf_from_url

                pdf_data = collect_pdf_from_url(url)
                return RawDocument(
                    source_url=url,
                    source_type="patent_pdf",
                    fetched_at=datetime.now(tz=timezone.utc),
                    title=None,
                    content_type="application/pdf",
                    raw_text=pdf_data.get("raw_text"),
                    raw_html=None,
                    metadata={**pdf_data.get("metadata", {}), "tables": pdf_data.get("raw_tables", [])},
                )
            except Exception:
                pass

        page = Fetcher.get(url)
        raw_html = getattr(page, "html", None)
        raw_text = getattr(page, "text", None)
        title = None
        if hasattr(page, "css"):
            try:
                title = page.css("title::text").get()
            except Exception:
                title = None

        return RawDocument(
            source_url=url,
            source_type=source_type,
            fetched_at=datetime.now(tz=timezone.utc),
            title=title,
            content_type="text/html",
            raw_text=str(raw_text) if raw_text is not None else None,
            raw_html=str(raw_html) if raw_html is not None else None,
            metadata={"fetcher": "scrapling", "source_url": url},
        )

    def collect_url(self, url: str, source_type: str = "patent_html") -> RawDocument:
        document = self.fetch_raw_document(url, source_type=source_type)
        # persist raw document
        self.database.add_raw_document(document)
        # enqueue for parsing
        # find the id of the last inserted raw document
        cur = self.database.connection.execute("SELECT id FROM raw_documents ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        if row:
            try:
                self.database.enqueue_raw_document(int(row["id"]))
            except Exception:
                pass
        return document

    def collect_many(self, urls: list[str], source_type: str = "patent_html") -> list[RawDocument]:
        return [self.collect_url(url, source_type=source_type) for url in urls]

    def parse_document(self, document: RawDocument) -> PatentRecord:
        record = self.parser.parse(document)
        self.database.upsert_patent(record)
        return record

    def parse_all(self) -> list[PatentRecord]:
        parsed_records: list[PatentRecord] = []
        for row in self.database.list_raw_documents():
            document = RawDocument(
                source_url=row["source_url"],
                source_type=row["source_type"],
                fetched_at=datetime.fromisoformat(row["fetched_at"]),
                title=row["title"],
                content_type=row["content_type"],
                raw_text=row["raw_text"],
                raw_html=row["raw_html"],
                metadata=json.loads(row["metadata_json"] or "{}"),
            )
            parsed_records.append(self.parse_document(document))
        return parsed_records


    def process_queue_once(self) -> int:
        """Process a single queue item if available. Returns 1 if processed, 0 if none."""
        item = self.database.get_next_queue_item()
        if item is None:
            return 0
        queue_id = int(item["id"])
        raw_id = int(item["raw_document_id"])
        # load raw document
        row = self.database.connection.execute("SELECT * FROM raw_documents WHERE id = ?", (raw_id,)).fetchone()
        if not row:
            self.database.update_queue_status(queue_id, "failed")
            return 0
        document = RawDocument(
            source_url=row["source_url"],
            source_type=row["source_type"],
            fetched_at=datetime.fromisoformat(row["fetched_at"]),
            title=row["title"],
            content_type=row["content_type"],
            raw_text=row["raw_text"],
            raw_html=row["raw_html"],
            metadata=json.loads(row["metadata_json"] or "{}"),
        )
        try:
            self.parse_document(document)
            self.database.update_queue_status(queue_id, "done")
            return 1
        except Exception:
            # increment attempts
            attempts = int(item["attempts"]) + 1
            if attempts >= 3:
                self.database.update_queue_status(queue_id, "failed", attempts=attempts)
            else:
                self.database.update_queue_status(queue_id, "pending", attempts=attempts)
            return 0


def build_reaction_record(
    patent_id: str,
    reaction_number: int,
    reaction_smarts: str | None = None,
    reactant_smiles: list[str] | None = None,
    product_smiles: str | None = None,
    yield_percent: float | None = None,
    temperature_celsius: float | None = None,
    solvent: str | None = None,
    catalyst: str | None = None,
    time_hours: float | None = None,
    mechanism_text: str | None = None,
    notes: str | None = None,
    metadata: dict | None = None,
) -> ReactionRecord:
    return ReactionRecord(
        reaction_id=f"{patent_id}:{reaction_number}",
        patent_id=patent_id,
        reaction_smarts=reaction_smarts,
        reactant_smiles=reactant_smiles or [],
        product_smiles=product_smiles,
        yield_percent=yield_percent,
        temperature_celsius=temperature_celsius,
        solvent=solvent,
        catalyst=catalyst,
        time_hours=time_hours,
        mechanism_text=mechanism_text,
        notes=notes,
        metadata=metadata or {},
    )
