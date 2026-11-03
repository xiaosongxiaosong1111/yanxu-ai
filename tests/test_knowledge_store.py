from copy import deepcopy
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from knowledge_store import KnowledgeStore, KnowledgeError


TASK_A='r_'+'1'*32
TASK_B='r_'+'2'*32


class KnowledgeStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'data'
        self.store=KnowledgeStore(self.root)
        self.lead={'url':'https://grad.example.edu.cn/catalog?a=1','title':'2027年计算机招生目录',
                   'unit_id':'u1','unit_name':'示例大学','year':None,'requested_year':2028,
                   'status':'search_lead','excerpt':'搜索摘要：计算机专业考试科目尚待核验。'}

    def original(self,text='官方原文：计算机专业考试科目为101、201、301、408。',raw=b'original-version-one'):
        digest=hashlib.sha256(raw).hexdigest()
        source={**self.lead,'status':'official_original_read','year':2027,'sha256':digest,
                'checked_at':'2026-11-02T12:00:00Z','excerpt':text,'field_verification':'not_structured'}
        document={'url':source['url'],'raw':raw,'sha256':digest,'attribution_status':'verified',
                  'text':text,'pages':[{'page':1,'text':text}]}
        return source,document

    def test_constructor_and_empty_reads_do_not_create_a_database(self):
        self.assertFalse(self.root.exists())
        result=self.store.search(quality='all')
        self.assertEqual(result['total'],0);self.assertEqual(result['entries'],[])
        self.assertFalse(self.root.exists())
        with self.assertRaises(KnowledgeError):self.store.get('k_'+'0'*24)

    def test_repeated_leads_merge_references_and_do_not_import_private_state(self):
        before=deepcopy(self.lead)
        first=self.store.save_source(self.lead,research_id=TASK_A,query='不会落盘的用户原句')
        second=self.store.save_source(self.lead,research_id=TASK_B)
        self.assertEqual(first['id'],second['id']);self.assertEqual(self.store.search(quality='all')['total'],1)
        self.assertEqual(set(second['provenance']),{TASK_A,TASK_B})
        self.assertEqual(self.lead,before)
        self.assertEqual(second['year'],None);self.assertEqual(second['requested_year'],2028)
        self.assertNotIn('不会落盘',self.store.path.read_bytes().decode('latin1'))

    def test_original_promotes_lead_and_preserves_the_exact_text_and_snapshot(self):
        first=self.store.save_source(self.lead,research_id=TASK_A)
        source,document=self.original()
        original=self.store.save_source(source,research_id=TASK_B,document=document)
        self.assertEqual(first['id'],original['id']);self.assertEqual(self.store.search(quality='all')['total'],1)
        self.assertTrue(original['original_available']);self.assertEqual(original['year'],2027)
        detail=self.store.get(original['id'])
        self.assertEqual(detail['text'],document['text']);self.assertEqual(detail['text_origin'],'full_extraction')
        self.assertEqual(detail['pages'],document['pages'])
        self.assertNotIn(str(self.root),json.dumps(detail))
        later=self.store.save_source(self.lead)
        self.assertEqual(later['status'],'official_original_read')
        self.assertEqual(later['excerpt'],source['excerpt'])

    def test_same_url_new_bytes_preserves_versions_and_same_bytes_share_source_group(self):
        source,doc=self.original();a=self.store.save_source(source,document=doc)
        source2,doc2=self.original(raw=b'original-version-two');b=self.store.save_source(source2,document=doc2)
        self.assertNotEqual(a['id'],b['id']);self.assertEqual(b['version_count'],2)
        copy_source={**source,'url':'https://grad.example.edu.cn/copied.pdf'}
        copied=self.store.save_source(copy_source,document={**doc,'url':copy_source['url']})
        self.assertEqual(copied['source_group'],a['source_group'])
        different=self.store.save_source({**self.lead,'unit_id':'u2','unit_name':'另一学校'})
        self.assertNotEqual(a['id'],different['id'])

    def test_snapshot_loss_or_same_length_tampering_revokes_text_and_cache_trust(self):
        source,doc=self.original();saved=self.store.save_source(source,document=doc)
        blob=self.root/'runtime/research-sources'/(source['sha256']+'.bin')
        blob.write_bytes(b'X'*len(doc['raw']))
        detail=self.store.get(saved['id'])
        self.assertEqual(detail['status'],'search_lead');self.assertFalse(detail['original_available'])
        self.assertEqual(detail['text'],'');self.assertEqual(detail['excerpt'],'')
        self.assertEqual(self.store.search(quality='all',status='official_original_read')['total'],0)
        renewed=self.store.save_source(source,document=doc)
        blob.unlink()
        rows=self.store.search(quality='all',query='计算机')['entries']
        self.assertEqual(rows[0]['status'],'search_lead');self.assertEqual(rows[0]['excerpt'],'')

    def test_historical_import_uses_only_evidence_and_marks_excerpt_not_full_document(self):
        source,doc=self.original()
        blob=self.root/'runtime/research-sources'/(source['sha256']+'.bin')
        blob.parent.mkdir(parents=True);blob.write_bytes(doc['raw'])
        research=self.root/'runtime/research.sqlite'
        with closing(sqlite3.connect(research)) as c:
            c.execute('CREATE TABLE research_tasks(id TEXT,payload TEXT)')
            payload={'query':'private-question','profile':{'phone':'13812345678'},
                     'report':{'evidence':[source,self.lead],'candidates':[{'name':'not-imported'}]}}
            c.execute('INSERT INTO research_tasks VALUES(?,?)',(TASK_A,json.dumps(payload)))
            c.commit()
        counts=self.store.import_reports(research)
        self.assertGreaterEqual(counts['imported'],1)
        entry=self.store.search(quality='all')['entries'][0];detail=self.store.get(entry['id'])
        self.assertEqual(detail['text_origin'],'captured_excerpt')
        self.assertNotIn('private-question',self.store.path.read_bytes().decode('latin1'))
        self.assertNotIn('13812345678',json.dumps(detail))
        self.assertEqual(self.store.import_reports(research)['imported'],counts['imported'])
        self.assertEqual(self.store.search(quality='all')['total'],1)

    def test_chinese_full_text_search_short_terms_pagination_facets_and_year_scope(self):
        source,doc=self.original(text='开头'+('甲'*700)+'招生专业有人工智能与计算机技术。')
        a=self.store.save_source(source,document=doc)
        self.store.save_source({**self.lead,'url':'https://grad.example.edu.cn/two','title':'南开目录','unit_name':'南开大学'})
        self.assertEqual(self.store.search(quality='all',query='人工智能')['entries'][0]['id'],a['id'])
        self.assertIn('人工智能',self.store.search(quality='all',query='人工智能')['entries'][0]['excerpt'])
        self.assertEqual(self.store.search(quality='all',query='南开')['total'],1)
        self.assertEqual(self.store.search(quality='all',query="%' OR 1=1 --")['total'],0)
        self.assertEqual(self.store.search(quality='all',year=2028)['total'],0)
        self.assertEqual(self.store.search(quality='all',year=2027)['total'],1)
        self.assertEqual(len(self.store.search(quality='all',page_size=1,page=2)['entries']),1)
        self.assertEqual(self.store.search(quality='all')['facets']['years'],[2027])
        self.assertEqual(self.store.search(quality='all',status='published_snapshot')['total'],0)
        self.assertEqual(self.store.search(quality='all',unit_id='u1')['total'],2)

    def test_sensitive_admission_rosters_are_excluded_without_indexed_identity_text(self):
        text='姓名 考生编号 初试分\n张同学 100123456789012 380'
        source,doc=self.original(text=text)
        source={**source,'title':'2027年硕士拟录取名单'}
        row=self.store.save_source(source,document=doc)
        self.assertIsNone(row['id']);self.assertFalse(row['saved']);self.assertEqual(row['quality']['state'],'excluded')
        self.assertEqual(self.store.search(quality='all',query='张同学')['total'],0)
        self.assertFalse(self.store.path.exists())

    def test_public_urls_and_boundary_types_are_validated(self):
        for url in ['https://u:p@example.edu.cn/x','http://127.0.0.1/x','https://169.254.169.254/x',
                    'https://localhost/x','file:///secret','https://grad.example.edu.cn/x?api_key=secret',
                    'https://2130706433/x','https://%31%32%37.0.0.1/x','https://localhost./x',
                    'https://127.0.0.1\\foo','https://grad.example.edu.cn/x?apikey=private-canary']:
            with self.subTest(url=url),self.assertRaises(KnowledgeError):self.store.save_source({**self.lead,'url':url})
        for changes in [{'secret_key':'private'},{'profile':{'name':'private'}},{'year':True},
                        {'status':'verified'},{'excerpt':'secret_key=private-canary'},{'unit_id':[]},{'checked_at':False}]:
            with self.subTest(changes=changes),self.assertRaises(KnowledgeError):self.store.save_source({**self.lead,**changes})
        for kwargs in [{'page':True},{'page_size':101},{'query':[]},{'status':'verified'},{'year':'2027'},{'unit_id':[]}]:
            with self.subTest(kwargs=kwargs),self.assertRaises(KnowledgeError):self.store.search(quality='all',**kwargs)
        with self.assertRaises(KnowledgeError):self.store.get('../secret')

    def test_published_material_is_not_promoted_to_original_and_public_text_is_bounded(self):
        row=self.store.save_source({**self.lead,'status':'published_snapshot','year':2027,'excerpt':'资料'*20000})
        self.assertEqual(row['status'],'published_snapshot');self.assertFalse(row['original_available'])
        self.assertLessEqual(len(row['excerpt']),600)
        detail=self.store.get(row['id']);self.assertLessEqual(len(detail['text']),32000)
        self.assertTrue(detail['text_truncated'])

    def test_official_excerpt_preserves_crlf_without_accepting_other_control_characters(self):
        quote='学制为3年。\r\n学费标准为每生每年8000元。'
        saved=self.store.save_source({**self.lead,'status':'published_snapshot','year':2027,'excerpt':quote})
        self.assertEqual(saved['excerpt'],quote)
        self.assertEqual(self.store.get(saved['id'])['text'],quote)
        with self.assertRaises(KnowledgeError):self.store.save_source({**self.lead,'excerpt':'原文\x0b不合法控制符'})
        with self.assertRaises(KnowledgeError):self.store.save_source({**self.lead,'url':'https://grad.example.edu.cn/\r\nprivate'})

    def test_catalog_navigation_roster_link_does_not_suppress_program_text(self):
        text='导航：招生简章 拟录取名单 常见问题\n081200计算机科学与技术，业务课二：408计算机学科专业基础。'
        source,doc=self.original(text=text)
        source={**source,'title':'测试大学2027年硕士研究生招生专业目录'}
        saved=self.store.save_source(source,document=doc)
        self.assertEqual(saved['text_origin'],'full_extraction')
        self.assertEqual(self.store.get(saved['id'])['text'],text)
        self.assertEqual(self.store.search(quality='all',query='408')['total'],1)

    def test_policy_reference_to_roster_is_not_personal_identity_content(self):
        text='录取办法：拟录取名单将在学校官网统一公示。考生应核对姓名、考生编号等信息并及时提交异议。'
        source,doc=self.original(text=text)
        source={**source,'title':'2027年硕士研究生复试录取办法'}
        saved=self.store.save_source(source,document=doc)
        self.assertEqual(self.store.get(saved['id'])['text'],text)

    def test_generic_title_with_split_identity_headers_remains_metadata_only(self):
        source,doc=self.original(text='姓\n名\n考生编\n号\n初试分数\n张同学 100123456789012 380')
        source={**source,'title':'2027年研究生通知'}
        saved=self.store.save_source(source,document=doc)
        self.assertIsNone(saved['id']);self.assertFalse(saved['saved']);self.assertEqual(saved['quality']['state'],'excluded')
        self.assertFalse(self.store.path.exists())

    def test_explicit_admission_result_title_remains_metadata_only(self):
        source,doc=self.original(text='结果材料保留来源链接，不向研究对话复制逐人结果。')
        saved=self.store.save_source({**source,'title':'2027年硕士研究生录取结果'},document=doc)
        self.assertIsNone(saved['id']);self.assertEqual(saved['quality']['state'],'excluded')
        self.assertFalse(self.store.path.exists())

    def test_all_missing_originals_are_revoked_before_filtered_pagination(self):
        for index in range(4):
            source,doc=self.original(raw=('version-'+str(index)).encode())
            self.store.save_source(source,document=doc)
        for path in (self.root/'runtime/research-sources').glob('*.bin'):path.unlink()
        result=self.store.search(quality='all',status='official_original_read',page_size=1)
        self.assertEqual(result['entries'],[]);self.assertEqual(result['total'],0)
        self.assertEqual(result['summary']['official_original_read'],0)
        self.assertEqual(result['summary']['search_lead'],4)

    def test_several_same_length_tampered_hits_are_revoked_until_filters_agree(self):
        for index in range(4):
            source,doc=self.original(raw=('version-'+str(index)).encode())
            self.store.save_source(source,document=doc)
        for path in (self.root/'runtime/research-sources').glob('*.bin'):
            path.write_bytes(b'X'*path.stat().st_size)
        result=self.store.search(quality='all',status='official_original_read',page_size=1)
        self.assertEqual(result['entries'],[]);self.assertEqual(result['total'],0)

    def test_identity_content_in_pages_is_not_exposed_when_body_is_empty(self):
        source,doc=self.original(text='')
        doc={**doc,'pages':[{'page':1,'text':'考生编号 姓名\n100123456789012 张同学'}]}
        saved=self.store.save_source(source,document=doc)
        self.assertIsNone(saved['id']);self.assertEqual(saved['quality']['state'],'excluded')
        self.assertFalse(self.store.path.exists())

    def test_malformed_document_pages_are_a_safe_domain_error(self):
        source,doc=self.original()
        with self.assertRaises(KnowledgeError):self.store.save_source(source,document={**doc,'pages':{}})

    def test_no_fts_fallback_and_later_index_rebuild_preserve_old_records(self):
        source,doc=self.original(text='计算机专业含人工智能方向')
        saved=self.store.save_source(source,document=doc)
        with closing(sqlite3.connect(self.store.path)) as c:c.execute('DROP TABLE knowledge_fts');c.commit()
        self.assertEqual(self.store.search(quality='all',query='人工智能')['entries'][0]['id'],saved['id'])
        self.store.save_source({**self.lead,'url':'https://grad.example.edu.cn/new'})
        self.assertEqual(self.store.search(quality='all',query='人工智能')['entries'][0]['id'],saved['id'])

    def test_corrupted_payload_and_connection_failure_have_safe_errors(self):
        saved=self.store.save_source(self.lead)
        with closing(sqlite3.connect(self.store.path)) as c:
            c.execute("UPDATE knowledge_entries SET payload='bad json'");c.commit()
        with self.assertRaises(KnowledgeError) as error:self.store.get(saved['id'])
        self.assertEqual(error.exception.code,'knowledge_unavailable')
        with patch('knowledge_store.sqlite3.connect',side_effect=sqlite3.OperationalError('private-path')):
            with self.assertRaises(KnowledgeError) as error:self.store.search(quality='all')
        self.assertNotIn('private-path',str(error.exception))

    def test_bad_snapshot_document_and_surrogate_text_are_rejected(self):
        source,doc=self.original()
        for broken in [{**doc,'raw':b'different'},{**doc,'attribution_status':'unverified'},
                       {**doc,'url':'https://other.edu.cn/source'}]:
            with self.subTest(broken=broken),self.assertRaises(KnowledgeError):self.store.save_source(source,document=broken)
        with self.assertRaises(KnowledgeError):self.store.save_source({**self.lead,'title':'\ud800'})

    def test_missing_import_database_empty_text_and_bad_import_records(self):
        self.assertEqual(self.store.import_reports(self.root/'absent.sqlite')['imported'],0)
        for kwargs in [{'query':' ' * 241},{'query':'secret_key=private-canary'}]:
            with self.subTest(kwargs=kwargs),self.assertRaises(KnowledgeError):self.store.search(quality='all',**kwargs)
        with self.assertRaises(KnowledgeError):self.store.save_source(self.lead,research_id='private-person')
        outside=Path(self.temp.name)/'outside.sqlite';outside.touch()
        with self.assertRaises(KnowledgeError):self.store.import_reports(outside)
        database=self.root/'runtime/research.sqlite';database.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(database)) as c:
            c.execute('CREATE TABLE research_tasks(id TEXT,payload TEXT)')
            c.executemany('INSERT INTO research_tasks VALUES(?,?)',[(TASK_A,'broken'),(TASK_B,json.dumps({'report':{'evidence':[{'url':'file:///bad'}]}}))])
            c.commit()
        report=self.store.import_reports(database)
        self.assertEqual(report['imported'],0);self.assertEqual(report['skipped'],2)


class KnowledgeQualityTests(unittest.TestCase):
    original=KnowledgeStoreTests.original
    def setUp(self):
        KnowledgeStoreTests.setUp(self)
        self.quality_calls=[]
        patcher=patch('knowledge_store.assess_quality',side_effect=self.assess)
        self.mock_quality=patcher.start();self.addCleanup(patcher.stop)

    def assess(self,source,body='',*,original_available=False,authority_verified=False,pages=()):
        self.quality_calls.append({'source':deepcopy(source),'body':body,'original':original_available,'authority':authority_verified})
        state='excluded' if 'EXCLUDE' in source.get('title','') else 'accepted' if original_available and authority_verified else 'pending'
        return {'version':1,'state':state,'reusable':state=='accepted','material_type':'admissions_catalog',
                'reasons':[] if state=='accepted' else [{'code':'test_'+state,'message':'测试夹具质量分类'}]}

    def test_quality_filters_default_accepted_and_new_excluded_never_stores_text(self):
        pending=self.store.save_source(self.lead)
        source,doc=self.original();accepted=self.store.save_source(source,document=doc)
        self.assertTrue(accepted['saved']);self.assertEqual(accepted['quality']['state'],'accepted')
        self.store.save_source({**self.lead,'url':'https://grad.example.edu.cn/pending'})
        excluded=self.store.save_source({**self.lead,'url':'https://grad.example.edu.cn/unrelated','title':'EXCLUDE unrelated material','excerpt':'not-to-be-stored-canary'})
        self.assertIsNone(excluded['id']);self.assertFalse(excluded['saved']);self.assertEqual(excluded['quality']['state'],'excluded')
        self.assertEqual(self.store.search()['total'],1)
        self.assertEqual(self.store.search(quality='pending')['total'],1)
        self.assertEqual(self.store.search(quality='all')['total'],2)
        self.assertEqual(self.store.search()['summary']['quality_counts'],{'accepted':1,'pending':1,'excluded':0})
        self.assertNotIn(b'not-to-be-stored-canary',self.store.path.read_bytes())
        with self.assertRaises(KnowledgeError):self.store.search(quality='garbage')

    def test_old_database_stays_pending_until_explicit_transactional_reassessment(self):
        source,doc=self.original();saved=self.store.save_source(source,document=doc)
        self.root.joinpath('units.json').write_text(json.dumps({'units':[{'id':'u1','official_url':'https://www.example.edu.cn'}]}),'utf-8')
        with closing(sqlite3.connect(self.store.path)) as c:
            value=json.loads(c.execute('SELECT payload FROM knowledge_entries').fetchone()[0])
            value.pop('quality',None);value.pop('authority_evidence',None)
            c.execute('UPDATE knowledge_entries SET payload=?',(json.dumps(value),))
            c.execute('DROP INDEX knowledge_quality_state')
            c.execute('ALTER TABLE knowledge_entries DROP COLUMN quality');c.commit()
        reopened=KnowledgeStore(self.root)
        self.assertEqual(reopened.search()['total'],0)
        self.assertEqual(reopened.get(saved['id'])['quality']['state'],'pending')
        result=reopened.reassess()
        self.assertEqual(result['accepted'],1)
        self.assertEqual(reopened.search()['total'],1)
        detail=reopened.get(saved['id'])
        self.assertEqual(detail['authority_evidence']['basis'],'unit_registry_url')
        self.assertEqual(detail['authority_evidence']['status'],'verified')

    def test_legacy_without_quality_column_distrusts_payload_and_pending_list_terminates(self):
        source,doc=self.original();saved=self.store.save_source(source,document=doc)
        with closing(sqlite3.connect(self.store.path)) as connection:
            connection.execute('DROP INDEX knowledge_quality_state')
            connection.execute('ALTER TABLE knowledge_entries DROP COLUMN quality');connection.commit()
        reopened=KnowledgeStore(self.root)
        self.assertEqual(reopened.get(saved['id'])['quality']['state'],'pending')
        self.assertEqual(reopened.search()['total'],0)
        pending=reopened.search(quality='pending')
        self.assertEqual(pending['total'],1)
        self.assertEqual(pending['entries'][0]['quality']['state'],'pending')

    def test_caller_quality_and_authority_flags_cannot_self_certify_a_source(self):
        fake={'version':1,'state':'accepted','reusable':True,'material_type':'admissions_catalog','reasons':[]}
        row=self.store.save_source({**self.lead,'quality':fake,'authority_evidence':{'status':'verified','basis':'document_attribution'}})
        self.assertEqual(row['quality']['state'],'pending')
        self.assertFalse(self.quality_calls[-1]['authority'])
        self.assertNotIn('quality',self.quality_calls[-1]['source'])
        self.assertNotIn('authority_evidence',self.quality_calls[-1]['source'])

    def test_reassessment_excludes_existing_record_but_preserves_auditable_source(self):
        source,doc=self.original();row=self.store.save_source(source,document=doc)
        with closing(sqlite3.connect(self.store.path)) as c:
            value=json.loads(c.execute('SELECT payload FROM knowledge_entries').fetchone()[0]);value['title']='EXCLUDE irrelevant document'
            c.execute('UPDATE knowledge_entries SET title=?,payload=?',(value['title'],json.dumps(value)));c.commit()
        counts=self.store.reassess()
        self.assertEqual(counts['excluded'],1);self.assertEqual(self.store.search()['total'],0)
        excluded=self.store.get(row['id'])
        self.assertEqual(excluded['quality']['state'],'excluded');self.assertEqual(excluded['text'],'');self.assertEqual(excluded['pages'],[])
        self.assertEqual(self.store.search(quality='excluded')['total'],1)
        with closing(sqlite3.connect(self.store.path)) as c:
            self.assertEqual(c.execute('SELECT full_text FROM knowledge_entries').fetchone()[0],doc['text'])
            self.assertEqual(c.execute("SELECT count(*) FROM knowledge_fts WHERE knowledge_fts MATCH '计算机'").fetchone()[0],0)

    def test_weaker_duplicate_does_not_replace_accepted_original_or_authority(self):
        source,doc=self.original();accepted=self.store.save_source(source,document=doc)
        weaker=self.store.save_source({**source,'status':'search_lead','excerpt':'弱摘要'})
        self.assertEqual(weaker['id'],accepted['id']);self.assertEqual(weaker['quality']['state'],'accepted')
        self.assertEqual(weaker['authority_evidence']['basis'],'document_attribution')
        self.assertEqual(self.store.get(accepted['id'])['text'],doc['text'])

    def test_missing_snapshot_revokes_previously_accepted_quality(self):
        source,doc=self.original();row=self.store.save_source(source,document=doc)
        (self.root/'runtime/research-sources'/(source['sha256']+'.bin')).unlink()
        self.assertEqual(self.store.search()['total'],0)
        self.assertEqual(self.store.get(row['id'])['quality']['state'],'pending')

    def test_explicit_new_source_year_does_not_reuse_a_different_year_original(self):
        source,doc=self.original();accepted=self.store.save_source(source,document=doc)
        other=self.store.save_source({**self.lead,'year':2028})
        self.assertNotEqual(other['id'],accepted['id']);self.assertEqual(other['year'],2028)
        self.assertEqual(other['quality']['state'],'pending')
        self.assertEqual(self.store.get(accepted['id'])['year'],2027)

    def test_quality_facets_cover_only_the_selected_quality_partition(self):
        source,doc=self.original();self.store.save_source(source,document=doc)
        self.store.save_source({**self.lead,'url':'https://other.example.edu.cn/2028','unit_id':'u2','unit_name':'待核大学','year':2028})
        accepted=self.store.search();pending=self.store.search(quality='pending')
        self.assertEqual([row['unit_id'] for row in accepted['facets']['units']],['u1'])
        self.assertEqual(accepted['facets']['years'],[2027])
        self.assertEqual([row['unit_id'] for row in pending['facets']['units']],['u2'])
        self.assertEqual(pending['facets']['years'],[2028])
        self.assertEqual(accepted['summary']['quality_counts'],pending['summary']['quality_counts'])

    def test_reassessment_rolls_back_all_record_and_fts_updates_on_failure(self):
        source,doc=self.original();self.store.save_source(source,document=doc)
        self.store.save_source({**source,'url':'https://grad.example.edu.cn/second'},document={**doc,'url':'https://grad.example.edu.cn/second'})
        denied={'version':1,'state':'excluded','reusable':False,'material_type':'unknown','reasons':[]}
        with patch('knowledge_store.assess_quality',side_effect=[denied,ValueError('private-failure')]):
            with self.assertRaises(KnowledgeError):self.store.reassess()
        self.assertEqual(self.store.search()['total'],2)
        with closing(sqlite3.connect(self.store.path)) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM knowledge_entries WHERE quality='accepted'").fetchone()[0],2)
            self.assertEqual(connection.execute("SELECT count(*) FROM knowledge_fts WHERE knowledge_fts MATCH '计算机'").fetchone()[0],2)

    def test_excluded_document_does_not_create_a_new_raw_snapshot(self):
        source,doc=self.original();source={**source,'title':'EXCLUDE unrelated original'}
        result=self.store.save_source(source,document=doc)
        self.assertFalse(result['saved']);self.assertIsNone(result['id'])
        self.assertFalse(self.root.exists())

    def test_offline_import_counts_filtered_records_separately_from_saved_records(self):
        database=self.root/'runtime/research.sqlite';database.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(database)) as connection:
            connection.execute('CREATE TABLE research_tasks(id TEXT,payload TEXT)')
            evidence=[self.lead,{**self.lead,'url':'https://grad.example.edu.cn/noise','title':'EXCLUDE irrelevant material'}]
            connection.execute('INSERT INTO research_tasks VALUES(?,?)',(TASK_A,json.dumps({'report':{'evidence':evidence}})))
            connection.commit()
        result=self.store.import_reports(database)
        self.assertEqual(result['imported'],1);self.assertEqual(result['filtered'],1)
        self.assertEqual(result['skipped'],0);self.assertEqual(self.store.search(quality='all')['total'],1)


if __name__=='__main__':unittest.main()
