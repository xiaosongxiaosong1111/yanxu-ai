"""Derived facts must follow the lifetime of their current supporting originals."""
from copy import deepcopy
import unittest

from research_fields import field_candidates
from tests.test_research_fields import source, program


class FieldLifetimeTests(unittest.TestCase):
    def originals(self, amount=12000, **scope):
        text = f'各专业学费均为{amount}元/年，收费标准一致。'
        return {'a': source(excerpt=text, **scope),
                'b': source('s_' + 'c' * 20, sha256='c' * 64,
                            url='https://grad.example.edu.cn/fees-2.html',
                            knowledge_id='k_' + 'd' * 24, excerpt=text, **scope)}

    def test_revocation_reverts_owned_tuition_to_unknown_and_can_reverify(self):
        rows = {'p1': program()}
        sources = self.originals()
        field_candidates(rows, sources)
        self.assertEqual(rows['p1']['tuition']['amount'], 12000)
        result = field_candidates(rows, {'a': sources['a'], 'b': {
            **sources['b'], 'usable': False, 'knowledge_current': False}})
        self.assertEqual(next(row for row in result['candidates'] if row['field']=='tuition')['status'], 'draft')
        self.assertIsNone(rows['p1']['tuition'])
        field_candidates(rows, self.originals(15000))
        self.assertEqual(rows['p1']['tuition']['amount'], 15000)

    def test_empty_evidence_restores_the_exact_original_unknown_field(self):
        unknown = {'amount': None, 'period': 'year', 'notice': '原目录尚未公布金额'}
        rows = {'p1': program(tuition=deepcopy(unknown))}
        field_candidates(rows, self.originals())
        field_candidates(rows, {})
        self.assertEqual(rows['p1']['tuition'], unknown)

    def test_scope_or_year_change_removes_a_previously_derived_amount(self):
        for change in ({'year': 2026}, {'unit_id': 'other'}):
            with self.subTest(change=change):
                rows = {'p1': program()}
                field_candidates(rows, self.originals())
                rows['p1'].update(change)
                field_candidates(rows, self.originals())
                self.assertIsNone(rows['p1']['tuition'])
        rows = {'p1': program(study_mode='全日制')}
        sources = self.originals()
        sources = {key: {**value, 'excerpt': '全日制各专业学费均为12000元/年，收费标准一致。'}
                   for key, value in sources.items()}
        field_candidates(rows, sources)
        rows['p1']['study_mode'] = '非全日制'
        field_candidates(rows, sources)
        self.assertIsNone(rows['p1']['tuition'])

    def test_conflict_clears_derived_tuition_and_resolution_clears_owned_review_state(self):
        rows = {'p1': program(requires_review=False)}
        sources = self.originals()
        field_candidates(rows, sources)
        conflicting = {**sources, 'b': {**sources['b'], 'excerpt': '各专业学费均为15000元/年，收费标准一致。'}}
        field_candidates(rows, conflicting)
        self.assertIsNone(rows['p1']['tuition'])
        self.assertTrue(rows['p1']['requires_review'])
        self.assertEqual(len(rows['p1']['field_conflicts']), 1)
        field_candidates(rows, self.originals(15000))
        self.assertEqual(rows['p1']['tuition']['amount'], 15000)
        self.assertFalse(rows['p1']['requires_review'])
        self.assertNotIn('field_conflicts', rows['p1'])

    def test_catalog_tuition_and_existing_review_requirement_are_never_deleted(self):
        original = {'amount': 8500, 'period': 'year', 'verified_by': 'published_catalog',
                    'evidence': [{'url': 'https://example.edu.cn/verified-fee', 'quote': '每年8500元'}]}
        rows = {'p1': program(tuition=deepcopy(original), requires_review=True)}
        sources = self.originals()
        field_candidates(rows, sources)
        self.assertEqual(rows['p1']['tuition'], original)
        field_candidates(rows, {**sources, 'b': {**sources['b'], 'excerpt': '各专业学费均为15000元/年，收费标准一致。'}})
        self.assertEqual(rows['p1']['tuition'], original)
        field_candidates(rows, {})
        self.assertEqual(rows['p1']['tuition'], original)
        self.assertTrue(rows['p1']['requires_review'])

    def test_unrelated_conflicts_survive_the_tuition_conflict_lifecycle(self):
        foreign = {'field': 'subjects', 'candidate_id': 'external-subject-review', 'paused': True}
        rows = {'p1': program(field_conflicts=[deepcopy(foreign)], requires_review=True)}
        sources = self.originals()
        field_candidates(rows, {**sources, 'b': {**sources['b'], 'excerpt': '各专业学费均为15000元/年，收费标准一致。'}})
        field_candidates(rows, {})
        self.assertEqual(rows['p1']['field_conflicts'], [foreign])
        self.assertTrue(rows['p1']['requires_review'])

    def test_missing_original_fields_are_restored_as_missing_and_reapplication_is_stable(self):
        row = program()
        row.pop('tuition')
        rows = {'p1': row}
        sources = self.originals()
        original_sources = deepcopy(sources)
        field_candidates(rows, sources)
        first = deepcopy(rows)
        field_candidates(rows, sources)
        self.assertEqual(rows, first)
        self.assertEqual(sources, original_sources)
        field_candidates(rows, {})
        self.assertNotIn('tuition', row)

    def test_legacy_module_derived_field_without_a_base_is_safely_downgraded(self):
        rows = {'p1': program(tuition={'amount': 12000, 'period': 'year',
            'verified_by': 'two_source_agreement', 'field_candidate_id': 'fc_'+'a'*20})}
        field_candidates(rows, {})
        self.assertIsNone(rows['p1']['tuition'])

    def test_changed_tracks_only_effective_overlay_changes(self):
        rows = {'p1': program()}
        self.assertTrue(field_candidates(rows, self.originals())['changed'])
        self.assertFalse(field_candidates(rows, self.originals())['changed'])
        self.assertTrue(field_candidates(rows, {})['changed'])
        self.assertFalse(field_candidates(rows, {})['changed'])
        conflicting = self.originals()
        conflicting['b'] = {**conflicting['b'], 'excerpt': '各专业学费均为15000元/年，收费标准一致。'}
        self.assertTrue(field_candidates(rows, conflicting)['changed'])
        self.assertFalse(field_candidates(rows, conflicting)['changed'])

    def test_program_code_change_removes_only_the_owned_scope(self):
        rows = {'p1': program()}
        sources = {key: {**value, 'excerpt': '081200 学费标准为12000元/年。'}
                   for key, value in self.originals().items()}
        field_candidates(rows, sources)
        self.assertEqual(rows['p1']['tuition']['amount'], 12000)
        rows['p1']['code'] = '085404'
        self.assertTrue(field_candidates(rows, sources)['changed'])
        self.assertIsNone(rows['p1']['tuition'])

    def test_explicit_non_reusable_quality_cannot_support_a_derived_fact(self):
        for invalid in ({'validity': 'revoked'}, {'knowledge_current': False}, {'usable': False},
                        {'knowledge_quality': {'state': 'pending', 'reusable': False}},
                        {'knowledge_quality': 'invalid'}):
            with self.subTest(invalid=invalid):
                rows = {'p1': program()}
                sources = self.originals()
                sources['b'] = {**sources['b'], **invalid}
                field_candidates(rows, sources)
                self.assertIsNone(rows['p1']['tuition'])


if __name__ == '__main__':
    unittest.main()
