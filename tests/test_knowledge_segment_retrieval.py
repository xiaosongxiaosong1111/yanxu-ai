"""Dispersed keywords locate one original without combining different sources into facts."""
import hashlib
from pathlib import Path
import tempfile
import unittest

from knowledge_store import KnowledgeStore


class SegmentRetrievalTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='n3-document-locate-')
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.store=KnowledgeStore(self.root)

    def save(self,name,*,unit='u1',year=2027,complete=True):
        texts=[f'{year}年硕士研究生招生专业目录\n081200 计算机科学与技术 初试科目101、201、301、408。',
               '收费说明\n学费每生每年8000元。', '报考说明\n计算机跨专业考生须参加加试。' if complete else '报考说明。']
        raw=('%PDF-1.4\nfixture '+name).encode()
        source={'url':'https://grad.example.edu.cn/'+name+'.pdf','title':f'{year}年硕士招生专业目录',
                'unit_id':unit,'unit_name':'合成大学','year':year,'status':'official_original_read',
                'sha256':hashlib.sha256(raw).hexdigest()}
        document={'raw':raw,'url':source['url'],'text':'\n'.join(texts),
                  'pages':[{'page':i,'text':value} for i,value in enumerate(texts,1)],'attribution_status':'verified'}
        return self.store.save_source(source,document=document)

    def test_document_all_terms_then_segment_any_term_is_explicit_and_scope_safe(self):
        wanted=self.save('wanted')
        self.save('partial',complete=False)
        self.save('other-unit',unit='u2')
        self.save('other-year',year=2026)
        result=self.store.search_segments('计算机 学费 加试',unit_id='u1',year=2027,page_size=1)
        self.assertEqual(result['segments'][0]['entry_id'],wanted['id'])
        self.assertEqual(result['segments'][0]['page_start'],3)
        self.assertEqual(result['match_mode'],'document_terms')
        self.assertEqual(result['segments'][0]['match_mode'],'document_terms')
        self.assertGreater(result['total'],1)
        second=self.store.search_segments('计算机 学费 加试',unit_id='u1',year=2027,page=2,page_size=1)
        self.assertNotEqual(second['segments'][0]['id'],result['segments'][0]['id'])

    def test_terms_from_separate_originals_never_qualify_one_another(self):
        self.save('partial',complete=False)
        result=self.store.search_segments('学费 加试',unit_id='u1',year=2027)
        self.assertEqual(result['segments'],[])

    def test_direct_hits_and_out_of_range_pages_do_not_switch_to_expansion(self):
        self.save('wanted')
        result=self.store.search_segments('计算机 加试',page=999)
        self.assertEqual(result['segments'],[])
        self.assertGreater(result['total'],0)
        self.assertEqual(result['match_mode'],'segment_terms')

    def test_natural_question_uses_derived_terms_instead_of_the_whole_sentence(self):
        wanted=self.save('wanted')
        result=self.store.search_segments('2027年合成大学计算机专业跨专业考生要参加加试吗？',unit_id='u1',year=2027,page_size=8)
        self.assertTrue(result['segments'])
        self.assertEqual(result['match_mode'],'segment_terms')
        self.assertIn(wanted['id'],{item['entry_id'] for item in result['segments']})
        self.assertEqual(result['segments'][0]['match_mode'],'segment_terms')

    def test_natural_question_with_no_known_terms_never_returns_the_whole_corpus(self):
        self.save('wanted')
        result=self.store.search_segments('天气晴朗适合去公园散步吗',unit_id='u1',year=2027)
        self.assertEqual(result['segments'],[])

    def test_entry_search_accepts_the_same_natural_question(self):
        wanted=self.save('wanted')
        result=self.store.search('2027年合成大学计算机专业跨专业考生要参加加试吗？')
        self.assertIn(wanted['id'],{item['id'] for item in result['entries']})

    def test_tampered_fallback_original_is_removed_before_count_and_pagination(self):
        bad=self.save('bad')
        good=self.save('good')
        (self.root/'runtime/research-sources'/(bad['sha256']+'.bin')).write_bytes(b'changed')
        result=self.store.search_segments('计算机 学费 加试',page_size=1)
        self.assertEqual(result['segments'][0]['entry_id'],good['id'])
        self.assertEqual(result['total'],3)


if __name__=='__main__':unittest.main()
