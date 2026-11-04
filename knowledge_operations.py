"""Explicit source maintenance; admission quality is never an operator switch."""
import json

import knowledge_lifecycle as lifecycle
from knowledge_schema import has_table
from knowledge_store import KnowledgeError


def _lifecycle(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except lifecycle.LifecycleError as error:
        raise KnowledgeError(error.code, error.message, error.status) from None


def _admitted_original(store, connection, entry_id):
    source = store._public(connection, entry_id)
    if not store._usable({**source, 'validity': 'active'}):
        raise KnowledgeError('knowledge_not_accepted', '来源、年度或原件仍待核对，不能采用为有效知识。', 409)
    return source


def _result(store, connection, entry_id):
    return {'entry': store._public(connection, entry_id),
            'events': _lifecycle(lifecycle.inspect_events, connection, entry_id),
            'jobs': _lifecycle(lifecycle.inspect_jobs, connection, entry_id)}


def change_validity(store, entry_id, validity, reason, replacement_id=None):
    store._entry_id(entry_id)
    if not store.path.is_file():
        raise KnowledgeError('unknown_knowledge', '没有找到这份知识库资料。', 404)
    with store._lock, store._connection(create=True) as connection:
        store._public(connection, entry_id)
        if validity == 'active': _admitted_original(store, connection, entry_id)
        if validity == 'superseded':
            store._entry_id(replacement_id)
            _admitted_original(store, connection, replacement_id)
        _lifecycle(lifecycle.change_state, connection, entry_id, validity, reason, replacement_id)
        from knowledge_vector_schema import dirty
        dirty(connection,entry_id)
        return _result(store, connection, entry_id)


def choose_version(store, entry_id, reason):
    store._entry_id(entry_id)
    if not store.path.is_file():
        raise KnowledgeError('unknown_knowledge', '没有找到这份知识库资料。', 404)
    with store._lock, store._connection(create=True) as connection:
        _admitted_original(store, connection, entry_id)
        _lifecycle(lifecycle.select_current, connection, entry_id, reason)
        from knowledge_vector_schema import dirty
        dirty(connection)
        return _result(store, connection, entry_id)


def jobs(store, entry_id=None):
    if entry_id is not None: store._entry_id(entry_id)
    if not store.path.is_file():
        if entry_id: raise KnowledgeError('unknown_knowledge', '没有找到这份知识库资料。', 404)
        return {'jobs': [], 'backend': 'local_segments'}
    with store._lock, store._connection() as connection:
        if entry_id: store._public(connection, entry_id)
        if not has_table(connection, 'knowledge_index_jobs'):
            return {'entry_id': entry_id, 'jobs': [], 'backend': 'local_segments'}
        if entry_id:
            result = _lifecycle(lifecycle.inspect_jobs, connection, entry_id)
        else:
            result = [dict(row) for row in connection.execute(
                'SELECT * FROM knowledge_index_jobs ORDER BY updated_at DESC,id LIMIT 50')]
        return {'entry_id': entry_id, 'jobs': result, 'backend': 'local_segments'}


def reconcile_versions(store, connection, entry_id):
    """Different complete texts at one known source/year require an explicit choice."""
    row = connection.execute('SELECT family,year FROM knowledge_entries WHERE id=?', (entry_id,)).fetchone()
    if row is None or type(row['year']) is not int: return
    records = connection.execute('''SELECT e.id,x.text_sha256,x.metadata FROM knowledge_entries e
        JOIN knowledge_entry_state st ON st.entry_id=e.id
        JOIN knowledge_generations g ON g.id=st.current_generation_id
        JOIN knowledge_extractions x ON x.id=g.extraction_id
        WHERE e.family=? AND e.year=? AND e.status='official_original_read'
        AND e.quality='accepted' AND st.validity IN ('active','conflict') ''', (row['family'], row['year'])).fetchall()
    candidates = [item for item in records
                  if json.loads(item['metadata']).get('extraction_status') == 'complete'
                  and store._usable({**store._public(connection, item['id']), 'validity': 'active'})]
    if len({item['text_sha256'] for item in candidates}) < 2: return
    for item in candidates:
        _lifecycle(lifecycle.change_state, connection, item['id'], 'conflict',
                   '同一原文范围与年度出现不同完整提取版本，需要核对后明确采用。')


def versions(store, connection, entry_id):
    row = connection.execute('SELECT family,year FROM knowledge_entries WHERE id=?', (entry_id,)).fetchone()
    identifiers = connection.execute('''SELECT id FROM knowledge_entries WHERE family=? AND year IS ?
        ORDER BY CASE WHEN id=? THEN 0 ELSE 1 END,last_seen_at DESC,id LIMIT 20''',
        (row['family'], row['year'],entry_id)).fetchall()
    keys = ('id','title','year','sha256','validity','quality','status','usable','last_seen_at')
    result = []
    for item in identifiers:
        source = store._public(connection, item['id'])
        result.append({**{key:source.get(key) for key in keys}, 'is_this_entry': item['id'] == entry_id})
    return result
