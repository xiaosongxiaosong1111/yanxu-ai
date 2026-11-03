import hashlib
from html import escape
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from collectors.official_bindings import binding_candidates, build_report, main, verify_candidate_home, verify_report_targets, ready_candidates, ready_corroborated_candidates
from public_sources import SourceError
from collectors.discovery import DiscoveryError


UNIT = {'id': '123', 'name': '样本大学', 'unit_code': '10001'}
URL = 'https://yz.chsi.com.cn/zsgs/zsjz/detail.action?schCode=10001&zsgsId=7'


def original(html, url=URL):
    raw = html.encode('utf-8')
    return {'url': url, 'local_path': 'data/sample.html', 'sha256': hashlib.sha256(raw).hexdigest(),
            'content_type': 'text/html;charset=utf-8'}, raw


class OfficialBindingTests(unittest.TestCase):
    def candidates(self, body, **kwargs):
        doc, raw = original('<h1>样本大学2027年硕士研究生招生章程</h1>'+body)
        return binding_candidates(UNIT, doc, raw, **kwargs)

    def test_explicit_site_roles_and_literal_plain_homepage_statements(self):
        for label in ['研究生招生网站', '本校研究生院官网', '部门网站', '学校主页', '学院网址']:
            with self.subTest(label=label):
                rows = self.candidates(f'<p>{label}：https://grad.sample.edu.cn/entry 。</p>')
                self.assertEqual(rows[0]['url'], 'https://grad.sample.edu.cn/')
                self.assertEqual(rows[0]['evidence']['quote'], 'https://grad.sample.edu.cn/entry')
                self.assertEqual(rows[0]['status'], 'declared_official_origin')
                self.assertTrue(rows[0]['evidence']['context'].startswith(label))

    def test_named_official_charter_link_can_establish_a_publication_origin(self):
        rows = self.candidates('<a href="https://www.sample.edu.cn/2027.html">样本大学2027年硕士研究生招生简章</a>')
        self.assertEqual(rows[0]['relation'], 'own_admissions_publication')
        self.assertEqual(rows[0]['evidence']['link_label'], '样本大学2027年硕士研究生招生简章')

    def test_nested_anchor_roles_and_own_portal_names_are_supported(self):
        rows = self.candidates('<p>信息查询：<a href="https://grad.sample.edu.cn"><b>样本大学研招在线官网</b></a></p>')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['url'], 'https://grad.sample.edu.cn/')

    def test_plain_generic_contact_url_is_a_review_candidate_without_own_role(self):
        rows = self.candidates('<p>（四）网址：https://grad.sample.edu.cn；</p>')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['status'], 'needs_review')
        self.assertEqual(rows[0]['evidence']['quote'], 'https://grad.sample.edu.cn')

    def test_explicit_bare_homepage_domain_is_preserved_for_target_verification(self):
        rows=self.candidates('<p>学校主页：www.sample.edu.cn</p>')
        self.assertEqual(rows[0]['url'],'https://www.sample.edu.cn/')
        self.assertEqual(rows[0]['status'],'needs_review')
        self.assertEqual(rows[0]['reason'],'scheme_unconfirmed')
        self.assertEqual(rows[0]['evidence']['quote'],'www.sample.edu.cn')

    def test_bare_academic_host_requires_own_school_or_graduate_site_declaration(self):
        for label in ['学校研招网站', '学校研究生招生网站', '研究生院网站', '我校官方网站', '研招网站']:
            with self.subTest(label=label):
                rows = self.candidates('<p>' + label + '：yz.sample.edu.cn/2027/index.htm。</p>')
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]['url'], 'https://yz.sample.edu.cn/')
                self.assertEqual(rows[0]['linked_url'], 'https://yz.sample.edu.cn/2027/index.htm')
                self.assertEqual(rows[0]['status'], 'needs_review')
                self.assertEqual(rows[0]['reason'], 'scheme_unconfirmed')
                self.assertEqual(rows[0]['evidence']['quote'], 'yz.sample.edu.cn/2027/index.htm')
                self.assertEqual(rows[0]['evidence']['target_form'], 'bare_academic_host')
        for label in ['研招网', '研招办网址', '我校研招办网站']:
            rows = self.candidates('<p>'+label+'：yz.sample.edu.cn；其他未尽事宜请联系学校。</p>')
            self.assertEqual(len(rows), 1, label)

    def test_bare_academic_domains_are_not_extracted_from_emails_links_or_peer_references(self):
        for body in ['<p>联系邮箱：admissions@yz.sample.edu.cn</p>',
                     '<p>联系网址：yz.sample.edu.cn</p>',
                     '<p>友情链接：其他大学研究生院网站：yz.other.edu.cn</p>',
                     '<p>参考其他大学研招网站：yz.other.edu.cn</p>',
                     '<p>学校研招网站：yz.sample.edu.cn.evil.com</p>',
                     '<p>学校研招网站：yz.sample.edu.cn:9999</p>',
                     '<p hidden>学校研招网站：yz.sample.edu.cn</p>']:
            with self.subTest(body=body):
                self.assertFalse(self.candidates(body, foreign_names=['其他大学']))
        explicit = self.candidates('<p>学校研招网站：https://yz.sample.edu.cn/</p>')
        self.assertEqual(len(explicit), 1)
        self.assertNotIn('target_form', explicit[0]['evidence'])

    def test_bare_faculty_or_auxiliary_host_cannot_become_school_homepage(self):
        for body in ['学校各学院网站：cce.sample.edu.cn', '报考学院网站：cce.sample.edu.cn',
                     '我校财务处网站：cwc.sample.edu.cn', '研究生院网站（报名系统）：ehall.sample.edu.cn']:
            rows = self.candidates('<p>' + body + '</p>')
            self.assertFalse(any(row['status'] == 'declared_official_origin' for row in rows))
            self.assertTrue(not rows or all(row['reason'] == 'auxiliary_department_site' for row in rows))

    def test_bare_host_target_needs_own_primary_title_and_rejects_a_faculty_title(self):
        with tempfile.TemporaryDirectory() as directory:
            candidate = self.candidates('<p>学校研招网站：yz.sample.edu.cn</p>')[0]
            for title, expected in [('样本大学研究生院', 'corroborated_official_origin'),
                                    ('样本大学计算机学院', 'needs_review'), ('其他大学研究生院', 'needs_review')]:
                raw = ('<title>' + title + '</title>').encode()
                result = verify_candidate_home(UNIT, candidate, Path(directory),
                    fetcher=lambda url, unit: {'url': url, 'raw': raw, 'sha256': hashlib.sha256(raw).hexdigest(), 'content_type': 'text/html'})
                self.assertEqual(result['status'], expected)

    def test_declared_deep_public_page_can_corroborate_host_without_guessing_or_chsi_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = self.candidates('<p>学校研究生招生网站：https://grad.sample.edu.cn/admissions/</p>')[0]
            deep = candidate['linked_url']
            raw = '<title>样本大学研究生招生网</title>'.encode()
            fetch = Mock(return_value={'url': deep, 'raw': raw,
                'sha256': hashlib.sha256(raw).hexdigest(), 'content_type': 'text/html'})
            result = verify_candidate_home(UNIT, candidate, root, fetcher=fetch,
                target_url=deep, target_dir='data/snapshots/binding-targets')
            self.assertEqual(result['status'], 'corroborated_official_origin')
            self.assertEqual(fetch.call_args.args[0], deep)
            self.assertEqual(result['target_evidence']['requested_url'], deep)
            self.assertTrue(result['target_evidence']['local_path'].startswith('data/snapshots/binding-targets/'))
            for target in ['https://yz.chsi.com.cn/', 'https://other.edu.cn/', 'http://127.0.0.1/']:
                fetch.reset_mock()
                denied = verify_candidate_home(UNIT, candidate, root, fetcher=fetch, target_url=target)
                fetch.assert_not_called()
                self.assertEqual(denied['target_check']['code'], 'wrong_unit')
            escaped = verify_candidate_home(UNIT, candidate, root, fetcher=fetch, target_dir=root.parent/'outside')
            self.assertNotIn('target_evidence', escaped)

    def test_visible_school_home_route_is_bound_to_its_declared_page_hash_and_anchor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root/'data').mkdir()
            deep = 'https://grad.sample.edu.cn/admissions/'
            home = 'https://www.sample.edu.cn/'
            doc, raw = original('<h1>样本大学</h1><p>学校研究生招生网站：'+deep+'</p>')
            (root/doc['local_path']).write_bytes(raw)
            candidate = binding_candidates(UNIT, doc, raw)[0]
            body = '<title>样本大学</title>'.encode()
            verified = verify_candidate_home(UNIT, candidate, root, target_url=home,
                fetcher=lambda url,unit: {'url':url,'raw':body,'sha256':hashlib.sha256(body).hexdigest(),'content_type':'text/html'})
            page = ('<title>研究生招生办</title><a href="'+home+'">样本大学</a>').encode()
            route_file = root/'data/route.html'; route_file.write_bytes(page)
            route = {'url':deep + 'index.htm','requested_url':deep,'sha256':hashlib.sha256(page).hexdigest(),
                     'local_path':'data/route.html','quote':home,'link_label':'样本大学'}
            verified['target_evidence']['corroboration_route'] = [route]
            report = {'units':[{'unit_id':'123','candidates':[verified]}]}
            self.assertEqual(ready_corroborated_candidates(report,{'units':[UNIT]},root)['metadata']['ready_units'],1)
            for override in [{'sha256':'0'*64},{'url':'https://other.edu.cn/'},{'quote':'https://wrong.sample.edu.cn/'},
                             {'requested_url':'https://grad.sample.edu.cn/undeclared/'}]:
                invalid = {**verified,'target_evidence':{**verified['target_evidence'],
                    'corroboration_route':[{**route,**override}]}}
                result = ready_corroborated_candidates({'units':[{'unit_id':'123','candidates':[invalid]}]}, {'units':[UNIT]}, root)
                self.assertEqual(result['metadata']['ready_units'],0)

    def test_foreign_university_and_reference_policy_links_are_never_own_bindings(self):
        self.assertEqual(self.candidates('<p>参考其他大学研究生院官网：<a href="https://other.edu.cn">研究生院官网</a></p>', foreign_names=['其他大学']), [])
        self.assertEqual(self.candidates('<p>教育部网站：https://www.moe.gov.cn/</p>'), [])
        self.assertEqual(self.candidates('<a href="https://other.edu.cn">其他大学2027硕士招生简章</a>', foreign_names=['其他大学']), [])

    def test_wrong_unit_unscoped_source_and_mismatched_hash_fail_closed(self):
        html = '<h1>其他大学</h1><p>学校官网：https://www.other.edu.cn/</p>'
        for url in [URL.replace('10001','10002'), 'https://yz.chsi.com.cn/zsgs/zsjz/article.action?id=7', 'https://example.com/page']:
            doc, raw = original(html, url)
            self.assertEqual(binding_candidates(UNIT, doc, raw), [])
        doc, raw = original(html)
        self.assertEqual(binding_candidates(UNIT, {**doc, 'sha256': '0'*64}, raw), [])

    def test_hidden_published_chsi_html_is_replayed_from_the_hashed_original(self):
        body = '<p>研究生招生网站：https://grad.sample.edu.cn/</p>'
        html = '<h1>样本大学2027年硕士招生章程</h1><input type="hidden" id="hid_content" value="'+escape(body, quote=True)+'"><div id="cnt_body_wrapper"></div><script>$("#cnt_body_wrapper").html($("#hid_content").val());</script>'
        doc, raw = original(html)
        rows = binding_candidates(UNIT, doc, raw)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['evidence']['sha256'], hashlib.sha256(raw).hexdigest())

    def test_private_addresses_unsafe_ports_masked_urls_and_global_nav_are_rejected(self):
        for target in ['http://127.0.0.1/', 'http://localhost/', 'https://site.edu.cn:9999/', 'http://www.***/']:
            self.assertEqual(self.candidates(f'<p>学校官网：{target}</p>'), [])
        self.assertEqual(self.candidates('<nav><a href="https://other.edu.cn">学校官网</a></nav>'), [])

    def test_hidden_role_text_and_inherited_hidden_anchors_cannot_establish_ownership(self):
        for attrs in ['hidden', 'aria-hidden="true"', 'style="display: none"', 'style="visibility:hidden"', 'class="tooltip-content"']:
            with self.subTest(attrs=attrs):
                hidden = f'<section {attrs}><p>我校研究生招生网站：<a href="https://other.edu.cn/">研究生院官网</a></p></section>'
                self.assertEqual(self.candidates(hidden), [])
        rows = self.candidates('<p hidden>学校官网：https://other.edu.cn/</p><p>学校官网：<img src="icon">https://www.sample.edu.cn/</p>')
        self.assertEqual([row['host'] for row in rows], ['www.sample.edu.cn'])

    def test_non_academic_domains_remain_explicit_review_candidates(self):
        rows = self.candidates('<p>本院官方网站：http://www.research.com/</p>')
        self.assertEqual(rows[0]['status'], 'needs_review')
        self.assertEqual(rows[0]['reason'], 'declared_non_academic_domain')

    def test_finance_and_enrollment_systems_do_not_become_recommended_admissions_homepages(self):
        for body in ['我校财务处网站（网址：https://cwc.sample.edu.cn/）', '申请我校研究生均需登录预推免报名系统（网址：https://ehall.sample.edu.cn/）']:
            rows = self.candidates('<p>'+body+'</p>')
            self.assertEqual(rows[0]['status'], 'needs_review')
            self.assertEqual(rows[0]['reason'], 'auxiliary_department_site')

    def test_duplicate_origin_keeps_multiple_exact_original_proofs(self):
        rows = self.candidates('<p>学校主页：https://grad.sample.edu.cn/</p><p>研究生招生网站：https://grad.sample.edu.cn/admissions/</p>')
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]['additional_evidence']), 1)
        stronger = self.candidates('<p>网址：https://grad.sample.edu.cn/</p><p>研究生招生网站：https://grad.sample.edu.cn/admissions/</p>')
        self.assertEqual(stronger[0]['status'], 'declared_official_origin')

    def test_report_replays_only_project_originals_and_does_not_touch_registry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'data').mkdir()
            doc, raw = original('<h1>样本大学</h1><p>学校主页：https://www.sample.edu.cn/</p>')
            (root/doc['local_path']).write_bytes(raw)
            registry = {'units': [UNIT, {**UNIT, 'id':'456', 'official_url':'https://known.edu.cn'}]}
            national = {'units': [{'unit_id':'123','pages':[doc, {**doc,'local_path':'../escape.html'}]}]}
            report = build_report(registry, national, {'units':[]}, root)
            self.assertEqual(report['metadata']['eligible_units'], 1)
            self.assertEqual(report['metadata']['recovered_units'], 1)
            self.assertEqual(len(report['units'][0]['candidates']), 1)
            self.assertEqual(report['units'][0]['errors'][0]['code'], 'invalid_original')
            self.assertNotIn('official_url', registry['units'][0])

    def test_cli_writes_only_its_explicit_candidate_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name,data in [('units',{'units':[UNIT]}),('national',{'units':[]}),('inventory',{'units':[]})]:
                (root/(name+'.json')).write_text(json.dumps(data),'utf-8')
            target=root/'report.json'
            self.assertEqual(main(['--root',str(root),'--registry',str(root/'units.json'),'--national',str(root/'national.json'),'--inventory',str(root/'inventory.json'),'--output',str(target)]),0)
            self.assertEqual(json.loads(target.read_text('utf-8'))['metadata']['eligible_units'],1)

    def test_target_verification_requires_own_primary_title_and_saved_hash_proof(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate=self.candidates('<p>本院官方网站：https://www.research.com/</p>')[0]
            def fetch(url, unit):
                self.assertEqual(unit['official_url'],candidate['url'])
                raw='<title>样本大学研究生院</title><p>原文内容</p>'.encode()
                return {'url':url,'raw':raw,'sha256':hashlib.sha256(raw).hexdigest(),'content_type':'text/html;charset=utf-8'}
            verified=verify_candidate_home(UNIT,candidate,root,fetcher=fetch)
            self.assertEqual(verified['status'],'corroborated_official_origin')
            self.assertTrue((root/verified['target_evidence']['local_path']).is_file())
            self.assertEqual(verified['target_evidence']['quote'],'样本大学研究生院')
            self.assertEqual(candidate['status'],'needs_review')

    def test_target_body_mentions_hash_mismatch_and_access_restrictions_never_confirm_a_domain(self):
        with tempfile.TemporaryDirectory() as directory:
            candidate=self.candidates('<p>网址：https://sample.edu.cn/</p>')[0]
            for raw,sha in [('<title>其他大学</title><p>样本大学</p>'.encode(),None),('<title>其他大学</title><h1 hidden>样本大学</h1>'.encode(),None),('<title>样本大学</title>'.encode(),'0'*64)]:
                result=verify_candidate_home(UNIT,candidate,Path(directory),fetcher=lambda url,unit:{'url':url,'raw':raw,'sha256':sha or hashlib.sha256(raw).hexdigest(),'content_type':'text/html;charset=utf-8'})
                self.assertEqual(result['status'],'needs_review')
            def restricted(url,unit):
                raise SourceError('restricted','来源需要验证码')
            result=verify_candidate_home(UNIT,candidate,Path(directory),fetcher=restricted)
            self.assertEqual(result['target_check']['code'],'restricted')
            self.assertNotIn('target_evidence',result)
            def dns_failed(url,unit):
                raise DiscoveryError('dns_error','Public hostname could not be resolved')
            result=verify_candidate_home(UNIT,candidate,Path(directory),fetcher=dns_failed)
            self.assertEqual(result['target_check']['code'],'dns_error')

    def test_parallel_target_report_preserves_input_and_skips_auxiliary_departments(self):
        candidate=self.candidates('<p>网址：https://sample.edu.cn/</p>')[0]
        report={'metadata':{'recovered_units':0,'review_units':1},'units':[{'unit_id':'123','status':'needs_review','candidates':[candidate,{**candidate,'url':'https://finance.sample.edu.cn/','reason':'auxiliary_department_site'}]}]}
        saved=[]
        with patch('collectors.official_bindings.verify_candidate_home',side_effect=lambda unit,value,root:{**value,'status':'corroborated_official_origin','target_check':{'code':'own_primary_title'}}) as verify:
            result=verify_report_targets(report,{'units':[UNIT]},Path('.'),save=saved.append)
        self.assertEqual(verify.call_count,1)
        self.assertEqual(result['metadata']['corroborated_units'],1)
        self.assertEqual(result['metadata']['targets_checked'],1)
        self.assertEqual(result['units'][0]['status'],'recovered')
        self.assertEqual(report['units'][0]['status'],'needs_review')
        self.assertTrue(saved)
        with self.assertRaises(ValueError):
            verify_report_targets(report,{'units':[UNIT]},Path('.'),workers=99)

    def test_ready_export_replays_visible_original_and_fingerprints_registry_without_publishing(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'data').mkdir()
            doc,raw=original('<h1>样本大学</h1><p>学校主页：https://sample.edu.cn/</p>')
            (root/doc['local_path']).write_bytes(raw)
            candidate=binding_candidates(UNIT,doc,raw)[0]
            report={'units':[{'unit_id':'123','unit_name':UNIT['name'],'candidates':[candidate]}]}
            exported=ready_candidates(report,{'units':[UNIT]},root,registry_sha256='a'*64)
            self.assertEqual(exported['metadata']['registry_sha256'],'a'*64)
            self.assertEqual(exported['metadata']['ready_units'],1)
            (root/doc['local_path']).write_text('<p hidden>学校主页：https://other.edu.cn/</p>','utf-8')
            self.assertEqual(ready_candidates(report,{'units':[UNIT]},root)['metadata']['ready_units'],0)

    def test_corroborated_export_rechecks_both_hashes_visible_title_source_link_and_redirect_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'data').mkdir()
            doc,raw=original('<h1>样本大学</h1><p>官方网站：https://sample.com/</p>')
            (root/doc['local_path']).write_bytes(raw)
            candidate=binding_candidates(UNIT,doc,raw)[0]
            body='<title>样本大学研究生院</title>'.encode()
            def fetch(url,unit):
                return {'url':url,'raw':body,'sha256':hashlib.sha256(body).hexdigest(),'content_type':'text/html'}
            verified=verify_candidate_home(UNIT,candidate,root,fetcher=fetch)
            report={'units':[{'unit_id':'123','candidates':[verified]}]}
            self.assertEqual(ready_corroborated_candidates(report,{'units':[UNIT]},root)['metadata']['ready_units'],1)
            for override in [{'url':'https://other.com/'},{'sha256':'0'*64},{'quote':'非原文标题'}]:
                damaged={**verified,'target_evidence':{**verified['target_evidence'],**override}}
                invalid={'units':[{'unit_id':'123','candidates':[damaged]}]}
                self.assertEqual(ready_corroborated_candidates(invalid,{'units':[UNIT]},root)['metadata']['ready_units'],0)
            hidden='<title>其他大学</title><h1 hidden>样本大学研究生院</h1>'.encode()
            target=root/verified['target_evidence']['local_path'];target.write_bytes(hidden)
            changed={**verified,'target_evidence':{**verified['target_evidence'],'sha256':hashlib.sha256(hidden).hexdigest()}}
            self.assertEqual(ready_corroborated_candidates({'units':[{'unit_id':'123','candidates':[changed]}]},{'units':[UNIT]},root)['metadata']['ready_units'],0)
