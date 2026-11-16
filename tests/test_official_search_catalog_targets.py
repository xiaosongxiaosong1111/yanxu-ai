"""Catalog discovery requires a directory title before stopping the search ladder."""
import unittest

from official_search import target_matches


class OfficialCatalogTargetTests(unittest.TestCase):
    def test_course_notices_are_not_catalog_hits(self):
        titles = (
            '【研究生招生】北京林业大学生态与自然保护学院 国家公园学院2027年研究生招生考试科目变更说明',
            '盐城工学院2027年硕士研究生招生考试初试科目参考书目',
            '2027年硕士研究生招生考试科目与考试大纲',
            '2027年硕士研究生招生考试科目咨询',
            '关于调整2027年硕士研究生招生专业目录的通知',
            '2027年硕士研究生招生专业目录的更正说明',
        )
        for title in titles:
            with self.subTest(title=title):
                self.assertFalse(target_matches({'title': title}, 2027, 'catalog'))

    def test_catalogues_are_discoverable_even_when_published_with_other_materials(self):
        titles = (
            '2027年青岛农业大学硕士研究生招生章程、专业目录和参考书目',
            '宿州学院2027年硕士研究生招生专业目录、初试自命题科目考试大纲和参考书目',
            '太原科技大学2027年硕士研究生招生目录、招生专业与咨询电话',
            '2027年硕士研究生招生专业目录（调整版）',
            '北方工业大学2027年硕士研究生招生学科专业目录',
        )
        for title in titles:
            with self.subTest(title=title):
                self.assertTrue(target_matches({'title': title}, 2027, 'catalog'))

    def test_subject_adjustment_can_still_match_its_own_search_kind(self):
        title = '北方工业大学关于2027年硕士研究生招生考试(初试)自命题科目调整的通知'
        self.assertTrue(target_matches({'title': title}, 2027, 'change'))


if __name__ == '__main__':
    unittest.main()
