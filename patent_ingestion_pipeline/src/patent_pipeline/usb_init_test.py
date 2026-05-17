"""Quick test for USB storage helper (uses local test folder)."""
from patent_pipeline.storage_usb import init_usb_structure, get_db_connection, save_parsed_json, example_db_schema
import os

TEST_DIR = os.path.abspath("./test_usb_mount")

if __name__ == '__main__':
    base = init_usb_structure(TEST_DIR)
    print('Init structure at', base)
    conn = get_db_connection(TEST_DIR)
    example_db_schema(conn)
    sample = {
        'patent_id': 'US-TEST-0001',
        'title': 'Test Patent',
        'abstract': 'This is a test patent for unit testing.',
        'source_url': 'https://example.com/patent/1',
        'publication_date': '2026-05-17',
        'metadata': {'source': 'test'},
        'reactions': [
            {'product_smiles': 'CCO', 'reactant_smiles': 'CC', 'yield_percent': 72.5, 'temperature_celsius': 80, 'metadata': {'confidence': 0.75}}
        ]
    }
    path = save_parsed_json(sample['patent_id'], sample, TEST_DIR)
    print('Saved parsed JSON to', path)
    from patent_pipeline.storage_usb import insert_parsed_meta
    insert_parsed_meta(conn, sample, path)
    print('Inserted metadata to SQLite at', get_db_connection(TEST_DIR).execute('PRAGMA database_list;').fetchall())
    conn.close()
