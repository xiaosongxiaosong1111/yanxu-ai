import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from collectors.national_catalog import collect_saved, main


UNIT={'id':'123','name':'样本大学','unit_code':'10001','official_url':'https://www.sample.edu.cn/'}
HTML='<h1>样本大学2027年硕士研究生招生专业目录</h1><table><tr><th>学院</th><th>专业</th><th>研究方向</th><th>学习方式</th><th>初试科目</th></tr><tr><td>001计算机学院</td><td>081200计算机科学与技术</td><td>00不区分研究方向</td><td>全日制</td><td>①101思想政治理论②201英语（一）或203日语③301数学（一）④408计算机学科专业基础</td></tr></table>'


class NationalCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.originals=self.root/'data/snapshots/national/2027/123'
        self.originals.mkdir(parents=True)
        self.output=self.root/'data/national_catalog.json'
        self.registry={'units':[UNIT]}
        self.doc=self.document('one',HTML)
        self.national={'units':[{'unit_id':'123','unit_name':'样本大学','year':2027,'status':'collected',
                                'documents':[self.doc],'pages':[self.doc]}]}

    def document(self,identifier,body,url=None,**extra):
        raw=body.encode() if isinstance(body,str) else body
        path=self.originals/(identifier+'.html')
        path.write_bytes(raw)
        return {'id':identifier,'unit_id':'123','url':url or 'https://grad.sample.edu.cn/'+identifier,
            'local_path':path.relative_to(self.root).as_posix(),'sha256':hashlib.sha256(raw).hexdigest(),
            'content_type':'text/html;charset=utf-8','kind':'catalog','year':2027,'status':'fetched',**extra}

    def collect(self,**kwargs):
        with patch('collectors.national_catalog.parser_revision',return_value='test-v1'):
            return collect_saved(self.registry,self.national,self.output,project_root=self.root,**kwargs)

    def test_real_saved_html_is_parsed_offline_with_every_or_option_and_original_quotes(self):
        with patch('public_sources.fetch_official_document',side_effect=AssertionError('network forbidden')):
            report=self.collect()
        self.assertEqual(report['metadata']['units_with_sources'],1)
        self.assertEqual(report['metadata']['units_with_programs'],1)
        self.assertFalse(report['metadata']['catalog_complete'])
        unit=report['units'][0]
        self.assertEqual(unit['files'][0]['status'],'ready')
        ready=json.loads((self.root/unit['ready_entry_path']).read_text('utf-8'))
        self.assertEqual(len(ready['programs']),1)
        self.assertEqual([s['code'] for s in ready['programs'][0]['subjects']],['101','201','203','301','408'])
        self.assertTrue(all(ref['quote'] for ref in ready['programs'][0]['evidence']))
        self.assertEqual(json.loads(self.output.read_text('utf-8'))['metadata']['units_with_programs'],1)

    def test_cache_is_reused_only_when_original_hash_parser_and_result_integrity_match(self):
        import collectors.national_catalog as catalog
        with patch('collectors.national_catalog.parse_saved_document',wraps=catalog.parse_saved_document) as parse:
            first=self.collect()
            self.collect()
            self.assertEqual(parse.call_count,1)
            cache=self.root/first['units'][0]['files'][0]['cache_path']
            cache.write_text(cache.read_text('utf-8').replace('计算机科学与技术','伪造的专业'), 'utf-8')
            self.collect()
            self.assertEqual(parse.call_count,2)
        with patch('collectors.national_catalog.parser_revision',return_value='test-v2'):
            report=collect_saved(self.registry,self.national,self.output,project_root=self.root)
        self.assertEqual(report['metadata']['parser_revision'],'test-v2')
        self.assertEqual(report['metadata']['cache_hits'],0)

    def test_bad_hash_escape_oversize_and_wrong_unit_are_isolated_before_parsing(self):
        documents=[{**self.doc,'id':'hash','sha256':'0'*64},
                   {**self.doc,'id':'escape','local_path':'../outside.html'},
                   {**self.doc,'id':'other','url':'https://other.edu.cn/catalog'}]
        oversized=self.originals/'large.html'
        with oversized.open('wb') as stream: stream.truncate(32*1024*1024+1)
        documents.append({**self.doc,'id':'large','local_path':oversized.relative_to(self.root).as_posix()})
        self.national['units'][0]['documents']=documents
        with patch('collectors.national_catalog.parse_saved_document',side_effect=AssertionError('unsafe source parsed')):
            result=self.collect()
        self.assertTrue(all(f['status']=='failure' for f in result['units'][0]['files']))
        self.assertEqual(result['metadata']['units_with_programs'],0)

    def test_one_failure_or_non_catalog_does_not_stop_other_files_and_restricted_is_explicit(self):
        self.national['units'][0]['documents']=[self.document('news','<h1>校园新闻</h1>'),
            self.document('broken',b'PK\x03\x04broken',content_type='application/octet-stream'),
            {**self.doc,'id':'restricted','status':'restricted'},self.doc]
        result=self.collect()
        states={row['source_id']:row['status'] for row in result['units'][0]['files']}
        self.assertEqual(states['news'],'not_catalog')
        self.assertIn(states['broken'],{'failure','needs_adapter'})
        self.assertEqual(states['restricted'],'restricted')
        self.assertEqual(states['one'],'ready')
        self.assertEqual(result['metadata']['program_count'],1)

    def test_truncated_source_collection_and_limit_do_not_claim_whole_school_coverage(self):
        self.national['units'][0]['status']='truncated'
        self.national['units'][0]['documents'].append(self.document('next',HTML.replace('081200','083500')))
        report=self.collect(max_files=1)
        self.assertTrue(report['metadata']['limited'])
        self.assertEqual(report['metadata']['catalog_verified_units'],0)
        self.assertEqual(report['units'][0]['catalog_status'],'partial')

    def test_driver_never_overwrites_admissions_or_original_sources(self):
        admissions=self.root/'data/admissions.json'
        admissions.write_bytes(b'KEEP ORIGINAL DATA')
        before=(self.root/self.doc['local_path']).read_bytes()
        self.collect()
        self.assertEqual(admissions.read_bytes(),b'KEEP ORIGINAL DATA')
        self.assertEqual((self.root/self.doc['local_path']).read_bytes(),before)
        with self.assertRaises(ValueError):
            collect_saved(self.registry,self.national,admissions,project_root=self.root)

    def test_cli_supports_repeatable_offline_unit_scope_and_reports_unknown_units(self):
        registry=self.root/'data/units.json';sources=self.root/'data/national_sources.json'
        registry.write_text(json.dumps(self.registry),'utf-8')
        sources.write_text(json.dumps(self.national),'utf-8')
        self.assertEqual(main(['--root',str(self.root),'--units',str(registry),'--sources',str(sources),
                              '--output',str(self.output),'--unit-id','123','--max-files','1']),0)
        with self.assertRaises(ValueError): self.collect(unit_ids=['unknown'])

    def test_saved_office_original_is_replayed_without_fabricated_pages(self):
        from tests.test_catalog_office import docx,CatalogOfficeTests
        self.registry={'units':[{**UNIT,'name':'测试大学'}]}
        self.national['units'][0]['documents']=[self.document('office',docx(CatalogOfficeTests().rows()))]
        result=self.collect()
        file=result['units'][0]['files'][0]
        self.assertEqual(file['parser'],'office_table_v1')
        self.assertEqual(file['status'],'ready')
        entry=json.loads((self.root/result['units'][0]['ready_entry_path']).read_text('utf-8'))
        self.assertTrue(all(ref['page'] is None for ref in entry['programs'][0]['evidence']))

    def test_whole_file_single_exam_recommendation_and_doctoral_scopes_are_not_unified_exam_rows(self):
        for title in ['2027年单独考试硕士生招生专业目录','2027年推免生招生专业目录',
                      '2027年博士研究生招生专业目录','2027年港澳台研究生招生目录']:
            with self.subTest(title=title):
                self.national['units'][0]['documents']=[{**self.doc,'title':title}]
                result=self.collect()
                self.assertEqual(result['units'][0]['files'][0]['status'],'not_catalog')
                self.assertEqual(result['metadata']['program_count'],0)
                self.assertIn('范围',result['units'][0]['files'][0]['reason'])

    def test_original_office_heading_and_decoded_filename_exclude_single_exam_scope(self):
        from tests.test_catalog_office import docx,package,CatalogOfficeTests
        import io,zipfile
        with zipfile.ZipFile(io.BytesIO(docx(CatalogOfficeTests().rows()))) as archive:
            xml=archive.read('word/document.xml').decode().replace('2027年硕士','2027年单独考试硕士')
        raw=package({'word/document.xml':xml})
        self.registry={'units':[{**UNIT,'name':'测试大学'}]}
        self.national['units'][0]['documents']=[self.document('office-single',raw)]
        self.assertEqual(self.collect()['units'][0]['files'][0]['status'],'not_catalog')
        from urllib.parse import quote
        self.national['units'][0]['documents']=[{**self.doc,'url':
            'https://grad.sample.edu.cn/'+quote('2027年单独考试目录.docx')}]
        self.assertEqual(self.collect()['units'][0]['files'][0]['status'],'not_catalog')

    def test_mixed_whole_file_scope_stays_unresolved_and_body_notes_do_not_exclude_normal_rows(self):
        self.national['units'][0]['documents']=[{**self.doc,'title':'2027年全国统考与推免招生专业目录'}]
        self.assertEqual(self.collect()['units'][0]['files'][0]['status'],'needs_adapter')
        self.national['units'][0]['documents']=[self.document('normal',HTML+'<p>推免生人数另见推免目录。</p>')]
        self.assertEqual(self.collect()['units'][0]['files'][0]['status'],'ready')

    def test_explicitly_excluding_recommendation_students_does_not_exclude_unified_catalog(self):
        self.national['units'][0]['documents']=[{**self.doc,'title':'2027年硕士招生专业目录（不含推免生）'}]
        self.assertEqual(self.collect()['units'][0]['files'][0]['status'],'ready')
        self.national['units'][0]['documents']=[{**self.doc,'title':'2027年硕士博士研究生招生专业目录'}]
        self.assertEqual(self.collect()['units'][0]['files'][0]['status'],'needs_adapter')

    def test_actual_parent_single_exam_anchor_cannot_publish_generic_child_table_as_unified_exam(self):
        parent=self.document('parent-scope','<h1>样本大学2027年硕士研究生招生专业目录</h1>'
            '<article><a href="/one">2027年单独考试目录</a></article>')
        self.national['units'][0]['pages']=[parent]
        self.national['units'][0]['documents']=[{**self.doc,'source_url':parent['url']}]
        self.assertEqual(self.collect()['units'][0]['files'][0]['status'],'not_catalog')

    def test_only_independent_index_and_all_actual_members_can_verify_a_school(self):
        index=self.document('index','<h1>样本大学2027年硕士研究生招生专业目录</h1><a href="/one">计算机学院</a><a href="/two">软件学院</a>')
        other=self.document('two',HTML.replace('081200','083500'))
        self.national['units'][0]['documents']=[index,self.doc,other]
        result=self.collect()
        self.assertEqual(result['metadata']['catalog_verified_units'],1)
        self.assertEqual(result['metadata']['program_count'],2)
        self.national['units'][0]['documents']=[index,self.doc]
        result=self.collect()
        self.assertEqual(result['metadata']['catalog_verified_units'],0)

    def test_parent_proof_is_checked_even_when_pdf_layout_is_not_supported(self):
        from collectors.documents import DocumentError
        parent=self.document('parent','<h1>样本大学2027年硕士研究生招生专业目录</h1><article><a href="/catalog.pdf">全日制招生专业目录</a></article>')
        attachment=self.document('pdf',b'%PDF-fake',url='https://grad.sample.edu.cn/catalog.pdf',source_url=parent['url'],content_type='application/pdf')
        self.national['units'][0]['pages']=[parent,attachment]
        self.national['units'][0]['documents']=[attachment]
        with patch('collectors.national_catalog.extract_pdf_text',side_effect=DocumentError('当前PDF表格待适配')):
            result=self.collect()
        file=result['units'][0]['files'][0]
        self.assertTrue(file['publication_verified'])
        self.assertNotEqual(file['status'],'ready')

    def test_changed_raw_hash_reparses_and_refresh_does_not_trust_stale_result(self):
        self.collect()
        path=self.root/self.doc['local_path']
        path.write_text(HTML.replace('081200','083500'),'utf-8')
        self.doc['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
        report=self.collect()
        entry=json.loads((self.root/report['units'][0]['ready_entry_path']).read_text('utf-8'))
        self.assertEqual(entry['programs'][0]['code'],'083500')
        self.assertEqual(report['metadata']['cache_hits'],0)
        self.assertEqual(self.collect(refresh=True)['metadata']['cache_hits'],0)

    def test_fetch_timestamp_does_not_invalidate_an_identical_original_parse(self):
        self.collect()
        self.doc['checked_at']='2026-10-30T12:00:00Z'
        self.assertEqual(self.collect()['metadata']['cache_hits'],1)

    def test_limit_keeps_unprocessed_unit_entries_and_checkpoints_the_report(self):
        other={**UNIT,'id':'456','name':'另一所大学'}
        self.registry['units'].append(other)
        self.national['units'].append({'unit_id':'456','documents':[self.doc],'pages':[]})
        old=self.root/'data/runtime/national_catalog/units/456-2027.json'
        old.parent.mkdir(parents=True)
        old.write_text('KEEP PREVIOUS DERIVATION','utf-8')
        seen=[]
        self.collect(max_files=1,progress=lambda value:seen.append(json.loads(self.output.read_text('utf-8'))['metadata']))
        self.assertFalse(seen[0]['traversal_complete'])
        final=json.loads(self.output.read_text('utf-8'))
        self.assertEqual(final['metadata']['pending_units'],1)
        self.assertEqual(old.read_text('utf-8'),'KEEP PREVIOUS DERIVATION')


if __name__=='__main__': unittest.main()
