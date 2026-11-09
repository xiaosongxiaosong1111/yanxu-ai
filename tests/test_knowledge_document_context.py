"""Exact hits keep priority without suppressing the same original's scope."""
import hashlib
from pathlib import Path
import tempfile
import unittest

from knowledge_store import KnowledgeStore


class DocumentContextTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='document-context-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = KnowledgeStore(self.root)

    def save(self, suffix, *, unit='u1', year=2027, missing=False, pending=False):
        pages = [f'{year}年硕士研究生招生专业目录\n081200 计算机科学与技术 初试科目101、201、301、408。',
                 '一、适用范围\n调剂目标为电子信息，报考资格仍须核对。' if not missing else '一、适用范围\n报考资格须核对。',
                 '二、考试要求\n408 计算机学科专业基础，英语科目选择须符合调剂规定。' if not missing else '二、考试要求\n408 计算机学科专业基础，英语科目须核对。',
                 '三、后续流程\n调剂考生按通知完成确认。' if not missing else '三、后续流程\n考生按通知完成确认。']
        raw = ('%PDF-1.4 isolated context fixture ' + suffix).encode()
        source = {'url': 'https://grad.example.edu.cn/' + suffix + '.pdf',
                  'title': '学校公开说明' if pending else f'{year}年硕士研究生招生专业目录', 'unit_id': unit, 'unit_name': '样本大学',
                  'year': year, 'status': 'official_original_read', 'sha256': hashlib.sha256(raw).hexdigest()}
        document = {'raw': raw, 'url': source['url'], 'attribution_status': 'verified',
                    'text': '\n'.join(pages), 'pages': [{'page': index, 'text': text}
                                                        for index, text in enumerate(pages, 1)]}
        saved = self.store.save_source(source, document=document)
        self.assertEqual(saved['quality']['state'], 'pending' if pending else 'accepted')
        return saved

    def search(self, **changes):
        return self.store.search_segments(query='408 英语 调剂', unit_id='u1', year=2027, **changes)

    def test_exact_row_first_and_same_original_scope_is_returned_as_document_context(self):
        wanted = self.save('wanted')
        result = self.search()
        rows = result['segments']
        self.assertTrue(all(row['entry_id'] == wanted['id'] for row in rows))
        self.assertTrue(all(term in rows[0]['text'] for term in ('408', '英语', '调剂')))
        self.assertEqual(rows[0]['match_mode'], 'segment_terms')
        context = next((row for row in rows if '调剂目标为' in row['text']), None)
        self.assertIsNotNone(context, 'An exact table row must not hide its separately stated scope.')
        self.assertEqual(context['match_mode'], 'document_terms')

    def test_context_cannot_borrow_words_from_another_source_or_bypass_school_year(self):
        wanted = self.save('wanted')
        self.save('missing', missing=True)
        self.save('other-unit', unit='u2')
        self.save('other-year', year=2026)
        result = self.search()
        self.assertEqual({row['entry_id'] for row in result['segments']}, {wanted['id']})
        self.assertTrue(any(row['match_mode'] == 'document_terms' for row in result['segments']))

    def test_direct_page_access_and_small_pages_use_the_same_order_and_total(self):
        self.save('wanted')
        whole = self.search(page_size=100)
        pages = [self.search(page=index + 1, page_size=1) for index in range(whole['total'])]
        self.assertEqual([page['segments'][0]['id'] for page in pages], [row['id'] for row in whole['segments']])
        self.assertTrue(all(page['total'] == whole['total'] for page in pages))
        self.assertTrue(any(row['match_mode'] == 'document_terms' for row in whole['segments']))
        self.assertEqual(self.search(page=whole['total'] + 1, page_size=1)['segments'], [])

    def test_current_original_is_revalidated_before_counts_and_context_pagination(self):
        bad = self.save('bad')
        good = self.save('good')
        (self.root / 'runtime/research-sources' / (bad['sha256'] + '.bin')).write_bytes(b'changed')
        result = self.search(page_size=100)
        self.assertEqual({row['entry_id'] for row in result['segments']}, {good['id']})
        self.assertTrue(any(row['match_mode'] == 'document_terms' for row in result['segments']))
        self.assertEqual(result['total'], len(result['segments']))

    def test_pending_or_revoked_documents_never_supply_supplementary_context(self):
        good = self.save('good')
        self.save('pending', pending=True)
        revoked = self.save('revoked')
        self.store.change_validity(revoked['id'], 'revoked', 'isolated regression fixture')
        result = self.search(page_size=100)
        self.assertEqual({row['entry_id'] for row in result['segments']}, {good['id']})
        self.assertTrue(any(row['match_mode'] == 'document_terms' for row in result['segments']))


if __name__ == '__main__':
    unittest.main()
