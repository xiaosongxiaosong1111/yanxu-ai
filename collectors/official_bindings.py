"""Offline recovery candidates from saved, unit-attributed CHSI originals.

This report does not publish domains, modify units, or claim annual admission status.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import ipaddress
import json
from pathlib import Path
import re
from urllib.parse import urlsplit, urlunsplit

from public_sources import SourceError, document_attribution, rendered_source_html, official_source_url
from .documents import PROJECT_ROOT, decode_text
from .discovery import DiscoveryError
from .national_sources import fetch_source, normalized_url, source_allowed


URL = re.compile(r'(?:https?://|(?<![\w/.])www\.)[^\s<>"\'，。；：（）\u4e00-\u9fff]+', re.I)
BARE_URL = re.compile(r'(?<![a-z0-9_@./:-])(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+'
    r'(?:edu|ac|gov|org)\.cn(?![a-z0-9_.-])(?::\d+)?(?:[/?#][^\s<>"\'，。；：（）\u4e00-\u9fff]+)?', re.I)
BARE_ROLE = re.compile(r'(?:学校|我校|本校|本单位|我单位|本院|我院|本所|我所)'
    r'(?:研究生(?:院|处|部|招生)?|研招|招生)?(?:官方网站|官网|网站|网址|主页)'
    r'|(?:研究生(?:院|处|部|招生办|招生办公室|招生)?|研招(?:办|办公室|在线)?)(?:招生)?(?:信息)?(?:网站|网址|主页|官网|网)')
BARE_REFERENCE = re.compile(r'友情链接|其他|其它|参考|参照|借用|合作单位|协作单位')
ROLE = re.compile(r'(?:研究生(?:院|处|部|教育|招生)?(?:招生)?(?:信息)?|研招(?:在线)?|招生信息|学校|学院|院校|部门|单位|本校|我校|本院|我院|本所|我所|本单位|我单位)?(?:官方网站|官网|网站|网址|主页)(?:地址)?')
FIRST_PERSON = re.compile(r'本校|我校|本院|我院|本所|我所|本单位|我单位')
REFERENCE = re.compile(r'其他|其它|参照|参考|借用|教育部|学信网|中国研究生招生信息网')
PUBLICATION = re.compile(r'硕士.*(?:招生简章|招生章程|招生专业目录|专业目录)|(?:招生简章|招生章程|专业目录).*硕士')
AUXILIARY = re.compile(r'财务处|计划财务|学生资助|就业|国际教育|国际研究生|报名系统|预推免(?:报名)?系统|统一身份|登录系统')
ACADEMIC = ('.edu.cn', '.ac.cn', '.gov.cn', '.org.cn')


class _OriginalHTML(HTMLParser):
    BLOCKS = {'p','div','li','tr','br','h1','h2','h3','section','article'}
    HIDDEN = {'script','style','nav','footer','header','aside','noscript','template'}
    VOID = {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.anchors, self.heading = [], [], []
        self.primary, self.primary_tag, self.primary_parts = [], None, []
        self.stack, self.current, self.anchor_parts, self.in_heading = [], None, [], False

    def is_hidden(self):
        return bool(self.stack and self.stack[-1][1])

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        style = re.sub(r'\s+','',attrs.get('style','').lower())
        hidden = (self.is_hidden() or tag in self.HIDDEN or 'hidden' in attrs
            or attrs.get('aria-hidden','').lower()=='true' or bool(re.search(r'display:none|visibility:hidden',style))
            or attrs.get('role')=='tooltip' or bool(re.search(r'(?:^|\s)(?:tooltip(?:-[\w-]+)?|popover(?:-[\w-]+)?|hidden|d-none|hide)(?:$|\s)',attrs.get('class',''),re.I)))
        if tag not in self.VOID:
            self.stack.append((tag,hidden))
        if hidden:
            return
        if tag in self.BLOCKS:
            self.parts.append('\n')
        if tag in {'h1','h2','h3','title'}:
            self.in_heading = True
        if tag in {'title','h1'}:
            self.primary_tag, self.primary_parts = tag, []
        if tag == 'a' and attrs.get('href'):
            self.current = {'href':attrs['href'], 'start':len(''.join(self.parts)), 'title':attrs.get('title','')}
            self.anchor_parts = []

    def handle_endtag(self, tag):
        hidden = self.is_hidden()
        for index in range(len(self.stack)-1,-1,-1):
            if self.stack[index][0]==tag:
                hidden = self.stack[index][1]
                self.stack = self.stack[:index]
                break
        if hidden:
            return
        if tag == 'a' and self.current:
            self.anchors.append({**self.current,'label':''.join(self.anchor_parts).strip() or self.current['title'], 'end':len(''.join(self.parts))})
            self.current, self.anchor_parts = None, []
        if tag in {'h1','h2','h3','title'}:
            self.in_heading = False
        if tag==self.primary_tag:
            self.primary.append(''.join(self.primary_parts).strip())
            self.primary_tag, self.primary_parts = None, []
        if tag in self.BLOCKS:
            self.parts.append('\n')

    def handle_data(self, value):
        if self.is_hidden():
            return
        self.parts.append(value)
        if self.current:
            self.anchor_parts.append(value)
        if self.in_heading:
            self.heading.append(value)
        if self.primary_tag:
            self.primary_parts.append(value)


def _target(href, base):
    literal = href.rstrip('.,;:!?)]}')
    implicit = literal.lower().startswith('www.') or BARE_URL.fullmatch(literal)
    url = normalized_url('https://'+literal if implicit else literal, base)
    if not url:
        return None
    parts = urlsplit(url)
    host = parts.hostname.lower().rstrip('.')
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?', host) or '.' not in host:
        return None
    try:
        ipaddress.ip_address(host)
        return None
    except ValueError:
        pass
    if host.endswith(('.chsi.com.cn','.moe.gov.cn','.localhost','.local','.internal')) or host in {'yz.chsi.com.cn','moe.gov.cn'}:
        return None
    return urlunsplit((parts.scheme,parts.netloc,'/','','')),url,host


def _context(text, start, end):
    left = text.rfind('\n',0,start)+1
    right = text.find('\n',end)
    return re.sub(r'\s+',' ',text[max(left,start-180):min(right,end+180) if right>=0 else end+180]).strip()


def _relation(label, prefix, context, unit, foreign_names):
    name = re.sub(r'\s+','',unit['name'])
    compact = re.sub(r'\s+','',label)
    clause = re.split(r'[。；;\n]',context)[-1]
    if REFERENCE.search(clause) or any(other and other not in name and other in clause for other in foreign_names):
        return None
    if name in compact and PUBLICATION.search(compact):
        return 'own_admissions_publication'
    if ROLE.search(compact) and (ROLE.fullmatch(compact) or name in compact or FIRST_PERSON.search(compact)):
        return 'declared_official_site'
    prefix = re.sub(r'[\s:：()（）\d一二三四五六七八九十、.]+$','',prefix)
    role = list(ROLE.finditer(prefix))
    if not role:
        return None
    tail = role[-1]
    if len(prefix)-tail.end()>20:
        return None
    role_text = tail[0]
    if name in prefix or FIRST_PERSON.search(prefix) or re.search(r'研究生|研招|学校|学院|院校|部门|单位',role_text):
        return 'declared_official_site'
    return 'generic_contact_url' if re.fullmatch(r'[\s（）()\d一二三四五六七八九十、.]*',prefix[:tail.start()]) else None


def binding_candidates(unit, document, raw, *, foreign_names=()):
    url = document.get('url','')
    if (urlsplit(url).hostname != 'yz.chsi.com.cn' or not source_allowed(url,unit)
            or hashlib.sha256(raw).hexdigest() != document.get('sha256')):
        return []
    parser = _OriginalHTML()
    parser.feed(rendered_source_html(decode_text(raw,document.get('content_type','')),url))
    text = ''.join(parser.parts)
    if document_attribution(url,unit,text,document_heading=' '.join(parser.heading))['status'] != 'verified':
        return []
    targets = [(anchor['href'],anchor['label'],anchor['start'],anchor['end']) for anchor in parser.anchors]
    targets += [(match[0],'',match.start(),match.end()) for match in URL.finditer(text)]
    targets += [(match[0],'',match.start(),match.end()) for match in BARE_URL.finditer(text)
                if not match[0].lower().startswith('www.')]
    result = {}
    for href,label,start,end in targets:
        target = _target(href,url)
        if not target:
            continue
        origin,linked,host = target
        context = _context(text,start,end)
        prefix = text[max(text.rfind('\n',0,start)+1,start-150):start]
        bare = bool(BARE_URL.fullmatch(href.rstrip('.,;:!?)]}'))) and not href.lower().startswith('www.')
        bare_clause = re.split(r'[。；;\n]', prefix)[-1] + label
        if bare and (BARE_REFERENCE.search(bare_clause) or not (BARE_ROLE.search(bare_clause)
                or unit['name'] in bare_clause and ROLE.search(bare_clause))
                or any(other and other not in unit['name'] and other in bare_clause for other in foreign_names)):
            continue
        relation = (_relation(label,prefix,bare_clause,unit,foreign_names) or 'declared_official_site') if bare else _relation(label,prefix,context,unit,foreign_names)
        if not relation:
            continue
        bare_scope = re.sub(r'\s+', '', prefix + label).replace(re.sub(r'\s+', '', unit['name']), '')
        bare_auxiliary = bare and (AUXILIARY.search(prefix + label)
            or re.search(r'学院|学部|院系|系所|二级单位', bare_scope.replace('研究生学院', '研究生院')))
        reason = ('auxiliary_department_site' if bare_auxiliary else 'scheme_unconfirmed'
            if bare or href.lower().startswith('www.') else 'declared_non_academic_domain'
            if not host.endswith(ACADEMIC) else 'auxiliary_department_site' if AUXILIARY.search(prefix) else relation)
        status = 'needs_review' if relation=='generic_contact_url' or reason in {'declared_non_academic_domain','auxiliary_department_site','scheme_unconfirmed'} else 'declared_official_origin'
        evidence = {'url':url,'sha256':document['sha256'],'local_path':document.get('local_path',document.get('snapshot_path')),
                    'quote':href.rstrip('.,;:!?)]}'),'link_label':label,'context':context,'relation':relation}
        if bare:
            evidence = {**evidence, 'target_form': 'bare_academic_host'}
        if origin in result:
            if result[origin]['status']=='needs_review' and status=='declared_official_origin':
                previous = result[origin]
                result[origin] = {'url':origin,'linked_url':linked,'host':host,'status':status,'reason':reason,
                    'relation':relation,'evidence':evidence,'additional_evidence':[previous['evidence'],*previous['additional_evidence']]}
                continue
            if evidence != result[origin]['evidence'] and evidence not in result[origin]['additional_evidence']:
                result[origin]['additional_evidence'].append(evidence)
            continue
        result[origin] = {'url':origin,'linked_url':linked,'host':host,'status':status,'reason':reason,
                          'relation':relation,'evidence':evidence,'additional_evidence':[]}
    return list(result.values())


def build_report(registry, national, inventory, root):
    root = Path(root).resolve()
    national = {row['unit_id']:row for row in national.get('units',[])}
    inventory = {row['unit_id']:row for row in inventory.get('units',[])}
    names = [unit['name'] for unit in registry.get('units',[])]
    rows, scanned = [], 0
    for unit in registry.get('units',[]):
        unit_id = unit['id']
        if unit.get('official_url') or national.get(unit_id,{}).get('bindings'):
            continue
        candidates, errors, seen = {}, [], set()
        documents = national.get(unit_id,{}).get('pages',[])+inventory.get(unit_id,{}).get('pages',[])
        for doc in documents:
            local = doc.get('local_path',doc.get('snapshot_path',''))
            key = (doc.get('url'),doc.get('sha256'),local)
            if key in seen or urlsplit(doc.get('url','')).hostname!='yz.chsi.com.cn':
                continue
            seen.add(key)
            try:
                path = (root/local).resolve()
                if not local or not path.is_relative_to(root) or path.suffix.lower() not in {'.html','.htm'}:
                    raise ValueError('original path outside project or not HTML')
                raw = path.read_bytes()
                if hashlib.sha256(raw).hexdigest()!=doc.get('sha256'):
                    raise ValueError('original hash mismatch')
                found = binding_candidates(unit,doc,raw,foreign_names=names)
                scanned += 1
            except (OSError,ValueError) as error:
                errors.append({'code':'invalid_original','url':doc.get('url'),'message':str(error)})
                continue
            for candidate in found:
                origin = candidate['url']
                if origin in candidates:
                    if candidates[origin]['status']=='needs_review' and candidate['status']=='declared_official_origin':
                        candidate = {**candidate,'additional_evidence':[candidates[origin]['evidence'],*candidates[origin]['additional_evidence'],*candidate['additional_evidence']]}
                        candidates[origin] = candidate
                        continue
                    candidates[origin]['additional_evidence'].append(candidate['evidence'])
                    candidates[origin]['additional_evidence'].extend(candidate['additional_evidence'])
                else:
                    candidates[origin] = candidate
        values = list(candidates.values())
        rows.append({'unit_id':unit_id,'unit_name':unit['name'],'unit_code':unit.get('unit_code'),
                     'status':'recovered' if any(row['status']=='declared_official_origin' for row in values) else 'needs_review' if values else 'unresolved',
                     'candidates':values,'errors':errors})
    return {'metadata':{'checked_at':datetime.now(timezone.utc).isoformat(timespec='seconds'),'eligible_units':len(rows),
            'scanned_originals':scanned,'recovered_units':sum(row['status']=='recovered' for row in rows),
            'review_units':sum(row['status']=='needs_review' for row in rows),'unresolved_units':sum(row['status']=='unresolved' for row in rows),
            'notice':'离线候选报告；未发布官网归属，未修改招生单位、年度名单及专业目录。'},'units':rows}


def _own_primary_title(unit, value):
    clean = lambda text: re.sub(r'\s+', '', text).replace('（', '(').replace('）', ')')
    name, title = clean(unit['name']), clean(value)
    if name not in title:
        return False
    remainder = title.split(name, 1)[1].replace('研究生学院', '研究生院')
    if AUXILIARY.search(remainder) or re.search(r'学院|学部|院系|系所|本科招生|继续教育', remainder):
        return False
    return True


def verify_candidate_home(unit, candidate, root, *, fetcher=None, target_dir=None, target_url=None):
    """Corroborate a declared candidate using its public homepage primary title only."""
    fetcher = fetcher or (lambda url,scope:fetch_source(url,scope,timeout=10,max_bytes=2*1024*1024))
    try:
        target = candidate['url'] if target_url is None else target_url
        scope = {**unit, 'official_url': candidate['url']}
        if (urlsplit(target).hostname == 'yz.chsi.com.cn' or not official_source_url(target, scope)
                or urlsplit(candidate['url']).hostname == 'yz.chsi.com.cn'):
            raise SourceError('wrong_unit', '官网候选不得请求已暂停访问的研招网主机。')
        root = Path(root).resolve()
        directory = Path(target_dir) if target_dir is not None else root/'data/runtime/official-binding-targets'
        directory = (Path(root)/directory if not directory.is_absolute() else directory).resolve()
        if not directory.is_relative_to(Path(root).resolve()):
            raise ValueError('目标官网原文目录超出项目范围。')
        fetched = fetcher(target, scope)
        raw = fetched['raw']
        if hashlib.sha256(raw).hexdigest()!=fetched.get('sha256'):
            raise SourceError('invalid_original','目标首页原文hash不一致。')
        if 'html' not in fetched.get('content_type','').lower():
            raise SourceError('unexpected_type','目标首页不是HTML。')
        html = decode_text(raw,fetched['content_type'])
        parsed = _OriginalHTML()
        parsed.feed(html)
        primary = parsed.primary
        clean = lambda value:re.sub(r'\s+','',re.sub(r'<[^>]+>','',value)).replace('（','(').replace('）',')')
        quote = next((re.sub(r'<[^>]+>','',value).strip() for value in primary
                      if _own_primary_title(unit, clean(value))),None)
        sha = fetched['sha256']
        path = directory/(sha+'.html')
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(raw)
        proof = {'url':fetched['url'],'requested_url':target,'sha256':sha,'local_path':path.relative_to(Path(root).resolve()).as_posix(),
                 'quote':quote,'relation':'own_primary_homepage_title'}
        return {**candidate,'status':'corroborated_official_origin' if quote else 'needs_review',
                'target_evidence':proof,'target_check':{'code':'own_primary_title' if quote else 'unconfirmed_primary_title'}}
    except (OSError,SourceError,DiscoveryError,ValueError) as error:
        return {**candidate,'target_check':{'code':getattr(error,'code','target_failed'),'message':str(error)}}


def ready_candidates(report, registry, root, *, registry_sha256=None):
    """Export only freshly replayed strong HTML declarations for independent review."""
    root=Path(root).resolve()
    units={unit['id']:unit for unit in registry['units']}
    names=[unit['name'] for unit in registry['units']]
    rows, errors=[],[]
    for row in report['units']:
        unit=units.get(row['unit_id'])
        if not unit or unit.get('official_url'):
            continue
        accepted={}
        for candidate in row['candidates']:
            if candidate['status']!='declared_official_origin':
                continue
            proof=candidate['evidence']
            try:
                path=(root/proof['local_path']).resolve()
                if not path.is_relative_to(root):
                    raise ValueError('original outside project')
                raw=path.read_bytes()
                document={**proof,'content_type':'text/html'}
                replayed=binding_candidates(unit,document,raw,foreign_names=names)
                match=next((value for value in replayed if value['url']==candidate['url'] and value['status']=='declared_official_origin'),None)
                if match:
                    accepted[match['url']]=match
                else:
                    errors.append({'unit_id':unit['id'],'url':candidate['url'],'code':'original_replay_failed'})
            except (OSError,ValueError) as error:
                errors.append({'unit_id':unit['id'],'url':candidate['url'],'code':'invalid_original','message':str(error)})
        if accepted:
            rows.append({'unit_id':unit['id'],'unit_name':unit['name'],'unit_code':unit.get('unit_code'),'candidates':list(accepted.values())})
    return {'metadata':{'status':'ready_for_independent_review','checked_at':datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'registry_sha256':registry_sha256,'ready_units':len(rows),'candidate_origins':sum(len(row['candidates']) for row in rows),
            'notice':'已按当前可见性/归属规则重放强声明；仅候选，不修改units，不代表年度目录完整。'},'units':rows,'errors':errors}


def _replay_home_route(unit, declared, proof, root):
    route = proof.get('corroboration_route')
    if route is None:
        return True
    if not isinstance(route, list) or not 1 <= len(route) <= 4 or any(not isinstance(node, dict) for node in route):
        return False
    literals = [declared['evidence'], *declared.get('additional_evidence', [])]
    first = {declared['url'], declared['linked_url']}
    for item in literals:
        target = _target(item.get('quote', ''), item.get('url', ''))
        if target:
            first.add(target[1])
    if normalized_url(route[0].get('requested_url', route[0].get('url', ''))) not in {normalized_url(url) for url in first}:
        return False
    scope = {**unit, 'official_url': declared['url']}
    for index, node in enumerate(route):
        url = node.get('url', '')
        if urlsplit(url).hostname == 'yz.chsi.com.cn' or not official_source_url(url, scope):
            return False
        path = (root/node.get('local_path', '')).resolve()
        if not path.is_relative_to(root):
            return False
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != node.get('sha256'):
            return False
        parser = _OriginalHTML(); parser.feed(decode_text(raw, 'text/html'))
        target = route[index+1].get('url') if index+1 < len(route) else proof.get('requested_url', proof['url'])
        if not any(anchor['href'] == node.get('quote') and anchor['label'] == node.get('link_label')
                   and normalized_url(anchor['href'], url) == normalized_url(target) for anchor in parser.anchors):
            return False
    return True


def _replay_corroborated(unit, candidate, root, names):
    evidence=candidate['evidence']
    path=(root/evidence['local_path']).resolve()
    target_proof=candidate['target_evidence']
    target_path=(root/target_proof['local_path']).resolve()
    if not path.is_relative_to(root) or not target_path.is_relative_to(root):
        return None
    source=binding_candidates(unit,{**evidence,'content_type':'text/html'},path.read_bytes(),foreign_names=names)
    declared=next((value for value in source if value['url']==candidate['url'] and value['reason']!='auxiliary_department_site'),None)
    if not declared or not _target(target_proof['url'],candidate['url']):
        return None
    scope={**unit,'official_url':candidate['url']}
    if urlsplit(target_proof['url']).hostname=='yz.chsi.com.cn' or not official_source_url(target_proof['url'],scope):
        return None
    if not _replay_home_route(unit, declared, target_proof, root):
        return None
    raw=target_path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=target_proof.get('sha256'):
        return None
    parsed=_OriginalHTML()
    parsed.feed(decode_text(raw,'text/html'))
    normalized=lambda value:re.sub(r'\s+','',value).replace('（','(').replace('）',')')
    quote=target_proof.get('quote')
    if not isinstance(quote,str) or quote not in parsed.primary or not _own_primary_title(unit, quote):
        return None
    return {**declared,'status':'corroborated_official_origin','target_evidence':target_proof,
            'resolved_url':target_proof['url'],'target_check':{'code':'replayed_visible_own_title_and_both_original_hashes'}}


def ready_corroborated_candidates(report, registry, root, *, registry_sha256=None):
    """Independently replay CHSI declaration, target title, both hashes and redirect scope."""
    root=Path(root).resolve()
    units={unit['id']:unit for unit in registry['units']}
    names=[unit['name'] for unit in registry['units']]
    rows,errors=[],[]
    for row in report['units']:
        unit=units.get(row['unit_id'])
        if not unit or unit.get('official_url'):
            continue
        values=[]
        for candidate in row['candidates']:
            if candidate['status']!='corroborated_official_origin':
                continue
            try:
                result=_replay_corroborated(unit,candidate,root,names)
                if result:
                    values.append(result)
                else:
                    errors.append({'unit_id':unit['id'],'url':candidate['url'],'code':'corroboration_replay_failed'})
            except (OSError,ValueError,KeyError) as error:
                errors.append({'unit_id':unit['id'],'url':candidate['url'],'code':'invalid_original','message':str(error)})
        if values:
            rows.append({'unit_id':unit['id'],'unit_name':unit['name'],'unit_code':unit.get('unit_code'),'candidates':values})
    return {'metadata':{'status':'ready_for_independent_review','checked_at':datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'registry_sha256':registry_sha256,'ready_units':len(rows),'candidate_origins':sum(len(row['candidates']) for row in rows),
            'notice':'CHSI声明、目标可见主标题、双原文hash及重定向归属均已离线重放；仅新增候选，不修改units。'},'units':rows,'errors':errors}


def verify_report_targets(report, registry, root, *, workers=4, limit=200, save=None):
    if type(workers) is not int or not 1<=workers<=8 or type(limit) is not int or not 1<=limit<=1000:
        raise ValueError('target verification limits are invalid')
    units = {unit['id']:unit for unit in registry['units']}
    rows = [{**row,'candidates':list(row['candidates'])} for row in report['units']]
    jobs = [(row_index,index,candidate) for row_index,row in enumerate(rows) for index,candidate in enumerate(row['candidates'])
            if candidate['status']=='needs_review' and candidate['reason']!='auxiliary_department_site' and 'target_check' not in candidate][:limit]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(verify_candidate_home,units[rows[row]['unit_id']],candidate,root):(row,index) for row,index,candidate in jobs}
        for future in as_completed(futures):
            row,index = futures[future]
            rows[row] = {**rows[row],'candidates':[future.result() if position==index else value for position,value in enumerate(rows[row]['candidates'])]}
            if save:
                save({**report,'units':rows,'metadata':{**report['metadata'],'targets_checked':sum('target_check' in candidate for item in rows for candidate in item['candidates'])}})
    rows = [{**row,'status':'recovered' if any(candidate['status'] in {'declared_official_origin','corroborated_official_origin'} for candidate in row['candidates']) else row['status']} for row in rows]
    return {**report,'units':rows,'metadata':{**report['metadata'],
        'targets_checked':sum('target_check' in candidate for row in rows for candidate in row['candidates']),
        'targets_pending':sum(candidate['status']=='needs_review' and candidate['reason']!='auxiliary_department_site' and 'target_check' not in candidate for row in rows for candidate in row['candidates']),
        'corroborated_units':sum(any(candidate['status']=='corroborated_official_origin' for candidate in row['candidates']) for row in rows),
        'recovered_units':sum(row['status']=='recovered' for row in rows),'review_units':sum(row['status']=='needs_review' for row in rows)}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=PROJECT_ROOT)
    parser.add_argument('--registry',type=Path,default=PROJECT_ROOT/'data/units.json')
    parser.add_argument('--national',type=Path,default=PROJECT_ROOT/'data/national_sources.json')
    parser.add_argument('--inventory',type=Path,default=PROJECT_ROOT/'data/source_inventory.json')
    parser.add_argument('--output',type=Path,default=PROJECT_ROOT/'data/runtime/official-binding-candidates.json')
    parser.add_argument('--verify-targets',action='store_true',help='Read declared public homepages; never bypass login or captcha.')
    parser.add_argument('--workers',type=int,default=4)
    parser.add_argument('--max-targets',type=int,default=200)
    args = parser.parse_args(argv)
    load = lambda path:json.loads(path.read_text('utf-8'))
    registry = load(args.registry)
    report = build_report(registry,load(args.national),load(args.inventory),args.root)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    def save(value):
        temporary = args.output.with_suffix('.tmp')
        temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),'utf-8')
        temporary.replace(args.output)
    save(report)
    if args.verify_targets:
        report = verify_report_targets(report,registry,args.root,workers=args.workers,limit=args.max_targets,save=save)
        save(report)
    print(json.dumps(report['metadata'],ensure_ascii=True))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
