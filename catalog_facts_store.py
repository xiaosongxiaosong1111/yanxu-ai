"""Read separately audited original-table facts without making admissions candidates."""
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import threading

from collectors.catalog_facts import audit_catalog_facts


NOTICE='以下是已保存官方原文的表格行，目录信息仍待完善；未知学习方式不作推断，不参与报考匹配。'
PUBLIC_FIELDS=('fact_id','unit_id','unit_name','year','code','name','department','direction','study_mode',
    'admission_method','notes','subjects','exam_groups','field_evidence','row_evidence','source_headers',
    'source_document_id','source_sha256','source_locator','missing_fields','matching_allowed','fact_status','notice')


class CatalogFactsError(Exception):
    def __init__(self,code,message,status=400):
        super().__init__(message);self.code,self.message,self.status=code,message,status


def _json(raw):
    return json.loads(raw.decode('utf-8-sig'),parse_constant=lambda value:(_ for _ in ()).throw(ValueError('nonfinite')))


def _bounded_bytes(path):
    chunks=[];size=0;limit=32*1024*1024
    with path.open('rb') as stream:
        while chunk:=stream.read(min(65536,limit+1-size)):
            size+=len(chunk)
            if size>limit:raise ValueError('snapshot size')
            chunks=[*chunks,chunk]
    if not size:raise ValueError('snapshot size')
    return b''.join(chunks)


def _public(value):
    if isinstance(value,dict):return {key:_public(item) for key,item in value.items() if not key.endswith('_path')}
    if isinstance(value,list):return [_public(item) for item in value]
    return value


class CatalogFactsStore:
    def __init__(self,data_dir):
        self.data_dir=Path(data_dir).resolve();self.project=self.data_dir.parent
        self._audits={};self._lock=threading.Lock()

    def _dataset(self):
        try:
            raw=_bounded_bytes(self.data_dir/'units.json')
            registry=_json(raw);rows=registry.get('units')
            if not isinstance(rows,list) or any(not isinstance(row,dict) or not isinstance(row.get('id'),str) for row in rows):
                raise ValueError('registry shape')
            units={unit['id']:unit for unit in rows}
            if len(units)!=len(rows):raise ValueError('duplicate unit')
            path=self.data_dir/'catalog_facts.json'
            if not path.is_file():return units,{'metadata':{},'facts':[],'documents':[],'dom_documents':[]}
            raw=_bounded_bytes(path)
            value=_json(raw)
            if (not isinstance(value,dict) or not isinstance(value.get('metadata'),dict)
                    or any(not isinstance(value.get(key),list) or any(not isinstance(row,dict) for row in value[key])
                           for key in ('facts','documents','dom_documents'))):
                raise ValueError('facts shape')
            for collection,keys in (('documents',('id','unit_id')),('dom_documents',('source_document_id',)),
                                    ('facts',('source_document_id','unit_id'))):
                if any(not isinstance(row.get(key),str) or not row[key] for row in value[collection] for key in keys):
                    raise ValueError('source identity shape')
            return units,value
        except (OSError,ValueError,TypeError,UnicodeError,AttributeError) as error:
            raise CatalogFactsError('data_unavailable','目录原文资料暂时无法读取，请稍后重试。',503) from error

    def _original(self,document):
        path=document.get('local_path');digest=document.get('sha256')
        if not isinstance(path,str) or '\x00' in path or not isinstance(digest,str) or not re.fullmatch(r'[a-f0-9]{64}',digest):return None
        try:
            target=(self.project/path).resolve()
            if not target.is_relative_to(self.data_dir/'snapshots'):return None
            if not target.is_file() or not 0<target.stat().st_size<=32*1024*1024:return None
            raw=_bounded_bytes(target)
            return raw if hashlib.sha256(raw).hexdigest()==digest else None
        except (OSError,ValueError,RuntimeError):return None

    def _source_facts(self,unit,year,document,dom_document,facts):
        raw=self._original(document);dom_raw=self._original(dom_document)
        if (raw is None or dom_raw is None or document.get('unit_id')!=unit['id'] or document.get('year')!=year
                or dom_document.get('source_document_id')!=document.get('id')
                or dom_document.get('source_sha256')!=document.get('sha256')):return []
        fingerprint=hashlib.sha256(json.dumps([unit,year,document,dom_document,facts],ensure_ascii=False,
            sort_keys=True,separators=(',',':'),allow_nan=False).encode()+raw+dom_raw).hexdigest()
        with self._lock:
            if fingerprint in self._audits:return deepcopy(self._audits[fingerprint])
        try:
            snapshot=_json(dom_raw)
            candidates=[fact for fact in facts if fact.get('fact_status')=='verified_original_fact'
                and isinstance(fact.get('independent_audit'),dict)
                and fact['independent_audit'].get('source_sha256')==document['sha256']
                and fact['independent_audit'].get('dom_sha256')==dom_document['sha256']]
            audit=audit_catalog_facts(raw,unit,year,document,candidates,snapshot)
            invalid=set(audit.get('invalid_fact_ids',[]))
            accepted=(candidates if audit['verified'] else [row for row in candidates if row.get('fact_id') not in invalid]
                      if invalid else [])
            if accepted and not audit['verified'] and not audit_catalog_facts(raw,unit,year,document,accepted,snapshot)['verified']:accepted=[]
            result=[_public({**{key:fact[key] for key in PUBLIC_FIELDS if key in fact},
                'matching_allowed':False,'fact_status':'verified_original_fact','notice':NOTICE}) for fact in accepted]
        except (ValueError,TypeError,KeyError,AttributeError,UnicodeError):result=[]
        with self._lock:self._audits={**(self._audits if len(self._audits)<64 else {}),fingerprint:result}
        return deepcopy(result)

    def _rows(self,units,payload,year,unit_id=None):
        documents=payload['documents'];dom_documents=payload['dom_documents']
        doc_counts=Counter(row.get('id') for row in documents)
        dom_counts=Counter(row.get('source_document_id') for row in dom_documents)
        dom_by_id={row.get('source_document_id'):row for row in dom_documents}
        facts=payload['facts'];result=[]
        if payload['metadata'].get('matching_allowed',False) is not False:return []
        for document in documents:
            identifier=document.get('id');unit=units.get(document.get('unit_id'))
            if (not isinstance(identifier,str) or unit is None or (unit_id is not None and unit['id']!=unit_id)
                    or doc_counts[identifier]!=1 or dom_counts[identifier]!=1):continue
            selected=[row for row in facts if row.get('source_document_id')==identifier]
            result=[*result,*self._source_facts(unit,year,document,dom_by_id[identifier],selected)]
        counts=Counter(row['fact_id'] for row in result)
        return [row for row in result if counts[row['fact_id']]==1]

    def counts(self,year=2027):
        units,payload=self._dataset()
        return dict(Counter(row['unit_id'] for row in self._rows(units,payload,year)))

    def query(self,unit_id=None,year=None,q='',offset=0,limit=10):
        if (not isinstance(unit_id,str) or not re.fullmatch(r'\d{1,32}',unit_id)
                or not isinstance(q,str) or len(q)>120 or any(ord(char)<32 for char in q)
                or type(offset) is not int or not 0<=offset<=1000000
                or type(limit) is not int or not 1<=limit<=30):
            raise CatalogFactsError('invalid_query','请选择一个招生单位，并使用正确的查询与分页参数。')
        if year is not None and (type(year) is not int or year!=2027):
            raise CatalogFactsError('unsupported_year','当前原文表格行只提供2027年历史参考资料。')
        year=2027 if year is None else year
        units,payload=self._dataset();unit=units.get(unit_id)
        if unit is None:raise CatalogFactsError('unknown_unit','该招生单位不在当前索引中。',404)
        rows=self._rows(units,payload,year,unit_id)
        needle=q.strip().casefold()
        selected=[row for row in rows if needle in ' '.join(str(row.get(key) or '')
            for key in ('code','name','department','direction','notes')).casefold()
            or any(needle in (subject['code']+' '+subject['name']).casefold() for subject in row['subjects'])]
        recorded=any(row.get('unit_id')==unit_id for row in payload['facts'])
        return {'unit_id':unit_id,'unit_name':unit['name'],'year':year,'facts':deepcopy(selected[offset:offset+limit]),
            'total':len(selected),'offset':offset,'limit':limit,'has_more':offset+limit<len(selected),
            'matching_allowed':False,'catalog_complete':False,'count_unit':'original_table_rows',
            'status':'available' if rows else 'source_needs_review' if recorded else 'not_collected','notice':NOTICE}
