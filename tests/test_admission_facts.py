"""Admission facts require current canonical clauses, never model-supplied values."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from admission_facts import aggregate_facts, validate_fact_drafts
from grad_engine import _condition, PROFILE_FIELDS
from tests.test_research_candidates import UNIT, SEGMENT, resolved, citation


PROGRAM = {'id': 'p1', 'unit_id': UNIT['id'], 'year': 2027, 'code': '081200',
           'name': '计算机科学与技术', 'study_mode': '全日制', 'degree_type': '学术学位',
           'tuition': None, 'duration_years': None, 'conditions': [], 'notes': []}
PREFIX = '081200计算机科学与技术全日制'


def request(field, text, **changes):
    return {'field': field, 'citation': citation(text, text), **changes}


def evaluate(text, field='tuition', program=None, payload=None, drafts=None):
    current = resolved(text) if payload is None else payload
    return validate_fact_drafts(UNIT, 2027, PROGRAM if program is None else program,
                               drafts or [request(field, text)], lambda ref: deepcopy(current))


class AdmissionFactTests(unittest.TestCase):
    def reject(self, result, code=None):
        self.assertEqual(result['accepted'], [], result)
        self.assertTrue(result['rejected'], result)
        if code:
            self.assertEqual(result['rejected'][0]['code'], code, result)
        self.assertFalse(result['eligibility_evidence_complete'])

    def pending(self, result):
        self.assertEqual(result['rejected'], [], result)
        self.assertEqual(result['accepted'][0]['status'], 'needs_review', result)
        self.assertEqual(result['accepted'][0]['value'], [])
        self.assertFalse(result['eligibility_evidence_complete'])

    def test_one_authoritative_canonical_fee_is_explicitly_single_source(self):
        text = PREFIX + '学费标准为12000元/年。'
        before = deepcopy((PROGRAM, UNIT))
        result = evaluate(text)
        self.assertEqual(result['rejected'], [], result)
        self.assertEqual(result['tuition']['amount'], 12000)
        self.assertEqual(result['tuition']['period'], 'year')
        self.assertEqual(result['accepted'][0]['status'], 'verified_single_source')
        self.assertEqual(result['accepted'][0]['evidence'][0]['quote'], text)
        self.assertEqual(result['accepted'][0]['scope']['code'], '081200')
        self.assertEqual((PROGRAM, UNIT), before)
        self.assertFalse(result['eligibility_evidence_complete'])

    def test_total_period_is_never_invented_as_annual_and_wan_amount_is_exact(self):
        result = evaluate(PREFIX + '学费共计3.6万元/全程。')
        self.assertEqual(result['tuition']['amount'], 36000)
        self.assertEqual(result['tuition']['period'], 'total')

    def test_schoolwide_fee_and_duration_must_state_their_scope(self):
        result = evaluate('我校所有全日制硕士研究生学费均为8000元/学年。')
        self.assertEqual(result['tuition']['amount'], 8000)
        self.reject(evaluate('学费均为8000元/年。'), 'unsupported_scope')
        result = evaluate('我校所有全日制硕士研究生学制均为三年。', 'duration_years')
        self.assertEqual(result['duration_years'], 3)

    def test_degree_mode_code_and_reference_year_never_cross(self):
        for text in ('085404计算机技术全日制学费为12000元/年。',
                     '081200计算机科学与技术非全日制学费为12000元/年。',
                     PREFIX + '专业学位学费为12000元/年。',
                     '2026年招生：' + PREFIX + '学费为12000元/年。'):
            with self.subTest(text=text):
                self.reject(evaluate(text), 'unsupported_scope')
        self.reject(evaluate('081200计算机科学与技术学费为12000元/年。'), 'unsupported_scope')

    def test_reject_wrong_unit_year_current_generation_and_receipt(self):
        text = PREFIX + '学费为12000元/年。'
        for key, wrong in (('unit_id', 'other'), ('year', 2026)):
            payload = resolved(text)
            payload['source'][key] = wrong
            self.reject(evaluate(text, payload=payload), 'source_scope')
        payload = resolved(text)
        payload['source']['extraction']['generation_id'] = 'stale'
        self.reject(evaluate(text, payload=payload), 'stale_generation')
        for key in ('trusted', 'current', 'read_complete'):
            self.reject(evaluate(text, payload=resolved(text, **{key: False})), 'untrusted_citation')

    def test_reject_fabricated_offsets_partial_quote_and_unread_continuation(self):
        text = PREFIX + '学费为12000元/年。'
        for key, wrong in (('quote', text + '假'), ('char_start', 199), ('char_end', 9999)):
            value = request('tuition', text)
            value['citation'][key] = wrong
            self.reject(evaluate(text, drafts=[value]), 'citation_mismatch')
        cropped = {'field': 'tuition', 'citation': citation(text[:-1], text)}
        self.reject(evaluate(text, drafts=[cropped]), 'incomplete_clause')
        payload = resolved(text)
        payload['block']['continues_next'] = True
        self.reject(evaluate(text, payload=payload), 'incomplete_row')

    def test_canonical_parent_headers_must_be_read_and_cannot_be_sibling_rows(self):
        text = '081200计算机科学与技术\t12000元/年'
        payload = resolved(text)
        heading = '全日制学术学位硕士研究生学费标准'
        parent = {**payload['block'], 'id': 'sg_parent', 'kind': 'heading',
                  'text': heading, 'char_start': 10, 'char_end': 10 + len(heading)}
        payload['block']['context_spans'] = [{'char_start': 10, 'char_end': 10 + len(heading),
                                             'text': heading, 'role': 'heading'}]
        payload['read_blocks'] = [parent, deepcopy(payload['block'])]
        result = evaluate(text, payload=payload)
        self.assertEqual(result['tuition']['amount'], 12000, result)
        self.assertEqual(len(result['accepted'][0]['evidence']), 2)
        payload['read_blocks'] = [deepcopy(payload['block'])]
        self.reject(evaluate(text, payload=payload), 'unread_scope')
        payload['block']['context_spans'] = []
        payload['read_blocks'] = [parent, deepcopy(payload['block'])]
        self.reject(evaluate(text, payload=payload), 'unsupported_scope')

    def test_model_values_operators_qualification_truth_or_unknown_fields_rejected(self):
        text = PREFIX + '学费为12000元/年。'
        for key, value in (('value', 0), ('op', 'eq'), ('eligibility_evidence_complete', True)):
            self.reject(evaluate(text, drafts=[request('tuition', text, **{key: value})]), 'invalid_draft')
        self.reject(evaluate(text, field='education'), 'invalid_draft')

    def test_partial_education_never_establishes_complete_eligibility(self):
        text = PREFIX + '学历要求为本科及以上。'
        result = evaluate(text, 'eligibility')
        self.assertEqual(result['conditions'][0]['field'], 'education', result)
        self.assertEqual(result['conditions'][0]['op'], 'gte')
        self.assertEqual(result['conditions'][0]['value'], 'bachelor')
        self.assertFalse(result['eligibility_evidence_complete'])
        self.assertIn('eligibility_scope_incomplete', result['missing'])
        result = evaluate(PREFIX + '报考条件：学历须为本科及以上。', 'eligibility')
        self.assertFalse(result['eligibility_evidence_complete'])
        self.assertEqual(result['accepted'][0]['status'], 'partial')

    def test_full_scoped_simple_conditions_use_existing_executable_ast(self):
        text = PREFIX + '完整报考条件如下：学历要求为本科及以上；工作年限不少于3年；本科专业须为计算机科学与技术或软件工程。'
        result = evaluate(text, 'eligibility')
        self.assertTrue(result['eligibility_evidence_complete'], result)
        self.assertEqual(len(result['conditions']), 3)
        self.assertEqual(result['conditions'][2]['value'], ['计算机科学与技术', '软件工程'])
        for node in result['conditions']:
            reason, _ = _condition(node, {'education': 'bachelor', 'work_years': 3,
                                         'background': '软件工程'}, [], PROFILE_FIELDS)
            self.assertEqual(reason['status'], 'matched')
        reason, _ = _condition(result['conditions'][1], {'work_years': 1}, [], PROFILE_FIELDS)
        self.assertEqual(reason['status'], 'excluded')

    def test_independent_or_stays_any_and_coupled_cross_degree_language_is_unknown(self):
        result = evaluate(PREFIX + '报考条件：学历须为硕士或工作年限不少于3年。', 'eligibility')
        self.assertIn('any', result['conditions'][0], result)
        for text in (PREFIX + '报考条件：本科毕业须满3年或硕士毕业须满2年。',
                     PREFIX + '报考条件：学历须为本科或硕士且工作年限不少于3年。'):
            self.pending(evaluate(text, 'eligibility'))

    def test_negation_exception_special_plan_and_health_do_not_become_eligible(self):
        for suffix in ('报考条件：不接收同等学力考生。',
                       '完整报考条件：学历须为本科及以上，但跨专业考生除外。'):
            self.pending(evaluate(PREFIX + suffix, 'eligibility'))
        self.reject(evaluate(PREFIX + '专项计划完整报考条件：学历须为本科及以上。', 'eligibility'),
                    'unsupported_condition')
        text = PREFIX + '完整报考条件：学历须为本科及以上；身体健康状况符合规定。'
        result = evaluate(text, 'eligibility')
        self.assertFalse(result['eligibility_evidence_complete'], result)
        self.assertIn('unsupported_condition:身体健康状况符合规定', result['missing'])

    def test_schoolwide_generic_eligibility_and_program_notes_never_clear_completeness(self):
        result = evaluate('我校所有全日制硕士研究生报考条件：学历须为本科及以上。', 'eligibility')
        self.assertFalse(result['eligibility_evidence_complete'])
        result = evaluate(PREFIX + '完整报考条件：学历须为本科及以上。', 'eligibility',
                          program={**PROGRAM, 'unparsed_condition_notes': ['跨专业另需加试']})
        self.assertFalse(result['eligibility_evidence_complete'])

    def test_single_field_conflict_pauses_that_field_instead_of_overwriting(self):
        a = PREFIX + '学费为12000元/年。'
        b = PREFIX + '学费为15000元/年。'
        ra, rb = resolved(a), resolved(b, segment='sg_second', offset=500)
        rb['source']['sha256'] = 'b' * 64
        second = {'field': 'tuition', 'citation': citation(b, b, 'sg_second', 500)}
        result = validate_fact_drafts(UNIT, 2027, PROGRAM, [request('tuition', a), second],
                                     lambda ref: deepcopy(ra if ref['segment_id'] == SEGMENT else rb))
        self.assertIsNone(result['tuition'])
        self.assertEqual(result['conflicts'][0]['field'], 'tuition')
        self.assertTrue(all(f['status'] == 'conflict' for f in result['accepted']))
        result = evaluate(a, program={**PROGRAM, 'tuition': {'amount': 16000, 'period': 'year'}})
        self.assertIsNone(result['tuition'])
        self.assertTrue(result['conflicts'])

    def test_two_links_same_sha_never_claim_independent_corroboration(self):
        text = PREFIX + '学费为12000元/年。'
        a, b = resolved(text), resolved(text, segment='sg_second', offset=500)
        b['source_id'] = 's_other'
        b['source']['url'] = 'https://grad.example.edu.cn/mirror.html'
        refs = [request('tuition', text), {'field': 'tuition',
                'citation': citation(text, text, 'sg_second', 500)}]
        result = validate_fact_drafts(UNIT, 2027, PROGRAM, refs,
                                     lambda ref: deepcopy(a if ref['segment_id'] == SEGMENT else b))
        self.assertTrue(all(row['status'] == 'verified_single_source' for row in result['accepted']))
        b['source']['sha256'] = 'b' * 64
        result = validate_fact_drafts(UNIT, 2027, PROGRAM, refs,
                                     lambda ref: deepcopy(a if ref['segment_id'] == SEGMENT else b))
        self.assertTrue(all(row['status'] == 'corroborated' for row in result['accepted']))

    def test_exceptional_duration_and_unbound_numbers_remain_unknown(self):
        for text in (PREFIX + '学制为2至3年。', PREFIX + '学制最长5年，基本学制3年。',
                     '我校全日制硕士研究生除国际商务专业的学制为2年外，其它专业的学制均为3年。'):
            self.reject(evaluate(text, 'duration_years'), 'unsupported_duration')
        self.reject(evaluate(PREFIX + '住宿费1200元/年，学费另行通知。'), 'unsupported_tuition')

    def test_equivalent_complete_rules_from_two_sources_corroborate_without_evidence_conflict(self):
        text = PREFIX + '完整报考条件：学历须为本科及以上。'
        a, b = resolved(text), resolved(text, segment='sg_second', offset=500)
        b['source']['sha256'] = 'b' * 64
        rows = [request('eligibility', text), {'field': 'eligibility',
                'citation': citation(text, text, 'sg_second', 500)}]
        result = validate_fact_drafts(UNIT, 2027, PROGRAM, rows,
                                     lambda ref: deepcopy(a if ref['segment_id'] == SEGMENT else b))
        self.assertTrue(result['eligibility_evidence_complete'], result)
        self.assertEqual(result['conflicts'], [])
        self.assertEqual(len(result['conditions']), 1)
        self.assertTrue(all(row['status'] == 'corroborated' for row in result['accepted']))

    def test_public_aggregation_is_immutable_and_detects_cross_call_conflicts(self):
        a = evaluate(PREFIX + '学费为12000元/年。')['accepted']
        b = evaluate(PREFIX + '学费为15000元/年。')['accepted']
        before = deepcopy((a, b, PROGRAM))
        result = aggregate_facts(PROGRAM, [*a, *b])
        self.assertIsNone(result['tuition'])
        self.assertEqual(result['conflicts'][0]['field'], 'tuition')
        self.assertEqual((a, b, PROGRAM), before)
        self.assertEqual(aggregate_facts(PROGRAM, a)['tuition']['amount'], 12000)

    def test_contextual_special_plan_and_borrowed_background_conditions_are_rejected(self):
        text = PREFIX + '完整报考条件：学历须为本科及以上。'
        payload = resolved(text)
        heading = '退役大学生士兵专项计划'
        parent = {**payload['block'], 'id': 'sg_parent', 'kind': 'heading',
                  'text': heading, 'char_start': 10, 'char_end': 10 + len(heading)}
        payload['block']['context_spans'] = [{'char_start': 10, 'char_end': 10 + len(heading),
                                             'text': heading, 'role': 'heading'}]
        payload['read_blocks'] = [parent, deepcopy(payload['block'])]
        self.reject(evaluate(text, 'eligibility', payload=payload), 'unsupported_scope')
        result = evaluate(PREFIX + '完整报考条件：本科专业须为计算机科学与技术或学历须为本科。', 'eligibility')
        nodes = result['conditions'][0]['any']
        self.assertEqual([node['field'] for node in nodes], ['background', 'education'])
        self.assertEqual(nodes[0]['value'], '计算机科学与技术')

    def test_related_unread_or_unsupported_qualification_never_clears_unknown(self):
        text = PREFIX + '完整报考条件：学历须为本科及以上。'
        payload = resolved(text)
        other = {**payload['block'], 'id': 'sg_other', 'text': '健康条件另需核验。',
                 'char_start': 700, 'char_end': 709}
        other['char_end'] = other['char_start'] + len(other['text'])
        payload['read_blocks'] = [deepcopy(payload['block']), other]
        result = evaluate(text, 'eligibility', payload=payload)
        self.assertFalse(result['eligibility_evidence_complete'])
        self.assertIn('additional_eligibility_clauses_unresolved', result['missing'])
        result = validate_fact_drafts(UNIT, 2027, PROGRAM,
                    [request('eligibility', text), request('eligibility', text, value=True)],
                    lambda ref: resolved(text))
        self.assertFalse(result['eligibility_evidence_complete'])
        self.assertEqual(result['accepted'], [])
        self.assertIn('eligibility_batch_incomplete', [row['code'] for row in result['rejected']])

    def test_mixed_rejected_eligibility_cannot_persist_a_complete_fact_but_fee_survives(self):
        text = PREFIX + '完整报考条件：学历须为本科及以上。'
        fee_text = PREFIX + '学费为12000元/年。'
        eligibility = request('eligibility', text)
        fee = {'field': 'tuition', 'citation': citation(fee_text, fee_text, 'sg_second', 500)}
        invalid = request('eligibility', text, value=True)
        result = validate_fact_drafts(UNIT, 2027, PROGRAM, [eligibility, fee, invalid],
                     lambda ref: resolved(text) if ref['segment_id'] == SEGMENT else
                     resolved(fee_text, segment='sg_second', offset=500))
        self.assertEqual([row['field'] for row in result['accepted']], ['tuition'])
        self.assertEqual(result['tuition']['amount'], 12000)
        self.assertFalse(aggregate_facts(PROGRAM, result['accepted'])['eligibility_evidence_complete'])

    def test_different_partial_education_rules_conflict_instead_of_conjoining(self):
        a = evaluate(PREFIX + '学历须为本科。', 'eligibility')['accepted']
        b = evaluate(PREFIX + '学历须为硕士。', 'eligibility')['accepted']
        result = aggregate_facts(PROGRAM, [*a, *b])
        self.assertEqual(result['conditions'], [])
        self.assertEqual(result['conflicts'][0]['field'], 'eligibility')

    def test_invalid_requests_and_explicit_previous_cohort_fail_closed(self):
        for drafts in ([], None, [request('tuition', PREFIX)] * 13):
            result = validate_fact_drafts(UNIT, 2027, PROGRAM, drafts, lambda ref: None)
            self.reject(result, 'invalid_request')
        self.reject(evaluate('2026级' + PREFIX + '学费为12000元/年。'), 'unsupported_scope')
        self.reject(evaluate(PREFIX + '学费暂定为12000元/年。'), 'unsupported_tuition')

    def test_unknown_or_invalid_baseline_fee_is_not_a_competing_price(self):
        for amount in (None, True, '8000', float('nan'), float('inf'), -1):
            with self.subTest(amount=amount):
                result = evaluate(PREFIX + '学费为12000元/年。', program={**PROGRAM,
                                  'tuition': {'amount': amount, 'period': 'year'}})
                self.assertEqual(result['tuition']['amount'], 12000, result)

    def test_unrelated_new_reads_keep_stable_id_but_read_price_conflict_blocks_adoption(self):
        text = PREFIX + '学费为12000元/年。'
        first = evaluate(text)['accepted'][0]['id']
        payload = resolved(text)
        other = {**payload['block'], 'id': 'sg_other', 'text': '学校地址与招生联系电话。',
                 'char_start': 700}
        other['char_end'] = 700 + len(other['text'])
        payload['read_blocks'] = [deepcopy(payload['block']), other]
        self.assertEqual(evaluate(text, payload=payload)['accepted'][0]['id'], first)
        other['text'] = PREFIX + '学费为15000元/年。'
        other['char_end'] = 700 + len(other['text'])
        self.reject(evaluate(text, payload=payload), 'fact_context_conflict')

    def test_numeric_bounds_disclaimed_prices_and_hidden_qualification_prefix_are_not_facts(self):
        for suffix in ('学费最高为12000元/年。', '学费约12000元/年。',
                       '本专业不执行学费标准12000元/年。'):
            self.reject(evaluate(PREFIX + suffix), 'unsupported_tuition')
        self.reject(evaluate(PREFIX + '学制不超过3年。', 'duration_years'), 'unsupported_duration')
        self.pending(evaluate(PREFIX + '本科专业须为临床医学，完整报考条件：学历须为本科及以上。',
                              'eligibility'))
        self.pending(evaluate(PREFIX + '完整报考条件：本科专业须为计算机相关专业。', 'eligibility'))

    def test_trusted_current_scoped_unresolved_clause_blocks_prior_complete_through_replay(self):
        complete = evaluate(PREFIX + '完整报考条件：学历须为本科及以上。', 'eligibility')['accepted']
        text = PREFIX + '完整报考条件：身体健康状况符合有关规定。'
        payload = resolved(text)
        payload['source']['sha256'] = 'b' * 64
        pending = evaluate(text, 'eligibility', payload=payload)['accepted']
        self.assertEqual(pending[0]['status'], 'needs_review')
        result = aggregate_facts(PROGRAM, [*complete, *pending])
        self.assertFalse(result['eligibility_evidence_complete'])
        self.assertEqual(len(result['conditions']), 1)
        self.assertEqual(result['accepted'][1]['status'], 'needs_review')
        self.assertEqual(result['accepted'][0]['status'], 'verified_single_source')
        again = validate_fact_drafts(UNIT, 2027, PROGRAM, [pending[0]['proof']['draft']], lambda ref: payload)
        self.assertEqual(again['accepted'][0]['id'], pending[0]['id'])
        payload['source']['year'] = 2026
        self.reject(evaluate(text, 'eligibility', payload=payload), 'source_scope')


class RecordedPublicAdmissionFactTests(unittest.TestCase):
    def setUp(self):
        from grad_engine import validate_profile
        from knowledge_store import KnowledgeStore
        from public_sources import document_attribution, extract_saved_document
        from research_tools import ResearchTools
        from tests.test_research_service import FakeSettings
        from tests.test_source_candidate_tools import EmptyAdmission
        self.unit = {'id': '367889', 'name': '北京服装学院', 'code': '10012',
                     'official_url': 'https://yjs.bift.edu.cn', 'province': '北京'}
        temporary = tempfile.TemporaryDirectory(prefix='admission-facts-original-')
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name)
        self.store = KnowledgeStore(self.data)
        fixture = Path(__file__).parent / 'fixtures/admission_facts/bift2027-cases.json'
        self.observed = json.loads(fixture.read_text('utf-8'))
        raw_path = fixture.parent / self.observed['raw_snapshot']
        raw = raw_path.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), self.observed['sha256'])
        document = extract_saved_document(raw, 'text/html', self.observed['source_url'])
        attribution = document_attribution(document['url'], self.unit, document['text'],
                                           document['document_title'], document['document_heading'])
        self.assertEqual(attribution['status'], 'verified')
        self.text = document['extraction']['text']
        self.entry = self.store.save_source({'url': self.observed['source_url'],
            'title': self.observed['source_title'], 'unit_id': self.unit['id'],
            'unit_name': self.unit['name'], 'year': 2027, 'requested_year': 2027,
            'status': 'official_original_read', 'sha256': self.observed['sha256']},
            document={**document, 'attribution_status': attribution['status']})
        admission = EmptyAdmission()
        admission.units = [deepcopy(self.unit)]
        self.task = {'id': 'r_' + 'd' * 32, 'query': '核对原文学费和学制',
                     'profile': validate_profile({}), 'unit_ids': [self.unit['id']],
                     'reference_year': 2027, 'target_year': 2028}
        self.tools = ResearchTools(admission, FakeSettings(), self.task, self.data, knowledge_store=self.store)

    def read(self, query, after=0):
        from research_model_projection import project_segment_read
        rows = self.tools.run('search_knowledge_segments', {'query': query,
                 'entry_id': self.entry['id'], 'unit_id': self.unit['id']})['segments']
        self.assertTrue(rows)
        selected = next(row for row in rows if query in row['text'])
        payload = self.tools.run('read_knowledge_segment', {'segment_id': selected['id'], 'after': after, 'max_chars': 8000})
        self.tools.confirm_segment_delivery(payload, project_segment_read(payload))
        self.last_read = payload
        block = payload['segment']
        self.assertEqual(block['text'], self.text[block['char_start']:block['char_end']])
        return block

    def validate(self, block, field, code, name):
        from research_candidate_tools import _resolver
        resolve, _ = _resolver(self.tools, list(self.tools._source_read_receipts.values()))
        ref = {'segment_id': block['id'], 'char_start': block['char_start'],
               'char_end': block['char_end'], 'quote': block['text']}
        program = {**PROGRAM, 'unit_id': self.unit['id'], 'code': code, 'name': name,
                   'degree_type': None}
        return validate_fact_drafts(self.unit, 2027, program,
                                   [{'field': field, 'citation': ref}], resolve)

    def test_untouched_same_year_official_exception_and_rowspan_stay_unknown(self):
        for quote in self.observed['quotes']:
            self.assertIn(quote, self.text)
        self.read('我校所有专业均招收全日制硕士研究生。')
        block = self.read(self.observed['quotes'][0])
        result = self.validate(block, 'duration_years', '082104', '服装设计与工程')
        self.assertFalse(result['eligibility_evidence_complete'])
        self.assertIsNone(result['duration_years'])
        self.assertIsNone(result['tuition'])
        self.assertEqual(result['rejected'][0]['code'], 'unsupported_duration')
        self.read('080200机械工程', after=2)
        # The current segmenter classifies this rowspan-only code as a heading.
        # It is not a read, self-contained fee row and may not inherit 6000.
        rows = self.tools.run('search_knowledge_segments', {'query': '082104服装设计与工程',
                'entry_id': self.entry['id'], 'unit_id': self.unit['id']})['segments']
        self.assertFalse(any('082104服装设计与工程' in row['text'] for row in rows))
        quote = '082104服装设计与工程'
        unread = {**block, 'text': quote, 'char_start': self.text.index(quote),
                  'char_end': self.text.index(quote) + len(quote)}
        result = self.validate(unread, 'tuition', '082104', '服装设计与工程')
        self.assertEqual(result['accepted'], [])
        self.assertIsNone(result['tuition'])

    def test_official_explicit_first_fee_row_is_usable_with_read_scope_headers(self):
        self.read('我校所有专业均招收全日制硕士研究生。')
        row = self.read('140300设计学')
        result = self.validate(row, 'tuition', '140300', '设计学')
        self.assertEqual(result['rejected'], [], result)
        self.assertEqual(result['tuition']['amount'], 8000)
        self.assertEqual(result['tuition']['period'], 'year')
        self.assertEqual(result['accepted'][0]['status'], 'verified_single_source')
        for proof in result['accepted'][0]['evidence']:
            self.assertEqual(proof['quote'], self.text[proof['char_start']:proof['char_end']])
        self.assertFalse(result['eligibility_evidence_complete'])


if __name__ == '__main__':
    unittest.main()
