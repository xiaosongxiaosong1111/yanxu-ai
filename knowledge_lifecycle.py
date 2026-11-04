"""Source validity and local index jobs; callers own transactions and raw-SHA checks."""
from datetime import datetime, timezone
import hashlib
import json
import re

from knowledge_schema import has_table


VALIDITIES = ('active', 'revoked', 'conflict', 'superseded')
_ACQUIRED = ('official_original_read', 'published_snapshot')
_SECRET = re.compile(r'\b(?:sk-[A-Za-z0-9_-]{8,}|AKID[A-Za-z0-9]{12,}|Bearer\s+\S{6,}|'
                     r'TC3-HMAC-SHA256\s+Credential\s*=|secret[_-]?(?:id|key)["\']?\s*[:=]\s*["\']?\S{4,})', re.I)


class LifecycleError(ValueError):
    def __init__(self, code, message, status=409):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _stamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _identifier(value, prefix, width):
    if not isinstance(value, str) or re.fullmatch(prefix + r'[0-9a-f]{' + str(width) + '}', value) is None:
        raise LifecycleError('invalid_knowledge_identity', '知识或索引任务编号不正确。', 400)
    return value


def _records(connection, sql, values=()):
    cursor = connection.execute(sql, values)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _entry(connection, entry_id):
    _identifier(entry_id, 'k_', 24)
    rows = _records(connection, 'SELECT * FROM knowledge_entries WHERE id=?', (entry_id,))
    if not rows:
        raise LifecycleError('unknown_knowledge', '没有找到这份知识资料。', 404)
    return rows[0]


def _schema(connection):
    names = ('knowledge_entry_state', 'knowledge_index_jobs', 'knowledge_generations',
             'knowledge_extractions', 'knowledge_state_events')
    if not all(has_table(connection, name) for name in names):
        raise LifecycleError('knowledge_schema_required', '请先显式升级知识库，再修改有效状态或索引任务。')


def _reason(value):
    if (not isinstance(value, str) or not value.strip() or len(value) > 500
            or _SECRET.search(value) or any(ord(char) < 32 for char in value)):
        raise LifecycleError('invalid_knowledge_reason', '请填写不含凭据、长度合适的状态变更理由。', 400)
    return value.strip()


def _trusted(connection, entry_id):
    entry = _entry(connection, entry_id)
    try:
        quality = json.loads(entry.get('payload', '{}')).get('quality', {})
    except (ValueError, AttributeError):
        quality = {}
    quality = quality if isinstance(quality, dict) else {}
    if (entry.get('quality') != 'accepted' or type(quality.get('version')) is not int or quality['version'] != 1
            or quality.get('state') != 'accepted' or quality.get('reusable') is not True
            or entry.get('status') not in _ACQUIRED
            or any(not isinstance(entry.get(key), str) or not entry[key].strip() for key in ('unit_id', 'family', 'url'))
            or type(entry.get('year')) is not int or not 1900 <= entry['year'] <= 2100):
        raise LifecycleError('knowledge_not_accepted', '资料尚未通过来源与年度准入，不能激活或自动建立可信索引。')
    return entry


def state_for(connection, entry_id):
    _entry(connection, entry_id)
    default = {'entry_id': entry_id, 'validity': 'active', 'reason': '', 'replaced_by': None,
               'current_generation_id': None, 'updated_at': None}
    if not has_table(connection, 'knowledge_entry_state'):
        return default
    rows = _records(connection, 'SELECT * FROM knowledge_entry_state WHERE entry_id=?', (entry_id,))
    result = rows[0] if rows else default
    if result['validity'] not in VALIDITIES:
        raise LifecycleError('invalid_knowledge_state', '知识有效状态需要复核，暂不允许自动复用。')
    return result


def _active(connection, entry_id):
    state = state_for(connection, entry_id)
    if state['validity'] != 'active':
        raise LifecycleError('knowledge_inactive', '资料已撤信、冲突或被替代，不能继续索引或复用。')
    return state


def _same_scope(left, right):
    return (left['id'] != right['id'] and bool(left.get('family')) and bool(left.get('unit_id'))
            and type(left.get('year')) is int and left.get('family') == right.get('family')
            and left.get('unit_id') == right.get('unit_id') and left.get('year') == right.get('year')
            and left.get('url') == right.get('url'))


def _peers(connection, entry):
    return _records(connection, '''SELECT e.id,e.quality,e.status,COALESCE(st.validity,'active') AS validity
        FROM knowledge_entries e LEFT JOIN knowledge_entry_state st ON st.entry_id=e.id
        WHERE e.family=? AND e.unit_id=? AND e.year IS ? AND e.url=? AND e.id<>?''',
        (entry['family'], entry['unit_id'], entry['year'], entry['url'], entry['id']))


def _cancel_jobs(connection, entry_id, stamp):
    connection.execute('''UPDATE knowledge_index_jobs SET state='cancelled',error_code='source_inactive',updated_at=?
        WHERE entry_id=? AND state IN ('pending','running')''', (stamp, entry_id))
    connection.execute("UPDATE knowledge_generations SET state='cancelled' WHERE entry_id=? AND state IN ('pending','running')",
                       (entry_id,))


def _write_state(connection, previous, validity, reason, replacement_id, action):
    if all(previous.get(key) == value for key, value in (
            ('validity', validity), ('reason', reason), ('replaced_by', replacement_id))):
        return previous
    stamp = _stamp()
    connection.execute('''INSERT INTO knowledge_entry_state
        (entry_id,validity,reason,replaced_by,current_generation_id,updated_at) VALUES(?,?,?,?,?,?)
        ON CONFLICT(entry_id) DO UPDATE SET validity=excluded.validity,reason=excluded.reason,
        replaced_by=excluded.replaced_by,updated_at=excluded.updated_at''',
        (previous['entry_id'], validity, reason, replacement_id, previous['current_generation_id'], stamp))
    connection.execute('''INSERT INTO knowledge_state_events
        (entry_id,action,previous_state,new_state,reason,replaced_by,created_at) VALUES(?,?,?,?,?,?,?)''',
        (previous['entry_id'], action, previous['validity'], validity, reason, replacement_id, stamp))
    if validity != 'active':
        _cancel_jobs(connection, previous['entry_id'], stamp)
    return state_for(connection, previous['entry_id'])


def change_state(connection, entry_id, validity, reason, replacement_id=None):
    _schema(connection)
    previous = state_for(connection, entry_id)
    reason = _reason(reason)
    if validity not in VALIDITIES:
        raise LifecycleError('invalid_knowledge_state', '知识有效状态不正确。', 400)
    if validity == 'active':
        entry = _trusted(connection, entry_id)
        if any(peer['validity'] == 'active' and peer['quality'] == 'accepted' and peer['status'] in _ACQUIRED
               for peer in _peers(connection, entry)):
            raise LifecycleError('knowledge_version_conflict', '同范围另有有效版本，请明确选择采用的版本。')
    if validity == 'superseded':
        left, right = _entry(connection, entry_id), _trusted(connection, replacement_id)
        if not _same_scope(left, right):
            raise LifecycleError('incompatible_knowledge_scope', '只能替代相同学校、原文范围和实际年度的版本。')
        _active(connection, replacement_id)
    elif replacement_id is not None:
        raise LifecycleError('invalid_knowledge_replacement', '仅替代状态允许指定对应版本。', 400)
    return _write_state(connection, previous, validity, reason, replacement_id, 'change_validity')


def select_current(connection, entry_id, reason):
    _schema(connection)
    entry, reason = _trusted(connection, entry_id), _reason(reason)
    for peer in _peers(connection, entry):
        if peer['validity'] in {'active', 'conflict', 'superseded'}:
            _write_state(connection, state_for(connection, peer['id']), 'superseded', reason, entry_id, 'select_current')
    return _write_state(connection, state_for(connection, entry_id), 'active', reason, None, 'select_current')


def _version(value):
    if not isinstance(value, str) or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}', value) is None:
        raise LifecycleError('invalid_segmenter_version', '分段版本标识不正确。', 400)
    return value


def _extraction(connection, extraction_id, entry_id):
    _identifier(extraction_id, 'ex_', 32)
    rows = _records(connection, 'SELECT * FROM knowledge_extractions WHERE id=?', (extraction_id,))
    if not rows or rows[0]['entry_id'] != entry_id:
        raise LifecycleError('unknown_extraction', '该提取版本不属于指定知识资料。', 404)
    return rows[0]


def _job(connection, job_id):
    _schema(connection)
    _identifier(job_id, 'kj_', 32)
    rows = _records(connection, 'SELECT * FROM knowledge_index_jobs WHERE id=?', (job_id,))
    if not rows:
        raise LifecycleError('unknown_index_job', '没有找到这个本地索引任务。', 404)
    return rows[0]


def _job_id(entry_id, extraction_id, segmenter_version):
    parts = json.dumps((entry_id, extraction_id, segmenter_version), ensure_ascii=False, separators=(',', ':'))
    return 'kj_' + hashlib.sha256(parts.encode('utf-8')).hexdigest()[:32]


def queue_job(connection, entry_id, extraction_id, segmenter_version, *, retry=False):
    _schema(connection)
    _trusted(connection, entry_id); _active(connection, entry_id)
    _extraction(connection, extraction_id, entry_id); _version(segmenter_version)
    if type(retry) is not bool:
        raise LifecycleError('invalid_index_retry', '重试标记不正确。', 400)
    identifier, stamp = _job_id(entry_id, extraction_id, segmenter_version), _stamp()
    connection.execute('''INSERT OR IGNORE INTO knowledge_entry_state
        (entry_id,validity,reason,replaced_by,current_generation_id,updated_at) VALUES(?,'active','',NULL,NULL,?)''',
        (entry_id, stamp))
    _trusted(connection, entry_id); _active(connection, entry_id)
    connection.execute('''INSERT OR IGNORE INTO knowledge_index_jobs
        (id,entry_id,extraction_id,segmenter_version,state,attempts,error_code,generation_id,created_at,updated_at)
        VALUES(?,?,?,?,'pending',0,'',NULL,?,?)''',
        (identifier, entry_id, extraction_id, segmenter_version, stamp, stamp))
    job = _job(connection, identifier)
    if retry and job['state'] in {'failed', 'cancelled'}:
        connection.execute("UPDATE knowledge_index_jobs SET state='pending',error_code='',updated_at=? WHERE id=? AND state IN ('failed','cancelled')",
                           (stamp, identifier))
    return _job(connection, identifier)


def claim_job(connection, job_id):
    job = _job(connection, job_id)
    _trusted(connection, job['entry_id']); _active(connection, job['entry_id'])
    changed = connection.execute('''UPDATE knowledge_index_jobs SET state='running',attempts=attempts+1,
        error_code='',updated_at=? WHERE id=? AND state='pending' AND attempts=?''',
        (_stamp(), job_id, job['attempts'])).rowcount
    if changed != 1:
        raise LifecycleError('index_job_not_pending', '该索引任务已被领取或已结束，不能重复启动。')
    return _job(connection, job_id)


def _attempt(job, attempt):
    if type(attempt) is not int or attempt < 1 or job['attempts'] != attempt:
        raise LifecycleError('stale_index_attempt', '这是已失效的索引执行轮次，不能覆盖当前任务。')


def fail_job(connection, job_id, error_code, *, attempt):
    job = _job(connection, job_id); _attempt(job, attempt)
    if not isinstance(error_code, str) or re.fullmatch(r'[a-z][a-z0-9_]{0,63}', error_code) is None:
        raise LifecycleError('invalid_index_error', '索引错误标识不正确。', 400)
    if job['state'] in {'failed', 'cancelled'}:
        return job
    if job['state'] != 'running':
        raise LifecycleError('index_job_not_running', '索引任务未在执行，不能改写失败结果。')
    changed = connection.execute("UPDATE knowledge_index_jobs SET state='failed',error_code=?,updated_at=? WHERE id=? AND state='running' AND attempts=?",
                                 (error_code, _stamp(), job_id, attempt)).rowcount
    if changed != 1:
        raise LifecycleError('stale_index_attempt', '索引任务已由其他执行轮次接管，不能改写结果。')
    connection.execute('''UPDATE knowledge_generations SET state='failed' WHERE entry_id=? AND extraction_id=?
        AND segmenter_version=? AND state IN ('pending','running')''',
        (job['entry_id'], job['extraction_id'], job['segmenter_version']))
    return _job(connection, job_id)


def _generation(connection, job, generation_id):
    _identifier(generation_id, 'kg_', 32)
    rows = _records(connection, 'SELECT * FROM knowledge_generations WHERE id=?', (generation_id,))
    if not rows or any(rows[0][key] != job[key] for key in ('entry_id', 'extraction_id', 'segmenter_version')):
        raise LifecycleError('incompatible_index_generation', '索引代次与任务的来源、提取或分段版本不一致。')
    if rows[0]['state'] not in {'running', 'succeeded'}:
        raise LifecycleError('index_generation_not_ready', '索引代次未完成有效处理，不能设为当前代次。')
    return rows[0]


def finish_job(connection, job_id, generation_id, *, attempt):
    job = _job(connection, job_id); _attempt(job, attempt)
    if job['state'] == 'succeeded' and job['generation_id'] == generation_id:
        return job
    if job['state'] != 'running':
        raise LifecycleError('index_job_not_running', '索引任务已结束或取消，旧任务不能重新激活资料。')
    _trusted(connection, job['entry_id']); _active(connection, job['entry_id'])
    _generation(connection, job, generation_id)
    stamp = _stamp()
    changed = connection.execute('''UPDATE knowledge_index_jobs SET state='succeeded',generation_id=?,error_code='',updated_at=?
        WHERE id=? AND state='running' AND attempts=?''', (generation_id, stamp, job_id, attempt)).rowcount
    if changed != 1:
        raise LifecycleError('stale_index_attempt', '索引任务状态已变化，旧执行不能切换当前代次。')
    _trusted(connection, job['entry_id']); _active(connection, job['entry_id'])
    connection.execute("UPDATE knowledge_generations SET state='succeeded' WHERE id=?", (generation_id,))
    changed = connection.execute('''UPDATE knowledge_entry_state SET current_generation_id=?,updated_at=?
        WHERE entry_id=? AND validity='active' ''', (generation_id, stamp, job['entry_id'])).rowcount
    if changed != 1:
        raise LifecycleError('knowledge_inactive', '资料有效状态已变化，索引不能设为当前代次。')
    return _job(connection, job_id)


def _limit(value):
    if type(value) is not int or not 1 <= value <= 100:
        raise LifecycleError('invalid_index_limit', '检查数量必须是1至100的整数。', 400)
    return value


def inspect_jobs(connection, entry_id, *, limit=20):
    _entry(connection, entry_id); _limit(limit)
    if not has_table(connection, 'knowledge_index_jobs'):
        return []
    return _records(connection, 'SELECT * FROM knowledge_index_jobs WHERE entry_id=? ORDER BY updated_at DESC,id DESC LIMIT ?',
                    (entry_id, limit))


def inspect_events(connection, entry_id, *, limit=20):
    _entry(connection, entry_id); _limit(limit)
    if not has_table(connection, 'knowledge_state_events'):
        return []
    return _records(connection, 'SELECT * FROM knowledge_state_events WHERE entry_id=? ORDER BY id DESC LIMIT ?',
                    (entry_id, limit))
