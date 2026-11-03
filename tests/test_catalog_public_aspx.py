from pathlib import Path
import hashlib
import json
import os
import tempfile
import unittest
from urllib.parse import urlencode

from collectors.catalog_public_aspx import parse_public_aspx_html,public_aspx_scope,decode_aspx_delta


def delta(kind,identity,text):return f'{len(text.encode("utf-16-le"))//2}|{kind}|{identity}|{text}|'


class PublicAspxTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.docs={}
        self.unit={'id':'368428','unit_code':'10560','name':'汕头大学','official_url':'https://www.gs.stu.edu.cn/'}
        self.base='https://www.gs.stu.edu.cn/Open/RecruitTkss/'
        self.leaf_url=self.base+'ZyYjfxView.aspx?EID=real-public-token'
        self.grid='<table id="ctl00_contentParent_dgData"><tr><td><a href="ZyYjfxView.aspx?EID=real-public-token">01|区域产业合作</a></td></tr></table>'
        self.entry_text='''<title>汕头大学统考硕士研究生招生系统</title><h1>2027年硕士研究生入学招生目录</h1>
        <form method="post" action="./ZsDir_View.aspx"><input name="__VIEWSTATE" value="first-state" type="hidden">
        <input name="__EVENTTARGET" value="" type="hidden"><input name="__EVENTARGUMENT" value="" type="hidden">
        <select name="ctl00$contentParent$drpNd"><option selected value="2027">2027</option></select>
        <select name="ctl00$contentParent$drpXxfs" onchange="__doPostBack('ctl00$contentParent$drpXxfs','')"><option selected value="1">全日制</option><option value="2">非全日制</option></select>
        <select name="ctl00$contentParent$drpYx"><option selected value="">全部</option></select>'''+self.grid+'''</form>'''
        self.entry=self.save('entry',self.entry_text,self.base+'ZsDir_View.aspx')
        self.entry={**self.entry,'public_form_profile':'aspx_stu','public_aspx_page':{'profile':'aspx_stu'}};self.docs['entry']=self.entry
        self.html='''<title>汕头大学统考硕士研究生招生系统</title><h1>汕头大学2027年硕士研究生入学招生专业研究方向信息</h1>
        <table class="tbline"><tr><td>研究<br>方向名称</td><td colspan="3">区域产业合作</td></tr>
        <tr><td>所属专业<br>（领域)代码</td><td>020202</td><td>所属专业<br>（领域)名称</td><td>区域经济学</td></tr>
        <tr><td>招生院系</td><td>商学院</td><td>学习方式</td><td>全日制</td></tr>
        <tr><td>专业类型</td><td>学术型</td><td>拟招人数</td><td></td></tr>
        <tr><td>初试科目</td><td colspan="3"><span id="ctl00_contentParent_dlistCskm"><span><span id="ctl00_contentParent_dlistCskm_ctl00_dlist">
        <a>①101|思想政治理论</a><a>②201|英语（一）</a><a>③303|数学（三）</a><a>④824|微观经济学与宏观经济学</a></span></span></span></td></tr>
        <tr><td>复试科目</td><td colspan="3"><a>F24|口语测试</a></td></tr></table>'''
        self.leaf=self.save('leaf',self.html,self.leaf_url)
        self.leaf={**self.leaf,'kind':'catalog','catalog_parser':'public_aspx_v1','public_form_context':{
            'profile':'aspx_stu','entry_document_id':'entry','listing_document_id':'entry'}};self.docs['leaf']=self.leaf

    def save(self,identity,text,url,request=None):
        path=self.root/'data/snapshots'/f'{identity}.response';path.parent.mkdir(parents=True,exist_ok=True);raw=text.encode();path.write_bytes(raw)
        doc={'id':identity,'unit_id':self.unit['id'],'year':2027,'kind':'catalog_context','url':url,
            'local_path':path.relative_to(self.root).as_posix(),'sha256':hashlib.sha256(raw).hexdigest(),'request_method':'GET'}
        if request is not None:
            body=urlencode(request);source=json.dumps({'method':'POST','url':url,'body':body}).encode();rq=path.with_suffix('.request.json');rq.write_bytes(source)
            doc={**doc,'request_method':'POST','request_sha256':hashlib.sha256(body.encode()).hexdigest(),
                'request_document':{'unit_id':self.unit['id'],'year':2027,'local_path':rq.relative_to(self.root).as_posix(),'sha256':hashlib.sha256(source).hexdigest()}}
        self.docs[identity]=doc;return doc

    def parse(self,document=None,html=None):return parse_public_aspx_html(self.html if html is None else html,self.unit,2027,document or self.leaf,project_root=self.root,documents=self.docs)

    def test_real_leaf_identity_and_visible_initial_exam_only(self):
        row=self.parse()['programs'][0]
        self.assertEqual(row['id'],'368428:2027:商学院:020202:01:全日制')
        self.assertIsNone(row['department_code']);self.assertEqual(row['direction'],'01 区域产业合作')
        self.assertEqual([s['code'] for s in row['subjects']],['101','201','303','824'])
        self.assertTrue(row['exam_matching_supported']);self.assertNotIn('F24',str(row['subjects']))

    def test_leaf_must_be_visible_on_exact_original_public_page(self):
        other={**self.leaf,'url':self.base+'ZyYjfxView.aspx?EID=not-in-public-list'}
        with self.assertRaises(ValueError):self.parse(document=other)

    def test_visible_mode_and_year_cannot_be_borrowed_or_guessed(self):
        for old,new in [('2027年','2026年'),('全日制</td>','未知</td>'),('区域产业合作</td>','其他方向</td>')]:
            text=self.html.replace(old,new);fresh=self.save('other',text,self.leaf_url);fresh={**fresh,'public_form_context':self.leaf['public_form_context'],'catalog_parser':'public_aspx_v1'}
            with self.assertRaises(ValueError):self.parse(document=fresh,html=text)

    def test_utf16_delta_segments_preserve_original_panel_and_do_not_execute_scripts(self):
        text=delta('#','','4')+delta('updatePanel','panel','<p>公开😀数据</p>')+delta('hiddenField','__VIEWSTATE','next-state')
        decoded=decode_aspx_delta(text)
        self.assertEqual(decoded['panels'],['<p>公开😀数据</p>']);self.assertEqual(decoded['hidden']['__VIEWSTATE'],'next-state')
        with self.assertRaises(ValueError):decode_aspx_delta(text[:-1])

    def test_scope_does_not_call_default_fulltime_all_modes_complete(self):
        result=public_aspx_scope(self.unit,2027,self.docs,project_root=self.root)
        self.assertFalse(result['complete'])

    def test_altered_raw_source_or_request_sha_is_rejected(self):
        with self.assertRaises(ValueError):self.parse(document={**self.leaf,'sha256':'0'*64})

    def test_absent_visible_exam_unit_or_duplicate_table_fails_safely(self):
        for text in [self.html.replace('<a>③303|数学（三）</a>',''),self.html+self.html]:
            fresh=self.save('bad',text,self.leaf_url);fresh={**fresh,'catalog_parser':'public_aspx_v1','public_form_context':self.leaf['public_form_context']}
            with self.assertRaises(ValueError):self.parse(document=fresh,html=text)

    def add_post_page(self,identity='mode2',grid=None,mode='2',target=None,previous='entry',state='first-state',extra=None):
        fields={'__EVENTTARGET':target or 'ctl00$contentParent$drpXxfs','__EVENTARGUMENT':'','__VIEWSTATE':state,
                'ctl00$contentParent$drpNd':'2027','ctl00$contentParent$drpXxfs':mode,'ctl00$contentParent$drpYx':'','__ASYNCPOST':'true',**(extra or {})}
        grid=grid or self.grid.replace('real-public-token','public-nonfull-token')
        text=delta('#','','4')+delta('updatePanel','panel',grid)+delta('hiddenField','__VIEWSTATE','next-state')
        document=self.save(identity,text,self.entry['url'],request=fields)
        document={**document,'public_form_profile':'aspx_stu','public_aspx_page':{'profile':'aspx_stu','previous_document_id':previous,'mode':mode}}
        self.docs[identity]=document;return document,text

    def add_nonfull_leaf(self):
        html=self.html.replace('全日制</td>','非全日制</td>')
        doc=self.save('nonfull',html,self.base+'ZyYjfxView.aspx?EID=public-nonfull-token')
        doc={**doc,'kind':'catalog','catalog_parser':'public_aspx_v1','public_form_context':{
            'profile':'aspx_stu','entry_document_id':'entry','listing_document_id':'mode2'}}
        self.docs['nonfull']=doc;return doc,html

    def test_both_real_mode_queries_are_complete_and_request_proof_is_mandatory(self):
        self.add_post_page();leaf,text=self.add_nonfull_leaf()
        self.assertEqual(self.parse(document=leaf,html=text)['programs'][0]['study_mode'],'非全日制')
        scope=public_aspx_scope(self.unit,2027,self.docs,project_root=self.root)
        self.assertTrue(scope['complete']);self.assertEqual(scope['expected_records'],2)
        self.docs['mode2']={**self.docs['mode2'],'request_sha256':'0'*64}
        with self.assertRaises(ValueError):self.parse(document=leaf,html=text)
        self.assertFalse(public_aspx_scope(self.unit,2027,self.docs,project_root=self.root)['complete'])

    def test_postback_rejects_unlisted_control_filter_change_or_lost_form_state(self):
        cases=[{'target':'invented-control'},{'state':'not-original-state'},{'mode':'3'},
               {'extra':{'ctl00$contentParent$drpYx':'001'}},{'extra':{'ctl00$contentParent$drpNd':'2026'}},
               {'extra':{'__ASYNCPOST':'false'}}]
        for fields in cases:
            self.add_post_page(**fields);leaf,text=self.add_nonfull_leaf()
            with self.assertRaises(ValueError):self.parse(document=leaf,html=text)

    def test_public_paging_requires_actual_button_and_contiguous_terminal_pages(self):
        pager='''<a id="Pager1_lnkPage1">1</a><a id="Pager1_lnkPage2" href="javascript:__doPostBack('pager2','')">2</a>'''
        self.entry_text=self.entry_text.replace(self.grid,self.grid+pager)
        entry=self.save('entry',self.entry_text,self.entry['url']);self.entry={**entry,'public_aspx_page':{'profile':'aspx_stu'}};self.docs['entry']=self.entry
        self.add_post_page();self.add_nonfull_leaf()
        self.assertFalse(public_aspx_scope(self.unit,2027,self.docs,project_root=self.root)['complete'])
        page2grid=self.grid.replace('real-public-token','full-second-token').replace('01|','02|')+'<a id="Pager1_lnkPage1" href="javascript:__doPostBack(\'pager1\',\'\')">1</a><a id="Pager1_lnkPage2">2</a>'
        document,_=self.add_post_page('page2',grid=page2grid,mode='1',target='pager2')
        leaf2=self.save('leaf2',self.html.replace('区域产业合作','另一个方向'),self.base+'ZyYjfxView.aspx?EID=full-second-token')
        self.docs['leaf2']={**leaf2,'catalog_parser':'public_aspx_v1','public_form_context':{**self.leaf['public_form_context'],'listing_document_id':document['id']}}
        self.assertTrue(public_aspx_scope(self.unit,2027,self.docs,project_root=self.root)['complete'])
        self.docs['page2']={**document,'public_aspx_page':{**document['public_aspx_page'],'page':9}}
        self.assertFalse(public_aspx_scope(self.unit,2027,self.docs,project_root=self.root)['complete'])

    def test_empty_leaf_direction_keeps_exact_direction_from_public_link(self):
        html=self.html.replace('区域产业合作</td>','</td>');doc=self.save('blank',html,self.leaf_url)
        doc={**doc,'public_form_context':self.leaf['public_form_context']}
        row=self.parse(document=doc,html=html)['programs'][0]
        self.assertEqual(row['direction'],'01 区域产业合作');self.assertTrue(all(ref.get('quote','').strip() for ref in row['evidence']))

    def test_explicit_or_continuation_uses_preceding_exam_unit_but_never_guesses(self):
        html=self.html.replace('<a>②201|英语（一）</a>','<a>②201|英语（一）</a><span>或 <a>203|日语</a></span>')
        doc=self.save('or',html,self.leaf_url);doc={**doc,'public_form_context':self.leaf['public_form_context']}
        row=self.parse(document=doc,html=html)['programs'][0]
        self.assertEqual([s['code'] for s in row['subjects']],['101','201','203','303','824']);self.assertEqual(len(row['exam_combinations']),2)
        html=html.replace('或 <a>203','<a>203');doc=self.save('no-or',html,self.leaf_url);doc={**doc,'public_form_context':self.leaf['public_form_context']}
        with self.assertRaises(ValueError):self.parse(document=doc,html=html)

    def test_conditional_exam_groups_keep_correlation(self):
        old='<a>①101|思想政治理论</a><a>②201|英语（一）</a><a>③303|数学（三）</a><a>④824|微观经济学与宏观经济学</a>'
        second=old.replace('②201|英语（一）','②204|英语（二）').replace('③303|数学（三）','③302|数学（二）')
        html=self.html.replace(old,old+'</span><span id="ctl00_contentParent_dlistCskm_ctl01_dlist">'+second)
        doc=self.save('conditional',html,self.leaf_url);doc={**doc,'public_form_context':self.leaf['public_form_context']}
        row=self.parse(document=doc,html=html)['programs'][0]
        self.assertFalse(row['exam_matching_supported']);self.assertEqual(len(row['exam_combinations']),2)

    def test_explicit_none_trailing_exam_slots_are_not_missing_courses(self):
        html=self.html.replace('<a>③303|数学（三）</a><a>④824|微观经济学与宏观经济学</a>','<a>③-|无</a><a>④-|无</a>')
        doc=self.save('none',html,self.leaf_url);doc={**doc,'public_form_context':self.leaf['public_form_context']}
        self.assertEqual(len(self.parse(document=doc,html=html)['programs'][0]['subjects']),2)

    def test_hidden_initial_course_and_hidden_identity_do_not_become_public_facts(self):
        for text in [self.html.replace('<a>③303|数学（三）</a>','<a style="display:none">③303|数学（三）</a>'),
                     self.html.replace('<td>020202</td>','<td hidden>020202</td>')]:
            doc=self.save('hidden',text,self.leaf_url);doc={**doc,'public_form_context':self.leaf['public_form_context']}
            with self.assertRaises(ValueError):self.parse(document=doc,html=text)

    def test_response_errors_duplicate_state_and_malformed_lengths_are_not_catalogs(self):
        for text in ['x|updatePanel|a|x|',delta('error','','denied'),delta('pageRedirect','','/login'),
                     delta('updatePanel','panel','grid')+delta('hiddenField','__VIEWSTATE','one')+delta('hiddenField','__VIEWSTATE','two')]:
            with self.assertRaises(ValueError):decode_aspx_delta(text)

    def test_scope_rejects_missing_leaf_duplicate_leaf_and_cyclic_request_chain(self):
        self.assertFalse(public_aspx_scope(self.unit,2027,{},project_root=self.root)['complete'])
        self.add_post_page();leaf,text=self.add_nonfull_leaf();del self.docs['nonfull']
        self.assertFalse(public_aspx_scope(self.unit,2027,self.docs,project_root=self.root)['complete'])
        self.docs['nonfull']=leaf;self.docs['duplicate']={**leaf,'id':'duplicate'}
        self.assertFalse(public_aspx_scope(self.unit,2027,self.docs,project_root=self.root)['complete'])
        del self.docs['duplicate'];page=self.docs['mode2'];self.docs['mode2']={**page,'public_aspx_page':{**page['public_aspx_page'],'previous_document_id':'mode2'}}
        with self.assertRaises(ValueError):self.parse(document=leaf,html=text)

    def test_source_year_school_profile_and_transformed_input_do_not_replay(self):
        for document in [{**self.leaf,'url':'https://example.org/leaf'},
                         {**self.leaf,'public_form_context':{**self.leaf['public_form_context'],'profile':'aspx_scut'}},
                         {**self.leaf,'request_method':'POST'},
                         {**self.leaf,'public_form_context':{**self.leaf['public_form_context'],'entry_document_id':'absent'}}]:
            with self.assertRaises(ValueError):self.parse(document=document)
        with self.assertRaises(ValueError):self.parse(html=self.html+' transformed')

    def test_scut_public_name_link_requires_real_direction_code_on_same_leaf(self):
        self.unit={**self.unit,'id':'368416','unit_code':'10561','name':'华南理工大学'}
        self.base='https://yanzhao.scut.edu.cn/open/Master/';self.leaf_url=self.base+'ZyYjfxView.aspx?EID=real-public-token'
        entry_text=self.entry_text.replace('汕头大学','华南理工大学').replace('年硕士研究生入学招生目录','年硕士研究生招生专业目录').replace('ZsDir_View.aspx','Zsml_view.aspx').replace('01|区域产业合作','区域产业合作')
        entry=self.save('entry',entry_text,self.base+'Zsml_view.aspx');self.docs['entry']={**entry,'public_aspx_page':{'profile':'aspx_scut'}}
        html=self.html.replace('汕头大学','华南理工大学').replace('>区域产业合作</td>','>01|区域产业合作</td>').replace('ctl00_contentParent_dlistCskm_ctl00_dlist','contentParent_dlistCskm_dlist_0')
        doc=self.save('scut',html,self.leaf_url);doc={**doc,'public_form_context':{'profile':'aspx_scut','entry_document_id':'entry','listing_document_id':'entry'}}
        row=self.parse(document=doc,html=html)['programs'][0]
        self.assertEqual(row['direction'],'01 区域产业合作')
        html=html.replace('01|区域产业合作','区域产业合作');doc=self.save('bad-scut',html,self.leaf_url);doc={**doc,'public_form_context':{'profile':'aspx_scut','entry_document_id':'entry','listing_document_id':'entry'}}
        with self.assertRaises(ValueError):self.parse(document=doc,html=html)

    def test_explicit_single_exam_recommendation_only_or_long_program_transfer_are_not_unified_recommendations(self):
        for note in ['本方向只接受符合单独考试报考条件考生报考。','本方向只接收推免生。','只接受长学制转段']:
            html=self.html.replace('</table>','<tr><td>备注</td><td>'+note+'</td></tr></table>')
            doc=self.save('special',html,self.leaf_url);doc={**doc,'public_form_context':self.leaf['public_form_context']}
            result=self.parse(document=doc,html=html)
            self.assertEqual(result['programs'],[]);self.assertEqual(result['expected_records'],0)
            self.assertEqual(len(result['excluded_records']),1);self.assertIn(note,result['excluded_records'][0]['evidence']['quote'])

    def test_inclusive_recommendation_wording_is_not_recommendation_only(self):
        note='接受推免生和统招考生报名'
        html=self.html.replace('</table>','<tr><td>备注</td><td>'+note+'</td></tr></table>')
        doc=self.save('inclusive',html,self.leaf_url);doc={**doc,'public_form_context':self.leaf['public_form_context']}
        self.assertEqual(len(self.parse(document=doc,html=html)['programs']),1)

    def test_negative_or_cross_clause_special_exam_wording_does_not_remove_ordinary_candidates(self):
        for note in ['只接收统考生，不接收推免生','只接受统考生，不接受长学制转段',
                     '仅接受统考生，不接受单独考试考生','只接受非推免生','只接收统考生及其他人员；推免生另行报名',
                     '不只接收推免生','并非只接收推免生','本方向不只接受长学制转段','并非仅接受单独考试考生']:
            html=self.html.replace('</table>','<tr><td>备注</td><td>'+note+'</td></tr></table>')
            doc=self.save('negative',html,self.leaf_url);doc={**doc,'public_form_context':self.leaf['public_form_context']}
            self.assertEqual(len(self.parse(document=doc,html=html)['programs']),1,note)

    def test_keyword_filtered_initial_catalog_cannot_claim_schoolwide_completeness(self):
        entry_text=self.entry_text.replace(self.grid,'<input name="ctl00$contentParent$txtWord" value="keyword-filter">'+self.grid)
        entry=self.save('entry',entry_text,self.entry['url']);self.docs['entry']={**entry,'public_aspx_page':{'profile':'aspx_stu'}}
        self.add_post_page(extra={'ctl00$contentParent$txtWord':'keyword-filter'});self.add_nonfull_leaf()
        self.assertFalse(public_aspx_scope(self.unit,2027,self.docs,project_root=self.root)['complete'])

    def test_warm_parsed_cache_rechecks_request_bytes_even_when_mtime_is_restored(self):
        page,_=self.add_post_page();leaf,text=self.add_nonfull_leaf()
        self.assertEqual(len(self.parse(document=leaf,html=text)['programs']),1)
        path=self.root/page['request_document']['local_path'];stat=path.stat();raw=path.read_bytes()
        path.write_bytes(raw.replace(b'first-state',b'false-state'));os.utime(path,ns=(stat.st_atime_ns,stat.st_mtime_ns))
        with self.assertRaises(ValueError):self.parse(document=leaf,html=text)

    def test_degree_uses_standard_internal_names_and_remains_compatible_with_historical_score_scope(self):
        from evidence import _score_applicability
        for source,expected in [('学术型','学术学位'),('专业型','专业学位')]:
            html=self.html.replace('学术型',source);doc=self.save('degree',html,self.leaf_url)
            doc={**doc,'public_form_context':self.leaf['public_form_context']}
            row=self.parse(document=doc,html=html)['programs'][0]
            self.assertEqual(row['degree_type'],expected)
            score={'unit_id':self.unit['id'],'program_code':row['code'],'degree_type':expected}
            self.assertEqual(_score_applicability(row,score),'applicable')


if __name__=='__main__':unittest.main()
