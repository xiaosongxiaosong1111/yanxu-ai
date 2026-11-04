"""Local source memory, separate from adopted admission facts and personal research inputs."""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import sqlite3
import threading
from urllib.parse import parse_qsl, urlsplit, urlunsplit
import uuid
from source_privacy import contains_personal_roster
from knowledge_quality import assess_quality, QUALITY_VERSION, QUALITY_STATES
from public_sources import official_source_url
from knowledge_schema import ensure_schema
import knowledge_repository as segments
from document_extraction import canonical_document


STATUSES=('search_lead','official_original_read','published_snapshot')
TEXT_LIMIT=500000
DETAIL_LIMIT=32000
_SECRETS=re.compile(r'\b(?:sk-[A-Za-z0-9_-]{8,}|AKID[A-Za-z0-9]{12,}|Bearer\s+\S{6,}|TC3-HMAC-SHA256\s+Credential\s*=|secret[_-]?(?:id|key)["\']?\s*[:=]\s*["\']?\S{4,})',re.I)
_SENSITIVE_FIELDS={'api_key','secret_id','secret_key','authorization','profile','password','access_token','session_token'}
_PRIVATE_QUERY_KEYS={re.sub('[^a-z0-9]','',name) for name in _SENSITIVE_FIELDS}|{'apikey','token','key','auth','credential','signature'}


class KnowledgeError(Exception):
    def __init__(self,code,message,status=400):
        super().__init__(message)
        self.code,self.message,self.status=code,message,status


def _stamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _pending_quality():
    return {'version':QUALITY_VERSION,'state':'pending','reusable':False,'material_type':'unknown',
            'reasons':[{'code':'quality_not_assessed','message':'这份旧资料尚未通过当前质量准入检查。'}]}


def _valid_quality(value):
    if (isinstance(value,dict) and type(value.get('version')) is int and value['version']==QUALITY_VERSION
            and value.get('state') in QUALITY_STATES and type(value.get('reusable')) is bool
            and value['reusable']==(value['state']=='accepted') and isinstance(value.get('reasons'),list)):
        return value
    return _pending_quality()


def _stored_quality(row, has_quality):
    value = _valid_quality(json.loads(row['payload']).get('quality'))
    if not has_quality:return _pending_quality()
    if row['quality'] == 'excluded' or value['state'] == 'excluded':
        return {**value, 'state': 'excluded', 'reusable': False}
    if row['quality'] != value['state']:return _pending_quality()
    return value


def _text(value,limit,empty=True):
    if not isinstance(value,str) or len(value)>limit or _SECRETS.search(value) or any(ord(c)<32 and c not in '\r\n\t' for c in value):
        raise KnowledgeError('invalid_knowledge','资料内容格式不正确，不能包含凭据或私密配置。')
    if not empty and not value.strip():
        raise KnowledgeError('invalid_knowledge','资料内容不能为空。')
    try:value.encode('utf-8')
    except UnicodeError:raise KnowledgeError('invalid_knowledge','资料字符格式不正确。') from None
    return value.strip()


def _optional(source,key,limit):
    value=source.get(key)
    return _text('' if value is None else value,limit)


def _year(value):
    if value is not None and (type(value) is not int or not 1900<=value<=2100):
        raise KnowledgeError('invalid_knowledge','资料年份不正确。')
    return value


def _url(value):
    value=_text(value,4096,False)
    try:
        parsed=urlsplit(value);port=parsed.port;host=(parsed.hostname or '').rstrip('.')
        unsafe=parsed.scheme not in {'http','https'} or not host or parsed.username is not None or parsed.password is not None
        unsafe=unsafe or '\\' in value or '%' in host or any(c.isspace() or ord(c)==127 for c in value)
        unsafe=unsafe or port not in {None,80,443} or host in {'localhost'} or host.endswith(('.localhost','.local','.internal'))
        try:unsafe=unsafe or not ipaddress.ip_address(host).is_global
        except ValueError:unsafe=unsafe or '.' not in host or bool(re.fullmatch(r'[\d.]+',host))
        unsafe=unsafe or any(re.sub('[^a-z0-9]','',key.lower()) in _PRIVATE_QUERY_KEYS for key,_ in parse_qsl(parsed.query))
        if unsafe:raise ValueError('unsafe')
        return urlunsplit((parsed.scheme,parsed.netloc.lower(),parsed.path or '/',parsed.query,''))
    except (ValueError,TypeError):
        raise KnowledgeError('unsafe_knowledge_url','知识库只保存不含凭据的公共网页链接。') from None


class KnowledgeStore:
    def __init__(self,data_dir):
        self.data_dir=Path(data_dir).resolve()
        self.path=self.data_dir/'runtime/knowledge.sqlite'
        self._lock=threading.RLock()
        self._registry_signature=None;self._registry_units={}

    @contextmanager
    def _connection(self,create=False):
        try:
            if create:self.path.parent.mkdir(parents=True,exist_ok=True)
            connection=sqlite3.connect(self.path,timeout=5)
        except (OSError,sqlite3.Error):
            raise KnowledgeError('knowledge_unavailable','本机知识库暂时无法读取或保存，请稍后重试。',503) from None
        connection.row_factory=sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        try:
            with connection:
                if create:ensure_schema(connection)
                yield connection
        except (sqlite3.Error,ValueError,TypeError):
            raise KnowledgeError('knowledge_unavailable','本机知识库暂时无法读取或保存，请稍后重试。',503) from None
        finally:connection.close()

    @staticmethod
    def _has_fts(connection):
        return connection.execute("SELECT 1 FROM sqlite_master WHERE name='knowledge_fts'").fetchone() is not None

    @staticmethod
    def _has_quality(connection):
        return any(row['name']=='quality' for row in connection.execute('PRAGMA table_info(knowledge_entries)'))

    @classmethod
    def _quality_column(cls,connection):
        return 'quality' if cls._has_quality(connection) else "'pending'"

    def _blob_path(self,digest):
        target=self.data_dir/'runtime/research-sources'/(digest+'.bin')
        if not re.fullmatch('[0-9a-f]{64}',digest) or not target.resolve().is_relative_to(self.data_dir):
            raise KnowledgeError('invalid_knowledge_snapshot','原文留档标识不正确。')
        return target

    def _blob_available(self,digest):
        if not digest:return False
        try:
            target=self._blob_path(digest)
            if not target.is_file() or target.stat().st_size>8*1024*1024:return False
            with target.open('rb') as stream:raw=stream.read(8*1024*1024+1)
            return len(raw)<=8*1024*1024 and hashlib.sha256(raw).hexdigest()==digest
        except (OSError,KnowledgeError):return False

    def _prepare(self,source,document):
        if not isinstance(source,dict) or source.keys()&_SENSITIVE_FIELDS:
            raise KnowledgeError('invalid_knowledge','资料不能包含个人档案或服务凭据。')
        status=source.get('status','search_lead')
        if status not in STATUSES:raise KnowledgeError('invalid_knowledge','资料状态不正确。')
        url=_url(source.get('url',''));title=_text(source.get('title','未命名资料'),1000)
        unit=_optional(source,'unit_id',100);unit_name=_optional(source,'unit_name',200)
        year=_year(source.get('year'));requested=_year(source.get('requested_year'))
        digest=_optional(source,'sha256',64)
        if digest and not re.fullmatch('[0-9a-f]{64}',digest):raise KnowledgeError('invalid_knowledge_snapshot','原文留档标识不正确。')
        excerpt=_optional(source,'excerpt',TEXT_LIMIT)
        text=excerpt;pages=[];origin='captured_excerpt';truncated=False;new_original=False;raw=None
        if document is not None:
            if not isinstance(document,dict):raise KnowledgeError('invalid_knowledge','原文格式不正确。')
            text=_optional(document,'text',4*TEXT_LIMIT) or excerpt
            if not isinstance(document.get('pages',[]),list):raise KnowledgeError('invalid_knowledge','原文页码格式不正确。')
            pages=[{'page':item.get('page'),'text':_optional(item,'text',4*TEXT_LIMIT)}
                   for item in document.get('pages',[])[:1000] if isinstance(item,dict) and type(item.get('page')) is int and item['page']>0]
            origin='full_extraction';raw=document.get('raw')
            if status=='official_original_read':
                if not isinstance(raw,bytes) or len(raw)>8*1024*1024 or hashlib.sha256(raw).hexdigest()!=digest or document.get('attribution_status')!='verified' or _url(document.get('url',url))!=url:
                    raise KnowledgeError('invalid_knowledge_snapshot','原文归属或完整性未通过，不能保存为已读取原文。')
                new_original=True
        private=contains_personal_roster(title,text,*(p['text'] for p in pages))
        if private:text='';excerpt='';pages=[];origin='metadata_only'
        truncated=len(text)>TEXT_LIMIT or len(pages)>20 or any(len(p['text'])>1600 for p in pages)
        text=text[:TEXT_LIMIT];pages=[{'page':p['page'],'text':p['text'][:1600]} for p in pages[:20]]
        available=status=='official_original_read' and (new_original or self._blob_available(digest))
        snapshot_status='available' if available else 'not_archived'
        if status=='official_original_read' and not available:
            status='search_lead';text='';excerpt='';pages=[];origin='captured_excerpt';snapshot_status='missing_or_changed'
        value={'url':url,'title':title,'unit_id':unit,'unit_name':unit_name,'year':year,'requested_year':requested,
                'status':status,'sha256':digest,'excerpt':excerpt[:DETAIL_LIMIT],'checked_at':_optional(source,'checked_at',100) or None,
                'field_verification':'published_snapshot' if status=='published_snapshot' else 'not_structured' if available else 'needs_review',
                'text_origin':origin,'text_truncated':truncated,'snapshot_status':snapshot_status,
                'content_notice':'材料可能包含逐人录取信息，仅保留来源和版本，不索引个人名单。' if private else '',
                'full_text':text,'pages':pages}
        proof=self._authority(value,new_original=new_original,available=available)
        quality=assess_quality(value,text,original_available=available,authority_verified=proof['status']=='verified',pages=pages)
        return {**value,'authority_evidence':proof,'quality':_valid_quality(quality),'_raw':raw if new_original else None}

    def _authority(self,value,*,new_original=False,available=False):
        basis=''
        if new_original:basis='document_attribution'
        elif value.get('status')=='published_snapshot' and value.get('unit_id'):basis='published_catalog'
        elif available:
            prior=value.get('authority_evidence') or {}
            if (prior.get('status')=='verified' and prior.get('basis') in {'document_attribution','unit_registry_url'}
                    and all(prior.get(key)==value.get(key) for key in ('url','unit_id','sha256'))):basis=prior['basis']
            else:
                path=self.data_dir/'units.json'
                try:
                    signature=(path.stat().st_size,path.stat().st_mtime_ns)
                    if signature[0]<=8*1024*1024:
                        if signature!=self._registry_signature:
                            registry=json.loads(path.read_text('utf-8-sig'))
                            self._registry_units={row.get('id'):row for row in registry.get('units',[]) if isinstance(row,dict)}
                            self._registry_signature=signature
                        unit=self._registry_units.get(value.get('unit_id'))
                        if unit and official_source_url(value.get('url'),unit):basis='unit_registry_url'
                except (OSError,ValueError,TypeError,AttributeError):pass
        return {'status':'verified' if basis else 'unverified','basis':basis,
                **({key:value.get(key) for key in ('url','unit_id','sha256')} if basis else {})}

    def _write_blob(self,value):
        raw=value.get('_raw')
        if raw is None:return
        try:
            target=self._blob_path(value['sha256']);target.parent.mkdir(parents=True,exist_ok=True)
            if not self._blob_available(value['sha256']):
                temporary=target.with_name(value['sha256']+'.'+uuid.uuid4().hex+'.tmp')
                temporary.write_bytes(raw);temporary.replace(target)
        except OSError:raise KnowledgeError('knowledge_unavailable','原文无法安全留档，请稍后重试。',503) from None

    def _index(self,connection,identifier,value,full):
        if not self._has_fts(connection):return
        connection.execute('DELETE FROM knowledge_fts WHERE entry_id=?',(identifier,))
        if value['quality']['state']!='excluded':
            connection.execute('INSERT INTO knowledge_fts VALUES(?,?,?,?)',(identifier,value['title'],value['unit_name'],full))

    def _reference(self,connection,identifier,research_id):
        if research_id:connection.execute('INSERT OR IGNORE INTO knowledge_provenance VALUES(?,?)',(identifier,research_id))

    def save_source(self,source,research_id='',query='',document=None):
        if research_id and (not isinstance(research_id,str) or not re.fullmatch(r'r_[0-9a-f]{32}',research_id)):
            raise KnowledgeError('invalid_knowledge','研究引用编号不正确。')
        source,document,canonical=self._document_input(source,document)
        value=self._prepare(source,document);stamp=_stamp()
        family=hashlib.sha256((value['url']+'|'+value['unit_id']).encode()).hexdigest()
        version=(value['sha256']+'|'+str(value['year'])) if value['sha256'] else value['status']+'|'+str(value['year'])+'|'+str(value['requested_year'])
        if value['quality']['state']=='excluded' and not self.path.is_file():
            return {key:value[key] for key in ('title','url','unit_id','unit_name','year','requested_year','status','quality')}|{'id':None,'saved':False}
        with self._lock,self._connection(create=True) as connection:
            if value['status']=='search_lead':
                existing=connection.execute("SELECT id FROM knowledge_entries WHERE family=? AND requested_year IS ? AND (? IS NULL OR year IS ?) AND status IN ('official_original_read','published_snapshot') ORDER BY last_seen_at DESC LIMIT 1",(family,value['requested_year'],value['year'],value['year'])).fetchone()
                if existing:
                    self._reference(connection,existing['id'],research_id)
                    return self._public(connection,existing['id'])
            current=connection.execute('SELECT id,first_seen_at FROM knowledge_entries WHERE family=? AND version_key=?',(family,version)).fetchone()
            if current is None and value['status']=='official_original_read':
                current=connection.execute("SELECT id,first_seen_at FROM knowledge_entries WHERE family=? AND requested_year IS ? AND status='search_lead' AND sha256='' LIMIT 1",(family,value['requested_year'])).fetchone()
            if current:
                previous=self._public(connection,current['id'])
                ranks={'excluded':0,'pending':1,'accepted':2}
                if ranks[previous['quality']['state']]>ranks[value['quality']['state']]:
                    self._reference(connection,current['id'],research_id);return previous
            if value['quality']['state']=='excluded':
                return {key:value[key] for key in ('title','url','unit_id','unit_name','year','requested_year','status','quality')}|{'id':None,'saved':False}
            self._write_blob(value)
            identifier=current['id'] if current else 'k_'+uuid.uuid4().hex[:24]
            first=current['first_seen_at'] if current else stamp
            payload={key:item for key,item in value.items() if key not in {'full_text','pages','_raw'}}
            row=(identifier,family,version,value['url'],value['unit_id'],value['unit_name'],value['year'],value['requested_year'],value['status'],value['title'],value['sha256'],value['full_text'],json.dumps(value['pages'],ensure_ascii=False),first,stamp,json.dumps(payload,ensure_ascii=False),value['quality']['state'])
            columns=('id','family','version_key','url','unit_id','unit_name','year','requested_year','status','title',
                     'sha256','full_text','pages','first_seen_at','last_seen_at','payload','quality')
            update=','.join(key+'=excluded.'+key for key in columns if key not in {'id','first_seen_at'})
            connection.execute('INSERT INTO knowledge_entries ('+','.join(columns)+') VALUES ('+','.join('?' for _ in row)+') ON CONFLICT(id) DO UPDATE SET '+update,row)
            self._index(connection,identifier,value,value['full_text'])
            self._reference(connection,identifier,research_id)
            if canonical is not None and value['quality']['state']!='excluded':
                segments.persist_document(connection,identifier,value,canonical,stamp)
                from knowledge_operations import reconcile_versions
                reconcile_versions(self,connection,identifier)
            from knowledge_vector_schema import dirty
            dirty(connection,identifier)
            return self._public(connection,identifier)

    def _document_input(self,source,document):
        if document is None:return source,None,None
        if not isinstance(source,dict) or not isinstance(document,dict):raise KnowledgeError('invalid_knowledge','原文格式不正确。')
        pages=document.get('pages',[])
        if isinstance(pages,list) and contains_personal_roster(str(source.get('title','')),document.get('text',''),
                *(item.get('text','') for item in pages if isinstance(item,dict))):
            return source,document,None
        try:canonical=canonical_document(document)
        except (ValueError,TypeError):raise KnowledgeError('invalid_knowledge','原文提取内容或页码格式不正确。') from None
        normalized=dict(document)
        text=document.get('text')
        if text is None:text=''
        if not isinstance(text,str):raise KnowledgeError('invalid_knowledge','原文文本格式不正确。')
        normalized['text']=canonical['text']
        normalized['pages']=canonical['pages']
        if canonical.get('format')=='pdf':normalized['text']=normalized['text'].replace('\f','\n')
        if canonical['extraction_status'] in {'unsupported','needs_ocr'}:
            normalized.update(text='',pages=[])
            source={**source,'excerpt':''}
        elif canonical.get('format')=='pdf' and isinstance(source,dict):
            source={**source,'excerpt':str(source.get('excerpt') or '').replace('\f','\n')}
        return source,normalized,canonical

    def _public(self,connection,identifier,detail=False,terms=()):
        row=connection.execute('SELECT * FROM knowledge_entries WHERE id=?',(identifier,)).fetchone()
        if row is None:raise KnowledgeError('unknown_knowledge','没有找到这份知识库资料。',404)
        has_quality=self._has_quality(connection)
        value=json.loads(row['payload']);value={**value,'quality':_stored_quality(row,has_quality)}
        available=row['status']=='official_original_read' and self._blob_available(row['sha256'])
        full=row['full_text'];pages=json.loads(row['pages'])
        if row['status']=='official_original_read' and not available:
            value={**value,'status':'search_lead','excerpt':'','field_verification':'needs_review','snapshot_status':'missing_or_changed'}
            value['authority_evidence']={'status':'unverified','basis':''}
            value['quality']=_valid_quality(assess_quality(value,'',original_available=False,authority_verified=False))
            full='';pages=[]
            connection.execute("UPDATE knowledge_entries SET status='search_lead',payload=? WHERE id=?",(json.dumps(value,ensure_ascii=False),identifier))
            self._index(connection,identifier,value,'')
            from knowledge_vector_schema import dirty
            dirty(connection,identifier)
        if has_quality and row['quality']!=value['quality']['state']:
            connection.execute('UPDATE knowledge_entries SET quality=?,payload=? WHERE id=?',(value['quality']['state'],json.dumps(value,ensure_ascii=False),identifier))
        if value['quality']['state']=='excluded' or value.get('snapshot_status')=='missing_or_changed':
            full='';pages=[];value={**value,'excerpt':''}
        snippet=value.get('excerpt','')
        for term in terms:
            position=full.casefold().find(term.casefold())
            if position>=0:snippet=full[max(0,position-100):position+500];break
        count=connection.execute('SELECT count(*) FROM knowledge_entries WHERE family=?',(row['family'],)).fetchone()[0]
        provenance=[item[0] for item in connection.execute('SELECT research_id FROM knowledge_provenance WHERE entry_id=? ORDER BY research_id',(identifier,))]
        public={**value,**segments.state_summary(connection,identifier),'id':identifier,'excerpt':snippet[:600],'first_seen_at':row['first_seen_at'],'last_seen_at':row['last_seen_at'],
                'provenance':provenance,'version_count':count,'source_group':'sha_'+row['sha256'] if row['sha256'] else 'url_'+row['family'][:24],
                'original_available':available,'text_truncated':value.get('text_truncated',False) or len(full)>DETAIL_LIMIT,'saved':True}
        public['usable']=self._usable(public)
        if public.get('validity')!='active':
            full='';pages=[];public['excerpt']=''
        if detail:public={**public,'text':full[:DETAIL_LIMIT],'text_length':len(full),'pages':pages}
        return public

    @staticmethod
    def _usable(value):
        return (value.get('quality',{}).get('state')=='accepted' and value.get('quality',{}).get('reusable') is True
                and value.get('validity','active')=='active' and type(value.get('year')) is int
                and (value.get('status')=='published_snapshot' or
                     value.get('status')=='official_original_read' and value.get('original_available') is True))

    @staticmethod
    def _entry_id(value):
        if not isinstance(value,str) or not re.fullmatch(r'k_[0-9a-f]{24}',value):
            raise KnowledgeError('invalid_knowledge','知识库资料编号不正确。')
        return value

    def change_validity(self,entry_id,validity,reason,replacement_id=None):
        from knowledge_operations import change_validity
        return change_validity(self,entry_id,validity,reason,replacement_id)

    def choose_version(self,entry_id,reason):
        from knowledge_operations import choose_version
        return choose_version(self,entry_id,reason)

    def jobs(self,entry_id=None):
        from knowledge_operations import jobs
        return jobs(self,entry_id)

    def search_segments(self,query='',entry_id=None,unit_id='',year=None,page=1,page_size=8,quality='accepted'):
        query=_text(query,240);unit_id=_text(unit_id,100);year=_year(year)
        if entry_id is not None:self._entry_id(entry_id)
        if (not isinstance(quality,str) or quality not in {'all',*QUALITY_STATES} or type(page) is not int or not 1<=page<=1000000
                or type(page_size) is not int or not 1<=page_size<=100):
            raise KnowledgeError('invalid_knowledge_query','片段筛选或分页参数不正确。')
        empty={'segments':[],'total':0,'page':page,'page_size':page_size,'index_state':'unindexed'}
        if not self.path.is_file():return empty
        scope={'query':query,'entry_id':entry_id,'unit_id':unit_id,'year':year,'quality':quality}
        with self._lock,self._connection() as connection:
            if not segments.available(connection):return empty
            for item in segments.matching_sources(connection,**scope):self._public(connection,item[0])
            rows,total,match_mode=segments.query_rows(connection,**scope,page=page,page_size=page_size)
            result=[]
            for item in rows:
                row=segments.segment_row(connection,item[0]);source=self._public(connection,row['entry_id'])
                current=source.get('extraction') or {}
                if (source.get('validity')!='active' or current.get('generation_id')!=row['generation_id']
                        or quality=='accepted' and not self._usable(source)):continue
                result.append({**segments.public_segment(row,source),'match_mode':match_mode})
            return {**empty,'segments':result,'total':total,'index_state':'ready','match_mode':match_mode}

    def read_segment(self,segment_id,*,before=0,after=0,max_chars=6000,trusted_only=True):
        if (not isinstance(segment_id,str) or not re.fullmatch(r'sg_[0-9a-f]{32}',segment_id)
                or type(before) is not int or not 0<=before<=3 or type(after) is not int or not 0<=after<=3
                or type(max_chars) is not int or not 1<=max_chars<=20000 or type(trusted_only) is not bool):
            raise KnowledgeError('invalid_segment_query','片段编号或补读范围不正确。')
        if not self.path.is_file():raise KnowledgeError('unknown_segment','没有找到这段来源。',404)
        with self._lock,self._connection() as connection:
            row=segments.segment_row(connection,segment_id)
            if row is None:raise KnowledgeError('unknown_segment','没有找到这段来源。',404)
            source=self._public(connection,row['entry_id'])
            current=(source.get('extraction') or {}).get('generation_id')==row['generation_id'] and row['generation_state']=='succeeded'
            if source['validity']!='active' or source['quality']['state']=='excluded' or not current or trusted_only and not self._usable(source):
                raise KnowledgeError('knowledge_not_reusable','这段来源已失效或尚未通过核验，不能作为可信依据。',409)
            return segments.read_window(connection,row,source,before,after,max_chars)

    def rebuild_segments(self,entry_id):
        from knowledge_indexing import rebuild
        return rebuild(self,entry_id)

    def get(self,entry_id):
        if not isinstance(entry_id,str) or not re.fullmatch(r'k_[0-9a-f]{24}',entry_id):
            raise KnowledgeError('invalid_knowledge','知识库资料编号不正确。')
        if not self.path.is_file():raise KnowledgeError('unknown_knowledge','没有找到这份知识库资料。',404)
        with self._lock,self._connection() as connection:
            from knowledge_operations import versions
            return {**self._public(connection,entry_id,detail=True), 'versions': versions(self,connection,entry_id)}

    def search(self,query='',status='',year=None,unit_id='',page=1,page_size=12,quality='accepted'):
        query=_text(query,240);unit_id=_text(unit_id,100);year=_year(year)
        if status not in ('',*STATUSES) or quality not in ('all',*QUALITY_STATES) or type(page) is not int or not 1<=page<=1000000 or type(page_size) is not int or not 1<=page_size<=100:
            raise KnowledgeError('invalid_knowledge_query','知识库筛选或分页参数不正确。')
        empty={'entries':[],'total':0,'page':page,'page_size':page_size,'summary':{'total':0,**{s:0 for s in STATUSES},'quality_counts':{s:0 for s in QUALITY_STATES}},'facets':{'units':[],'years':[]}}
        if not self.path.is_file():return empty
        terms=query.split()[:8];where=[];values=[]
        if status:where.append('status=?');values.append(status)
        if year is not None:where.append('year=?');values.append(year)
        if unit_id:where.append('unit_id=?');values.append(unit_id)
        with self._lock,self._connection() as connection:
            quality_column=self._quality_column(connection)
            if quality!='all':where.append(quality_column+'=?');values.append(quality)
            if quality=='accepted' and segments.available(connection):
                where.append("id NOT IN (SELECT entry_id FROM knowledge_entry_state WHERE validity<>'active')")
            # Missing files are cheap to invalidate globally; actual cache hits are hashed below.
            for record in connection.execute("SELECT id,sha256 FROM knowledge_entries WHERE status='official_original_read'").fetchall():
                try:
                    blob=self._blob_path(record['sha256'])
                    missing=not blob.is_file() or blob.stat().st_size>8*1024*1024
                except (OSError,KnowledgeError):missing=True
                if missing:self._public(connection,record['id'])
            if terms and quality not in {'all','excluded'} and all(len(term)>=3 for term in terms) and self._has_fts(connection):
                where.append('id IN (SELECT entry_id FROM knowledge_fts WHERE knowledge_fts MATCH ?)')
                values.append(' AND '.join('"'+term.replace('"','""')+'"' for term in terms))
            else:
                for term in terms:where.append("instr(lower(title||' '||unit_name||' '||CASE WHEN "+quality_column+"='excluded' THEN '' ELSE full_text END),lower(?))>0");values.append(term)
            clause=' WHERE '+' AND '.join(where) if where else ''
            order=" ORDER BY CASE status WHEN 'official_original_read' THEN 0 WHEN 'published_snapshot' THEN 1 ELSE 2 END,last_seen_at DESC,id"
            while True:
                records=connection.execute('SELECT id,status,'+quality_column+' AS qstate FROM knowledge_entries'+clause+order+' LIMIT ? OFFSET ?',(*values,page_size,(page-1)*page_size)).fetchall()
                entries=[self._public(connection,record['id'],terms=terms) for record in records]
                if all(record['status']==entry['status'] and record['qstate']==entry['quality']['state'] for record,entry in zip(records,entries)):break
            total=connection.execute('SELECT count(*) FROM knowledge_entries'+clause,values).fetchone()[0]
            counts={r[0]:r[1] for r in connection.execute('SELECT status,count(*) FROM knowledge_entries GROUP BY status')}
            quality_counts={r[0]:r[1] for r in connection.execute('SELECT '+quality_column+' AS state,count(*) FROM knowledge_entries GROUP BY state')}
            summary={'total':sum(counts.values()),**{s:counts.get(s,0) for s in STATUSES},'quality_counts':{s:quality_counts.get(s,0) for s in QUALITY_STATES},'notice':'质量准入不等于字段已核验；年份与适用范围仍需逐项确认。'}
            facet_clause='' if quality=='all' else ' AND '+quality_column+'=?';facet_args=() if quality=='all' else (quality,)
            units=[{'unit_id':r[0],'unit_name':r[1] or r[0]} for r in connection.execute("SELECT unit_id,max(unit_name) FROM knowledge_entries WHERE unit_id<>''"+facet_clause+' GROUP BY unit_id ORDER BY max(unit_name)',facet_args)]
            years=[r[0] for r in connection.execute('SELECT DISTINCT year FROM knowledge_entries WHERE year IS NOT NULL'+facet_clause+' ORDER BY year DESC',facet_args)]
            return {'entries':entries,'total':total,'page':page,'page_size':page_size,'summary':summary,'facets':{'units':units,'years':years}}

    def reassess(self):
        counts={'total':0,'accepted':0,'pending':0,'excluded':0,'changed':0}
        if not self.path.is_file():return counts
        with self._lock,self._connection(create=True) as connection:
            for row in connection.execute('SELECT * FROM knowledge_entries').fetchall():
                value=json.loads(row['payload']);available=row['status']=='official_original_read' and self._blob_available(row['sha256'])
                proof=self._authority(value,available=available)
                quality=_valid_quality(assess_quality(value,row['full_text'],original_available=available,authority_verified=proof['status']=='verified',pages=json.loads(row['pages'])))
                counts['total']+=1;counts[quality['state']]+=1;counts['changed']+=value.get('quality')!=quality
                value={**value,'authority_evidence':proof,'quality':quality}
                connection.execute('UPDATE knowledge_entries SET quality=?,payload=? WHERE id=?',(quality['state'],json.dumps(value,ensure_ascii=False),row['id']))
                self._index(connection,row['id'],value,row['full_text'])
            # A global reassessment can move entries in or out of the eligible set, so
            # the derived vector index must be marked stale even when nothing changed.
            from knowledge_vector_schema import dirty
            dirty(connection)
        return counts

    def import_reports(self,research_db_path):
        counts={'imported':0,'filtered':0,'skipped':0,'tasks':0}
        path=Path(research_db_path).resolve()
        if not path.is_file():return counts
        if not path.is_relative_to(self.data_dir):raise KnowledgeError('invalid_knowledge','研究记录不属于当前项目。')
        try:
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as connection:
                for identifier,payload in connection.execute('SELECT id,payload FROM research_tasks'):
                    counts['tasks']+=1
                    try:value=json.loads(payload);sources=value.get('report',{}).get('evidence',[])
                    except (ValueError,AttributeError):counts['skipped']+=1;continue
                    if not isinstance(sources,list):counts['skipped']+=1;continue
                    for source in sources[:1000]:
                        try:
                            saved=self.save_source(source,research_id=identifier)
                            counts['imported' if saved.get('saved') else 'filtered']+=1
                        except KnowledgeError:counts['skipped']+=1
        except sqlite3.Error:raise KnowledgeError('knowledge_import_unavailable','研究资料暂时无法导入，原记录未修改。',503) from None
        return counts
