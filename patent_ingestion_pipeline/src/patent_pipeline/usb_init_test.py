"""Quick test for USB storage + unified database schema."""

from __future__ import annotations

import os
import tempfile

from patent_pipeline.config import init_storage
from patent_pipeline.database import PatentDatabase
from patent_pipeline.models import PatentRecord, ReactionRecord


def main() -> None:
    test_dir = os.path.abspath(os.environ.get("PATENT_TEST_DIR", "./test_usb_mount"))
    init_storage(test_dir)
    db = PatentDatabase(data_dir=test_dir)
    record = PatentRecord(
        patent_id="US-TEST-0001",
        title="Test Patent",
        abstract="Unit test patent.",
        source_url="https://example.com/patent/1",
        reactions=[
            ReactionRecord(
                reaction_id="US-TEST-0001:1",
                patent_id="US-TEST-0001",
                product_smiles="CCO",
                reactant_smiles=["CC"],
                yield_percent=72.5,
                metadata={"confidence": 0.75},
            )
        ],
    )
    db.upsert_patent(record)
    row = db.connection.execute("SELECT COUNT(*) FROM reactions").fetchone()[0]
    print("Init structure at", test_dir)
    print("Database at", db.db_path)
    print("Reactions stored:", row)
    db.close()


if __name__ == "__main__":
    main()
