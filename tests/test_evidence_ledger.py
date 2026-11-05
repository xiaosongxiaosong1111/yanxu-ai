import unittest

from evidence_ledger import audit


def ref(url, quote, document='doc-a', **extra):
    return {'document_id': document, 'url': url, 'quote': quote, **extra}


def decision(reasons, **overrides):
    return {'candidate_id': 'c1', 'unit_name': '甲大学', 'status': 'matched',
            'reasons': reasons, **overrides}


class EvidenceLedgerTests(unittest.TestCase):
    def test_two_documents_with_quotes_are_corroborated(self):
        result = audit([decision([{'field': 'tuition', 'label': '学费', 'status': 'matched', 'evidence': [
            ref('https://a.edu.cn/catalog', '学费 8000 元', 'doc-a'),
            ref('https://b.edu.cn/charter', '每学年学费8000元', 'doc-b')]}])])
        claim = result['claims'][0]
        self.assertEqual(claim['assessment'], 'corroborated')
        self.assertEqual(claim['independent_sources'], 2)
        self.assertEqual(claim['documents'], ['doc-a', 'doc-b'])
        self.assertEqual(result['summary']['coverage_ratio'], 1.0)
        self.assertEqual(result['summary']['duplicate_quote_clusters'], 0)
        self.assertIn('不证明来源彼此独立', result['note'])

    def test_identical_quote_on_two_urls_is_one_source_and_a_duplicate_cluster(self):
        quote = '报考条件以本校研究生院公告为准。'
        result = audit([decision([{'field': 'eligibility', 'label': '资格', 'status': 'unknown', 'evidence': [
            ref('https://a.edu.cn/notice', quote, 'doc-a'),
            ref('https://mirror.example/notice', quote, 'doc-b')]}])])
        claim = result['claims'][0]
        self.assertEqual(claim['assessment'], 'single_source')
        self.assertEqual(claim['independent_sources'], 1)
        self.assertEqual(result['summary']['duplicate_quote_clusters'], 1)
        self.assertEqual(result['duplicate_clusters'][0]['document_count'], 2)

    def test_same_document_with_many_quotes_is_still_one_source(self):
        result = audit([decision([{'field': 'catalog', 'label': '目录', 'status': 'matched', 'evidence': [
            ref('https://a.edu.cn/catalog', '计算机科学与技术', 'doc-a', page=1),
            ref('https://a.edu.cn/catalog', '①101思想政治理论', 'doc-a', source_locator={'row': 2})]}])])
        claim = result['claims'][0]
        self.assertEqual(claim['independent_sources'], 1)
        self.assertEqual(claim['assessment'], 'single_source')
        self.assertEqual(claim['locator_refs'], 2)

    def test_missing_url_or_quote_never_counts_as_evidence(self):
        result = audit([decision([{'field': 'catalog', 'label': '目录', 'status': 'unknown', 'evidence': [
            {'url': 'javascript:alert(1)', 'quote': '伪造'},
            {'url': 'https://a.edu.cn/catalog', 'quote': '   '},
            {'url': 'https://a.edu.cn/catalog'},
            'bad']}])])
        claim = result['claims'][0]
        self.assertEqual(claim['evidence_refs'], 0)
        self.assertEqual(claim['assessment'], 'unsourced')
        self.assertEqual(result['summary']['unsourced_claims'], 1)
        self.assertEqual(result['summary']['coverage_ratio'], 0.0)

    def test_audit_is_deterministic_bounded_and_never_upgrades_status(self):
        rows = [decision([{'field': f'f{index}', 'label': f'字段{index}', 'status': 'unknown',
                           'evidence': [ref('https://a.edu.cn/x', f'第{index}条公开说明', f'doc-{index}')]}])
                for index in range(120)]
        first, second = audit(rows), audit(rows)
        self.assertEqual(first, second)
        self.assertEqual(len(first['claims']), 96)
        self.assertEqual(first['summary']['claim_count'], 120)
        self.assertEqual(first['claims'][0]['candidate_status'], 'matched')
        self.assertEqual(first['claims'][0]['status'], 'unknown')

    def test_duplicate_clusters_are_counted_in_full_but_listed_within_the_cap(self):
        rows=[]
        for index in range(25):
            quote=f'完全相同说明{index}'
            rows.append(decision([{'field': f'f{index}', 'label': '字段', 'status': 'unknown', 'evidence': [
                ref('https://a.edu.cn/x', quote, f'a-{index}'),
                ref('https://b.edu.cn/x', quote, f'b-{index}')]}], candidate_id=f'c{index}'))
        result=audit(rows)
        self.assertEqual(result['summary']['duplicate_quote_clusters'],25)
        self.assertEqual(len(result['duplicate_clusters']),20)
        self.assertTrue(all(row['document_count']==2 for row in result['duplicate_clusters']))

    def test_invalid_input_returns_an_explicit_empty_ledger(self):
        for value in (None, {}, 'bad', [None, {'reasons': 'bad'}, {'reasons': [None]}]):
            with self.subTest(value=value):
                result = audit(value)
                self.assertEqual(result['version'], 'evidence_ledger_v1')
                self.assertEqual(result['summary']['claim_count'], 0)
                self.assertEqual(result['claims'], [])


if __name__ == '__main__':
    unittest.main()
