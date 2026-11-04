"""Real exam-column labels become exact table context, never ordinary obligations."""
import unittest

from knowledge_segments import build_segments
from test_knowledge_segments import extraction


class KnowledgeExamHeaderTests(unittest.TestCase):
    def test_exam_subject_header_is_retained_as_its_own_cross_page_context(self):
        document = extraction([
            '计算机学院\n专业代码\t专业名称\t考试科目',
            '081200\t计算机科学与技术\t101 201 301 408',
        ])
        rows = build_segments(document)
        header = next(row for row in rows if row['text'] == '专业代码\t专业名称\t考试科目')
        self.assertEqual(header['kind'], 'table_header')
        self.assertFalse(header['searchable'])
        course = next(row for row in rows if '408' in row['text'])
        self.assertEqual(course['page_start'], 2)
        contexts = [span for span in course['context_spans'] if span['role'] == 'table_header']
        self.assertEqual([span['text'] for span in contexts], [header['text']])
        self.assertEqual(document['text'][contexts[0]['char_start']:contexts[0]['char_end']], header['text'])

    def test_sentence_mentioning_exam_subjects_remains_searchable_rule(self):
        rule = '考生须核对专业代码、专业名称与考试科目，不得跨专业误报。'
        rows = build_segments(extraction(['计算机学院\n' + rule]))
        segment = next(row for row in rows if rule in row['text'])
        self.assertEqual(segment['kind'], 'paragraph')
        self.assertTrue(segment['searchable'])


if __name__ == '__main__':
    unittest.main()
