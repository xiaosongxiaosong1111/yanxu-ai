"""Transactional knowledge schema, including independently versioned derivations."""
import sqlite3


BASE_SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS knowledge_entries(
        id TEXT PRIMARY KEY,family TEXT NOT NULL,version_key TEXT NOT NULL,
        url TEXT NOT NULL,unit_id TEXT NOT NULL,unit_name TEXT NOT NULL,
        year INTEGER,requested_year INTEGER,status TEXT NOT NULL,title TEXT NOT NULL,
        sha256 TEXT NOT NULL,full_text TEXT NOT NULL,pages TEXT NOT NULL,
        first_seen_at TEXT NOT NULL,last_seen_at TEXT NOT NULL,payload TEXT NOT NULL,
        quality TEXT NOT NULL DEFAULT 'pending',UNIQUE(family,version_key))''',
    'CREATE INDEX IF NOT EXISTS knowledge_scope ON knowledge_entries(status,year,unit_id)',
    '''CREATE TABLE IF NOT EXISTS knowledge_provenance(
        entry_id TEXT NOT NULL,research_id TEXT NOT NULL,PRIMARY KEY(entry_id,research_id))''',
)
DERIVATION_SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS knowledge_extractions(
        id TEXT PRIMARY KEY,entry_id TEXT NOT NULL REFERENCES knowledge_entries(id),
        raw_sha TEXT NOT NULL,extractor_version TEXT NOT NULL,text_sha256 TEXT NOT NULL,
        text TEXT NOT NULL,pages TEXT NOT NULL,metadata TEXT NOT NULL,created_at TEXT NOT NULL,
        UNIQUE(entry_id,raw_sha,extractor_version,text_sha256))''',
    '''CREATE TABLE IF NOT EXISTS knowledge_generations(
        id TEXT PRIMARY KEY,entry_id TEXT NOT NULL REFERENCES knowledge_entries(id),
        extraction_id TEXT NOT NULL REFERENCES knowledge_extractions(id),
        segmenter_version TEXT NOT NULL,state TEXT NOT NULL,created_at TEXT NOT NULL,
        UNIQUE(entry_id,extraction_id,segmenter_version))''',
    '''CREATE TABLE IF NOT EXISTS knowledge_segments(
        id TEXT PRIMARY KEY,generation_id TEXT NOT NULL REFERENCES knowledge_generations(id),
        entry_id TEXT NOT NULL REFERENCES knowledge_entries(id),extraction_id TEXT NOT NULL,
        ordinal INTEGER NOT NULL,kind TEXT NOT NULL,char_start INTEGER NOT NULL,
        char_end INTEGER NOT NULL,text TEXT NOT NULL,search_text TEXT NOT NULL,
        page_start INTEGER,page_end INTEGER,searchable INTEGER NOT NULL,payload TEXT NOT NULL,
        UNIQUE(generation_id,ordinal))''',
    'CREATE INDEX IF NOT EXISTS knowledge_segment_generation ON knowledge_segments(entry_id,generation_id,ordinal)',
    '''CREATE TABLE IF NOT EXISTS knowledge_entry_state(
        entry_id TEXT PRIMARY KEY REFERENCES knowledge_entries(id),validity TEXT NOT NULL DEFAULT 'active',
        reason TEXT NOT NULL DEFAULT '',replaced_by TEXT,current_generation_id TEXT,updated_at TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS knowledge_index_jobs(
        id TEXT PRIMARY KEY,entry_id TEXT NOT NULL,extraction_id TEXT NOT NULL,segmenter_version TEXT NOT NULL,
        state TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,error_code TEXT NOT NULL DEFAULT '',
        generation_id TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
        UNIQUE(entry_id,extraction_id,segmenter_version))''',
    '''CREATE TABLE IF NOT EXISTS knowledge_state_events(
        id INTEGER PRIMARY KEY AUTOINCREMENT,entry_id TEXT NOT NULL,action TEXT NOT NULL,
        previous_state TEXT NOT NULL,new_state TEXT NOT NULL,reason TEXT NOT NULL,
        replaced_by TEXT,created_at TEXT NOT NULL)''',
)


def has_table(connection, name):
    return connection.execute('SELECT 1 FROM sqlite_master WHERE type=\'table\' AND name=?', (name,)).fetchone() is not None


def ensure_schema(connection):
    if not connection.in_transaction:
        connection.execute('BEGIN')
    for statement in BASE_SCHEMA:
        connection.execute(statement)
    if 'quality' not in {row[1] for row in connection.execute('PRAGMA table_info(knowledge_entries)')}:
        connection.execute("ALTER TABLE knowledge_entries ADD COLUMN quality TEXT NOT NULL DEFAULT 'pending'")
    connection.execute('CREATE INDEX IF NOT EXISTS knowledge_quality_state ON knowledge_entries(quality,status,year,unit_id)')
    for statement in DERIVATION_SCHEMA:
        connection.execute(statement)
    _ensure_fts(connection)
    # Vector tables are a separate explicit derivation: ordinary N3 saves must not
    # create them. Only an explicit vector build (knowledge_vector_store) does.


def _ensure_fts(connection):
    existed = has_table(connection, 'knowledge_fts')
    try:
        connection.execute("CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(entry_id UNINDEXED,title,unit_name,body,tokenize='trigram')")
        if not existed:
            connection.execute("INSERT INTO knowledge_fts SELECT id,title,unit_name,full_text FROM knowledge_entries WHERE quality<>'excluded'")
        connection.execute("CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_segments_fts USING fts5(segment_id UNINDEXED,body,tokenize='trigram')")
    except sqlite3.OperationalError:
        # SQLite builds without FTS5/trigram use parameterized substring search.
        pass
