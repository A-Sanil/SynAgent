"""Quick test: parse one chemistry paper with Gemini."""
import os, json, time
os.environ.setdefault("PATENT_DATA_DIR", "./data")

from src.patent_pipeline.database import PatentDatabase
from src.patent_pipeline.llm_parser import GeminiLLMParser
from src.patent_pipeline.models import RawDocument
from datetime import datetime, timezone

print("Imports OK")
db = PatentDatabase()
print("DB path:", str(db.db_path))

parser = GeminiLLMParser()
print("Gemini parser ready, model:", parser.model)

job = db.get_next_queue_item()
if job is None:
    print("No pending items in queue!")
    db.close()
    exit(0)

queue_id = job["id"]
raw_doc_id = job["raw_document_id"]
row = db.connection.execute("SELECT * FROM raw_documents WHERE id=?", (raw_doc_id,)).fetchone()
print("Parsing doc:", str(row["title"] or row["source_url"])[:60].encode("ascii","replace").decode())

doc = RawDocument(
    source_url=row["source_url"],
    source_type=row["source_type"],
    title=row["title"],
    fetched_at=datetime.now(tz=timezone.utc),
    content_type=row["content_type"],
    raw_text=row["raw_text"],
    raw_html=row["raw_html"],
    metadata=json.loads(row["metadata_json"] or "{}"),
)

print("Calling Gemini API...")
try:
    patent = parser.parse(doc)
    print("Gemini returned", len(patent.reactions), "reactions")
    db.upsert_patent(patent)
    rxn_count = 0
    for rxn in patent.reactions:
        rxn.metadata["source"] = "patent"
        try:
            db._insert_reaction(rxn)
            db.connection.execute("UPDATE reactions SET source='patent' WHERE reaction_id=?", (rxn.reaction_id,))
            db.connection.commit()
            rxn_count += 1
        except Exception as e:
            print("  rxn insert error:", e)
    db.update_queue_status(queue_id, "done")
    print(f"SUCCESS: {rxn_count} reactions inserted")
    for r in patent.reactions[:3]:
        print(f"  product_smiles={r.product_smiles}, yield={r.yield_percent}, conf={r.metadata.get('confidence',0):.2f}")
except Exception as exc:
    print("FAILED:", exc)
    db.update_queue_status(queue_id, "error")

total = db.connection.execute("SELECT COUNT(*) FROM reactions").fetchone()[0]
patent_rxns = db.connection.execute("SELECT COUNT(*) FROM reactions WHERE source='patent'").fetchone()[0]
print(f"\nDB totals: {total} reactions, {patent_rxns} from LLM, {total-patent_rxns} from ORD")
db.close()
