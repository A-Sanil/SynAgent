"""Fetch a broader set of chemistry synthesis papers from Europe PMC.

Uses 15 diverse queries targeting reaction-rich abstracts to populate the parse queue.
Skips papers already in DB (deduped by source_url).
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

os.environ.setdefault("PATENT_DATA_DIR", "./data")

from src.patent_pipeline.database import PatentDatabase
from src.patent_pipeline.models import RawDocument

QUERIES = [
    # Reaction types
    "Suzuki coupling reaction synthesis yield",
    "Heck reaction palladium catalysis",
    "Michael addition asymmetric synthesis",
    "Diels-Alder cycloaddition synthesis",
    "reductive amination synthesis yield",
    "ring-closing metathesis olefin synthesis",
    "Buchwald-Hartwig amination reaction",
    "C-H activation functionalization synthesis",
    "photoredox catalysis synthesis reaction",
    "organocatalysis enantioselective reaction",
    # Target classes
    "heterocyclic synthesis nitrogen-containing",
    "natural product total synthesis steps",
    "fluorination reaction synthesis conditions",
    "glycosylation carbohydrate synthesis yield",
    "peptide coupling synthesis conditions",
]

EPMC_URL = (
    "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
    "?query={q}&resultType=core&pageSize=20&format=json"
)


def fetch_papers() -> list[dict]:
    papers: list[dict] = []
    seen_ids: set[str] = set()
    for i, q in enumerate(QUERIES):
        url = EPMC_URL.format(q=urllib.parse.quote_plus(q))
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "SynAgent/1.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read())
            results = data.get("resultList", {}).get("result", [])
            new_this_query = 0
            for item in results:
                pid = item.get("id") or item.get("pmid") or ""
                if pid in seen_ids:
                    continue
                abstract = item.get("abstractText", "")
                if not abstract:
                    continue
                seen_ids.add(pid)
                title = item.get("title", "")
                doi = item.get("doi", "")
                source_url = (
                    f"https://doi.org/{doi}" if doi else f"https://europepmc.org/article/MED/{pid}"
                )
                papers.append({
                    "id": pid,
                    "title": title,
                    "abstract": abstract,
                    "source_url": source_url,
                    "source_type": "europepmc",
                    "journal": item.get("journalTitle", ""),
                    "date": item.get("firstPublicationDate", ""),
                })
                new_this_query += 1
            print(f"  Query {i+1:>2}/{len(QUERIES)}: {q[:50]!r} -> {new_this_query} new papers")
        except Exception as exc:
            print(f"  [WARN] Query failed: {exc}")
        time.sleep(0.5)
    print(f"\nTotal unique papers fetched: {len(papers)}")
    return papers


def enqueue_papers(db: PatentDatabase, papers: list[dict]) -> int:
    inserted = 0
    skipped = 0
    for p in papers:
        if db.has_source_url(p["source_url"]):
            skipped += 1
            continue
        raw_text = f"{p['title']}\n\n{p['abstract']}"
        if p.get("journal"):
            raw_text += f"\n\nJournal: {p['journal']}"
        doc = RawDocument(
            source_url=p["source_url"],
            source_type=p["source_type"],
            title=p["title"] or None,
            fetched_at=datetime.now(tz=timezone.utc),
            content_type="text/plain",
            raw_text=raw_text,
            raw_html=None,
            metadata={
                "epmc_id": p["id"],
                "journal": p["journal"],
                "date": p["date"],
                "ingest_source": "europepmc",
            },
        )
        db.add_raw_document(doc)
        row = db.connection.execute(
            "SELECT id FROM raw_documents WHERE source_url = ? ORDER BY id DESC LIMIT 1",
            (p["source_url"],),
        ).fetchone()
        if row:
            db.enqueue_raw_document(int(row["id"]))
            inserted += 1
    print(f"Enqueued {inserted} new documents ({skipped} already in DB, skipped)")
    return inserted


if __name__ == "__main__":
    db = PatentDatabase()
    print(f"DB: {db.db_path}")
    print("\nFetching papers from Europe PMC ...")
    papers = fetch_papers()
    print("\nEnqueueing new papers ...")
    n = enqueue_papers(db, papers)

    pending = db.connection.execute(
        "SELECT COUNT(*) FROM parse_queue WHERE status='pending'"
    ).fetchone()[0]
    total_docs = db.connection.execute("SELECT COUNT(*) FROM raw_documents").fetchone()[0]
    print(f"\nQueue now has {pending} pending documents to parse.")
    print(f"Total raw documents in DB: {total_docs}")
    db.close()
