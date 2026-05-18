"""Verify patent search returns URLs and text."""
from patent_pipeline.patent_search import fetch_patent_document, search_us_patents
from patent_pipeline.pipeline import IngestionPipeline, RawDocumentParserStub
from patent_pipeline.database import PatentDatabase
import tempfile
from pathlib import Path

query = "organic chemical synthesis"
hits = search_us_patents(query, limit=5)
print("xhr hits", len(hits))
for h in hits[:3]:
    print(" ", h["publication_number"], h.get("title", "")[:60])

with tempfile.TemporaryDirectory() as tmp:
    db = PatentDatabase(data_dir=Path(tmp))
    pipe = IngestionPipeline(db, RawDocumentParserStub())
    urls = pipe.search_google_patents(query, limit=5)
    print("pipeline urls", len(urls))
    for u in urls[:3]:
        print(" ", u)
    if urls:
        doc = pipe.fetch_raw_document(urls[0])
        print("raw text len", len(doc.raw_text or ""), "source", doc.source_type)
    db.close()

if hits:
    detail = fetch_patent_document(hits[0]["publication_number"])
    print("detail text len", len(detail.get("text") or ""))
