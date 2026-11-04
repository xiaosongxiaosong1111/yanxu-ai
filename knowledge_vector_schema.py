"""Vector derivations are separate from the N3 structure-generation state."""

SCHEMA=(
    '''CREATE TABLE IF NOT EXISTS knowledge_vector_spaces(
        id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL UNIQUE,profile_id TEXT NOT NULL,
        provider TEXT NOT NULL,model TEXT NOT NULL,dimension INTEGER NOT NULL,metadata TEXT NOT NULL,
        state TEXT NOT NULL,expected_count INTEGER NOT NULL DEFAULT 0,completed_count INTEGER NOT NULL DEFAULT 0,
        error_code TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS knowledge_vector_items(
        space_id TEXT NOT NULL,item_id TEXT NOT NULL,segment_id TEXT NOT NULL,entry_id TEXT NOT NULL,
        generation_id TEXT NOT NULL,input_sha TEXT NOT NULL,char_start INTEGER NOT NULL,char_end INTEGER NOT NULL,
        state TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(space_id,item_id))''',
    '''CREATE TABLE IF NOT EXISTS knowledge_vector_jobs(
        space_id TEXT NOT NULL,item_id TEXT NOT NULL,entry_id TEXT NOT NULL,state TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,lease_token TEXT,lease_until REAL,error_code TEXT NOT NULL DEFAULT '',
        usage TEXT NOT NULL DEFAULT '{}',usage_known INTEGER NOT NULL DEFAULT 0,updated_at TEXT NOT NULL,
        PRIMARY KEY(space_id,item_id))''',
    '''CREATE TABLE IF NOT EXISTS knowledge_vector_control(
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),active_space_id TEXT,revision INTEGER NOT NULL DEFAULT 0,
        dirty INTEGER NOT NULL DEFAULT 1,build_id TEXT,build_until REAL,last_build TEXT NOT NULL DEFAULT '{}')''',
    '''INSERT OR IGNORE INTO knowledge_vector_control(singleton) VALUES(1)''',
    'CREATE INDEX IF NOT EXISTS knowledge_vector_entry ON knowledge_vector_items(entry_id,generation_id)',
)


def ensure_vector_schema(connection):
    for statement in SCHEMA:connection.execute(statement)


def dirty(connection, entry_id=None):
    exists=connection.execute("SELECT 1 FROM sqlite_master WHERE name='knowledge_vector_control'").fetchone()
    if not exists:return
    connection.execute('UPDATE knowledge_vector_control SET dirty=1,revision=revision+1 WHERE singleton=1')
    if entry_id:
        connection.execute("UPDATE knowledge_vector_jobs SET state='cancelled',error_code='source_changed' WHERE entry_id=? AND state IN ('pending','running')",(entry_id,))
