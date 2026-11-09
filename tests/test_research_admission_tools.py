"""Strict admission facts retain their read proofs throughout their lifetime."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from grad_engine import match_programs, validate_profile
from research_store import ResearchError
import research_candidate_tools
import research_admission_tools as facts


PREFIX = '081200计算机科学与技术全日制'
UNIT = {'id': 'u1', 'name': '测试大学', 'official_url': 'https://grad.example.edu.cn/'}


def program(identifier='p1', **changes):
    return {'id': identifier, 'unit_id': 'u1', 'unit_name': '测试大学', 'year': 2027,
            'code': '081200', 'name': '计算机科学与技术', 'study_mode': '全日制',
            'degree_type': '学术学位', 'tuition': None, 'duration_years': None,
            'conditions': [], 'eligibility_evidence_complete': False, 'notes': [],
            'evidence': [{'url': UNIT['official_url'], 'title': '招生目录', 'year': 2027}],
            'subjects': [{'code': '408', 'name': '计算机学科专业基础', 'scope': 'national'}],
            **changes}


class Store:
    def __init__(self):
        self.entries, self.blocks = {}, {}

    def get(self, entry_id):
        return deepcopy(self.entries[entry_id])

    def read_segment(self, segment_id, **kwargs):
        block = deepcopy(self.blocks[segment_id])
        return {'segment': {**block, 'source': self.get(block['entry_id'])},
                'contexts': [], 'neighbors': [], 'truncated': False}

    def search_segments(self, **kwargs):
        rows = [{**deepcopy(block), 'source': self.get(block['entry_id'])}
                for block in self.blocks.values() if kwargs.get('query', '') in block['text']]
        return {'segments': rows, 'total': len(rows)}


class Tools:
    def __init__(self):
        self.task = {'id': 'r_' + '1' * 32, 'reference_year': 2027, 'target_year': 2028,
                     'unit_ids': ['u1'], 'profile': validate_profile({})}
        self.units = {'u1': deepcopy(UNIT)}
        self.programs = {'p1': program()}
        self._research_pool = {'p1': program(), 'p2': program('p2')}
        self._research_pool_version = 0
        self.algorithm = {'cached': True}
        self._algorithm_signature = 'old'
        self.sources, self.gaps = {}, []
        self.store = Store()
        research_candidate_tools.initialize(self)
        facts.initialize(self)

    def _unit(self, identifier):
        if identifier not in self.units:
            raise ResearchError('unknown_unit', '学校未知')
        return self.units[identifier]

    def _knowledge(self):
        return self.store

    def refresh_knowledge(self):
        return None

    def _candidate(self, row):
        return {key: deepcopy(value) for key, value in row.items() if not key.startswith('_')}

    def read(self, text, field='tuition', *, delivered=True, index=None):
        number = len(self.store.entries) + 1 if index is None else index
        entry_id, source_id, segment_id = ('k_' + f'{number:024x}', 's_' + f'{number:020x}',
                                          'sg_' + f'{number:032x}')
        entry = {'id': entry_id, 'unit_id': 'u1', 'unit_name': '测试大学', 'year': 2027,
                 'title': '2027年硕士研究生招生章程', 'url': f'https://grad.example.edu.cn/{number}.html',
                 'sha256': hashlib.sha256((str(number) + text).encode()).hexdigest(),
                 'status': 'official_original_read', 'original_available': True, 'usable': True,
                 'quality': {'version': 1, 'state': 'accepted', 'reusable': True,
                             'material_type': 'admissions', 'reasons': []}, 'validity': 'active',
                 'extraction': {'id': 'ex_' + f'{number:032x}',
                                'generation_id': 'kg_' + f'{number:032x}', 'extraction_status': 'complete'}}
        block = {'id': segment_id, 'entry_id': entry_id, 'extraction_id': entry['extraction']['id'],
                 'generation_id': entry['extraction']['generation_id'], 'kind': 'paragraph',
                 'char_start': 200, 'char_end': 200 + len(text), 'text': text,
                 'row_complete': True, 'continues_previous': False, 'continues_next': False,
                 'content_truncated': False, 'context_spans': []}
        self.store.entries[entry_id], self.store.blocks[segment_id] = entry, block
        self.sources[source_id] = {**deepcopy(entry), 'id': source_id, 'knowledge_id': entry_id,
                                   'knowledge_current': True}
        payload = self.store.read_segment(segment_id)
        payload['segment']['source_id'] = source_id
        research_candidate_tools.register_read(self, payload, 0, 0, 6000)
        if delivered:
            research_candidate_tools.confirm_delivery(self, payload, payload)
        return {'field': field, 'citation': {'segment_id': segment_id, 'char_start': 200,
                                            'char_end': 200 + len(text), 'quote': text}}


class AdmissionFactIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tools = Tools()

    def propose(self, text, field='tuition', **kwargs):
        draft = self.tools.read(text, field, **kwargs)
        return facts.propose(self.tools, {'program_id': 'p1', 'drafts': [draft]})

    def test_one_confirmed_original_reaches_selected_and_full_pool(self):
        result = self.propose(PREFIX + '学费为12000元/年。')
        self.assertEqual(result['accepted_count'], 1, result)
        for row in (self.tools.programs['p1'], self.tools._research_pool['p1']):
            self.assertEqual(row['tuition']['amount'], 12000)
            self.assertEqual(row['tuition']['verified_by'], 'verified_single_source')
            self.assertTrue(row['tuition']['evidence'][0]['quote'])
        self.assertIsNone(self.tools._research_pool['p2']['tuition'])
        self.assertEqual(self.tools.algorithm, {})
        self.assertIsNone(self.tools._algorithm_signature)
        self.assertGreater(self.tools._research_pool_version, 0)

    def test_search_preview_pending_delivery_and_malformed_requests_never_authorize(self):
        draft = self.tools.read(PREFIX + '学费为12000元/年。', delivered=False)
        result = facts.propose(self.tools, {'program_id': 'p1', 'drafts': [draft]})
        self.assertEqual(result['accepted_count'], 0)
        self.assertIsNone(self.tools.programs['p1']['tuition'])
        for args in ({'program_id': 'unknown', 'drafts': [draft]},
                     {'program_id': 'p1', 'drafts': [draft] * 7},
                     {'program_id': 'p1', 'drafts': [draft], 'value': 0}):
            with self.subTest(args=args), self.assertRaises(ResearchError):
                facts.propose(self.tools, args)

    def test_repeated_refresh_and_proposal_are_stable(self):
        draft = self.tools.read(PREFIX + '学费为12000元/年。')
        args = {'program_id': 'p1', 'drafts': [draft]}
        facts.propose(self.tools, args)
        before = deepcopy((self.tools.programs, self.tools._research_pool))
        version = self.tools._research_pool_version
        self.tools.algorithm = {'stable': True}
        facts.restore(self.tools)
        facts.refresh(self.tools)
        facts.propose(self.tools, args)
        self.assertEqual((self.tools.programs, self.tools._research_pool), before)
        self.assertEqual(self.tools._research_pool_version, version)
        self.assertEqual(self.tools.algorithm, {'stable': True})
        self.assertEqual(len(facts.snapshot(self.tools)['facts']), 1)

    def test_revoked_original_restores_base_and_dependent_total_is_unknown(self):
        self.propose(PREFIX + '学费为12000元/年。')
        self.propose(PREFIX + '学制为3年。', 'duration_years')
        profile = validate_profile({'max_total_tuition': 35000})
        result = match_programs(list(self.tools.programs.values()), profile)
        self.assertEqual(next(row for row in result['excluded'][0]['reasons']
                              if row['field'] == 'max_total_tuition')['status'], 'excluded')
        for entry in self.tools.store.entries.values():
            if entry['url'].endswith('/2.html'):
                entry['validity'] = 'revoked'
        facts.refresh(self.tools)
        self.assertIsNone(self.tools.programs['p1']['duration_years'])
        self.assertEqual(self.tools.programs['p1']['tuition']['amount'], 12000)
        result = match_programs(list(self.tools.programs.values()), profile)
        reason = next(row for row in result['needs_confirmation'][0]['reasons']
                      if row['field'] == 'max_total_tuition')
        self.assertEqual(reason['status'], 'unknown')
        self.assertEqual(facts.snapshot(self.tools)['pending_count'], 1)

    def test_generation_change_and_tampered_proof_never_reuse_old_value(self):
        self.propose(PREFIX + '学费为12000元/年。')
        saved = facts.snapshot(self.tools)
        saved['facts'][0]['value'] = {'amount': 1, 'period': 'year'}
        saved['facts'][0]['status'] = 'corroborated'
        other = Tools()
        other.store, other.sources = self.tools.store, deepcopy(self.tools.sources)
        facts.inherit(other, {'admission_facts': saved})
        facts.refresh(other)
        self.assertEqual(other.programs['p1']['tuition']['amount'], 12000)
        next(iter(other.store.entries.values()))['extraction']['generation_id'] = 'kg_' + 'f' * 32
        facts.refresh(other)
        self.assertIsNone(other.programs['p1']['tuition'])
        self.assertEqual(facts.snapshot(other)['facts'][0]['state'], 'stale')

    def test_same_scope_conflict_is_explicit_and_only_pauses_affected_field(self):
        self.propose(PREFIX + '学费为12000元/年。')
        self.propose(PREFIX + '学制为3年。', 'duration_years')
        self.propose(PREFIX + '学费为15000元/年。')
        row = self.tools.programs['p1']
        self.assertIsNone(row['tuition'])
        self.assertEqual(row['duration_years'], 3)
        self.assertTrue(row['requires_review'])
        self.assertEqual(row['field_conflicts'][0]['field'], 'tuition')
        self.assertEqual(facts.snapshot(self.tools)['conflict_count'], 2)
        self.tools.store.entries['k_' + f'{3:024x}']['validity'] = 'revoked'
        facts.refresh(self.tools)
        self.assertEqual(self.tools.programs['p1']['tuition']['amount'], 12000)
        self.assertFalse(self.tools.programs['p1'].get('requires_review', False))

    def test_published_and_legacy_disagreement_cannot_silently_overwrite_known_fee(self):
        for marker in ('published_catalog', 'two_source_agreement'):
            with self.subTest(marker=marker):
                self.setUp()
                base = {'amount': 8000, 'period': 'year', 'verified_by': marker}
                for rows in (self.tools.programs, self.tools._research_pool):
                    rows['p1']['tuition'] = deepcopy(base)
                self.propose(PREFIX + '学费为12000元/年。')
                self.assertIsNone(self.tools.programs['p1']['tuition'])
                self.assertTrue(self.tools.programs['p1']['field_conflicts'])
                facts.restore(self.tools)
                self.assertEqual(self.tools.programs['p1']['tuition'], base)

    def test_partial_eligibility_can_exclude_but_cannot_claim_complete(self):
        self.propose(PREFIX + '学历要求为本科及以上。', 'eligibility')
        row = self.tools.programs['p1']
        self.assertEqual(row['conditions'][0]['value'], 'bachelor')
        self.assertFalse(row['eligibility_evidence_complete'])
        result = match_programs([row], validate_profile({'education': 'associate'}))
        self.assertEqual(result['excluded'][0]['status'], 'excluded')
        self.assertTrue(any(reason['field'] == 'education' and reason['evidence']
                            for reason in result['excluded'][0]['reasons']))

    def test_complete_eligibility_and_revocation_preserve_existing_conditions(self):
        existing = {'field': 'work_years', 'op': 'gte', 'value': 1,
                    'evidence': [{'url': UNIT['official_url'], 'quote': '工作满1年'}]}
        for rows in (self.tools.programs, self.tools._research_pool):
            rows['p1']['conditions'] = [deepcopy(existing)]
        self.propose(PREFIX + '完整报考条件如下：学历要求为本科及以上；工作年限不少于3年。', 'eligibility')
        self.assertTrue(self.tools.programs['p1']['eligibility_evidence_complete'])
        self.assertIn(existing, self.tools.programs['p1']['conditions'])
        next(iter(self.tools.store.entries.values()))['usable'] = False
        facts.refresh(self.tools)
        self.assertEqual(self.tools.programs['p1']['conditions'], [existing])
        self.assertFalse(self.tools.programs['p1']['eligibility_evidence_complete'])

    def test_snapshot_is_bounded_and_contains_only_bounded_canonical_read_proofs(self):
        self.propose(PREFIX + '学费为12000元/年。')
        result = facts.snapshot(self.tools)
        self.assertEqual(result['applied_count'], 1)
        self.assertEqual(result['pending_count'], 0)
        record = result['facts'][0]
        self.assertEqual(record['program_id'], 'p1')
        self.assertEqual(record['state'], 'verified_single_source')
        self.assertTrue(record['read_receipts'])
        self.assertTrue(record['evidence_refs'])
        self.assertTrue(all('text' not in receipt for receipt in record['read_receipts']))
        self.assertNotIn('full_text', json.dumps(result))
        result['facts'][0]['value']['amount'] = 1
        self.assertEqual(facts.snapshot(self.tools)['facts'][0]['value']['amount'], 12000)

    def test_scope_change_and_missing_published_key_restore_exact_base(self):
        self.tools.programs['p1'].pop('tuition')
        self.tools._research_pool['p1'].pop('tuition')
        self.propose(PREFIX + '学费为12000元/年。')
        self.tools.programs['p1']['study_mode'] = '非全日制'
        self.tools._research_pool['p1']['study_mode'] = '非全日制'
        facts.refresh(self.tools)
        self.assertNotIn('tuition', self.tools.programs['p1'])

    def test_complete_pool_direction_and_corroborated_facts_are_not_limited_to_selection(self):
        for index in (1, 2):
            draft = self.tools.read(PREFIX + '学费为12000元/年。', index=index)
            result = facts.propose(self.tools, {'program_id': 'p2', 'drafts': [draft]})
        self.assertEqual(result['accepted_count'], 1)
        self.assertIsNone(self.tools.programs['p1']['tuition'])
        self.assertEqual(self.tools._research_pool['p2']['tuition']['amount'], 12000)
        self.assertEqual({row['state'] for row in facts.snapshot(self.tools)['facts']}, {'corroborated'})

    def test_unknown_amount_dictionary_is_filled_and_exactly_restored(self):
        unknown = {'amount': None, 'period': 'year', 'notice': '尚待公布'}
        for rows in (self.tools.programs, self.tools._research_pool):
            rows['p1']['tuition'] = deepcopy(unknown)
        self.propose(PREFIX + '学费为12000元/年。')
        self.assertEqual(self.tools.programs['p1']['tuition']['amount'], 12000)
        next(iter(self.tools.store.entries.values()))['quality']['reusable'] = False
        facts.refresh(self.tools)
        self.assertEqual(self.tools.programs['p1']['tuition'], unknown)

    def test_agreeing_annual_fact_preserves_known_explicit_total_and_its_evidence(self):
        published = {'amount': 12000, 'period': 'year', 'total': 30000,
                     'evidence': [{'url': UNIT['official_url'], 'quote': '本专业全程学费合计30000元'}],
                     'verified_by': 'published_catalog'}
        for rows in (self.tools.programs, self.tools._research_pool):
            rows['p1'].update(tuition=deepcopy(published), duration_years=3)
        self.propose(PREFIX + '学费为12000元/年。')
        row = self.tools.programs['p1']
        self.assertEqual(row['tuition']['total'], 30000)
        self.assertIn(published['evidence'][0], row['tuition']['evidence'])
        matched = match_programs([row], validate_profile({'max_total_tuition': 35000}))
        reason = next(item for item in matched['needs_confirmation'][0]['reasons']
                      if item['field'] == 'max_total_tuition')
        self.assertEqual(reason['status'], 'matched')

    def test_duration_conflict_keeps_annual_fee_but_removes_total_computation(self):
        self.propose(PREFIX + '学费为12000元/年。')
        self.propose(PREFIX + '学制为3年。', 'duration_years')
        self.propose(PREFIX + '学制为2年。', 'duration_years')
        row = self.tools.programs['p1']
        self.assertEqual(row['tuition']['amount'], 12000)
        self.assertIsNone(row['duration_years'])
        self.assertEqual(row['duration_evidence'], [])
        self.assertEqual({item['field'] for item in row['field_conflicts']}, {'duration_years'})

    def test_eligibility_conflict_pauses_conditions_and_has_readable_display_labels(self):
        self.propose(PREFIX + '工作年限不少于3年。', 'eligibility')
        label = facts.snapshot(self.tools)['facts'][0]['value'][0]['label']
        self.assertIn('3年', label)
        self.propose(PREFIX + '工作年限不少于5年。', 'eligibility')
        self.assertEqual(self.tools.programs['p1']['conditions'], [])
        self.assertFalse(self.tools.programs['p1']['eligibility_evidence_complete'])
        self.assertEqual(facts.snapshot(self.tools)['conflict_count'], 2)

    def test_new_same_source_read_and_tampered_id_replay_are_safe(self):
        self.propose(PREFIX + '学费为12000元/年。')
        audit = facts.snapshot(self.tools)
        audit['facts'][0]['id'] = 'af_' + '0' * 24
        other = Tools()
        other.store, other.sources = self.tools.store, deepcopy(self.tools.sources)
        facts.inherit(other, {'admission_facts': audit})
        self.assertIsNone(other.programs['p1']['tuition'])
        self.assertEqual(facts.snapshot(other)['facts'][0]['state'], 'stale')

    def test_malformed_inherited_records_fail_closed_without_exceptions(self):
        self.propose(PREFIX + '学费为12000元/年。')
        original = facts.snapshot(self.tools)['facts'][0]
        for changes in ({'proof': []}, {'proof': {'draft': {'field': [], 'citation': {}}}},
                        {'read_receipts': {}}, {'read_receipts': [None]},
                        {'read_receipts': [{**original['read_receipts'][0], 'before': 'bad'}]},
                        {'read_receipts': [{**original['read_receipts'][0], 'max_chars': 999999}]},
                        {'read_receipts': [{**original['read_receipts'][0], 'group': ['segment']}]},
                        {'read_receipts': [{**original['read_receipts'][0], 'kind': 'x' * 10000}]},
                        {'id': []}):
            with self.subTest(changes=changes):
                other = Tools()
                other.store, other.sources = self.tools.store, deepcopy(self.tools.sources)
                facts.inherit(other, {'admission_facts': {'facts': [{**original, **changes}]}})
                self.assertIsNone(other.programs['p1']['tuition'])

    def test_limit_preserves_prior_audit_and_duplicate_proposals_are_idempotent(self):
        draft = self.tools.read(PREFIX + '学费为12000元/年。')
        with patch.object(facts, 'MAX_FACTS', 1):
            args = {'program_id': 'p1', 'drafts': [draft]}
            self.assertEqual(facts.propose(self.tools, args)['accepted_count'], 1)
            self.assertEqual(facts.propose(self.tools, args)['accepted_count'], 1)
            other = self.tools.read(PREFIX + '学制为3年。', 'duration_years')
            result = facts.propose(self.tools, {'program_id': 'p1', 'drafts': [other]})
            self.assertEqual(result['accepted_count'], 0)
            self.assertEqual(result['rejected'][0]['code'], 'admission_fact_limit')
            self.assertEqual(len(facts.snapshot(self.tools)['facts']), 1)

    def test_saved_report_view_replays_and_never_changes_the_persisted_original(self):
        self.propose(PREFIX + '学费为12000元/年。')
        report = {'candidates': [self.tools._candidate(self.tools.programs['p1'])],
                  'admission_facts': facts.snapshot(self.tools), 'algorithm': {'old': True}}
        saved = {**deepcopy(self.tools.task), 'status': 'completed', 'report': deepcopy(report)}
        before = deepcopy(saved)
        next(iter(self.tools.store.entries.values()))['validity'] = 'revoked'
        def fresh(*args, **kwargs):
            tools = Tools()
            tools.store, tools.sources = self.tools.store, deepcopy(self.tools.sources)
            facts.inherit(tools, report)
            return tools
        with patch('research_tools.ResearchTools', side_effect=fresh):
            view = facts.refresh_saved_facts(saved, object(), self.tools.store, '.')
        self.assertEqual(saved, before)
        self.assertEqual(view['status'], 'partial')
        self.assertIsNone(view['report']['candidates'][0]['tuition'])
        self.assertEqual(view['report']['algorithm'], {})
        self.assertEqual(view['report']['admission_facts']['facts'][0]['state'], 'stale')

    def test_foreign_field_changes_and_reviews_survive_overlay_restore(self):
        foreign = {'field': 'subjects', 'origin': 'other', 'paused': True}
        for rows in (self.tools.programs, self.tools._research_pool):
            rows['p1'].update(field_conflicts=[deepcopy(foreign)], requires_review=True)
        self.propose(PREFIX + '学费为12000元/年。')
        self.tools.programs['p1']['tuition'] = {'amount': 9000, 'period': 'year', 'verified_by': 'new_catalog'}
        facts.restore(self.tools)
        self.assertEqual(self.tools.programs['p1']['tuition']['amount'], 9000)
        self.assertEqual(self.tools.programs['p1']['field_conflicts'], [foreign])
        self.assertTrue(self.tools.programs['p1']['requires_review'])

    def test_complete_temporary_candidate_can_match_and_reverts_to_catalog_review_on_revocation(self):
        from tests.test_research_candidates import evaluate
        candidate = evaluate()['accepted'][0]
        candidate['validation'] = {'state': 'computable', 'status': 'valid'}
        identifier = candidate['id']
        self.assertTrue(candidate['requires_review'])
        self.tools.programs = {identifier: deepcopy(candidate)}
        self.tools._research_pool = {identifier: deepcopy(candidate)}
        draft = self.tools.read(PREFIX + '完整报考条件如下：学历要求为本科及以上。', 'eligibility')
        result = facts.propose(self.tools, {'program_id': identifier, 'drafts': [draft]})
        self.assertEqual(result['accepted_count'], 1)
        row = self.tools.programs[identifier]
        self.assertTrue(row['eligibility_evidence_complete'])
        self.assertFalse(row['requires_review'])
        profile = validate_profile({'education': 'bachelor', 'subjects': [
            {key: value for key, value in subject.items()
             if key in {'code', 'name', 'scope', 'exam_unit', 'choice_group'}} for subject in row['subjects']]})
        self.assertEqual(match_programs([row], profile)['matches'][0]['status'], 'matched')
        next(iter(self.tools.store.entries.values()))['validity'] = 'revoked'
        facts.refresh(self.tools)
        self.assertFalse(self.tools.programs[identifier]['eligibility_evidence_complete'])
        self.assertTrue(self.tools.programs[identifier]['requires_review'])

    def test_complete_bundle_never_clears_existing_published_review(self):
        for rows in (self.tools.programs, self.tools._research_pool):
            rows['p1']['requires_review'] = True
        self.propose(PREFIX + '完整报考条件如下：学历要求为本科及以上。', 'eligibility')
        self.assertTrue(self.tools.programs['p1']['requires_review'])

    def test_inherited_pool_only_fact_restores_target_from_current_catalog(self):
        draft = self.tools.read(PREFIX + '学费为12000元/年。')
        facts.propose(self.tools, {'program_id': 'p2', 'drafts': [draft]})
        report = {'admission_facts': facts.snapshot(self.tools)}
        other = Tools()
        other._research_pool.pop('p2')
        other.store, other.sources = self.tools.store, deepcopy(self.tools.sources)
        class Admission:
            def catalog_program(self, identifier, **kwargs):
                return {'program': program(identifier)}
        other.admission = Admission()
        facts.inherit(other, report)
        self.assertNotIn('p2', other.programs)
        self.assertEqual(other._research_pool['p2']['tuition']['amount'], 12000)

    def test_inherited_fact_cannot_add_catalog_targets_outside_inferred_school_or_without_current_proof(self):
        draft = self.tools.read(PREFIX + '学费为12000元/年。')
        facts.propose(self.tools, {'program_id': 'p2', 'drafts': [draft]})
        report = {'admission_facts': facts.snapshot(self.tools)}
        for foreign in (False, True):
            with self.subTest(foreign=foreign):
                other = Tools()
                other._research_pool.pop('p2')
                other.store, other.sources = self.tools.store, deepcopy(self.tools.sources)
                other.task.update(unit_ids=[], query='测试大学')
                other.infer_unit = lambda query: 'u1'
                other.units['u2'] = {**UNIT, 'id': 'u2', 'name': '另一大学'}
                class Admission:
                    def catalog_program(self, identifier, **kwargs):
                        return {'program': program(identifier, unit_id='u2' if foreign else 'u1')}
                other.admission = Admission()
                if not foreign:
                    other.sources = {}
                facts.inherit(other, report)
                self.assertNotIn('p2', other._research_pool)

    def test_strict_fact_refresh_does_not_scan_unrelated_national_pool(self):
        class CatalogPool(dict):
            def items(self):
                raise AssertionError('strict facts must address only their bounded target IDs')
            def values(self):
                raise AssertionError('strict facts must not rescan every catalog direction')
            def __iter__(self):
                raise AssertionError('strict facts must not iterate the national catalog')
        self.tools._research_pool = CatalogPool(self.tools._research_pool)
        self.propose(PREFIX + '学费为12000元/年。')
        self.assertEqual(self.tools._research_pool['p1']['tuition']['amount'], 12000)
        facts.refresh(self.tools)
        self.assertIsNone(self.tools._research_pool['p2']['tuition'])

    def test_unresolved_current_qualification_blocks_prior_complete_until_its_revocation(self):
        self.propose(PREFIX + '完整报考条件如下：学历要求为本科及以上。', 'eligibility')
        self.assertTrue(self.tools.programs['p1']['eligibility_evidence_complete'])
        result = self.propose(PREFIX + '报考条件：身体健康状况符合规定。', 'eligibility')
        self.assertEqual(result['accepted_count'], 1)
        self.assertFalse(self.tools.programs['p1']['eligibility_evidence_complete'])
        self.assertEqual(self.tools.programs['p1']['conditions'][0]['value'], 'bachelor')
        audit = facts.snapshot(self.tools)
        self.assertEqual(audit['pending_count'], 1)
        self.assertTrue(any(row['state'] == 'needs_review' for row in audit['facts']))
        self.tools.store.entries['k_' + f'{2:024x}']['validity'] = 'revoked'
        facts.refresh(self.tools)
        self.assertTrue(self.tools.programs['p1']['eligibility_evidence_complete'])

    def test_unresolved_canonical_clause_also_pauses_published_complete_flag(self):
        for rows in (self.tools.programs, self.tools._research_pool):
            rows['p1']['eligibility_evidence_complete'] = True
        self.propose(PREFIX + '报考条件：身体健康状况符合规定。', 'eligibility')
        self.assertFalse(self.tools.programs['p1']['eligibility_evidence_complete'])
        next(iter(self.tools.store.entries.values()))['validity'] = 'revoked'
        facts.refresh(self.tools)
        self.assertTrue(self.tools.programs['p1']['eligibility_evidence_complete'])

    def test_mixed_rejected_qualification_batch_cannot_become_complete_during_replay(self):
        qualification = self.tools.read(PREFIX + '完整报考条件如下：学历要求为本科及以上。', 'eligibility')
        malformed = {**deepcopy(qualification), 'value': True}
        fee = self.tools.read(PREFIX + '学费为12000元/年。')
        result = facts.propose(self.tools, {'program_id': 'p1', 'drafts': [qualification, malformed, fee]})
        self.assertEqual(result['accepted_count'], 1)
        self.assertEqual(result['facts'][0]['field'], 'tuition')
        self.assertFalse(self.tools.programs['p1']['eligibility_evidence_complete'])
        self.assertEqual(self.tools.programs['p1']['conditions'], [])
        facts.refresh(self.tools)
        self.assertFalse(self.tools.programs['p1']['eligibility_evidence_complete'])

    def test_plain_education_heading_remains_partial_even_when_scoped_to_program(self):
        result = self.propose(PREFIX + '报考条件：学历须为本科及以上。', 'eligibility')
        self.assertEqual(result['facts'][0]['state'], 'partial')
        self.assertFalse(self.tools.programs['p1']['eligibility_evidence_complete'])


class ResearchToolsAdmissionWorkflowTests(unittest.TestCase):
    def setUp(self):
        from research_tools import ResearchTools
        self.temporary = tempfile.TemporaryDirectory(prefix='strict-admission-integration-')
        self.addCleanup(self.temporary.cleanup)
        self.fixture = Tools()
        class Admission:
            def catalog_bootstrap(self):
                return {'units': [deepcopy(UNIT)], 'revision': 'fixture',
                        'meta': {'reference_year': 2027, 'target_year': 2028}}
            def catalog_program(self, identifier, **kwargs):
                return {'program': program(identifier)}
        self.admission = Admission()
        self.tools = ResearchTools(self.admission, None, deepcopy(self.fixture.task),
                                   Path(self.temporary.name), knowledge_store=self.fixture.store)
        self.tools.programs = {'p1': program()}
        self.tools._research_pool = {'p1': program()}

    def propose(self, text, field):
        draft = self.fixture.read(text, field)
        found = self.tools.run('search_knowledge_segments', {'query': text, 'unit_id': 'u1'})
        self.assertEqual(len(found['segments']), 1)
        read = self.tools.run('read_knowledge_segment', {'segment_id': draft['citation']['segment_id']})
        self.tools.confirm_segment_delivery(read, read)
        return self.tools.run('propose_admission_facts', {'program_id': 'p1', 'drafts': [draft]})

    def test_real_tool_dispatch_read_receipt_report_and_budget_reason_close(self):
        self.tools.task['profile'] = validate_profile({'max_total_tuition': 35000})
        self.assertEqual(self.propose(PREFIX + '学费为12000元/年。', 'tuition')['accepted_count'], 1)
        self.assertEqual(self.propose(PREFIX + '学制为3年。', 'duration_years')['accepted_count'], 1)
        compared = self.tools.run('compare_candidates', {})
        reason = next(row for row in compared['excluded'][0]['reasons'] if row['field'] == 'max_total_tuition')
        self.assertEqual(reason['status'], 'excluded')
        self.assertTrue(reason['evidence'])
        self.assertEqual({ref.get('field') for ref in reason['evidence']}, {'tuition', 'duration_years'})
        report = self.tools.report()
        self.assertEqual(report['admission_facts']['applied_count'], 2)
        self.assertEqual(report['candidates'][0]['tuition']['amount'], 12000)
        self.assertEqual(report['candidates'][0]['duration_years'], 3)
        self.assertTrue(report['decisions'][0]['original_links'])
        self.assertEqual(self.tools.usage['searches'], 0)
        self.assertEqual(self.tools.usage['documents'], 0)

    def test_actual_cross_round_and_historical_get_revoke_old_cost_without_writes(self):
        from research_tools import ResearchTools
        self.propose(PREFIX + '学费为12000元/年。', 'tuition')
        report = self.tools.report()
        next_tools = ResearchTools(self.admission, None,
            {**deepcopy(self.tools.task), 'previous_report': report},
            Path(self.temporary.name), knowledge_store=self.fixture.store)
        self.assertEqual(next_tools.programs['p1']['tuition']['amount'], 12000)
        saved = {**deepcopy(self.tools.task), 'status': 'completed', 'report': report}
        before = deepcopy(saved)
        next(iter(self.fixture.store.entries.values()))['validity'] = 'revoked'
        current = facts.refresh_saved_facts(saved, self.admission, self.fixture.store, Path(self.temporary.name))
        self.assertEqual(saved, before)
        self.assertIsNone(current['report']['candidates'][0]['tuition'])
        self.assertEqual(current['report']['admission_facts']['facts'][0]['state'], 'stale')
        self.assertEqual(current['report']['algorithm'], {})

    def test_temporary_canonical_refresh_does_not_drop_strict_fields_or_reset_stable_cache(self):
        from tests.test_research_candidates import ROW, EXAMS
        draft = self.fixture.read(ROW)
        self.tools.run('search_knowledge_segments', {'query': ROW, 'unit_id': 'u1'})
        read = self.tools.run('read_knowledge_segment', {'segment_id': draft['citation']['segment_id']})
        self.tools.confirm_segment_delivery(read, read)
        row = draft['citation']
        exams = {**row, 'char_start': row['char_start'] + ROW.index(EXAMS), 'quote': EXAMS}
        result = self.tools.run('propose_source_candidates', {
            'source_id': read['segment']['source_id'], 'segment_id': row['segment_id'], 'drafts': [
                {'code': '081200', 'name': '计算机科学与技术', 'department': '计算机学院',
                 'direction': '01人工智能', 'study_mode': '全日制', 'row': row, 'exams': exams}]})
        identifier = result['candidates'][0]['id']
        fee = self.fixture.read(PREFIX + '学费为12000元/年。')
        self.tools.run('search_knowledge_segments', {'query': fee['citation']['quote'], 'unit_id': 'u1'})
        read = self.tools.run('read_knowledge_segment', {'segment_id': fee['citation']['segment_id']})
        self.tools.confirm_segment_delivery(read, read)
        self.tools.run('propose_admission_facts', {'program_id': identifier, 'drafts': [fee]})
        self.tools._apply_fields()
        self.tools.algorithm = {'stable': True}
        version = self.tools._research_pool_version
        self.tools.refresh_knowledge()
        self.assertEqual(self.tools.programs[identifier]['tuition']['amount'], 12000)
        self.assertEqual(self.tools.algorithm, {'stable': True})
        self.assertEqual(self.tools._research_pool_version, version)


if __name__ == '__main__':
    unittest.main()
