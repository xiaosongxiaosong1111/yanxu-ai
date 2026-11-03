import unittest
import hashlib
from pathlib import Path
import tempfile

from collectors.catalog_batch import collect_school, unit_scope_supported


class CatalogOriginalScopeTests(unittest.TestCase):
    def test_pdf_title_scope_does_not_include_first_program_row_mode(self):
        unit = {'id': 'u', 'name': '测试大学'}
        text = ('测试大学2027年硕士研究生招生专业目录\n'
                '拟招生人数仅供参考\n院系所 专业 研究方向 学习方式 考试科目\n'
                '001学院 081200计算机科学与技术\n01人工智能 (1)全日制 21全国统考')
        self.assertTrue(unit_scope_supported(unit, 2027, [('pdf_row_v1', text)]))
        limited = text.replace('招生专业目录', '全日制招生专业目录', 1)
        self.assertFalse(unit_scope_supported(unit, 2027, [('pdf_row_v1', limited)]))

    def test_batch_attaches_replayable_same_source_fee_and_duration(self):
        html = '<h1>测试大学2027年硕士研究生招生专业目录</h1>'
        html += ('<table><tr><th>学院</th><th>专业</th><th>研究方向</th><th>学习方式</th><th>考试科目</th></tr>'
                 '<tr><td>001计算机学院</td><td>081200计算机科学与技术</td><td>00不区分方向</td><td>全日制</td>'
                 '<td>①101思想政治理论②201英语（一）③301数学（一）④408计算机学科专业基础</td></tr></table>'
                 '<table><tr><th>专业</th><th>学费（每生每年）</th><th>学制</th><th>学习方式</th></tr>'
                 '<tr><td>计算机科学与技术</td><td>8000元</td><td>3年</td><td>全日制</td></tr></table>')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / 'raw.html'
            path.write_text(html, 'utf-8')
            unit = {'id': 'u', 'name': '测试大学', 'official_url': 'https://www.a.edu.cn'}
            spec = {'unit_id': 'u', 'year': 2027, 'sources': [{
                'url': 'https://yz.a.edu.cn/catalog', 'source_path': str(path),
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'parser': 'html_table_v1'}]}
            result = collect_school(spec, unit, root / 'data/snapshots', project_root=root, offline=True)
            self.assertEqual(len(result['programs']), 1)
            row = result['programs'][0]
            self.assertEqual(row['tuition']['amount'], 8000)
            self.assertEqual(row['duration_years'], 3)
            self.assertFalse(row['eligibility_evidence_complete'])
            self.assertEqual(row['tuition']['evidence'][0]['document_id'], result['documents'][0]['id'])


if __name__ == '__main__':
    unittest.main()
