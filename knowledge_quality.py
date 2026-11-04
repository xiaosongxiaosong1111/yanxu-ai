"""Deterministic source admission, independent of acquisition and field verification."""
from html import unescape
import re

from source_privacy import contains_personal_roster

QUALITY_VERSION = 1
QUALITY_STATES = ('accepted', 'pending', 'excluded')
_KINDS = (
    ('scores', r'分数线|复试基本(?:分数|要求)|初试成绩(?:基本)?要求'),
    ('catalog', r'(?:招生)?专业目录|招生目录|目录依据'),
    ('subjects', r'考试科目|初试科目|科目调整|考试大纲|参考书|考试范围'),
    ('fees', r'学费|收费|学制|奖助'),
    ('transfer', r'调剂'),
    ('interview', r'复试|录取办法'),
    ('admissions', r'招生简章|招生章程|招生办法|招生管理|报考条件|招生规定'),
)
_GATE_TITLE = re.compile(r'^(?:访问验证|安全验证|人机验证|验证码|请先登录|用户登录|访问异常|访问受限|系统维护|Access Denied|403 Forbidden)$', re.I)
_GATE_BODY = re.compile(r'请完成.{0,16}(?:验证|验证码)|验证后.{0,8}访问|访问(?:过于)?频繁|请求过于频繁|Access Denied|403 Forbidden|checking your browser', re.I)
_NOISE = re.compile(r'@font-face\s*\{|\.[\w-]+(?:\s+\w+)?\s*\{[^}]*\}|showDynClicks\s*\([^)]*\)|(?:font-family|width|height)\s*:[^;\n]+;', re.I)
_EXAM_CODE = re.compile(r'(?<!\d)(?:10\d|20\d|30\d|[4-9]\d\d)(?!\d)')
_NUMBER_CODE = re.compile(r'(?<!\d)(?:0[1-9]|1[0-4])\d{4}(?!\d)')
_SCORE_COLUMNS = re.compile(r'总分|思想政治理论|政治|外(?:国语|语)|英语(?:[一二])?|业务[课科][一二12]|专业课[一二12]|单科')
_SCORE_ROW = re.compile(r'(?P<label>(?:\d{2,6}[ \t]*)?[\u3400-\u9fff][^\n]*?)[ \t|]+(?P<values>\d{1,3}(?:\.\d+)?(?:[ \t|]+\d{1,3}(?:\.\d+)?){2,5})[ \t|]*')
_SCORE_SUBJECT_CODE = r'(?:0[1-9]|1[0-4])\d{0,4}(?:[A-Z]\d{1,2})?'
_BRACKETED_SUBJECT_CODES = re.compile(r'[（(]\s*' + _SCORE_SUBJECT_CODE +
                                     r'(?:\s*[、,，/]\s*' + _SCORE_SUBJECT_CODE + r')*\s*[)）]')


def _plain(value):
    if not isinstance(value, str): return ''
    value = re.sub(r'<(?:script|style)\b[^>]*>[\s\S]*?</(?:script|style)\s*>', '', value, flags=re.I)
    return unescape(re.sub(r'<[^>]+>', '\n', value))


def _material(title):
    return next((kind for kind, pattern in _KINDS if re.search(pattern, title)), 'unknown')


def _score_label(label):
    # Codes belong in a leading field or an explicit parenthesized category.
    # A date or an extra numeric score cell must never be absorbed into a label.
    label = re.sub(r'^' + _SCORE_SUBJECT_CODE + r'[ \t]*', '', label)
    label = _BRACKETED_SUBJECT_CODES.sub('', label)
    return not re.search(r'\d', label)


def _score_table(body):
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    for index, row in enumerate(lines):
        if len(row) > 300: continue
        found = _SCORE_ROW.fullmatch(row)
        if not found or not _score_label(found['label']): continue
        if re.search(r'附件|下载|点击|浏览|阅读|发布|更新|时间|日期|电话|地址', found['label']): continue
        heading = re.sub(r'\s+', '', '\n'.join(lines[max(0, index - 18):index]))
        columns = _SCORE_COLUMNS.findall(heading)
        if len([column for column in columns if column not in {'单科', '总分'}]) >= 2:
            columns = [column for column in columns if column != '单科']
        values = re.split(r'[ \t|]+', found['values'])
        if columns.count('总分') == 1 and 3 <= len(columns) == len(values) <= 6 and all(0 <= float(value) <= 500 for value in values):
            return True
    return False


def _fee_table(body):
    # A unit in a separate real table header can govern numeric data rows.
    # Bare years, download counters and policy citations are not tuition facts.
    unit = r'[（(]\s*(?:单位\s*[:：]\s*)?(?:万)?元\s*[/／]\s*生\s*[.·/／]\s*(?:学年|年|学期)\s*[）)]'
    if not re.search(r'收费标准\s*' + unit, body): return False
    for line in body.splitlines():
        if re.search(r'附件|下载|浏览|阅读|发布|更新|时间|日期|电话|地址|邮编', line): continue
        row = re.fullmatch(r'\s*([\u3400-\u9fff][^\n]{0,160}?)\s+(\d{1,8}(?:\.\d{1,2})?)\s*', line)
        if not row: continue
        descriptor = row[1].split()[0]
        if re.search(r'政策|文件|文号|字号|依据|单位|备注|序号|页码|页数|表号|编号|收费标准', descriptor): continue
        if not re.match(r'[^\s]{0,6}(?:发改|教财|财综|财教|价费|财税|财办)', row[1]): return True
    return False


def _score_facts(body):
    without_full_marks = re.sub(r'[（(][^()（）]{0,30}满分[^()（）]{0,60}[)）]', '', body)
    without_full_marks = re.sub(r'满分[^\d]{0,12}\d{1,3}\s*分', '', without_full_marks)
    return (bool(re.search(r'分数|总分|成绩|门类|专业', without_full_marks) and
                 re.search(r'\d+(?:\.\d+)?\s*(?:分|%)', without_full_marks)) or _score_table(body))


def _substantive(kind, body, title=''):
    # Analyse the saved text without rewriting the exact original returned to readers.
    body = '\n'.join(line for line in body.splitlines() if line.strip() != title and
                     not re.search(r'邮编|邮政编码|联系电话|传真|Copyright|版权所有', line, re.I) and
                     not re.match(r'\s*(?:附件(?:下载)?|点击下载|下载附件)\s*[:：]', line))
    body = _NOISE.sub(' ', body)
    named_change = bool(re.search(r'调整为|改为|变更为|科目为', body) and
                        re.search(r'英语[一二]|数学[一二三]|思想政治理论|计算机学科专业基础|管理类综合能力|经济类综合能力', body))
    subjects = named_change or bool(_EXAM_CODE.search(body) and re.search(r'初试|业务课|考试|科目|数学|英语|计算机|统考|专业基础', body))
    catalog = bool((any(re.search(r'专业|学科|技术|工程|研究方向', body[max(0, code.start()-25):code.end()+40])
                        for code in _NUMBER_CODE.finditer(body))) or
                   len(set(_EXAM_CODE.findall(body))) >= 2 and subjects)
    fees = bool(re.search(r'(?:学费|收费|学制|奖助)', body) and
                re.search(r'\d+(?:\.\d+)?\s*(?:万元|元)|学制\D{0,8}(?:\d{1,2}(?:\.\d)?|[一二三四五六七八九])\s*年', body)) or _fee_table(body)
    eligibility = bool(re.search(r'报考|学历|学位|同等学力|毕业|资格', body) and re.search(r'须|必须|应当|不得|不接受|允许|要求|符合|具有', body))
    deadline = bool(re.search(r'申请|截止|报名|提交|开放', body) and re.search(r'\d{1,2}月\d{1,2}日|20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}', body))
    scores = _score_facts(body)
    facts = {'catalog': catalog, 'subjects': subjects, 'fees': fees,
             'admissions': eligibility or fees or deadline,
             'interview': scores or deadline or bool(re.search(r'复试.{0,50}(?:笔试|面试|权重|比例|资格)', body)),
             'transfer': deadline or bool(re.search(r'调剂.{0,50}(?:须|要求|不接受|相同|相近|缺额)', body)),
             'scores': scores}
    return facts.get(kind, False)


def _decision(state, kind, reasons):
    return {'version': QUALITY_VERSION, 'state': state, 'reusable': state == 'accepted',
            'material_type': kind, 'reasons': [{'code': code, 'message': message} for code, message in reasons]}


def _heading_year_conflict(content, year):
    for line in content.splitlines()[:30]:
        heading = line.strip()
        if len(heading) > 120 or re.match(r'参考|参照|根据|依据|往年|历年|例如', heading): continue
        if re.search(r'[。；]', heading) or _material(heading) == 'unknown': continue
        found = re.match(r'[^\d]{0,40}(20\d{2})\s*(?:年|级)', heading)
        if found and int(found[1]) != year: return True
    return False


def assess_quality(source, body='', *, original_available=False, authority_verified=False, pages=()):
    title = _plain(source.get('title', '')).strip()
    body = _plain(body)
    page_text = '\n'.join(_plain(page.get('text', '')) for page in pages if isinstance(page, dict))
    content = body + '\n' + page_text
    kind = _material(title)
    facts = _substantive(kind, content, title)
    excluded = []
    if source.get('text_origin') == 'metadata_only' or contains_personal_roster(title, content):
        excluded.append(('personal_roster', '逐人名单不进入可复用知识，避免保存个人身份正文。'))
    if _GATE_TITLE.fullmatch(title) or _GATE_BODY.search(content) and not facts:
        excluded.append(('access_gate', '实际内容为访问验证、频控或登录门禁，未取得招生正文。'))
    if re.search(r'课程促销|购买课程|保过班|考研培训|扫码咨询', title):
        excluded.append(('commercial_content', '培训营销信息不属于官方招生依据。'))
    if re.search(r'本科招生|博士(?:研究生)?招生|校园新闻|校庆|运动会', title) and not re.search(r'硕士|硕博', title):
        excluded.append(('out_of_scope', '材料不属于硕士招生研究范围。'))
    if not facts and kind == 'unknown' and re.search(r'研究生院(?:欢迎您[!！]?)?$|研究生部$|^招生信息$|^首页$|^通知公告$|^新闻中心$', title):
        excluded.append(('navigation_only', '学校主页或导航列表不能作为具体招生知识。'))
    if not facts and _NOISE.search(content) and len(re.sub(r'\s+', '', _NOISE.sub('', content))) < 60:
        excluded.append(('code_noise', '提取内容主要是网页样式或脚本，没有可用招生正文。'))
    if excluded: return _decision('excluded', kind, excluded)
    reasons = []
    status = source.get('status')
    if status == 'search_lead': reasons.append(('search_only', '只有搜索摘要，须读取官方原文后再次检查准入。'))
    elif status not in {'official_original_read', 'published_snapshot'}:
        reasons.append(('source_unverified', '资料取得状态尚不能支持知识复用。'))
    if not source.get('unit_id'): reasons.append(('unit_unknown', '尚未确认对应招生单位。'))
    if status != 'published_snapshot' and not authority_verified:
        reasons.append(('authority_unverified', '官方来源与招生单位的归属尚未核对。'))
    if status == 'official_original_read' and not original_available:
        reasons.append(('original_unavailable', '原文留档缺失或完整性校验未通过。'))
    year = source.get('year')
    if type(year) is not int or not 1900 <= year <= 2100:
        reasons.append(('year_unknown', '来源适用年度未明确，检索目标年度不能替代。'))
    else:
        years = set(int(value) for value in re.findall(r'(?<!\d)(20\d{2})\s*(?:年|级)', title))
        if years and year not in years or _heading_year_conflict(content, year):
            reasons.append(('year_conflict', '标题或正文主标题的年度与记录年度不一致，需要核对。'))
    if kind == 'unknown': reasons.append(('material_unknown', '尚未识别为具体的招生目录、规则或公告。'))
    if not facts: reasons.append(('body_not_substantive', '正文尚无与材料类型相符的实质信息；可能只是发布页或附件入口。'))
    if reasons: return _decision('pending', kind, reasons)
    return _decision('accepted', kind, [('admission_passed', '已通过来源、实际年度与招生正文准入；具体字段仍需逐项核验。')])
