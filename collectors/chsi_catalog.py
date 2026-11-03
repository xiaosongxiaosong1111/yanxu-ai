"""Conservative public CHSI originals. No login, paging forgery or admissions writes."""
import argparse
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime, timezone
import hashlib
import http.client
import json
from pathlib import Path
import re
import socket
import threading
import time
import unicodedata
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from public_sources import _DeadlineGuard, _DNS_POOL
from .discovery import public_target, _PinnedConnection, _Response, DiscoveryError, USER_AGENT
from .documents import PROJECT_ROOT, decode_text


BASE='https://yz.chsi.com.cn'
CSS_URL='https://t2.chei.com.cn/yz/zsml/assets/pc/css/app-1.0.1.css'
GET_PATHS={'/zsml/dw.do','/zsml/dwzy.do','/zsml/yjfxdetail'}
POST_PATHS={'/zsml/rs/dws.do','/zsml/rs/dwzys.do','/zsml/rs/yjfxs.do'}
MAX_BYTES=4*1024*1024


class PublicCatalogError(ValueError):
    def __init__(self,code,message):
        super().__init__(message)
        self.code=code


class PublicAccessStop(PublicCatalogError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def atomic_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    stage=path.with_suffix(path.suffix+'.tmp')
    stage.write_text(json.dumps(value,ensure_ascii=False,indent=2),'utf-8');stage.replace(path)


class HostGate:
    """Persistent global stop; there is deliberately no automatic retry/reset."""
    def __init__(self,root,minimum_interval=1.0):
        self.path=Path(root)/'data/runtime/chsi-host-gate.json'
        self.minimum_interval=minimum_interval;self.last=0;self.lock=threading.Lock()

    @property
    def reason(self):
        if not self.path.exists():return None
        try:
            value=json.loads(self.path.read_text('utf-8'))
            status=value.get('status')
            return None if status=='allowed' else {'code':status or 'invalid_host_gate','message':str(value.get('reason','公开来源尚未获准恢复。'))}
        except (OSError,ValueError):
            return {'code':'invalid_host_gate','message':'公开来源全局停止状态无法核验。'}

    @property
    def stopped(self):
        return self.reason is not None

    @property
    def allows_network(self):
        return self.path.exists() and self.reason is None

    def stop(self,error,document_id=None):
        atomic_json(self.path,{'status':error.code,'observed_at':now(),'host':'yz.chsi.com.cn',
            'reason':str(error),'document_id':document_id,
            'notice':'遇访问频繁、登录或验证后停止；不自动重试或更换身份，恢复需明确的后续操作。'})

    def before_request(self):
        with self.lock:
            reason=self.reason
            if reason:raise PublicAccessStop(reason['code'],reason['message'])
            delay=self.last+self.minimum_interval-time.monotonic()
            if delay>0:time.sleep(delay)
            self.last=time.monotonic()


def _validate_request(url,method,body):
    try:
        parts=urlsplit(url)
        css=url==CSS_URL and method=='GET' and body is None
        if (parts.scheme!='https' or parts.hostname!='yz.chsi.com.cn' and not css or parts.username or parts.password
                or parts.port not in {None,443} or parts.fragment or len(url)>4096):
            raise ValueError('wrong origin')
    except (ValueError,TypeError) as error:
        raise PublicCatalogError('unsafe_request','仅允许研招网普通公开目录入口。') from error
    if css:return parts
    if method=='GET' and parts.path in GET_PATHS and body is None:
        query=parse_qs(parts.query,keep_blank_values=True)
        if parts.path.endswith('/dw.do') and query or parts.path.endswith('/yjfxdetail') and (
                set(query)!={'id'} or len(query['id'])!=1 or not re.fullmatch(r'[a-f0-9]{32}',query['id'][0])):
            raise PublicCatalogError('unsafe_request','公开原文链接参数不合法。')
        if parts.path.endswith('/dwzy.do'):
            required={'dwdm','dwmc','ssmc','schId','xxfs','mldm','tydxs','jsggjh','jsxbjh','sign'}
            optional={'dwlxs[0]','dwlxs[1]','dwlxs[2]'}
            if (not required.issubset(query) or not set(query)<=required|optional
                    or any(len(value)!=1 for value in query.values())
                    or not re.fullmatch(r'\d{5}',query['dwdm'][0]) or not query['schId'][0].isdigit()
                    or not re.fullmatch(r'[a-f0-9]{32}',query['sign'][0])
                    or not query['dwmc'][0] or query['xxfs'][0] not in {'','1','2'}
                    or any(query[key][0] not in {'zhx','syl','bs'} for key in optional&set(query))):
                raise PublicCatalogError('unsafe_request','单位详情只采用正常公开首屏原始签名链接，不接受分页或隐藏参数。')
        return parts
    if method!='POST' or parts.path not in POST_PATHS or parts.query or not isinstance(body,bytes):
        raise PublicCatalogError('unsafe_request','不请求登录入口或未声明的目录接口。')
    fields=parse_qs(body.decode('ascii'),keep_blank_values=True)
    common={'dwdm','dwmc','ssdm','xxfs','dwlxs[0]','tydxs','jsggjh','jsxbjh','start','curPage','pageSize','totalPage','totalCount'}
    allowed=(common if parts.path.endswith('/dws.do') else
        {'zydm','zymc','dwdm','xxfs','dwlxs','tydxs','jsggjh','jsxbjh','start','pageSize','totalCount'} if parts.path.endswith('/yjfxs.do') else
        {'dwdm','dwmc','zydm','zymc','xwlx','mldm','yjxkdm','xxfs','tydxs','jsggjh','jsxbjh','start','curPage','pageSize','totalPage','totalCount'})
    if set(fields)!=allowed or any(len(value)!=1 for value in fields.values()):
        raise PublicCatalogError('unsafe_request','公开查询不接受额外隐藏参数或重复字段。')
    expected={'start':'0','pageSize':'3' if parts.path.endswith('/yjfxs.do') else '20','totalCount':'0'}
    if not parts.path.endswith('/yjfxs.do'):expected={**expected,'curPage':'1','totalPage':'0'}
    if any(fields.get(key)!=[value] for key,value in expected.items()):
        raise PublicCatalogError('forbidden_pagination','仅允许页面本身普通公开首屏请求，不能伪造分页。')
    return parts


def fetch_public(url,method='GET',body=None,*,timeout=15,resolver=socket.getaddrinfo,transport=None,allow_network=False,host_gate=None):
    """One fixed-host public request with DNS pinning and a total deadline; no retries."""
    parts=_validate_request(url,method,body)
    if allow_network is not True:raise PublicCatalogError('network_disabled','公开接口网络默认关闭；离线处理不发任何请求。')
    host_gate=host_gate or HostGate(PROJECT_ROOT)
    if host_gate.stopped:
        reason=host_gate.reason;raise PublicAccessStop(reason['code'],reason['message'])
    if not host_gate.allows_network:raise PublicCatalogError('host_not_authorized','该主机尚未明确恢复公开访问。')
    if not 0<timeout<=30:raise PublicCatalogError('invalid_limit','公开原文读取期限无效。')
    deadline=time.monotonic()+timeout
    def remaining():
        left=deadline-time.monotonic()
        if left<=0:raise PublicCatalogError('timeout','公开原文超过总读取期限。')
        return left
    future=_DNS_POOL.submit(public_target,url,resolver)
    try:target=future.result(timeout=remaining())
    except FutureTimeout as error:
        future.cancel();raise PublicCatalogError('timeout','公开来源DNS解析超时。') from error
    guard=_DeadlineGuard(remaining());response=None
    try:
        if transport is not None:response=transport(url,target,remaining(),method,body,guard)
        else:
            host,port,addresses=target
            connection=_PinnedConnection(host,port,addresses[0],remaining(),True);guard.bind(connection)
            request_path=urlunsplit(('','',parts.path,parts.query,''))
            headers={'User-Agent':USER_AGENT,'Accept':'text/html,application/json'}
            if body is not None:headers['Content-Type']='application/x-www-form-urlencoded;charset=UTF-8'
            try:
                connection.request(method,request_path,body=body,headers=headers);guard.bind(connection)
                response=_Response(connection.getresponse(),connection)
            except Exception:
                connection.close();raise
        if response.status in {301,302,303,307,308,401,403,429}:
            raise PublicAccessStop('access_restricted','公开来源要求跳转、登录或访问验证，停止该主机。')
        if response.status!=200:raise PublicCatalogError('http_error',f'公开来源HTTP {response.status}')
        content_type=response.getheader('Content-Type','')
        if not any(value in content_type.lower() for value in ('text/html','application/json','text/css')):
            raise PublicCatalogError('unexpected_type','目录来源不是声明的HTML或JSON。')
        declared=response.getheader('Content-Length','')
        if declared.isdigit() and int(declared)>MAX_BYTES:raise PublicCatalogError('too_large','公开目录原文超过读取上限。')
        chunks=[];total=0
        while True:
            remaining();chunk=response.read(min(65536,MAX_BYTES+1-total))
            if not chunk:break
            chunks.append(chunk);total+=len(chunk)
            if total>MAX_BYTES:raise PublicCatalogError('too_large','公开目录原文超过读取上限。')
        remaining();raw=b''.join(chunks)
        if guard.expired or not raw or declared.isdigit() and total!=int(declared):
            raise PublicCatalogError('incomplete_original','公开原文未完整读取，不能采用。')
        return {'url':url,'raw':raw,'content_type':content_type,'status':response.status}
    finally:
        guard.close()
        if response:response.close()


def _payload(raw):
    value=json.loads(decode_text(raw))
    if not isinstance(value,dict):raise PublicCatalogError('invalid_response','公开接口返回格式无法核验。')
    if value.get('flag') is not True:
        message=str(value.get('msg','公开接口未成功返回。'))
        code='rate_limited' if re.search(r'频繁|次数|繁忙',message) else 'login_required' if re.search(r'登录|登陆',message) else 'verification_required' if re.search(r'验证码|验证',message) else 'public_query_stopped'
        raise PublicAccessStop(code,message)
    msg=value.get('msg')
    if (not isinstance(msg,dict) or not isinstance(msg.get('list'),list) or any(not isinstance(row,dict) for row in msg.get('list',[]))
            or type(msg.get('totalCount')) is not int or msg['totalCount']<len(msg['list'])
            or msg.get('curPage',1)!=1):
        raise PublicCatalogError('invalid_response','公开首屏列表或数量声明不一致。')
    return value


def visible_gate(payload,context_html,layer):
    """Only index zero is confirmed. Preloaded rows are not an access grant."""
    if payload.get('flag') is not True:_payload(json.dumps(payload).encode())
    if layer not in {'programs','directions'} or 'getListDetail' not in context_html or not re.search(r'login\s*:\s*!!null',context_html):
        raise PublicAccessStop('unconfirmed_visible_gate','公开页面的未登录首项访问规则尚未核验。')
    msg=payload['msg'];rows=msg['list'];total=msg['totalCount']
    return {'policy':'first_public_item_only','allowed_indexes':[0] if rows else [],
            'returned_count':len(rows),'official_total':total,'unconfirmed_count':max(0,total-1),
            'complete':len(rows)==total==1,'reason':'仅首项已确认普通公开可达，其余未请求，不以预返回数据绕过登录遮罩。'}


def _identity(row,unit):
    compact=lambda value:re.sub(r'\s+','',unicodedata.normalize('NFKC',str(value)))
    return all(compact(row.get(field,''))==compact(unit.get(key,'')) for field,key in [('schId','id'),('dwdm','unit_code'),('dwmc','name')])


def _year(html,year):
    if re.search(r'<title[^>]*>[^<]*(?:登录|验证码|安全验证|访问太频繁)',html,re.I):
        raise PublicAccessStop('verification_required','公开页面要求登录或验证。')
    if set(re.findall(r'curYear\s*:\s*["\'](20\d{2})["\']',html))!={str(year)} or '硕士专业目录' not in html:
        raise PublicCatalogError('wrong_year','公开目录页面没有独立证明目标招生年度。')


class _Snapshots:
    def __init__(self,root,unit,year,fetcher,gate,previous,offline,checkpoint):
        self.root,self.unit,self.year=Path(root).resolve(),unit,year
        self.fetcher,self.gate,self.offline,self.checkpoint=fetcher,gate,offline,checkpoint
        self.cache={(doc['url'],doc.get('method','GET'),doc.get('request_body','')):doc for doc in (previous or {}).get('documents',[])}
        self.documents=[]

    def read(self,url,method='GET',fields=None):
        body=urlencode(fields).encode('ascii') if fields is not None else None
        _validate_request(url,method,body);key=(url,method,body.decode() if body else '')
        cached=self.cache.get(key);raw=None
        if cached:
            path=(self.root/cached['local_path']).resolve()
            if path.is_relative_to(self.root/'data/snapshots') and path.is_file():
                candidate=path.read_bytes()
                if hashlib.sha256(candidate).hexdigest()==cached.get('sha256'):raw=candidate
        if raw is None:
            if self.offline:raise PublicCatalogError('missing_snapshot','离线原文缺失或hash不一致，未发网络请求。')
            self.gate.before_request();result=self.fetcher(url,method=method,body=body)
            if result.get('url')!=url or not 200<=result.get('status',200)<300:
                raise PublicAccessStop('unexpected_redirect','公开原文地址或响应状态改变，未继续追踪。')
            raw=result['raw'];sha=hashlib.sha256(raw).hexdigest()
            suffix='.json' if 'json' in result.get('content_type','') else '.html'
            request_hash=hashlib.sha256((url+'|'+method+'|'+key[2]).encode()).hexdigest()
            path=self.root/f'data/snapshots/chsi-public/{self.year}/{self.unit["id"]}/{request_hash[:20]}-{sha[:16]}{suffix}'
            path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
            cached={'id':f'chsi-public-{request_hash[:20]}-{sha[:16]}','url':url,'method':method,'request_body':key[2],
                'request_sha256':hashlib.sha256(body or b'').hexdigest(),'sha256':sha,'local_path':path.relative_to(self.root).as_posix(),
                'content_type':result.get('content_type',''),'status':result.get('status',200),'checked_at':now(),'unit_id':self.unit['id'],'year':self.year}
        self.documents.append(cached)
        if self.checkpoint:self.checkpoint(self.documents)
        return cached,raw


def _unit_form(unit):
    return {'dwmc':unit['name'],'dwdm':'','ssdm':'','xxfs':'','dwlxs[0]':'all','tydxs':'','jsggjh':'','jsxbjh':'','start':0,'curPage':1,'pageSize':20,'totalPage':0,'totalCount':0}


def _detail_url(row):
    if not re.fullmatch(r'[a-f0-9]{32}',str(row.get('sign',''))):raise PublicCatalogError('invalid_link','公开单位链接缺少原始签名。')
    fields={'dwdm':row['dwdm'],'dwmc':row['dwmc'],'ssmc':row['szss'],'schId':row['schId'],'xxfs':row.get('mxxfs','') or '',
            'mldm':row.get('mldm','') or ''}
    flags=[key for key in ['zhx','syl','bs'] if row.get(key)=='1']
    fields.update({f'dwlxs[{index}]':flag for index,flag in enumerate(flags)})
    fields.update({'tydxs':row.get('mtydxs','') or '','jsggjh':row.get('mjsggjh','') or '','jsxbjh':row.get('mjsxbjh','') or '','sign':row['sign']})
    return BASE+'/zsml/dwzy.do?'+urlencode(fields)


def collect_unit(unit,root,*,year=2027,fetcher=None,parser=None,verifier=None,previous=None,offline=False,gate=None,checkpoint=None,allow_network=False):
    if not re.fullmatch(r'\d+',str(unit.get('id',''))) or not re.fullmatch(r'\d{5}',str(unit.get('unit_code',''))):
        raise PublicCatalogError('invalid_unit','单位代码或研招网标识不合法。')
    if parser is None:
        from .catalog_chsi import parse_chsi_detail_html
        parser=parse_chsi_detail_html
    if verifier is None:
        from .chsi_public_proof import verify_public_detail
        verifier=verify_public_detail
    gate=gate or HostGate(root,minimum_interval=1.0 if fetcher is None else 0)
    row={'unit_id':unit['id'],'unit_name':unit['name'],'unit_code':unit['unit_code'],'year':year,'status':'crawling',
         'enumeration_complete':False,'catalog_complete':False,'documents':[],'entries':[],'excluded_records':[],'errors':[],'visibility':{'directions':[]},'checked_at':now()}
    requester=fetcher or (lambda url,method='GET',body=None:fetch_public(url,method,body,allow_network=allow_network,host_gate=gate))
    snapshots=_Snapshots(root,unit,year,requester,gate,previous,offline,checkpoint)
    try:
        _,raw=snapshots.read(BASE+'/zsml/dw.do');_year(decode_text(raw),year)
        unit_doc,raw=snapshots.read(BASE+'/zsml/rs/dws.do','POST',_unit_form(unit));units=_payload(raw)['msg']['list']
        matches=[(index,value) for index,value in enumerate(units) if _identity(value,unit)]
        if len(matches)!=1:raise PublicCatalogError('wrong_unit','普通公开单位首屏没有唯一匹配本单位身份。')
        unit_index,identity=matches[0]
        if unit_index!=0:raise PublicAccessStop('unconfirmed_visible_gate','精准单位未处于已确认公开可达的首项，未跟踪潜在遮罩内链接。')
        context_doc,raw=snapshots.read(_detail_url(identity));context=decode_text(raw);_year(context,year)
        rules_doc,_=snapshots.read(CSS_URL)
        fields={'dwdm':identity['dwdm'],'dwmc':identity['dwmc'],'zydm':'','zymc':'','xwlx':'','mldm':'','yjxkdm':'','xxfs':'','tydxs':'','jsggjh':'','jsxbjh':'','start':0,'curPage':1,'pageSize':20,'totalPage':0,'totalCount':0}
        programs_doc,raw=snapshots.read(BASE+'/zsml/rs/dwzys.do','POST',fields);programs=_payload(raw)
        program_gate=visible_gate(programs,context,'programs');row['visibility']['programs']=program_gate
        complete=program_gate['complete']
        for index in program_gate['allowed_indexes']:
            program=programs['msg']['list'][index]
            if not _identity(program,unit):raise PublicCatalogError('wrong_unit','专业首项不属于本单位。')
            fields={'zydm':program['zydm'],'zymc':program['zymc'],'dwdm':identity['dwdm'],'xxfs':program.get('mxxfs','') or '',
                'dwlxs':'','tydxs':program.get('mtydxs','') or '','jsggjh':program.get('mjsggjh','') or '','jsxbjh':program.get('mjsxbjh','') or '',
                'start':0,'pageSize':3,'totalCount':0}
            directions_doc,raw=snapshots.read(BASE+'/zsml/rs/yjfxs.do','POST',fields);directions=_payload(raw)
            direction_gate=visible_gate(directions,context,'directions');row['visibility']['directions'].append(direction_gate);complete=complete and direction_gate['complete']
            for direction_index in direction_gate['allowed_indexes']:
                direction=directions['msg']['list'][direction_index]
                if not _identity(direction,unit) or direction.get('zydm')!=program.get('zydm'):
                    raise PublicCatalogError('wrong_unit','方向首项单位或专业身份不一致。')
                if direction.get('ksfsdm')!='21' or direction.get('ksfsmc')!='统考' or direction.get('xxfs') not in {'1','2'}:
                    row['excluded_records'].append({'id':direction.get('id'),'reason':'仅明确统考21及真实学习方式1/2进入普通统考资料。'});complete=False;continue
                identifier=direction.get('id','')
                if not re.fullmatch(r'[a-f0-9]{32}',identifier):raise PublicCatalogError('invalid_link','首方向详情原始ID不合法。')
                originals=list(snapshots.documents)
                detail_doc,raw=snapshots.read(BASE+'/zsml/yjfxdetail?'+urlencode({'id':identifier}))
                proof={'version':1,'policy':'first_public_item_only','unit_id':unit['id'],'year':year,'unit_index':unit_index,
                    'program_index':index,'direction_index':direction_index,'direction_id':identifier,'context_document_id':context_doc['id'],
                    'units_document_id':unit_doc['id'],'programs_document_id':programs_doc['id'],'directions_document_id':directions_doc['id'],
                    'rules_document_ids':[rules_doc['id']],'originals':originals}
                detail_doc={**detail_doc,'kind':'catalog','catalog_parser':'chsi_detail_v1','context_document_id':context_doc['id'],
                    'context_sha256':context_doc['sha256'],'context_document':context_doc,'public_gate_proof':proof,
                    'http_status':detail_doc.get('status'),'status':'parsed'}
                snapshots.documents[-1]=detail_doc
                parsed=parser(decode_text(raw),unit,year,detail_doc,context_html=context)
                row['errors'].extend({'code':'parse_issue','message':str(issue)} for issue in parsed.get('issues',[]))
                checked=verifier(detail_doc,unit,year,project_root=root)
                if checked.get('verified') is not True:
                    row['errors'].append({'code':'public_proof_failed','message':str(checked.get('reason','公开访问及原文证据链未通过独立重放。'))})
                    snapshots.documents[-1]={**detail_doc,'status':'needs_review'}
                    continue
                row['entries'].extend({'program':entry,'document_id':detail_doc['id'],'public_gate_proof':proof} for entry in parsed.get('programs',[]))
        row={**row,'status':'enumerated' if complete and row['entries'] and not row['errors'] else 'partial',
             'enumeration_complete':bool(complete and row['entries'] and not row['errors'])}
    except PublicAccessStop as error:
        gate.stop(error,snapshots.documents[-1]['id'] if snapshots.documents else None)
        row={**row,'status':'restricted','errors':[*row['errors'],{'code':error.code,'message':str(error)}]}
    except (PublicCatalogError,DiscoveryError,OSError,ValueError,KeyError,http.client.HTTPException) as error:
        row={**row,'status':'failed','errors':[*row['errors'],{'code':getattr(error,'code','invalid_original'),'message':str(error)}]}
    return {**row,'documents':snapshots.documents,'checked_at':now()}


def initialize_manifest(units,year):
    return {'metadata':{'schema_version':1,'year':year,'total_units':len(units),'state':'pending','catalog_complete':False,'checked_at':now()},
            'units':[{'unit_id':unit['id'],'unit_name':unit['name'],'unit_code':unit['unit_code'],'year':year,'status':'pending',
                      'catalog_complete':False,'enumeration_complete':False,'entries':[],'documents':[]} for unit in units]}


def collect_catalogs(units,root,*,year=2027,fetcher=None,parser=None,verifier=None,offline=False,allow_network=False):
    root=Path(root).resolve();path=root/f'data/chsi_public_catalog_{year}.json';gate=HostGate(root,minimum_interval=1.0 if fetcher is None else 0)
    previous=json.loads(path.read_text('utf-8')) if path.exists() else initialize_manifest(units,year)
    old={row['unit_id']:row for row in previous['units']};manifest=initialize_manifest(units,year)
    rows=[{**row,**old[row['unit_id']],'catalog_complete':False} if row['unit_id'] in old and old[row['unit_id']].get('year')==year
          and old[row['unit_id']].get('unit_code')==row['unit_code'] else row for row in manifest['units']]
    manifest={**manifest,'units':rows};atomic_json(path,manifest)
    for index,unit in enumerate(units):
        if gate.stopped and not offline:break
        if fetcher is None and not allow_network and not offline:break
        def checkpoint(documents):
            rows[index]={**rows[index],'status':'crawling','documents':list(documents)}
            atomic_json(path,{**manifest,'units':rows})
        rows[index]=collect_unit(unit,root,year=year,fetcher=fetcher,parser=parser,verifier=verifier,previous=old.get(unit['id']),offline=offline,gate=gate,checkpoint=checkpoint,allow_network=allow_network)
        atomic_json(path,{**manifest,'units':rows})
    state='stopped' if gate.stopped else 'network_disabled' if fetcher is None and not allow_network and not offline else 'finished_public_scope'
    manifest={**manifest,'units':rows,'metadata':{**manifest['metadata'],'state':state,
        'stop_reason':gate.reason,'checked_at':now(),'notice':'仅保存正常公开可访问范围；预返回隐藏项、登录分页与尚未核验的可见范围均未抓取。'}}
    atomic_json(path,manifest);return manifest


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=PROJECT_ROOT);parser.add_argument('--offline',action='store_true')
    parser.add_argument('--initialize-only',action='store_true')
    parser.add_argument('--allow-network',action='store_true',help='Still requires the existing shared host gate to be explicitly allowed.')
    args=parser.parse_args(argv)
    registry=json.loads((args.root/'data/units.json').read_text('utf-8'))
    annual=json.loads((args.root/'data/annual_registry_2027.json').read_text('utf-8'))
    if annual['metadata'].get('complete') is not True or annual['metadata'].get('year')!=2027:
        raise PublicCatalogError('unverified_annual_registry','全国年度招生单位名单尚未核准。')
    confirmed={row['unit_id'] for row in annual['units'] if row['annual_status']=='confirmed'}
    units=[unit for unit in registry['units'] if unit['id'] in confirmed]
    if len(units)!=annual['metadata'].get('official_total'):raise PublicCatalogError('annual_scope_mismatch','年度单位名单未完整对账。')
    if args.initialize_only:
        path=args.root/'data/chsi_public_catalog_2027.json'
        if not path.exists():
            manifest=initialize_manifest(units,2027);gate=HostGate(args.root)
            if gate.stopped:manifest={**manifest,'metadata':{**manifest['metadata'],'state':'stopped','stop_reason':gate.reason}}
            atomic_json(path,manifest)
        return 0
    result=collect_catalogs(units,args.root,offline=args.offline,allow_network=args.allow_network);print(json.dumps(result['metadata'],ensure_ascii=True));return 0


if __name__=='__main__':
    raise SystemExit(main())
