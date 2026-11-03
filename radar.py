"""Observe official catalog changes without changing admissions or release eligibility."""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from collectors.catalogs import catalog_status
from collectors.documents import decode_text
from service_verification import SourceVerifier, canonical, official_for_unit

_CONTEXTS, _CONTEXT_LOCK = {}, threading.Lock()
FIELDS = ('subjects', 'planned_enrollment', 'tuition', 'duration_years', 'conditions', 'notes')


def _records(value):
    return list(value.values()) if isinstance(value, dict) else list(value)


def _evidence_ids(program):
    items = program.get('evidence', [])
    items = [items] if isinstance(items, dict) else items
    exam = program.get('exam_evidence')
    return {item.get('document_id') for item in [*(items or []), *([exam] if isinstance(exam, dict) else [])]
            if isinstance(item, dict)}


def _key(program):
    direction = str(program.get('direction', ''))
    code = re.match(r'[0-9A-Z]{2}', direction)
    return '|'.join(canonical(part) for part in (
        program.get('unit_id'), program.get('year'), program.get('department_code') or program.get('department'),
        program.get('code'), code.group() if code else direction, program.get('study_mode')))


def _clean(value):
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items() if key not in {'evidence', 'exam_evidence'}}
    if isinstance(value, list): return [_clean(item) for item in value]
    if isinstance(value, str): return ' '.join(value.split())
    return value


def _row(program):
    fields = {('duration' if field == 'duration_years' else field): _clean(program.get(field)) for field in FIELDS}
    fields['subjects'] = sorted(fields['subjects'] or [], key=lambda item: (item.get('exam_unit', 0), item.get('code', '')))
    return {'id': program['id'], 'key': _key(program), 'fields': fields, 'signature': SourceVerifier._signature(program)}


class RadarService:
    poll_interval = 3600
    interval_seconds = 3600

    def __init__(self, data_dir, fetcher=None, clock=None):
        self.root = Path(data_dir).resolve()
        self.runtime = self.root / 'runtime'
        self.path = self.runtime / 'radar.json'
        self.snapshots = self.runtime / 'radar_snapshots'
        self.verifier = SourceVerifier(self.root)
        self.fetcher = fetcher
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        with _CONTEXT_LOCK:
            self.context = _CONTEXTS.setdefault(str(self.path), {'lock': threading.RLock(), 'check_lock': threading.Lock(), 'running': False})
        self.lock = self.context['lock']

    def _now(self):
        value = self.clock()
        return value.isoformat(timespec='seconds') if isinstance(value, datetime) else str(value)

    def _read(self):
        if not self.path.exists():
            return {'schema_version': 1, 'watches': [], 'baselines': {}, 'observations': {}, 'events': [],
                    'last_checked_at': None, 'check_state': 'idle'}
        state = json.loads(self.path.read_text('utf8'))
        if not isinstance(state, dict) or state.get('schema_version') != 1:
            raise ValueError('招生雷达状态格式不正确；原文件已保留。')
        return state

    def _write(self, state):
        self.runtime.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix='radar-', suffix='.tmp', dir=self.runtime)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf8') as stream:
                json.dump(state, stream, ensure_ascii=False, allow_nan=False, indent=2)
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)

    @staticmethod
    def _ids(program_ids):
        if not isinstance(program_ids, list) or len(program_ids) > 100 or any(not isinstance(value, str) or not value or len(value) > 300 for value in program_ids):
            raise ValueError('请提供至多100个真实专业方向ID。')
        return list(dict.fromkeys(program_ids))

    def _year_confirmed(self, document, unit):
        pages = self.verifier.pages(document, unit)
        if not pages: raise ValueError('原始官方文件缺失、哈希不符或无法提取。')
        source = self.verifier.read(document)[1]
        if source.startswith(b'%PDF-'):
            heading = pages[0].split('21全国统考', 1)[0][:1200]
            primary = [heading] if re.search(r'硕士|研究生|招生|目录', heading) else []
        else:
            from public_sources import _VisibleHTML
            parser = _VisibleHTML()
            parser.feed(decode_text(source, document.get('content_type', '')))
            parser.close()
            primary = [''.join(parser.heading), ''.join(parser.title)]
        years = {match for heading in primary for match in re.findall(r'(?<!\d)(20\d{2})(?!\d)', heading)}
        if years and years != {str(document['year'])}:
            raise ValueError('来源主标题出现不同或冲突年度，未将跨年目录视为同一年度计划。')
        parsed_url = urlsplit(document.get('final_url', document['url']))
        url_years = set(re.findall(r'/(20\d{2})(?=/|$)', parsed_url.path))
        query = parse_qs(parsed_url.query)
        url_years.update(value for key in ('year', 'nd', 'zsnd') for value in query.get(key, []) if re.fullmatch(r'20\d{2}', value))
        if url_years and url_years != {str(document['year'])}:
            raise ValueError('来源跳转到不同年度，未将跨年目录视为同一年度计划。')
        return bool(years or url_years)

    def _parse(self, document, unit, programs):
        verifier = self.verifier
        self._year_confirmed(document, unit)
        text = '\f'.join(verifier.pages(document, unit))
        departments = {str(p.get('department_code')): str(p.get('department_code')) + p.get('department', '')
                       for p in programs if p.get('department_code') and p.get('unit_id') == unit['id']}
        parsed = verifier._source_programs(document, unit, document['year'], departments)
        if not parsed or catalog_status(parsed, unit, document['year'])['status'] != 'verified':
            raise ValueError('目录未能完整解析；不能据此判断停招或删除方向。')
        if '21全国统考' in text and (len(re.findall(r'\([12]\)(?:全日制|非全日制)', text)) != parsed['expected_records'] or text.count('①') != parsed['expected_records']):
            raise ValueError('目录独立计数不符；不能据此判断停招。')
        if not all(verifier.program(p, {document['id']: document}, unit) for p in parsed['programs']):
            raise ValueError('专业方向或考试科目无法与原始文件逐项对账。')
        return [_row(program) for program in parsed['programs']]

    def _snapshot(self, document, raw, rows):
        sha = hashlib.sha256(raw).hexdigest()
        extension = '.pdf' if raw.startswith(b'%PDF-') else '.html'
        self.snapshots.mkdir(parents=True, exist_ok=True)
        path = self.snapshots / (sha + extension)
        with self.lock:
            if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != sha:
                descriptor, temporary = tempfile.mkstemp(prefix='source-', suffix='.tmp', dir=self.snapshots)
                try:
                    with os.fdopen(descriptor, 'wb') as stream: stream.write(raw)
                    os.replace(temporary, path)
                finally:
                    if os.path.exists(temporary): os.unlink(temporary)
        metadata = {key: value for key, value in document.items() if not key.startswith('text_')}
        return {'document': {**metadata, 'id': f'radar-{sha[:24]}',
                             'source_document_id': document.get('source_document_id', document['id']),
                             'sha256': sha, 'local_path': path.relative_to(self.root).as_posix()},
                'rows': rows, 'observed_at': self._now(), 'observation_id': uuid4().hex}

    def watch(self, program_ids, *, programs, documents, units):
        ids = self._ids(program_ids)
        programs, documents, units = map(_records, (programs, documents, units))
        known = {row['id']: row for row in programs}
        if any(identity not in known for identity in ids):
            raise ValueError('订阅包含当前目录不存在的专业方向；已保留原订阅。')
        docs, schools = {d['id']: d for d in documents}, {u['id']: u for u in units}
        watches, sources = [], {}
        for identity in ids:
            program = known[identity]
            source_ids = _evidence_ids(program)
            source_documents = [docs[key] for key in source_ids if key in docs and docs[key].get('kind') == 'catalog'
                                and docs[key].get('catalog_active', True) and docs[key].get('unit_id') == program['unit_id']
                                and docs[key].get('year') == program['year']]
            unit = schools.get(program['unit_id'])
            if not unit or not source_documents or any(not official_for_unit(unit, d.get('url')) for d in source_documents):
                raise ValueError('所选方向缺少可追溯的当前官方目录来源；已保留原订阅。')
            keys = []
            for document in source_documents:
                key = hashlib.sha256(json.dumps([program['unit_id'], program['year'], document['url']], ensure_ascii=False).encode()).hexdigest()[:24]
                sources[key] = (document, unit)
                keys.append(key)
            watches.append({'program_id': identity, 'program_key': _key(program), 'unit_id': program['unit_id'],
                            'year': program['year'], 'source_keys': keys, 'source_urls': [d['url'] for d in source_documents]})
        with self.lock:
            state = self._read()
            state = {**state, 'watches': watches}
            for key, (document, unit) in sources.items():
                if key in state['baselines']: continue
                try:
                    if document.get('stale') or any(known[w['program_id']].get('stale') for w in watches if key in w['source_keys']):
                        raise ValueError('旧目录已标为过期，等待当前官方资料复核。')
                    rows = self._parse(document, unit, programs)
                    current = [p for p in programs if document['id'] in _evidence_ids(p)]
                    if Counter(json.dumps(row['signature']) for row in rows) != Counter(json.dumps(SourceVerifier._signature(p)) for p in current) or any(
                        not self.verifier.program(p, docs, unit) for p in current):
                        raise ValueError('当前保存方向与来源完整目录不一致。')
                    raw = self.verifier.read(document)[1]
                    snapshot = self._snapshot(document, raw, rows)
                    state['baselines'][key] = snapshot
                    state['observations'][key] = snapshot
                except (ValueError, OSError):
                    state['check_state'] = 'needs_review'
            self._write(state)
        return self.status()

    def status(self, *, program_ids=None):
        ids = None if program_ids is None else set(self._ids(program_ids))
        with self.lock:
            state = self._read()
            watches = [w for w in state['watches'] if ids is None or w['program_id'] in ids]
            selected = {w['program_id'] for w in watches}
            events = [e for e in state['events'] if selected.intersection(e['program_ids'])]
            sources = {key for watch in watches for key in watch['source_keys']}
            return deepcopy({'watch_count': len(watches), 'last_checked_at': state['last_checked_at'],
                             'interval_seconds': self.interval_seconds, 'running': self.context['running'],
                             'check_state': 'checking' if self.context['running'] else state['check_state'],
                             'events': list(reversed(events)), 'unread_count': sum(e.get('unread', False) for e in events),
                             'baseline_count': len(sources.intersection(state['baselines'])),
                             'program_ids': [w['program_id'] for w in watches],
                             'limitations': ['仅运行服务期间定期检查订阅来源；无系统任务、邮件或站外推送。',
                                             '变化只存为观察记录，不自动修改招生资料、报考资格或全国完整性门槛。',
                                             '仅支持已完整解析的校方目录；抓取失败、结构变化和跨年内容须人工复核。']})

    def _event(self, state, kind, key, watches, before, after, changes=None, message=''):
        identity = hashlib.sha256(json.dumps([kind, key, before['document']['sha256'] if before else None,
                                             before.get('observation_id') if before else None,
                                             after['document']['sha256'] if after else None, changes, message], sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
        for index, event in enumerate(state['events']):
            if event['id'] == identity:
                state['events'] = [*state['events'][:index], {**event, 'program_ids': sorted(
                    {*event['program_ids'], *(w['program_id'] for w in watches)})}, *state['events'][index + 1:]]
                return
        state['events'] = [*state['events'], {'id': identity, 'type': kind, 'created_at': self._now(),
                         'program_ids': sorted({w['program_id'] for w in watches}), 'changes': changes or {},
                         'evidence': {'before': before['document'] if before else None, 'after': after['document'] if after else None},
                         'message': message, 'unread': kind != 'source_updated'}][-500:]

    def _compare(self, state, key, watches, before, after, year_confirmed=True):
        if not year_confirmed:
            signatures = [json.dumps({row['key']: [row['fields'], row['signature']] for row in snapshot['rows']},
                                     sort_keys=True, ensure_ascii=False) for snapshot in (before, after)]
            if signatures[0] != signatures[1]:
                raise ValueError('新版目录发生实质变化，但原文及来源无法确认招生年度；仅保留两版证据待复核，未判定同年停招或新增。')
        old, new = ({row['key']: row for row in snapshot['rows']} for snapshot in (before, after))
        emitted = False
        for watch in watches:
            left, right = old.get(watch['program_key']), new.get(watch['program_key'])
            if left and right:
                changes = {field: {'before': left['fields'][field], 'after': right['fields'][field]}
                           for field in left['fields'] if left['fields'][field] != right['fields'][field]}
                if changes:
                    self._event(state, 'program_changed', key, [watch], before, after, changes, '官方目录字段变化，请核对前后两版原文。')
                    emitted = True
            elif left and not right:
                self._event(state, 'direction_missing', key, [watch], before, after,
                            {'direction': {'before': left['id'], 'after': None}}, '同年度完整目录未见原方向，请向学校确认招生安排。')
                emitted = True
        added = sorted(set(new) - set(old))
        if added:
            self._event(state, 'directions_added', key, watches, before, after,
                        {'directions': {'before': [], 'after': [new[item]['id'] for item in added]}}, '订阅来源的同年度目录出现新方向。')
            emitted = True
        if not emitted:
            self._event(state, 'source_updated', key, watches, before, after, message='来源文件已更新，订阅方向未发现实质字段变化。')

    def check(self, *, programs, documents, units, stop_event=None):
        if not self.context['check_lock'].acquire(blocking=False): return self.status()
        try:
            with self.lock:
                self.context['running'] = True
                state = self._read()
                watches = deepcopy(state['watches'])
            programs, documents, units = map(_records, (programs, documents, units))
            schools = {u['id']: u for u in units}
            missing = any(key not in state['baselines'] for watch in watches for key in watch['source_keys'])
            if missing:
                with self.lock:
                    watches = deepcopy(self._read()['watches'])
                    if all(any(p['id'] == watch['program_id'] for p in programs) for watch in watches):
                        self.watch([w['program_id'] for w in watches], programs=programs, documents=documents, units=units)
            source_keys = {key for watch in watches for key in watch['source_keys']}
            outcomes = []
            for key in sorted(source_keys):
                if stop_event is not None and stop_event.is_set():
                    outcomes.append('cancelled')
                    break
                affected = [w for w in watches if key in w['source_keys']]
                with self.lock:
                    before = self._read()['observations'].get(key)
                unit = schools.get(affected[0]['unit_id'])
                if not before or not unit:
                    outcomes.append('needs_review')
                    continue
                document = before['document']
                after = None
                try:
                    fetcher = self.fetcher
                    if fetcher is None:
                        from public_sources import fetch_official_document
                        fetcher = fetch_official_document
                    fetched = fetcher(document['url'], unit)
                    if stop_event is not None and stop_event.is_set():
                        outcomes.append('cancelled')
                        break
                    raw = fetched['raw']
                    if not isinstance(raw, bytes) or not raw or len(raw) > 8 * 1024 * 1024:
                        raise ValueError('官方文件为空或超过8MB下载上限。')
                    url = fetched.get('url', document['url'])
                    if not official_for_unit(unit, url): raise ValueError('来源跳转超出本校官方域名。')
                    updated = {**document, 'final_url': url, 'content_type': fetched.get('content_type', ''), 'checked_at': self._now()}
                    after = self._snapshot(updated, raw, [])
                    year_confirmed = self._year_confirmed(after['document'], unit)
                    if after['document']['sha256'] == document['sha256']:
                        outcomes.append('complete')
                        continue
                    rows = self._parse(after['document'], unit, programs)
                    after = {**after, 'rows': rows}
                    with self.lock:
                        current = self._read()
                        active = [w for w in current['watches'] if key in w['source_keys']]
                        if active:
                            self._compare(current, key, active, before, after, year_confirmed=year_confirmed)
                            current['observations'][key] = after
                            self._write(current)
                    outcomes.append('complete')
                except Exception as error:
                    kind = 'needs_review' if isinstance(error, ValueError) else 'check_failed'
                    message = str(error) if isinstance(error, ValueError) else f'官方来源检查失败：{type(error).__name__}。原基线已保留。'
                    with self.lock:
                        current = self._read()
                        active = [w for w in current['watches'] if key in w['source_keys']]
                        if active:
                            self._event(current, kind, key, active, before, after, message=message)
                            self._write(current)
                    outcomes.append(kind)
            with self.lock:
                current = self._read()
                current['last_checked_at'] = self._now()
                current['check_state'] = ('cancelled' if 'cancelled' in outcomes else 'check_failed' if 'check_failed' in outcomes
                                          else 'needs_review' if 'needs_review' in outcomes else 'complete')
                self._write(current)
        finally:
            with self.lock: self.context['running'] = False
            self.context['check_lock'].release()
        return self.status()
