import hashlib
import json
import os
import unittest
from unittest.mock import patch

from service import ServiceError
from tests import test_service as fixtures


class PublishedServiceTests(unittest.TestCase):
    setUp = fixtures.ServiceTests.setUp
    write = fixtures.ServiceTests.write

    def publish(self, revision='published-1'):
        from catalog_index import CatalogIndex
        registry, admissions, coverage, _ = self.service._state()
        self.index = CatalogIndex(self.data / 'runtime/catalog-index.sqlite')
        self.index.sync(registry, admissions, coverage, revision)

    def test_light_bootstrap_and_page_never_open_originals_or_full_snapshot(self):
        self.publish()
        with patch.object(self.service, '_state', side_effect=AssertionError('full snapshot forbidden')):
            with patch('service.SourceVerifier.read', side_effect=AssertionError('original read forbidden')):
                bootstrap = self.service.catalog_bootstrap()
                page = self.service.catalog_programs(unit_id='123', year=2027, limit=1)
        self.assertNotIn('programs', bootstrap)
        self.assertNotIn('documents', bootstrap)
        self.assertEqual(bootstrap['revision'], 'published-1')
        self.assertEqual(bootstrap['verification_mode'], 'published_snapshot')
        self.assertEqual(bootstrap['service']['data_revision'], 'published-1')
        self.assertEqual(sum(u['program_count'] for u in bootstrap['units']), 1)
        self.assertEqual(page['programs'][0]['id'], 'a-1')
        self.assertEqual(page['total'], 1)
        self.assertFalse(page['has_more'])
        self.assertNotIn('local_path', json.dumps(page))

    def test_light_read_requires_explicit_publication_and_never_adopts_new_json(self):
        with self.assertRaises(ServiceError) as error:
            self.service.catalog_bootstrap()
        self.assertEqual(error.exception.status, 503)
        self.publish()
        self.admissions['programs'][0]['name'] = 'not published'
        self.write()
        self.assertEqual(self.service.catalog_programs()['programs'][0]['name'], '计算机技术')

    def test_pagination_limits_and_stale_revision_are_explicit(self):
        self.publish()
        for query in [{'limit':201}, {'offset':True}, {'offset':-1}, {'year':True},
                      {'q':[]}, {'unit_id':[]}, {'revision':[]}]:
            with self.subTest(query=query), self.assertRaises(ServiceError) as error:
                self.service.catalog_programs(**query)
            self.assertEqual(error.exception.status, 400)
        with self.assertRaises(ServiceError) as error:
            self.service.catalog_programs(revision='older-publication')
        self.assertEqual(error.exception.status, 409)

    def test_single_detail_retains_history_without_exposing_storage_paths(self):
        self.admissions['score_lines'] = [{'id':'line','unit_id':'123','year':2026,'total':300}]
        self.write()
        self.publish()
        with patch.object(self.service, '_state', side_effect=AssertionError('full snapshot forbidden')):
            result = self.service.catalog_program('a-1', revision='published-1')
        self.assertEqual(result['program']['id'], 'a-1')
        self.assertEqual(result['score_lines'][0]['id'], 'line')
        self.assertEqual(result['revision'], 'published-1')
        self.assertNotIn('local_path', json.dumps(result))
        with self.assertRaises(ServiceError) as error:
            self.service.catalog_program('missing')
        self.assertEqual(error.exception.status, 404)

    def test_scoped_evidence_and_matching_do_not_load_full_json_or_unrelated_originals(self):
        self.publish()
        from service_verification import SourceVerifier
        read=SourceVerifier.read
        def selected_only(verifier,document,key='local_path'):
            self.assertNotEqual(document.get('unit_id'),'456','unrelated original must not be read')
            return read(verifier,document,key)
        with patch.object(self.service, '_state', side_effect=AssertionError('full snapshot forbidden')):
            with patch.object(SourceVerifier,'read',new=selected_only):
                evidence = self.service.evidence('a-1')
                result = self.service.match({'profile':{}, 'unit_ids':['123']})
        self.assertEqual(next(c for c in evidence['claims'] if c['id']=='subjects')['status'], 'verified')
        self.assertEqual(result['matches'][0]['program']['id'], 'a-1')
        self.assertEqual(result['revision'], 'published-1')
        self.assertTrue(result['originals_revision'])

    def test_single_evidence_replays_only_requested_direction_without_claiming_unit_completeness(self):
        from service_verification import SourceVerifier
        self.admissions['programs'].append({**self.program, 'id':'a-2'})
        self.write()
        self.publish()
        verify = SourceVerifier.program
        def selected_only(verifier, program, documents, unit):
            self.assertEqual(program['id'], 'a-1', 'another direction must not be reverified for this field drawer')
            return verify(verifier, program, documents, unit)
        with patch.object(SourceVerifier, 'program', new=selected_only), patch.object(
                SourceVerifier, 'unit_reconciles', side_effect=AssertionError('single-field evidence cannot audit the whole unit')):
            evidence = self.service.evidence('a-1')
        self.assertEqual(next(c for c in evidence['claims'] if c['id']=='subjects')['status'], 'verified')
        state = self.service._catalog.evidence_scope('a-1')
        self.assertEqual([row['id'] for row in state['admissions']['programs']], ['a-1'])
        self.assertEqual(state['coverage']['verified_unit_ids'], [])
        self.assertFalse(state['coverage']['unit_catalog_status'][0]['verified'])
        self.assertFalse(state['coverage']['catalog_complete'])

    def test_single_evidence_cache_still_detects_same_length_original_tampering_with_restored_mtime(self):
        self.publish()
        first = self.service.evidence('a-1', revision='published-1')
        self.assertEqual(next(c for c in first['claims'] if c['id']=='subjects')['status'], 'verified')
        source = self.data / self.document['local_path']
        before = source.stat()
        source.write_bytes(source.read_bytes().replace(b'408', b'409'))
        os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
        second = self.service.evidence('a-1', revision='published-1')
        self.assertEqual(next(c for c in second['claims'] if c['id']=='subjects')['status'], 'needs_review')
        self.assertNotEqual(first['originals_revision'], second['originals_revision'])

    def test_single_evidence_preserves_unknown_program_and_revision_errors(self):
        self.publish()
        for program_id, revision, status in [('missing', None, 404), ('a-1', 'older-publication', 409), ('a-1', [], 400)]:
            with self.subTest(program_id=program_id, revision=revision), self.assertRaises(ServiceError) as error:
                self.service.evidence(program_id, revision=revision)
            self.assertEqual(error.exception.status, status)

    def test_scoped_original_tampering_cannot_enter_algorithms_even_with_restored_mtime(self):
        self.publish()
        payload = {'profile':{}, 'unit_ids':['123']}
        first = self.service.match(payload)
        source = self.data / self.document['local_path']
        before = source.stat()
        raw = source.read_bytes()
        source.write_bytes(raw.replace(b'408', b'409'))
        os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
        with patch.object(self.service, '_state', side_effect=AssertionError('full snapshot forbidden')):
            second = self.service.match(payload)
        self.assertEqual(second['matches'], [])
        self.assertNotEqual(first['originals_revision'], second['originals_revision'])
        self.assertFalse(second['release_ready'])
        self.assertEqual(self.service.catalog_program('a-1')['program']['subjects'][-1]['code'], '408')

    def test_published_health_does_not_hash_national_originals(self):
        self.publish()
        with patch.object(self.service._cache,'status',side_effect=AssertionError('national hash forbidden')):
            result = self.service.health()
        self.assertEqual(result['status'],'ok')
        self.assertEqual(result['verification_mode'],'published_snapshot')
        self.assertEqual(result['data_revision'],'published-1')

    def test_unverified_fee_is_unknown_to_algorithms_and_unrelated_source_is_not_read(self):
        fee_path = self.data/'snapshots/fees.html'
        fee_path.write_text('测试甲大学2027年学费 计算机技术8000元/年','utf-8')
        fee_doc = {**self.document,'id':'fee','kind':'fees','local_path':'snapshots/fees.html',
                   'url':'https://grad.a.edu.cn/fees','sha256':hashlib.sha256(fee_path.read_bytes()).hexdigest()}
        self.admissions['documents'].append(fee_doc)
        self.admissions['programs'][0]['tuition'] = {'amount':8000,'period':'year','currency':'CNY',
            'evidence':{'document_id':'fee','url':fee_doc['url'],'quote':'计算机技术8000元/年'}}
        self.write()
        self.publish()
        payload={'profile':{},'unit_ids':['123']}
        before = self.service.match(payload)
        self.assertEqual(before['matches'][0]['program']['tuition']['amount'],8000)
        stamp=fee_path.stat()
        fee_path.write_text(fee_path.read_text('utf-8').replace('8000','9000'),'utf-8')
        os.utime(fee_path,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
        after=self.service.match(payload)
        self.assertIsNone(after['matches'][0]['program']['tuition'])

    def test_published_sources_and_subjects_are_paged_with_explicit_snapshot_status(self):
        self.publish()
        with patch.object(self.service,'_state',side_effect=AssertionError('full snapshot forbidden')):
            sources=self.service.catalog_sources(unit_id='123',limit=1,revision='published-1')
            subjects=self.service.catalog_subjects(code='408',scope='national',exam_unit=4,limit=1)
        self.assertEqual(sources['sources'][0]['verification'],'published_snapshot')
        self.assertEqual(sources['total'],1)
        self.assertEqual(sources['summary']['verified_count'],0)
        self.assertNotIn('local_path',json.dumps(sources))
        self.assertEqual(subjects['subjects'][0]['code'],'408')
        self.assertEqual(subjects['subjects'][0]['exam_units'],[4])
        for query in [{'kind':[]},{'offset':True},{'limit':201}]:
            with self.subTest(query=query),self.assertRaises(ServiceError):
                self.service.catalog_sources(**query)
        for query in [{'exam_unit':True},{'code':[]},{'scope':[]}]:
            with self.subTest(query=query),self.assertRaises(ServiceError):
                self.service.catalog_subjects(**query)

    def test_structured_catalog_format_uses_the_actual_published_detail_url(self):
        detail='https://yz.chsi.com.cn/zsml/yjfxdetail?id=public-original'
        self.admissions['documents'][0]={**self.document,'catalog_parser':'chsi_detail_v1',
            'url':detail,'detail_url':'javascript:untrusted-metadata','original_format':'invented'}
        self.admissions['documents'][1]={**self.charter,'original_format':'structured_fields',
            'detail_url':'https://untrusted.example/forged'}
        self.write()
        self.publish()
        rows=self.service.catalog_sources()['sources']
        source=next(row for row in rows if row['id']==self.document['id'])
        self.assertEqual(source['original_format'],'structured_fields')
        self.assertEqual(source['detail_url'],detail)
        self.assertEqual(source['verification'],'published_snapshot')
        ordinary=next(row for row in rows if row['id']==self.charter['id'])
        self.assertNotIn('original_format',ordinary)
        self.assertNotIn('detail_url',ordinary)

    def test_index_scope_does_not_adopt_invalid_profiles_or_unknown_units(self):
        self.publish()
        with patch.object(self.service._catalog,'scope',side_effect=AssertionError('no source work for invalid profile')):
            with self.assertRaises(ServiceError) as error:
                self.service.match({'profile':{'bad':True}})
        self.assertEqual(error.exception.code,'invalid_profile')
        for selected in [[],['missing'],{},[True]]:
            with self.subTest(selected=selected),self.assertRaises(ServiceError) as error:
                self.service.match({'profile':{},'unit_ids':selected})
            self.assertEqual(error.exception.code,'unknown_unit')

    def test_annual_not_listed_unit_cannot_be_selected_as_current_independent_candidate(self):
        self.registry['units'][0] = {**self.registry['units'][0],
            'annual_status':'not_listed','annual_year':2027}
        self.write()
        self.publish()
        value=self.service.match({'profile':{},'unit_ids':['123']})
        self.assertEqual(value['matches'],[])
        self.assertEqual(value['coverage']['annual_not_listed_unit_ids'],['123'])
        self.assertEqual(value['coverage']['unit_catalog_status'][0]['status'],'not_listed')
        self.assertIn('独立',value['coverage']['unit_catalog_status'][0]['notes'])

    def test_annual_not_listed_status_does_not_exclude_another_year(self):
        self.registry['units'][0] = {**self.registry['units'][0],
            'annual_status':'not_listed','annual_year':2026}
        self.write()
        self.publish()
        value=self.service.match({'profile':{},'unit_ids':['123']})
        self.assertEqual(value['matches'][0]['program']['id'],'a-1')
        self.assertEqual(value['coverage']['annual_not_listed_unit_ids'],[])

    def test_unresolved_exam_combinations_do_not_enter_any_planning_api_or_subject_selector(self):
        self.admissions['programs'][0]={**self.program,'exam_matching_supported':False,
            'subject_identity_issues':[{'exam_unit':3,'code':'301','name':'数学（二）','reason':'原文科目代码与名称冲突'}]}
        self.write()
        for published in (False,True):
            if published:self.publish()
            with self.subTest(published=published):
                match=self.service.match({'profile':{},'unit_ids':['123']})
                self.assertEqual(match['matches'],[])
                self.assertEqual(match['exam_unresolved_programs'][0]['program_id'],'a-1')
                self.assertIn('原文科目代码与名称冲突',match['exam_unresolved_programs'][0]['reason'])
                self.assertEqual(match['exam_unresolved_programs'][0]['url'],self.document['url'])
                self.assertTrue(any('未参与' in value for value in match['warnings']))
                self.assertEqual(self.service.subjects(unit_id='123')['subjects'],[])
                sensitivity=self.service.sensitivity({'profile':{},'unit_ids':['123'],'scenarios':[]})
                self.assertEqual(sensitivity['baseline']['candidate_ids'],[])
                routes=self.service.route_plan({'profile':{},'unit_ids':['123']})
                self.assertTrue(all(not route['candidate_ids'] for route in routes['routes']))
                adjusted=self.service.adjustments({'profile':{},'unit_ids':['123'],
                    'allowed_changes':{'max_subject_changes':[0,1]}})
                self.assertEqual(adjusted['suggestions'],[])
                self.assertEqual(len(adjusted['exam_unresolved_programs']),1)
        self.assertEqual(self.service.catalog_program('a-1')['program']['subjects'][-1]['code'],'408')

    def test_scoped_rules_and_proof_use_the_same_published_unit_without_full_snapshot(self):
        self.publish()
        with patch.object(self.service,'_state',side_effect=AssertionError('full snapshot forbidden')):
            tasks=self.service.source_tasks('a-1')
            proof=self.service.source_proof({'program_id':'a-1','kind':'fees'})
            sources=self.service.sources(unit_id='123')
        self.assertEqual(tasks['program_id'],'a-1')
        self.assertEqual(proof['status'],'not_configured')
        self.assertEqual(sources['total'],1)

    def test_missing_or_malformed_published_index_never_falls_back_to_raw_json(self):
        import sqlite3
        from contextlib import closing
        self.publish()
        with closing(sqlite3.connect(self.index.path)) as connection, connection:
            connection.execute("UPDATE publication SET payload='invalid JSON'")
        with patch.object(self.service,'_state',side_effect=AssertionError('full fallback forbidden')):
            with self.assertRaises(ServiceError) as error:
                self.service.catalog_bootstrap()
        self.assertEqual(error.exception.status,503)

    def test_evidence_can_be_bound_to_the_same_published_revision_as_the_page(self):
        self.publish()
        value=self.service.evidence('a-1',revision='published-1')
        self.assertEqual(value['revision'],'published-1')
        self.assertTrue(value['originals_revision'])
        with self.assertRaises(ServiceError) as error:
            self.service.evidence('a-1',revision='outdated')
        self.assertEqual(error.exception.status,409)

    def test_index_backed_radar_watch_and_check_do_not_load_national_json(self):
        from unittest.mock import Mock
        self.publish()
        self.service.radar.fetcher=Mock(return_value={'raw':(self.data/self.document['local_path']).read_bytes(),
            'url':self.document['url'],'content_type':'text/html'})
        with patch.object(self.service,'_state',side_effect=AssertionError('full snapshot forbidden')):
            watched=self.service.radar_watch({'program_ids':['a-1']})
            filtered=self.service.radar_status(unit_id='123')
            checked=self.service.radar_check({})
            cleared=self.service.radar_watch({'program_ids':[]})
        self.assertEqual(watched['watch_count'],1)
        self.assertEqual(filtered['program_ids'],['a-1'])
        self.assertEqual(checked['check_state'],'complete')
        self.assertEqual(cleared['watch_count'],0)

    def test_historical_rules_read_only_their_published_rows_and_original_evidence(self):
        path=self.data/'snapshots/transfer.html'
        quote='初试总分不低于300分'
        path.write_text('<h1>测试甲大学2026年调剂公告</h1><p>'+quote+'</p>','utf-8')
        document={**self.document,'id':'transfer','year':2026,'kind':'transfer','local_path':'snapshots/transfer.html',
                  'url':'https://grad.a.edu.cn/transfer','sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        self.admissions['documents'].append(document)
        self.admissions['transfer_rules']=[{'id':'rule','unit_id':'123','unit_name':'测试甲大学','year':2026,
            'requirements':[],'requires_review':True,'evidence':{'document_id':'transfer','url':document['url'],'quote':quote}}]
        self.write()
        self.publish()
        with patch.object(self.service,'_state',side_effect=AssertionError('full snapshot forbidden')):
            result=self.service.history_match({'kind':'transfer','year':2026,'facts':{}})
        self.assertEqual(result['matched'][0]['id'],'rule')
        self.assertEqual(result['revision'],'published-1')
        stamp=path.stat()
        path.write_text(path.read_text('utf-8').replace('300','301'),'utf-8')
        os.utime(path,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
        with patch.object(self.service,'_state',side_effect=AssertionError('full snapshot forbidden')):
            result=self.service.history_match({'kind':'transfer','year':2026,'facts':{}})
        self.assertEqual(result['matched'],[])
        self.assertEqual(result['data_status'],'needs_review')


if __name__ == '__main__':
    unittest.main()
