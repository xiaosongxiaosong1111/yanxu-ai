"""Temporary directions must be derived from explicitly read current originals."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from grad_engine import validate_profile
from research_store import ResearchError
from research_tools import ResearchTools, TOOLS
import research_candidate_tools
from tests.test_knowledge_agent_tools import CurrentKnowledge
from tests.test_research_service import FakeAdmission, FakeSettings


class EmptyAdmission(FakeAdmission):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.lookups = []

    def catalog_program(self, identifier, **kwargs):
        self.lookups.append(identifier)
        raise AssertionError('temporary directions must not be looked up in the catalog')


class CandidateKnowledge(CurrentKnowledge):
    def __init__(self):
        super().__init__()
        self.entry['extraction']['extraction_status'] = 'complete'
        body = '081200 计算机科学与技术 计算机学院 01人工智能 全日制 ①101思想政治理论②201英语一③301数学一④408计算机学科专业基础'
        self.segment = {**self.segment, 'kind': 'table_row', 'row_complete': True,
                        'continues_previous': False, 'continues_next': False,
                        'text': body, 'char_end': self.segment['char_start'] + len(body)}


class SourceCandidateToolTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='temporary-directions-')
        self.addCleanup(self.temporary.cleanup)
        self.admission = EmptyAdmission()
        self.store = CandidateKnowledge()
        self.task = {'id': 'r_' + '1' * 32, 'query': '测试大学计算机', 'profile': validate_profile({}),
                     'unit_ids': ['123'], 'reference_year': 2027, 'target_year': 2028}
        self.tools = self.make()

    def make(self, task=None):
        return ResearchTools(self.admission, FakeSettings(), task or self.task,
                             Path(self.temporary.name), knowledge_store=self.store)

    def discover(self):
        return self.tools.run('search_knowledge_segments', {'query': '计算机', 'unit_id': '123'})['segments'][0]

    def read(self):
        segment = self.discover()
        self.tools.run('read_knowledge_segment', {'segment_id': segment['id']})
        return segment

    def args(self, segment):
        citation = {key: segment[key] for key in ('char_start', 'char_end')}
        citation.update(segment_id=segment['id'], quote=segment['text'])
        exam_start = segment['text'].index('①' if '①' in segment['text'] else '②')
        exams = {**citation, 'char_start': segment['char_start'] + exam_start,
                 'quote': segment['text'][exam_start:]}
        return {'source_id': segment['source_id'], 'segment_id': segment['id'], 'drafts': [
            {'code': '081200', 'name': '计算机科学与技术', 'department': '计算机学院',
             'direction': '01人工智能', 'study_mode': '全日制', 'row': deepcopy(citation),
             'exams': exams}]}

    def propose(self):
        return self.tools.run('propose_source_candidates', self.args(self.read()))

    def test_zero_catalog_original_to_compare_routes_and_report(self):
        self.assertIn('propose_source_candidates', {item['function']['name'] for item in TOOLS})
        result = self.propose()
        self.assertEqual(result['accepted_count'], 1)
        identifier = result['candidates'][0]['id']
        self.assertTrue(identifier.startswith('tp_'))
        self.assertEqual(self.tools.compare({})['summary']['input_count'], 1)
        self.tools.routes({})
        report = self.tools.report()
        candidate = report['candidates'][0]
        self.assertEqual(candidate['verification_mode'], 'research_original')
        self.assertEqual(candidate['candidate_origin'], 'official_original')
        self.assertEqual(candidate['candidate_ref']['kind'], 'research')
        self.assertFalse(candidate['eligibility_evidence_complete'])
        self.assertEqual(report['coverage']['research_original_count'], 1)
        self.assertEqual(report['coverage']['published_count'], 0)
        self.assertFalse(report['coverage']['national_complete'])
        self.assertNotIn('已发布候选', report['summary'])
        self.assertEqual(report['research_candidates'][0]['validation']['status'], 'valid')
        self.assertEqual({row['status'] for row in report['evidence']}, {'official_original_read'})
        self.assertEqual(self.store.reads[0][0], self.store.segment['id'])

    def test_search_hit_and_bad_span_do_not_count_as_an_explicit_read(self):
        segment = self.discover()
        result = self.tools.run('propose_source_candidates', self.args(segment))
        self.assertEqual(result['accepted_count'], 0)
        self.assertTrue(result['rejected'])
        self.read()
        args = self.args(segment)
        args['drafts'][0]['exams']['char_start'] += 1
        result = self.tools.run('propose_source_candidates', args)
        self.assertEqual(result['accepted_count'], 0)
        self.assertEqual(self.tools._research_pool, {})

    def test_proposal_is_idempotent_and_forbids_model_school_or_year(self):
        args = self.args(self.read())
        first = self.tools.run('propose_source_candidates', args)
        second = self.tools.run('propose_source_candidates', args)
        self.assertEqual(first['candidates'][0]['id'], second['candidates'][0]['id'])
        self.assertEqual(len(self.tools._research_pool), 1)
        self.assertEqual(len(self.tools.report()['research_candidates']), 1)
        for field, value in [('year', 2028), ('unit_id', 'other'), ('tuition', 0), ('id', 'p_fake')]:
            bad = deepcopy(args)
            bad['drafts'][0][field] = value
            self.assertEqual(self.tools.run('propose_source_candidates', bad)['accepted_count'], 0)

    def test_different_school_year_summary_or_truncated_original_is_rejected(self):
        for mutation in ('year', 'unit', 'summary', 'truncated'):
            with self.subTest(mutation=mutation):
                self.store = CandidateKnowledge()
                self.tools = self.make()
                segment = self.read()
                if mutation == 'year': self.store.entry['year'] = 2026
                elif mutation == 'unit': self.store.entry['unit_id'] = '456'
                elif mutation == 'summary': self.store.entry['status'] = 'search_lead'
                else: self.store.segment['content_truncated'] = True
                result = self.tools.run('propose_source_candidates', self.args(segment))
                self.assertEqual(result['accepted_count'], 0)

    def test_revoke_or_generation_change_removes_calculable_pool_preserving_proof(self):
        for mutation in ('revoke', 'generation'):
            with self.subTest(mutation=mutation):
                self.store = CandidateKnowledge()
                self.tools = self.make()
                self.propose()
                self.tools.ensure_algorithms()
                if mutation == 'revoke': self.store.entry['validity'] = 'revoked'
                else: self.store.entry['extraction']['generation_id'] = 'kg_' + 'f' * 32
                report = self.tools.report()
                self.assertEqual(report['candidates'], [])
                self.assertEqual(self.tools._research_pool, {})
                self.assertNotIn('matching', report['algorithm'])
                self.assertEqual(report['research_candidates'][0]['validation']['status'], 'invalid')
                self.assertTrue(report['research_candidates'][0]['candidate_proof'])

    def test_hard_excluded_original_survives_pool_report_and_changed_profile_inheritance(self):
        self.task['profile'] = validate_profile({'excluded_subjects': [{'code': '408'}]})
        result = self.propose()
        self.assertEqual(result['accepted_count'], 1)
        self.assertEqual(self.tools.programs, {})
        self.assertEqual(len(self.tools._research_pool), 1)
        report = self.tools.report()
        next_task = {**self.task, 'id': 'r_' + '2' * 32, 'profile': validate_profile({}),
                     'previous_report': report, 'previous_turn_id': 0, 'turn_id': 1}
        inherited = self.make(next_task)
        self.assertEqual(len(inherited._research_pool), 1)
        self.assertEqual(len(inherited.programs), 1)
        self.assertEqual(inherited.compare({})['summary']['input_count'], 1)
        self.assertEqual(self.admission.lookups, [])
        self.store.entry['validity'] = 'revoked'
        stale = self.make(next_task)
        self.assertEqual(stale.programs, {})
        self.assertEqual(stale.report()['research_candidates'][0]['validation']['status'], 'invalid')

    def test_no_candidates_plan_guides_exact_read_and_proposal(self):
        segment = self.read()
        actions = self.tools.plan()['next_actions']
        self.assertTrue(any(row['tool'] == 'propose_source_candidates' for row in actions))
        self.assertTrue(any(row['tool'] == 'read_knowledge_segment' and
                            row['args']['segment_id'] == segment['id'] for row in actions))

    def test_twenty_four_bound_and_full_pool_survives_twelve_item_display_and_inherit(self):
        directions = [f'{number:02d}研究方向' for number in range(1, 26)]
        body = self.store.segment['text'].replace('01人工智能', ' '.join(directions))
        self.store.segment.update(text=body, char_end=self.store.segment['char_start']+len(body))
        args = self.args(self.read())
        drafts = [{**deepcopy(args['drafts'][0]), 'direction': value} for value in directions]
        for index in range(0, 25, 6):
            result = self.tools.run('propose_source_candidates', {**args, 'drafts': drafts[index:index+6]})
        self.assertEqual(result['accepted_count'], 0)
        self.assertEqual(result['rejected'][0]['code'], 'candidate_limit')
        report = self.tools.report()
        self.assertEqual(len(report['candidates']), 12)
        self.assertEqual(len(report['research_candidates']), 24)
        self.assertEqual(len(self.tools._research_pool), 24)
        inherited = self.make({**self.task, 'id': 'r_'+'2'*32, 'previous_report': report})
        self.assertEqual(len(inherited.programs), 12)
        self.assertEqual(len(inherited._research_pool), 24)
        self.assertEqual(inherited.routes({})['research_scope']['pool_count'], 24)
        self.assertEqual(self.admission.lookups, [])

    def test_delivery_projection_cannot_authorize_hidden_or_clipped_blocks(self):
        segment = self.discover()
        original = self.tools.run('read_knowledge_segment', {'segment_id': segment['id']})
        clipped = deepcopy(original)
        clipped['segment']['text'] = clipped['segment']['text'][:10]
        clipped['segment']['content_truncated'] = True
        self.tools.confirm_segment_delivery(original, clipped)
        self.assertEqual(self.tools.run('propose_source_candidates', self.args(segment))['accepted_count'], 0)
        original = self.tools.run('read_knowledge_segment', {'segment_id': segment['id']})
        self.tools.confirm_segment_delivery(original, original)
        self.assertEqual(self.tools.run('propose_source_candidates', self.args(segment))['accepted_count'], 1)

    def test_read_receipts_have_a_sixty_four_block_bound_and_no_full_text(self):
        segment = self.read()
        for index in range(70):
            block = {**deepcopy(segment), 'id': 'sg_'+f'{index:032x}'}
            research_candidate_tools.register_read(self.tools, {'segment': block}, 0, 0, 8000)
        self.assertEqual(len(self.tools._source_read_receipts), 64)
        self.assertTrue(all('text' not in row for row in self.tools._source_read_receipts.values()))

    def test_saved_report_return_rechecks_proof_without_mutating_original(self):
        self.propose()
        self.tools.ensure_algorithms()
        saved = {**self.task, 'status': 'completed', 'report': self.tools.report()}
        self.store.entry['validity'] = 'revoked'
        current = research_candidate_tools.refresh_saved_candidates(saved, self.admission, self.store,
                                                                    Path(self.temporary.name))
        self.assertEqual(current['report']['candidates'], [])
        self.assertEqual(current['report']['algorithm'], {})
        self.assertEqual(current['report']['research_candidates'][0]['validation']['state'], 'stale')
        self.assertEqual(len(saved['report']['candidates']), 1)

    def test_requested_source_is_registered_from_fresh_store_metadata_and_pending_stays_lead(self):
        task = {**self.task, 'source_context': {'entry_id': self.store.entry['id'], 'unit_id': 'fake',
                                              'year': 2000, 'title': 'untrusted browser metadata'}}
        seeded = self.make(task)
        source = next(iter(seeded.sources.values()))
        self.assertEqual(source['unit_id'], '123')
        self.assertEqual(source['year'], 2027)
        self.assertTrue(source['knowledge_current'])
        self.store.entry['quality'] = {**self.store.entry['quality'], 'state': 'pending', 'reusable': False}
        pending = self.make(task)
        source = next(iter(pending.sources.values()))
        self.assertEqual(source['status'], 'search_lead')
        self.assertEqual(source['excerpt'], '')
        self.assertFalse(source['knowledge_current'])
        self.assertEqual(pending._source_read_receipts, {})

    def test_precisely_read_context_without_own_id_supports_only_its_exact_supplement(self):
        body = self.store.segment['text'].replace('全日制 ', '')
        self.store.segment.update(text=body, char_end=self.store.segment['char_start']+len(body))
        context = {'role': 'heading', 'kind': 'paragraph', 'char_start': 10,
                   'text': '我校所有专业均招收全日制硕士研究生'}
        context['char_end'] = context['char_start']+len(context['text'])
        original = self.store.read_segment
        self.store.read_segment = lambda identifier, **kwargs: {
            **original(identifier, **kwargs), 'contexts': [deepcopy(context)]}
        args = self.args(self.read())
        self.assertEqual(self.tools.run('propose_source_candidates', args)['accepted_count'], 0)
        args['drafts'][0]['supplements'] = [{'field': 'study_mode', 'citation': {
            'segment_id': self.store.segment['id'], 'char_start': context['char_start'],
            'char_end': context['char_end'], 'quote': context['text']}}]
        result = self.tools.run('propose_source_candidates', args)
        self.assertEqual(result['accepted_count'], 1)
        self.assertEqual(result['candidates'][0]['evidence_refs'][-1]['quote'], context['text'])
        context['text'] = context['text'].replace('所有', '部分')
        self.assertEqual(self.tools.report()['candidates'], [])

    def test_unknown_eligibility_reason_does_not_borrow_subject_proof(self):
        self.propose()
        self.tools.compare({})
        decision = self.tools.decisions()[0]
        for reason in decision['reasons']:
            if reason['field'] in {'education', 'eligibility', 'background', 'tuition'}:
                self.assertEqual(reason['evidence_refs'], [])
            if reason['field'] == 'subjects':
                self.assertTrue(reason['evidence_refs'])
                self.assertTrue(all(ref['field'] in {'exams', 'politics'} for ref in reason['evidence_refs']))

    def test_inheritance_reconstructs_canonical_fields_instead_of_trusting_saved_program(self):
        self.propose()
        report = self.tools.report()
        stored = report['research_candidates'][0]
        stored['subjects'][0]['code'] = '999'
        stored['subjects'][0]['scope'] = 'other'
        stored['eligibility_evidence_complete'] = True
        stored['tuition'] = 0
        inherited = self.make({**self.task, 'previous_report': report})
        candidate = next(iter(inherited._research_pool.values()))
        self.assertEqual(candidate['subjects'][0]['code'], '101')
        self.assertFalse(candidate['eligibility_evidence_complete'])
        self.assertIsNone(candidate['tuition'])

    def test_plan_never_calls_uncomputable_subjects_known(self):
        from research_plan import build_plan
        complete = [{'code': code, 'name': name, 'scope': 'national', 'exam_unit': slot}
                    for slot, code, name in [(1, '101', '思想政治理论'), (2, '201', '英语一'),
                                             (3, '301', '数学一'), (4, '408', '计算机学科专业基础')]]
        cases = [
            {'subjects': complete, 'exam_matching_supported': False},
            {'subjects': complete, 'subject_identity_issues': [{'reason': '代码与名称冲突'}]},
            {'subjects': [complete[3]]},
            {'subjects': [*complete[:2], complete[3]]},
            {'subjects': [{**item, 'scope': 'unknown'} for item in complete]},
            {'subjects': [*complete, deepcopy(complete[-1])]},
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                plan = build_plan(self.task, {'p1': {'id': 'p1', 'unit_id': '123', 'year': 2027, **changes}},
                                  {}, {}, [], {})
                fact = next(row for row in plan['facts'] if row['fact'] == 'subjects')
                self.assertEqual(fact['status'], 'unknown')
                self.assertIsNone(fact['value'])
                self.assertEqual(fact['next_action']['tool'], 'discover_official_entry')
        for subjects in (complete, complete[:3], complete[:2],
                         [{key: value for key, value in item.items() if key != 'exam_unit'} for item in complete]):
            with self.subTest(complete=subjects):
                plan = build_plan(self.task, {'p1': {'id': 'p1', 'subjects': subjects}}, {}, {}, [], {})
                self.assertEqual(next(row for row in plan['facts'] if row['fact'] == 'subjects')['status'], 'known')

    def test_plan_read_action_can_be_delivered_without_clipping_and_search_is_scoped_to_entry(self):
        from research_model_projection import project_segment_read
        segment = self.read()
        action = next(row for row in self.tools.plan()['next_actions'] if row['tool'] == 'read_knowledge_segment')
        self.assertLessEqual(action['args']['max_chars'], 3200)
        original = self.tools.run(action['tool'], action['args'])
        delivered = project_segment_read(original)
        self.tools.confirm_segment_delivery(original, delivered)
        self.assertEqual(delivered['segment']['text'], segment['text'])
        self.assertEqual(self.tools.run('propose_source_candidates', self.args(segment))['accepted_count'], 1)
        seeded = self.make({**self.task, 'source_context': {'entry_id': self.store.entry['id']}})
        search = next(row for row in seeded.plan()['next_actions'] if row['tool'] == 'search_knowledge_segments')
        result = seeded.run(search['tool'], search['args'])
        self.assertTrue(result['segments'])
        self.assertEqual(self.store.searches[-1]['entry_id'], self.store.entry['id'])

    def test_supplement_cannot_ignore_a_conflicting_neighbor_read_in_its_window(self):
        from research_model_projection import project_segment_read
        body = self.store.segment['text'].replace('①101思想政治理论', '')
        self.store.segment.update(text=body, char_end=self.store.segment['char_start']+len(body))
        politics = {**deepcopy(self.store.segment), 'id': 'sg_'+'4'*32, 'kind': 'paragraph',
                    'row_complete': None, 'char_start': 100, 'text': '1.考试科目一均为：①101思想政治理论。'}
        exception = {**deepcopy(politics), 'id': 'sg_'+'5'*32, 'char_start': 200,
                     'text': '2.部分专业科目一不考政治。'}
        previous = {**deepcopy(politics), 'id': 'sg_'+'6'*32, 'char_start': 5000,
                    'text': '3.阅读说明。'+'公开资料。'*620}
        for block in (politics, exception, previous):
            block['char_end'] = block['char_start']+len(block['text'])
        original_search, original_read = self.store.search_segments, self.store.read_segment
        def search(**kwargs):
            if kwargs['query'] != '政治':
                return original_search(**kwargs)
            return {'segments': [{**deepcopy(politics), 'source': deepcopy(self.store.entry)}], 'total': 1}
        def read(identifier, **kwargs):
            if identifier != politics['id']:
                return original_read(identifier, **kwargs)
            return {'segment': {**deepcopy(politics), 'source': deepcopy(self.store.entry)}, 'contexts': [],
                    'neighbors': [*([{**deepcopy(previous), 'source': deepcopy(self.store.entry)}]
                                    if kwargs.get('before') else []),
                                  *([{**deepcopy(exception), 'source': deepcopy(self.store.entry)}]
                                    if kwargs.get('after') else [])], 'truncated': False}
        self.store.search_segments, self.store.read_segment = search, read
        args = self.args(self.read())
        self.tools.run('search_knowledge_segments', {'query': '政治', 'unit_id': '123'})
        self.tools.run('read_knowledge_segment', {'segment_id': politics['id'], 'after': 1})
        args['drafts'][0]['supplements'] = [{'field': 'politics', 'citation': {
            'segment_id': politics['id'], 'char_start': politics['char_start'],
            'char_end': politics['char_end'], 'quote': politics['text']}}]
        result = self.tools.run('propose_source_candidates', args)
        self.assertEqual(result['accepted_count'], 0)
        self.assertEqual(result['rejected'][0]['code'], 'unsupported_supplement')
        self.tools.run('read_knowledge_segment', {'segment_id': politics['id']})
        self.assertEqual(self.tools.run('propose_source_candidates', args)['accepted_count'], 0)
        for repeat_same_window in (False, True):
            with self.subTest(repeat_same_window=repeat_same_window):
                if repeat_same_window:
                    self.tools.run('read_knowledge_segment', {'segment_id': politics['id'], 'before': 1, 'after': 1})
                original = self.tools.run('read_knowledge_segment', {'segment_id': politics['id'], 'before': 1, 'after': 1})
                delivered = project_segment_read(original)
                self.assertTrue(any(row['id'] == previous['id'] for row in delivered['neighbors']))
                self.assertFalse(any(row['id'] == exception['id'] for row in delivered['neighbors']))
                self.tools.confirm_segment_delivery(original, delivered)
                self.assertEqual(self.tools.run('propose_source_candidates', args)['accepted_count'], 0)


if __name__ == '__main__':
    unittest.main()
