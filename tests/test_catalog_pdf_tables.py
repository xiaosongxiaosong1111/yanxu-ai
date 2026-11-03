import copy
import json
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from collectors.catalog_pdf_tables import _pdf_grid, _svg_grid, extract_pdf_layout, parse_catalog_pdf_tables
from collectors.catalog_pdf import parse_catalog_pdf
from collectors.catalogs import compact
from collectors.documents import DocumentError
from service_verification import canonical


def line(text, x, y, width=100):
    return {'text': text, 'x0': x, 'y0': y, 'x1': x + width, 'y1': y + 12,
            'words': [{'text': text, 'x0': x, 'y0': y, 'x1': x + width, 'y1': y + 12}]}


def layout(subjects=None, mode='全日制', extra=None):
    lines = [line('测试大学2027年' + mode + '硕士研究生招生专业目录', 40, 10, 500),
             line('院系所代码及名称：001 计算机学院', 40, 30, 200),
             line('专业代码、名称及研究方向', 40, 60, 150),
             line('拟招人数', 210, 60, 50), line('初试科目', 300, 60, 100),
             line('复试科目', 460, 60, 100), line('备注', 580, 60, 50),
             line('081200 计算机科学与技术', 40, 100, 180),
             line('01 人工智能', 40, 130), line('02 软件工程', 40, 150),
             line('F01 复试专业课', 460, 160), line('①同等学力加试', 580, 175)]
    for i, text in enumerate(subjects or ['①101思想政治理论', '②201英语（一）或202俄语', '③301数学（一）', '④408计算机学科专业基础']):
        lines.append(line(text, 290, 120 + 14 * i, 160))
    lines.extend(extra or [])
    return {'pages': [{'page': 1, 'width': 700, 'height': 900, 'lines': lines,
                       'grid': [[x, 90, x, 220] for x in [30, 200, 280, 450, 570, 690]] + [[30, 90, 690, 90], [30, 220, 690, 220], [30, 115, 280, 115], [30, 145, 280, 145]],
                       'words': [w for row in lines for w in row['words']]}],
            'raw_text': '\n'.join(row['text'] for row in lines) + '\f'}


class PdfTableTests(unittest.TestCase):
    def setUp(self):
        self.unit = {'id': '1', 'name': '测试大学', 'province': '测试省'}
        self.doc = {'id': 'd', 'unit_id': '1', 'year': 2027, 'url': 'https://test.edu.cn/2027.pdf'}

    def test_shared_initial_cell_and_or_combinations_are_preserved(self):
        source = layout()
        before = copy.deepcopy(source)
        result = parse_catalog_pdf_tables(source, self.unit, 2027, self.doc)
        self.assertEqual(result['issues'], [])
        self.assertEqual(result['expected_records'], 2)
        self.assertEqual(len(result['programs']), 2)
        self.assertEqual(result['programs'][0]['department'], '计算机学院')
        self.assertEqual(result['programs'][0]['name'], '计算机科学与技术')
        self.assertEqual([s['code'] for s in result['programs'][0]['subjects']], ['101', '201', '202', '301', '408'])
        self.assertEqual(result['programs'][0]['subjects'][1]['choice_group'], 'exam-unit-2')
        self.assertNotIn('F01', str(result['programs'][0]['subjects']))
        self.assertEqual(source, before)
        for program in result['programs']:
            self.assertEqual(program['catalog_parser'], 'pdf_table_v1')
            self.assertTrue(program['requires_review'])
            self.assertFalse(program['eligibility_evidence_complete'])
            for ref in program['evidence'] + program['exam_evidence']:
                self.assertIn(canonical(ref['quote']), canonical(source['raw_text']))

    def test_two_and_three_exam_units_are_valid(self):
        for subjects in [['①199管理类综合能力', '②204英语（二）'], ['①101思想政治理论', '②201英语（一）', '③306临床医学综合能力（西医）']]:
            with self.subTest(subjects=subjects):
                result = parse_catalog_pdf_tables(layout(subjects), self.unit, 2027, self.doc)
                self.assertEqual(len(result['programs']), 2)
                self.assertEqual(result['issues'], [])

    def test_unstated_direction_stays_empty_and_scope_is_explicit(self):
        source = layout()
        source['pages'][0]['lines'] = [row for row in source['pages'][0]['lines'] if not row['text'].startswith(('01 ', '02 '))]
        result = parse_catalog_pdf_tables(source, self.unit, 2027, self.doc)
        self.assertEqual(result['expected_records'], 1)
        self.assertEqual(result['programs'][0]['direction'], '')
        self.assertEqual(result['programs'][0]['direction_scope'], 'program')

    def test_missing_mode_or_exam_unit_is_partial(self):
        for source in [layout(mode=''), layout(['①101思想政治理论', '②201英语（一）', '④408计算机学科专业基础'])]:
            result = parse_catalog_pdf_tables(source, self.unit, 2027, self.doc)
            self.assertEqual(result['expected_records'], 2)
            self.assertEqual(result['programs'], [])
            self.assertTrue(result['issues'])

    def test_coupled_exam_schemes_and_recommended_only_rows_are_not_flattened(self):
        for note in ['考试科目任选一组：方案一，方案二', '仅招收推免生']:
            result = parse_catalog_pdf_tables(layout(extra=[line(note, 580, 210)]), self.unit, 2027, self.doc)
            self.assertEqual(result['programs'], [])
            self.assertTrue(result['issues'])

    def test_wrong_school_year_and_unknown_header_fail_closed(self):
        for unit, year, source in [({**self.unit, 'name': '别的大学'}, 2027, layout()), (self.unit, 2028, layout()), (self.unit, 2027, {'pages': [], 'raw_text': ''})]:
            self.assertTrue(parse_catalog_pdf_tables(source, unit, year, self.doc)['issues'])

    def test_unrecognized_catalog_page_cannot_disappear_from_completeness(self):
        source = layout()
        second = [line('085404 计算机技术', 40, 100), line('01 数据工程', 40, 120), line('①101思想政治理论', 330, 120)]
        source['pages'].append({'page': 2, 'width': 700, 'height': 900, 'lines': second,
                                'words': [w for row in second for w in row['words']], 'grid': []})
        source['raw_text'] += '\n'.join(row['text'] for row in second) + '\f'
        result = parse_catalog_pdf_tables(source, self.unit, 2027, self.doc)
        self.assertTrue(result['issues'])
        self.assertGreater(result['expected_records'], 2)

    def test_svg_strokes_and_thin_filled_rectangles_define_actual_cells(self):
        svg = '<svg><path stroke="black" d="M 0 10 L 100 10"/><path fill="black" d="M 20 10 L 20.5 10 L 20.5 100 L 20 100 Z M 20 10"/><path fill="black" d="M 30 30 C 40 40 50 50 60 60"/></svg>'
        actual = _svg_grid(ET.fromstring(svg))
        self.assertEqual(len(actual), 2)
        self.assertEqual(actual[0], [0, 10, 100, 10])
        self.assertEqual(actual[1], [20.25, 10, 20.25, 100])
        transformed = '<svg><g transform="matrix(2, 0, 0, 2, 10, 20)"><path stroke="black" d="M 0 10 L 100 10" transform="matrix(1, 0, 0, 1, 5, 5)"/></g></svg>'
        self.assertEqual(_svg_grid(ET.fromstring(transformed)), [[20, 50, 220, 50]])

    def test_four_digit_discipline_code_is_preserved_without_inventing_suffix(self):
        source = layout()
        for item in source['pages'][0]['lines']:
            item['text'] = item['text'].replace('081200', '0812')
            for word in item['words']:
                word['text'] = word['text'].replace('081200', '0812')
        source['raw_text'] = source['raw_text'].replace('081200', '0812')
        result = parse_catalog_pdf_tables(source, self.unit, 2027, self.doc)
        self.assertEqual(result['programs'][0]['code'], '0812')
        self.assertEqual(result['programs'][0]['code_level'], 'discipline')

    def test_reference_topics_do_not_become_registration_directions(self):
        policy = '仅标“★”的专业按研究方向招生。其他专业均按专业招生，网上报名时应选择“00不区分研究方向”，表中所列研究方向仅供参考。'
        source = layout(extra=[line(policy, 40, 45, 600)])
        result = parse_catalog_pdf_tables(source, self.unit, 2027, self.doc)
        self.assertEqual(len(result['programs']), 1)
        self.assertEqual(result['expected_records'], 1)
        self.assertEqual(result['programs'][0]['direction'], '00不区分研究方向')
        self.assertEqual(result['programs'][0]['research_topics'], ['01人工智能', '02软件工程'])
        for row in source['pages'][0]['lines']:
            row['text'] = row['text'].replace('计算机科学与技术', '计算机科学与技术★')
            for word in row['words']:
                word['text'] = word['text'].replace('计算机科学与技术', '计算机科学与技术★')
        source['raw_text'] = source['raw_text'].replace('计算机科学与技术', '计算机科学与技术★')
        actual = parse_catalog_pdf_tables(source, self.unit, 2027, self.doc)
        self.assertEqual(len(actual['programs']), 2)
        self.assertEqual(actual['programs'][0]['name'], '计算机科学与技术')

    def test_two_real_independent_adapters_agree_on_all_48_medical_rows(self):
        source = json.loads((Path(__file__).parent / 'fixtures/catalog_pdf/qmu-2027-layout.json').read_text('utf8'))
        unit = {'id': '2125721162', 'name': '齐齐哈尔医学院', 'province': '黑龙江'}
        doc = {**self.doc, 'unit_id': unit['id']}
        actual = parse_catalog_pdf_tables(source, unit, 2027, doc)
        expected = parse_catalog_pdf(source['raw_text'], unit, 2027, doc)
        key = lambda p: (p['department_code'], p['code'], compact(p['direction']), p['study_mode'])
        signature = lambda p: [(s['exam_unit'], s['code'], compact(s['name']), s['scope']) for s in p['subjects']]
        self.assertEqual(actual['issues'], [])
        self.assertEqual(actual['expected_records'], 48)
        self.assertEqual({key(p): signature(p) for p in actual['programs']}, {key(p): signature(p) for p in expected['programs']})
        pages = source['raw_text'].split('\f')
        for program in actual['programs']:
            for ref in program['evidence'] + program['exam_evidence']:
                self.assertIn(canonical(ref['quote']), canonical(pages[ref['page']-1]))

    def test_poppler_bbox_extraction_uses_raw_engine_for_exact_page_citations(self):
        xml = b'<doc><page width="700" height="900"><line xMin="1" yMin="2" xMax="10" yMax="14"><word xMin="1" yMin="2" xMax="10" yMax="14">test</word></line></page></doc>'
        process = subprocess.CompletedProcess([], 0, xml, b'')
        with patch.dict(os.environ, {'GRAD_PDFTOTEXT': '/isolated/pdftotext'}), patch('collectors.catalog_pdf_tables.subprocess.run', return_value=process), patch('collectors.catalog_pdf_tables.extract_pdf_text', return_value='RAW\f'):
            actual = extract_pdf_layout('saved.pdf')
        self.assertEqual(actual['raw_text'], 'RAW\f')
        self.assertEqual(actual['pages'][0]['lines'][0]['text'], 'test')
        self.assertEqual(actual['pages'][0]['grid'], [])

    def test_missing_old_or_malformed_pdf_tools_fail_with_a_clear_error(self):
        for error in [subprocess.CalledProcessError(99, ['pdftotext']), OSError('missing'), subprocess.TimeoutExpired('pdftotext', 90)]:
            with self.subTest(error=type(error).__name__), patch.dict(os.environ, {'GRAD_PDFTOTEXT': '/isolated/pdftotext'}), patch('collectors.catalog_pdf_tables.subprocess.run', side_effect=error):
                self.assertRaises(DocumentError, extract_pdf_layout, 'saved.pdf')
        with patch.dict(os.environ, {'GRAD_PDFTOTEXT': ''}), patch('collectors.catalog_pdf_tables.Path.is_file', return_value=False), patch('collectors.catalog_pdf_tables.shutil.which', return_value=None):
            self.assertRaises(DocumentError, extract_pdf_layout, 'saved.pdf')
        with patch.dict(os.environ, {'GRAD_PDFTOTEXT': '/isolated/pdftotext'}), patch('collectors.catalog_pdf_tables.subprocess.run', return_value=subprocess.CompletedProcess([], 0, b'<broken>', b'')):
            self.assertRaises(DocumentError, extract_pdf_layout, 'saved.pdf')

    def test_svg_tool_failure_cannot_be_replaced_with_guessed_row_boundaries(self):
        with patch('collectors.catalog_pdf_tables.subprocess.run', side_effect=OSError('missing')):
            self.assertRaises(DocumentError, _pdf_grid, 'cairo', 'saved.pdf', 1)
        svg = b'<svg><path stroke="black" d="M 0 10 L 100 10"/></svg>'
        with patch('collectors.catalog_pdf_tables.subprocess.run', return_value=subprocess.CompletedProcess([], 0, svg, b'')):
            self.assertEqual(_pdf_grid('cairo', 'saved.pdf', 1), [[0, 10, 100, 10]])

    def test_retest_inside_the_same_cell_is_not_part_of_initial_subject_name(self):
        source = layout(extra=[line('复试专业课：F001综合基础', 290, 185, 150), line('同等学力加试：①801数学', 290, 200, 150)])
        result = parse_catalog_pdf_tables(source, self.unit, 2027, self.doc)
        self.assertEqual(result['issues'], [])
        self.assertEqual(result['programs'][0]['subjects'][-1]['name'], '计算机学科专业基础')
        self.assertNotIn('F001', str(result['programs'][0]['exam_evidence']))

    def test_next_year_mentioned_in_body_does_not_replace_the_actual_pdf_title_year(self):
        source = layout(extra=[line('详情另见《测试大学2027年硕士研究生招生专业目录》', 580, 230)])
        source['pages'][0]['lines'][0]['text'] = source['pages'][0]['lines'][0]['text'].replace('2027', '2026')
        source['pages'][0]['lines'][0]['words'][0]['text'] = source['pages'][0]['lines'][0]['text']
        major = next(row for row in source['pages'][0]['lines'] if row['text'].startswith('081200'))
        major['text'] += '（全日制）'
        major['words'][0]['text'] = major['text']
        source['raw_text'] = '\n'.join(row['text'] for row in source['pages'][0]['lines']) + '\f'
        result = parse_catalog_pdf_tables(source, self.unit, 2027, self.doc)
        self.assertEqual(result['programs'], [])
        self.assertTrue(result['issues'])

    def publication_case(self, label='2027年全日制硕士研究生招生专业目录.pdf', body=''):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        (root / 'source.pdf').write_bytes(b'%PDF-source')
        html = '<h1>测试大学2027年硕士研究生招生专业目录</h1>' + body + '<a href="/2027.pdf">' + label + '</a>'
        (root / 'publication.html').write_text(html, 'utf8')
        doc = {**self.doc, 'local_path': 'source.pdf', 'sha256': hashlib.sha256((root/'source.pdf').read_bytes()).hexdigest(),
               'publication_page': {'id': 'pub', 'unit_id': '1', 'year': 2027, 'url': 'https://test.edu.cn/article.htm',
                                    'local_path': 'publication.html', 'sha256': hashlib.sha256((root/'publication.html').read_bytes()).hexdigest(), 'content_type': 'text/html'}}
        source = layout(mode='')
        source['pages'][0]['lines'][0]['text'] = '计算机学院硕士招生专业目录'
        source['pages'][0]['lines'][0]['words'][0]['text'] = '计算机学院硕士招生专业目录'
        source['raw_text'] = '\n'.join(row['text'] for row in source['pages'][0]['lines']) + '\f'
        return source, {**self.unit, 'official_url': 'https://test.edu.cn/'}, doc, root

    def test_saved_publication_proves_a_faculty_attachment_without_school_or_year(self):
        source, unit, doc, root = self.publication_case()
        actual = parse_catalog_pdf_tables(source, unit, 2027, doc, project_root=root)
        self.assertEqual(actual['issues'], [])
        self.assertEqual(len(actual['programs']), 2)
        self.assertEqual(actual['documents'][0]['kind'], 'publication')
        self.assertEqual(actual['programs'][0]['study_mode'], '全日制')
        self.assertIn('pub', {ref['document_id'] for ref in actual['programs'][0]['evidence']})
        self.assertIn('d', {ref['document_id'] for ref in actual['programs'][0]['evidence']})

    def test_parent_context_does_not_override_contradictory_pdf_year_or_school(self):
        for title in ['测试大学2026年全日制硕士研究生招生专业目录', '另一所大学2027年全日制硕士研究生招生专业目录']:
            source, unit, doc, root = self.publication_case()
            source['pages'][0]['lines'][0]['text'] = title
            source['pages'][0]['lines'][0]['words'][0]['text'] = title
            source['raw_text'] = '\n'.join(row['text'] for row in source['pages'][0]['lines']) + '\f'
            actual = parse_catalog_pdf_tables(source, unit, 2027, doc, project_root=root)
            with self.subTest(title=title):
                self.assertEqual(actual['programs'], [])
                self.assertTrue(actual['issues'])

    def test_valid_parent_year_does_not_invent_learning_mode_or_resolve_conflicting_modes(self):
        for label, body in [('计算机学院目录.pdf', ''), ('2027年全日制硕士招生目录.pdf', '<p>我校2027年仅招收非全日制硕士研究生。</p>')]:
            source, unit, doc, root = self.publication_case(label, body)
            actual = parse_catalog_pdf_tables(source, unit, 2027, doc, project_root=root)
            self.assertEqual(actual['programs'], [])
            self.assertTrue(actual['issues'])


if __name__ == '__main__':
    unittest.main()
