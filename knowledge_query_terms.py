"""Deterministic Chinese query terms for bounded lexical retrieval.

The knowledge store keeps exact original text and segment offsets. It does not
download a dictionary and does not run a learned segmenter: a long natural
Chinese question is decomposed with a small, versioned admissions lexicon plus
codes, years, ASCII tokens, numbers, stop-word removal and short residual
phrases. Every consumer sees the same ordered terms and weights, so SQLite
ranking, RRF and the rule reranker stay reproducible.

Spaced queries keep the historical word list semantics in the repository; this
module only supplies the extra analysis for an unspaced natural question.
"""
import re
import unicodedata

CJK = re.compile(r'[\u3400-\u9fff]')
ASCII = re.compile(r'[A-Za-z][A-Za-z0-9.+#/-]{1,19}')
YEAR = re.compile(r'(?<!\d)(20[2-3]\d)(?!\d)')
CODE6 = re.compile(r'(?<!\d)\d{6}(?!\d)')
CODE3 = re.compile(r'(?<!\d)(?:101|199|201|202|203|204|301|302|303|396|408|806|814|F015)(?!\d)')
DIGITS = re.compile(r'(?<!\d)\d{2,5}(?!\d)')

# Admissions vocabulary only. Longest match wins, so "计算机科学与技术" masks
# "计算机" and "数学一" masks "数学".
LEXICON = tuple(sorted(set('''同等学力 第一作者 第二作者 通讯作者 学术论文 公开发表 主干课 本科主干课 补修
英语四级 英语六级 公共英语三级 全国统一考试 单独考试 推免生 接收推免 退役大学生士兵 专项计划
少数民族 定向就业 非定向就业 非全日制 全日制 在职 在编 脱产 应届本科 应届 往届 专科毕业 本科结业
跨学科 跨专业 加试 复试 初试 面试 笔试 拟录取 录取 学硕 专硕 硕士 博士 统考 推免 调剂 非全
招生简章 招生章程 招生专业目录 专业目录 招生计划 拟招生人数 招生人数 拟接收推免生 招生名额 名额
调剂公告 复试线 学院复试线 学校线 国家线 分数线 总分 单科 招生年度 招生目录
考试科目 初试科目 复试科目 科目一 科目二 科目三 科目四 专业课 业务课 命题 大纲 参考书目
数学一 数学二 数学三 英语一 英语二 计算机学科专业基础 程序设计 无机化学
学费标准 收费标准 收费公示 计费口径 学费 学制 学习方式 学习年限 奖学金 助学金 培养方式 联合培养
学术学位 专业学位 学术型 专业型 工学 工程类 电子信息 软件工程 人工智能 计算机技术 计算机
计算机科学与技术 网络空间安全 机械 材料 化学工程 化学 临床医学 中医内科学 内科学 护理 生物学
数学 物理学 设计 美术 书法 音乐 舞蹈 体育 工商管理 公共管理 工业工程与管理 工业工程 物流工程 旅游管理
会计 金融 法律 教育 心理学 新闻与传播 就业方式 学习证明 档案 户口 体检 政审 网上报名 报名 考试时间
截止时间 研究方向 导师 专科 高职 毕业 成绩 论文 马克笔 高端科技投资
EMBA MBA MEM MPA'''.split()), key=lambda term: (-len(term), term)))

STOP_WORDS = tuple(sorted(set('''是否 能否 可以 能够 能不能 是不是 有没有 什么 哪些 怎么 如何 为什么 多少 几年 分别 一样 一律
直接 确定 认定 满足 证明 要求 需要 现在 当年 今年 这份 那个 这个 其中 以及 或者 还是 并且 而且 但是
因为 所以 如果 就是 一定 都 也 还 就 只 仅 凭 按 根据 对于 关于 在 与 和 或 及 等 这 那 该 其
有 无 把 被 让 给 从 到 为 对 以 的 了 吗 呢 吧 啊 年 级 页 份 里 中 上 下
是唯一 途径 条款 参加 准备 人员 本身 确认 强调 允许 想研究 不看 看到 当作 机会 能据此 断言 甚至
达到 就能 才 各自 只给 只凭 合读 官方 入口 附件 发布 这份 它 该段 当年 仍不招 且不设 必须是 可接受范围'''.split()),
                     key=lambda term: (-len(term), term)))

_EDGE = frozenset('是在和的了吗呢带用考看给让把被从到为对以按凭只就也还都仅这那该其')
_MAX_TERMS = 10
_MAX_BIGRAMS = 24


def is_natural(query):
    """An unspaced, long-enough Chinese sentence needs term extraction.

    Short labels such as "计算机" and anything with explicit spaces keep the
    existing exact/word-list behaviour and its tests.
    """
    if not isinstance(query, str):
        return False
    value = unicodedata.normalize('NFKC', query).strip()
    return len(value) >= 6 and not re.search(r'\s', value) and bool(CJK.search(value))


def _add(rows, seen, term, kind, weight):
    value = unicodedata.normalize('NFKC', term).strip()
    if not value or value in seen or value in STOP_WORDS:
        return
    seen.add(value)
    rows.append({'term': value, 'kind': kind, 'weight': weight})


def analyze(query):
    """Return ordered ``{term, kind, weight}`` rows (at most ten)."""
    if not isinstance(query, str):
        return []
    source = unicodedata.normalize('NFKC', query).strip()
    if not source:
        return []
    rows, seen = [], set()
    masked = list(source)
    for pattern, kind, weight in ((CODE6, 'code', 3), (CODE3, 'code', 3), (YEAR, 'year', 2),
                                  (ASCII, 'ascii', 2), (DIGITS, 'number', 1)):
        for match in pattern.finditer(source):
            _add(rows, seen, match.group(0), kind, weight)
            for index in range(match.start(), match.end()):
                masked[index] = ' '
    work = ''.join(masked)
    for term in LEXICON:
        start = 0
        while True:
            index = work.find(term, start)
            if index < 0:
                break
            _add(rows, seen, term, 'lexicon', 3 if len(term) >= 4 else 2)
            work = work[:index] + ' ' * len(term) + work[index + len(term):]
            start = index + len(term)
    work = re.sub(r'[^\u3400-\u9fff]+', ' ', work)
    for stop in STOP_WORDS:
        work = work.replace(stop, ' ')
    for piece in work.split():
        piece = piece.strip(''.join(_EDGE))
        if 2 <= len(piece) <= 10:
            _add(rows, seen, piece, 'phrase', 1)
    names = [row['term'] for row in rows]
    rows = [row for row in rows
            if not any(row['term'] != other and row['term'] in other and row['weight'] <= 2
                       for other in names)]
    return rows[:_MAX_TERMS]


def terms(query):
    """Ordered term strings; empty for non-text or blank input."""
    return tuple(row['term'] for row in analyze(query))


def bigrams(query):
    """Unique CJK bigrams for soft rerank matching, capped for stable payloads."""
    if not isinstance(query, str):
        return ()
    work = re.sub(r'[^\u3400-\u9fff]+', ' ', unicodedata.normalize('NFKC', query))
    for stop in STOP_WORDS:
        work = work.replace(stop, ' ')
    values = []
    for piece in work.split():
        for index in range(len(piece) - 1):
            gram = piece[index:index + 2]
            if gram not in values:
                values.append(gram)
    return tuple(values[:_MAX_BIGRAMS])
