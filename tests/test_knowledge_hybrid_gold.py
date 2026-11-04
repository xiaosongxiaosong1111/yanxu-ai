"""Contracts for the frozen keyword-versus-hybrid comparison, without loading the model."""
import unittest

from tools import evaluate_knowledge_gold as gold
from tools import evaluate_knowledge_hybrid as hybrid_gold


UNSAFE = {
    'PF02': ['enrichment-zzu-tuition-current-b4ba23c5f304', 'support-zzu-fees-publication-052606791aaa'],
    'catalog-11': ['doc-8c2a6f9ac1e47065d65d-ed9234849449'],
    'catalog-12': ['doc-8c2a6f9ac1e47065d65d-ed9234849449'],
}


class FrozenComparisonContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = gold.load_fixture(gold.PROJECT_ROOT, gold.FIXTURE)

    def test_frozen_corpus_identity_and_challenge_population_are_unchanged(self):
        counts = self.corpus['counts']
        self.assertEqual(counts['questions'], 34)
        self.assertEqual(counts['sources'], 29)
        self.assertEqual(counts['trusted_recall_applicable_cases'], 31)
        self.assertEqual(gold.CORPUS_SHA256, hybrid_gold.gold.CORPUS_SHA256)
        self.assertEqual(gold.FREEZE_SHA256, hybrid_gold.gold.FREEZE_SHA256)
        challenges = {q['id'] for q in self.corpus['questions'] if not q['retrieval_recall_applicable']}
        self.assertEqual(challenges, set(UNSAFE))

    def test_metadata_year_is_never_adopted_when_the_runtime_has_no_actual_year(self):
        indexed = {source['id']: {'entry': {'year': source['actual_year'],
                                           'quality': {'state': 'pending'}}}
                   for source in self.corpus['sources']}
        arms = {name: [{'id': item, 'returned_source_ids': [], 'degraded': False,
                        'degraded_reason': ''} for item in UNSAFE]
                for name in ('frozen_retrieval_query', 'human_keyword', 'human_hybrid')}
        for question in self.corpus['questions']:
            if not question['retrieval_recall_applicable']:
                arms['frozen_retrieval_query'].append({'id': question['id'], 'returned_source_ids': [],
                                                       'degraded': False, 'degraded_reason': ''})
        # Keep only the challenge rows each arm needs.
        arms = {name: [row for row in rows if row['id'] in UNSAFE] for name, rows in arms.items()}
        result = hybrid_gold._challenges(self.corpus, indexed, arms, set())
        by_id = {item['id']: item for item in result}
        self.assertEqual(set(by_id), set(UNSAFE))
        for identifier, unsafe_ids in UNSAFE.items():
            item = by_id[identifier]
            self.assertEqual(item['unsafe_year_source_ids'], unsafe_ids)
            for unsafe in unsafe_ids:
                self.assertIsNone(item['source_runtime_years'][unsafe])
            self.assertTrue(item['unsafe_sources_absent_from_eligible_vectors'])
            self.assertTrue(all(arm['unsafe_sources_absent'] for arm in item['arms'].values()))
            if unsafe_ids[0] in item['metadata_year_not_adopted']:
                self.assertIsNone(item['metadata_year_not_adopted'][unsafe_ids[0]]['runtime_year'])
                self.assertEqual(item['metadata_year_not_adopted'][unsafe_ids[0]]['metadata_year'], 2027)
        self.assertFalse(by_id['catalog-11']['publication_chain_runtime_supported'])
        self.assertTrue(by_id['catalog-11']['publication_chain'])
        self.assertEqual(by_id['catalog-11']['publication_chain'][0]['audited_link_year'], 2027)

    def test_unsafe_source_returned_by_any_arm_is_visible_as_a_failure(self):
        indexed = {source['id']: {'entry': {'year': source['actual_year'],
                                           'quality': {'state': 'pending'}}}
                   for source in self.corpus['sources']}
        arms = {name: [{'id': identifier, 'returned_source_ids': [],
                        'degraded': False, 'degraded_reason': ''} for identifier in UNSAFE]
                for name in ('frozen_retrieval_query', 'human_keyword', 'human_hybrid')}
        arms['human_keyword'][0]['returned_source_ids'] = UNSAFE['PF02']
        arms['frozen_retrieval_query'][2]['returned_source_ids'] = UNSAFE['catalog-12']
        result = {item['id']: item for item in hybrid_gold._challenges(self.corpus, indexed, arms, set())}
        self.assertFalse(result['PF02']['arms']['human_keyword']['unsafe_sources_absent'])
        self.assertTrue(result['PF02']['arms']['human_hybrid']['unsafe_sources_absent'])
        self.assertFalse(result['catalog-12']['arms']['frozen_retrieval_query']['unsafe_sources_absent'])

    def test_operational_summary_keeps_unknown_tokens_out_of_known_totals(self):
        rows = [
            {'usage': {'embedding_calls': 1, 'successful_embedding_calls': 1, 'input_tokens': 7},
             'usage_known': True, 'degraded': False, 'degraded_reason': '', 'latency_ms': 10,
             'returned_count': 2, 'read_chars': 100},
            {'usage': {'embedding_calls': 1, 'successful_embedding_calls': 0, 'input_tokens': None},
             'usage_known': False, 'degraded': True, 'degraded_reason': 'vector_not_ready',
             'latency_ms': 30, 'returned_count': 1, 'read_chars': 50},
        ]
        summary = hybrid_gold._operational(rows)
        self.assertEqual(summary['embedding_calls'], 2)
        self.assertEqual(summary['successful_embedding_calls'], 1)
        self.assertEqual(summary['input_tokens_known'], 7)
        self.assertEqual(summary['usage_unknown_calls'], 1)
        self.assertEqual(summary['degraded_reasons'], {'vector_not_ready': 1})
        self.assertEqual(summary['latency_ms_p50'], 30)

    def test_evaluate_rejects_changed_split_or_unbounded_limit_before_any_model_work(self):
        for split, limit in (('blind', 8), ('all', 0), ('all', 51), ('all', True)):
            with self.subTest(split=split, limit=limit), self.assertRaises(ValueError):
                hybrid_gold.evaluate(split=split, top_k=limit)


if __name__ == '__main__':
    unittest.main()
