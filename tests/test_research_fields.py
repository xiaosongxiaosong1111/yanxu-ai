"""Field candidates keep provenance, demand agreement and pause conflicts."""
import unittest

from research_fields import extract_field_candidates, merge_field_candidates, apply_field_candidates, field_candidates


def source(identifier='s_' + 'a' * 20, **overrides):
    row = {'id': identifier, 'unit_id': 'u1', 'unit_name': '测试大学', 'year': 2027,
           'status': 'official_original_read', 'title': '2027年硕士研究生招生简章',
           'sha256': 'a' * 64, 'url': 'https://grad.example.edu.cn/2027.html',
           'excerpt': '报考条件：具有国家承认的本科毕业学历。\n学费标准为12000元/年，各专业相同。',
           'knowledge_id': 'k_' + 'b' * 24}
    row.update(overrides)
    return row


def program(**overrides):
    row = {'id': 'p1', 'unit_id': 'u1', 'unit_name': '测试大学', 'year': 2027, 'code': '081200',
           'name': '计算机科学与技术', 'tuition': None, 'eligibility_evidence_complete': False,
           'subjects': [{'code': '408', 'name': '计算机学科专业基础', 'scope': 'national'}]}
    row.update(overrides)
    return row


class FieldCandidateTests(unittest.TestCase):
    def test_two_independent_originals_agreeing_make_a_verified_tuition_field_with_provenance(self):
        drafts = extract_field_candidates({'a': source(), 'b': source(
            's_' + 'c' * 20, sha256='c' * 64, url='https://grad.example.edu.cn/fees.html',
            knowledge_id='k_' + 'd' * 24,
            excerpt='各专业学费均为12000元/年，收费标准一致。')})
        merged = merge_field_candidates(drafts)
        tuition = next(row for row in merged if row['field'] == 'tuition')
        self.assertEqual(tuition['status'], 'verified')
        self.assertFalse(tuition['paused'])
        self.assertEqual(tuition['value'], 12000)
        self.assertEqual(tuition['scope'], {'unit_id': 'u1', 'year': 2027})
        self.assertGreaterEqual(tuition['source_count'], 2)
        self.assertTrue(all(item['quote'] and item['sha256'] for item in tuition['values']))

        programs = {'p1': program()}
        result = apply_field_candidates(programs, merged)
        self.assertEqual(result['applied'][0]['amount'], 12000)
        self.assertEqual(programs['p1']['tuition']['period'], 'year')
        self.assertEqual(programs['p1']['tuition']['verified_by'], 'two_source_agreement')
        self.assertEqual(len(programs['p1']['tuition']['evidence']), 2)
        self.assertTrue(programs['p1']['tuition']['evidence'][0]['url'])

    def test_a_single_original_stays_a_draft_and_never_reaches_recommendation(self):
        merged = merge_field_candidates(extract_field_candidates({'a': source()}))
        tuition = next(row for row in merged if row['field'] == 'tuition')
        self.assertEqual(tuition['status'], 'draft')
        self.assertTrue(tuition['paused'])
        programs = {'p1': program()}
        self.assertEqual(apply_field_candidates(programs, merged)['applied'], [])
        self.assertIsNone(programs['p1']['tuition'])

    def test_conflicting_originals_pause_the_affected_candidate_instead_of_choosing(self):
        drafts = extract_field_candidates({
            'a': source(),
            'b': source('s_' + 'c' * 20, sha256='c' * 64, url='https://grad.example.edu.cn/b.html',
                        knowledge_id='k_' + 'd' * 24,
                        excerpt='各专业学费均为15000元/年，收费标准一致。')})
        merged = merge_field_candidates(drafts)
        tuition = next(row for row in merged if row['field'] == 'tuition')
        self.assertEqual(tuition['status'], 'conflict')
        self.assertTrue(tuition['paused'])
        self.assertIsNone(tuition['value'])
        programs = {'p1': program()}
        result = apply_field_candidates(programs, merged)
        self.assertEqual(result['applied'], [])
        self.assertTrue(programs['p1']['requires_review'])
        self.assertEqual(programs['p1']['field_conflicts'][0]['field'], 'tuition')
        self.assertIsNone(programs['p1']['tuition'])

    def test_program_code_scope_only_applies_to_the_named_program(self):
        text = '081200 学费标准为12000元/年。'
        merged = merge_field_candidates(extract_field_candidates({
            'a': source(excerpt=text),
            'b': source('s_' + 'c' * 20, sha256='c' * 64, url='https://grad.example.edu.cn/b.html',
                        knowledge_id='k_' + 'd' * 24, excerpt=text)}))
        target, other = program(), program(id='p2', code='085400')
        programs = {'p1': target, 'p2': other}
        apply_field_candidates(programs, merged)
        self.assertEqual(target['tuition']['amount'], 12000)
        self.assertIsNone(other['tuition'])

    def test_different_program_codes_never_merge_into_two_source_agreement(self):
        merged = merge_field_candidates(extract_field_candidates({
            'a': source(excerpt='081200 学费标准为12000元/年。'),
            'b': source('s_' + 'c' * 20, sha256='c' * 64, url='https://grad.example.edu.cn/b.html',
                        knowledge_id='k_' + 'd' * 24,
                        excerpt='085404 学费标准为12000元/年。')}))
        tuition = [row for row in merged if row['field'] == 'tuition']
        self.assertEqual(len(tuition), 2)
        self.assertTrue(all(row['status'] == 'draft' and row['source_count'] == 1 for row in tuition))
        programs = {'a': program(), 'b': program(id='p2', code='085404')}
        self.assertEqual(apply_field_candidates(programs, merged)['applied'], [])
        self.assertIsNone(programs['a']['tuition'])
        self.assertIsNone(programs['b']['tuition'])

    def test_different_codes_with_different_amounts_do_not_conflict_or_pause_other_programs(self):
        merged = merge_field_candidates(extract_field_candidates({
            'a': source(excerpt='081200 学费标准为12000元/年。'),
            'b': source('s_' + 'c' * 20, sha256='c' * 64, url='https://grad.example.edu.cn/b.html',
                        knowledge_id='k_' + 'd' * 24,
                        excerpt='085404 学费标准为15000元/年。')}))
        programs = {'a': program(), 'b': program(id='p2', code='085404'),
                    'c': program(id='p3', code='125100')}
        result = apply_field_candidates(programs, merged)
        self.assertEqual(result['applied'], [])
        self.assertEqual(result['paused'], [])
        for row in programs.values():
            self.assertIsNone(row['tuition'])
            self.assertNotIn('field_conflicts', row)
            self.assertNotEqual(row.get('requires_review'), True)

    def test_conflict_pauses_only_the_program_inside_that_code_scope(self):
        merged = merge_field_candidates(extract_field_candidates({
            'a': source(excerpt='081200 学费标准为12000元/年。'),
            'b': source('s_' + 'c' * 20, sha256='c' * 64, url='https://grad.example.edu.cn/b.html',
                        knowledge_id='k_' + 'd' * 24,
                        excerpt='081200 学费标准为15000元/年。')}))
        target, other = program(), program(id='p2', code='085404')
        programs = {'p1': target, 'p2': other}
        result = apply_field_candidates(programs, merged)
        self.assertEqual(result['applied'], [])
        self.assertEqual([row['program_id'] for row in result['paused']], ['p1'])
        self.assertEqual(target['field_conflicts'][0]['field'], 'tuition')
        self.assertIsNone(other['tuition'])
        self.assertNotIn('field_conflicts', other)
        self.assertNotEqual(other.get('requires_review'), True)

    def test_two_links_to_the_same_original_are_one_source(self):
        excerpt = '各专业学费均为12000元/年，收费标准一致。'
        merged = merge_field_candidates(extract_field_candidates({
            'a': source(excerpt=excerpt),
            'b': source('s_' + 'c' * 20, url='https://grad.example.edu.cn/second-link.html',
                        knowledge_id='k_' + 'd' * 24, excerpt=excerpt)}))
        tuition = next(row for row in merged if row['field'] == 'tuition')
        self.assertEqual(tuition['source_count'], 1)
        self.assertEqual(tuition['status'], 'draft')
        self.assertEqual(len(tuition['values']), 1)
        programs = {'p1': program()}
        self.assertEqual(apply_field_candidates(programs, merged)['applied'], [])
        self.assertIsNone(programs['p1']['tuition'])

    def test_study_mode_scope_does_not_apply_part_time_fees_to_full_time_programs(self):
        excerpt = '各专业非全日制学费标准为12000元/年。'
        merged = merge_field_candidates(extract_field_candidates({
            'a': source(excerpt=excerpt),
            'b': source('s_' + 'c' * 20, sha256='c' * 64, url='https://grad.example.edu.cn/b.html',
                        knowledge_id='k_' + 'd' * 24, excerpt=excerpt)}))
        full_time, part_time = program(), program(id='p2', study_mode='非全日制')
        programs = {'p1': full_time, 'p2': part_time}
        result = apply_field_candidates(programs, merged)
        self.assertIsNone(full_time['tuition'])
        self.assertEqual(part_time['tuition']['amount'], 12000)
        self.assertEqual([row['program_id'] for row in result['applied']], ['p2'])

    def test_quote_offsets_slice_the_original_after_leading_space_trim_and_truncation(self):
        text = ' ' * 40 + '各专业学费标准为12000元/年' + '乙' * 400
        draft = next(row for row in extract_field_candidates({'a': source(excerpt=text)})
                     if row['field'] == 'tuition')
        self.assertEqual(len(draft['quote']), 300)
        self.assertEqual(draft['quote'], text[draft['excerpt_start']:draft['excerpt_end']])
        self.assertEqual(draft['excerpt_start'], 40)
        other = source('s_' + 'c' * 20, sha256='c' * 64, url='https://grad.example.edu.cn/b.html',
                       knowledge_id='k_' + 'd' * 24, excerpt=text)
        merged = merge_field_candidates(extract_field_candidates({'a': source(excerpt=text), 'b': other}))
        programs = {'p1': program()}
        apply_field_candidates(programs, merged)
        evidence = programs['p1']['tuition']['evidence']
        self.assertEqual(len(evidence), 2)
        for item in evidence:
            self.assertEqual(item['quote'], text[item['excerpt_start']:item['excerpt_end']])

    def test_eligibility_prose_is_only_a_needs_review_lead_and_unknown_stays_unknown(self):
        drafts = extract_field_candidates({'a': source()})
        eligibility = [row for row in drafts if row['field'] == 'eligibility']
        self.assertTrue(eligibility)
        merged = merge_field_candidates(drafts)
        row = next(item for item in merged if item['field'] == 'eligibility')
        self.assertEqual(row['status'], 'needs_review')
        self.assertTrue(row['paused'])
        self.assertIsNone(row['value'])
        programs = {'p1': program()}
        apply_field_candidates(programs, merged)
        self.assertFalse(programs['p1']['eligibility_evidence_complete'])
        self.assertIsNone(programs['p1']['tuition'])

    def test_unsaved_or_yearless_sources_cannot_propose_fields(self):
        for broken in (source(year=None), source(sha256=''), source(status='search_lead'),
                       source(status='published_snapshot', field_verification='published_snapshot')):
            self.assertEqual(extract_field_candidates({'x': broken}), [])

    def test_total_fee_is_not_used_as_an_annual_budget_value(self):
        text = '学费共计36000元/全程，分三年收取。'
        drafts = extract_field_candidates({'a': source(excerpt=text),
                                           'b': source('s_' + 'c' * 20, sha256='c' * 64,
                                                       url='https://grad.example.edu.cn/b.html',
                                                       knowledge_id='k_' + 'd' * 24, excerpt=text)})
        merged = merge_field_candidates(drafts)
        programs = {'p1': program()}
        self.assertEqual(apply_field_candidates(programs, merged)['applied'], [])
        self.assertIsNone(programs['p1']['tuition'])

    def test_field_candidates_convenience_returns_notice_and_no_zero_for_missing(self):
        result = field_candidates({'p1': program()}, {})
        self.assertEqual(result['candidates'], [])
        self.assertIn('缺失仍为未知', result['notice'])


if __name__ == '__main__':
    unittest.main()
