"""Recognize obvious personal rosters without mistaking links or policy references for a roster."""
from html import unescape
import re


_ROSTER_TITLE=re.compile(r'拟录取(?:名单|结果)|(?:复试|考生|录取|入围)名单|录取结果')
_NAME=r'(?:考\s*生\s*)?姓\s*名'
_IDENTITY=r'(?:考\s*生\s*(?:编\s*号|号)|准\s*考\s*证(?:\s*号(?:\s*码)?)?|身\s*份\s*证(?:\s*号(?:\s*码)?)?)'
_SCORE=r'(?:初试(?:成绩|分数|分)|复试(?:成绩|分数|分)|总成绩|综合成绩|总分)'
_HEADER_PAIR=re.compile(r'(?:'+_NAME+r'.{0,120}?'+_IDENTITY+r'|'+_IDENTITY+r'.{0,120}?'+_NAME+r')',re.S)
_ID=r'(?:\d{12,18}[Xx]?|\d{3,8}[*＊]{2,}\d{2,8})'
_IDENTIFIER=re.compile(r'(?<![\dA-Za-z])'+_ID+r'(?![\dA-Za-z])')
_PERSONAL_ROW=re.compile(r'(?:^|[\n|])\s*(?:\d{1,4}[ \t|]+)?(?:[\u3400-\u9fff·*＊]{2,8}[ \t|,，]+'+_ID+r'|'+_ID+r'[ \t|,，]+[\u3400-\u9fff·*＊]{2,8})(?:[ \t|,，]|$)')
_HEADER_LABELS=re.compile(r'考生姓名|考生编号|准考证号码|准考证号|身份证号码|身份证号|考生号|身份证|准考证|初试总成绩|初试成绩|初试分数|复试成绩|复试分数|综合成绩|总成绩|初试分|复试分|专业代码|报考专业|研究方向|学习方式|专项计划|录取状态|姓名|序号|专业|学院|备注|性别|总分|分数|排名')


def _plain(value):
    return unescape(re.sub(r'<[^>]*>','\n',value)) if isinstance(value,str) else ''


def _looks_like_table_header(body):
    lines=[line.strip() for line in body.splitlines() if line.strip()]
    for start in range(len(lines)):
        for width in range(1,min(8,len(lines)-start)+1):
            block=re.sub(r'\s+','', ''.join(lines[start:start+width]))
            if not re.search(_NAME,block) or not (re.search(_IDENTITY,block) or re.search(_SCORE,block)):
                continue
            remainder=_HEADER_LABELS.sub('',block)
            if not re.sub(r'[\s\d|,，、:：;；()（）/\-]+','',remainder):return True
    return False


def contains_personal_roster(title,*texts):
    """Keep roster titles private; body-only matches require identity/table evidence."""
    if _ROSTER_TITLE.search(re.sub(r'\s+','',_plain(title))):return True
    body='\n'.join(_plain(text) for text in texts)
    for match in _HEADER_PAIR.finditer(body):
        if _IDENTIFIER.search(body[max(0,match.start()-100):match.end()+500]):return True
    return bool(_PERSONAL_ROW.search(body) or _looks_like_table_header(body))
