import json
import unittest

from research_service import ResearchService


def block(text, **changes):
    return {'id': 'sg_' + 'a' * 32, 'entry_id': 'k_' + 'a' * 24,
            'extraction_id': 'ex_1', 'generation_id': 'kg_1', 'kind': 'table_row',
            'char_start': 100, 'char_end': 100 + len(text), 'text': text,
            'row_complete': True, 'content_truncated': False, **changes}


class ReadProjectionTests(unittest.TestCase):
    def project(self, value):
        return json.loads(ResearchService._model_result(value))

    def test_complete_read_over_900_chars_remains_exact_with_offsets(self):
        original = block('专业目录原句' * 230)
        result = self.project({'segment': original, 'contexts': [], 'neighbors': [], 'truncated': False})
        self.assertEqual(result['segment']['text'], original['text'])
        self.assertEqual(result['segment']['char_end'] - result['segment']['char_start'], len(original['text']))
        self.assertFalse(result['truncated'])

    def test_oversized_read_retains_structure_and_requires_narrower_read(self):
        result = self.project({'segment': block('完整目录原句' * 4000), 'contexts': [], 'neighbors': []})
        self.assertNotIn('preview', result)
        self.assertTrue(result['segment']['content_truncated'])
        self.assertTrue(result['needs_narrower_read'])
        self.assertFalse(result['segment'].get('text'))
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False)), 6000)

    def test_never_cut_a_context_block_or_leak_unlisted_metadata(self):
        original = block('①101思想政治理论②201英语一③301数学一④408计算机学科专业基础')
        original['source'] = {'id': 'k_' + 'a' * 24, 'url': 'https://example.edu.cn/a',
                              'raw': 'private raw', 'text': 'full body', 'api_key': 'no-key',
                              'year': 2027, 'quality': {'state': 'accepted', 'reusable': True}}
        result = self.project({'segment': original,
                               'contexts': [block('巨大表头' * 2000, kind='table_header')],
                               'neighbors': [block('完整相邻行', char_start=300, char_end=305)]})
        self.assertEqual(result['segment']['text'], original['text'])
        self.assertEqual(result['contexts'], [])
        self.assertEqual(result['neighbors'][0]['text'], '完整相邻行')
        self.assertTrue(result['needs_narrower_read'])
        encoded = json.dumps(result, ensure_ascii=False)
        self.assertNotIn('private raw', encoded)
        self.assertNotIn('full body', encoded)
        self.assertNotIn('api_key', encoded)

    def test_unusually_large_metadata_still_respects_the_delivery_budget(self):
        original = block('完整目录行', heading_path=['超大标题' * 2000])
        original['source'] = {'title': '巨大标题' * 3000, 'url': 'https://example.edu.cn/' + 'x' * 10000}
        result = self.project({'segment': original, 'contexts': [], 'neighbors': []})
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False)), 6000)
        self.assertEqual(result['segment']['text'], '完整目录行')

    def test_maximal_valid_metadata_yields_to_exact_row_body(self):
        original = block('完整目录行', original_url='https://example.edu.cn/' + 'x' * 2000)
        original['source'] = {'url': original['original_url'], 'title': '大标题' * 130,
                              **{key: 'x' * 120 for key in ('id', 'knowledge_id', 'unit_id', 'unit_name',
                                  'status', 'validity', 'sha256')}}
        result = self.project({'segment': original, 'contexts': [], 'neighbors': []})
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False)), 6000)
        self.assertEqual(result['segment']['text'], '完整目录行')


if __name__ == '__main__':
    unittest.main()
