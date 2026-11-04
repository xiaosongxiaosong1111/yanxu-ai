"""Versioned full text and exact source spans; source trust stays in KnowledgeStore."""
from copy import deepcopy
import hashlib
import json
import sqlite3

from knowledge_schema import has_table
from knowledge_segments import build_segments, SEGMENT_VERSION


def identity(prefix, *parts):
    value = json.dumps(parts, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return prefix + hashlib.sha256(value).hexdigest()[:32]


def available(connection):
    return all(has_table(connection, name) for name in (
        'knowledge_extractions', 'knowledge_generations', 'knowledge_segments', 'knowledge_entry_state'))


def _extraction(connection, entry_id, source, document, stamp):
    identifier = identity('ex_', entry_id, source['sha256'], document['extractor_version'], document['text_sha256'])
    metadata = {key: value for key, value in document.items() if key not in {'text', 'pages', 'raw'}}
    connection.execute('''INSERT OR IGNORE INTO knowledge_extractions
        (id,entry_id,raw_sha,extractor_version,text_sha256,text,pages,metadata,created_at)
        VALUES(?,?,?,?,?,?,?,?,?)''', (identifier, entry_id, source['sha256'], document['extractor_version'],
        document['text_sha256'], document['text'], json.dumps(document['pages'], ensure_ascii=False),
        json.dumps(metadata, ensure_ascii=False), stamp))
    return identifier


def _checked_segments(document):
    segments = build_segments(document, max_chars=1800)
    ordinals = set()
    for segment in segments:
        start, end, ordinal = segment.get('char_start'), segment.get('char_end'), segment.get('ordinal')
        if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(document['text'])
                or type(ordinal) is not int or ordinal < 0 or ordinal in ordinals
                or segment.get('text') != document['text'][start:end] or end-start > 1800):
            raise ValueError('Invalid segment derivation.')
        ordinals.add(ordinal)
    return segments


def _write_segments(connection, entry_id, extraction_id, generation_id, document):
    segments = _checked_segments(document)
    identifiers = {segment['ordinal']: identity('sg_', entry_id, extraction_id, SEGMENT_VERSION,
                   segment['ordinal'], segment['char_start'], segment['char_end']) for segment in segments}
    for segment in segments:
        identifier = identifiers[segment['ordinal']]
        payload = {**segment, 'parent_id': identifiers.get(segment.get('parent_ordinal')),
                   'previous_id': identifiers.get(segment.get('previous_ordinal')),
                   'next_id': identifiers.get(segment.get('next_ordinal'))}
        values = (identifier, generation_id, entry_id, extraction_id, segment['ordinal'], segment['kind'],
                  segment['char_start'], segment['char_end'], segment['text'], segment.get('search_text', segment['text']),
                  segment.get('page_start'), segment.get('page_end'), int(segment.get('searchable', True)),
                  json.dumps(payload, ensure_ascii=False))
        connection.execute('''INSERT OR REPLACE INTO knowledge_segments
            (id,generation_id,entry_id,extraction_id,ordinal,kind,char_start,char_end,text,search_text,
             page_start,page_end,searchable,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', values)
        if has_table(connection, 'knowledge_segments_fts'):
            connection.execute('DELETE FROM knowledge_segments_fts WHERE segment_id=?', (identifier,))
            if segment.get('searchable', True):
                connection.execute('INSERT INTO knowledge_segments_fts VALUES(?,?)', (identifier, values[9]))
    return len(segments)


def persist_document(connection, entry_id, source, document, stamp):
    extraction_id = _extraction(connection, entry_id, source, document, stamp)
    generation_id = identity('kg_', entry_id, extraction_id, SEGMENT_VERSION)
    job_id = identity('kj_', entry_id, extraction_id, SEGMENT_VERSION)
    connection.execute('''INSERT OR IGNORE INTO knowledge_entry_state
        (entry_id,validity,reason,replaced_by,current_generation_id,updated_at) VALUES(?,'active','',NULL,NULL,?)''',
        (entry_id, stamp))
    validity = connection.execute('SELECT validity FROM knowledge_entry_state WHERE entry_id=?', (entry_id,)).fetchone()[0]
    prior = connection.execute('SELECT state FROM knowledge_generations WHERE id=?', (generation_id,)).fetchone()
    if prior and prior[0] == 'succeeded':
        return extraction_id
    connection.execute('''INSERT OR REPLACE INTO knowledge_generations
        (id,entry_id,extraction_id,segmenter_version,state,created_at) VALUES(?,?,?,?,'running',?)''',
        (generation_id, entry_id, extraction_id, SEGMENT_VERSION, stamp))
    connection.execute('''INSERT INTO knowledge_index_jobs
        (id,entry_id,extraction_id,segmenter_version,state,attempts,error_code,generation_id,created_at,updated_at)
        VALUES(?,?,?,?,'running',1,'',?,?,?) ON CONFLICT(id) DO UPDATE SET
        state='running',attempts=knowledge_index_jobs.attempts+1,error_code='',updated_at=excluded.updated_at''',
        (job_id, entry_id, extraction_id, SEGMENT_VERSION, generation_id, stamp, stamp))
    if validity != 'active':
        _finish_job(connection, generation_id, job_id, 'cancelled', stamp)
        return extraction_id
    if document.get('extraction_status') not in {'complete','partial'} or not document.get('text'):
        _finish_job(connection,generation_id,job_id,'failed',stamp,'extraction_'+document.get('extraction_status','empty'))
        return extraction_id
    _write_segments(connection, entry_id, extraction_id, generation_id, document)
    _finish_job(connection, generation_id, job_id, 'succeeded', stamp)
    connection.execute('UPDATE knowledge_entry_state SET current_generation_id=?,updated_at=? WHERE entry_id=? AND validity=\'active\'',
                       (generation_id, stamp, entry_id))
    return extraction_id


def _finish_job(connection, generation_id, job_id, state, stamp,error_code=''):
    connection.execute('UPDATE knowledge_generations SET state=? WHERE id=?', (state, generation_id))
    connection.execute('UPDATE knowledge_index_jobs SET state=?,updated_at=?,error_code=? WHERE id=?', (state, stamp,error_code, job_id))


def state_summary(connection, entry_id):
    if not available(connection):return {'validity': 'active', 'index_state': 'unindexed', 'extraction': None}
    row = connection.execute('''SELECT st.validity,st.reason,st.replaced_by,st.current_generation_id,
        g.segmenter_version,g.state,x.* FROM knowledge_entry_state st
        LEFT JOIN knowledge_generations g ON g.id=st.current_generation_id
        LEFT JOIN knowledge_extractions x ON x.id=g.extraction_id WHERE st.entry_id=?''', (entry_id,)).fetchone()
    if row is None:return {'validity': 'active', 'index_state': 'unindexed', 'extraction': None}
    result = {'validity': row['validity'], 'validity_reason': row['reason'], 'replaced_by': row['replaced_by'],
              'index_state': row['state'] or 'unindexed', 'extraction': None}
    if row['id']:result['extraction']=_extraction_summary(connection,row,row['current_generation_id'],row['segmenter_version'])
    else:
        latest=connection.execute('SELECT * FROM knowledge_extractions WHERE entry_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1',(entry_id,)).fetchone()
        if latest:result['extraction']=_extraction_summary(connection,latest,None,None)
    job=connection.execute('SELECT id,state,error_code,attempts,generation_id FROM knowledge_index_jobs WHERE entry_id=? ORDER BY updated_at DESC,rowid DESC LIMIT 1',(entry_id,)).fetchone()
    if job:
        result['latest_index_job']=dict(job)
        if not row['id']:result['index_state']=job['state']
    return result


def _extraction_summary(connection,row,generation_id,segmenter_version):
    metadata=json.loads(row['metadata'])
    count=connection.execute('SELECT count(*) FROM knowledge_segments WHERE generation_id=?',(generation_id,)).fetchone()[0]
    return {**{key:metadata.get(key) for key in ('format','page_kind','extraction_status')},
        'id':row['id'],'generation_id':generation_id,'extractor_version':row['extractor_version'],
        'segmenter_version':segmenter_version,'text_sha256':row['text_sha256'],
        'text_length':len(row['text']),'segment_count':count,'offset_units':'unicode_codepoints'}


def get_document(connection, entry_id):
    if not available(connection):return None
    row = connection.execute('''SELECT x.* FROM knowledge_entry_state st
        JOIN knowledge_generations g ON g.id=st.current_generation_id
        JOIN knowledge_extractions x ON x.id=g.extraction_id WHERE st.entry_id=?''', (entry_id,)).fetchone()
    if row is None:return None
    return {**json.loads(row['metadata']), 'text': row['text'], 'pages': json.loads(row['pages']),
            'extractor_version': row['extractor_version'], 'text_sha256': row['text_sha256']}


def segment_row(connection, identifier):
    if not available(connection):return None
    return connection.execute('''SELECT s.*,x.text AS extraction_text,x.pages AS extraction_pages,x.metadata AS extraction_metadata,
        st.validity,st.current_generation_id,g.state AS generation_state
        FROM knowledge_segments s JOIN knowledge_extractions x ON x.id=s.extraction_id
        JOIN knowledge_entry_state st ON st.entry_id=s.entry_id
        JOIN knowledge_generations g ON g.id=s.generation_id WHERE s.id=?''', (identifier,)).fetchone()


def query_rows(connection, *, query, entry_id, unit_id, year, quality, page, page_size):
    where,args=_scope(query,entry_id,unit_id,year,quality)
    terms=query.split()[:8]
    if terms and all(len(term) >= 3 for term in terms) and has_table(connection, 'knowledge_segments_fts'):
        condition = 's.id IN (SELECT segment_id FROM knowledge_segments_fts WHERE knowledge_segments_fts MATCH ?)'
        value = ' AND '.join('"' + term.replace('"', '""') + '"' for term in terms)
        try:
            result=_query(connection,[*where,condition],[*args,value],page,page_size)
            if result[1]:return (*result,'segment_terms')
        except sqlite3.OperationalError:pass
    result=_query(connection,where,args,page,page_size)
    if result[1] or len(terms)<2:return (*result,'segment_terms')
    where,args=_document_scope(query,entry_id,unit_id,year,quality)
    rank=' + '.join('CASE WHEN instr(lower(s.search_text),lower(?))>0 THEN 1 ELSE 0 END' for _ in terms)
    return (*_query(connection,where,args,page,page_size,rank,terms),'document_terms')


def _scope(query,entry_id,unit_id,year,quality,include_terms=True):
    where = ["st.validity='active'", "g.state='succeeded'", 's.searchable=1']
    args = []
    for clause, value in [('s.entry_id=?', entry_id), ('e.unit_id=?', unit_id), ('e.year=?', year)]:
        if value is not None and value != '':where.append(clause);args.append(value)
    if quality != 'all':where.append('e.quality=?');args.append(quality)
    for term in query.split()[:8] if include_terms else []:
        where.append('instr(lower(s.search_text),lower(?))>0');args.append(term)
    return where,args


def _document_scope(query,entry_id,unit_id,year,quality):
    terms=query.split()[:8]
    where,args=_scope(query,entry_id,unit_id,year,quality,include_terms=False)
    if not terms:return where,args
    clauses=['instr(lower(x.text),lower(?))>0' for _ in terms]
    where.append('EXISTS (SELECT 1 FROM knowledge_extractions x WHERE x.id=s.extraction_id AND '+' AND '.join(clauses)+')')
    args.extend(terms)
    where.append('('+' OR '.join('instr(lower(s.search_text),lower(?))>0' for _ in terms)+')')
    args.extend(terms)
    return where,args


def matching_sources(connection, *, query,entry_id,unit_id,year,quality):
    where,args=_document_scope(query,entry_id,unit_id,year,quality)
    return connection.execute('SELECT DISTINCT s.entry_id '+_joins(where),args).fetchall()


def _query(connection, where, args, page, page_size, rank='',rank_args=()):
    joins=_joins(where)
    total = connection.execute('SELECT count(*) ' + joins, args).fetchone()[0]
    order=(rank+' DESC,' if rank else '')+'e.last_seen_at DESC,e.id,s.ordinal'
    rows=connection.execute('SELECT s.id '+joins+' ORDER BY '+order+' LIMIT ? OFFSET ?',
                            [*args,*rank_args,page_size,(page-1)*page_size]).fetchall()
    return rows, total


def _joins(where):
    return '''FROM knowledge_segments s JOIN knowledge_entries e ON e.id=s.entry_id
        JOIN knowledge_entry_state st ON st.entry_id=s.entry_id AND st.current_generation_id=s.generation_id
        JOIN knowledge_generations g ON g.id=s.generation_id WHERE ''' + ' AND '.join(where)


def public_segment(row, source):
    metadata = json.loads(row['extraction_metadata'])
    payload = {key:value for key,value in json.loads(row['payload']).items() if key!='search_text'}
    payload['context_spans']=[{key:value for key,value in span.items() if key!='text'}
                              for span in payload.get('context_spans',[])]
    keys = ('id', 'url', 'title', 'unit_id', 'unit_name', 'year', 'requested_year', 'status', 'sha256',
            'quality', 'validity', 'usable', 'original_available', 'snapshot_status', 'field_verification')
    return {**payload, 'id': row['id'], 'entry_id': row['entry_id'], 'extraction_id': row['extraction_id'],
            'generation_id': row['generation_id'], 'source': {key: deepcopy(source.get(key)) for key in keys},
            'format': metadata.get('format'), 'page_kind': metadata.get('page_kind'),
            'text_sha256': metadata.get('text_sha256'), 'offset_units': 'unicode_codepoints'}


def read_window(connection, row, source, before, after, max_chars):
    selected = public_segment(row, source)
    contexts = []
    for span in selected.get('context_spans', []):
        start, end = span.get('char_start'), span.get('char_end')
        if type(start) is int and type(end) is int and 0 <= start < end <= len(row['extraction_text']):
            contexts.append({**span, 'text': row['extraction_text'][start:end],
                             **_span_location(row, start, end)})
    neighbors = connection.execute('''SELECT id FROM knowledge_segments WHERE generation_id=?
        AND ordinal BETWEEN ? AND ? AND id<>? ORDER BY ordinal''',
        (row['generation_id'], row['ordinal']-before, row['ordinal']+after, row['id'])).fetchall()
    items = [public_segment(segment_row(connection, item['id']), source) for item in neighbors]
    budget = max_chars
    selected, budget, clipped = _clip(selected, budget)
    kept_contexts, budget, context_clip = _bounded(contexts, budget)
    kept_neighbors, budget, neighbor_clip = _bounded(items, budget)
    selected = {**selected, **_span_location(row, selected['char_start'], selected['char_end'])}
    kept_contexts = [{**item, **_span_location(row, item['char_start'], item['char_end'])} for item in kept_contexts]
    kept_neighbors = [{**item, **_span_location(row, item['char_start'], item['char_end'])} for item in kept_neighbors]
    return {'segment': selected, 'contexts': kept_contexts, 'neighbors': kept_neighbors,
            'returned_chars': max_chars-budget, 'truncated': clipped or context_clip or neighbor_clip,
            'offset_units': 'unicode_codepoints'}


def _span_location(row, start, end):
    pages = json.loads(row['extraction_pages'])
    matching = [page['page'] for page in pages
                if page['char_start'] < end and page['char_end'] > start]
    return {'page_start': matching[0] if matching else None,
            'page_end': matching[-1] if matching else None}


def _clip(value, budget):
    original = value.get('text', '')
    text = original[:budget]
    result = {**value, 'text': text, 'char_end': value['char_start'] + len(text)}
    if len(text) < len(original):result.update(full_char_end=value['char_end'], content_truncated=True)
    return result, budget-len(text), len(text) < len(original)


def _bounded(values, budget):
    result = [];truncated = False
    for value in values:
        if budget <= 0:return result, budget, True
        clipped, budget, partial = _clip(value, budget)
        result.append(clipped);truncated = truncated or partial
    return result, budget, truncated
