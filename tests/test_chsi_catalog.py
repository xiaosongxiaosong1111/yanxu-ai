import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from collectors.chsi_catalog import (PublicCatalogError, PublicAccessStop, HostGate,
    visible_gate, collect_unit, collect_catalogs, fetch_public, initialize_manifest, main, _unit_form)


UNIT={'id':'123','name':'样本研究所','unit_code':'10001','annual_status':'confirmed','annual_year':2027}
CONTEXT='<title>硕士专业目录</title><script>login: !!null, curYear: "2027";</script>'
CSS_URL='https://t2.chei.com.cn/yz/zsml/assets/pc/css/app-1.0.1.css'
SCHOOL='<title>硕士专业目录</title><script>login: !!null, curYear: "2027"; cxvo:{dwdm:"10001",dwmc:"样本研究所",schId:"123"};</script><a @click="getListDetail(item)">展开</a><template v-if="form.totalPage > 1 && list.length > 0 && login">下一页</template><div class="need-login" v-if="!login && form.totalPage > 1">登录后，可查看更多内容</div>'
ROW={'schId':'123','dwdm':'10001','dwmc':'样本研究所','szss':'北京','szssm':'11','sign':'a'*32,'zhx':'0','syl':'0','bs':'1'}
PROGRAM={**ROW,'zydm':'070801','zymc':'固体地球物理学','mxxfs':'','mdwlxs':None,'mtydxs':'','mjsggjh':'','mjsxbjh':''}
DIRECTION={**PROGRAM,'id':'b'*32,'ksfsdm':'21','ksfsmc':'统考','xxfs':'1','yxsdm':'001','yxsmc':'地球物理研究室','yjfxdm':'01','yjfxmc':'地震预测'}


def payload(rows,total=None):
    total=len(rows) if total is None else total
    return {'flag':True,'msg':{'curPage':1,'pageCount':10,'totalCount':total,'totalPage':max(1,(total+9)//10),'list':rows}}


def response(url,value,kind=None):
    raw=value.encode() if isinstance(value,str) else json.dumps(value,ensure_ascii=False).encode()
    return {'url':url,'raw':raw,'content_type':kind or ('text/html;charset=utf-8' if isinstance(value,str) else 'application/json;charset=utf-8'),'status':200}


class PublicCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.dns=patch('collectors.chsi_catalog.public_target',side_effect=AssertionError('TEST NETWORK DENIED: DNS')).start()
        self.socket_dns=patch('socket.getaddrinfo',side_effect=AssertionError('TEST NETWORK DENIED: socket DNS')).start()
        self.socket_connect=patch('socket.create_connection',side_effect=AssertionError('TEST NETWORK DENIED: socket connection')).start()
        self.connection=patch('collectors.chsi_catalog._PinnedConnection',side_effect=AssertionError('TEST NETWORK DENIED: public transport')).start()
        self.addCleanup(patch.stopall)
        self.proof_verifier=Mock(return_value={"verified":True})

    def fetcher(self,*,programs=None,directions=None,failure=None):
        calls=[]
        def fetch(url,method='GET',body=None):
            calls.append((url,method,body))
            path=urlsplit(url).path
            if failure and path==failure[0]:return response(url,failure[1])
            if path=='/zsml/dw.do':return response(url,CONTEXT)
            if path=='/zsml/rs/dws.do':return response(url,payload([ROW]))
            if path=='/zsml/dwzy.do':return response(url,SCHOOL)
            if url==CSS_URL:return response(url,'.need-login{height:80px;bottom:20px}.need-login-small{height:46px;bottom:12px}','text/css')
            if path=='/zsml/rs/dwzys.do':return response(url,payload(programs if programs is not None else [PROGRAM]))
            if path=='/zsml/rs/yjfxs.do':return response(url,payload(directions if directions is not None else [DIRECTION]))
            if path=='/zsml/yjfxdetail':return response(url,'<h1>样本研究所2027年硕士目录</h1>')
            raise AssertionError('unexpected public route '+url)
        return fetch,calls

    def parser(self,html,unit,year,document,*,context_html):
        self.assertEqual(year,2027);self.assertIn('样本研究所',context_html)
        return {'programs':[{'id':'entry','unit_id':unit['id'],'year':year,'exam_method':'统考','study_mode':'全日制'}],'issues':[],'excluded_records':[]}

    def test_visible_gate_never_treats_preloaded_hidden_rows_as_permission(self):
        gate=visible_gate(payload([PROGRAM]*10,91),SCHOOL,'programs')
        self.assertEqual(gate['allowed_indexes'],[0]);self.assertFalse(gate['complete'])
        gate=visible_gate(payload([DIRECTION]*8,8),SCHOOL,'directions')
        self.assertEqual(gate['allowed_indexes'],[0]);self.assertFalse(gate['complete'])
        self.assertTrue(visible_gate(payload([DIRECTION]),SCHOOL,'directions')['complete'])
        with self.assertRaises(PublicAccessStop):visible_gate({'flag':False,'msg':'请登录'},SCHOOL,'directions')

    def test_one_public_program_one_direction_stores_exact_chain_and_only_get_leaf_entries(self):
        fetch,calls=self.fetcher()
        row=collect_unit(UNIT,self.root,fetcher=fetch,parser=self.parser,verifier=self.proof_verifier)
        self.assertEqual(row['status'],'enumerated');self.assertTrue(row['enumeration_complete']);self.assertFalse(row['catalog_complete'])
        self.assertEqual(len(row['entries']),1);entry=row['entries'][0]
        doc=next(doc for doc in row['documents'] if doc['id']==entry['document_id'])
        self.assertEqual(doc['method'],'GET');self.assertEqual(doc['catalog_parser'],'chsi_detail_v1')
        self.assertEqual(doc['public_gate_proof']['direction_index'],0);self.assertEqual(entry['program']['unit_id'],'123')
        for original in row['documents']:
            raw=(self.root/original['local_path']).read_bytes();self.assertEqual(hashlib.sha256(raw).hexdigest(),original['sha256'])
        self.assertTrue(any(method=='POST' and '/rs/yjfxs.do' in url for url,method,body in calls))

    def test_large_first_page_is_partial_and_no_hidden_second_program_or_direction_is_requested(self):
        fetch,calls=self.fetcher(programs=[PROGRAM,{**PROGRAM,'zydm':'070802'}],directions=[DIRECTION,{**DIRECTION,'id':'c'*32}])
        row=collect_unit(UNIT,self.root,fetcher=fetch,parser=self.parser,verifier=self.proof_verifier)
        self.assertEqual(row['status'],'partial');self.assertFalse(row['enumeration_complete'])
        self.assertEqual(sum('/rs/yjfxs.do' in url for url,method,body in calls),1)
        self.assertEqual(sum('/yjfxdetail?' in url for url,method,body in calls),1)
        self.assertEqual(row['visibility']['directions'][0]['unconfirmed_count'],1)

    def test_single_exam_and_unknown_study_mode_never_become_unified_entries(self):
        for overrides in [{'ksfsdm':'23','ksfsmc':'单考'},{'ksfsdm':'21','ksfsmc':'单考'},{'xxfs':'0'}]:
            fetch,calls=self.fetcher(directions=[{**DIRECTION,**overrides}])
            row=collect_unit(UNIT,self.root,fetcher=fetch,parser=self.parser,verifier=self.proof_verifier)
            self.assertEqual(row['entries'],[])
            self.assertFalse(any('/yjfxdetail?' in url for url,method,body in calls))

    def test_wrong_year_unit_identity_and_tampered_cache_fail_closed(self):
        fetch,calls=self.fetcher(failure=('/zsml/dw.do',CONTEXT.replace('2027','2026')))
        row=collect_unit(UNIT,self.root,fetcher=fetch,parser=self.parser,verifier=self.proof_verifier)
        self.assertEqual(row['status'],'failed');self.assertEqual(len(calls),1)
        fetch,calls=self.fetcher(failure=('/zsml/rs/dws.do',payload([{**ROW,'schId':'999'}])))
        self.assertEqual(collect_unit(UNIT,self.root,fetcher=fetch,parser=self.parser,verifier=self.proof_verifier)['status'],'failed')

    def test_rate_limit_persists_a_global_stop_and_preserves_other_units_as_pending(self):
        fetch,calls=self.fetcher(failure=('/zsml/rs/yjfxs.do',{'flag':False,'msg':'访问太频繁'}))
        units=[UNIT,{**UNIT,'id':'456','unit_code':'10002','name':'另一研究所'}]
        manifest=collect_catalogs(units,self.root,fetcher=fetch,parser=self.parser,verifier=self.proof_verifier)
        self.assertEqual(manifest['metadata']['state'],'stopped');self.assertEqual(manifest['units'][0]['status'],'restricted');self.assertEqual(manifest['units'][1]['status'],'pending')
        count=len(calls);again=collect_catalogs(units,self.root,fetcher=fetch,parser=self.parser,verifier=self.proof_verifier)
        self.assertEqual(len(calls),count);self.assertEqual(again['metadata']['stop_reason']['code'],'rate_limited')
        self.assertEqual(again['units'][0]['status'],'restricted');self.assertTrue(again['units'][0]['documents'])
        self.assertTrue(HostGate(self.root).stopped)

    def test_offline_cache_replay_never_networks_and_revalidates_all_original_hashes(self):
        fetch,calls=self.fetcher();original=collect_unit(UNIT,self.root,fetcher=fetch,parser=self.parser,verifier=self.proof_verifier)
        blocked=Mock(side_effect=AssertionError('offline cannot network'))
        replay=collect_unit(UNIT,self.root,previous=original,offline=True,fetcher=blocked,parser=self.parser,verifier=self.proof_verifier)
        self.assertEqual(len(replay['entries']),1);blocked.assert_not_called()
        path=self.root/original['documents'][0]['local_path'];path.write_text('tampered','utf-8')
        self.assertEqual(collect_unit(UNIT,self.root,previous=original,offline=True,fetcher=blocked,parser=self.parser,verifier=self.proof_verifier)['status'],'failed')

    def test_initialization_creates_every_annual_unit_without_claiming_catalogs_are_complete(self):
        manifest=initialize_manifest([UNIT],2027)
        self.assertEqual(manifest['units'][0]['status'],'pending');self.assertEqual(manifest['metadata']['total_units'],1)
        self.assertFalse(manifest['metadata']['catalog_complete'])

    def test_http_transport_rejects_wrong_host_and_never_changes_public_pagination(self):
        for url in ['http://yz.chsi.com.cn/zsml/dw.do','https://evil.com/zsml/dw.do','https://yz.chsi.com.cn/zsml/a/dw.do']:
            with self.assertRaises(PublicCatalogError):fetch_public(url)
        with self.assertRaises(PublicCatalogError):fetch_public('https://yz.chsi.com.cn/zsml/rs/dwzys.do',method='POST',body=b'curPage=2')
        with self.assertRaises(PublicCatalogError):fetch_public('https://yz.chsi.com.cn/zsml/dwzy.do?start=10')
        with self.assertRaises(PublicCatalogError):fetch_public('https://yz.chsi.com.cn/zsml/rs/dwzys.do',method='POST',body=b'curPage=1&start=0&pageSize=20&totalPage=0&totalCount=0&hidden=override')

    def test_html_login_stops_the_host_instead_of_becoming_a_wrong_year_retry(self):
        fetch,calls=self.fetcher(failure=('/zsml/dw.do','<title>登录</title>'))
        row=collect_unit(UNIT,self.root,fetcher=fetch,parser=self.parser,verifier=self.proof_verifier)
        self.assertEqual(row['status'],'restricted');self.assertTrue(HostGate(self.root).stopped)

    def test_network_is_disabled_by_default_and_shared_root_host_gate_precedes_dns(self):
        transport=Mock(side_effect=AssertionError('should never reach transport'))
        with self.assertRaises(PublicCatalogError) as error:
            fetch_public('https://yz.chsi.com.cn/zsml/dw.do',transport=transport)
        self.assertEqual(error.exception.code,'network_disabled')
        HostGate(self.root).stop(PublicAccessStop('rate_limited','访问太频繁'))
        with self.assertRaises(PublicAccessStop) as error:
            fetch_public('https://yz.chsi.com.cn/zsml/dw.do',transport=transport,allow_network=True,host_gate=HostGate(self.root))
        self.assertEqual(error.exception.code,'rate_limited')
        self.dns.assert_not_called();self.socket_dns.assert_not_called();self.socket_connect.assert_not_called();self.connection.assert_not_called();transport.assert_not_called()
        self.assertTrue((self.root/'data/runtime/chsi-host-gate.json').exists())
        self.assertFalse((self.root/'data/runtime/chsi-public-host-gate.json').exists())

    def test_empty_bad_and_forged_get_queries_are_rejected_before_any_dns_or_transport(self):
        gate=HostGate(self.root);gate.path.parent.mkdir(parents=True);gate.path.write_text(json.dumps({'host':'yz.chsi.com.cn','status':'allowed'}),'utf-8')
        transport=Mock(side_effect=AssertionError('should never reach transport'))
        for url in ['', 'https://yz.chsi.com.cn/zsml/dwzy.do?start=10', 'https://yz.chsi.com.cn/zsml/dw.do?curPage=2', 'https://yz.chsi.com.cn/zsml/yjfxdetail?id=bad']:
            with self.subTest(url=url):
                with self.assertRaises(PublicCatalogError):
                    fetch_public(url,allow_network=True,host_gate=gate,transport=transport)
        self.dns.assert_not_called();self.socket_connect.assert_not_called();transport.assert_not_called()

    def allow_fake_transport(self):
        gate=HostGate(self.root);gate.path.parent.mkdir(parents=True,exist_ok=True)
        gate.path.write_text(json.dumps({'host':'yz.chsi.com.cn','status':'allowed'}),'utf-8')
        self.dns.side_effect=None;self.dns.return_value=('yz.chsi.com.cn',443,('93.184.216.34',))
        return gate

    def test_explicit_fake_transport_reads_exact_public_post_body_without_any_socket(self):
        gate=self.allow_fake_transport();raw=b'{"flag":true,"msg":{}}'
        class Response(io.BytesIO):
            status=200
            def getheader(self,name,default=''):
                return {'Content-Type':'application/json','Content-Length':str(len(raw))}.get(name,default)
        transport=Mock(return_value=Response(raw));body=__import__('urllib.parse',fromlist=['urlencode']).urlencode(_unit_form(UNIT)).encode()
        result=fetch_public('https://yz.chsi.com.cn/zsml/rs/dws.do',method='POST',body=body,allow_network=True,host_gate=gate,transport=transport)
        self.assertEqual(result['raw'],raw);self.assertEqual(transport.call_args.args[3:5],('POST',body))
        self.socket_connect.assert_not_called();self.connection.assert_not_called()

    def test_fake_http_restrictions_incomplete_bodies_and_wrong_types_fail_without_retry(self):
        gate=self.allow_fake_transport()
        class Response(io.BytesIO):
            def __init__(self,body,status=200,**headers):
                super().__init__(body);self.status=status;self.headers={'Content-Type':'text/html',**headers}
            def getheader(self,name,default=''):return self.headers.get(name,default)
        cases=[Response(b'login',429),Response(b'login',302),Response(b'no',500),Response(b'bad',**{'Content-Type':'application/octet-stream'}),Response(b'partial',**{'Content-Length':'20'}),Response(b''),Response(b'large',**{'Content-Length':str(5*1024*1024)})]
        for original in cases:
            transport=Mock(return_value=original)
            with self.assertRaises(PublicCatalogError):
                fetch_public('https://yz.chsi.com.cn/zsml/dw.do',allow_network=True,host_gate=gate,transport=transport)
            self.assertTrue(original.closed);self.assertEqual(transport.call_count,1)
        self.socket_connect.assert_not_called()

    def test_cli_initialization_and_default_disabled_collection_never_network(self):
        data=self.root/'data';data.mkdir()
        (data/'units.json').write_text(json.dumps({'units':[UNIT]}),'utf-8')
        (data/'annual_registry_2027.json').write_text(json.dumps({'metadata':{'complete':True,'year':2027,'official_total':1},'units':[{'unit_id':'123','annual_status':'confirmed'}]}),'utf-8')
        self.assertEqual(main(['--root',str(self.root),'--initialize-only']),0)
        self.assertEqual(main(['--root',str(self.root)]),0)
        saved=json.loads((data/'chsi_public_catalog_2027.json').read_text('utf-8'))
        self.assertEqual(saved['metadata']['state'],'network_disabled');self.assertEqual(saved['units'][0]['status'],'pending')
        self.dns.assert_not_called();self.socket_connect.assert_not_called()

    def test_parser_output_is_not_adopted_when_independent_public_proof_fails(self):
        self.proof_verifier.return_value={'verified':False,'reason':'source hash changed'}
        fetch,calls=self.fetcher();row=collect_unit(UNIT,self.root,fetcher=fetch,parser=self.parser,verifier=self.proof_verifier)
        self.assertEqual(row['entries'],[]);self.assertEqual(row['status'],'partial')
        self.assertEqual(row['errors'][0]['code'],'public_proof_failed')
        self.assertEqual(row['documents'][-1]['status'],'needs_review')

    def test_actual_saved_first_public_entry_replays_without_dns_or_network(self):
        root=Path(__file__).resolve().parents[1];path=root/'data/runtime/chsi-public-first-entry.json'
        if not path.exists():self.skipTest('actual local UI probe not packaged in this checkout')
        from collectors.chsi_public_proof import verify_public_detail
        from collectors.catalog_chsi import parse_chsi_detail_html
        entry=json.loads(path.read_text('utf-8'));documents=entry.get('documents',[])
        document=next(doc for doc in documents if doc.get('catalog_parser')=='chsi_detail_v1')
        unit={'id':'367878','unit_code':'10001','name':'北京大学'}
        checked=verify_public_detail(document,unit,2027,project_root=root)
        self.assertTrue(checked['verified'],checked.get('reason'))
        raw=(root/document['local_path']).read_bytes()
        result=parse_chsi_detail_html(raw.decode('utf-8'),unit,2027,document,context_html=checked['context_html'])
        self.assertEqual(len(result['programs']),1);self.assertEqual(len(result['programs'][0]['subjects']),7)
        self.dns.assert_not_called();self.socket_connect.assert_not_called()
