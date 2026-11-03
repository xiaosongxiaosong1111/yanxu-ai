from copy import deepcopy
import hashlib
import os
from pathlib import Path
import tempfile
import unittest

from collectors.catalog_hit_public import parse_hit_public,verify_hit_publication,PARSER


class HitPublicTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.unit={'id':'368087','unit_code':'10213','name':'哈尔滨工业大学','province':'黑龙江','official_url':'https://yzb.hit.edu.cn/'}
        self.pdf_url='https://yzb.hit.edu.cn/_upload/article/files/12/cb/333643d84636a30ab6b586266704/501748af-b6cb-4851-8931-49196a409f82.pdf'
        self.entry_url='https://yzb.hit.edu.cn/2026/0924/c22272a401989/page.htm'
        self.parent_html='<h1>哈尔滨工业大学</h1><div portletmode="simpleArticleAttri"><div class="tit">2027年统考硕士研究生招生目录</div><div class="wp_articlecontent"><div class="wp_pdf_player" id="actual-embed" pdfsrc="'+self.pdf_url.replace('https://yzb.hit.edu.cn','')+'"></div></div></div>'
        self.parent=self.save('parent',self.parent_html.encode(),self.entry_url,'catalog_context')
        self.document={**self.save('pdf',b'%PDF-1.4\noriginal fixture source',self.pdf_url,'catalog'),'catalog_parser':PARSER,'publication_page':self.parent}
        self.header=['专业\n（标注*号为专业学位）','研究方向','学习方式','考试科目']
        self.exam='①101思想政治理论②201英语（一）或202俄语或203日语③301数学（一）④801控制原理'
        self.layout={'pages':[self.page(1,'001航天学院',[
            ['081100控制科学与工程','10不区分研究方向.','(1)全日制',self.exam],
            ['*085400电子信息','34集成电路工程（深圳校区校企联培）','(2)非全日制',self.exam]])]}
        self.refresh_raw()

    def save(self,identity,raw,url,kind):
        path=self.root/'data/snapshots'/identity;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
        return {'id':identity,'unit_id':self.unit['id'],'year':2027,'kind':kind,'status':'parsed','url':url,
                'local_path':path.relative_to(self.root).as_posix(),'sha256':hashlib.sha256(raw).hexdigest(),'request_method':'GET'}

    def page(self,number,department,records):
        rows=[{'texts':self.header,'cells':[[n*100,0,(n+1)*100,10] for n in range(4)]}]
        if department:rows.append({'texts':[department,'','',''],'cells':[[0,10,400,20],None,None,None]})
        for values in records:
            y=10*len(rows);rows.append({'texts':values,'cells':[[n*100,y,(n+1)*100,y+10] for n in range(4)]})
        return {'page':number,'width':500,'height':800,'tables':[{'bbox':[0,0,400,10*len(rows)],'rows':rows}]}

    def refresh_raw(self):
        self.layout['raw_text']='\f'.join('\n'.join(' '.join(row['texts']) for table in page['tables'] for row in table['rows']) for page in self.layout['pages'])+'\f'

    def parse(self,layout=None,document=None,unit=None,year=2027):
        return parse_hit_public(layout or self.layout,unit or self.unit,year,document or self.document,project_root=self.root)

    def replace_parent(self,html):
        self.parent=self.save('parent',html.encode(),self.entry_url,'catalog_context');self.document={**self.document,'publication_page':self.parent}

    def test_actual_pdf_embed_is_verified_without_fabricating_an_anchor_quote(self):
        result=verify_hit_publication(self.document,self.unit,2027,project_root=self.root)
        self.assertTrue(result['verified']);self.assertEqual(result['attachment_locator']['attribute'],'pdfsrc')
        self.assertEqual(result['attachment_locator']['attribute_value'],self.pdf_url.replace('https://yzb.hit.edu.cn',''))
        self.assertEqual(result['attachment_locator']['url'],self.pdf_url)
        self.assertTrue(all(ref['quote'] in self.parent_html for ref in result['evidence']))

    def test_article_scope_ignores_navigation_titles_and_cannot_borrow_a_title_outside_article(self):
        self.replace_parent('<div class="tit">新闻动态</div>'+self.parent_html+'<div class="tit">咨询电话</div>')
        self.assertTrue(verify_hit_publication(self.document,self.unit,2027,project_root=self.root)['verified'])
        self.replace_parent(self.parent_html.replace('<div class="tit">2027年统考硕士研究生招生目录</div>','')+'<div class="tit">2027年统考硕士研究生招生目录</div>')
        self.assertFalse(verify_hit_publication(self.document,self.unit,2027,project_root=self.root)['verified'])

    def test_all_physical_rows_keep_identity_mode_and_original_location_punctuation(self):
        result=self.parse();self.assertEqual(result['expected_records'],2);self.assertEqual(result['issues'],[])
        first,second=result['programs'];self.assertEqual(first['id'],'368087:2027:001:081100:10:全日制')
        self.assertEqual(first['direction'],'10 不区分研究方向.');self.assertEqual(first['department'],'航天学院')
        self.assertEqual(second['direction'],'34 集成电路工程（深圳校区校企联培）');self.assertEqual(second['study_mode'],'非全日制')
        self.assertIsNone(first['province']);self.assertIsNone(second['province']);self.assertEqual(second['campus'],'深圳校区校企联培')

    def test_star_only_proves_professional_degree_and_unmarked_stays_unknown(self):
        first,second=self.parse()['programs'];self.assertIsNone(first['degree_type']);self.assertEqual(second['degree_type'],'专业学位')
        self.assertTrue(any('标注*号为专业学位' in ref['quote'] for ref in second['evidence']))

    def test_or_subjects_remain_choices_in_their_actual_exam_unit(self):
        first=self.parse()['programs'][0]
        self.assertEqual([s['code'] for s in first['subjects']],['101','201','202','203','301','801'])
        self.assertEqual([s['exam_unit'] for s in first['subjects']],[1,2,2,2,3,4])
        self.assertEqual(len(first['exam_combinations']),3);self.assertTrue(first['exam_matching_supported'])

    def test_visible_prose_year_and_title_are_required_even_with_a_plausible_url(self):
        for html in [self.parent_html.replace('2027','2026'),self.parent_html.replace('统考硕士','推免硕士'),self.parent_html.replace('哈尔滨工业大学','其他学校')]:
            self.replace_parent(html);self.assertFalse(verify_hit_publication(self.document,self.unit,2027,project_root=self.root)['verified'])

    def test_embed_must_be_original_visible_main_article_and_exact_pdf(self):
        for html in [self.parent_html.replace('wp_pdf_player','other-player'),self.parent_html.replace(' pdfsrc=',' hidden pdfsrc='),
                     self.parent_html.replace('wp_articlecontent','sidebar'),self.parent_html.replace('501748af-b6cb','other-document'),
                     self.parent_html.replace('pdfsrc=','href=')]:
            self.replace_parent(html)
            self.assertFalse(verify_hit_publication(self.document,self.unit,2027,project_root=self.root)['verified'])

    def test_duplicate_embed_is_ambiguous(self):
        self.replace_parent(self.parent_html.replace('</div></div>','</div><div class="wp_pdf_player" pdfsrc="'+self.pdf_url+'"></div></div>'))
        with self.assertRaises(ValueError):self.parse()

    def test_parent_and_pdf_source_sha_are_replayed_on_warm_calls(self):
        self.parse()
        for document in [self.parent,self.document]:
            path=self.root/document['local_path'];original=path.read_bytes();stamp=path.stat()
            path.write_bytes(original.replace(original[-1:],b'X'));os.utime(path,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
            with self.assertRaises(ValueError):self.parse()
            path.write_bytes(original)

    def test_foreign_host_path_bounds_wrong_unit_year_or_request_method_are_rejected(self):
        for document in [{**self.document,'url':self.pdf_url.replace('yzb.hit.edu.cn','example.edu.cn')},
                         {**self.document,'local_path':'../outside.pdf'},{**self.document,'request_method':'POST'}]:
            with self.assertRaises(ValueError):self.parse(document=document)
        with self.assertRaises(ValueError):self.parse(unit={**self.unit,'id':'other'})
        with self.assertRaises(ValueError):self.parse(year=2028)

    def test_an_unreadable_non_pdf_or_missing_publication_does_not_parse(self):
        for doc in [{**self.document,'local_path':'data/snapshots/missing.pdf'},{**self.document,'publication_page':None}]:
            with self.assertRaises(ValueError):self.parse(document=doc)
        fresh=self.save('not-pdf',b'HTML page',self.pdf_url,'catalog')
        with self.assertRaises(ValueError):self.parse(document={**fresh,'publication_page':self.parent})

    def test_cross_page_department_inheritance_is_chapter_scoped_and_next_heading_replaces_it(self):
        self.layout['pages']+=[self.page(2,None,[['081100控制科学与工程','11继续方向','(1)全日制',self.exam]]),
                               self.page(3,'903哈尔滨工业大学（深圳）',[['081100控制科学与工程','31深圳方向','(1)全日制',self.exam]])]
        self.refresh_raw();rows=self.parse()['programs'];self.assertEqual([p['department_code'] for p in rows],['001','001','001','903'])
        self.assertTrue(any(ref.get('page')==1 and ref['quote']=='001航天学院' for ref in rows[2]['evidence']))

    def test_direction_without_a_proven_initial_department_is_not_guessed(self):
        self.layout['pages'][0]['tables'][0]['rows'].pop(1);self.refresh_raw()
        with self.assertRaises(ValueError):self.parse()

    def test_merged_department_must_cover_the_real_whole_grid(self):
        self.layout['pages'][0]['tables'][0]['rows'][1]['cells'][0][2]=200
        with self.assertRaises(ValueError):self.parse()

    def test_missing_cell_wrong_column_width_or_overlapping_rows_are_rejected(self):
        for change in ['cell','width','overlap']:
            layout=deepcopy(self.layout);row=layout['pages'][0]['tables'][0]['rows'][2]
            if change=='cell':row['cells'][1]=None
            elif change=='width':row['cells'][1][0]=101
            else:row['cells'][0][1]=0
            with self.assertRaises(ValueError):self.parse(layout=layout)

    def test_raw_text_and_physical_grid_must_have_identical_page_and_record_counts(self):
        for change in ['page','row','raw']:
            layout=deepcopy(self.layout)
            if change=='page':layout['pages'][0]['page']=2
            elif change=='row':layout['pages'][0]['tables'][0]['rows'].pop()
            else:layout['raw_text']+='extra page\f'
            with self.assertRaises(ValueError):self.parse(layout=layout)

    def test_unrecognised_header_or_extra_table_is_not_silently_skipped(self):
        layout=deepcopy(self.layout);layout['pages'][0]['tables'][0]['rows'][0]['texts'][0]='专业（*号另有含义）'
        with self.assertRaises(ValueError):self.parse(layout=layout)
        layout=deepcopy(self.layout);layout['pages'][0]['tables']*=2
        with self.assertRaises(ValueError):self.parse(layout=layout)

    def test_missing_exam_unit_or_mode_code_conflict_is_rejected(self):
        original=deepcopy(self.layout)
        for field,value in [(3,self.exam.replace('③301数学（一）','')),(2,'(1)非全日制'),(0,'0854错误四位码'),(1,'没有方向代码')]:
            self.layout=deepcopy(original)
            self.layout['pages'][0]['tables'][0]['rows'][2]['texts'][field]=value;self.refresh_raw()
            with self.assertRaises(ValueError):self.parse()

    def test_repeated_initial_unit_does_not_silently_become_a_new_or_choice(self):
        self.layout['pages'][0]['tables'][0]['rows'][2]['texts'][3]=self.exam.replace('②201','②204英语（二）②201');self.refresh_raw()
        with self.assertRaises(ValueError):self.parse()

    def test_duplicate_application_identity_cannot_inflate_coverage(self):
        self.layout['pages'][0]['tables'][0]['rows'][3]['texts']=list(self.layout['pages'][0]['tables'][0]['rows'][2]['texts']);self.refresh_raw()
        with self.assertRaises(ValueError):self.parse()

    def test_every_pdf_quote_is_literal_on_its_own_original_page(self):
        result=self.parse();pages=self.layout['raw_text'].split('\f')
        for row in result['programs']:
            refs=[*row['evidence'],*row['exam_evidence'],*[ref for subject in row['subjects'] for ref in subject['evidence']]]
            for ref in refs:
                if ref['document_id']==self.document['id']:
                    self.assertIn(''.join(ref['quote'].split()),''.join(pages[ref['page']-1].split()))

    def test_evidence_cannot_be_reconstructed_from_different_unrelated_text(self):
        self.layout['raw_text']=self.layout['raw_text'].replace('校企联培','不同原文')
        with self.assertRaises(ValueError):self.parse()


if __name__=='__main__':unittest.main()
