import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
import http.client
import threading
import io
from unittest.mock import patch

from catalog_facts_store import CatalogFactsError, CatalogFactsStore,_bounded_bytes
from collectors.catalog_facts import parse_catalog_facts
from tests.test_catalog_facts import UNIT, EXAM, source, table


class CatalogFactsStoreTests(unittest.TestCase):
    def test_original_reader_uses_small_buffers_and_keeps_the_exact_source_bytes(self):
        class BoundedStream(io.BytesIO):
            def read(self,size=-1):
                if size<0 or size>1024*1024:raise MemoryError('large source buffer')
                return super().read(size)
        raw=b'official source'*10000
        with patch.object(Path,'open',side_effect=lambda *a,**k:BoundedStream(raw)):
            self.assertEqual(_bounded_bytes(Path('source')),raw)

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.project=Path(self.temp.name);self.data=self.project/'data';self.data.mkdir()
        self.write('units.json',{'units':[UNIT,{**UNIT,'id':'456','name':'另一大学'}]})
        self.headers=['专业代码','专业名称','初试科目']
        self.values=[['081200','计算机科学与技术',EXAM],['083500','软件工程',EXAM]]
        self.raw,self.document=source(table(self.values,self.headers))
        self.raw_path=self.data/'snapshots/original.html';self.raw_path.parent.mkdir();self.raw_path.write_bytes(self.raw)
        self.document={**self.document,'local_path':'data/snapshots/original.html','status':'fetched'}
        self.dom={'source_sha256':self.document['sha256'],'auditor':'browser_dom_v1','tables':[[
            [{'text':value,'origin_row':row_number,'column':column+1} for column,value in enumerate(row)]
            for row_number,row in enumerate([self.headers,*self.values],1)]]}
        self.dom_path=self.data/'snapshots/dom.json'
        self.dom_path.write_text(json.dumps(self.dom,ensure_ascii=False),'utf-8')
        self.dom_sha=hashlib.sha256(self.dom_path.read_bytes()).hexdigest()
        parsed=parse_catalog_facts(self.raw,UNIT,2027,self.document)['facts']
        self.facts=[{**f,'fact_status':'verified_original_fact','independent_audit':{
            'source_sha256':self.document['sha256'],'dom_sha256':self.dom_sha}} for f in parsed]
        self.payload={'metadata':{'reference_year':2027,'target_year':2028,'matching_allowed':False,'catalog_complete':False},
            'facts':self.facts,'documents':[self.document],'dom_documents':[{'source_document_id':self.document['id'],
            'source_sha256':self.document['sha256'],'sha256':self.dom_sha,'local_path':'data/snapshots/dom.json'}]}
        self.write('catalog_facts.json',self.payload);self.store=CatalogFactsStore(self.data)

    def write(self,name,payload):
        (self.data/name).write_text(json.dumps(payload,ensure_ascii=False),'utf-8')

    def query(self,**kwargs):return self.store.query(unit_id='123',**kwargs)

    def test_verified_original_rows_preserve_unknown_mode_quotes_and_urls(self):
        result=self.query()
        self.assertEqual(result['total'],2);self.assertEqual(result['year'],2027)
        row=result['facts'][0];self.assertIsNone(row['study_mode']);self.assertFalse(row['matching_allowed'])
        self.assertIn('study_mode',row['missing_fields'])
        self.assertEqual(row['field_evidence']['name'][0]['url'],self.document['url'])
        self.assertEqual(row['field_evidence']['name'][0]['quote'],'计算机科学与技术')
        self.assertNotIn('local_path',json.dumps(result));self.assertNotIn(str(self.project),json.dumps(result))
        self.assertEqual(self.store.counts(),{'123':2})

    def test_pagination_and_search_never_return_other_units_or_all_rows(self):
        result=self.query(offset=1,limit=1)
        self.assertEqual(len(result['facts']),1);self.assertEqual(result['facts'][0]['code'],'083500')
        self.assertFalse(result['has_more']);self.assertEqual(self.query(q='计算机')['total'],1)
        self.assertEqual(self.store.query(unit_id='456')['facts'],[])

    def test_invalid_parameters_and_unknown_unit_have_clear_codes(self):
        for args in [{'unit_id':None},{'unit_id':[]},{'unit_id':'../123'},{'offset':True},{'offset':-1},
                     {'limit':0},{'limit':31},{'year':True},{'q':[]},{'q':'x'*121},{'q':'a\x00b'}]:
            with self.subTest(args=args),self.assertRaises(CatalogFactsError):
                self.store.query(**{'unit_id':'123',**args})
        with self.assertRaises(CatalogFactsError) as caught:self.store.query(unit_id='999')
        self.assertEqual(caught.exception.code,'unknown_unit');self.assertEqual(caught.exception.status,404)
        with self.assertRaises(CatalogFactsError) as caught:self.query(year=2028)
        self.assertEqual(caught.exception.code,'unsupported_year')

    def test_raw_and_dom_tampering_revoke_warm_results_even_with_restored_mtime(self):
        for path in (self.raw_path,self.dom_path):
            with self.subTest(path=path.name):
                original=path.read_bytes();stat=path.stat();self.assertEqual(self.query()['total'],2)
                path.write_bytes(original+b'x');os.utime(path,ns=(stat.st_atime_ns,stat.st_mtime_ns))
                value=self.query();self.assertEqual(value['facts'],[])
                self.assertEqual(self.store.counts(),{});self.assertNotIn(str(path),json.dumps(value))
                path.write_bytes(original)

    def test_fact_fields_are_reaudited_and_invalid_sibling_is_not_published(self):
        self.assertEqual(self.query()['total'],2)
        modified=copy.deepcopy(self.payload);modified['facts'][0]['name']='伪造名称'
        self.write('catalog_facts.json',modified)
        result=self.query();self.assertEqual(result['total'],1);self.assertEqual(result['facts'][0]['code'],'083500')
        for changes in ({'study_mode':'全日制'},{'matching_allowed':True},{'year':2026}):
            payload=copy.deepcopy(self.payload);payload['facts'][0]={**payload['facts'][0],**changes}
            self.write('catalog_facts.json',payload)
            self.assertLess(self.query()['total'],2)

    def test_source_year_foreign_url_and_snapshot_escape_are_rejected(self):
        for changes in ({'year':2026},{'unit_id':'456'},{'url':'https://foreign.edu.cn/catalog'},
                        {'local_path':'../outside.html'},{'sha256':'0'*64}):
            payload=copy.deepcopy(self.payload);payload['documents']=[{**self.document,**changes}]
            self.write('catalog_facts.json',payload);self.assertEqual(self.query()['facts'],[],changes)
        payload=copy.deepcopy(self.payload);payload['dom_documents'][0]['local_path']='data/catalog_facts.json'
        self.write('catalog_facts.json',payload);self.assertEqual(self.query()['facts'],[])

    def test_missing_proof_file_or_duplicate_proof_does_not_authorize_rows(self):
        for changes in ({'dom_documents':[]},{'documents':[self.document,self.document]},
                        {'dom_documents':self.payload['dom_documents']*2}):
            self.write('catalog_facts.json',{**self.payload,**changes});self.assertEqual(self.query()['facts'],[])

    def test_returned_rows_are_defensive_copies_and_self_reported_counts_are_ignored(self):
        self.write('catalog_facts.json',{**self.payload,'metadata':{**self.payload['metadata'],'fact_count':99999}})
        result=self.query();result['facts'][0]['name']='外部改写'
        self.assertEqual(self.query()['facts'][0]['name'],'计算机科学与技术')
        self.assertEqual(self.store.counts(),{'123':2})

    def test_absent_dataset_is_clear_and_malformed_dataset_does_not_leak_paths(self):
        path=self.data/'catalog_facts.json';path.unlink()
        self.assertEqual(self.query()['status'],'not_collected')
        path.write_text('{bad','utf-8')
        with self.assertRaises(CatalogFactsError) as caught:self.query()
        self.assertEqual(caught.exception.status,503);self.assertNotIn(str(path),caught.exception.message)

    def test_non_scalar_source_identities_fail_as_a_public_data_error(self):
        for field,identity in (('documents','id'),('dom_documents','source_document_id'),('facts','source_document_id')):
            payload=copy.deepcopy(self.payload);payload[field][0][identity]=[]
            self.write('catalog_facts.json',payload)
            with self.subTest(field=field),self.assertRaises(CatalogFactsError) as caught:self.query()
            self.assertEqual(caught.exception.status,503)


class CatalogFactsBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.fixture=CatalogFactsStoreTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        from service import AdmissionService
        from server import create_server
        self.service=AdmissionService(data_dir=self.fixture.data)
        self.server=create_server(port=0,service=self.service,static_dir=self.fixture.project,rate_limit=100)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=2)

    def request(self,path,method='GET'):
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_address[1],timeout=3)
        try:
            connection.request(method,path);response=connection.getresponse()
            return response.status,json.loads(response.read())
        finally:connection.close()

    def test_http_pages_original_rows_and_progress_count_stays_separate_from_program_count(self):
        status,response=self.request('/api/catalog-facts?unit_id=123&year=2027&offset=1&limit=1')
        self.assertEqual(status,200);self.assertTrue(response['ok'])
        self.assertEqual(response['data']['facts'][0]['code'],'083500')
        progress=self.service.collection_status(unit_id='123')['units'][0]
        self.assertEqual(progress['fact_count'],2);self.assertEqual(progress['program_count'],0)
        self.assertNotIn('local_path',json.dumps(response));self.assertNotIn('facts',self.service.collection_status()['summary'])

    def test_http_requires_one_school_rejects_unknown_extra_duplicate_and_bad_page_arguments(self):
        for query in ('','unit_id=123&unit_id=456','unit_id=123&wat=1','unit_id=123&limit=999',
                      'unit_id=123&offset=-1','unit_id=123&year=2028'):
            with self.subTest(query=query):self.assertEqual(self.request('/api/catalog-facts?'+query)[0],400)
        status,response=self.request('/api/catalog-facts?unit_id=999')
        self.assertEqual(status,404);self.assertEqual(response['error']['code'],'unknown_unit')
        self.assertEqual(self.request('/api/catalog-facts?unit_id=123','POST')[0],405)


if __name__=='__main__':unittest.main()
