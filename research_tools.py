"""Whitelisted research tools; discovered text never becomes an adopted admission fact."""
from copy import deepcopy
from itertools import zip_longest
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import time
from urllib.parse import urlsplit

from evidence import exam_unresolved_reason
from grad_engine import match_programs, validate_profile
from planning_engine import plan_routes, suggest_adjustments
from public_sources import fetch_official_document, official_source_url, SourceError
from research_store import ResearchError
from source_privacy import contains_personal_roster

LIMITS={'max_rounds':8,'max_searches':4,'max_documents':4,'max_model_calls':8,
        'max_tool_calls':20,'max_seconds':180,'max_total_tokens':40000,'max_candidates':12}
SKILL_CATALOG=(
    ('understand-intent','理解需求',('find_units','find_candidates','propose_profile')),
    ('research-official-sources','研究官方资料',('find_units','find_candidates','search_official_sources','search_knowledge')),
    ('verify-evidence','核对原文依据',('read_official_source','search_knowledge')),
    ('compare-plans','比较备考方案',('compare_candidates','plan_subject_routes','suggest_adjustments')),
    ('assess-transfer-options','复试与调剂研究',('read_knowledge','read_official_source','search_official_sources')),
)
_SECRET=re.compile(r'\b(?:sk-[A-Za-z0-9_-]{8,}|Bearer\s+\S{6,}|AKID[A-Za-z0-9]{12,}|'
                   r'TC3-HMAC-SHA256\s+Credential\s*=|secret[_-]?(?:id|key)["\']?\s*[:=]\s*["\']?\S{4,})',re.I)
_PII=re.compile(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?<!\d)1[3-9]\d{9}(?!\d)|(?<!\d)\d{17}[\dXx](?!\d)')
_SOURCE_METADATA=('id','url','title','unit_id','unit_name','year','requested_year','status','source_type','checked_at',
                  'sha256','page_count','field_verification','original_available','text_origin','text_truncated',
                  'snapshot_status','content_notice','cached_at','cache_hit')


def _public_source(value):
    if contains_personal_roster(str(value.get('title','')),str(value.get('excerpt',''))):
        return {**value,'excerpt':'','text_origin':'metadata_only',
                'content_notice':'材料可能含逐人招生信息，仅保留来源和版本，不向研究对话展示名单。'}
    return value


def _knowledge_quality(value):
    if (not isinstance(value,dict) or type(value.get('version')) is not int or value['version']<1
            or value.get('state') not in {'accepted','pending','excluded'} or type(value.get('reusable')) is not bool):
        raise ValueError('invalid knowledge quality')
    reasons=[]
    for reason in value.get('reasons',[])[:8] if isinstance(value.get('reasons'),list) else []:
        if not isinstance(reason,dict):continue
        code=str(reason.get('code','quality_check'))[:80];message=str(reason.get('message','资料需要继续核对。'))[:300]
        if _SECRET.search(code+' '+message):code,message='quality_check','资料未达到知识库准入要求。'
        reasons.append({'code':code,'message':message})
    return {'version':value['version'],'state':value['state'],'reusable':value['state']=='accepted' and value['reusable'],
            'material_type':str(value.get('material_type','unknown'))[:80],'reasons':reasons}


def _reusable_knowledge(entry):
    if not isinstance(entry,dict):return False
    try:quality=_knowledge_quality(entry.get('quality'))
    except ValueError:return False
    if quality['state']!='accepted' or quality['reusable'] is not True:return False
    if type(entry.get('year')) is not int:return False
    status=entry.get('status')
    return status=='published_snapshot' or status=='official_original_read' and entry.get('original_available') is True


def text(value, limit=240, empty=False):
    if (not isinstance(value,str) or len(value)>limit or (not empty and not value.strip())
            or any(ord(char)<32 and char not in '\n\t' for char in value) or _SECRET.search(value)):
        raise ResearchError('invalid_query','输入内容格式不正确；密钥请在连接设置中填写。')
    return value.strip()


def _object(value, allowed):
    if not isinstance(value,dict) or value.keys()-set(allowed):
        raise ResearchError('invalid_tool_arguments','工具参数缺失或包含不支持的字段。')
    return value


def _schema(name,description,properties,required=()):
    return {'type':'function','function':{'name':name,'description':description,
            'parameters':{'type':'object','properties':properties,'required':list(required),'additionalProperties':False}}}


def gated_resolver(host,*args,**kwargs):
    if str(host).lower().rstrip('.')=='yz.chsi.com.cn':
        raise SourceError('restricted','研招网访问门禁保持生效，不发起新请求。')
    return socket.getaddrinfo(host,*args,**kwargs)


TOOLS=[
    _schema('find_units','查询全国招生单位索引，未入库专业不等于学校不招生。',{'query':{'type':'string'},'province':{'type':'string'}}),
    _schema('find_candidates','查询已发布核验快照的有限候选。可以用专业词或408等科目查询；返回范围不代表全国全集。',
            {'query':{'type':'string'},'unit_id':{'type':'string'}},('query',)),
    _schema('search_official_sources','使用元宝搜索查公共学校、专业、年度资料。不得发送姓名、电话、成绩单、API Key等个人信息。搜索结果仅为线索。',
            {'query':{'type':'string'},'unit_id':{'type':'string'},'year':{'type':'integer'}},('query',)),
    _schema('read_official_source','按已发现的source_id读取已确认学校官网原文；不能读取任意URL，研招网门禁保持不请求。',
            {'source_id':{'type':'string'},'unit_id':{'type':'string'}},('source_id','unit_id')),
    _schema('compare_candidates','对已经发现的本地核验候选调用资格三态、科目距离、偏好排序和Pareto算法，只使用用户已填条件。',
            {'program_ids':{'type':'array','items':{'type':'string'},'maxItems':12}}),
    _schema('plan_subject_routes','比较0/1/2个考试单元调整的路线；未知科目不当作零换科。',
            {'program_ids':{'type':'array','items':{'type':'string'},'maxItems':12}}),
    _schema('suggest_adjustments','试算最小条件调整，不修改保存的个人条件，不放宽学历等资格；仅分析显式列出的科目数、地区、年费情景。',
            {'allowed_changes':{'type':'object'},'program_ids':{'type':'array','items':{'type':'string'},'maxItems':12}},('allowed_changes',)),
    _schema('read_knowledge','读取官方规则知识索引与适用年度，规则和来源链接不能替代具体学校目录。',
            {'topic':{'type':'string','enum':['overview','subjects','evidence','transfer','years']}}),
    _schema('search_knowledge','优先检索本机已积累的公共资料。缓存保留原读取日期与年度，不等于刚联网核验；不能把线索当作已采用事实。',
            {'query':{'type':'string'},'unit_id':{'type':'string'},'year':{'type':'integer'}},('query',)),
    _schema('propose_profile','把用户原句理解成待确认的备考条件；每个字段附完整用户原句。不会自动应用，必须等待用户确认后再按新条件计算。',
            {'updates':{'type':'object'},'evidence':{'type':'array','items':{'type':'object','properties':{
                'field':{'type':'string'},'quote':{'type':'string'}},'required':['field','quote'],'additionalProperties':False}}},('updates','evidence')),
]


class ResearchTools:
    def __init__(self, admission, settings, task, data_dir, *, network=None, source_fetcher=fetch_official_document,
                 stop_event=None,deadline=None,knowledge_store=None):
        self.admission,self.settings,self.task=admission,settings,task
        self.data_dir=Path(data_dir).resolve();self.network=network;self.source_fetcher=source_fetcher
        self.stop_event=stop_event;self.deadline=deadline if deadline is not None else time.monotonic()+LIMITS['max_seconds']
        self.programs={};self.sources={};self.algorithm={};self.gaps=[];self.searched_units=set()
        self.profile_proposal=None
        self._knowledge_store=knowledge_store;self.knowledge_errors=[];self._knowledge_failures={};self.saved_sources=set();self.cache_hits=set()
        self._knowledge_states={};self._filtered_sources=set()
        self.loaded_skills=();self.executed_tools=set();self._algorithm_signature=None
        self.usage={'searches':0,'documents':0,'tool_calls':0,'model_calls':0,'rounds':0,'total_tokens':0,'algorithm_calls':0}
        self.bootstrap=admission.catalog_bootstrap()
        self.units={row['id']:row for row in self.bootstrap.get('units',[]) if isinstance(row,dict) and isinstance(row.get('id'),str)}
        self.revision=self.bootstrap.get('revision')

    def _unit(self,identifier):
        if not isinstance(identifier,str) or identifier not in self.units:
            raise ResearchError('unknown_unit','请先从全国单位索引确认学校身份。')
        if self.task['unit_ids'] and identifier not in self.task['unit_ids']:
            raise ResearchError('outside_research_scope','该学校不在本轮选定研究范围内。')
        return self.units[identifier]

    def run(self,name,args):
        self._check_control()
        if self.usage['tool_calls']>=LIMITS['max_tool_calls']:
            raise ResearchError('research_budget_exceeded','已达到本轮工具调用上限。',409)
        self.usage['tool_calls']+=1
        handlers={'find_units':self.find_units,'find_candidates':self.find_candidates,
                  'search_official_sources':self.search_sources,'read_official_source':self.read_source,
                  'compare_candidates':self.compare,'plan_subject_routes':self.routes,
                  'suggest_adjustments':self.adjustments,'read_knowledge':self.knowledge,'propose_profile':self.propose_profile,
                  'search_knowledge':self.search_knowledge}
        if name not in handlers:raise ResearchError('unknown_tool','该操作不在允许的研究工具中。')
        result=handlers[name](args)
        self._check_control()
        if not isinstance(result,dict) or not result.get('error'):self.executed_tools.add(name)
        return result

    def _check_control(self):
        if time.monotonic()>=self.deadline:raise ResearchError('research_budget_exceeded','已达到本轮研究时间上限。',409)
        if self.stop_event is not None and self.stop_event.is_set():raise ResearchError('cancelled','研究已取消。',409)

    def find_units(self,args):
        _object(args,('query','province'));query=text(args.get('query',''),120,True).casefold()
        province=text(args.get('province',''),40,True)
        values=[{key:unit.get(key) for key in ('id','name','province','official_url','unit_code','program_count','annual_status')}
                for unit in self.units.values() if (not query or query in (unit['name']+' '+str(unit.get('unit_code',''))).casefold())
                and (not province or unit.get('province')==province)]
        return {'units':values[:20],'total':len(values),'truncated':len(values)>20,'scope':'national_unit_index_not_complete_program_catalog'}

    def find_candidates(self,args):
        _object(args,('query','unit_id'));query=text(args.get('query'),120)
        uid=args.get('unit_id');unit=self._unit(uid) if uid else None
        if not unit and self.task['unit_ids']:
            units=self.task['unit_ids'][:8]
        elif not unit and self.task['profile'].get('provinces'):
            groups=[[row['id'] for row in self.units.values() if row.get('province')==province
                     and row.get('program_count',1)] for province in self.task['profile']['provinces']]
            units=[identifier for group in zip_longest(*groups) for identifier in group if identifier][:8]
        elif not unit:units=[]
        else:units=[uid]
        pages=[];total=0
        if units:
            for selected in units:
                page=self.admission.catalog_programs(unit_id=selected,year=self.task['reference_year'],q=query,limit=12,revision=self.revision)
                total+=page['total'];pages.append(page['programs'])
            rows=[row for group in zip_longest(*pages) for row in group if row is not None]
        else:rows,total=self._diverse_candidates(query)
        for row in rows[:12]:
            if len(self.programs)>=LIMITS['max_candidates'] and row['id'] not in self.programs:
                self.gaps.append('本轮已达到12个候选的研究上限；新的方向尚未纳入，可以调整范围后继续研究。')
                break
            if self.task['unit_ids'] and row['unit_id'] not in self.task['unit_ids']:continue
            if row.get('year')!=self.task['reference_year']:continue
            self.programs[row['id']]=deepcopy(row)
            for ref in row.get('evidence',[]) if isinstance(row.get('evidence'),list) else [row.get('evidence')]:
                if not isinstance(ref,dict) or not isinstance(ref.get('url'),str):continue
                sid=self._source({'url':ref['url'],'title':f"{row['unit_name']} {row.get('name','')}目录依据",
                              'unit_id':row['unit_id'],'year':row['year'],'status':'published_snapshot',
                              'excerpt':str(ref.get('quote',''))[:1200],'source_type':'published_catalog'})
                if sid:self._save_source(sid)
        return {'candidates':[self._candidate(row) for row in rows[:12] if row['id'] in self.programs],
                'total_in_query':total,'candidate_limit':12,'queried_unit_ids':[k for k in units if k], 'revision':self.revision,
                'verification_mode':'published_snapshot','national_complete':False,
                'notice':'本轮有限候选来自最后发布的核验快照，未重新核验全国原文。'}

    def _diverse_candidates(self,query):
        groups={};total=0;offset=0
        # A finite paged scan avoids filling the report with one school's first directions.
        for _ in range(5):
            self._check_control()
            page=self.admission.catalog_programs(year=self.task['reference_year'],q=query,offset=offset,limit=200,revision=self.revision)
            total=page['total'];rows=page['programs']
            for row in rows:groups.setdefault(row.get('unit_id'),[]).append(row)
            offset+=len(rows)
            if not rows or offset>=total or len(groups)>=12:break
        if offset<total:self.gaps.append('本轮按学校分散抽取有限候选，尚未遍历该查询的全部方向；可限定地区或学校继续。')
        return [row for group in zip_longest(*groups.values()) for row in group if row is not None],total

    @staticmethod
    def _candidate(row):
        keys=('id','unit_id','unit_name','name','code','department','direction','study_mode','degree_type','year',
              'subjects','tuition','duration_years','eligibility_evidence_complete','requires_review','notes')
        result={key:deepcopy(row.get(key)) for key in keys if key in row}
        return {**result,'verification_mode':'published_snapshot','exam_unresolved_reason':exam_unresolved_reason(row)}

    def _source(self,value):
        url=value.get('url','');parsed=urlsplit(url)
        if parsed.scheme not in {'https','http'} or not parsed.hostname or parsed.username or parsed.password:return None
        source_id='s_'+hashlib.sha256((url+'|'+str(value.get('year'))+'|'+str(value.get('unit_id'))).encode()).hexdigest()[:20]
        current=self.sources.get(source_id,{})
        if current.get('status')=='official_original_read' and not current.get('knowledge_filtered'):return source_id
        if current.get('knowledge_filtered') and current.get('status')==value.get('status'):return source_id
        unit=self.units.get(value.get('unit_id'))
        if unit:value={**value,'unit_name':unit['name']}
        self.sources[source_id]={'id':source_id,**_public_source(value)}
        return source_id

    def _knowledge(self):
        if self._knowledge_store is None:
            from knowledge_store import KnowledgeStore
            self._knowledge_store=KnowledgeStore(self.data_dir)
        return self._knowledge_store

    def _save_source(self,source_id,query='',document=None):
        source=self.sources[source_id]
        if source.get('knowledge_filtered') and document is None:return
        try:
            record=self._knowledge().save_source(source,research_id=self.task.get('id',''),query=query,document=document)
            quality=_knowledge_quality(record.get('quality') if isinstance(record,dict) else None)
            if quality['state']=='excluded':
                self._filter_source(source_id,quality)
                self._clear_knowledge_failure(source_id)
                return
            identifier=record.get('id') if isinstance(record,dict) else None
            if not isinstance(identifier,str) or not identifier:raise ValueError('invalid knowledge response')
            canonical=(any(key in record and record[key]!=source.get(key) for key in ('status','year','title','checked_at'))
                or 'sha256' in record and (record['sha256'] or '')!=(source.get('sha256') or '')
                or 'excerpt' in record and not str(source.get('excerpt','')).startswith(str(record['excerpt'])))
            if canonical:
                source=self._canonical_source(source,record)
            self.sources[source_id]={**source,'knowledge_id':identifier,'knowledge_saved':True,'knowledge_quality':quality,
                                     'knowledge_filtered':False,'knowledge_filter_reason':''}
            self.saved_sources.add(identifier)
            self._knowledge_states[identifier]=quality['state'];self._filtered_sources.discard(source_id)
            self._clear_knowledge_failure(source_id)
        except Exception:
            message='这份资料的知识库保存尚未完成；本轮来源仍保留，可以稍后重试。'
            self._knowledge_failures[source_id]=message;self.gaps.append(message)
            self.sources[source_id]={**source,'knowledge_saved':False}

    def _canonical_source(self,source,record):
        metadata={key:deepcopy(record[key]) for key in (*_SOURCE_METADATA,'excerpt') if key in record and key!='id'}
        reusable=_reusable_knowledge(record)
        if reusable:self.cache_hits.add(record['id'])
        return _public_source({**metadata,'id':source['id'],'requested_year':source.get('requested_year',record.get('requested_year')),
            'source_type':'knowledge_cache','knowledge_reused':True,'cache_hit':reusable,'cached_at':record.get('checked_at'),
            'notice':'本次发现的链接对应既有留档，采用留档的实际年度、摘录与原读取日期；未重新读取原文，招生字段仍须审校。'})

    def _clear_knowledge_failure(self,source_id):
        previous=self._knowledge_failures.pop(source_id,None)
        if previous and previous not in self._knowledge_failures.values():self.gaps=[gap for gap in self.gaps if gap!=previous]

    def _filter_source(self,source_id,quality):
        source=self.sources[source_id];previous=source.get('knowledge_id')
        if previous:
            self.saved_sources.discard(previous);self._knowledge_states.pop(previous,None)
        reason='；'.join(item['message'] for item in quality['reasons']) or '资料未达到知识库准入要求。'
        metadata={key:source[key] for key in _SOURCE_METADATA if key in source}
        self.sources[source_id]={**metadata,'excerpt':'','knowledge_id':None,'knowledge_saved':False,'knowledge_filtered':True,
                                 'knowledge_quality':quality,'knowledge_filter_reason':reason,
                                 'notice':'这份来源已过滤，未进入知识库；仅保留来源元数据与原因。'}
        self._filtered_sources.add(source_id)

    def search_knowledge(self,args):
        _object(args,('query','unit_id','year'));query=text(args.get('query'),240)
        if _PII.search(query):raise ResearchError('private_search_query','知识库查询只接受公开学校、专业和年度资料。')
        uid=args.get('unit_id','')
        if uid:self._unit(uid)
        year=args.get('year',self.task['reference_year'])
        if type(year) is not int or not 2020<=year<=self.task['target_year']:raise ResearchError('invalid_year','知识库检索年份不正确。')
        try:
            entries=[];query_total=0
            for page in range(1,5):
                batch=self._knowledge().search(query=query,unit_id=uid,page=page,page_size=12,quality='accepted')
                query_total=batch.get('total',0)
                entries.extend(entry for entry in batch.get('entries',[]) if _reusable_knowledge(entry) and entry.get('year')==year)
                if len(entries)>=8 or page*12>=query_total:break
        except Exception:
            message='本机知识库暂时无法读取；可继续使用已发布目录或主动检索。'
            self.knowledge_errors.append(message);self.gaps.append(message)
            return {'results':[],'total':0,'error':{'code':'knowledge_unavailable','message':message}}
        self.knowledge_errors=[message for message in self.knowledge_errors if message!='本机知识库暂时无法读取；可继续使用已发布目录或主动检索。']
        self.gaps=[message for message in self.gaps if message!='本机知识库暂时无法读取；可继续使用已发布目录或主动检索。']
        results=[]
        for entry in entries[:8]:
            if self.task['unit_ids'] and entry.get('unit_id') not in self.task['unit_ids']:continue
            value={key:deepcopy(entry[key]) for key in ('url','title','unit_id','unit_name','year','requested_year','status','excerpt','checked_at','sha256','field_verification','original_available','text_origin','text_truncated') if key in entry}
            quality=_knowledge_quality(entry['quality'])
            value={**value,'knowledge_id':entry.get('id'),'knowledge_saved':True,'cache_hit':True,
                   'knowledge_quality':quality,'knowledge_filtered':False,
                   'cached_at':entry.get('checked_at'),'notice':'复用本机留档，读取日期保持原样；本次没有重新联网核验。'}
            sid=self._source(value)
            if sid and self.sources[sid].get('knowledge_quality',{}).get('state')=='accepted' and self.sources[sid].get('knowledge_quality',{}).get('reusable') is True:
                self.cache_hits.add(entry.get('id') or sid);results.append(deepcopy(self.sources[sid]))
        return {'results':results,'total':len(results),'query_total':query_total,
                'notice':'仅复用通过知识准入的资料；待核摘要和过滤来源不作为跨轮知识缓存。缓存也不等于实时验证或新增招生事实。'}

    def search_sources(self,args):
        self._check_control()
        _object(args,('query','unit_id','year'));query=text(args.get('query'),240)
        if _PII.search(query):raise ResearchError('private_search_query','搜索词仅限公开学校、专业和年份，请移除个人信息。')
        year=args.get('year',self.task['reference_year'])
        if type(year) is not int or not 2020<=year<=self.task['target_year']:
            raise ResearchError('invalid_year','检索年份不正确。')
        unit=self._unit(args['unit_id']) if args.get('unit_id') else None
        resolver=getattr(self.admission,'_search_config',None)
        if callable(resolver):config=resolver()
        else:
            resolver=getattr(self.settings,'runtime_yuanbao',None)
            config=resolver() if callable(resolver) else None
            if not config:
                resolver=getattr(self.settings,'yuanbao_key',None)
                key=resolver() if callable(resolver) else ''
                key=key or os.environ.get('GRAD_SEARCH_API_KEY','')
                config={'provider':'yuanbao','auth_mode':'api_key','api_key':key} if key else None
        if not config:raise ResearchError('search_not_configured','元宝搜索尚未配置；已发布资料仍可研究。',503)
        if self.usage['searches']>=LIMITS['max_searches']:
            raise ResearchError('research_budget_exceeded','已达到本轮元宝检索上限。',409)
        self.usage['searches']+=1
        body={'Query':f"{year}年 {unit['name']+' ' if unit else ''}{query}"}
        if unit:
            host=urlsplit(unit.get('official_url') or '').hostname
            if host:body['Site']=host
            self.searched_units.add(unit['id'])
        if self.network is None:
            from ai_transport import AITransport
            self.network=AITransport()
        raw=self.network.request_json(config,'POST','',body,stop_event=self.stop_event)
        self._check_control()
        payload=raw.get('Response',raw) if isinstance(raw,dict) else None
        if not isinstance(payload,dict) or not isinstance(payload.get('Pages'),list) or payload.get('Error'):
            raise ResearchError('search_invalid_response','元宝返回内容无法解析。',502)
        results=[]
        for encoded in payload['Pages'][:50]:
            try:
                row=json.loads(encoded) if isinstance(encoded,str) else encoded
                if not isinstance(row,dict):continue
                parsed=urlsplit(row.get('url',''));host=parsed.hostname or ''
                if unit and not official_source_url(row.get('url'),unit):continue
                if not unit and not host.endswith(('.edu.cn','.ac.cn','.gov.cn','.chsi.com.cn')):continue
                value={'url':row['url'],'title':str(row.get('title') or '待核验的检索结果')[:300],
                       'unit_id':unit['id'] if unit else None,'unit_name':unit['name'] if unit else None,'year':None,'requested_year':year,
                       'status':'search_lead','source_type':'yuanbao_search','checked_at':None,
                       'excerpt':str(row.get('content') or row.get('passage') or '')[:1800]}
                sid=self._source(value)
                if sid:
                    self._save_source(sid,query=body['Query']);results.append(deepcopy(self.sources[sid]))
            except (ValueError,TypeError,KeyError):continue
        return {'provider':'yuanbao_search','query':body['Query'],'request_id':str(payload.get('RequestId',''))[:120],
                'results':results,'notice':'检索片段不是完整原文；年度与字段尚未核验，不能直接用于资格或数值判断。'}

    def read_source(self,args):
        self._check_control()
        _object(args,('source_id','unit_id'));source_id=text(args.get('source_id'),100)
        source=self.sources.get(source_id);unit=self._unit(args.get('unit_id'))
        if source is None:raise ResearchError('unknown_source','请先通过检索或已发布目录发现这份来源。')
        if urlsplit(source['url']).hostname=='yz.chsi.com.cn':
            raise ResearchError('source_restricted','研招网访问门禁保持生效；可查看已保存依据，不发起新请求。',409)
        if not official_source_url(source['url'],unit):raise ResearchError('wrong_unit','来源不属于该学校的已核准官网。')
        if source.get('status')=='official_original_read':return deepcopy(source)
        if self.usage['documents']>=LIMITS['max_documents']:
            raise ResearchError('research_budget_exceeded','已达到本轮官方原文读取上限。',409)
        self.usage['documents']+=1
        doc=self.source_fetcher(source['url'],unit,timeout=min(15,max(.1,self.deadline-time.monotonic())),
                                max_bytes=8*1024*1024,resolver=gated_resolver)
        self._check_control()
        raw=doc.get('raw');digest=doc.get('sha256')
        if (not isinstance(raw,bytes) or len(raw)>8*1024*1024 or hashlib.sha256(raw).hexdigest()!=digest
                or doc.get('attribution_status')!='verified' or not official_source_url(doc.get('url'),unit)
                or urlsplit(doc['url']).hostname=='yz.chsi.com.cn'):
            raise ResearchError('source_needs_review','原文归属或完整性需要复核，未作为已核验来源采用。',409)
        directory=self.data_dir/'runtime/research-sources';directory.mkdir(parents=True,exist_ok=True)
        target=directory/(digest+'.bin')
        if not target.resolve().is_relative_to(self.data_dir):raise ResearchError('invalid_source','原文存储位置不正确。')
        if not target.exists():
            temporary=target.with_suffix('.tmp');temporary.write_bytes(raw);temporary.replace(target)
        heading=str(doc.get('document_heading') or doc.get('document_title') or '')
        years=set(int(y) for y in re.findall(r'(?<!\d)(20\d{2})年',heading))
        value={**source,'unit_id':unit['id'],'unit_name':unit['name'],'title':doc.get('document_title') or source['title'],
               'url':doc['url'],'year':next(iter(years)) if len(years)==1 else None,'checked_at':doc.get('checked_at'),
               'sha256':digest,'status':'official_original_read','source_type':'official_original',
               'excerpt':str(doc.get('text',''))[:12000],'page_count':len(doc.get('pages',[])),
               'field_verification':'not_structured','notice':'已读取并保存官方原文；其中具体数值、范围和年度仍需逐字段核验，不自动写入招生库。'}
        self.sources[source_id]=_public_source(value)
        self._save_source(source_id,document=doc)
        return deepcopy(self.sources[source_id])

    def _selected(self,args):
        ids=args.get('program_ids',list(self.programs))
        if not isinstance(ids,list) or not ids or len(ids)>12 or any(not isinstance(k,str) or k not in self.programs for k in ids):
            raise ResearchError('unknown_candidates','请先选择本轮已查询的真实候选。')
        rows=[deepcopy(self.programs[k]) for k in dict.fromkeys(ids)]
        blocked=[row['id'] for row in rows if exam_unresolved_reason(row)]
        if blocked:self.gaps.append('存在科目标识待核候选，已排除其换科与排序计算。')
        return [row for row in rows if row['id'] not in blocked]

    def compare(self,args):
        _object(args,('program_ids',));rows=self._selected(args)
        result=match_programs(rows,self.task['profile'])
        self.algorithm['matching']=result
        self.executed_tools.add('compare_candidates')
        return result

    def routes(self,args):
        _object(args,('program_ids',));result=plan_routes(self._selected(args),self.task['profile'])
        self.algorithm['routes']=result
        self.executed_tools.add('plan_subject_routes')
        return result

    def adjustments(self,args):
        _object(args,('program_ids','allowed_changes'));changes=args.get('allowed_changes')
        if not isinstance(changes,dict) or not changes or len(changes)>3:
            raise ResearchError('invalid_adjustment','请选择有限的条件试算情景。')
        if any(not isinstance(values,list) or len(values)>4 for values in changes.values()):
            raise ResearchError('invalid_adjustment','每项条件最多试算四个值。')
        result=suggest_adjustments(self._selected(args),self.task['profile'],changes)
        self.algorithm['adjustments']=result
        self.executed_tools.add('suggest_adjustments')
        return result

    def ensure_algorithms(self):
        if not self.programs:return []
        stop=self.stop_event
        if stop is not None:
            cancelled=stop.user_stop.is_set() or stop.service_stop.is_set() if hasattr(stop,'user_stop') else stop.is_set()
            if cancelled:return []
        signature=hashlib.sha256(json.dumps([self.programs,self.task['profile']],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        if signature==self._algorithm_signature:return []
        self.compare({});actions=['compare_candidates']
        units={subject.get('exam_unit') for subject in self.task['profile'].get('subjects',[])}
        expected={subject.get('exam_unit') for row in self.programs.values() for subject in row.get('subjects',[])}
        if expected and expected<=units:
            self.routes({});actions.append('plan_subject_routes')
        else:self.algorithm.pop('routes',None)
        self._algorithm_signature=signature
        self.usage['algorithm_calls']+=len(actions)
        return actions

    def questions(self):
        profile=self.task['profile'];questions=[]
        relative=any(word in self.task.get('query','') for word in ('明年','今年','后年'))
        if relative and 'target_year' not in self.task.get('confirmed_fields',[]):
            year=self.task['target_year']
            questions.append({'field':'target_year','label':'确认目标入学年份',
                'message':f'当前按{year}年入学、{year-1}年考试研究。“明年”是指考试还是入学？其他年份请补充说明。',
                'options':[{'label':f'按{year}年入学继续','value':year}]})
        subjects=profile.get('subjects',[])
        if not subjects or any(item.get('subject_changes',{}).get('uncertain') for kind in ('matches','needs_confirmation') for item in self.algorithm.get('matching',{}).get(kind,[])):
            questions.append({'field':'subjects','label':'已经准备了哪些考试科目？',
                'message':'请按考试单元补充政治、外语、数学或专业基础、业务课；只准备408不能视为其他科目已完成。','options':[]})
        if profile.get('education') in (None,'unknown'):
            questions.append({'field':'education','label':'你目前的最高学历是什么？','message':'学历条件会改变可研究范围，尚未填写时资格保留待确认。',
                'options':[{'label':'本科','value':'bachelor'},{'label':'硕士','value':'master'},{'label':'专科','value':'associate'}]})
        if profile.get('study_mode') in (None,'unknown'):
            questions.append({'field':'study_mode','label':'希望选择哪种学习方式？','message':'全日制与非全日制的费用、条件和备考路线分别核对。',
                'options':[{'label':'全日制','value':'全日制'},{'label':'非全日制','value':'非全日制'}]})
        return questions[:3] if self.programs or relative else []

    def skills(self):
        return [{'id':identifier,'label':label,'loaded':identifier in self.loaded_skills,'related_tools':list(related),
                 'executed_tools':[tool for tool in related if tool in self.executed_tools],
                 'status':'not_loaded' if identifier not in self.loaded_skills else 'used' if self.executed_tools.intersection(related) else 'loaded',
                 'notice':'已加载规程与相关工具已执行分别记录，不代表全部规程已自动验收。'}
                for identifier,label,related in SKILL_CATALOG]

    def knowledge(self,args):
        _object(args,('topic',));topic=args.get('topic','overview')
        path=Path(__file__).parent/'knowledge/research_rules.json'
        payload=json.loads(path.read_text('utf-8'))
        if topic not in {'overview','subjects','evidence','transfer','years'}:raise ResearchError('unknown_topic','该规则主题不存在。')
        return {'topic':topic,'rules':[row for row in payload['rules'] if topic=='overview' or row['topic']==topic],
                'notice':'规则资料保留适用年度，不替代具体学校公告和目标年度资格核验。'}

    def propose_profile(self,args):
        _object(args,('updates','evidence'));updates=args.get('updates');proof=args.get('evidence')
        if not isinstance(updates,dict) or not updates or len(updates)>12 or not isinstance(proof,list) or len(proof)>16:
            raise ResearchError('invalid_profile_proposal','条件提案需要有限的字段和对应用户原句。')
        inputs=[self.task['query'],*self.task.get('follow_ups',[])]
        quotes={}
        for item in proof:
            _object(item,('field','quote'));field=item.get('field');quote=text(item.get('quote'),1000)
            if not isinstance(field,str) or field not in updates or not any(quote in source for source in inputs):
                raise ResearchError('unsubstantiated_profile','识别条件缺少对应用户原句，不能作为条件提案。')
            quotes[field]={'field':field,'quote':quote}
        if set(quotes)!=set(updates):raise ResearchError('unsubstantiated_profile','每个识别字段都需要用户原句。')
        checked=validate_profile({**self.task['profile'],**updates})
        self.profile_proposal={'updates':deepcopy(updates),'profile':checked,'evidence':list(quotes.values()),
                               'requires_confirmation':True,
                               'notice':'以下是AI从你的原句理解的条件；确认后才用于新一轮算法，不会自动覆盖已填条件。'}
        return deepcopy(self.profile_proposal)

    def report(self,assistant_summary='',questions=()):
        candidates=[self._candidate(row) for row in self.programs.values()]
        gaps=list(dict.fromkeys([*self.gaps,'全国专业目录尚未完整覆盖，本轮候选不代表全国最优或全部选择。',
                               '2027资料仅作2028入学规划参考；具体报考条件和新年度目录需继续核验。',
                               *(['尚未找到已发布的可计算候选，可以继续调整关键词或补充意向地区。'] if not candidates else [])]))
        actual_questions=self.questions()
        if self.profile_proposal:actual_questions=[{'field':'profile_proposal','label':'请确认识别到的备考条件是否准确。','message':'确认后才用于新一轮算法。','options':[]}]
        return {'summary':f"本轮整理了{len({row.get('unit_id') for row in candidates})}所学校的{len(candidates)}个已发布候选、{len(self.sources)}份来源或检索线索。",
                'assistant_summary':assistant_summary,'questions':actual_questions,'candidates':candidates,
                'evidence':list(deepcopy(self.sources).values()),'algorithm':deepcopy(self.algorithm),'gaps':gaps,
                'profile_proposal':deepcopy(self.profile_proposal),
                'skills':self.skills(),'knowledge':{'saved_count':len(self.saved_sources),'cache_hits':len(self.cache_hits),
                    'accepted_count':sum(state=='accepted' for state in self._knowledge_states.values()),
                    'pending_count':sum(state=='pending' for state in self._knowledge_states.values()),'filtered_count':len(self._filtered_sources),
                    'errors':list(dict.fromkeys([*self.knowledge_errors,*self._knowledge_failures.values()])),
                    'notice':'保存数包含通过准入与待核资料；仅通过准入资料可复用。过滤来源不入库，新增原文仍须字段审校才能用于招生事实。'},
                'coverage':{'candidate_count':len(candidates),'candidate_unit_count':len({row.get('unit_id') for row in candidates}),
                            'turn_id':self.task.get('turn_id',0),'searched_units':sorted(self.searched_units),
                            'national_complete':False,'reference_year':self.task['reference_year'],'target_year':self.task['target_year'],
                            'revision':self.revision,'verification_mode':'bounded_research'}}
