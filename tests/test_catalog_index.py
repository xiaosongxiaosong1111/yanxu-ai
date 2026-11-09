from copy import deepcopy
from contextlib import closing, contextmanager
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from catalog_index import CatalogIndex, CatalogIndexError


class CatalogIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'runtime with spaces/catalog-index.sqlite'
        self.index = CatalogIndex(self.path)
        self.registry = {'units': [{'id':'a','name':'甲大学','province':'北京'},
                                   {'id':'b','name':'乙研究所','province':'天津'}]}
        self.programs = [{'id':f'p{i}','unit_id':'a' if i<3 else 'b','year':2027,
                          'unit_name':'甲大学' if i<3 else '乙研究所','name':f'计算机方向{i}',
                          'code':'081200','department':'计算机学院','direction':f'{i:02d}人工智能',
                          'subjects':[{'code':'408','name':'计算机学科专业基础','scope':'national','exam_unit':4}],
                          'tuition':None,'requires_review':True} for i in range(5)]
        self.admissions = {'metadata':{'reference_year':2027,'target_year':2028},
            'programs':self.programs,'documents':[{'id':'d','unit_id':'a','year':2027,'kind':'catalog',
                'url':'https://example.edu.cn/catalog.pdf','sha256':'a'*64,
                'local_path':'data/snapshots/catalog.pdf'}]}
        self.coverage = {'program_count':5,'release_ready':False,'unit_catalog_status':[
            {'unit_id':'a','status':'verified','verified':True},
            {'unit_id':'b','status':'partial','verified':False}]}

    def sync(self, revision='revision-1'):
        return self.index.sync(self.registry,self.admissions,self.coverage,revision)

    def test_publication_retains_raw_json_and_queries_only_one_page(self):
        before = deepcopy((self.registry,self.admissions,self.coverage))
        result = self.sync()
        self.assertEqual(result['program_count'],5)
        page = self.index.query_programs(year=2027,offset=1,limit=2)
        self.assertEqual([p['id'] for p in page['programs']],['p1','p2'])
        self.assertEqual(page['total'],5)
        self.assertEqual(page['revision'],'revision-1')
        self.assertEqual((self.registry,self.admissions,self.coverage),before)
        page['programs'][0]['name']='modified'
        self.assertEqual(self.index.get_program('p1')['name'],'计算机方向1')

    def test_unit_year_query_and_subject_search_are_parameterized(self):
        self.sync()
        page=self.index.query_programs(unit_id='b',year=2027,q='408',limit=10)
        self.assertEqual(page['total'],2)
        self.assertTrue(all(p['unit_id']=='b' for p in page['programs']))
        self.assertEqual(self.index.query_programs(q='甲大学 408')['total'],3)
        self.assertEqual(self.index.query_programs(q='甲大学 计算机方向1')['total'],1)
        self.assertEqual(self.index.query_programs(q='甲大学 数学')['total'],0)
        self.assertEqual(self.index.query_programs(year=2026)['total'],0)
        self.assertEqual(self.index.query_programs(q="' OR 1=1 --")['total'],0)
        self.assertEqual(self.index.query_programs(q='%')['total'],0)
        self.assertEqual(self.index.query_programs(q='_')['total'],0)
        self.assertEqual(self.index.query_programs(unit_id="a' OR 1=1 --")['total'],0)

    def test_colloquial_and_mode_terms_use_the_published_search_text(self):
        self.programs[0].update({'study_mode': '非全日制', 'degree_type': '专业学位'})
        self.sync()
        self.assertEqual([row['id'] for row in self.index.query_programs(q='非全日制')['programs']], ['p0'])
        self.assertEqual([row['id'] for row in self.index.query_programs(q='专硕')['programs']], ['p0'])
        self.assertEqual(self.index.query_programs(q='甲大学 专硕')['total'], 1)
        self.assertEqual(self.index.query_programs(q='学硕')['total'], 0)

    def test_document_queries_and_unit_summaries_remain_scope_specific(self):
        self.sync()
        units=self.index.unit_summaries()
        self.assertEqual([u['program_count'] for u in units],[3,2])
        self.assertTrue(units[0]['verified'])
        self.assertEqual(units[1]['catalog_status'],'partial')
        published = self.index.get_document('d')
        self.assertEqual({key: published[key] for key in self.admissions['documents'][0]}, self.admissions['documents'][0])
        self.assertEqual(published['unit_name'], '甲大学')
        self.assertIsNone(self.index.get_document('missing'))
        self.assertEqual(self.index.query_documents(unit_id='b')['total'],0)
        self.assertEqual(self.index.query_documents(year=2027)['total'],1)

    def test_metadata_is_published_with_revision_and_never_claims_live_source_validation(self):
        self.sync()
        metadata=self.index.metadata()
        self.assertEqual(metadata['revision'],'revision-1')
        self.assertEqual(metadata['meta']['target_year'],2028)
        self.assertFalse(metadata['coverage']['release_ready'])
        self.assertEqual(metadata['verification_mode'],'published_snapshot')
        self.assertTrue(metadata['published_at'])

    def test_atomic_new_revision_replaces_old_rows_and_noop_does_not_rewrite(self):
        self.sync()
        stamp=self.path.stat().st_mtime_ns
        result=self.sync()
        self.assertFalse(result['updated'])
        self.assertEqual(stamp,self.path.stat().st_mtime_ns)
        self.admissions={**self.admissions,'programs':[self.programs[4]]}
        self.sync('revision-2')
        self.assertIsNone(self.index.get_program('p0'))
        self.assertEqual(self.index.query_programs()['total'],1)
        self.assertEqual(self.index.metadata()['revision'],'revision-2')

    def test_invalid_publication_keeps_last_good_revision(self):
        self.sync()
        for bad in [[self.programs[0],self.programs[0]],
                    [{**self.programs[0],'unit_id':'other'}],
                    [{**self.programs[0],'id':None}]]:
            with self.subTest(bad=bad), self.assertRaises(CatalogIndexError):
                self.index.sync(self.registry,{**self.admissions,'programs':bad},self.coverage,'bad')
            self.assertEqual(self.index.metadata()['revision'],'revision-1')
            self.assertEqual(self.index.query_programs()['total'],5)

    def test_read_only_queries_never_create_a_missing_database(self):
        with self.assertRaises(CatalogIndexError):
            self.index.query_programs()
        self.assertFalse(self.path.exists())
        self.sync()
        with self.index.read_connection() as connection:
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute('DELETE FROM programs')

    def test_input_limits_reject_bools_overflow_and_wrong_types(self):
        self.sync()
        for kwargs in [{'offset':-1},{'offset':True},{'limit':0},{'limit':201},
                       {'year':True},{'year':'2027'},{'unit_id':[]},{'q':[]},{'q':'x'*201}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(CatalogIndexError):
                self.index.query_programs(**kwargs)
        for revision in ['',None,'x'*129]:
            with self.assertRaises(CatalogIndexError):
                self.sync(revision)
        self.assertIsNone(self.index.get_program('missing'))

    def test_published_year_types_are_consistent_between_payload_and_sql_filters(self):
        self.sync()
        for year in ['2027',True,2027.0,1999,2101,None]:
            with self.subTest(year=year), self.assertRaises(CatalogIndexError):
                self.index.sync(self.registry,{**self.admissions,'programs':[{**self.programs[0],'year':year}]},self.coverage,'bad-year')
        self.assertEqual(self.index.metadata()['revision'],'revision-1')

    def test_concurrent_publication_cannot_mix_old_revision_with_new_page(self):
        self.sync()
        original = self.index.read_connection
        published = []
        index = self.index
        source = self.admissions
        registry, coverage = self.registry, self.coverage
        class Cursor:
            def __init__(self, cursor): self.cursor=cursor
            def fetchone(self):
                row=self.cursor.fetchone()
                self.cursor.close()
                if not published:
                    published.append(True)
                    index.sync(registry,{**source,'programs':[source['programs'][4]]},coverage,'revision-2')
                return row
        class Connection:
            def __init__(self, connection): self.connection=connection
            def execute(self, sql, *args):
                cursor=self.connection.execute(sql,*args)
                return Cursor(cursor) if sql.startswith('SELECT revision') else cursor
        @contextmanager
        def watched():
            with original() as connection:
                yield Connection(connection)
        with patch.object(self.index,'read_connection',watched):
            result=self.index.query_programs(limit=20)
        self.assertEqual(result['revision'],'revision-1')
        self.assertEqual(result['total'],5)
        self.assertEqual(len(result['programs']),5)
        self.assertEqual(self.index.query_programs()['total'],1)

    def test_light_bootstrap_returns_summary_without_all_program_payloads(self):
        self.sync()
        result=self.index.bootstrap()
        self.assertEqual(result['revision'],'revision-1')
        self.assertEqual(len(result['units']),2)
        self.assertNotIn('programs',result)
        self.assertEqual(sum(u['program_count'] for u in result['units']),5)

    def test_scope_and_program_bundle_include_published_history_from_one_revision(self):
        for kind in ['score_lines','retest_rules','transfer_rules','notices','admitted_statistics']:
            self.admissions[kind] = [{'id':kind+'-a','unit_id':'a','year':2026,'value':'真实发布值'},
                                     {'id':kind+'-b','unit_id':'b','year':2025,'value':'其他单位'}]
        self.registry['metadata'] = {'unit_count':2}
        self.sync()
        snapshot=self.index.scope_snapshot(['a'],revision='revision-1')
        self.assertEqual(snapshot['revision'],'revision-1')
        self.assertEqual(snapshot['registry_metadata'],{'unit_count':2})
        self.assertEqual([row['id'] for row in snapshot['units']],['a'])
        self.assertEqual([row['id'] for row in snapshot['programs']],['p0','p1','p2'])
        self.assertEqual([row['id'] for row in snapshot['documents']],['d'])
        for kind in ['score_lines','retest_rules','transfer_rules','notices','admitted_statistics']:
            self.assertEqual([row['id'] for row in snapshot[kind]],[kind+'-a'])
        bundle=self.index.program_bundle('p1',revision='revision-1')
        self.assertEqual(bundle['program']['id'],'p1')
        self.assertEqual([row['id'] for row in bundle['programs']],['p1'])
        self.assertEqual(bundle['score_lines'],snapshot['score_lines'])
        missing=self.index.program_bundle('missing')
        self.assertIsNone(missing['program'])
        self.assertEqual(missing['units'],[])
        self.assertEqual(self.index.scope_snapshot(['unknown'])['programs'],[])

    def test_published_subjects_have_scope_year_exam_unit_and_distinct_program_counts(self):
        self.admissions['programs']=[
            {**self.programs[0],'subjects':[{'code':'408','name':'计算机学科专业基础','scope':'national','exam_unit':4},
                                         {'code':'801','name':'专业基础','scope':'a','exam_unit':3}]},
            {**self.programs[1],'subjects':[{'code':'408','name':'计算机学科专业基础','scope':'national','exam_unit':4}]},
            {**self.programs[3],'year':2026,'subjects':[{'code':'801','name':'另一校自命题','scope':'b','exam_unit':4}]}]
        self.sync()
        all_subjects=self.index.query_subjects(limit=10)
        self.assertEqual(all_subjects['total'],3)
        national=next(row for row in all_subjects['subjects'] if row['scope']=='national')
        self.assertEqual(national['program_count'],2)
        self.assertEqual(national['exam_units'],[4])
        scoped=self.index.query_subjects(unit_id='a',year=2027,q='801',exam_unit=3)
        self.assertEqual(scoped['total'],1)
        self.assertEqual(scoped['subjects'][0]['scope'],'a')
        self.assertEqual(self.index.query_subjects(unit_id='a',year=2026)['total'],0)
        self.assertEqual(self.index.query_subjects(q="' OR 1=1 --")['total'],0)
        self.assertEqual(self.index.query_subjects(q='%')['total'],0)
        self.assertEqual(self.index.query_subjects(q='_')['total'],0)
        self.assertEqual(self.index.query_subjects(unit_id='b',q='801')['subjects'][0]['scope'],'b')
        with self.index.read_connection() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM program_subjects').fetchone()[0],4)

    def test_unsupported_or_disputed_exam_rows_remain_visible_but_never_enter_subject_selectors(self):
        programs = [{**self.programs[0], 'exam_matching_supported': False},
                    {**self.programs[1], 'subject_identity_issues': [{'code': '301', 'name': '数学（二）'}]}]
        self.index.sync(self.registry, {**self.admissions, 'programs': programs}, self.coverage, 'disputed')
        self.assertEqual(self.index.query_subjects()['total'], 0)
        self.assertEqual(self.index.program_bundle(programs[0]['id'])['program']['subjects'], programs[0]['subjects'])
        self.assertEqual(self.index.program_bundle(programs[1]['id'])['program']['subject_identity_issues'], programs[1]['subject_identity_issues'])
        with self.index.read_connection() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM program_subjects').fetchone()[0], 0)

    def test_scope_limits_and_expected_revision_fail_without_creating_an_index(self):
        for unit_ids in [None,'a',[],[None],['a']*2001,['x'*101]]:
            with self.subTest(unit_ids=unit_ids),self.assertRaises(CatalogIndexError):
                self.index.scope_snapshot(unit_ids)
        self.assertFalse(self.path.exists())
        self.sync()
        calls=[lambda:self.index.scope_snapshot(['a'],revision='old'),
               lambda:self.index.program_bundle('p0',revision='old'),
               lambda:self.index.query_programs(revision='old'),
               lambda:self.index.query_documents(revision='old'),
               lambda:self.index.query_subjects(revision='old')]
        for call in calls:
            with self.assertRaises(CatalogIndexError) as caught: call()
            self.assertEqual(caught.exception.code,'revision_changed')
        for kwargs in [{'exam_unit':True},{'exam_unit':5},{'limit':201},{'q':[]},{'scope':[]},{'code':[]},{'year':True}]:
            with self.subTest(kwargs=kwargs),self.assertRaises(CatalogIndexError):
                self.index.query_subjects(**kwargs)

    def test_history_meta_and_subject_type_validation_preserve_the_last_publication(self):
        self.sync()
        bad_values=[{'metadata':{'reference_year':True}}, {'metadata':[]},
                    {'score_lines':[{'id':'score','unit_id':'a','year':'2026'}]},
                    {'retest_rules':[{'id':'rule','unit_id':'other','year':2026}]},
                    {'notices':[{'id':'notice','unit_id':'a','year':None}]},
                    {'admitted_statistics':['bad']},
                    {'programs':[{**self.programs[0],'subjects':[{'code':'408','name':'基础','scope':'national','exam_unit':True}]}]},
                    {'programs':[{**self.programs[0],'subjects':'bad'}]},
                    {'documents':[{**self.admissions['documents'][0],'kind':None}]}]
        for mutation in bad_values:
            with self.subTest(mutation=mutation),self.assertRaises(CatalogIndexError):
                self.index.sync(self.registry,{**self.admissions,**mutation},self.coverage,'bad')
            self.assertEqual(self.index.metadata()['revision'],'revision-1')

    def test_subject_counts_deduplicate_the_same_program_across_exam_units(self):
        subject={'code':'801','name':'专业综合','scope':'a','exam_unit':3}
        self.admissions['programs']=[{**self.programs[0],'subjects':[subject,{**subject,'exam_unit':4}]}]
        self.sync()
        result=self.index.query_subjects(code='801',scope='a')['subjects'][0]
        self.assertEqual(result['program_count'],1)
        self.assertEqual(result['exam_units'],[3,4])
        for slot in [3,4]:
            row=self.index.query_subjects(exam_unit=slot)['subjects'][0]
            self.assertEqual(row['program_count'],1)
            self.assertEqual(row['exam_units'],[slot])
        self.assertEqual(self.index.query_subjects(scope="a' OR 1=1 --")['total'],0)
        self.assertEqual(self.index.scope_snapshot(["a') OR 1=1 --"])['units'],[])

    def test_subject_pagination_uses_the_precomputed_scope_index(self):
        self.admissions['programs']=[{**self.programs[0],'subjects':[
            {'code':str(800+i),'name':'科目'+str(i),'scope':'a','exam_unit':4} for i in range(5)]}]
        self.sync()
        page=self.index.query_subjects(unit_id='a',year=2027,offset=1,limit=2)
        self.assertEqual([row['code'] for row in page['subjects']],['801','802'])
        self.assertEqual(page['total'],5)
        with self.index.read_connection() as connection:
            plan=connection.execute('EXPLAIN QUERY PLAN SELECT payload FROM subject_catalog WHERE unit_filter=? AND year_filter=? AND exam_filter=? ORDER BY ordinal',('a',2027,0)).fetchall()
        self.assertTrue(any('USING INDEX subject_catalog_scope' in row[-1] for row in plan),plan)

    def test_atomic_scope_bundle_and_subjects_cannot_mix_new_rows_with_old_metadata(self):
        self.admissions['score_lines']=[{'id':'old-score','unit_id':'a','year':2026}]
        for query in [lambda:self.index.scope_snapshot(['a']), lambda:self.index.program_bundle('p0'),
                      lambda:self.index.query_subjects(unit_id='a')]:
            self.sync('old')
            original=self.index.read_connection
            switched=[]
            index=self.index
            new={**self.admissions,'programs':[self.programs[4]],'score_lines':[]}
            class Cursor:
                def __init__(self,cursor): self.cursor=cursor
                def fetchone(inner):
                    row=inner.cursor.fetchone(); inner.cursor.close()
                    if not switched:
                        switched.append(True)
                        index.sync(self.registry,new,self.coverage,'new')
                    return row
            class Connection:
                def __init__(self,connection): self.connection=connection
                def execute(self,sql,*args):
                    cursor=self.connection.execute(sql,*args)
                    return Cursor(cursor) if sql.startswith('SELECT revision') else cursor
            @contextmanager
            def watched():
                with original() as connection: yield Connection(connection)
            with patch.object(self.index,'read_connection',watched): result=query()
            self.assertEqual(result['revision'],'old')
            if 'subjects' in result:
                self.assertEqual(result['subjects'][0]['program_count'],3)
            else:
                self.assertEqual(result['score_lines'][0]['id'],'old-score')
                self.assertTrue(result['programs'])
            self.assertEqual(self.index.metadata()['revision'],'new')

    def test_explicit_same_revision_sync_upgrades_the_schema_without_implicit_read_writes(self):
        self.sync()
        with closing(sqlite3.connect(self.path)) as connection:
            publication=self.index.metadata()
            publication.pop('schema_version')
            connection.execute('UPDATE publication SET payload=?',(json.dumps(publication),))
            connection.execute('DROP TABLE records')
            connection.execute('DROP TABLE program_subjects')
            connection.execute('DROP TABLE subject_catalog')
            connection.commit()
        with self.assertRaises(CatalogIndexError): self.index.query_subjects()
        self.assertTrue(self.sync()['updated'])
        self.assertEqual(self.index.query_subjects()['total'],1)
        self.assertFalse(self.sync()['updated'])

    def test_history_collection_identity_is_per_kind_and_all_years_are_retained(self):
        self.admissions['score_lines']=[{'id':'shared','unit_id':'a','year':2024}, {'id':'other','unit_id':'a','year':2026}]
        self.admissions['notices']=[{'id':'shared','unit_id':'a','year':2027}]
        self.sync()
        scope=self.index.scope_snapshot(['a','a'])
        self.assertEqual([row['year'] for row in scope['score_lines']],[2024,2026])
        self.assertEqual(len(scope['notices']),1)
        bad={**self.admissions,'notices':self.admissions['notices']*2}
        with self.assertRaises(CatalogIndexError): self.index.sync(self.registry,bad,self.coverage,'bad')

    def test_invalid_coverage_types_cannot_publish_or_replace_the_previous_revision(self):
        self.sync()
        for mutation in [{'unit_catalog_status':None},{'unit_catalog_status':[{'unit_id':'unknown'}]},
                         {'unit_catalog_status':self.coverage['unit_catalog_status']*2},
                         {'unit_catalog_status':[{'unit_id':'a','verified':'true'}]}, {'release_ready':1}]:
            with self.subTest(mutation=mutation),self.assertRaises(CatalogIndexError):
                self.index.sync(self.registry,self.admissions,{**self.coverage,**mutation},'bad')
            self.assertEqual(self.index.metadata()['revision'],'revision-1')

    def test_source_search_paginates_published_metadata_without_claiming_original_readback(self):
        first=self.admissions['documents'][0]
        self.admissions['documents']=[{**first,'id':'d'+str(i),'year':2027 if i<3 else 2026,
            'kind':'score' if i%2 else 'catalog','title':'分数线' if i%2 else '专业目录',
            'url':'https://example.edu.cn/source'+str(i)} for i in range(4)]
        self.sync()
        result=self.index.query_sources(limit=2)
        self.assertEqual(result['total'],4)
        self.assertEqual(len(result['documents']),2)
        self.assertEqual(result['summary']['document_count'],4)
        self.assertEqual(result['summary']['published_count'],4)
        selected=self.index.query_sources(unit_id='a',year=2027,q='分数线',kind='score')
        self.assertEqual(selected['total'],1)
        self.assertEqual(selected['documents'][0]['id'],'d1')
        self.assertNotIn('snapshot_verified',selected['documents'][0])
        for q in ["' OR 1=1 --",'%', '_']:
            self.assertEqual(self.index.query_sources(q=q)['total'],0)
        self.assertEqual(self.index.query_sources(unit_id='b')['total'],0)
        self.assertEqual(self.index.query_sources(kind="catalog' OR 1=1 --")['total'],0)
        with self.assertRaises(CatalogIndexError): self.index.query_sources(kind=[])
        with self.assertRaises(CatalogIndexError) as caught: self.index.query_sources(revision='old')
        self.assertEqual(caught.exception.code,'revision_changed')

    def test_published_sources_derive_actual_fields_and_ignore_forged_or_foreign_relations(self):
        first = self.programs[0]
        self.admissions['programs'] = [{**first,
            'evidence': [{'document_id': 'd'}], 'exam_evidence': {'document_id': 'd'},
            'tuition': {'amount': 8000, 'evidence': [{'document_id': 'fees'}, {'document_id': 'fees'}]},
            'duration_evidence': {'document_id': 'fees'},
            'planned_enrollment': {'evidence': {'document_id': 'plan'}},
            'conditions': [{'evidence': {'document_id': 'plan'}}]},
            {**self.programs[1], 'tuition': {'evidence': [{'document_id': 'foreign'}, {'document_id': 'old'}]}},
            self.programs[3]]
        base = self.admissions['documents'][0]
        self.admissions['documents'] += [
            {**base, 'id': 'fees', 'kind': 'fees', 'title': '收费', 'related_program_ids': ['forged'],
             'related_claims': [{'program_id': 'forged', 'claim_id': 'catalog'}], 'unit_name': '伪造学院'},
            {**base, 'id': 'plan', 'kind': 'enrollment'},
            {**base, 'id': 'foreign', 'unit_id': 'b', 'kind': 'fees', 'related_program_ids': ['p1']},
            {**base, 'id': 'old', 'year': 2026, 'kind': 'fees'}]
        before = deepcopy(self.admissions)
        self.sync()
        sources = {row['id']: row for row in self.index.query_sources(limit=20)['documents']}
        self.assertEqual(sources['fees']['unit_name'], '甲大学')
        self.assertEqual(sources['fees']['related_program_ids'], ['p0'])
        self.assertEqual(sources['fees']['related_claims'], [
            {'program_id': 'p0', 'claim_id': 'tuition', 'label': '学费与计费口径'},
            {'program_id': 'p0', 'claim_id': 'duration', 'label': '学制'}])
        self.assertEqual({row['claim_id'] for row in sources['d']['related_claims']}, {'catalog', 'subjects'})
        self.assertEqual({row['claim_id'] for row in sources['plan']['related_claims']}, {'enrollment', 'eligibility'})
        self.assertEqual(sources['foreign'].get('related_program_ids', []), [])
        self.assertEqual(sources['old'].get('related_program_ids', []), [])
        self.assertEqual(self.admissions, before)
        selected = self.index.query_sources(unit_id='a', year=2027, kind='fees', q='收费')
        self.assertEqual(selected['documents'][0]['related_program_ids'], ['p0'])

    def test_published_historical_sources_link_only_matching_program_and_true_historical_year(self):
        program = {**self.programs[0], 'study_mode': '全日制', 'degree_type': '学术学位'}
        self.admissions['programs'] = [program, {**program, 'id': 'other', 'code': '085400'}]
        base = self.admissions['documents'][0]
        self.admissions['documents'] += [
            {**base, 'id': 'history', 'year': 2026, 'kind': 'admission_results'},
            {**base, 'id': 'score', 'year': 2025, 'kind': 'score'},
            {**base, 'id': 'dependency', 'kind': 'charter'}]
        self.admissions['documents'][0] = {**base, 'applicability_evidence': {'document_id': 'dependency'}}
        self.admissions['programs'][0] = {**program, 'evidence': {'document_id': 'd'}}
        self.admissions['admitted_statistics'] = [{'id': 'cohort', 'unit_id': 'a', 'year': 2026,
            'program_code': '081200', 'department': program['department'], 'study_mode': '全日制',
            'evidence': [{'document_id': 'history'}, {'document_id': 'missing'}]}]
        self.admissions['score_lines'] = [{'id': 'cutoff', 'unit_id': 'a', 'year': 2025,
            'program_code': '081200', 'evidence': [{'document_id': 'score'}]}]
        self.sync()
        sources = {row['id']: row for row in self.index.query_sources(limit=20)['documents']}
        self.assertEqual(sources['history']['related_program_ids'], ['p0'])
        self.assertEqual(sources['history']['related_claims'][0]['claim_id'], 'history:cohort')
        self.assertEqual(sources['score']['related_program_ids'], ['p0'])
        self.assertEqual(sources['score']['related_claims'][0]['claim_id'], 'score:cutoff')
        self.assertEqual(sources['dependency']['related_program_ids'], ['p0'])

    def test_source_edges_reject_wrong_explicit_scope_and_refresh_after_program_removal(self):
        base = self.admissions['documents'][0]
        self.admissions['documents'] += [{**base, 'id': 'fees', 'kind': 'fees'}]
        self.admissions['programs'] = [{**self.programs[0], 'conditions': [None],
            'evidence_conflicts': {'tuition': {'evidence': {'document_id': 'fees'}}}},
            {**self.programs[1], 'conditions': None, 'evidence_conflicts': 'invalid',
             'planned_enrollment': 1, 'tuition': {'evidence': {'document_id': 'fees', 'scope': {'code': '999999'}}}}]
        self.sync()
        self.assertEqual(self.index.query_sources(kind='fees')['documents'][0]['related_program_ids'], ['p0'])
        self.admissions['programs'] = [self.admissions['programs'][1]]
        self.sync('revision-2')
        source = self.index.query_sources(kind='fees')['documents'][0]
        self.assertEqual(source.get('related_program_ids', []), [])
        self.assertEqual(source.get('related_claims', []), [])

    def test_source_search_columns_upgrade_inside_an_explicit_old_schema_publication(self):
        self.path.parent.mkdir(parents=True)
        with closing(sqlite3.connect(self.path)) as connection:
            connection.executescript('CREATE TABLE publication(singleton INTEGER PRIMARY KEY,revision TEXT,payload TEXT);'
                'CREATE TABLE documents(document_id TEXT PRIMARY KEY,unit_id TEXT,year INTEGER,ordinal INTEGER,payload TEXT);')
            connection.execute('INSERT INTO publication VALUES(1,?,?)',('revision-1',json.dumps({'revision':'revision-1','schema_version':2})))
            connection.commit()
        with self.assertRaises(CatalogIndexError): self.index.query_sources(q='目录')
        self.assertTrue(self.sync()['updated'])
        self.assertEqual(self.index.query_sources(kind='catalog')['total'],1)
        with self.index.read_connection() as connection:
            columns={row[1] for row in connection.execute('PRAGMA table_info(documents)')}
        self.assertTrue({'kind','search_text'} <= columns)


if __name__=='__main__':
    unittest.main()
