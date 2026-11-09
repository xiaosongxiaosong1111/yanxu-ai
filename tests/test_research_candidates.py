"""Temporary candidates need exact, current, fully read row evidence."""
from copy import deepcopy
import unittest

from research_candidates import validate_drafts


UNIT = {'id': 'u1', 'name': '测试大学', 'province': '天津'}
SID = 's_' + 'a' * 20
SEGMENT = 'sg_' + 'a' * 32
ENTRY = 'k_' + 'a' * 24
EXTRACTION = 'ex_' + 'a' * 32
GENERATION = 'kg_' + 'a' * 32
EXAMS = '①101思想政治理论②201英语(一)③301数学(一)④408计算机学科专业基础综合'
ROW = '001计算机学院\t081200计算机科学与技术\t01人工智能\t全日制\t' + EXAMS


def citation(text, full=ROW, segment=SEGMENT, offset=200):
    start = offset + full.index(text)
    return {'segment_id': segment, 'char_start': start, 'char_end': start + len(text), 'quote': text}


def draft(row=ROW, exams=EXAMS, **changes):
    value = {'code': '081200', 'name': '计算机科学与技术', 'department': '计算机学院',
             'direction': '01人工智能', 'study_mode': '全日制',
             'row': citation(row, row), 'exams': citation(exams, row)}
    return {**value, **changes}


def resolved(text=ROW, segment=SEGMENT, offset=200, **changes):
    source = {'id': ENTRY, 'unit_id': 'u1', 'year': 2027, 'sha256': 'a' * 64,
              'url': 'https://grad.example.edu.cn/2027.html', 'title': '2027招生专业目录',
              'status': 'official_original_read', 'usable': True, 'original_available': True,
              'validity': 'active', 'quality': {'state': 'accepted'},
              'extraction': {'id': EXTRACTION, 'generation_id': GENERATION, 'extraction_status': 'complete'}}
    block = {'id': segment, 'entry_id': ENTRY, 'extraction_id': EXTRACTION,
             'generation_id': GENERATION, 'kind': 'table_row', 'text': text,
             'char_start': offset, 'char_end': offset + len(text), 'row_complete': True,
             'continues_previous': False, 'continues_next': False, 'content_truncated': False}
    return {**{'block': block, 'source': source, 'source_id': SID, 'read_complete': True,
               'trusted': True, 'current': True}, **changes}


def evaluate(value=None, payload=None, resolver=None):
    supplied = resolved() if payload is None else payload
    return validate_drafts(UNIT, 2027, SID, SEGMENT, [draft() if value is None else value],
                           resolver or (lambda _citation: deepcopy(supplied)))


class CandidateValidationTests(unittest.TestCase):
    def reject(self, result, code=None):
        self.assertEqual(result['accepted'], [])
        self.assertEqual(len(result['rejected']), 1)
        if code:
            self.assertEqual(result['rejected'][0]['code'], code)
        self.assertTrue(result['rejected'][0]['reason'])

    def test_exact_complete_row_generates_immutable_computable_program(self):
        value, payload = draft(), resolved()
        before = deepcopy((UNIT, value, payload))
        result = evaluate(value, payload)
        self.assertEqual(result['rejected'], [])
        program = result['accepted'][0]
        self.assertTrue(program['id'].startswith('tp_'))
        self.assertEqual(program['candidate_origin'], 'official_original')
        self.assertEqual(program['verification_mode'], 'research_original')
        self.assertEqual(program['province'], '天津')
        self.assertEqual(program['unit_name'], '测试大学')
        self.assertEqual(program['department'], '计算机学院')
        self.assertEqual(program['conditions'], [])
        self.assertTrue(program['requires_review'])
        self.assertFalse(program['eligibility_evidence_complete'])
        self.assertIsNone(program['tuition'])
        self.assertIsNone(program['duration_years'])
        self.assertTrue(program['exam_matching_supported'])
        self.assertEqual([s['exam_unit'] for s in program['subjects']], [1, 2, 3, 4])
        self.assertTrue(all(s['scope'] == 'national' for s in program['subjects']))
        proof = program['candidate_proof']
        self.assertEqual(proof['draft'], value)
        self.assertEqual(proof['fields']['row']['quote'], ROW)
        self.assertEqual(proof['sources'][0]['sha256'], 'a' * 64)
        self.assertEqual(proof['sources'][0]['generation_id'], GENERATION)
        self.assertEqual((UNIT, value, payload), before)

    def test_ids_are_idempotent_but_bind_all_source_versions(self):
        a = evaluate()['accepted'][0]
        self.assertEqual(a['id'], evaluate()['accepted'][0]['id'])
        payload = resolved()
        payload['source']['sha256'] = 'b' * 64
        self.assertNotEqual(a['id'], evaluate(payload=payload)['accepted'][0]['id'])
        payload = resolved()
        payload['source']['extraction']['generation_id'] = 'kg_' + 'b' * 32
        payload['block']['generation_id'] = 'kg_' + 'b' * 32
        self.assertNotEqual(a['id'], evaluate(payload=payload)['accepted'][0]['id'])

    def test_reject_fabricated_quote_and_wrong_absolute_offsets(self):
        for key, value in [('quote', ROW + '伪造'), ('char_start', 199), ('char_end', 9999)]:
            value_draft = draft()
            value_draft['row'][key] = value
            self.reject(evaluate(value_draft), 'citation_mismatch')

    def test_reject_exam_quote_from_another_actual_row_even_same_document(self):
        other = '085404计算机技术\t全日制\t' + EXAMS
        value = {**draft(), 'exams': citation(EXAMS, other, 'sg_' + 'b' * 32, 900)}
        self.reject(evaluate(value, resolver=lambda ref: resolved(other, ref['segment_id'], 900)
                             if ref['segment_id'] != SEGMENT else resolved()), 'cross_row')

    def test_reject_wrong_school_year_and_stale_generation(self):
        for field, wrong in [('unit_id', 'u2'), ('year', 2026)]:
            payload = resolved()
            payload['source'][field] = wrong
            self.reject(evaluate(payload=payload), 'source_scope')
        payload = resolved()
        payload['source']['extraction']['generation_id'] = 'kg_' + 'b' * 32
        self.reject(evaluate(payload=payload), 'stale_generation')

    def test_reject_unread_untrusted_and_revoked_blocks(self):
        for field in ('read_complete', 'trusted', 'current'):
            self.reject(evaluate(payload=resolved(**{field: False})), 'untrusted_citation')
        for field, value in [('validity', 'revoked'), ('usable', False),
                             ('original_available', False), ('status', 'published_snapshot'),
                             ('quality', {'state': 'pending'})]:
            payload = resolved()
            payload['source'][field] = value
            self.reject(evaluate(payload=payload), 'untrusted_citation')

    def test_reject_partial_truncated_and_continuation_rows(self):
        for field, value in [('row_complete', False), ('content_truncated', True),
                             ('continues_previous', True), ('continues_next', True)]:
            payload = resolved()
            payload['block'][field] = value
            self.reject(evaluate(payload=payload), 'incomplete_row')

    def test_reject_partial_row_citation_and_multiline_join(self):
        value = {**draft(), 'row': citation(ROW[3:])}
        self.reject(evaluate(value), 'incomplete_row')
        row = ROW + '\n085404计算机技术\t全日制\t' + EXAMS
        self.reject(evaluate(draft(row, EXAMS), resolved(row)), 'incomplete_row')

    def test_reject_multiple_major_codes_in_one_row(self):
        row = ROW.replace('081200', '085404/081200')
        self.reject(evaluate(draft(row), resolved(row)), 'ambiguous_identity')

    def test_missing_exam_unit_and_national_code_name_conflict(self):
        for exams in [EXAMS.replace('③301数学(一)', ''), EXAMS.replace('201英语(一)', '201英语(二)')]:
            row = ROW.replace(EXAMS, exams)
            self.reject(evaluate(draft(row, exams), resolved(row)),
                        'national_identity_conflict' if '201英语(二)' in exams else 'invalid_exams')

    def test_independent_or_is_not_lost_and_local_scope_is_from_unit(self):
        exams = EXAMS.replace('②201英语(一)', '②201英语(一)或203日语').replace('④408计算机学科专业基础综合', '④801计算机基础')
        row = ROW.replace(EXAMS, exams)
        program = evaluate(draft(row, exams), resolved(row))['accepted'][0]
        options = [s for s in program['subjects'] if s['exam_unit'] == 2]
        self.assertEqual([s['code'] for s in options], ['201', '203'])
        self.assertTrue(all(s['choice_group'] == 'exam-unit-2' for s in options))
        self.assertEqual(program['subjects'][-1]['scope'], 'u1')

    def test_correlated_exam_bundles_are_rejected_instead_of_flattened(self):
        exams = EXAMS + '；或整组：①101思想政治理论②204英语(二)③302数学(二)④801计算机基础'
        row = ROW.replace(EXAMS, exams)
        self.reject(evaluate(draft(row, exams), resolved(row)), 'correlated_exams')

    def test_study_mode_cannot_be_guessed_or_take_full_from_part_time(self):
        row = ROW.replace('\t全日制', '')
        self.reject(evaluate(draft(row), resolved(row)), 'unsupported_identity')
        row = ROW.replace('\t全日制', '\t非全日制')
        self.reject(evaluate(draft(row), resolved(row)), 'unsupported_identity')

    def test_identity_requires_entire_code_name_and_real_cells(self):
        for changes in [{'name': '计算机'}, {'direction': '人工智能'}, {'department': '学院'},
                        {'code': '085404'}, {'study_mode': '未知'}]:
            self.reject(evaluate(draft(**changes)), 'unsupported_identity')

    def test_reject_authority_fields_in_drafts_and_citations(self):
        for field, value in [('id', 'p1'), ('year', 2027), ('unit_id', 'u1'), ('scope', 'national'),
                             ('tuition', None), ('conditions', []), ('subjects', [])]:
            self.reject(evaluate(draft(**{field: value})), 'invalid_draft')
        value = draft()
        value['exams']['source_id'] = SID
        self.reject(evaluate(value), 'invalid_citation')

    def test_batch_limit_and_bad_resolver_fail_closed(self):
        result = validate_drafts(UNIT, 2027, SID, SEGMENT, [draft()] * 7, lambda ref: resolved())
        self.assertFalse(result['accepted'])
        self.assertEqual(result['rejected'][0]['code'], 'batch_limit')
        self.reject(evaluate(resolver=lambda ref: None), 'untrusted_citation')
        def unavailable(_ref):
            raise RuntimeError('secret implementation details')
        result = evaluate(resolver=unavailable)
        self.reject(result, 'untrusted_citation')
        self.assertNotIn('secret', str(result))

    def test_two_and_three_exam_units_are_allowed_when_explicitly_complete(self):
        for exams in ['①199管理类综合能力②204英语(二)', '①101思想政治理论②201英语(一)③311教育学专业基础综合']:
            row = ROW.replace(EXAMS, exams)
            self.assertEqual(evaluate(draft(row, exams), resolved(row))['rejected'], [])

    def test_bift_like_schoolwide_politics_and_mode_supplements_are_exact(self):
        exams = '②201英语(一)③302数学(二)④902服装理论'
        row = '001服装艺术与工程学院\t082104服装设计与工程\t学硕\t01功能性服装研究\t' + exams
        politics, mode = '1.考试科目一均为：①101思想政治理论。', '我校所有专业均招收全日制硕士研究生。'
        polseg, modeseg = 'sg_' + 'b' * 32, 'sg_' + 'c' * 32
        supplements = [{'field': 'politics', 'citation': citation(politics, politics, polseg, 900)},
                       {'field': 'study_mode', 'citation': citation(mode, mode, modeseg, 1100)}]
        value = draft(row, exams, code='082104', name='服装设计与工程', department='服装艺术与工程学院',
                      direction='01功能性服装研究', supplements=supplements)
        def resolver(ref):
            text, offset = {SEGMENT: (row, 200), polseg: (politics, 900), modeseg: (mode, 1100)}[ref['segment_id']]
            result = resolved(text, ref['segment_id'], offset)
            if ref['segment_id'] != SEGMENT:
                result['block']['kind'] = 'paragraph'
                result['block']['row_complete'] = None
            return result
        result = evaluate(value, resolver=resolver)
        self.assertEqual(result['rejected'], [])
        self.assertEqual([s['code'] for s in result['accepted'][0]['subjects']], ['101', '201', '302', '902'])
        self.assertEqual(len(result['accepted'][0]['candidate_proof']['fields']['supplements']), 2)

    def test_schoolwide_supplements_cannot_borrow_other_year_or_exceptions(self):
        row = ROW.replace('\t全日制', '')
        for text in ['部分专业招收全日制硕士研究生。', '我校除计算机专业外所有专业均招收全日制硕士研究生。']:
            segment = 'sg_' + 'b' * 32
            ref = citation(text, text, segment, 900)
            value = draft(row, supplements=[{'field': 'study_mode', 'citation': ref}])
            def resolver(cite):
                return resolved(row) if cite['segment_id'] == SEGMENT else resolved(text, segment, 900)
            self.reject(evaluate(value, resolver=resolver), 'unsupported_supplement')
        text = '我校所有专业均招收全日制硕士研究生。'
        segment = 'sg_' + 'b' * 32
        value = draft(row, supplements=[{'field': 'study_mode', 'citation': citation(text, text, segment, 900)}])
        def resolver(cite):
            result = resolved(row) if cite['segment_id'] == SEGMENT else resolved(text, segment, 900)
            if cite['segment_id'] != SEGMENT:
                result['source']['year'] = 2026
            return result
        self.reject(evaluate(value, resolver=resolver), 'source_scope')

    def test_explicit_labeled_paragraph_is_supported_without_guessing_structure(self):
        row = '专业代码：081200；专业名称：计算机科学与技术；院系：计算机学院；研究方向：01人工智能；学习方式：全日制；初试科目：' + EXAMS
        payload = resolved(row)
        payload['block']['kind'] = 'paragraph'
        payload['block']['row_complete'] = None
        self.assertEqual(evaluate(draft(row), payload)['rejected'], [])

    def test_complete_single_line_and_separate_code_name_cells(self):
        for row in [ROW.replace('081200计算机科学与技术', '081200\t计算机科学与技术'), ROW.replace('\t', ' ')]:
            self.assertEqual(evaluate(draft(row), resolved(row))['rejected'], [])

    def test_a_suffix_or_alternative_cannot_be_dropped_from_the_exam_citation(self):
        row = ROW + '或801计算机基础'
        self.reject(evaluate(draft(row), resolved(row)), 'invalid_exams')

    def test_repeated_and_reordered_marked_units_cannot_be_flattened(self):
        for exams, code in [(EXAMS + '③302数学(二)', 'correlated_exams'),
                            (EXAMS + '科目三：302数学(二)', 'correlated_exams'),
                            (EXAMS.replace('①101思想政治理论②201英语(一)', '②201英语(一)①101思想政治理论'), 'invalid_exams')]:
            row = ROW.replace(EXAMS, exams)
            self.reject(evaluate(draft(row, exams), resolved(row)), code)

    def test_exact_quotes_are_kept_when_subject_names_contain_spaces(self):
        exams = EXAMS.replace('201英语(一)', '201 英语 (一)').replace('301数学(一)', '301 数学 (一)')
        row = ROW.replace(EXAMS, exams)
        program = evaluate(draft(row, exams), resolved(row))['accepted'][0]
        self.assertTrue(all(s['evidence']['quote'] == exams for s in program['subjects']))
        fields = program['candidate_proof']['fields']['subjects']
        for item in fields:
            for key in ('code', 'name'):
                ref = item[key]
                self.assertEqual(row[ref['char_start'] - 200:ref['char_end'] - 200], ref['quote'])
                self.assertEqual(ref['source_sha256'], 'a' * 64)

    def test_bad_requests_and_mixed_batches_have_bounded_structured_rejections(self):
        for args in [(None, 2027, SID, SEGMENT, []), (UNIT, True, SID, SEGMENT, []),
                     (UNIT, 2027, '', SEGMENT, []), (UNIT, 2027, SID, '', []),
                     (UNIT, 2027, SID, SEGMENT, {})]:
            result = validate_drafts(*args, lambda ref: resolved())
            self.reject(result, 'invalid_request')
        result = validate_drafts(UNIT, 2027, SID, SEGMENT, [draft(), None, draft()], lambda ref: resolved())
        self.assertEqual(len(result['accepted']), 1)
        self.assertEqual(result['rejected'][0]['index'], 1)

    def test_malformed_citations_fail_closed(self):
        for ref in [None, [], {}, {**citation(ROW), 'char_start': True},
                    {**citation(ROW), 'quote': ''}, {**citation(ROW), 'char_end': 0}]:
            self.reject(evaluate({**draft(), 'row': ref}), 'invalid_citation')

    def test_forged_extraction_identity_and_block_bounds_are_rejected(self):
        for field, value, code in [('extraction_id', 'other', 'stale_generation'),
                                   ('entry_id', 'other', 'stale_generation'),
                                   ('char_end', 201, 'citation_mismatch'),
                                   ('id', 'other', 'citation_mismatch')]:
            payload = resolved()
            payload['block'][field] = value
            self.reject(evaluate(payload=payload), code)

    def test_duplicate_supplement_and_unscoped_politics_are_rejected(self):
        text, segment = '某专业考试科目一均为：①101思想政治理论。', 'sg_' + 'b' * 32
        item = {'field': 'politics', 'citation': citation(text, text, segment, 900)}
        value = draft(supplements=[item])
        def resolver(ref):
            return resolved() if ref['segment_id'] == SEGMENT else resolved(text, segment, 900)
        self.reject(evaluate(value, resolver=resolver), 'unsupported_supplement')
        self.reject(evaluate(draft(supplements=[item, item])), 'invalid_draft')

    def test_authority_statement_inside_a_denial_is_not_a_supplement(self):
        quote, segment = '我校所有专业均招收全日制硕士研究生。', 'sg_' + 'b' * 32
        row = ROW.replace('\t全日制', '')
        for text in ['并非' + quote, '“' + quote + '”为错误说法。']:
            value = draft(row, supplements=[{'field': 'study_mode',
                           'citation': citation(quote, text, segment, 900)}])
            def resolver(ref):
                return resolved(row) if ref['segment_id'] == SEGMENT else resolved(text, segment, 900)
            self.reject(evaluate(value, resolver=resolver), 'unsupported_supplement')

    def test_unhashable_supplement_field_returns_a_rejection(self):
        self.reject(evaluate(draft(supplements=[{'field': [], 'citation': citation(ROW)}])), 'invalid_draft')

    def test_exact_other_columns_cannot_be_relabelled_as_department_or_direction(self):
        for changes in [{'department': '全日制'}, {'direction': '全日制'},
                        {'department': '01人工智能'}, {'direction': '001计算机学院'}]:
            self.reject(evaluate(draft(**changes)), 'unsupported_identity')

    def test_correlated_choice_note_cannot_hide_inside_a_local_course_name(self):
        exams = EXAMS.replace('②201英语(一)', '②201英语(一)或204英语(二)').replace(
            '③301数学(一)', '③301数学(一)或302数学(二)').replace(
            '④408计算机学科专业基础综合', '④801计算机基础（英语一对应数学一，英语二对应数学二）')
        row = ROW.replace(EXAMS, exams)
        self.reject(evaluate(draft(row, exams), resolved(row)), 'correlated_exams')
        row = ROW.replace('②201英语(一)', '②201英语(一)或204英语(二)') + '\t英语与数学须配套选择'
        self.reject(evaluate(draft(row, EXAMS.replace('②201英语(一)', '②201英语(一)或204英语(二)')),
                             resolved(row)), 'correlated_exams')

    def test_row_explicit_old_year_example_or_stopped_admission_cannot_be_promoted(self):
        for row in ['2026年历史目录\t' + ROW, '示例\t' + ROW, ROW + '\t2027年暂停招生',
                    ROW + '\t本专业已停止招生', ROW + '\t仅供参考']:
            self.reject(evaluate(draft(row), resolved(row)), 'unsupported_row_scope')

    def test_large_source_detail_preview_does_not_truncate_a_fully_read_row(self):
        payload = resolved()
        payload['source']['text_truncated'] = True
        self.assertEqual(evaluate(payload=payload)['rejected'], [])
        payload['source']['extraction']['extraction_status'] = 'partial'
        self.reject(evaluate(payload=payload), 'untrusted_citation')

    def test_numbered_universal_clause_is_separate_from_unrelated_numbered_notes(self):
        quote = '1.考试科目一均为：①101思想政治理论。'
        text = '备注：\n' + quote + '\n2.部分研究方向名称较去年有所调整。\n3.拟招生人数仅供参考。'
        exams = EXAMS.replace('①101思想政治理论', '')
        row, segment = ROW.replace(EXAMS, exams), 'sg_' + 'b' * 32
        value = draft(row, exams, supplements=[{'field': 'politics',
                      'citation': citation(quote, text, segment, 900)}])
        def resolver(ref):
            return resolved(row) if ref['segment_id'] == SEGMENT else resolved(text, segment, 900)
        result = evaluate(value, resolver=resolver)
        self.assertEqual(result['rejected'], [])
        self.assertEqual(result['accepted'][0]['subjects'][0]['code'], '101')

    def test_numbered_clause_cannot_hide_its_own_continuation_or_related_exception(self):
        quote = '1.考试科目一均为：①101思想政治理论。'
        exams = EXAMS.replace('①101思想政治理论', '')
        row, segment = ROW.replace(EXAMS, exams), 'sg_' + 'b' * 32
        for tail in ['但部分专业除外。\n2.拟招生人数仅供参考。',
                     '其中管理类专业科目一为199。\n2.拟招生人数仅供参考。',
                     '2.部分专业考试科目一不考政治。', '2.部分专业不适用上述规定。']:
            text = '备注：\n' + quote + '\n' + tail
            value = draft(row, exams, supplements=[{'field': 'politics',
                          'citation': citation(quote, text, segment, 900)}])
            def resolver(ref):
                return resolved(row) if ref['segment_id'] == SEGMENT else resolved(text, segment, 900)
            self.reject(evaluate(value, resolver=resolver), 'unsupported_supplement')

    def test_local_course_name_must_be_atomic_and_not_swallow_extra_exam_codes(self):
        for name in ['计算机基础（选英语一的考数学一，选英语二的考数学二）',
                     '计算机基础（选考英语一者须考数学一，选考英语二者须考数学二）',
                     '计算机基础 408计算机学科专业基础综合', '计算机基础（含选考说明）']:
            exams = EXAMS.replace('④408计算机学科专业基础综合', '④801' + name)
            row = ROW.replace(EXAMS, exams)
            self.reject(evaluate(draft(row, exams), resolved(row)), 'invalid_exams')
        for name in ['C/C++语言程序设计', '数据结构(一)', '程序设计（A）']:
            exams = EXAMS.replace('④408计算机学科专业基础综合', '④801' + name)
            row = ROW.replace(EXAMS, exams)
            self.assertEqual(evaluate(draft(row, exams), resolved(row))['rejected'], [])

    def test_universal_supplement_cannot_ignore_related_exceptions_in_read_neighbors(self):
        quote = '1.考试科目一均为：①101思想政治理论。'
        exams = EXAMS.replace('①101思想政治理论', '')
        row, segment = ROW.replace(EXAMS, exams), 'sg_' + 'b' * 32
        value = draft(row, exams, supplements=[{'field': 'politics',
                      'citation': citation(quote, quote, segment, 900)}])
        for neighbor in ['2.部分专业科目一不考政治。', '2.管理类专业科目一为199管理类综合能力。',
                         '2.部分专业不适用上述规定。']:
            def resolver(ref):
                if ref['segment_id'] == SEGMENT:
                    return resolved(row)
                result = resolved(quote, segment, 900)
                result['read_blocks'] = [result['block'], resolved(neighbor, 'sg_' + 'c' * 32, 1000)['block']]
                return result
            self.reject(evaluate(value, resolver=resolver), 'unsupported_supplement')

    def test_unrelated_read_neighbor_is_preserved_in_proof_without_restricting_politics(self):
        quote, neighbor = '1.考试科目一均为：①101思想政治理论。', '2.部分研究方向名称较去年有所调整。'
        exams = EXAMS.replace('①101思想政治理论', '')
        row, segment = ROW.replace(EXAMS, exams), 'sg_' + 'b' * 32
        value = draft(row, exams, supplements=[{'field': 'politics',
                      'citation': citation(quote, quote, segment, 900)}])
        def resolver(ref):
            if ref['segment_id'] == SEGMENT:
                return resolved(row)
            result = resolved(quote, segment, 900)
            result['read_blocks'] = [result['block'], resolved(neighbor, 'sg_' + 'c' * 32, 1000)['block']]
            return result
        result = evaluate(value, resolver=resolver)
        self.assertEqual(result['rejected'], [])
        proof = result['accepted'][0]['candidate_proof']['fields']['supplements'][0]
        self.assertEqual([item['quote'] for item in proof['read_contexts']], [quote, neighbor])
        self.assertTrue(all(item['generation_id'] == GENERATION for item in proof['read_contexts']))
        identifier = result['accepted'][0]['id']
        def narrow(ref):
            return resolved(row) if ref['segment_id'] == SEGMENT else resolved(quote, segment, 900)
        self.assertEqual(identifier, evaluate(value, resolver=narrow)['accepted'][0]['id'])

    def test_read_contexts_must_share_current_exact_version_and_include_the_cited_block(self):
        quote = '1.考试科目一均为：①101思想政治理论。'
        exams = EXAMS.replace('①101思想政治理论', '')
        row, segment = ROW.replace(EXAMS, exams), 'sg_' + 'b' * 32
        value = draft(row, exams, supplements=[{'field': 'politics',
                      'citation': citation(quote, quote, segment, 900)}])
        for fault in ['generation', 'bounds', 'missing']:
            def resolver(ref):
                if ref['segment_id'] == SEGMENT:
                    return resolved(row)
                result = resolved(quote, segment, 900)
                neighbor = resolved('2.方向名称调整。', 'sg_' + 'c' * 32, 1000)['block']
                if fault == 'generation': neighbor['generation_id'] = 'kg_' + 'b' * 32
                if fault == 'bounds': neighbor['char_end'] += 1
                result['read_blocks'] = [neighbor] if fault == 'missing' else [result['block'], neighbor]
                return result
            self.reject(evaluate(value, resolver=resolver), 'untrusted_citation')

    def test_mode_supplement_obeys_known_neighbor_modes_and_limiting_headings(self):
        quote = '我校所有专业均招收全日制硕士研究生。'
        row, segment = ROW.replace('\t全日制', ''), 'sg_' + 'b' * 32
        value = draft(row, supplements=[{'field': 'study_mode',
                      'citation': citation(quote, quote, segment, 900)}])
        for text, kind in [('MBA采用非全日制学习方式。', 'paragraph'), ('部分专业', 'heading')]:
            def resolver(ref):
                if ref['segment_id'] == SEGMENT:
                    return resolved(row)
                result = resolved(quote, segment, 900)
                neighbor = resolved(text, 'sg_' + 'c' * 32, 1000)['block']
                neighbor['kind'] = kind
                result['read_blocks'] = [result['block'], neighbor]
                return result
            self.reject(evaluate(value, resolver=resolver), 'unsupported_supplement')


if __name__ == '__main__':
    unittest.main()
