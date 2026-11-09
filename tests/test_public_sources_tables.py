"""HTML canonical originals preserve actual rows, not invented cell inheritance."""
import unittest

from public_sources import extract_saved_document


def extract(body):
    raw = ('<meta charset="utf-8">' + body).encode('utf-8')
    return extract_saved_document(raw, 'text/html', 'https://example.edu.cn/catalog')['text']


class HTMLTableTextTests(unittest.TestCase):
    def test_cell_markup_and_indentation_keep_one_real_row_with_column_boundaries(self):
        text = extract('''<article><h1>2027招生专业目录</h1><table>
          <tr><th><p>学院</p></th><th><p>专业</p></th><th>考试科目</th></tr>
          <tr>
            <td><p>001计算机学院</p></td>
            <td><p><b>081200</b>计算机科学与技术</p></td>
            <td><p>①101思想政治理论</p><p>②201英语一<br>③301数学一④408计算机学科专业基础</p></td>
          </tr>
        </table><p>目录外说明。</p><p>第二段。</p></article>''')
        self.assertIn('学院\t专业\t考试科目', text)
        row = next(line for line in text.splitlines() if '081200' in line)
        self.assertEqual(len(row.split('\t')), 3)
        self.assertIn('④408计算机学科专业基础', row)
        self.assertTrue(text.endswith('目录外说明。\n第二段。'))

    def test_rowspan_never_copies_identity_into_next_row_and_hidden_text_stays_hidden(self):
        text = extract('''<main><table>
        <tr><td rowspan="2">081200计算机科学与技术</td><td>01人工智能</td><td>④408</td></tr>
        <tr><td>02软件工程</td><td>④801<span hidden>假的999</span><span style="display:none">假的888</span></td></tr>
        </table></main>''')
        rows = text.splitlines()
        self.assertEqual(len(rows), 2)
        self.assertNotIn('081200', rows[1])
        self.assertNotIn('假的', text)
        self.assertEqual(rows[1].split('\t'), ['02软件工程', '④801'])

    def test_nested_tables_do_not_flatten_outer_and_inner_identity_into_one_row(self):
        text = extract('''<main><table><tr><td>外层学院</td><td><table>
          <tr><td>081200计算机科学与技术</td><td>④408</td></tr>
          </table></td><td>外层其他科目</td></tr></table></main>''')
        inner = next(line for line in text.splitlines() if '081200' in line)
        self.assertNotIn('外层学院', inner)
        self.assertNotIn('外层其他科目', inner)


if __name__ == '__main__':
    unittest.main()
