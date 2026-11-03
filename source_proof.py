"""Research missing public evidence without changing active admission data."""
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading

from public_sources import SourceError, document_attribution, fetch_official_document, official_source_url


KINDS = {'charter': '报考条件与招生章程', 'fees': '学费与学制', 'enrollment': '招生人数与推免口径',
         'score': '复试分数线', 'transfer': '调剂办法', 'change': '考试科目调整'}
_LOCKS, _LOCK_GUARD = {}, threading.Lock()


class ProofError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _lock_for(path):
    with _LOCK_GUARD:
        return _LOCKS.setdefault(str(path.resolve()), threading.Lock())


def _scope(program, text):
    compact = re.sub(r'\s+', '', text)
    department = re.sub(r'\s+', '', str(program.get('department') or ''))
    code, name = str(program.get('code') or ''), str(program.get('name') or '')
    matched_code = bool(code and re.search(rf'(?<!\d){re.escape(code)}(?!\d)', compact))
    if not department or department not in compact or not (matched_code or name and name in compact):
        return None
    codes = set(re.findall(r'(?<!\d)\d{6}(?!\d)', text))
    if codes and codes != {code}:
        return None
    if '非全日制' in compact and program.get('study_mode') == '全日制':
        return None
    if program.get('study_mode') == '非全日制' and '全日制' in compact and '非全日制' not in compact:
        return None
    if program.get('study_mode') in {'全日制', '非全日制'} and program['study_mode'] not in compact:
        return None
    direction = _direction_scope(program, text)
    if direction is None:
        return None
    return {**{key: program.get(key) for key in ('department', 'code', 'name', 'study_mode')}, **direction}


def _direction_scope(program, text):
    target = re.sub(r'\s+', '', str(program.get('direction') or ''))
    shared = {'level': 'program', 'direction': None, 'direction_specific': False,
              'note': '原文仅证明院系专业共用条款，未单独确认目标研究方向。'}
    tokens = re.findall(r'(?<!\d)(\d{1,3})(?!\d)\s*(?:[（(](?:非)?全日制[）)]\s*)?'
                        r'([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z（）()、-]*)', text)
    tokens = [(code, name) for code, name in tokens if not re.match(r'(?:年|元|学年|全日制|非全日制|人$|名$|分$)', name)]
    match = re.fullmatch(r'(\d{1,3})?([^\d].*)', target)
    clean = lambda value: re.sub(r'(?:研究)?方向$', '', value)
    if not tokens:
        if re.search(r'不区分(?:研究)?方向|(?:所有|全部|各)(?:研究)?方向', text):
            return shared
        direction_text = text
        for key in ('department', 'name'):
            if program.get(key):
                direction_text = direction_text.replace(program[key], '')
        names = re.findall(r'(?:研究)?方向\s*[:：]\s*([^\s，。；:：()（）\d]+)', direction_text)
        names += re.findall(r'([^\s，。；:：()（）\d]{2,40}?)(?:研究)?方向', direction_text)
        names = {clean(name) for name in names if name != '研究'}
        if not names:
            return None if '方向' in direction_text else shared
        if not match or names != {clean(match.group(2))}:
            return None
        return {'level': 'direction', 'direction': program.get('direction'), 'direction_specific': True,
                'note': '原文明示的研究方向名称与目标一致，未单独确认方向代码。'}
    if not match or len(tokens) != 1:
        return None
    code, name = tokens[0]
    target_code, target_name = match.groups()
    if target_code and code != target_code or clean(name) != clean(target_name):
        return None
    return {'level': 'direction', 'direction': program.get('direction'), 'direction_specific': True,
            'note': '原文明示的研究方向与目标代码和名称一致。'}


def _values(kind, quote):
    values = []
    if kind == 'fees':
        fee = re.search(r'学费\s*(?:标准)?\s*[:：为]?\s*(\d+(?:\.\d+)?)\s*(万?)\s*元\s*(?:[/／每]\s*(?:生[/／])?\s*(学年|年|全程)|[（(]\s*(学年|年|全程)\s*[）)])', quote)
        if fee and not re.search(r'待定|暂定|另行公布|以[^。；\n]{0,60}为准', quote):
            amount = float(fee.group(1)) * (10000 if fee.group(2) else 1)
            period = fee.group(3) or fee.group(4)
            if math.isfinite(amount) and 0 <= amount <= 10000000:
                values.append({'field': 'tuition', 'value': int(amount) if amount.is_integer() else amount,
                               'billing_period': 'total' if period == '全程' else 'year', 'currency': 'CNY'})
        duration = re.search(r'学制\s*[:：为]?\s*(\d+(?:\.\d+)?)\s*年', quote)
        if duration and 0 < float(duration.group(1)) <= 20:
            values.append({'field': 'duration_years', 'value': float(duration.group(1))})
    elif kind == 'enrollment':
        count = re.search(r'(?:招生人数|招生计划|计划招生)\s*[:：为]?\s*(\d+)\s*[人名]', quote)
        if count and len(count.group(1)) <= 6:
            values.append({'field': 'enrollment', 'value': int(count.group(1)),
                           'counting_basis': '不含推免' if '不含推免' in quote else '含推免' if '含推免' in quote else 'unknown'})
    elif kind == 'score':
        score = re.search(r'(?:复试分数线|总分)\s*[:：为]?\s*(\d{3})\s*分?', quote)
        if score:
            values.append({'field': 'score_line', 'value': int(score.group(1)), 'score_basis': 'total'})
    elif any(word in quote for word in {'charter': ('报考', '学历', '资格', '同等学力'),
                                       'transfer': ('调剂',), 'change': ('调整', '变更')}[kind]):
        values.append({'field': kind + '_evidence', 'value': quote})
    return values


class SourceProofService:
    def __init__(self, data_dir, search_callback, fetcher=fetch_official_document, clock=None):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / 'runtime' / 'proofs.json'
        self.search_callback, self.fetcher = search_callback, fetcher
        self.clock = clock or (lambda: datetime.now(timezone.utc).isoformat(timespec='seconds'))
        self.lock = _lock_for(self.path)

    def _history(self):
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text('utf-8'))
            if not isinstance(data, dict) or not isinstance(data.get('checks'), list):
                raise ValueError('invalid proof history')
            return data['checks']
        except (OSError, ValueError) as error:
            raise ProofError('storage_error', '补证记录读取失败，旧记录保持原样。', 500) from error

    def _save(self, result):
        with self.lock:
            records = [*self._history(), result][-500:]
            temporary = None
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=self.path.parent,
                                                 prefix='proofs-', suffix='.tmp', delete=False) as handle:
                    temporary = Path(handle.name)
                    json.dump({'schema_version': 1, 'checks': records}, handle, ensure_ascii=False, indent=2, allow_nan=False)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
            except (OSError, ValueError, TypeError) as error:
                raise ProofError('storage_error', '补证记录保存失败，旧记录保持原样。', 500) from error
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        return result

    def tasks(self, program, documents, coverage=None):
        relevant = [d for d in documents if d.get('unit_id') == program.get('unit_id') and d.get('year') == program.get('year')]
        with self.lock:
            history = [h for h in self._history() if h.get('program_id') == program.get('id') and h.get('year') == program.get('year')]
        missing = {'charter': program.get('eligibility_evidence_complete') is not True or bool(program.get('requires_review')),
                   'fees': program.get('tuition') is None or program.get('duration_years') is None,
                   'enrollment': program.get('enrollment') is None and program.get('planned_enrollment') is None,
                   'score': not program.get('score_lines') and not program.get('score_line'),
                   'transfer': not program.get('transfer_rules'), 'change': True}
        reasons = {'charter': '资格证据尚未完整；单份章程不能自动确认所有院系、专业条件。',
                   'fees': '学费或计费口径、学制缺失，需要逐项查看官方原文。',
                   'enrollment': '招生人数及是否包含推免的统计口径待核验。',
                   'score': '缺少适用院系、专业和年度的复试线。', 'transfer': '缺少适用专业的调剂办法。',
                   'change': '检查招生科目调整公告；未发现公告不等于没有调整。'}
        rows = []
        for kind, label in KINDS.items():
            evidence = [{key: d.get(key) for key in ('id', 'title', 'url', 'year', 'kind', 'sha256', 'status')}
                        for d in relevant if d.get('kind') == kind]
            last = next((h for h in reversed(history) if h.get('kind') == kind), None)
            rows.append({'kind': kind, 'label': label,
                         'status': 'known' if not missing[kind] else 'needs_review' if evidence else 'missing',
                         'reason': reasons[kind] if missing[kind] else '已有对应字段，仍可查看证据并核对适用范围。',
                         'query': self._query(program, kind), 'known_evidence': evidence, 'last_check': last})
        return {'program_id': program['id'], 'year': program.get('year'), 'tasks': rows,
                'notice': '资料补证只提供待复核候选，不自动修改学费、资格或正式招生数据。'}

    @staticmethod
    def _query(program, kind):
        return f"{program.get('year')}年 {program.get('unit_name', '')} {KINDS[kind]}"

    def _snapshot(self, page):
        raw = page.get('raw')
        if not isinstance(raw, bytes) or hashlib.sha256(raw).hexdigest() != page.get('sha256'):
            raise SourceError('hash_mismatch', '原文字节与来源摘要不一致，不能补证。')
        folder = self.path.parent / 'proof-snapshots'
        folder.mkdir(parents=True, exist_ok=True)
        extension = '.pdf' if 'pdf' in page.get('content_type', '') else '.html'
        path = folder / (page['sha256'] + extension)
        with self.lock:
            if path.exists():
                if path.read_bytes() != raw:
                    raise SourceError('hash_mismatch', '已存原文快照校验失败。')
            else:
                path.write_bytes(raw)
        return path.relative_to(self.data_dir).as_posix()

    def _candidates(self, program, kind, page):
        title = page.get('document_title', '')
        years = set(re.findall(r'(?<!\d)20\d{2}(?!\d)', page.get('document_heading', '')))
        if not years:
            years = set(re.findall(r'(?<!\d)20\d{2}(?!\d)', title))
        if not years:
            years = set(re.findall(r'(?<!\d)20\d{2}(?!\d)', page.get('text', '')[:1000]))
        year = str(program['year'])
        if years and year not in years:
            return [], 'wrong_year', '原文适用年度与目标年度不一致。'
        if years != {year}:
            return [], 'needs_review', '原文适用年度未明确或混合多个年度，需人工确认。'
        candidates = []
        for item in page.get('pages', []):
            if not isinstance(item.get('page'), int) or item['page'] < 1 or not isinstance(item.get('text'), str):
                continue
            page_years = set(re.findall(r'(?<!\d)20\d{2}(?!\d)', item['text']))
            for line in item['text'].splitlines():
                if len(line) > 2000:
                    continue
                scope = _scope(program, line)
                other_years = set(re.findall(r'(?<!\d)20\d{2}(?!\d)', line)) - {year}
                if not scope or other_years or page_years - {year} and year not in line:
                    continue
                for value in _values(kind, line):
                    candidates.append({**value, 'quote': line, 'source_url': page['url'],
                                       'source_sha256': page['sha256'], 'year': program['year'],
                                       'page': item['page'], 'scope': scope, 'needs_review': True})
        return candidates[:30], 'needs_review', ('已找到可核对的原文候选，仍需人工复核。' if candidates
            else '原文没有同时明确目标院系专业、数值及适用口径，未填入任何值。')

    def investigate(self, program, unit, kind):
        if not isinstance(kind, str) or kind not in KINDS or not isinstance(program, dict) or not isinstance(unit, dict):
            raise ProofError('invalid_kind', '不支持的补证类型。')
        year = program.get('year')
        if isinstance(year, bool) or not isinstance(year, int) or not 2000 <= year <= 2100:
            raise ProofError('invalid_year', '补证需要明确的有效招生年度。')
        if program.get('unit_id') != unit.get('id') or not isinstance(program.get('id'), str):
            raise ProofError('invalid_unit', '专业与招生单位不匹配。')
        result = {'program_id': program['id'], 'unit_id': unit['id'], 'year': year, 'kind': kind,
                  'query': self._query(program, kind), 'checked_at': self.clock(),
                  'status': 'not_found', 'candidates': [], 'checks': [],
                  'notice': '检索摘要不作为字段证据；所有候选需要复核，资格完整标记保持原样。'}
        try:
            response = self.search_callback({'unit_id': unit['id'], 'year': year, 'kind': kind})
        except Exception as error:
            code = getattr(error, 'code', None)
            if not code:
                raise ProofError('search_failed', '补证检索失败，尚未读取任何候选来源。', 502) from error
            return self._save({**result, 'status': code, 'error': '检索服务尚未配置。' if code == 'not_configured' else '检索暂未成功，请稍后重试。'})
        if not isinstance(response, dict) or not isinstance(response.get('results'), list):
            raise ProofError('search_invalid_response', '检索返回格式不符合元宝来源列表契约。', 502)
        seen = set()
        checks, candidates = [], []
        for item in response['results'][:50]:
            url = item.get('url') if isinstance(item, dict) else None
            if not official_source_url(url, unit):
                checks.append({'url': url if isinstance(url, str) else '', 'status': 'wrong_unit', 'reason': '结果不属于所选招生单位的官方来源。'})
                continue
            if url in seen:
                continue
            if len(seen) == 3:
                break
            seen.add(url)
            try:
                page = self.fetcher(url, unit, cache_dir=self.path.parent)
                if not official_source_url(page.get('url'), unit):
                    raise SourceError('wrong_unit', '重定向后来源不属于所选招生单位。')
                snapshot_path = self._snapshot(page)
                attribution = document_attribution(page['url'], unit, page.get('text', ''),
                                                   page.get('document_title', ''), page.get('document_heading', ''))
                if attribution['status'] != 'verified':
                    checks.append({'url': page['url'], 'status': attribution['status'], 'reason': attribution['reason'],
                                   'source_sha256': page['sha256'], 'snapshot_path': snapshot_path})
                    continue
                found, status, reason = self._candidates(program, kind, page)
                checks.append({'url': page['url'], 'status': status, 'reason': reason,
                               'source_sha256': page['sha256'], 'snapshot_path': snapshot_path,
                               'document_title': page.get('document_title', ''), 'candidate_count': len(found)})
                candidates.extend(found)
            except SourceError as error:
                checks.append({'url': url, 'status': error.code, 'reason': error.message})
            except (OSError, ValueError, TypeError) as error:
                checks.append({'url': url, 'status': 'fetch_error', 'reason': '原文读取或保存失败，未补入字段。'})
        return self._save({**result, 'status': 'needs_review' if candidates or checks else 'not_found',
                           'checks': checks, 'candidates': candidates})
