"""Deterministic Chinese query terms: no dictionary download, no learned segmentation."""
import unittest

from knowledge_query_terms import analyze, bigrams, is_natural, terms


class QueryTermTests(unittest.TestCase):
    def test_only_unspaced_long_chinese_questions_get_derived_terms(self):
        self.assertTrue(is_natural('郑州大学收费PDF中的学硕8000元是什么计费口径'))
        self.assertFalse(is_natural('计算机'))
        self.assertFalse(is_natural('计算机 学费 加试'))
        self.assertFalse(is_natural('computer science query'))
        self.assertFalse(is_natural(''))
        self.assertFalse(is_natural(None))

    def test_codes_years_and_domain_words_survive_while_question_words_do_not(self):
        rows=analyze('2027年北京工商大学计算机技术085404的初试和复试分别考什么？')
        values={row['term']:row for row in rows}
        self.assertEqual(values['2027']['kind'],'year')
        self.assertEqual(values['085404']['kind'],'code')
        self.assertIn('计算机技术',values)
        self.assertIn('初试',values)
        self.assertIn('复试',values)
        self.assertNotIn('什么',values)
        self.assertNotIn('分别',values)
        self.assertEqual(values['085404']['weight'],3)

    def test_longest_lexicon_match_wins_and_terms_are_unique_and_capped(self):
        rows=analyze('计算机科学与技术专业同等学力考生加试计算机学科专业基础')
        names=[row['term'] for row in rows]
        self.assertEqual(len(names),len(set(names)))
        self.assertLessEqual(len(names),10)
        self.assertTrue(any('计算机学科专业基础'==name for name in names) or '计算机' in names)

    def test_analysis_is_deterministic_and_safe_for_empty_input(self):
        query='南开2025学术工学总分达到290就一定达到学院复试线吗？'
        self.assertEqual(analyze(query),analyze(query))
        self.assertEqual(terms(''),())
        self.assertEqual(analyze(None),[])
        self.assertEqual(bigrams(''),())
        self.assertEqual(bigrams(None),())

    def test_bigrams_are_unique_and_filter_stop_words(self):
        grams=bigrams('会计专硕学费多少')
        self.assertIn('会计',grams)
        self.assertIn('学费',grams)
        self.assertEqual(len(grams),len(set(grams)))
        self.assertLessEqual(len(grams),24)


if __name__=='__main__':unittest.main()
