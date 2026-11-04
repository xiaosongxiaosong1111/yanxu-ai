"""Structural segmentation preserves exact extraction offsets and source scope."""
from copy import deepcopy
import hashlib
import unittest

from knowledge_segments import SEGMENT_VERSION, build_segments


def extraction(pages, format='pdf', separator='\n'):
    text = separator.join(pages)
    offset = 0
    mapped = []
    for number, value in enumerate(pages, 1):
        mapped.append({'page': number, 'text': value, 'char_start': offset,
                       'char_end': offset + len(value)})
        offset += len(value) + len(separator)
    return {'text': text, 'pages': mapped, 'format': format,
            'extraction_status': 'complete', 'extractor_version': 'fixture-v1',
            'text_sha256': hashlib.sha256(text.encode()).hexdigest()}


class KnowledgeSegmentTests(unittest.TestCase):
    def assert_spans(self, document, rows):
        self.assertTrue(rows)
        for index, row in enumerate(rows):
            self.assertEqual(row['ordinal'], index)
            self.assertEqual(row['text'], document['text'][row['char_start']:row['char_end']])
            self.assertLessEqual(len(row['text']), row['max_chars'])
            self.assertEqual(row['previous_ordinal'], index - 1 if index else None)
            self.assertEqual(row['next_ordinal'], index + 1 if index + 1 < len(rows) else None)
            for span in row['context_spans']:
                self.assertEqual(span['text'], document['text'][span['char_start']:span['char_end']])

    def test_long_document_keeps_late_pages_and_offsets(self):
        pages = ['一、报考条件\n' + ('公开招生正文。' * 240)] * 20
        pages += ['二、计算机学院\n仅允许本科学历报考，专科须另行核验。']
        document = extraction(pages)
        before = deepcopy(document)
        rows = build_segments(document)
        late = [row for row in rows if '专科须另行核验' in row['text']]
        self.assertEqual(len(late), 1)
        self.assertEqual(late[0]['page_start'], 21)
        self.assertGreater(late[0]['char_start'], 32000)
        self.assertEqual(document, before)
        self.assert_spans(document, rows)

    def test_single_page_clause_after_1600_characters_is_not_lost(self):
        document = extraction(['报考条件\n' + '已发布正文。' * 360 + '\n\n禁止依据历史条件推断下一年资格。'])
        rows = build_segments(document)
        clause = next(row for row in rows if '禁止依据历史条件' in row['text'])
        self.assertGreater(clause['char_start'], 1600)
        self.assertEqual(clause['page_start'], 1)
        self.assert_spans(document, rows)

    def test_short_pdf_pages_keep_the_matching_clause_on_its_real_page(self):
        pages = ['招生目录'] + ['目录正文。'] * 19
        pages += ['第21页条款：跨专业考生须参加加试课程。', '目录结尾。']
        document = extraction(pages)
        rows = build_segments(document)
        clause = next(row for row in rows if '跨专业' in row['text'])
        self.assertEqual((clause['page_start'], clause['page_end']), (21, 21))
        self.assertTrue(clause['continues_previous'])
        self.assertTrue(clause['continues_next'])
        self.assertTrue(all(row['page_start'] == row['page_end'] for row in rows))
        self.assert_spans(document, rows)

    def test_cross_page_pdf_rule_retains_neighbors_and_continuation(self):
        document = extraction(['计算机学院\n本专业不接受跨专业申请。',
                               '但数学专业申请人可提交材料核验。'])
        rows = build_segments(document)
        rule = next(row for row in rows if '不接受' in row['text'])
        exception = next(row for row in rows if '但数学专业' in row['text'])
        self.assertEqual((rule['page_start'], exception['page_start']), (1, 2))
        self.assertEqual(rule['next_ordinal'], exception['ordinal'])
        self.assertTrue(rule['continues_next'])
        self.assertTrue(exception['continues_previous'])
        self.assertIn('计算机学院', exception['section_path'])
        self.assert_spans(document, rows)

    def test_repeated_sentence_has_distinct_real_occurrences(self):
        phrase = '同等学力考生须加试两门课程。'
        document = extraction(['第一章 报考条件\n' + phrase, '', '第二章 学院要求\n' + phrase])
        rows = build_segments(document)
        occurrences = [row for row in rows if phrase in row['text']]
        self.assertEqual([row['page_start'] for row in occurrences], [1, 3])
        self.assertNotEqual(occurrences[0]['char_start'], occurrences[1]['char_start'])
        self.assert_spans(document, rows)

    def test_table_header_is_separate_exact_context_across_pages(self):
        document = extraction(['一、非全日制硕士\n专业代码\t专业名称\t学制\t学费\n085400\t电子信息\t3\t14000',
                               '085405\t软件工程\t3\t18000'])
        rows = build_segments(document)
        software = next(row for row in rows if '软件工程' in row['text'])
        self.assertEqual(software['kind'], 'table_row')
        self.assertEqual(software['page_start'], 2)
        self.assertNotIn('专业代码', software['text'])
        self.assertTrue(any(span['role'] == 'table_header' and '专业代码' in span['text']
                            for span in software['context_spans']))
        self.assertIn('非全日制硕士', ' '.join(software['section_path']))
        self.assertIn('专业代码', software['search_text'])
        self.assert_spans(document, rows)

    def test_new_college_does_not_inherit_previous_college_scope(self):
        document = extraction(['计算机学院\n仅允许本科生报考。\n\n人工智能学院\n专科考生须满足学院要求。'])
        rows = build_segments(document)
        second = next(row for row in rows if '专科考生' in row['text'])
        self.assertIn('人工智能学院', second['section_path'])
        self.assertNotIn('计算机学院', second['section_path'])
        self.assertFalse(any('计算机学院' in span['text'] for span in second['context_spans']))

    def test_numbered_requirements_keep_their_college_and_program_scope(self):
        document = extraction(['计算机学院\n081200 计算机科学与技术\n一、报考条件\n本专业不接受同等学力考生。'])
        rows = build_segments(document)
        rule = next(row for row in rows if '不接受' in row['text'])
        self.assertEqual(rule['section_path'], ['计算机学院', '081200 计算机科学与技术', '一、报考条件'])
        self.assert_spans(document, rows)

    def test_numbered_college_with_parenthesized_alias_replaces_previous_scope(self):
        document = extraction(['001 计算机学院\n081200 计算机科学与技术\n本专业不接受同等学力报考。\n\n'
                               '002 软件学院（示范性软件学院）\n085405 软件工程\n本专业接受符合条件的同等学力报考。'])
        rows = build_segments(document)
        rule = next(row for row in rows if '本专业接受' in row['text'])
        self.assertEqual(rule['section_path'], ['002 软件学院（示范性软件学院）', '085405 软件工程'])
        self.assertFalse(any('001 计算机学院' in span['text'] for span in rule['context_spans']))
        self.assert_spans(document, rows)

    def test_short_college_name_also_replaces_previous_scope(self):
        document = extraction(['计算机学院\n081200 计算机科学与技术\n本科生可申请。\n\n'
                               '医学院\n100100 基础医学\n本专业须核验医学背景。'])
        rows = build_segments(document)
        rule = next(row for row in rows if '须核验' in row['text'])
        self.assertEqual(rule['section_path'], ['医学院', '100100 基础医学'])
        self.assert_spans(document, rows)

    def test_new_program_and_subsection_clear_previous_table_header(self):
        for transition in ['085400 电子信息\n以下为培养年限与学费，不沿用上一专业的考试科目表',
                           '（二）学制与学费']:
            with self.subTest(transition=transition):
                document = extraction(['计算机学院\n081200 计算机科学与技术\n'
                                       '专业代码\t专业名称\t政治\t外语\t业务课一\t业务课二\n'
                                       '081200\t计算机科学与技术\t101\t201\t301\t408',
                                       transition + '\n085400\t电子信息\t3\t10000'])
                rows = build_segments(document)
                row = next(row for row in rows if row['text'].startswith('085400\t'))
                self.assertFalse(any(span['role'] == 'table_header' for span in row['context_spans']))
                self.assertTrue(row['table_context_unresolved'])
                self.assert_spans(document, rows)

    def test_tsv_first_and_last_empty_cells_keep_their_exact_positions(self):
        header = '专业代码\t专业名称\t业务课一\t业务课二'
        value = '\t计算机科学与技术\t301\t'
        document = extraction([header + '\n' + value], format='xlsx')
        rows = build_segments(document)
        row = next(row for row in rows if '计算机科学与技术' in row['text'])
        self.assertEqual(row['text'], value)
        self.assertEqual(len(row['text'].split('\t')), 4)
        self.assertTrue(row['row_complete'])
        self.assert_spans(document, rows)

    def test_tsv_row_with_only_one_numeric_code_does_not_become_a_program_heading(self):
        value = '085410\t人工智能\t仅限本科考生\t'
        document = extraction(['专业代码\t专业名称\t备注\t业务课二\n' + value], format='xlsx')
        rows = build_segments(document)
        row = next(row for row in rows if '085410' in row['text'])
        self.assertEqual(row['kind'], 'table_row')
        self.assertEqual(row['text'], value)
        self.assertTrue(row['searchable'])
        self.assertTrue(row['row_complete'])
        self.assert_spans(document, rows)

    def test_rules_with_column_names_or_college_names_remain_searchable(self):
        for clause in ['考生的专业代码和专业名称必须与本科学籍记录一致',
                       '本专业仅接受计算机学院', '学费及学制均以正式公告为准',
                       '专业代码与专业名称须按目录填报', '仅限计算机学院',
                       '本条适用于计算机学院', '毕业于计算机学院']:
            with self.subTest(clause=clause):
                document = extraction(['一、资格要求\n' + clause])
                rows = build_segments(document)
                row = next(row for row in rows if clause in row['text'])
                self.assertEqual(row['kind'], 'paragraph')
                self.assertTrue(row['searchable'])
                self.assertIn('一、资格要求', row['section_path'])

    def test_negation_and_following_exception_remain_together(self):
        document = extraction(['一、申请条件\n本专业不接受跨专业申请。\n\n但数学专业申请人可提交材料核验。'])
        rows = build_segments(document)
        rule = next(row for row in rows if '不接受跨专业' in row['text'])
        self.assertIn('但数学专业', rule['text'])
        self.assert_spans(document, rows)

    def test_long_blocks_are_bounded_and_linked_without_fabricated_overlap(self):
        document = extraction(['一、研究范围\n' + '需要逐项核验原文中的限制条件。' * 60])
        rows = build_segments(document, max_chars=100)
        body = [row for row in rows if row['kind'] == 'paragraph']
        self.assertGreater(len(body), 2)
        self.assertTrue(body[0]['continues_next'])
        self.assertTrue(body[-1]['continues_previous'])
        self.assertTrue(all(left['char_end'] <= right['char_start'] for left, right in zip(rows, rows[1:])))
        self.assert_spans(document, rows)

    def test_unicode_offsets_are_codepoints_and_idempotent(self):
        document = extraction(['一、专业要求\n计算机💡方向保留真实Unicode位置。'], format='html')
        first = build_segments(document)
        self.assertEqual(first, build_segments(deepcopy(document)))
        self.assertEqual(SEGMENT_VERSION, 'structure-v1')
        self.assert_spans(document, first)

    def test_form_feed_and_blank_pages_do_not_renumber_pages(self):
        document = extraction(['第一章 条件\n普通条款。', '', '第二章 例外\n但本学院另有要求。'], separator='\f')
        rows = build_segments(document)
        self.assertEqual(next(row for row in rows if '本学院另有要求' in row['text'])['page_start'], 3)
        self.assert_spans(document, rows)

    def test_program_title_can_be_wrapped_in_either_order(self):
        for title in ['085410\n人工智能', '人工智能\n085410', '人工智能（085410）']:
            with self.subTest(title=title):
                document = extraction(['计算机学院\n' + title, '仅接收具有本科学历的申请人。'])
                rows = build_segments(document)
                rule = next(row for row in rows if '仅接收' in row['text'])
                self.assertTrue(any('085410' in item and '人工智能' in item for item in rule['section_path']))
                self.assert_spans(document, rows)

    def test_narrative_with_numbers_is_not_invented_as_a_table(self):
        document = extraction(['2027年学制为3年，学费8000元\n考生必须如实填写联系电话，不得提供他人号码。'])
        rows = build_segments(document)
        rule = next(row for row in rows if '学费8000' in row['text'])
        self.assertEqual(rule['kind'], 'paragraph')
        self.assertTrue(rule['searchable'])

    def test_standalone_exam_codes_are_retained_with_their_names(self):
        document = extraction(['一、初试科目\n101\n思想政治理论\n201\n英语一\n301\n数学一\n408\n计算机学科专业基础'])
        rows = build_segments(document)
        body = '\n'.join(row['text'] for row in rows if row['searchable'])
        for code in ['101', '201', '301', '408']:
            self.assertIn(code, body)

    def test_segment_limit_rejects_excessive_structure_without_silent_truncation(self):
        document = extraction(['一、章节\n正文。\n' * 4])
        from unittest.mock import patch
        with patch('knowledge_segments.MAX_SEGMENTS', 3), self.assertRaises(ValueError):
            build_segments(document)

    def test_oversized_table_row_is_explicitly_a_partial_row(self):
        document = extraction(['专业代码\t专业名称\t备注\n085410\t人工智能\t' + '仅限已核验资格的申请人' * 20])
        rows = build_segments(document, max_chars=100)
        fragments = [row for row in rows if row['kind'] == 'table_row']
        self.assertGreater(len(fragments), 1)
        self.assertTrue(all(row['row_complete'] is False for row in fragments))
        self.assertTrue(fragments[0]['continues_next'])
        self.assert_spans(document, rows)

    def test_navigation_and_contact_lines_are_not_searchable_facts(self):
        document = extraction(['首页 招生信息 联系我们\n联系电话：02212345678\n\n一、报考条件\n本科生须取得学士学位。'])
        rows = build_segments(document)
        contact = [row for row in rows if '02212345678' in row['text']]
        self.assertTrue(contact)
        self.assertTrue(all(row['searchable'] is False for row in contact))
        self.assertTrue(next(row for row in rows if '本科生须' in row['text'])['searchable'])

    def test_unsupported_or_ocr_only_documents_do_not_create_fake_segments(self):
        for status in ['needs_ocr', 'unsupported']:
            document = extraction(['只有附件标题'])
            document['extraction_status'] = status
            self.assertEqual(build_segments(document), [])
        self.assertEqual(build_segments(extraction([''])), [])

    def test_invalid_limits_and_mapping_are_rejected(self):
        document = extraction(['真实原文'])
        for limit in [True, 0, -1, 63, 12001, '100']:
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                build_segments(document, max_chars=limit)
        broken = deepcopy(document)
        broken['pages'][0]['text'] = '伪造原文'
        with self.assertRaises(ValueError): build_segments(broken)
        for update in [{'text_sha256': '0' * 64}, {'pages': {}}, {'text': 42},
                       {'format': 'unknown'}, {'extraction_status': 'fake'}, {'text': '正文\x00'}]:
            with self.subTest(update=update), self.assertRaises(ValueError):
                build_segments({**document, **update})


if __name__ == '__main__':
    unittest.main()
