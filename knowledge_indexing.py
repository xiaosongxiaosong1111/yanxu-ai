"""Explicit, offline local reindexing; a failed attempt cannot replace usable content."""
import json
import hashlib
import sqlite3
from urllib.parse import urlsplit

from document_extraction import canonical_document
from knowledge_quality import assess_quality
import knowledge_lifecycle as lifecycle
import knowledge_repository as repository
from knowledge_operations import _lifecycle
from knowledge_store import KnowledgeError, _stamp
from public_sources import extract_saved_document, SourceError
from source_privacy import contains_personal_roster


def _media(source):
    suffix = urlsplit(source['url']).path.lower().rsplit('.',1)[-1]
    return {'docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'xls':'application/vnd.ms-excel','doc':'application/msword'}.get(suffix,'')


def _quality_after_extraction(store,connection,source,canonical):
    value={**source,'excerpt':'','text_origin':'full_extraction','text_truncated':canonical['text_truncated']}
    quality=assess_quality(value,canonical['text'],original_available=True,authority_verified=True,pages=canonical['pages'])
    if quality['state']=='accepted':return quality
    payload=json.loads(connection.execute('SELECT payload FROM knowledge_entries WHERE id=?',(source['id'],)).fetchone()[0])
    payload={**payload,'quality':quality,'excerpt':''}
    connection.execute('UPDATE knowledge_entries SET quality=?,payload=? WHERE id=?',
                       (quality['state'],json.dumps(payload,ensure_ascii=False),source['id']))
    store._index(connection,source['id'],payload,'')
    return quality


def _reextract(store, connection, source):
    if not source['original_available']:
        return None
    proof=store._authority(source,available=True)
    if proof['status']!='verified':
        raise KnowledgeError('knowledge_authority_required','原件归属尚待核对，不能重新建立可信索引。',409)
    try:
        with store._blob_path(source['sha256']).open('rb') as stream:raw=stream.read(8*1024*1024+1)
        if len(raw)>8*1024*1024 or hashlib.sha256(raw).hexdigest()!=source['sha256']:
            raise KnowledgeError('invalid_knowledge_snapshot','原件在读取期间发生变化，请重新核对来源。',409)
        document = extract_saved_document(raw,
                    _media(source), source['url'], timeout=15)
        canonical = canonical_document(document)
    except SourceError as error:
        raise KnowledgeError(error.code, error.message, 409) from None
    except (OSError,ValueError,TypeError):
        raise KnowledgeError('knowledge_reextract_failed','原件暂时无法重新提取，原有资料仍保留。',409) from None
    if contains_personal_roster(source['title'],canonical['text'],*(page['text'] for page in canonical['pages'])):
        _quality_after_extraction(store,connection,source,canonical)
        return {'excluded':True}
    quality=_quality_after_extraction(store,connection,source,canonical)
    if quality['state']!='accepted':
        repository.persist_document(connection,source['id'],{**source,'quality':quality},canonical,_stamp())
        return {'review_required':True}
    return canonical


def _attempt(store, connection, source, document):
    stamp=_stamp()
    extraction_id=repository._extraction(connection,source['id'],source,document,stamp)
    job=_lifecycle(lifecycle.queue_job,connection,source['id'],extraction_id,repository.SEGMENT_VERSION,retry=True)
    connection.execute('SAVEPOINT local_index_attempt')
    try:
        repository.persist_document(connection,source['id'],source,document,stamp)
        connection.execute('RELEASE SAVEPOINT local_index_attempt')
        return False
    except (sqlite3.Error,ValueError,TypeError):
        connection.execute('ROLLBACK TO SAVEPOINT local_index_attempt')
        connection.execute('RELEASE SAVEPOINT local_index_attempt')
        running=_lifecycle(lifecycle.claim_job,connection,job['id'])
        _lifecycle(lifecycle.fail_job,connection,job['id'],'local_index_failed',attempt=running['attempts'])
        return True


def rebuild(store, entry_id):
    store._entry_id(entry_id)
    if not store.path.is_file():
        raise KnowledgeError('unknown_knowledge','没有找到这份知识库资料。',404)
    failed=False
    with store._lock,store._connection(create=True) as connection:
        source=store._public(connection,entry_id)
        if not store._usable(source):
            raise KnowledgeError('knowledge_not_reusable','原文尚不可复用，不能重建可信索引。',409)
        document=repository.get_document(connection,entry_id)
        if document is None:document=_reextract(store,connection,source)
        if document is None:
            return {'entry_id':entry_id,'state':'needs_reextract','rebuilt':False}
        if document.get('excluded'):
            return {'entry_id':entry_id,'state':'excluded','rebuilt':False}
        if document.get('review_required'):
            return {'entry_id':entry_id,'state':'needs_review','rebuilt':False,**repository.state_summary(connection,entry_id)}
        failed=_attempt(store,connection,source,document)
        # A rebuild can change the current generation or admission state, so the
        # derived vector index must be marked stale for this entry (light hook only;
        # the N3 structure index semantics are untouched).
        from knowledge_vector_schema import dirty
        dirty(connection,entry_id)
        summary=repository.state_summary(connection,entry_id)
        state=summary.get('latest_index_job',{}).get('state',summary['index_state'])
        result={'entry_id':entry_id,'rebuilt':not failed and state=='succeeded',**summary,'state':state}
    if failed:
        raise KnowledgeError('knowledge_reindex_failed','本地索引重建失败，已保留原可用版本，可稍后重试。',503)
    return result
