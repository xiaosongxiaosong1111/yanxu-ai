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

from decision_engine import analyze as decision_analysis
from evidence import exam_unresolved_reason
from evidence_ledger import audit as evidence_audit
from grad_engine import EngineValidationError, match_programs, rank_order_key, validate_profile
from planning_engine import plan_routes, suggest_adjustments
import official_search
import unit_bindings
from public_sources import fetch_official_document, official_roots, official_source_url, SourceError
from research_store import ResearchError
from source_privacy import contains_personal_roster
import research_knowledge
import research_fields
import research_candidate_tools
import research_official_tools
import research_admission_tools
from research_plan import build_plan

LIMITS={'max_rounds':8,'max_searches':4,'max_documents':4,'max_model_calls':8,
        'max_tool_calls':20,'max_seconds':180,'max_total_tokens':40000,'max_candidates':12,
        'max_discoveries':2,'max_navigation_fetches':12,'max_links':20}
SKILL_CATALOG=(
    ('understand-intent','理解需求',('find_units','find_candidates','propose_profile')),
    ('research-official-sources','研究官方资料',('find_units','find_candidates','search_official_sources','discover_official_entry','discover_official_links','search_knowledge','search_knowledge_segments','propose_source_candidates')),
    ('verify-evidence','核对原文依据',('read_official_source','search_knowledge','search_knowledge_segments','read_knowledge_segment','propose_source_candidates','propose_admission_facts')),
    ('compare-plans','比较备考方案',('compare_candidates','analyze_decisions','plan_subject_routes','suggest_adjustments')),
    ('assess-transfer-options','复试与调剂研究',('read_knowledge','read_official_source','search_official_sources')),
)
_SECRET=re.compile(r'\b(?:sk-[A-Za-z0-9_-]{8,}|Bearer\s+\S{6,}|AKID[A-Za-z0-9]{12,}|'
                   r'TC3-HMAC-SHA256\s+Credential\s*=|secret[_-]?(?:id|key)["\']?\s*[:=]\s*["\']?\S{4,})',re.I)
_PII=re.compile(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?<!\d)1[3-9]\d{9}(?!\d)|(?<!\d)\d{17}[\dXx](?!\d)')
_SOURCE_METADATA=('id','url','title','unit_id','unit_name','year','requested_year','status','source_type','checked_at',
                  'sha256','page_count','field_verification','original_available','text_origin','text_truncated',
                  'snapshot_status','content_notice','cached_at','cache_hit','validity','usable','extraction','source_group','index_state',
                  'authority_id','source_role')


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
    if entry.get('validity','active')!='active' or entry.get('usable') is False:return False
    if type(entry.get('year')) is not int:return False
    status=entry.get('status')
    return status=='published_snapshot' or status=='official_original_read' and entry.get('original_available') is True


def _heading_line(lines,index):
    current=lines[index]
    if index+1>=len(lines):return current
    following=lines[index+1]
    if re.search(r'20\d{2}\s*年(?:度)?$',current):return current+' '+following
    if len(current)>80 or len(following)>45 or re.search(r'[\d，,。；;:：]',following):return current
    catalog=(re.search(r'(?:招生|招收).*研究生$',current) and re.fullmatch(
        r'(?:[（(](?:学术型|专业型|学术学位|专业学位|全日制|非全日制)[）)])?招生(?:专业)?目录(?:及简介)?',following))
    scores=re.search(r'(?:初试|考试)成绩$',current) and re.fullmatch(r'基本要求(?:[（(](?:学校线|学院线|专业线)[）)])?',following)
    return current+following if catalog or scores else current


def _document_scope(doc):
    metadata=[str(doc.get(key) or '').strip() for key in ('document_heading','document_title')]
    lines=[line.strip() for line in str(doc.get('text') or '').splitlines()[:30] if line.strip()]
    body=[_heading_line(lines,index) for index in range(len(lines))]
    candidates=[body[lines.index(value)] if value in lines else value for value in metadata]+body
    headings=[];years=set()
    for index,candidate in enumerate(candidates):
        heading=re.sub(r'\s+',' ',candidate)
        if len(heading)>160 or re.search(r'[，,。；;]|发布时间|发布日期|往年|历年|对比|比较',heading):continue
        if re.search(r'(?:参考|参照|根据|依据|按照|依照|遵照|例如).*20\d{2}',heading):continue
        if not re.search(r'招生|招收|初试|复试|调剂|考试科目|考试大纲|分数线|学费|收费|学制|奖助|报考',heading):continue
        label=re.match(r'^[【\[]\s*(20\d{2})\s*(?:年)?(?:考研|招生)\s*[】\]]\s*',heading)
        title=heading[label.end():] if label else heading
        first_title=index==len(metadata) and len(heading)<=80 and re.search(
            r'(?:招生(?:简章|专业目录|目录|计划)|学费(?:收费)?(?:标准|公示表)?|收费(?:标准|公示表)|学制(?:表)?)$',heading)
        marker=r'(?:年|级)' if index<len(metadata) or first_title else r'年'
        if not re.match(r'[^\d]{0,60}20\d{2}\s*'+marker,title):continue
        found={int(year) for year in re.findall(r'(?<!\d)(20\d{2})\s*'+marker,heading)}
        if label:found.add(int(label.group(1)))
        if found:headings.append(heading);years.update(found)
    return (headings[0] if headings else metadata[1]),next(iter(years)) if len(years)==1 else None


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
    _schema('find_units','查询全国招生单位索引，未入库专业不等于学校不招生。支持常用简称；已登记的可信官网会作为可读入口返回 official_source_id，尚未登记官网的单位会明确标出。',
            {'query':{'type':'string'},'province':{'type':'string'}}),
    _schema('find_candidates','查询已发布核验快照的有限候选。可以用专业词或408等科目查询；返回范围不代表全国全集。',
            {'query':{'type':'string'},'unit_id':{'type':'string'}},('query',)),
    _schema('search_official_sources','使用元宝搜索查公共学校、专业、年度资料。不得发送姓名、电话、成绩单、API Key等个人信息。搜索结果仅为线索；未登记官网的单位会返回归属待确认的疑似官方页面。',
            {'query':{'type':'string'},'unit_id':{'type':'string'},'year':{'type':'integer'}},('query',)),
    _schema('read_official_source','按已发现的source_id读取已确认学校官网原文；不能读取任意URL，研招网门禁保持不请求。返回结果同时列出同站官方文章与PDF入口的source_id。',
            {'source_id':{'type':'string'},'unit_id':{'type':'string'}},('source_id','unit_id')),
    _schema('discover_official_links','从已读取的官方页面继续发现同站文章、栏目和PDF入口。只访问已核准官方域名，受深度、数量、时间与原文读取预算限制；返回的入口仍需逐个读取核验。',
            {'source_id':{'type':'string'},'unit_id':{'type':'string'},'depth':{'type':'integer'},'limit':{'type':'integer'}},('source_id','unit_id')),
    _schema('compare_candidates','对已经发现的本地核验候选调用资格三态、科目距离、偏好排序和Pareto算法，只使用用户已填条件。',
            {'program_ids':{'type':'array','items':{'type':'string'},'maxItems':12}}),
    _schema('plan_subject_routes','比较0/1/2个考试单元调整的路线；未知科目不当作零换科。',
            {'program_ids':{'type':'array','items':{'type':'string'},'maxItems':12}}),
    _schema('suggest_adjustments','试算最小条件调整，不修改保存的个人条件，不放宽学历等资格；仅分析显式列出的科目数、地区、年费情景。',
            {'allowed_changes':{'type':'object'},'program_ids':{'type':'array','items':{'type':'string'},'maxItems':12}},('allowed_changes',)),
    _schema('analyze_decisions','在已有候选上计算：先确认哪个未知条件最影响前k名、前k名在显式情景中的稳定性、以及按学校或地区分散的推荐。只使用已填条件和真实候选，不补造字段，不输出录取概率。',
            {'program_ids':{'type':'array','items':{'type':'string'},'maxItems':12},'top_k':{'type':'integer'},
             'scenarios':{'type':'array','maxItems':8,'items':{'type':'object','additionalProperties':False,
                 'properties':{'name':{'type':'string'},'changes':{'type':'object'}},'required':['name','changes']}}}),
    _schema('read_knowledge','读取官方规则知识索引与适用年度，规则和来源链接不能替代具体学校目录。',
            {'topic':{'type':'string','enum':['overview','subjects','evidence','transfer','years']}}),
    _schema('search_knowledge','优先检索本机已积累的公共资料。缓存保留原读取日期与年度，不等于刚联网核验；不能把线索当作已采用事实。',
            {'query':{'type':'string'},'unit_id':{'type':'string'},'year':{'type':'integer'}},('query',)),
    _schema('search_knowledge_segments','按学校和实际年度检索当前有效原文片段，定位长文后半段、表格和条款；本机向量索引可用时融合关键词与语义候选，退化时如实退回关键词；语义命中不等于字段已核验。',
            {'query':{'type':'string'},'entry_id':{'type':'string'},'unit_id':{'type':'string'},'year':{'type':'integer'}},('query',)),
    _schema('read_knowledge_segment','精读本轮已发现的segment_id及有限邻接上下文，保留原文位置、表头、否定和例外；失效来源不可复用。',
            {'segment_id':{'type':'string'},'before':{'type':'integer'},'after':{'type':'integer'},'max_chars':{'type':'integer'}},('segment_id',)),
    _schema('propose_source_candidates','从本轮已精读的当前有效官方目录原文提出最多六个临时方向；精确引用完整专业行与科目，不接收学校、年度、费用或命题范围。通过校验后仅供本轮确定性比较，资格仍未知。',
            {'source_id':{'type':'string'},'segment_id':{'type':'string'},
             'drafts':{'type':'array','minItems':1,'maxItems':6,'items':{'type':'object','additionalProperties':False,
                 'properties':{'code':{'type':'string'},'name':{'type':'string'},'department':{'type':'string'},
                     'direction':{'type':'string'},'study_mode':{'type':'string'},
                     'row':{'type':'object','additionalProperties':False,'properties':{
                         'segment_id':{'type':'string'},'char_start':{'type':'integer'},'char_end':{'type':'integer'},'quote':{'type':'string'}},
                         'required':['segment_id','char_start','char_end','quote']},
                     'exams':{'type':'object','additionalProperties':False,'properties':{
                         'segment_id':{'type':'string'},'char_start':{'type':'integer'},'char_end':{'type':'integer'},'quote':{'type':'string'}},
                         'required':['segment_id','char_start','char_end','quote']},
                     'supplements':{'type':'array','maxItems':2,'items':{'type':'object','additionalProperties':False,
                         'properties':{'field':{'type':'string','enum':['study_mode','politics']},
                             'citation':{'type':'object','additionalProperties':False,'properties':{
                                 'segment_id':{'type':'string'},'char_start':{'type':'integer'},'char_end':{'type':'integer'},'quote':{'type':'string'}},
                                 'required':['segment_id','char_start','char_end','quote']}},'required':['field','citation']}}},
                 'required':['code','name','study_mode','row','exams']}}},('source_id','segment_id','drafts')),
    _schema('propose_profile','把用户原句理解成待确认的备考条件；每个字段附完整用户原句。不会自动应用，必须等待用户确认后再按新条件计算。',
            {'updates':{'type':'object'},'evidence':{'type':'array','items':{'type':'object','properties':{
                'field':{'type':'string'},'quote':{'type':'string'}},'required':['field','quote'],'additionalProperties':False}}},('updates','evidence')),
]

TOOLS.extend([
    _schema('discover_official_entry','从登记官网或已留档权威声明发现研究生招生入口，核验新增入口归属并查目标年度公告；元宝未命中或未配置时仍可使用。分开报告官网、公告和正文状态。',
        {'unit_id':{'type':'string'},'year':{'type':'integer'},'kind':{'type':'string','enum':list(official_search.SEARCH_KINDS)}},('unit_id',)),
    _schema('propose_admission_facts','把本轮已精读完整条款的逐字引用提交程序核验学费、学制或资格。不得提交金额或条件判断；不完整资格仍待核，冲突或失效事实停止参与算法。',
        {'program_id':{'type':'string'},'drafts':{'type':'array','minItems':1,'maxItems':6,'items':{
            'type':'object','additionalProperties':False,'properties':{'field':{'type':'string','enum':['tuition','duration_years','eligibility']},
            'citation':{'type':'object','additionalProperties':False,'properties':{
                'segment_id':{'type':'string'},'char_start':{'type':'integer'},'char_end':{'type':'integer'},'quote':{'type':'string'}},
                'required':['segment_id','char_start','char_end','quote']}},'required':['field','citation']}}},('program_id','drafts')),
])


class ResearchTools:
    def __init__(self, admission, settings, task, data_dir, *, network=None, source_fetcher=fetch_official_document,
                 stop_event=None,deadline=None,knowledge_store=None,retriever=None,
                 navigation_fetcher=None,saved_declarations=None):
        self.admission,self.settings,self.task=admission,settings,task
        self.data_dir=Path(data_dir).resolve();self.network=network;self.source_fetcher=source_fetcher
        self.stop_event=stop_event;self.deadline=deadline if deadline is not None else time.monotonic()+LIMITS['max_seconds']
        self.programs={};self.sources={};self.algorithm={};self.gaps=[];self.searched_units=set()
        self._research_pool={};self._research_scans=[];self._research_pool_version=0
        self.profile_proposal=None;self.candidate_metrics=None
        self._knowledge_store=knowledge_store;self._hybrid_retriever_instance=retriever
        self.knowledge_errors=[];self._knowledge_failures={};self.saved_sources=set();self.cache_hits=set()
        self._knowledge_states={};self._filtered_sources=set()
        self._knowledge_segments={};self._knowledge_invalidated=set();self._trusted_knowledge_ids=set()
        self.loaded_skills=();self.executed_tools=set();self._algorithm_signature=None
        self._memo={}
        self.usage={'searches':0,'documents':0,'tool_calls':0,'model_calls':0,'rounds':0,'total_tokens':0,
                    'algorithm_calls':0,'embedding_calls':0,'successful_embedding_calls':0,
                    'embedding_input_tokens':0,'embedding_usage_known':True,
                    'discoveries':0,'navigation_fetches':0}
        self.bootstrap=admission.catalog_bootstrap()
        # Evidence-backed extra domains and short names are merged in one
        # place so search, filtering and reading all agree on what counts as
        # this unit's official site.
        self.units={row['id']:unit_bindings.enrich({**row,'id':row['id']})
                    for row in self.bootstrap.get('units',[]) if isinstance(row,dict) and isinstance(row.get('id'),str)}
        self.revision=self.bootstrap.get('revision')
        self.field_candidates=[];self.field_applications={'applied':[],'paused':[]}
        research_candidate_tools.initialize(self)
        research_official_tools.initialize(self,navigation_fetcher,saved_declarations)
        research_admission_tools.initialize(self)
        self.inheritance=self._inherit(task.get('previous_report'))
        research_candidate_tools.seed_source(self)

    def _inherit(self,previous):
        """Revalidate the previous turn's candidates and sources before reuse."""
        info={'turn_id':self.task.get('turn_id',0),'previous_turn_id':self.task.get('previous_turn_id'),
              'candidate_ids':[],'stale_candidate_ids':[],'source_ids':[],'stale_source_ids':[],
              'profile_changed':list(self.task.get('turn_changes') or []),
              'scope_changed':self.task.get('unit_scope_changed') is True,'reused':False}
        if not isinstance(previous,dict):return info
        evidence=previous.get('evidence') if isinstance(previous.get('evidence'),list) else []
        selected=set(self.task['unit_ids'])
        inferred=self.infer_unit(self.task.get('query','')) if not selected else ''
        if inferred:selected.add(inferred)
        for row in evidence[:60]:
            if not isinstance(row,dict):continue
            identifier,url=row.get('id'),row.get('url')
            if not isinstance(identifier,str) or not re.fullmatch(r's_[0-9a-f]{20}',identifier):continue
            if row.get('status') not in {'official_original_read','published_snapshot'}:continue
            if row.get('knowledge_filtered') is True or row.get('usable') is False:continue
            unit=(research_official_tools.current_unit(self,row['unit_id'])
                  if row.get('unit_id') in self.units else None)
            if (unit is None or row.get('year')!=self.task['reference_year']
                    or selected and row.get('unit_id') not in selected
                    or not official_source_url(str(url or ''),unit)):
                info['stale_source_ids'].append(identifier);continue
            knowledge_id=row.get('knowledge_id')
            if knowledge_id:
                try:current=self._knowledge().get(knowledge_id)
                except Exception:current=None
                if (not _reusable_knowledge(current) or current.get('id')!=knowledge_id
                        or any(current.get(key)!=row.get(key) for key in ('unit_id','year','sha256'))):
                    info['stale_source_ids'].append(identifier);continue
                row={**row,**{key:deepcopy(current[key]) for key in (*_SOURCE_METADATA,'excerpt')
                             if key in current and key!='id'},
                     'knowledge_quality':deepcopy(current['quality']),'knowledge_current':True}
                self._trusted_knowledge_ids.add(knowledge_id)
            if identifier in self.sources:continue
            self.sources[identifier]=_public_source({**deepcopy(row),'inherited':True,
                                                     'inherited_from_turn':info['previous_turn_id']})
            info['source_ids'].append(identifier)
        candidates=previous.get('candidates') if isinstance(previous.get('candidates'),list) else []
        for row in candidates[:24]:
            if not isinstance(row,dict):continue
            identifier=row.get('id')
            if not isinstance(identifier,str) or not identifier or identifier in self.programs:continue
            if identifier.startswith('tp_'):continue
            try:bundle=self.admission.catalog_program(identifier,revision=self.revision)
            except Exception:
                info['stale_candidate_ids'].append(identifier);continue
            program=bundle.get('program') if isinstance(bundle,dict) and isinstance(bundle.get('program'),dict) else bundle
            stale=(not isinstance(program,dict) or program.get('id')!=identifier
                   or program.get('year')!=self.task['reference_year']
                   or program.get('unit_id') not in self.units
                   or selected and program.get('unit_id') not in selected)
            if stale:
                info['stale_candidate_ids'].append(identifier);continue
            self.programs[identifier]={**deepcopy(program),'inherited_from_turn':info['previous_turn_id']}
            info['candidate_ids'].append(identifier)
        self._remember_research_pool(list(self.programs.values()))
        research_official_tools.inherit(self,previous)
        research_candidate_tools.inherit(self,previous,info)
        research_admission_tools.inherit(self,previous,info)
        info['reused']=bool(self.programs or self.sources)
        return info

    def _apply_fields(self):
        research_admission_tools.restore(self)
        research_candidate_tools.refresh(self)
        previous=deepcopy(self.programs)
        result=research_fields.field_candidates(self.programs,self.sources)
        pool=research_fields.field_candidates(self._research_pool,self.sources)
        if pool.get('changed'):self._research_pool_version+=1
        if previous!=self.programs or pool.get('changed'):
            self.algorithm.clear();self._algorithm_signature=None
        self.field_candidates=result['candidates'];self.field_applications=result
        research_admission_tools.refresh(self)
        for row in result.get('paused',[]):
            message='部分来源的收费口径互相冲突，相关候选已暂停推荐；请回到原文核对。'
            if message not in self.gaps:self.gaps.append(message)
        return result

    def discover_entry(self,args):
        return research_official_tools.discover(self,args)

    def propose_admission_facts(self,args):
        return research_admission_tools.propose(self,args)

    def _unit(self,identifier):
        if not isinstance(identifier,str) or identifier not in self.units:
            raise ResearchError('unknown_unit','请先从全国单位索引确认学校身份。')
        if self.task['unit_ids'] and identifier not in self.task['unit_ids']:
            raise ResearchError('outside_research_scope','该学校不在本轮选定研究范围内。')
        return research_official_tools.current_unit(self,identifier)

    def run(self,name,args):
        self._check_control()
        key=self._memo_key(name,args)
        if key is not None and key in self._memo:
            # Avoid another paid request, but recheck mutable local trust first.
            result=self._current_memo(name,args,self._memo[key]);result['repeated_call']=True
            notice='本轮已执行过完全相同的调用，这里直接复用当时的结果，未重复消耗检索或原文读取预算。'
            result['notice']=(result['notice']+' '+notice) if isinstance(result.get('notice'),str) else notice
            return result
        if self.usage['tool_calls']>=LIMITS['max_tool_calls']:
            raise ResearchError('research_budget_exceeded','已达到本轮工具调用上限。',409)
        self.usage['tool_calls']+=1
        handlers={'find_units':self.find_units,'find_candidates':self.find_candidates,
                  'search_official_sources':self.search_sources,'read_official_source':self.read_source,
                  'discover_official_links':self.discover_links,
                  'discover_official_entry':self.discover_entry,'propose_admission_facts':self.propose_admission_facts,
                  'compare_candidates':self.compare,'analyze_decisions':self.analyze_decisions,
                  'plan_subject_routes':self.routes,
                  'suggest_adjustments':self.adjustments,'read_knowledge':self.knowledge,'propose_profile':self.propose_profile,
                  'search_knowledge':self.search_knowledge,'search_knowledge_segments':self.search_segments,
                  'read_knowledge_segment':self.read_segment,'propose_source_candidates':self.propose_source_candidates}
        if name not in handlers:raise ResearchError('unknown_tool','该操作不在允许的研究工具中。')
        result=handlers[name](args)
        self._check_control()
        if not isinstance(result,dict) or not result.get('error'):
            self.executed_tools.add(name)
            if key is not None:self._memo[key]=deepcopy(result)
        return result

    def _current_memo(self,name,args,cached):
        self.refresh_knowledge();self._apply_fields()
        if args.get('unit_id'):self._unit(args['unit_id'])
        if name=='read_official_source':
            current=self.sources.get(args.get('source_id'))
            changed=bool(cached.get('knowledge_id')) and (current is None or any(
                current.get(key)!=cached.get(key) for key in ('knowledge_id','unit_id','year','sha256'))
                or (current.get('extraction') or {}).get('generation_id')!=
                   (cached.get('extraction') or {}).get('generation_id'))
            if current is None or changed or self._source_unavailable(current):
                raise ResearchError('knowledge_not_current','该来源状态或版本已变化，不能复用旧原文。',409)
            return deepcopy(current)
        def refresh(value):
            if isinstance(value,list):return [refresh(item) for item in value]
            if not isinstance(value,dict):return deepcopy(value)
            if value.get('knowledge_id'):
                current=deepcopy(self.sources.get(value.get('id')) or value)
                if value.get('id') not in self.sources or self._source_unavailable(current):
                    current={**current,'excerpt':'','text':'','pages':[],'segments':[],'links':[],
                             'usable':False,'knowledge_current':False,'notice':research_knowledge.CURRENT_NOTICE}
                elif (value.get('status')==current.get('status')=='search_lead'
                      and (value.get('knowledge_quality') or {}).get('state')==
                          (current.get('knowledge_quality') or {}).get('state')=='pending'):
                    return deepcopy(value)
                return current
            return {key:refresh(item) for key,item in value.items()}
        return refresh(cached)

    def _source_unavailable(self,source):
        quality=source.get('knowledge_quality') or {}
        return bool(source.get('knowledge_id')) and (
            source.get('validity','active')!='active' or source.get('snapshot_status')=='missing_or_changed'
            or quality.get('state')=='excluded'
            or source.get('knowledge_current') is False and (
                source.get('knowledge_id') in self._trusted_knowledge_ids
                or source.get('notice')==research_knowledge.CURRENT_NOTICE))

    REPEATABLE_TOOLS=frozenset({'search_official_sources','read_official_source'})

    @classmethod
    def _memo_key(cls,name,args):
        if name not in cls.REPEATABLE_TOOLS or not isinstance(args,dict):return None
        try:return name+'\0'+json.dumps(args,sort_keys=True,ensure_ascii=False,allow_nan=False)
        except (TypeError,ValueError):return None

    def _check_control(self):
        if time.monotonic()>=self.deadline:raise ResearchError('research_budget_exceeded','已达到本轮研究时间上限。',409)
        if self.stop_event is not None and self.stop_event.is_set():raise ResearchError('cancelled','研究已取消。',409)

    def infer_unit(self,query):
        """Return the one catalog unit named in the query, or '' when unclear.

        A longer name only shadows names inside the same mention, such as
        北京大学 inside 北京大学医学部. Separate school mentions remain ambiguous.
        """
        value=unit_bindings.normalize_name(query)
        if not value or re.search(r'除了|除去',value):return ''
        matches=set()
        for unit in self.units.values():
            names=[unit_bindings.normalize_name(unit.get('name'))]
            names.extend(unit_bindings.normalize_name(alias) for alias in unit.get('aliases') or ())
            for name in names:
                if name:
                    matches.update((unit['id'],match.start(),match.end())
                                   for match in re.finditer(re.escape(name),value))
        mentions=[row for row in matches if not any(
            left<=row[1] and row[2]<=right and (left<row[1] or row[2]<right)
            for _,left,right in matches)]
        rejected={identifier for identifier,start,end in mentions if self._negated_unit_mention(value,start,end)}
        winners={identifier for identifier,start,end in mentions if identifier not in rejected}
        return next(iter(winners)) if len(winners)==1 else ''

    @staticmethod
    def _negated_unit_mention(value,start,end):
        # Only explicit local intent markers affect this hint; subject negatives
        # do not cross a clause or a later positive school-intent marker.
        prefix=re.split(r'[，,。；;！？!?\n]',value[:start])[-1]
        cues=re.findall(r'不(?:再|太)?(?:想|打算|准备|愿意)?(?:考虑|选择|报考|就读|喜欢|去|读|考)'
                        r'|不要(?:考虑|选择|报考|去|读|考)?|排除(?:掉)?|避开|不是|不选|不包括|不包含'
                        r'|(?:想|打算|准备|愿意)?(?:考虑|选择|报考|就读|去|读|考)',prefix)
        before=bool(cues) and cues[-1].startswith(('不','排除','避开'))
        after=bool(re.match(r'(?:我|也|就|暂时)?(?:不考虑|不选|不去|不读|不报考|不要)',value[end:]))
        return before or after

    def find_units(self,args):
        _object(args,('query','province'));raw=text(args.get('query',''),120,True)
        query=raw.casefold()
        province=text(args.get('province',''),40,True)
        # Short names such as 国科大 / 社科大 are resolved by a reviewed alias
        # table, not by asking the model to guess. A name that cannot be
        # resolved returns an explicit reason instead of an empty list.
        resolution=unit_bindings.resolve_query(raw) if raw else None
        alias=resolution.get('alias') if resolution else None
        canonical=resolution.get('canonical') if resolution else None
        matched=[]
        for unit in self.units.values():
            name=unit['name'].casefold()
            code=str(unit.get('unit_code') or '').casefold()
            aliases=' '.join(unit.get('aliases') or ()).casefold()
            by=[]
            if raw:
                if query and query in name:by.append('name')
                if query and query in (name+' '+code) and 'name' not in by:by.append('unit_code')
                if alias and (alias.casefold() in aliases or (canonical and canonical.casefold() in name)):by.append('alias')
                if not by:continue
            if province and unit.get('province')!=province:continue
            row={key:unit.get(key) for key in ('id','name','province','official_url','unit_code','program_count','annual_status')}
            row['binding_status']='bound' if unit.get('official_url') else 'missing'
            domains=[item['url'] for item in unit.get('official_urls') or [] if isinstance(item,dict) and item.get('url')]
            if domains:row['official_domains']=list(dict.fromkeys(domains))
            if unit.get('official_binding'):row['official_binding']=unit['official_binding']
            row['matched_by']=by or ['scope']
            if unit.get('aliases'):row['aliases']=list(unit['aliases'])
            matched.append((row,unit))
        if canonical:
            matched.sort(key=lambda item:(0 if item[1]['name']==canonical else 1))
        missing=sum(row['binding_status']=='missing' for row,_ in matched)
        result={'units':[row for row,_ in matched[:20]],'total':len(matched),'truncated':len(matched)>20,
                'scope':'national_unit_index_not_complete_program_catalog','binding_missing_count':missing}
        if alias:
            result['alias_resolution']={'alias':alias,'canonical':canonical}
        # A single unambiguous unit can hand back its verified official entry as
        # a readable source: knowing the URL must not require a paid search hit
        # first. Broad queries only list rows so one school's entry is never
        # silently attached to another.
        for row,unit in matched[:20]:
            if row['binding_status']=='bound' and (len(matched)==1 or (canonical and unit['name']==canonical)):
                identifier=self._official_entry(unit)
                if identifier:row['official_source_id']=identifier
        if not matched:
            result['reason']='alias_resolved_without_index_match' if alias else 'index_no_match'
            result['notice']=(f'简称“{alias}”已解析为{canonical}，但全国单位索引中没有匹配条目；索引覆盖不完整不代表学校不存在。'
                              if alias else '全国单位索引中没有与查询匹配的单位；索引覆盖不完整不代表学校不存在。')
        elif missing:
            result['notice']=f'其中{missing}个单位尚未登记可信官网域名，检索结果无法按官网归属过滤；这不代表官网没有资料。'
        return result

    def _official_entry(self,unit):
        """Register the unit's verified official entry as a controlled source."""
        url=unit.get('official_url')
        if not isinstance(url,str) or not url:
            return None
        return self._source({'url':url,'title':f"{unit['name']}官网入口",'unit_id':unit['id'],
                             'unit_name':unit['name'],'year':None,'status':'official_entry',
                             'source_type':'official_entry','checked_at':None,'excerpt':'',
                             'notice':'单位索引登记的官网入口，不是招生原文；读取后仍需核对年度与字段。'})

    def find_candidates(self,args):
        _object(args,('query','unit_id'));requested=text(args.get('query'),120)
        units,scope_mode=self._candidate_scope(args.get('unit_id'))
        query=self._candidate_query(requested)
        scan=self._scan_candidates(query,units)
        effective,relaxed=query,None
        if not scan['programs'] and scan['complete'] and scan['interrupted'] is None:
            # A natural-language query such as "南开大学 计算机 408 考研" is
            # usually treated as one literal string by the catalog index.
            # Retry single meaningful terms rather than returning an empty
            # candidate set; the response records that the query was relaxed.
            for term in self._relaxed_terms(query,tuple(unit.get('name','') for unit in self.units.values()))[:4]:
                trial=self._scan_candidates(self._candidate_query(term),units)
                if trial['interrupted'] is not None:
                    scan=trial
                    break
                if trial['programs']:
                    scan,effective,relaxed=trial,term,{'requested':query,'effective':term}
                    break
        for message in scan['warnings']:
            if message not in self.gaps:self.gaps.append(message)
        screened=match_programs(scan['programs'],self.task['profile'])
        if any(exam_unresolved_reason(entry['program']) for entry in screened['excluded']):
            message='存在科目标识待核候选，已排除其换科与排序计算。'
            if message not in self.gaps:self.gaps.append(message)
        selected,query_ids=self._select_and_store(scan['programs'])
        scope_no_match=units==[]
        if not scan['complete'] and not any('部分' in gap or '尚未' in gap for gap in self.gaps):
            self.gaps.append('本轮尚未遍历该查询的全部方向；已按实际扫描范围先硬筛再精选，结果标记为部分。')
        self.candidate_metrics=self._candidate_metrics(scan,screened,selected,query_ids,scope_mode,units)
        notice=self._candidate_notice(scan,units,scope_no_match,self.candidate_metrics['carried_over_count'])
        if relaxed:
            notice=f"检索词“{requested}”没有命中，已改用“{effective}”放宽检索；仍只使用已发布目录与条件筛选。"+notice
        # Keep the relaxation visible in the persisted report, not only in the
        # tool result the model sees; the UI must not silently change the query.
        self.candidate_metrics={**self.candidate_metrics,'requested_query':requested,
                                'effective_query':effective,'query_relaxed':bool(relaxed)}
        self._record_research_scan(effective,units,scan)
        result={**self.candidate_metrics,'candidates':[self._candidate(row) for row in selected],
                'status':'complete' if scan['complete'] else 'partial',
                'verification_mode':'published_snapshot','national_complete':False,
                'requested_query':requested,'effective_query':effective,'query_relaxed':bool(relaxed),
                'notice':notice}
        if scan['interrupted'] is not None and scan['interrupted'].code=='cancelled':
            raise scan['interrupted']
        return result

    @staticmethod
    def _relaxed_terms(query,names=()):
        stop={'考研','硕士','研究生','招生','专业','目录','考试','科目','备考','真题','参考书','院校','学校'}
        school={name for name in names if isinstance(name,str) and len(name)>=4}
        terms,deferred=[],[]
        def add(term):
            term=term.strip()
            if len(term)<2 or term in stop or term in terms or term in deferred:return
            if term in school:deferred.append(term);return
            if any(name in term for name in school):return
            terms.append(term)
        def add_parts(value):
            for part in re.findall(r'[一-鿿]+|[A-Za-z]+|\d{3,4}',value):add(part)
        # Subject codes are the most specific fallback; generic school names
        # are tried last so a broad school query does not hide the major.
        for code in re.findall(r'(?<!\d)\d{3}(?!\d)',query):add(code)
        for name in sorted(school,key=len,reverse=True):
            if name not in query:continue
            add(name)
            rest=query.replace(name,'',1)
            for word in stop:rest=rest.replace(word,' ')
            add_parts(rest)
            break
        core=query
        for word in stop:core=core.replace(word,' ')
        for term in re.split(r'[\s,，、;；/]+',core):add_parts(term)
        return [*terms,*deferred]

    def _candidate_scope(self,uid):
        if uid:
            self._unit(uid);return [uid],'explicit_units'
        if self.task['unit_ids']:
            return list(self.task['unit_ids'][:8]),'explicit_units'
        provinces=self.task['profile'].get('provinces') or []
        if provinces:
            return [row['id'] for row in self.units.values() if row.get('province') in provinces],'province_scope'
        return None,'national_query'

    def _candidate_query(self,query):
        value=query
        for exclusion in self.task['profile'].get('excluded_subjects',[]):
            code=str(exclusion.get('code','')).strip()
            if not code:continue
            value=re.sub(r'(?:不考|不要|不选|排除|避开)\s*'+re.escape(code),' ',value,flags=re.I)
            value=re.sub(r'(?<![0-9A-Za-z])'+re.escape(code)+r'(?![0-9A-Za-z])',' ',value,flags=re.I)
        return ' '.join(value.split())

    def _scan_page(self,query,unit_id,offset):
        self._check_control()
        try:
            page=self.admission.catalog_programs(unit_id=unit_id,year=self.task['reference_year'],q=query,
                                                 offset=offset,limit=200,revision=self.revision)
        except Exception as error:
            if getattr(error,'code','')=='revision_changed':return 'switched',None
            raise
        if page.get('revision')!=self.revision:return 'mixed',None
        return 'ok',page

    def _scan_scope(self,query,unit_id):
        rows=[];offset=0;total=None;complete=True;reason=None
        while total is None or offset<total:
            try:state,page=self._scan_page(query,unit_id,offset)
            except ResearchError as error:return rows,total,offset,False,'stopped',error
            if state!='ok':return rows,total,offset,False,state,None
            batch=page.get('programs') or []
            total=page.get('total',len(batch))
            rows.extend(deepcopy(batch));offset+=len(batch)
            if not batch:
                complete=offset>=total
                if not complete:reason='empty'
                break
        return rows,total,offset,complete,reason,None

    def _scan_candidates(self,query,units):
        result={'programs':[],'total':0,'scanned':0,'complete':True,'warnings':[],
                'interrupted':None,'unqueried_unit_ids':[],'incomplete_unit_ids':[],
                'total_complete':units is not None}
        if units is None:
            rows,total,scanned,complete,reason,interrupted=self._scan_scope(query,None)
            result.update(programs=rows,total=total or 0,scanned=scanned,complete=complete,
                          interrupted=interrupted,total_complete=total is not None)
            self._scan_warning(result,reason,'全局')
            return result
        for index,unit in enumerate(units):
            rows,total,scanned,complete,reason,interrupted=self._scan_scope(query,unit)
            result['programs'].extend(rows);result['scanned']+=scanned
            if total is not None:result['total']+=total
            result['complete']=result['complete'] and complete
            if interrupted is not None:result['interrupted']=interrupted
            if not complete:
                result['incomplete_unit_ids'].append(unit)
                result['unqueried_unit_ids']=list(units[index+1:])
                if result['unqueried_unit_ids'] or total is None:result['total_complete']=False
                self._scan_warning(result,reason,'学校')
                break
        return result

    @staticmethod
    def _scan_warning(result,reason,scope):
        messages={'stopped':'本轮在分页过程中达到时间或停止条件；已保留已扫描结果并标记为部分。',
                  'switched':'资料版本在分页过程中发生切换，已停止后续查询并保留原版本已扫描结果。',
                  'mixed':'目录版本在分页过程中发生变化，已停止混合版本结果并标记为部分。',
                  'empty':f'已发布目录提前返回空页；{scope}总数尚不完整。'}
        if reason in messages and messages[reason] not in result['warnings']:
            result['warnings'].append(messages[reason])

    def _select_candidates(self,entries):
        ordered=sorted(entries,key=lambda entry:(0 if entry['status']=='matched' else 1,
                                                 *rank_order_key(entry,self.task['profile']),
                                                 str(entry['program'].get('unit_id','')),
                                                 str(entry['program'].get('id',''))))
        grouped={}
        for entry in ordered:
            identifier=str(entry['program'].get('unit_id') or entry['program'].get('id'))
            grouped.setdefault(identifier,[]).append(entry)
        selected=[]
        while len(selected)<LIMITS['max_candidates'] and any(grouped.values()):
            for identifier in list(grouped):
                if grouped[identifier]:
                    selected.append(grouped[identifier].pop(0))
                    if len(selected)>=LIMITS['max_candidates']:break
        return [entry['program'] for entry in selected]

    def _select_and_store(self,scanned):
        self._remember_research_pool(scanned)
        previous=deepcopy(self.programs)
        query_ids={row['id'] for row in scanned}
        union={row['id']:deepcopy(row) for row in scanned}
        for row in self.programs.values():union.setdefault(row['id'],deepcopy(row))
        selection=match_programs(list(union.values()),self.task['profile'])
        selected=self._select_candidates(selection['matches']+selection['needs_confirmation'])
        self.programs={row['id']:deepcopy(row) for row in selected}
        if previous!=self.programs:
            self.algorithm.clear();self._algorithm_signature=None
        self._save_candidate_sources(selected)
        return selected,query_ids

    def _remember_research_pool(self,rows):
        allowed=set(self.task['unit_ids'])
        current={row['id']:deepcopy(row) for row in rows if isinstance(row,dict)
                 and isinstance(row.get('id'),str) and row.get('year')==self.task['reference_year']
                 and row.get('unit_id') in self.units and (not allowed or row['unit_id'] in allowed)}
        updated={**self._research_pool,**current}
        if updated!=self._research_pool:
            self._research_pool=updated;self._research_pool_version+=1
            self.algorithm.clear();self._algorithm_signature=None

    def _record_research_scan(self,query,units,scan):
        value={'query':query,'unit_ids':None if units is None else list(units),
               'scan_complete':bool(scan['complete'] and scan['total_complete']),
               'revision':self.revision}
        self._research_scans=[row for row in self._research_scans
                              if (row['query'],row['unit_ids'])!=(query,value['unit_ids'])]+[value]

    def _counterfactual_rows(self,args):
        if 'program_ids' in args:return self._selected(args)
        self._apply_fields()
        rows=list(self._research_pool.values()) if self._research_pool else self._selected(args)
        return [row for row in rows if not exam_unresolved_reason(row)]

    def _expand_research_pool(self,changes):
        if not changes.get('provinces') or not self._research_scans:return
        queries=list(dict.fromkeys(row['query'] for row in self._research_scans))
        for provinces in changes['provinces']:
            if provinces==self.task['profile']['provinces']:continue
            units=(list(self.task['unit_ids']) if self.task['unit_ids'] else
                   [unit['id'] for unit in self.units.values() if unit.get('province') in provinces]
                   if provinces else None)
            for query in queries:
                complete=[row for row in self._research_scans if row['query']==query and row['scan_complete']]
                if any(row['unit_ids'] is None for row in complete):continue
                covered={identifier for row in complete for identifier in row['unit_ids']}
                pending=[identifier for identifier in units if identifier not in covered] if units is not None else None
                if pending==[]:continue
                scan=self._scan_candidates(query,pending)
                self._remember_research_pool(scan['programs']);self._record_research_scan(query,pending,scan)
                for warning in scan['warnings']:
                    if warning not in self.gaps:self.gaps.append(warning)
                if scan['interrupted'] is not None:
                    if scan['interrupted'].code=='cancelled':raise scan['interrupted']
                    return

    def _counterfactual_scope(self,args,rows):
        explicit='program_ids' in args
        complete=explicit or bool(self._research_scans) and all(row['scan_complete'] for row in self._research_scans)
        return {'pool_count':len(rows),'scan_complete':complete,'revision':self.revision,
                'queries':deepcopy(self._research_scans),'national_complete':False,
                'mode':'selected_candidates' if explicit else 'research_query_pool' if any(
                    row.get('candidate_origin')=='official_original' for row in rows) else 'published_query_pool',
                'research_original_count':sum(row.get('candidate_origin')=='official_original' for row in rows),
                'published_count':sum(row.get('candidate_origin')!='official_original' for row in rows),
                'notice':'只比较明确指定的候选。' if explicit else
                         '按本轮已取得的目录与核验原文方向试算，不代表全国全部选择。' if complete else
                         '仅对本轮已取得的部分目录与原文方向试算，未取得资料的方向不计为不存在。'}

    def _save_candidate_sources(self,selected):
        for row in selected:
            if row.get('candidate_origin')=='official_original':continue
            for ref in row.get('evidence',[]) if isinstance(row.get('evidence'),list) else [row.get('evidence')]:
                if not isinstance(ref,dict) or not isinstance(ref.get('url'),str):continue
                sid=self._source({'url':ref['url'],'title':f"{row.get('unit_name','')} {row.get('name','')}目录依据",
                              'unit_id':row.get('unit_id'),'year':row.get('year'),'status':'published_snapshot',
                              'excerpt':str(ref.get('quote',''))[:1200],'source_type':'published_catalog'})
                if sid:self._save_source(sid)

    def _candidate_metrics(self,scan,screened,selected,query_ids,scope_mode,units):
        scope_no_match=units==[]
        query_candidate_ids=[row['id'] for row in selected if row['id'] in query_ids]
        cumulative_ids=[row['id'] for row in selected]
        carried_over_count=len(cumulative_ids)-len(query_candidate_ids)
        return {**{
            'total_in_query':scan['total'],'total_complete':scan['total_complete'],'scanned_count':scan['scanned'],
            'matched_count':len(screened['matches']),'pending_count':len(screened['needs_confirmation']),
            'hard_excluded_count':len(screened['excluded']),
            'selected_count':len(query_candidate_ids),'cumulative_selected_count':len(cumulative_ids),
            'carried_over_count':carried_over_count,'query_candidate_ids':query_candidate_ids,
            'candidate_ids':cumulative_ids,
            'candidate_unit_ids':list(dict.fromkeys(row.get('unit_id') for row in selected)),
            'scan_complete':scan['complete'],'scope_mode':scope_mode,'scope_no_match':scope_no_match,
            'queried_unit_ids':list(units or []),
            'unqueried_unit_ids':scan['unqueried_unit_ids'],'incomplete_unit_ids':scan['incomplete_unit_ids'],
            'candidate_limit':LIMITS['max_candidates'],'revision':self.revision},
            **({'provinces':list(self.task['profile'].get('provinces') or [])} if scope_no_match else {})}

    def _candidate_notice(self,scan,units,scope_no_match,carried_over_count):
        if scope_no_match:
            return f"当前意向地区{'、'.join(self.task['profile'].get('provinces') or [])}没有已发布学校；未回退到全国范围。"
        if not scan['complete']:
            return ('本轮只遍历了查询范围内的部分方向，返回候选还包含此前保留的累计精选；本次查询结果与累计结果分开计数。'
                    if carried_over_count else '本轮只遍历了查询范围内的部分方向；候选按已扫描的完整行先硬筛再精选，未把部分结果当作全部。')
        if carried_over_count:
            return (f"本次查询只覆盖{('、'.join(units) if units else '当前范围')}；"
                    f"返回候选包含此前保留的{carried_over_count}个累计精选方向，比较时请按累计理解。")
        return '本轮在已发布核验快照的查询范围内先硬筛、全范围粗排，再精选候选；未重新核验全国原文。'

    @staticmethod
    def _candidate(row):
        keys=('id','unit_id','unit_name','name','code','department','direction','study_mode','degree_type','year',
              'subjects','tuition','duration_years','eligibility_evidence_complete','requires_review','notes',
              'field_conflicts','candidate_origin','candidate_ref','validation','evidence_refs',
              'conditions','duration_evidence')
        result={key:deepcopy(row.get(key)) for key in keys if key in row}
        return {**result,'verification_mode':row.get('verification_mode','published_snapshot'),
                'exam_unresolved_reason':exam_unresolved_reason(row)}

    def _source(self,value):
        url=value.get('url','');parsed=urlsplit(url)
        if parsed.scheme not in {'https','http'} or not parsed.hostname or parsed.username or parsed.password:return None
        version=value.get('knowledge_id') or value.get('sha256') or ''
        source_id='s_'+hashlib.sha256((url+'|'+str(value.get('year'))+'|'+str(value.get('unit_id'))+'|'+version).encode()).hexdigest()[:20]
        current=self.sources.get(source_id,{})
        if current.get('status')=='official_original_read' and not current.get('knowledge_filtered'):return source_id
        if current.get('knowledge_filtered') and current.get('status')==value.get('status'):return source_id
        unit=self.units.get(value.get('unit_id'))
        if unit:value={**value,'unit_name':unit['name']}
        self.sources[source_id]={'id':source_id,**_public_source(value)}
        return source_id

    def _hybrid_retriever(self):
        return self._hybrid_retriever_instance

    def _record_retrieval_usage(self, usage, known):
        """Add only the finite accounting facts the vector layer reported."""
        value=usage if isinstance(usage,dict) else {}
        for key in ('embedding_calls','successful_embedding_calls'):
            count=value.get(key,0)
            if type(count) is int and count>=0:self.usage[key]+=count
        tokens=value.get('input_tokens')
        if type(tokens) is int and tokens>=0:self.usage['embedding_input_tokens']+=tokens
        if known is not True:self.usage['embedding_usage_known']=False

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
                                     **{key:deepcopy(record[key]) for key in ('original_available','validity','usable','extraction','source_group','index_state') if key in record},
                                     'knowledge_filtered':False,'knowledge_filter_reason':''}
            if _reusable_knowledge(record):self._trusted_knowledge_ids.add(identifier)
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
        return _public_source({**metadata,'id':source['id'],
            **{key:deepcopy(source[key]) for key in ('authority_id','source_role') if key in source},
            **({'links':deepcopy(source['links'])} if 'links' in source else {}),
            'requested_year':source.get('requested_year',record.get('requested_year')),
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
        units=[uid] if uid else self.task['unit_ids'] or ['']
        def collect(search_query):
            groups=[];query_total=0
            for selected in units:
                entries=[]
                for page in range(1,5):
                    batch=self._knowledge().search(query=search_query,year=year,unit_id=selected,page=page,page_size=12,quality='accepted')
                    total=batch.get('total',0)
                    entries.extend(entry for entry in batch.get('entries',[]) if _reusable_knowledge(entry) and
                                   entry.get('year')==year and (not selected or entry.get('unit_id')==selected))
                    if len(entries)>=8 or page*12>=total:break
                groups.append(entries);query_total+=total
            return [entry for group in zip_longest(*groups) for entry in group if entry is not None],query_total
        requested,effective=query,query
        try:
            entries,query_total=collect(query)
            expanded=unit_bindings.expand_query(query)
            if not entries and expanded!=query and len(expanded)<=240:
                attempt,total=collect(expanded)
                if attempt:
                    entries,query_total,effective=attempt,total,expanded
        except Exception:
            message='本机知识库暂时无法读取；可继续使用已发布目录或主动检索。'
            self.knowledge_errors.append(message);self.gaps.append(message)
            return {'results':[],'total':0,'error':{'code':'knowledge_unavailable','message':message}}
        self.knowledge_errors=[message for message in self.knowledge_errors if message!='本机知识库暂时无法读取；可继续使用已发布目录或主动检索。']
        self.gaps=[message for message in self.gaps if message!='本机知识库暂时无法读取；可继续使用已发布目录或主动检索。']
        results=[]
        for entry in entries[:8]:
            if self.task['unit_ids'] and entry.get('unit_id') not in self.task['unit_ids']:continue
            value={key:deepcopy(entry[key]) for key in (*_SOURCE_METADATA,'excerpt') if key in entry and key!='id'}
            quality=_knowledge_quality(entry['quality'])
            value={**value,'knowledge_id':entry.get('id'),'knowledge_saved':True,'cache_hit':True,
                   'knowledge_quality':quality,'knowledge_filtered':False,
                   'cached_at':entry.get('checked_at'),'notice':'复用本机留档，读取日期保持原样；本次没有重新联网核验。'}
            sid=self._source(value)
            if sid and self.sources[sid].get('knowledge_quality',{}).get('state')=='accepted' and self.sources[sid].get('knowledge_quality',{}).get('reusable') is True:
                self._trusted_knowledge_ids.add(entry['id'])
                self.cache_hits.add(entry.get('id') or sid);results.append(deepcopy(self.sources[sid]))
        response={'results':results,'total':len(results),'query_total':query_total,
                  'notice':'仅复用通过知识准入的资料；待核摘要和过滤来源不作为跨轮知识缓存。缓存也不等于实时验证或新增招生事实。'}
        if effective!=requested:
            response.update({'requested_query':requested,'effective_query':effective,'alias_expanded':True})
            response['notice']+=' 学校简称已按已复核别名表展开为规范校名；这只是检索词变化，不是新的资料或事实。'
        if not results:
            try:
                years=sorted({value for selected in units
                              for value in self._knowledge().stored_years(unit_id=selected,quality='accepted')},
                             reverse=True)[:10]
            except Exception:
                years=[]
            if years:
                response['stored_years']=years
                response['notice']+=' 知识库保存资料的实际年度为 '+'、'.join(str(value) for value in years)+' 年；若问题涉及历史年度，可按实际年度重新检索，不能把历史年度当作目标年度。'
        return response

    def refresh_knowledge(self):
        research_admission_tools.restore(self)
        result=research_knowledge.refresh_sources(self,_reusable_knowledge)
        research_official_tools.refresh(self)
        research_candidate_tools.refresh(self)
        research_admission_tools.refresh(self)
        return result

    def search_segments(self,args):
        _object(args,('query','entry_id','unit_id','year'));query=text(args.get('query'),240)
        if _PII.search(query):raise ResearchError('private_search_query','知识库查询只接受公开学校、专业和年度资料。')
        uid=text(args.get('unit_id',''),100,True)
        if uid:self._unit(uid)
        year=args.get('year',self.task['reference_year']);entry_id=args.get('entry_id')
        if type(year) is not int or not 2020<=year<=self.task['target_year']:raise ResearchError('invalid_year','知识库检索年份不正确。')
        if entry_id is not None and (not isinstance(entry_id,str) or not re.fullmatch(r'k_[0-9a-f]{24}',entry_id)):
            raise ResearchError('invalid_knowledge_entry','请选择实际发现的知识条目。')
        result=research_knowledge.search_segments(self,query,uid,year,entry_id,_reusable_knowledge)
        expanded=unit_bindings.expand_query(query)
        if not result.get('segments') and expanded!=query and len(expanded)<=240:
            retry=research_knowledge.search_segments(self,expanded,uid,year,entry_id,_reusable_knowledge)
            combined=result.get('query_total',0)+retry.get('query_total',0)
            if retry.get('segments'):
                retry['query_total']=combined
                retry['requested_query']=query;retry['effective_query']=expanded;retry['alias_expanded']=True
                retry['notice']=str(retry.get('notice') or '')+' 学校简称已展开为知识库中的规范校名；这只是检索词变化，不是新的资料或事实。'
                return retry
            result['query_total']=combined
            result['alias_attempted']=True
        return result

    def read_segment(self,args):
        _object(args,('segment_id','before','after','max_chars'));identifier=text(args.get('segment_id'),100)
        before=args.get('before',0);after=args.get('after',0);limit=args.get('max_chars',6000)
        if any(type(value) is not int or not 0<=value<=2 for value in (before,after)) or type(limit) is not int or not 64<=limit<=8000:
            raise ResearchError('invalid_segment_window','补读范围必须为前后0至2段、64至8000字符。')
        result=research_knowledge.read_segment(self,identifier,before,after,limit,_reusable_knowledge)
        research_candidate_tools.register_read(self,result,before,after,limit)
        return result

    def propose_source_candidates(self,args):
        return research_candidate_tools.propose(self,args)

    def confirm_segment_delivery(self,original_result,delivered_result):
        research_candidate_tools.confirm_delivery(self,original_result,delivered_result)

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
        if self.network is None:
            from ai_transport import AITransport
            self.network=AITransport()
        from provider_catalog import TransportError
        from yuanbao_auth import checked_response
        errors={'provider_rate_limited':'search_rate_limited','provider_auth_failed':'search_auth_failed',
                'provider_permission_denied':'search_auth_failed','signature_expired':'search_auth_failed',
                'search_not_enabled':'search_unavailable','search_unavailable':'search_unavailable',
                'provider_request_failed':'search_unavailable'}

        def call(body):
            # Budget counts real provider requests, not tool calls: a Site
            # fallback or a wider rewrite is another paid request.
            if self.usage['searches']>=LIMITS['max_searches']:
                raise ResearchError('research_budget_exceeded','已达到本轮元宝检索上限。',409)
            self.usage['searches']+=1
            try:
                raw=self.network.request_json(config,'POST','',body,stop_event=self.stop_event)
            except TransportError as error:
                code=errors.get(error.code,'search_unavailable')
                raise ResearchError(code,error.message,429 if code=='search_rate_limited' else error.status) from None
            self._check_control()
            try:return checked_response(raw)
            except TransportError:
                raise ResearchError('search_invalid_response','元宝返回内容无法解析。',502) from None

        roots=official_roots(unit) if unit else ()
        site=roots[0] if roots else ''
        if unit:
            primary=f"{year}年 {unit['name']} {query}"
            wider=f"{unit['name']} 研究生院 {year} {query}"
            queries=[primary] if wider==primary else [primary,wider]
            def is_official(url):return bool(official_source_url(url,unit))
        else:
            queries=[f"{year}年 {query}"]
            def is_official(url):
                host=(urlsplit(url).hostname or '').lower().rstrip('.')
                return host.endswith(('.edu.cn','.ac.cn','.gov.cn','.chsi.com.cn'))
        binding_missing=bool(unit) and not roots
        kind=official_search.infer_kind(query)
        start_requests=self.usage['searches']
        per_unit_limit=LIMITS['max_searches'] if len(self.task['unit_ids'])==1 else 2
        unit_requests=self._official_unit_requests.get(unit['id'],0) if unit else 0
        result=official_search.run_ladder(call,queries=queries,is_official=is_official,
            unit_name=unit['name'] if unit else '',roots=roots,candidate_enabled=bool(unit),site=site,
            target_match=lambda row:official_search.target_matches(row,year,kind),
            collect_third=True,
            can_call=lambda:self.usage['searches']<LIMITS['max_searches']
                and unit_requests+self.usage['searches']-start_requests<per_unit_limit)
        if unit:
            self.searched_units.add(unit['id'])
            self._official_unit_requests[unit['id']]=unit_requests+self.usage['searches']-start_requests
        results=[]
        for row in result['results']:
            value={'url':row['url'],'title':row['title'] or '待核验的检索结果',
                   'unit_id':unit['id'] if unit else None,'unit_name':unit['name'] if unit else None,
                   'year':None,'requested_year':year,'status':'search_lead','source_type':'yuanbao_search',
                   'checked_at':None,'excerpt':row['summary'],
                   'source_tier':row['source_tier'],'source_status':row['source_status']}
            sid=self._source(value)
            if sid:
                self._save_source(sid,query=queries[0]);results.append(deepcopy(self.sources[sid]))
        official_results=[row for row in result['results'] if row['source_tier']=='official']
        candidates=[row for row in result['results'] if row['source_tier']=='official_domain_candidate']
        third=[row for row in result['results'] if row['source_tier']=='third_party_reference']
        outcome=('official_results' if official_results else
                 'official_domain_candidates' if candidates else
                 'extended_reference' if third else 'no_official_results')
        stage=official_search.failure_stage(official_results,candidates,third,result['rejected_count'],binding_missing)
        notice=official_search.notice(official_results,candidates,third,len(result['queries_tried']),
                                      binding_missing=binding_missing)
        global_exhausted=self.usage['searches']>=LIMITS['max_searches']
        unit_exhausted=result['budget_exhausted'] and not global_exhausted
        if global_exhausted:notice+=' 本轮检索额度已用完，后续请求已停止；仍可沿官网核验。'
        elif unit_exhausted:notice+=' 该校本次元宝检索额度已用完，其他学校仍可检索；接下来沿官网核验。'
        return {'provider':'yuanbao_search','query':queries[0],'queries_tried':result['queries_tried'],
                'request_count':result['request_count'],'request_id':result['request_id'],
                'results':results,'outcome':outcome,'failure_stage':stage,
                'binding_status':'missing' if binding_missing else 'bound' if unit else 'not_applicable',
                'binding_domains':list(roots),
                'rejected_count':result['rejected_count'],'malformed_count':result['malformed_count'],
                'site_fallback':result['site_fallback'],'budget_exhausted':global_exhausted,
                'unit_budget_exhausted':unit_exhausted,'searches_remaining':max(0,LIMITS['max_searches']-self.usage['searches']),
                'third_party_count':len(third),
                'candidate_count':result['candidate_count'],'candidate_hosts':result['candidate_hosts'],
                'target_status':result['target_status'],
                'next_action':({'tool':'discover_official_entry','args':{'unit_id':unit['id'],'year':year,'kind':kind}}
                    if unit and result['target_status']!='found' else None),
                'notice':notice}

    def read_source(self,args,*,refresh=False):
        self._check_control()
        _object(args,('source_id','unit_id'));source_id=text(args.get('source_id'),100)
        self.refresh_knowledge()
        source=self.sources.get(source_id);unit=self._unit(args.get('unit_id'))
        if source is None:raise ResearchError('unknown_source','请先通过检索、单位索引或已发布目录发现这份来源。')
        if source.get('knowledge_current') is False and source.get('knowledge_id') in self._trusted_knowledge_ids:
            raise ResearchError('knowledge_not_current','该来源当前不可可信复用，请重新检索有效原文。',409)
        if urlsplit(source['url']).hostname=='yz.chsi.com.cn':
            raise ResearchError('source_restricted','研招网访问门禁保持生效；可查看已保存依据，不发起新请求。',409)
        if source.get('unit_id') and source['unit_id']!=unit['id']:
            raise ResearchError('wrong_unit','来源登记在其他学校名下，不能在本次学校范围内读取。')
        if not official_source_url(source['url'],unit):raise ResearchError('wrong_unit','来源不属于该学校的已核准官网。')
        if source.get('status')=='official_original_read' and not refresh:return deepcopy(source)
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
        heading,year=_document_scope(doc)
        links=self._link_sources(doc.get('links'),unit)
        value={**source,'unit_id':unit['id'],'unit_name':unit['name'],'title':heading or source['title'],
               'url':doc['url'],'year':year,'checked_at':doc.get('checked_at'),
               'sha256':digest,'status':'official_original_read','source_type':'official_original',
               'excerpt':str(doc.get('text',''))[:12000],'page_count':len(doc.get('pages',[])),
               'links':links,'field_verification':'not_structured',
               'notice':'已读取并保存官方原文；其中具体数值、范围和年度仍需逐字段核验，不自动写入招生库。'}
        self.sources[source_id]=_public_source(value)
        self._save_source(source_id,document=doc)
        return deepcopy(self.sources[source_id])

    def _link_sources(self,links,unit):
        """Register official same-site links found on a already-readable page.

        Navigation wording is not evidence, but the links themselves are what
        turns a homepage into a usable entry. Only links that pass the school's
        official-domain check are registered; third-party or foreign-school
        links are not offered as readable sources.
        """
        offered=[];candidates=[]
        for item in (links or [])[:200]:
            if not isinstance(item,dict) or not isinstance(item.get('url'),str):continue
            url=item['url'].split('#',1)[0]
            try:host=urlsplit(url).hostname or ''
            except ValueError:continue
            if host=='yz.chsi.com.cn' or not official_source_url(url,unit):continue
            row={'url':url,'title':str(item.get('label') or item.get('title') or '')[:120]}
            candidates.append(row)
        for row in self._rank_links(candidates,unit,LIMITS['max_links']):
            url=row['url']
            sid=self._source({'url':url,'title':row['title'] or '官方页面入口','unit_id':unit['id'],
                              'unit_name':unit['name'],'year':None,'status':'official_link',
                              'source_type':'official_link','checked_at':None,'excerpt':''})
            if sid:row['source_id']=sid
            offered.append(row)
        return offered

    # Link text and URL hints are ranking aids only; the page itself still has
    # to be read and attributed before any fact may use it.
    LINK_MARKERS=('招生简章','招生章程','招生目录','专业目录','招生信息','硕士','研究生','复试',
                  '分数','调剂','推免','考试科目','录取','附件','名单')
    SYSTEM_MARKERS=('登录','报名系统','统一身份','财务','就业','旧版','english','en.jsp')

    def _rank_links(self,links,unit,limit):
        scored=[]
        for row in links or []:
            if not isinstance(row,dict) or not isinstance(row.get('url'),str):continue
            if not official_source_url(row['url'],unit):continue
            label=str(row.get('title') or '')
            if contains_personal_roster(label):continue
            lowered=(label+' '+row['url']).lower()
            if any(word in lowered for word in self.SYSTEM_MARKERS):continue
            score=0
            score+=3 if urlsplit(row['url']).path.lower().endswith('.pdf') else 0
            score+=2 if any(word in label for word in self.LINK_MARKERS) else 0
            score+=1 if any(part in row['url'].lower() for part in ('/info/','/zs','/sszs','/yjs','/news','/tzgg')) else 0
            scored.append((score,row))
        scored.sort(key=lambda item:(-item[0],item[1]['url']))
        return [{**row,'kind':'pdf' if urlsplit(row['url']).path.lower().endswith('.pdf') else 'page'}
                for _,row in scored[:limit]]

    def discover_links(self,args):
        self._check_control()
        self.refresh_knowledge();self._apply_fields()
        _object(args,('source_id','unit_id','depth','limit'))
        source_id=text(args.get('source_id'),100);unit=self._unit(args.get('unit_id'))
        depth=args.get('depth',1);limit=args.get('limit',10)
        if type(depth) is not int or not 1<=depth<=2:
            raise ResearchError('invalid_depth','站内发现深度只能为 1 或 2。')
        if type(limit) is not int or not 1<=limit<=LIMITS['max_links']:
            raise ResearchError('invalid_limit',f'站内发现数量必须是1至{LIMITS["max_links"]}。')
        if self.usage['discoveries']>=LIMITS['max_discoveries']:
            raise ResearchError('research_budget_exceeded','已达到本轮站内发现次数上限。',409)
        source=self.sources.get(source_id)
        if source is None:raise ResearchError('unknown_source','请先读取这份官方页面，再进行站内发现。')
        if self._source_unavailable(source):
            raise ResearchError('knowledge_not_current','该来源当前不可复用，不能继续使用旧页面入口。',409)
        if source.get('unit_id') and source['unit_id']!=unit['id']:
            raise ResearchError('wrong_unit','来源登记在其他学校名下，不能在本轮范围内继续发现。')
        if not official_source_url(source['url'],unit):
            raise ResearchError('wrong_unit','来源不属于该学校的已核准官网。')
        if source.get('status')!='official_original_read' or not isinstance(source.get('links'),list):
            if self.usage['navigation_fetches']>=LIMITS['max_navigation_fetches']:
                raise ResearchError('research_budget_exceeded','已达到本轮站内导航读取上限。',409)
            before=self.usage['documents']
            missing_links=not isinstance(source.get('links'),list)
            self.usage['navigation_fetches']+=1
            source=self.read_source({'source_id':source_id,'unit_id':unit['id']},refresh=missing_links)
            parent_requests=1 if self.usage['documents']>before else 0
        else:
            parent_requests=0
        self.usage['discoveries']+=1
        rows=self._rank_links(source.get('links'),unit,limit)
        result={'source_id':source_id,'url':source['url'],'depth':depth,'links':rows,
                'deeper_links':[],'errors':[],'request_count':parent_requests,
                'notice':'只列出同站官方入口；读取每个入口仍会消耗原文读取预算并重新核验归属。'}
        if depth==2:
            for row in rows:
                if len(result['deeper_links'])>=limit:break
                if self.usage['documents']>=LIMITS['max_documents'] or time.monotonic()>=self.deadline:break
                if row.get('kind')!='page' or not row.get('source_id'):continue
                child=self.sources.get(row['source_id'])
                missing_links=bool(child and child.get('status')=='official_original_read'
                                   and not isinstance(child.get('links'),list))
                will_fetch=missing_links or not (child and child.get('status')=='official_original_read')
                if will_fetch:
                    if self.usage['navigation_fetches']>=LIMITS['max_navigation_fetches']:break
                    self.usage['navigation_fetches']+=1
                before=self.usage['documents']
                try:
                    deeper=self.read_source({'source_id':row['source_id'],'unit_id':unit['id']},
                                            refresh=missing_links)
                    if self.usage['documents']>before:result['request_count']+=1
                    for child_link in self._rank_links(deeper.get('links'),unit,limit-len(result['deeper_links'])):
                        result['deeper_links'].append({**child_link,'parent_url':row['url']})
                        if len(result['deeper_links'])>=limit:break
                except ResearchError as error:
                    if self.usage['documents']>before:result['request_count']+=1
                    result['errors'].append({'url':row['url'],'code':error.code,'message':error.message})
        if result['request_count']==0:
            result['notice']='本次只列出已读页面上的入口，没有新增原文请求；读取入口请用 read_official_source。'
        return result

    def _selected(self,args):
        self._apply_fields()
        ids=args.get('program_ids',list(self.programs))
        if not ids and not self.programs and (self._source_candidate_records or
                self.candidate_metrics and self.candidate_metrics.get('scanned_count',0)):
            return []
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
        _object(args,('program_ids',));rows=self._counterfactual_rows(args)
        result={**plan_routes(rows,self.task['profile']),'research_scope':self._counterfactual_scope(args,rows)}
        self.algorithm['routes']=result
        self.executed_tools.add('plan_subject_routes')
        return result

    def adjustments(self,args):
        _object(args,('program_ids','allowed_changes'));changes=args.get('allowed_changes')
        if not isinstance(changes,dict) or not changes or len(changes)>3:
            raise ResearchError('invalid_adjustment','请选择有限的条件试算情景。')
        if any(not isinstance(values,list) or len(values)>4 for values in changes.values()):
            raise ResearchError('invalid_adjustment','每项条件最多试算四个值。')
        # Validate every user-authorized combination before an extra local catalog read.
        suggest_adjustments([],self.task['profile'],changes)
        if 'program_ids' not in args:self._expand_research_pool(changes)
        rows=self._counterfactual_rows(args)
        result={**suggest_adjustments(rows,self.task['profile'],changes),
                'research_scope':self._counterfactual_scope(args,rows)}
        self.algorithm['adjustments']=result
        self.executed_tools.add('suggest_adjustments')
        return result

    def analyze_decisions(self,args):
        _object(args,('program_ids','top_k','scenarios'));top_k=args.get('top_k',5);scenarios=args.get('scenarios',[])
        if type(top_k) is not int or not 1<=top_k<=12:
            raise ResearchError('invalid_tool_arguments','前k名数量必须为1至12的整数。')
        if not isinstance(scenarios,list) or len(scenarios)>8:
            raise ResearchError('invalid_tool_arguments','试算场景必须为最多8项的数组。')
        try:result=decision_analysis(self._selected(args),self.task['profile'],top_k=top_k,scenarios=scenarios)
        except EngineValidationError as error:
            raise ResearchError('invalid_tool_arguments',str(error)) from error
        self.algorithm['decision_intelligence']=result
        self.executed_tools.add('analyze_decisions')
        return result

    def ensure_algorithms(self):
        if not self.programs and not self._research_pool and not (self.candidate_metrics and self.candidate_metrics.get('scanned_count',0)):return []
        self.refresh_knowledge();self._apply_fields()
        stop=self.stop_event
        if stop is not None:
            cancelled=stop.user_stop.is_set() or stop.service_stop.is_set() if hasattr(stop,'user_stop') else stop.is_set()
            if cancelled:return []
        signature=hashlib.sha256(json.dumps([self.programs,self.task['profile'],self._research_pool_version],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        if signature==self._algorithm_signature:return []
        self.algorithm.pop('adjustments',None)
        self.compare({});actions=['compare_candidates']
        self.analyze_decisions({});actions.append('analyze_decisions')
        units={subject.get('exam_unit') for subject in self.task['profile'].get('subjects',[])}
        pool=self._research_pool or self.programs
        expected={subject.get('exam_unit') for row in pool.values() for subject in row.get('subjects',[])}
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
        kinds={'provinces':'hard','preferred_provinces':'soft','excluded_subjects':'excluded'}
        condition_types={field:kinds.get(field,'fact') for field in updates}
        conflicts=[{'field':field,'current':deepcopy(self.task['profile'].get(field)),'proposed':deepcopy(value)}
                   for field,value in updates.items()
                   if field in self.task.get('confirmed_fields',[]) and value!=self.task['profile'].get(field)]
        notice='以下是AI从你的原句理解的条件；确认后才用于新一轮算法，不会自动覆盖已填条件。'
        if conflicts:notice='部分提案与已确认条件不同，请先核对冲突再决定是否确认；当前算法输入没有改变。'
        self.profile_proposal={'updates':deepcopy(updates),'profile':checked,'evidence':list(quotes.values()),
                               'condition_types':condition_types,'conflicts':conflicts,
                               'requires_confirmation':True,'notice':notice}
        return deepcopy(self.profile_proposal)

    def plan(self,questions=None):
        self._apply_fields()
        return build_plan(self.task,self.programs,self.sources,self.algorithm,self.gaps,self.usage,
                          self.loaded_skills,self.executed_tools,self.questions() if questions is None else questions,
                          self.task.get('previous_report'),inheritance=self.inheritance,
                          field_candidates=self.field_candidates,
                          official_discovery=list(self._official_discovery.values()),
                          read_receipts=research_admission_tools._receipts(self),
                          admission_facts=research_admission_tools.snapshot(self),
                          searched_units=self.searched_units)

    def decisions(self):
        """Deterministic recommendation rows whose reasons and sources stay traceable."""
        matching=(self.algorithm or {}).get('matching') if isinstance(self.algorithm,dict) else {}
        matching=matching if isinstance(matching,dict) else {}
        by_url={str(row.get('url')):row.get('id') for row in self.sources.values() if isinstance(row,dict) and row.get('url')}
        rows=[]
        for kind in ('matches','needs_confirmation','excluded'):
            entries=matching.get(kind) if isinstance(matching.get(kind),list) else []
            for entry in entries:
                if not isinstance(entry,dict):continue
                program=entry.get('program') if isinstance(entry.get('program'),dict) else {}
                reasons,links=[],[]
                for reason in entry.get('reasons') or []:
                    if not isinstance(reason,dict):continue
                    evidence=[]
                    for ref in reason.get('evidence') or []:
                        if not isinstance(ref,dict):continue
                        identifier=by_url.get(str(ref.get('url')))
                        evidence.append({**deepcopy(ref),'source_id':identifier,
                                         'return_to_original':bool(ref.get('url'))})
                        if ref.get('url') and not any(item.get('url')==ref.get('url') for item in links):
                            links.append({'url':ref.get('url'),'title':ref.get('title') or '来源原文',
                                          'year':ref.get('year'),'source_id':identifier})
                    reasons.append({'field':reason.get('field'),'label':reason.get('label'),
                                    'status':reason.get('status'),'message':reason.get('message'),
                                    'evidence':evidence,
                                    'evidence_refs':research_candidate_tools.reason_refs(program,reason.get('field'))})
                rows.append({'candidate_id':program.get('id'),'unit_id':program.get('unit_id'),
                             'unit_name':program.get('unit_name'),'name':program.get('name'),
                             'status':entry.get('status'),'rank_score':entry.get('rank_score'),
                             'rank_interval':deepcopy(entry.get('rank_interval')),
                             'subject_changes':deepcopy(entry.get('subject_changes')),
                             'reasons':reasons,'original_links':links,'deterministic':True,
                             'candidate_origin':program.get('candidate_origin'),
                             'verification_mode':program.get('verification_mode','published_snapshot'),
                             'candidate_ref':deepcopy(program.get('candidate_ref')),
                             'validation':deepcopy(program.get('validation')),
                             'evidence_refs':deepcopy(program.get('evidence_refs',[])),
                             'not_admission_probability':True})
        return rows[:24]

    def report(self,assistant_summary='',questions=()):
        self.refresh_knowledge()
        self._apply_fields()
        if self._knowledge_invalidated:assistant_summary=research_knowledge.CURRENT_NOTICE
        candidates=[self._candidate(row) for row in self.programs.values()]
        gaps=list(dict.fromkeys([*self.gaps,'全国专业目录尚未完整覆盖，本轮候选不代表全国最优或全部选择。',
                               '2027资料仅作2028入学规划参考；具体报考条件和新年度目录需继续核验。',
                               *(['尚未找到可计算方向，可以继续定位官方原文或补充意向地区。'] if not candidates else [])]))
        actual_questions=self.questions()
        if self.profile_proposal:actual_questions=[{'field':'profile_proposal','label':'请确认识别到的备考条件是否准确。','message':'确认后才用于新一轮算法。','options':[]}]
        plan=self.plan(actual_questions)
        decisions=self.decisions()
        original_count=sum(row.get('candidate_origin')=='official_original' for row in candidates)
        published_count=len(candidates)-original_count
        return {'summary':f"本轮整理了{len({row.get('unit_id') for row in candidates})}所学校的{len(candidates)}个方向（已发布{published_count}个、官方原文临时方向{original_count}个）、{len(self.sources)}份来源或检索线索。",
                'plan':plan,
                'assistant_summary':assistant_summary,'questions':actual_questions,'candidates':candidates,
                'research_candidates':research_candidate_tools.snapshot(self),
                'official_discovery':list(deepcopy(self._official_discovery).values()),
                'admission_facts':research_admission_tools.snapshot(self),
                'evidence':list(deepcopy(self.sources).values()),'algorithm':deepcopy(self.algorithm),'gaps':gaps,
                'profile_proposal':deepcopy(self.profile_proposal),
                'inheritance':deepcopy(self.inheritance),
                'field_candidates':deepcopy(self.field_candidates),
                'field_applications':deepcopy(self.field_applications),
                'decisions':decisions,
                'evidence_ledger':evidence_audit(decisions),
                'decision_intelligence':deepcopy(self.algorithm.get('decision_intelligence')),
                'skills':self.skills(),'knowledge':{'saved_count':len(self.saved_sources),'cache_hits':len(self.cache_hits),
                    'accepted_count':sum(state=='accepted' for state in self._knowledge_states.values()),
                    'pending_count':sum(state=='pending' for state in self._knowledge_states.values()),'filtered_count':len(self._filtered_sources),
                    'invalidated_count':len(self._knowledge_invalidated),
                    'errors':list(dict.fromkeys([*self.knowledge_errors,*self._knowledge_failures.values()])),
                    'notice':'保存数包含通过准入与待核资料；仅通过准入资料可复用。过滤来源不入库，新增原文仍须字段审校才能用于招生事实。'},
                'coverage':{'candidate_count':len(candidates),'candidate_unit_count':len({row.get('unit_id') for row in candidates}),
                            'research_original_count':original_count,'published_count':published_count,
                            'research_pool_count':len(self._research_pool),
                            'research_original_pool_count':sum(row.get('candidate_origin')=='official_original'
                                for row in self._research_pool.values()),
                            'turn_id':self.task.get('turn_id',0),'searched_units':sorted(self.searched_units),
                            'selection':deepcopy(self.candidate_metrics),
                            'national_complete':False,'reference_year':self.task['reference_year'],'target_year':self.task['target_year'],
                            'revision':self.revision,'verification_mode':'bounded_research'}}
