"""Counterfactual scope and live evidence trust stay valid across a research turn."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from grad_engine import validate_profile
from research_store import ResearchError
from research_tools import ResearchTools
from tests.test_engine import SUBJECTS, program, profile, subject
from tests.test_knowledge_agent_tools import CurrentKnowledge
from tests.test_research_service import FakeAdmission, FakeSettings


class Catalog(FakeAdmission):
    def __init__(self, rows=(), *, partial=False):
        super().__init__()
        self.units += [{'id': '456', 'name': '他校', 'province': '河南',
                        'official_url': 'https://other.example.edu.cn'}]
        self.rows = deepcopy(list(rows))
        self.calls = []
        self.partial = partial

    def catalog_programs(self, **query):
        self.calls.append(deepcopy(query))
        rows = [row for row in self.rows if row['year'] == query['year']
                and (not query.get('unit_id') or row['unit_id'] == query['unit_id'])
                and query['q'] in row['name']]
        offset, limit = query['offset'], query['limit']
        return {'programs': deepcopy(rows[offset:offset + limit]),
                'total': len(rows) + int(self.partial), 'revision': 'fixture-r1'}

    def catalog_program(self, identifier, revision=None):
        row = next(row for row in self.rows if row['id'] == identifier)
        return {'program': deepcopy(row), 'revision': 'fixture-r1'}


def candidate(identifier, **options):
    return program(identifier, **{'unit_id': '123', 'unit_name': '测试大学', 'name': '计算机', **options})


def task(values=None, **options):
    return {'id': 'r_' + '1' * 32, 'query': '计算机', 'follow_ups': [],
            'profile': validate_profile(values or profile()), 'unit_ids': [],
            'reference_year': 2027, 'target_year': 2028, **options}


class SchoolIntentTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        admission = Catalog()
        admission.units = [{'id': 'nankai', 'name': '南开大学'},
                           {'id': 'ucas', 'name': '中国科学院大学'},
                           {'id': 'pku', 'name': '北京大学'},
                           {'id': 'pku-med', 'name': '北京大学医学部'}]
        self.tools = ResearchTools(admission, FakeSettings(), task(), Path(folder.name))

    def test_excluded_school_mentions_do_not_become_the_only_positive_intent(self):
        for query in ('我不考虑南开大学，想考计算机', '排除南开大学', '不要南开大学',
                      '南开大学我不考虑', '排除南开大学和中国科学院大学',
                      '不考虑北京大学医学部', '除了南开大学，也考虑国科大',
                      '除了南开大学，其他学校都不考虑'):
            with self.subTest(query=query):
                self.assertEqual(self.tools.infer_unit(query), '')

    def test_a_distinct_positive_school_survives_an_explicit_negative_mention(self):
        for query in ('不考虑南开大学，考虑中国科学院大学', '不考虑南开大学而考虑国科大',
                      '排除南开大学，想考国科大'):
            with self.subTest(query=query):
                self.assertEqual(self.tools.infer_unit(query), 'ucas')
        self.assertEqual(self.tools.infer_unit('不考虑北京大学医学部，考虑北京大学'), 'pku')

    def test_subject_negation_does_not_negate_a_separate_school_preference(self):
        for query in ('不想考408，考虑南开大学', '不想考408考虑南开大学'):
            with self.subTest(query=query):
                self.assertEqual(self.tools.infer_unit(query), 'nankai')
        self.assertEqual(self.tools.infer_unit('北京大学医学部临床医学'), 'pku-med')
        self.assertEqual(self.tools.infer_unit('南开大学和中国科学院大学计算机哪个好'), '')
        self.assertEqual(self.tools.infer_unit('北京大学和北京大学医学部哪个好'), '')


class CounterfactualPoolTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.data = Path(folder.name)
        self.rows = [candidate('same'), candidate('one-change',
            subjects=SUBJECTS[:3] + [subject('901', 4, '123')])]

    def tools(self, rows=None, values=None, **options):
        admission = Catalog(self.rows if rows is None else rows, **options)
        return ResearchTools(admission, FakeSettings(), task(values), self.data)

    def test_default_routes_recompute_the_full_scanned_pool_before_hard_filter_and_display_limit(self):
        rows = self.rows + [candidate('more-' + str(index), direction=str(index),
            subjects=SUBJECTS[:3] + [subject('901', 4, '123')]) for index in range(24)]
        tools = self.tools(rows, profile(max_subject_changes=0))
        tools.find_candidates({'query': '计算机'})
        self.assertEqual(list(tools.programs), ['same'])
        result = tools.routes({})
        self.assertEqual([row['coverage']['program_count'] for row in result['routes']], [1, 26, 26])
        self.assertEqual(result['research_scope']['pool_count'], 26)
        self.assertTrue(result['research_scope']['scan_complete'])
        self.assertLessEqual(len(result['routes'][1]['candidates']), 20)
        self.assertLessEqual(len(tools.programs), 12)

    def test_explicit_program_ids_remain_limited_to_currently_discovered_candidates(self):
        tools = self.tools(values=profile(max_subject_changes=0))
        tools.find_candidates({'query': '计算机'})
        result = tools.routes({'program_ids': ['same']})
        self.assertEqual([row['coverage']['program_count'] for row in result['routes']], [1, 1, 1])
        with self.assertRaises(ResearchError):
            tools.routes({'program_ids': ['one-change']})

    def test_partial_scans_are_kept_partial_in_counterfactual_results(self):
        tools = self.tools(values=profile(max_subject_changes=0), partial=True)
        found = tools.find_candidates({'query': '计算机'})
        self.assertFalse(found['scan_complete'])
        result = tools.routes({})
        self.assertIn('research_scope', result)
        self.assertFalse(result['research_scope']['scan_complete'])
        self.assertIn('部分', result['research_scope']['notice'])

    def test_pool_cannot_escape_explicit_school_year_or_query(self):
        rows = self.rows + [candidate('old', year=2026), candidate('other-topic', name='法律')]
        rows += [{**candidate('foreign'), 'unit_id': '456', 'unit_name': '他校'}]
        admission = Catalog(rows)
        tools = ResearchTools(admission, FakeSettings(),
                              task(profile(max_subject_changes=0), unit_ids=['123']), self.data)
        tools.find_candidates({'query': '计算机'})
        result = tools.routes({})
        self.assertEqual([row['coverage']['program_count'] for row in result['routes']], [1, 2, 2])
        self.assertEqual(result['research_scope']['pool_count'], 2)

    def test_adjustments_can_restore_a_scanned_direction_excluded_by_current_budget(self):
        rows = [candidate('cheap', tuition={'amount': 8000, 'period': 'year'}),
                candidate('expensive', tuition={'amount': 12000, 'period': 'year'})]
        tools = self.tools(rows, profile(max_annual_tuition=8000))
        tools.find_candidates({'query': '计算机'})
        self.assertEqual(list(tools.programs), ['cheap'])
        result = tools.adjustments({'allowed_changes': {'max_annual_tuition': [12000]}})
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['suggestions'][0]['added_ids'], ['expensive'])

    def test_allowed_region_change_requeries_the_same_published_query_only_in_allowed_schools(self):
        rows = [candidate('local'), {**candidate('remote'), 'unit_id': '456', 'province': '河南'}]
        tools = self.tools(rows, profile(provinces=['天津']))
        tools.find_candidates({'query': '计算机'})
        self.assertEqual(list(tools.programs), ['local'])
        result = tools.adjustments({'allowed_changes': {'provinces': [['天津', '河南']]}})
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['suggestions'][0]['added_ids'], ['remote'])
        self.assertEqual(list(tools.programs), ['local'], 'counterfactual work does not change current selection')
        self.assertEqual({call['unit_id'] for call in tools.admission.calls}, {'123', '456'})
        for call in tools.admission.calls:
            self.assertEqual((call['q'], call['year'], call['revision']), ('计算机', 2027, 'fixture-r1'))
        restricted = ResearchTools(Catalog(rows), FakeSettings(),
                                  task(profile(provinces=['天津']), unit_ids=['123']), self.data)
        restricted.find_candidates({'query': '计算机'})
        result = restricted.adjustments({'allowed_changes': {'provinces': [['天津', '河南']]}})
        self.assertEqual(result['suggestions'], [])
        self.assertEqual({call['unit_id'] for call in restricted.admission.calls}, {'123'})

    def test_pool_revision_is_part_of_the_automatic_algorithm_signature(self):
        tools = self.tools(values=profile(max_subject_changes=0))
        tools.find_candidates({'query': '计算机'})
        tools.ensure_algorithms()
        before = tools._algorithm_signature
        tools.admission.rows += [candidate('new-one-change', direction='new',
            subjects=SUBJECTS[:3] + [subject('901', 4, '123')])]
        tools.find_candidates({'query': '计算机'})
        self.assertEqual(list(tools.programs), ['same'])
        tools.ensure_algorithms()
        self.assertNotEqual(tools._algorithm_signature, before)
        self.assertEqual([row['coverage']['program_count'] for row in tools.algorithm['routes']['routes']], [1, 3, 3])

    def test_derived_fee_withdrawal_rebuilds_all_pool_rows_and_invalidates_old_matching(self):
        from tests.test_research_memory import evidence
        rows = [{**row, 'tuition': None} for row in self.rows]
        tools = self.tools(rows, profile(max_subject_changes=0, max_annual_tuition=10000))
        tools.find_candidates({'query': '计算机'})
        first = evidence()
        second = evidence('s_' + 'b' * 20, sha256='b' * 64,
                          url='https://grad.example.edu.cn/second.html')
        tools.sources = {first['id']: first, second['id']: second}
        before = tools.routes({})
        self.assertEqual([row['coverage']['program_count'] for row in before['routes']], [0, 0, 0])
        self.assertTrue(all(row['tuition']['amount'] == 12000 for row in tools._research_pool.values()))
        tools.algorithm['matching'] = {'STALE': True}
        tools.sources[second['id']] = {**second, 'usable': False, 'knowledge_current': False}
        after = tools.routes({})
        self.assertEqual([row['coverage']['program_count'] for row in after['routes']], [1, 2, 2])
        self.assertTrue(all(row['tuition'] is None for row in tools._research_pool.values()))
        self.assertNotIn('matching', tools.algorithm)

    def test_inherited_display_candidates_do_not_claim_the_previous_full_scan_was_restored(self):
        admission = Catalog(self.rows)
        previous = {'candidates': [{'id': 'same'}],
                    'coverage': {'selection': {'scan_complete': True, 'scanned_count': 200}}}
        tools = ResearchTools(admission, FakeSettings(),
                              task(profile(max_subject_changes=0), previous_report=previous), self.data)
        result = tools.routes({})
        self.assertFalse(result['research_scope']['scan_complete'])
        self.assertEqual(result['research_scope']['pool_count'], 1)
        self.assertIn('部分', result['research_scope']['notice'])
        self.assertEqual(admission.calls, [])

    def test_conflicting_fees_pause_both_display_and_non_display_pool_rows(self):
        from tests.test_research_memory import evidence
        tools = self.tools([{**row, 'tuition': None} for row in self.rows], profile(max_subject_changes=0))
        tools.find_candidates({'query': '计算机'})
        first = evidence()
        second = evidence('s_' + 'b' * 20, sha256='b' * 64,
                          url='https://grad.example.edu.cn/second.html',
                          excerpt='各专业学费均为15000元/年，收费标准一致。')
        tools.sources = {first['id']: first, second['id']: second}
        result = tools.routes({})
        self.assertTrue(all(row.get('requires_review') and row.get('field_conflicts')
                            for row in tools._research_pool.values()))
        self.assertTrue(all(row['status'] == 'unknown' for row in result['routes'][1]['candidates']))


class EvidenceTrustTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.data = Path(folder.name)
        self.store = CurrentKnowledge()
        self.fetcher = Mock(side_effect=AssertionError('memo replay must not fetch'))
        self.tools = ResearchTools(Catalog(), FakeSettings(), task(), self.data,
                                   knowledge_store=self.store, source_fetcher=self.fetcher)

    def cached_source(self):
        found = self.tools.search_knowledge({'query': '计算机'})
        source_id = found['results'][0]['id']
        return source_id, {'source_id': source_id, 'unit_id': '123'}

    def test_memoized_original_is_revalidated_before_replay_without_extra_requests(self):
        source_id, args = self.cached_source()
        first = self.tools.run('read_official_source', args)
        self.assertIn('CURRENT_SOURCE_CANARY', first['excerpt'])
        self.store.entry = {**self.store.entry, 'validity': 'revoked', 'usable': False}
        with self.assertRaises(ResearchError) as caught:
            self.tools.run('read_official_source', args)
        self.assertEqual(caught.exception.code, 'knowledge_not_current')
        self.assertEqual(self.tools.sources[source_id]['excerpt'], '')
        self.fetcher.assert_not_called()
        self.assertEqual(self.tools.usage['documents'], 0)

    def test_memoized_original_rejects_changed_generation_or_unavailable_store(self):
        for changed in ('generation', 'unavailable'):
            with self.subTest(changed=changed):
                self.setUp()
                _, args = self.cached_source()
                self.tools.run('read_official_source', args)
                if changed == 'generation':
                    self.store.entry['extraction'] = {**self.store.entry['extraction'],
                                                       'generation_id': 'kg_' + 'f' * 32}
                else:
                    self.store.get_failure = True
                with self.assertRaises(ResearchError) as caught:
                    self.tools.run('read_official_source', args)
                self.assertEqual(caught.exception.code, 'knowledge_not_current')
                self.fetcher.assert_not_called()

    def test_memoized_search_refreshes_knowledge_metadata_without_repeating_search(self):
        source_id, _ = self.cached_source()
        args = {'query': '计算机', 'unit_id': '123'}
        key = self.tools._memo_key('search_official_sources', args)
        self.tools._memo[key] = {'results': [deepcopy(self.tools.sources[source_id])], 'notice': 'cached'}
        self.store.entry = {**self.store.entry, 'validity': 'revoked', 'usable': False}
        self.tools.network = Mock(side_effect=AssertionError('memo replay must not search'))
        result = self.tools.run('search_official_sources', args)
        self.assertTrue(result['repeated_call'])
        self.assertEqual(result['results'][0]['excerpt'], '')
        self.assertFalse(result['results'][0]['usable'])
        self.assertFalse(result['results'][0]['knowledge_current'])
        self.assertEqual(self.tools.usage['searches'], 0)

    def test_discovery_does_not_reuse_links_of_a_revoked_cached_original(self):
        source_id, args = self.cached_source()
        self.tools.sources[source_id] = {**self.tools.sources[source_id], 'links': []}
        self.store.entry = {**self.store.entry, 'validity': 'revoked', 'usable': False}
        with self.assertRaises(ResearchError) as caught:
            self.tools.discover_links(args)
        self.assertEqual(caught.exception.code, 'knowledge_not_current')
        self.fetcher.assert_not_called()
        self.assertEqual(self.tools.usage['discoveries'], 0)

    def test_inherited_sources_are_filtered_by_current_school_and_reference_year(self):
        base = {**deepcopy(self.store.entry), 'id': 's_' + '1' * 20}
        previous = {'evidence': [base, {**base, 'id': 's_' + '2' * 20, 'unit_id': '456'},
                                  {**base, 'id': 's_' + '3' * 20, 'year': 2026}], 'candidates': []}
        tools = ResearchTools(Catalog(), FakeSettings(), task(unit_ids=['123'], previous_report=previous),
                              self.data, knowledge_store=self.store)
        self.assertEqual(list(tools.sources), ['s_' + '1' * 20])
        self.assertEqual(set(tools.inheritance['stale_source_ids']), {'s_' + '2' * 20, 's_' + '3' * 20})

    def test_inheritance_checks_authoritative_knowledge_scope_before_reuse(self):
        base = {**deepcopy(self.store.entry), 'id': 's_' + '1' * 20,
                'knowledge_id': self.store.entry['id']}
        self.store.entry = {**self.store.entry, 'year': 2026}
        tools = ResearchTools(Catalog(), FakeSettings(), task(previous_report={'evidence': [base]}),
                              self.data, knowledge_store=self.store)
        self.assertEqual(tools.sources, {})
        self.assertIn(base['id'], tools.inheritance['stale_source_ids'])

    def test_field_changes_invalidate_algorithms_but_idempotent_refresh_does_not(self):
        self.tools.programs = {'p': candidate('p')}
        self.tools.algorithm = {'matching': {'STALE': True}}
        self.tools._algorithm_signature = 'old-signature'
        def apply(programs, sources):
            for row in programs.values():
                row['tuition'] = {'amount': 20000, 'period': 'year'}
            return {'candidates': [], 'applied': [], 'paused': []}
        with patch('research_tools.research_fields.field_candidates', side_effect=apply):
            self.tools._apply_fields()
            self.assertEqual(self.tools.algorithm, {})
            self.assertIsNone(self.tools._algorithm_signature)
            self.tools.algorithm = {'matching': {'FRESH': True}}
            self.tools._algorithm_signature = 'fresh-signature'
            self.tools._apply_fields()
            self.assertIn('matching', self.tools.algorithm)
            self.assertEqual(self.tools._algorithm_signature, 'fresh-signature')


if __name__ == '__main__':
    unittest.main()
