"""SQLite authority, vector task leases and safe serving-space transitions."""
from contextlib import contextmanager
import json
import sqlite3
import time
import uuid

from knowledge_schema import has_table
from knowledge_store import KnowledgeError, _stamp
import knowledge_repository as repository
from knowledge_vector_cache import identifier
from knowledge_vector_schema import ensure_vector_schema


@contextmanager
def connection(store, create=False):
    with store._lock,store._connection(create=create) as handle:
        if create:ensure_vector_schema(handle)
        yield handle


def eligible(store, unit_id='',year=None,entry_id=None):
    if not store.path.is_file():return []
    with connection(store) as handle:
        if not repository.available(handle):return []
        where=["e.quality='accepted'","st.validity='active'","g.state='succeeded'","s.searchable=1"]
        args=[]
        for clause,value in [('e.unit_id=?',unit_id),('e.year=?',year),('e.id=?',entry_id)]:
            if value is not None and value!='':where.append(clause);args.append(value)
        rows=handle.execute('''SELECT s.* FROM knowledge_segments s
            JOIN knowledge_entries e ON e.id=s.entry_id JOIN knowledge_entry_state st
            ON st.entry_id=e.id AND st.current_generation_id=s.generation_id
            JOIN knowledge_generations g ON g.id=s.generation_id WHERE '''+' AND '.join(where),args).fetchall()
        sources={};result=[]
        for row in rows:
            if row['entry_id'] not in sources:sources[row['entry_id']]=store._public(handle,row['entry_id'])
            source=sources[row['entry_id']]
            if not store._usable(source) or (source.get('extraction') or {}).get('generation_id')!=row['generation_id']:continue
            full=repository.segment_row(handle,row['id'])
            public=repository.public_segment(full,source)
            contexts=repository.read_window(handle,full,source,0,0,20000)['contexts']
            result.append({**public,'embedding_text':row['search_text'],'contexts':contexts})
        return result


def summary(store):
    empty={'status':'not_ready','backend':'milvus_lite','active_space_id':None,'active_profile_id':None,
           'active_provider':None,'spaces':[],'jobs':{key:0 for key in ('pending','running','succeeded','failed','cancelled','uncertain')},
           'dirty':True,'building':False,'error_code':'','last_build':{}}
    if not store.path.is_file():return empty
    with connection(store) as handle:
        if not has_table(handle,'knowledge_vector_control'):return empty
        control=dict(handle.execute('SELECT * FROM knowledge_vector_control WHERE singleton=1').fetchone())
        spaces=[dict(row) for row in handle.execute('SELECT id,profile_id,provider,model,dimension,state,expected_count,completed_count,error_code FROM knowledge_vector_spaces ORDER BY updated_at DESC,id')]
        active=next((row for row in spaces if row['id']==control['active_space_id']),None)
        counts={row[0]:row[1] for row in handle.execute('SELECT state,count(*) FROM knowledge_vector_jobs GROUP BY state')}
        building=bool(control['build_id'] and (control['build_until'] or 0)>time.time())
        return {**empty,'status':'building' if building else 'ready' if active else 'failed' if any(row['state']=='failed' for row in spaces) else 'not_ready',
                'spaces':spaces,'active_space_id':control['active_space_id'],
                'active_profile_id':active['profile_id'] if active else None,'active_provider':active['provider'] if active else None,
                'jobs':{**empty['jobs'],**counts},'dirty':bool(control['dirty']),'building':building,
                'build_id':control['build_id'] if building else None,
                'error_code':next((row['error_code'] for row in spaces if row['error_code']),''),
                'last_build':json.loads(control['last_build'])}


def begin_build(store, metadata, profile_id, build_id):
    space_id=identifier('vs_',metadata['fingerprint'])
    with connection(store,True) as handle:
        control=dict(handle.execute('SELECT * FROM knowledge_vector_control WHERE singleton=1').fetchone())
        if control['build_id'] and (control['build_until'] or 0)>time.time():
            raise KnowledgeError('vector_build_running','已有向量索引构建正在执行。',409)
        handle.execute('UPDATE knowledge_vector_control SET build_id=?,build_until=? WHERE singleton=1',
                       (build_id,time.time()+300))
        stamp=_stamp()
        handle.execute('''INSERT INTO knowledge_vector_spaces(id,fingerprint,profile_id,provider,model,dimension,metadata,state,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,'staging',?,?) ON CONFLICT(id) DO UPDATE SET error_code='',updated_at=excluded.updated_at''',
            (space_id,metadata['fingerprint'],profile_id,metadata['provider'],metadata['model'],metadata['dimension'],json.dumps(metadata),stamp,stamp))
        row=dict(handle.execute('SELECT * FROM knowledge_vector_spaces WHERE id=?',(space_id,)).fetchone())
        return {**row,'metadata':metadata},control['revision']


def space(store, space_id):
    with connection(store) as handle:
        row=handle.execute('SELECT * FROM knowledge_vector_spaces WHERE id=?',(space_id,)).fetchone()
        return {**dict(row),'metadata':json.loads(row['metadata'])} if row else None


def register_items(store, space_id, items):
    stamp=_stamp()
    with connection(store,True) as handle:
        for item in items:
            handle.execute('''INSERT INTO knowledge_vector_items VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(space_id,item_id) DO NOTHING''',(space_id,item['id'],item['segment_id'],item['entry_id'],
                item['generation_id'],item['input_sha'],item['char_start'],item['char_end'],'pending',stamp))
            handle.execute('''INSERT OR IGNORE INTO knowledge_vector_jobs(space_id,item_id,entry_id,state,updated_at)
                VALUES(?,?,?,'pending',?)''',(space_id,item['id'],item['entry_id'],stamp))
        handle.execute('UPDATE knowledge_vector_spaces SET expected_count=?,updated_at=? WHERE id=?',(len(items),stamp,space_id))


def claim(store, space_id, item_id, build_id):
    """CAS a job into this attempt; returns (lease_token, previous_state).

    A running job can only be taken over after its lease expired, and an expired
    build round can no longer extend the control lease — stale workers stop early.
    """
    token=uuid.uuid4().hex
    with connection(store,True) as handle:
        row=handle.execute('SELECT state FROM knowledge_vector_jobs WHERE space_id=? AND item_id=?',
                           (space_id,item_id)).fetchone()
        changed=handle.execute('''UPDATE knowledge_vector_jobs SET state='running',attempts=attempts+1,lease_token=?,lease_until=?,error_code='',updated_at=?
            WHERE space_id=? AND item_id=? AND (state<>'running' OR lease_until IS NULL OR lease_until<?)''',
            (token,time.time()+120,_stamp(),space_id,item_id,time.time())).rowcount
        if changed!=1:raise KnowledgeError('vector_job_running','该索引任务已由另一执行轮次领取。',409)
        if handle.execute('UPDATE knowledge_vector_control SET build_until=? WHERE singleton=1 AND build_id=?',
                          (time.time()+300,build_id)).rowcount!=1:
            raise KnowledgeError('stale_vector_build','索引构建轮次已失效。',409)
        return token,row['state'] if row else 'pending'


def finish_item(store,space_id,item,state,token,usage=None,usage_known=False,error_code=''):
    """CAS completion; an expired or taken-over lease may never finish its work."""
    with connection(store,True) as handle:
        row=handle.execute('SELECT lease_token,state,lease_until FROM knowledge_vector_jobs WHERE space_id=? AND item_id=?',(space_id,item['id'])).fetchone()
        if (not row or row['lease_token']!=token or row['state']!='running'
                or not row['lease_until'] or row['lease_until']<=time.time()):return False
        handle.execute('UPDATE knowledge_vector_jobs SET state=?,usage=?,usage_known=?,error_code=?,updated_at=? WHERE space_id=? AND item_id=?',
                       (state,json.dumps(usage or {}),int(usage_known),error_code,_stamp(),space_id,item['id']))
        handle.execute('UPDATE knowledge_vector_items SET state=?,updated_at=? WHERE space_id=? AND item_id=?',
                       (state,_stamp(),space_id,item['id']))
        return True


def completed_items(store,space_id,segments):
    wanted={row['id']:row for row in segments}
    with connection(store) as handle:
        rows=handle.execute("SELECT * FROM knowledge_vector_items WHERE space_id=? AND state='succeeded'",(space_id,)).fetchall()
        return [dict(row) for row in rows if row['segment_id'] in wanted and row['generation_id']==wanted[row['segment_id']]['generation_id']]


def finish_build(store,space_id,build_id,revision,items,stats,error='',profile_id=None):
    """Close a build round. Only a fully verified round may switch the serving
    pointer; a same-fingerprint round then adopts its (possibly renewed) serving
    profile, while a failed round keeps the old space and its bound model."""
    with connection(store,True) as handle:
        control=handle.execute('SELECT * FROM knowledge_vector_control WHERE singleton=1').fetchone()
        if control['build_id']!=build_id:raise KnowledgeError('stale_vector_build','索引构建轮次已失效。',409)
        states={row[0]:row[1] for row in handle.execute('SELECT item_id,state FROM knowledge_vector_items WHERE space_id=?',(space_id,))}
        complete=sum(states.get(item['id'])=='succeeded' for item in items)
        if not error and complete!=len(items):error='source_changed'
        if not error and control['revision']!=revision:error='source_changed'
        state='ready' if not error else 'cancelled' if error=='cancelled' else 'failed'
        handle.execute('UPDATE knowledge_vector_spaces SET state=?,completed_count=?,error_code=?,updated_at=? WHERE id=?',
                       (state,complete,'' if error in ('','cancelled') else error,_stamp(),space_id))
        handle.execute('UPDATE knowledge_vector_control SET build_id=NULL,build_until=NULL,last_build=? WHERE singleton=1',(json.dumps(stats),))
        if not error:
            handle.execute('UPDATE knowledge_vector_control SET active_space_id=?,dirty=0 WHERE singleton=1',(space_id,))
            if profile_id:handle.execute('UPDATE knowledge_vector_spaces SET profile_id=? WHERE id=?',(profile_id,space_id))
        return error
