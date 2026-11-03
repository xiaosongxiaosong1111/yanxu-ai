"""Typed catalog adapters must retain raw-source checks through ingestion and evidence."""
import hashlib
from pathlib import Path
import tempfile
import unittest
import os
import io
import json
from unittest.mock import patch
from copy import deepcopy

from service_verification import SourceVerifier,CACHE_LIMITS
from collectors.catalog_batch import PARSERS, parse_document
from collectors.catalog_office_national import parse_catalog_office_national
from tests.test_catalog_office_national import workbook, cells
import tests.test_catalog_public_forms as form_fixtures
import tests.test_catalog_public_aspx as aspx_fixtures
from collectors.catalog_batch import ingest_verified_batch, collect_school
from collectors.catalog_batch import unit_scope_supported
from collectors.catalog_semantic_audit import PARSER_FILES
from tests import test_catalog_facts as fact_fixtures
from collectors.national_sources import atomic_json


class ExpansionIntegrationTests(unittest.TestCase):
    def test_large_catalog_atomic_writer_streams_and_preserves_previous_data_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'catalog.json';payload={'学校':['原文甲','原文乙'],'count':2}
            expected=(json.dumps(payload,ensure_ascii=False,indent=2)+'\n').replace('\n',os.linesep).encode('utf-8')
            with patch('collectors.national_sources.json.dumps',side_effect=MemoryError('whole serialized allocation')):
                atomic_json(target,payload)
            self.assertEqual(target.read_bytes(),expected)
            with self.assertRaises(TypeError):atomic_json(target,{'invalid':object()})
            self.assertEqual(target.read_bytes(),expected)

    def test_named_masters_or_special_plan_titles_do_not_prove_whole_university_scope(self):
        unit={'id':'sample','name':'样本大学'}
        for label in ['法律硕士','教育硕士','退役大学生士兵专项计划硕士']:
            text=f'<h1>样本大学2027年{label}研究生招生专业目录</h1>'
            self.assertFalse(unit_scope_supported(unit,2027,[('html_structured_v1',text)]),label)
        self.assertTrue(unit_scope_supported(unit,2027,[('html_structured_v1','<h1>样本大学2027年招收攻读硕士研究生招生专业目录</h1>')]))

    def test_structured_html_and_office_tables_keep_originals_through_the_service(self):
        headers=('培养单位','招生专业','研究方向','学习方式','考试科目')
        html,metadata=fact_fixtures.source(fact_fixtures.table([
            ('001信息学院','081200计算机科学与技术','00不区分研究方向','全日制',fact_fixtures.EXAM)],headers))
        office=workbook({'A1':'样本大学2027年硕士研究生招生专业目录',
            **{f'{column}2':label for column,label in zip('ABCDE',headers)},
            'A3':'001信息学院','B3':'081200计算机科学与技术','C3':'00不区分研究方向',
            'D3':'全日制','E3':fact_fixtures.EXAM})
        for adapter,raw,extension in [('html_structured_v1',html,'html'),('office_structured_v1',office,'xlsx')]:
            with self.subTest(adapter=adapter),tempfile.TemporaryDirectory() as directory:
                self.assertIn(adapter,PARSERS)
                root=Path(directory);path=root/f'data/snapshots/original.{extension}'
                path.parent.mkdir(parents=True);path.write_bytes(raw)
                doc={**metadata,'catalog_parser':adapter,'status':'parsed','url':f'https://grad.sample.edu.cn/catalog.{extension}',
                     'local_path':path.relative_to(root).as_posix(),'sha256':hashlib.sha256(raw).hexdigest()}
                parsed=parse_document(doc,fact_fixtures.UNIT,2027,root,raw)
                self.assertEqual(len(parsed['programs']),1,parsed['issues'])
                row=parsed['programs'][0];verifier=SourceVerifier(root)
                self.assertTrue(verifier.program(row,{doc['id']:doc},fact_fixtures.UNIT))
                self.assertFalse(verifier.program({**row,'name':'伪造专业'}, {doc['id']:doc},fact_fixtures.UNIT))

    def test_context_fingerprint_hashes_originals_without_large_buffer_allocations(self):
        class BoundedStream(io.BytesIO):
            def read(self,size=-1):
                if size<0 or size>1024*1024: raise MemoryError('unbounded source buffer')
                return super().read(size)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'original';raw=b'public original source'
            record={'id':'entry','local_path':str(path),'sha256':hashlib.sha256(raw).hexdigest()}
            verifier=SourceVerifier(directory)
            with patch.object(verifier,'read',return_value=(path,raw)),patch.object(Path,'open',side_effect=lambda *a,**k:BoundedStream(raw)):
                fingerprint=verifier._context_stamp({'public_form_context':{'entry_document_id':'entry'}},{'entry':record})
            self.assertEqual(len(fingerprint),64)

    def test_many_public_original_versions_do_not_retain_every_parsed_catalog(self):
        fixture=form_fixtures.PublicFormTests('test_public_form_complete_identity_exams_and_exact_quotes')
        fixture.setUp();self.addCleanup(fixture.doCleanups)
        docs={key:{**value,'status':'parsed','kind':'catalog' if key=='leaf' else 'catalog_context'}
              for key,value in fixture.docs.items()}
        verifier=SourceVerifier(fixture.root);rows=[]
        for number in range(22):
            doc={**docs['leaf'],'id':f'original-{number}'};docs={**docs,doc['id']:doc}
            result=verifier._source_programs(doc,fixture.unit,2027,{},docs)
            self.assertIsNotNone(result);rows.append(result['programs'][0])
        self.assertLessEqual(len(verifier._parsed),16)
        self.assertTrue(verifier.program(rows[0],docs,fixture.unit))
        self.assertTrue(verifier.program(rows[-1],docs,fixture.unit))

    def test_source_byte_budget_evicts_old_buffers_and_rereads_their_hash(self):
        with tempfile.TemporaryDirectory() as directory,patch.dict(CACHE_LIMITS,{'_bytes':(4,10)}):
            root=Path(directory);verifier=SourceVerifier(root);records=[]
            for number in range(3):
                raw=f'raw-{number:03}'.encode();path=root/f'{number}.txt';path.write_bytes(raw)
                record={'local_path':str(path),'sha256':hashlib.sha256(raw).hexdigest()};records.append(record)
                self.assertIsNotNone(verifier.read(record))
            self.assertLessEqual(sum(len(value) for value in verifier._bytes.values()),10)
            self.assertIsNotNone(verifier.read(records[0]))
            Path(records[1]['local_path']).write_bytes(b'changed')
            self.assertIsNone(verifier.read(records[1]))

    def test_second_national_pdf_layout_remains_behind_the_independent_semantic_gate(self):
        self.assertIn('pdf_table_national_v2',PARSERS)
        self.assertEqual(PARSER_FILES.get('pdf_table_national_v2'),'catalog_pdf_national_v2.py')

    def test_new_office_adapter_replays_original_merged_cells_and_rejects_edited_course(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            raw=workbook(cells(),['A4:A7','B4:B7','C4:C5','C6:C7','D4:D7','E4:E7'])
            path=root/'data/snapshots/source.xlsx';path.parent.mkdir(parents=True);path.write_bytes(raw)
            unit={'id':'sample','name':'样本大学','official_url':'https://sample.edu.cn/'}
            doc={'id':'sample-doc','unit_id':'sample','year':2027,'kind':'catalog','status':'parsed',
                 'catalog_parser':'office_national_v1','url':'https://sample.edu.cn/catalog.xlsx',
                 'local_path':'data/snapshots/source.xlsx','sha256':hashlib.sha256(raw).hexdigest()}
            self.assertIn('office_national_v1',PARSERS)
            parsed=parse_document(doc,unit,2027,root,raw)
            self.assertEqual(len(parsed['programs']),2)
            verifier=SourceVerifier(root)
            self.assertTrue(verifier.program(parsed['programs'][0],{doc['id']:doc},unit))
            changed=deepcopy(parsed['programs'][0]);changed['subjects'][3]['code']='999'
            self.assertFalse(verifier.program(changed,{doc['id']:doc},unit))

    def test_public_form_contexts_are_not_treated_as_direction_pages(self):
        fixture=form_fixtures.PublicFormTests('test_public_form_complete_identity_exams_and_exact_quotes')
        fixture.setUp();self.addCleanup(fixture.doCleanups)
        docs={key:{**value,'status':'parsed','kind':'catalog' if key=='leaf' else 'catalog_context'}
              for key,value in fixture.docs.items()}
        verifier=SourceVerifier(fixture.root)
        parsed=fixture.parse();row=parsed['programs'][0]
        self.assertTrue(verifier.program(row,docs,fixture.unit))
        self.assertTrue(verifier.unit_reconciles(fixture.unit,2027,[row],docs))
        missing={key:value for key,value in docs.items() if key!='directions'}
        self.assertFalse(SourceVerifier(fixture.root).program(row,missing,fixture.unit))
        self.assertFalse(SourceVerifier(fixture.root).unit_reconciles(fixture.unit,2027,[row],missing))

    def test_public_context_same_length_tampering_with_restored_mtime_invalidates_replay_cache(self):
        fixture=form_fixtures.PublicFormTests('test_public_form_complete_identity_exams_and_exact_quotes')
        fixture.setUp();self.addCleanup(fixture.doCleanups)
        docs={key:{**value,'status':'parsed','kind':'catalog' if key=='leaf' else 'catalog_context'}
              for key,value in fixture.docs.items()}
        row=fixture.parse()['programs'][0];verifier=SourceVerifier(fixture.root)
        self.assertTrue(verifier.program(row,docs,fixture.unit))
        path=fixture.root/docs['directions']['local_path'];before=path.stat()
        raw=path.read_bytes();changed=raw.replace(b'value="01"',b'value="02"')
        self.assertNotEqual(raw,changed);self.assertEqual(len(raw),len(changed))
        path.write_bytes(changed);os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns))
        self.assertFalse(verifier.program(row,docs,fixture.unit))

    def test_public_primary_url_change_cannot_reuse_previously_verified_route(self):
        fixture=form_fixtures.PublicFormTests('test_public_form_complete_identity_exams_and_exact_quotes')
        fixture.setUp();self.addCleanup(fixture.doCleanups)
        docs={key:{**value,'status':'parsed','kind':'catalog' if key=='leaf' else 'catalog_context'}
              for key,value in fixture.docs.items()}
        row=fixture.parse()['programs'][0];verifier=SourceVerifier(fixture.root)
        self.assertTrue(verifier.program(row,docs,fixture.unit))
        changed={**docs,'leaf':{**docs['leaf'],'url':'https://yzb.buaa.edu.cn/wrong-original-route'}}
        self.assertFalse(verifier.program(row,changed,fixture.unit))
        self.assertFalse(SourceVerifier(fixture.root).program(row,changed,fixture.unit))

    def test_typed_original_degree_cannot_be_changed_to_a_different_cutoff_category(self):
        fixture=form_fixtures.PublicFormTests('test_public_form_complete_identity_exams_and_exact_quotes')
        fixture.setUp();self.addCleanup(fixture.doCleanups)
        docs={key:{**value,'status':'parsed','kind':'catalog' if key=='leaf' else 'catalog_context'}
              for key,value in fixture.docs.items()}
        row=fixture.parse()['programs'][0];verifier=SourceVerifier(fixture.root)
        self.assertEqual(row['degree_type'],'学术学位')
        self.assertFalse(verifier.program({**row,'degree_type':'专业学位'},docs,fixture.unit))

    def test_typed_original_restriction_note_cannot_be_replaced_without_source_proof(self):
        fixture=form_fixtures.PublicFormTests('test_public_form_complete_identity_exams_and_exact_quotes')
        fixture.setUp();self.addCleanup(fixture.doCleanups)
        docs={key:{**value,'status':'parsed','kind':'catalog' if key=='leaf' else 'catalog_context'}
              for key,value in fixture.docs.items()}
        row=fixture.parse()['programs'][0];verifier=SourceVerifier(fixture.root)
        self.assertTrue(verifier.program(row,docs,fixture.unit))
        self.assertFalse(verifier.program({**row,'notes':'任何本科毕业生均可报考'},docs,fixture.unit))

    def test_public_post_request_bytes_cannot_reuse_warm_replay_after_tampering(self):
        fixture=aspx_fixtures.PublicAspxTests('test_real_leaf_identity_and_visible_initial_exam_only')
        fixture.setUp();self.addCleanup(fixture.doCleanups)
        fixture.add_post_page();leaf,html=fixture.add_nonfull_leaf()
        docs={key:{**value,'status':'parsed'} for key,value in fixture.docs.items()}
        row=fixture.parse(document=leaf,html=html)['programs'][0]
        verifier=SourceVerifier(fixture.root)
        self.assertTrue(verifier.program(row,docs,fixture.unit))
        request=docs['mode2']['request_document'];path=fixture.root/request['local_path'];before=path.stat()
        raw=path.read_bytes();changed=raw.replace(b'first-state',b'other-state')
        self.assertNotEqual(raw,changed);self.assertEqual(len(raw),len(changed))
        path.write_bytes(changed);os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns))
        self.assertFalse(verifier.program(row,docs,fixture.unit))

    def test_ingestion_retains_proven_excluded_leaves_for_whole_catalog_reconciliation(self):
        fixture=aspx_fixtures.PublicAspxTests('test_real_leaf_identity_and_visible_initial_exam_only')
        fixture.setUp();self.addCleanup(fixture.doCleanups)
        fixture.add_post_page();leaf,html=fixture.add_nonfull_leaf()
        special=html.replace('</table>','<tr><td>备注</td><td>本方向只接收推免生。</td></tr></table>')
        excluded=fixture.save('nonfull',special,leaf['url'])
        fixture.docs['nonfull']={**excluded,**{key:leaf[key] for key in ('kind','catalog_parser','public_form_context')}}
        row=fixture.parse()['programs'][0]
        entry={'status':{'unit_id':fixture.unit['id'],'year':2027,'status':'verified','notes':[]},
               'programs':[row],'documents':[{**doc,'status':'parsed'} for doc in fixture.docs.values()]}
        result=ingest_verified_batch({'units':[fixture.unit]},[entry],project_root=fixture.root)
        self.assertEqual(len(result['programs']),1)
        self.assertEqual(result['metadata']['unit_catalog_status'][0]['status'],'verified')
        self.assertTrue(next(doc for doc in result['documents'] if doc['id']=='nonfull')['catalog_active'])

    def test_collect_public_catalog_preserves_request_chain_and_uses_visible_all_mode_scope(self):
        fixture=aspx_fixtures.PublicAspxTests('test_real_leaf_identity_and_visible_initial_exam_only')
        fixture.setUp();self.addCleanup(fixture.doCleanups)
        fixture.add_post_page();fixture.add_nonfull_leaf()
        contexts={key:{**doc,'status':'parsed'} for key,doc in fixture.docs.items() if doc['kind']=='catalog_context'}
        sources=[{**doc,'source_path':str(fixture.root/doc['local_path']),'parser':'public_aspx_v1','context_documents':contexts}
                 for doc in fixture.docs.values() if doc.get('catalog_parser')=='public_aspx_v1']
        result=collect_school({'year':2027,'sources':sources},fixture.unit,fixture.root/'data/snapshots/formal',
                              project_root=fixture.root,offline=True)
        self.assertEqual(len(result['programs']),2)
        self.assertEqual(result['status']['status'],'verified',result['issues'])
        self.assertIn('mode2',{doc['id'] for doc in result['documents']})


if __name__=='__main__':unittest.main()
