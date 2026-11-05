"""Local task journal; reads never invoke an AI model or external source."""
from datetime import datetime, timezone
from contextlib import contextmanager
import json
from pathlib import Path
import re
import sqlite3
import threading
import uuid


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


class ResearchError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def identity(value):
    if not isinstance(value, str) or not re.fullmatch(r'r_[0-9a-f]{32}', value):
        raise ResearchError('invalid_research', '研究任务编号不正确。')
    return value


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


class ResearchStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connect() as connection:
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS research_tasks (
                    id TEXT PRIMARY KEY, status TEXT NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS research_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, research_id TEXT NOT NULL,
                    payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS research_events_task ON research_events(research_id,id);
            ''')
            rows=connection.execute("SELECT id,payload FROM research_tasks WHERE status IN ('running','queued')").fetchall()
            for identifier,payload in rows:
                value={**json.loads(payload),'status':'interrupted','updated_at':now(),
                       'error':{'code':'process_restarted','message':'上次服务已结束；可以继续研究，系统不会自动重启付费调用。'}}
                connection.execute('UPDATE research_tasks SET status=?,updated_at=?,payload=? WHERE id=?',
                                   ('interrupted',value['updated_at'],_encoded(value),identifier))

    @contextmanager
    def _connect(self):
        connection=sqlite3.connect(self.path, timeout=5)
        connection.execute('PRAGMA busy_timeout=5000')
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def create(self, request, status):
        stamp=now();identifier='r_'+uuid.uuid4().hex
        value={'id':identifier,'research_id':identifier,'query':request['query'],'profile':request.get('profile',{}),
               'unit_ids':request.get('unit_ids',[]),'follow_ups':[],'status':status,'created_at':stamp,'updated_at':stamp,
               'target_year':request.get('profile',{}).get('target_year',2028),
               'reference_year':request.get('profile',{}).get('reference_year',2027),
               'turn_id':1,'confirmed_fields':request.get('confirmed_fields',[]),
               'budget':{},'usage':{},'report':{},'error':None}
        with self._lock,self._connect() as connection:
            connection.execute('INSERT INTO research_tasks VALUES(?,?,?,?,?)',
                               (identifier,status,stamp,stamp,_encoded(value)))
        return self.get(identifier)

    def get(self, identifier, after_event=0):
        identity(identifier)
        if type(after_event) is not int or after_event<0 or after_event>2**63-1:
            raise ResearchError('invalid_cursor','研究事件位置不正确。')
        with self._lock,self._connect() as connection:
            row=connection.execute('SELECT payload FROM research_tasks WHERE id=?',(identifier,)).fetchone()
            if row is None:raise ResearchError('unknown_research','没有找到这项研究任务。',404)
            events=connection.execute('SELECT id,payload FROM research_events WHERE research_id=? AND id>? ORDER BY id LIMIT 200',
                                      (identifier,after_event)).fetchall()
            last=connection.execute('SELECT coalesce(max(id),0) FROM research_events WHERE research_id=?',(identifier,)).fetchone()[0]
        cursor=events[-1][0] if events else min(after_event,last)
        task=json.loads(row[0])
        return {**task,'turn_id':task.get('turn_id',0),'events':[{'id':item[0],**json.loads(item[1])} for item in events],
                'last_event_id':cursor,'latest_event_id':last,'has_more_events':cursor<last}

    def update(self, identifier, **updates):
        identity(identifier)
        with self._lock,self._connect() as connection:
            row=connection.execute('SELECT payload FROM research_tasks WHERE id=?',(identifier,)).fetchone()
            if row is None:raise ResearchError('unknown_research','没有找到这项研究任务。',404)
            value={**json.loads(row[0]),**updates,'updated_at':now()}
            connection.execute('UPDATE research_tasks SET status=?,updated_at=?,payload=? WHERE id=?',
                               (value['status'],value['updated_at'],_encoded(value),identifier))
        return self.get(identifier)

    def event(self, identifier, stage, title, message, *, event_type='progress', data=None):
        identity(identifier)
        value={'type':event_type,'stage':stage,'title':title,'message':message,'created_at':now(),'data':data or {}}
        with self._lock,self._connect() as connection:
            task=connection.execute('SELECT payload FROM research_tasks WHERE id=?',(identifier,)).fetchone()
            if task is None:raise ResearchError('unknown_research','没有找到这项研究任务。',404)
            value={**value,'turn_id':json.loads(task[0]).get('turn_id',0)}
            encoded=_encoded(value)
            if len(encoded)>65536:raise ValueError('event size')
            cursor=connection.execute('INSERT INTO research_events(research_id,payload) VALUES(?,?)',(identifier,encoded))
            return cursor.lastrowid

    def delete(self, identifier):
        identity(identifier)
        with self._lock,self._connect() as connection:
            row=connection.execute('SELECT status FROM research_tasks WHERE id=?',(identifier,)).fetchone()
            if row is None:raise ResearchError('unknown_research','没有找到这项研究任务。',404)
            connection.execute('DELETE FROM research_events WHERE research_id=?',(identifier,))
            connection.execute('DELETE FROM research_tasks WHERE id=?',(identifier,))
        return {'deleted':True,'research_id':identifier}

    def list(self, limit=20):
        if type(limit) is not int or not 1<=limit<=100:
            raise ResearchError('invalid_query','研究列表数量应为1至100。')
        with self._lock,self._connect() as connection:
            rows=connection.execute('SELECT payload FROM research_tasks ORDER BY updated_at DESC,rowid DESC LIMIT ?',(limit,)).fetchall()
            total=connection.execute('SELECT count(*) FROM research_tasks').fetchone()[0]
        return {'tasks':[json.loads(row[0]) for row in rows],'total':total}
