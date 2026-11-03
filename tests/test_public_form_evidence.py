import copy
import json
from pathlib import Path
import unittest
import io
import hashlib
import zipfile

from catalog_index import CatalogIndex
from evidence import EvidenceIndex
from published_catalog import PublishedCatalog, public_value
from service_verification import SourceVerifier
from tests import test_catalog_public_forms as fixtures
from tests import test_catalog_university_forms as university_fixtures
from tests import test_catalog_public_aspx as aspx_fixtures


class PublicFormEvidenceTests(unittest.TestCase):
    def build_new(self,profile='szu',post_listing=False):
        if profile in {'szu','scu'}:
            cls=university_fixtures.SzuFormsTests if profile=='szu' else university_fixtures.UniversityFormsTests
            case=cls();case.setUp()
            case.unit={**case.unit,'official_url':'https://yz.'+profile+'.edu.cn/'}
        else:
            case=aspx_fixtures.PublicAspxTests();case.setUp()
            if profile=='aspx_scut':
                case.unit={**case.unit,'id':'368416','unit_code':'10561','name':'华南理工大学',
                    'official_url':'https://yanzhao.scut.edu.cn/'}
                case.base='https://yanzhao.scut.edu.cn/open/Master/'
                case.leaf_url=case.base+'ZyYjfxView.aspx?EID=real-public-token'
                entry_text=case.entry_text.replace('汕头大学','华南理工大学').replace('年硕士研究生入学招生目录',
                    '年硕士研究生招生专业目录').replace('ZsDir_View.aspx','Zsml_view.aspx').replace('01|区域产业合作','区域产业合作')
                entry=case.save('entry',entry_text,case.base+'Zsml_view.aspx')
                case.docs={**case.docs,'entry':{**entry,'public_aspx_page':{'profile':profile}}}
                case.html=case.html.replace('汕头大学','华南理工大学').replace('>区域产业合作</td>',
                    '>01|区域产业合作</td>').replace('ctl00_contentParent_dlistCskm_ctl00_dlist','contentParent_dlistCskm_dlist_0')
                leaf=case.save('leaf',case.html,case.leaf_url)
                case.leaf={**leaf,'catalog_parser':'public_aspx_v1','public_form_context':
                    {'profile':profile,'entry_document_id':'entry','listing_document_id':'entry'}}
                case.docs={**case.docs,'leaf':case.leaf}
            if post_listing:
                case.add_post_page();case.leaf,case.html=case.add_nonfull_leaf()
        self.addCleanup(case.doCleanups)
        identity=case.leaf['id']
        case.docs={key:{**row,'status':'parsed','kind':'catalog' if key==identity else 'catalog_context',
            'public_form_profile':profile} for key,row in case.docs.items()}
        case.leaf=case.docs[identity]
        program=case.parse()['programs'][0]
        return case,{'units':[case.unit]},{'metadata':{'reference_year':2027},
            'documents':list(case.docs.values()),'programs':[program]},program

    def test_new_public_profiles_offer_only_verified_get_original_reproductions(self):
        for profile in ('scu','szu','aspx_stu','aspx_scut'):
            with self.subTest(profile=profile):
                case,registry,admissions,program=self.build_new(profile)
                for document in admissions['documents']:
                    document.update(detail_url='https://evil.example/fake',detail_document_id='forged',detail_sha256='f'*64)
                result=EvidenceIndex(registry,admissions,case.root).program(program['id'])
                ref=next(ref for ref in result['claims'][1]['evidence'] if ref['document_id']==case.leaf['id'])
                target=case.docs['entry'] if profile=='szu' else case.leaf
                self.assertEqual(ref['detail_url'],target['url'])
                self.assertEqual(ref['detail_document_id'],target['id'])
                self.assertEqual(ref['detail_sha256'],target['sha256'])
                self.assertEqual(result['claims'][0]['status'],'verified')
                self.assertEqual(result['claims'][1]['status'],'verified')
                self.assertEqual(ref['verification'],'structured_fields' if profile=='szu' else 'exact_quote')

    def test_szu_structured_original_fields_and_query_links_survive_index_publication(self):
        case,registry,admissions,program=self.build_new()
        index=CatalogIndex(case.root/'data/runtime/catalog-index.sqlite')
        index.sync(registry,admissions,{'release_ready':False},'v1',verifier=SourceVerifier(case.root))
        result=PublishedCatalog(case.root/'data').sources()
        source=next(row for row in result['sources'] if row['id']=='leaf')
        self.assertEqual(source['detail_url'],case.docs['entry']['url'])
        self.assertEqual(source['request_url'],case.leaf['url'])
        self.assertEqual(source['original_format'],'structured_fields')
        proof=EvidenceIndex(registry,admissions,case.root).program(program['id'])['claims'][1]['evidence'][0]
        self.assertEqual(proof['field_path'],'zskm[0]')
        self.assertEqual(proof['structured_fields']['KMDM'],'101')
        self.assertEqual(proof['quote'],'')
        self.assertNotIn('local_path',json.dumps(public_value(proof)))

    def test_aspx_post_listing_uses_entry_and_leaf_keeps_actual_get_url(self):
        case,registry,admissions,program=self.build_new('aspx_stu',True)
        result=EvidenceIndex(registry,admissions,case.root).program(program['id'])
        listing=next(r for r in result['claims'][0]['evidence'] if r['document_id']=='mode2')
        leaf=next(r for r in result['claims'][1]['evidence'] if r['document_id']==case.leaf['id'])
        self.assertEqual(listing['request_method'],'POST')
        self.assertEqual(listing['detail_url'],case.docs['entry']['url'])
        self.assertEqual(leaf['detail_url'],case.leaf['url'])

    def test_parsed_and_verified_detached_form_context_never_gain_snapshot_verified(self):
        for profile in ('ezui','szu','scu','aspx_stu'):
            for status in ('parsed','verified'):
                with self.subTest(profile=profile,status=status):
                    case,registry,admissions,program=self.build() if profile=='ezui' else self.build_new(profile)
                    context=next(doc for doc in admissions['documents'] if doc['kind']=='catalog_context')
                    detached={**context,'id':'detached','status':status}
                    admissions={**admissions,'documents':[*admissions['documents'],detached]}
                    index=EvidenceIndex(registry,admissions,case.root)
                    source=index._source('detached')
                    self.assertEqual(source['verification'],'invalid_source')
                    self.assertFalse(source.get('detail_url'))

    def test_context_without_self_reported_form_labels_still_needs_a_verified_primary(self):
        case,registry,admissions,program=self.build_new('szu')
        context=next(doc for doc in admissions['documents'] if doc['kind']=='catalog_context')
        detached={key:value for key,value in context.items() if key not in {'public_form_profile','catalog_parser'}}
        detached={**detached,'id':'anonymous-detached','status':'parsed'}
        admissions={**admissions,'documents':[*admissions['documents'],detached]}
        source=EvidenceIndex(registry,admissions,case.root)._source(detached['id'])
        self.assertEqual(source['verification'],'invalid_source')
        detached={**detached,'request_method':'POST','detail_url':'https://evil.example/fake'}
        admissions={**admissions,'documents':[doc for doc in admissions['documents'] if doc['id']!=detached['id']]+[detached]}
        index=CatalogIndex(case.root/'data/runtime/catalog-index.sqlite')
        index.sync(registry,admissions,{'release_ready':False},'anonymous-v1',verifier=SourceVerifier(case.root))
        source=next(doc for doc in PublishedCatalog(case.root/'data').sources()['sources'] if doc['id']==detached['id'])
        self.assertIn('detail_url',source);self.assertEqual(source['detail_url'],'')

    def test_replayed_cross_document_learning_mode_context_keeps_its_original_quote(self):
        from collectors.catalog_html_national import parse_catalog_html_national
        case=university_fixtures.UniversityFormsTests();case.setUp();self.addCleanup(case.doCleanups)
        case.unit={'id':'u','name':'样本大学','official_url':'https://grad.sample.edu.cn/'}
        quote='我校各学科专业硕士研究生学习方式均为全日制，学生需全脱产在校学习。'
        mode=case.save('mode','<h1>样本大学2027年硕士研究生招生章程</h1><p>'+quote+'</p>',
            'https://grad.sample.edu.cn/charter',context=True)
        mode={key:value for key,value in {**mode,'status':'parsed'}.items() if key!='request_method'}
        html='''<h1>样本大学2027年硕士研究生招生专业目录</h1><table><tr><th>院系所</th><th>专业</th><th>研究方向</th><th>考试科目代码及名称</th></tr>
        <tr><td>001计算机学院</td><td>081200计算机科学与技术</td><td>01人工智能</td><td>①101思想政治理论②201英语（一）③301数学（一）④408计算机学科专业基础综合</td></tr></table>'''
        primary=case.save('primary',html,'https://grad.sample.edu.cn/catalog')
        primary={**primary,'status':'parsed','catalog_parser':'html_national_v1','learning_mode_document':mode}
        program=parse_catalog_html_national(html,case.unit,2027,primary,project_root=case.root)['programs'][0]
        admissions={'programs':[program],'documents':[mode,primary]}
        result=EvidenceIndex({'units':[case.unit]},admissions,case.root).program(program['id'])
        ref=next(ref for ref in result['claims'][0]['evidence'] if ref['document_id']=='mode')
        self.assertEqual(ref['verification'],'exact_quote');self.assertEqual(ref['quote'],quote)
        self.assertFalse(ref.get('detail_url'))

    def test_new_profile_context_sha_tampering_revokes_previously_replayed_links(self):
        for profile in ('scu','szu','aspx_stu','aspx_scut'):
            with self.subTest(profile=profile):
                case,registry,admissions,program=self.build_new(profile)
                verifier=SourceVerifier(case.root)
                first=EvidenceIndex(registry,admissions,case.root,verifier=verifier).program(program['id'])
                self.assertEqual(first['claims'][0]['status'],'verified')
                context=next(doc for doc in admissions['documents'] if doc['id']=='entry')
                (case.root/context['local_path']).write_bytes(b'tampered')
                second=EvidenceIndex(registry,admissions,case.root,verifier=verifier).program(program['id'])
                self.assertNotEqual(second['claims'][0]['status'],'verified')
                self.assertTrue(all(not ref.get('detail_url') for claim in second['claims'] for ref in claim['evidence']))

    def build(self, profile='ezui'):
        cls = {'ezui': fixtures.JsonPublicFormTests, 'zju': fixtures.ZjuPublicFormTests,
               'buaa': fixtures.PublicFormTests}[profile]
        case = cls()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.docs = {key: {**row, 'status': 'parsed', 'kind': 'catalog' if key == 'leaf' else 'catalog_context',
                          'public_form_profile': profile} for key, row in case.docs.items()}
        case.leaf = case.docs['leaf']
        program = case.parse()['programs'][0]
        registry = {'units': [case.unit]}
        admissions = {'metadata': {'reference_year': 2027}, 'documents': list(case.docs.values()),
                      'programs': [program]}
        return case, registry, admissions, program

    def test_ezui_cross_source_identity_and_exams_retain_typed_raw_fields_and_get_pages(self):
        case, registry, admissions, program = self.build()
        result = EvidenceIndex(registry, admissions, case.root).program(program['id'])
        catalog = next(row for row in result['claims'] if row['id'] == 'catalog')
        exams = next(row for row in result['claims'] if row['id'] == 'subjects')
        self.assertEqual((catalog['status'], exams['status']), ('verified', 'verified'))
        identity = next(ref for ref in catalog['evidence'] if ref['document_id'] == 'listing')
        self.assertEqual(identity['verification'], 'structured_fields')
        self.assertEqual(identity['field_path'], '$[0]')
        self.assertEqual(identity['structured_fields']['organName'], '卓越工程师学院')
        self.assertEqual(identity['quote'], '')
        self.assertEqual(identity['request_method'], 'POST')
        self.assertEqual(identity['request_url'], case.listing['url'])
        self.assertEqual(identity['detail_url'], case.entry['url'])
        first = exams['evidence'][0]
        self.assertEqual(first['structured_fields']['kskm'], '101思想政治理论')
        self.assertEqual(first['detail_url'], case.page['url'])
        self.assertEqual(first['original_format'], 'structured_fields')

    def test_fetched_context_requires_replayed_primary_chain_and_retains_field_evidence(self):
        case, registry, admissions, program = self.build()
        admissions['documents'] = [{**doc, 'status': 'fetched' if doc['id'] != 'leaf' else 'parsed'}
                                  for doc in admissions['documents']]
        detached = {**next(doc for doc in admissions['documents'] if doc['id'] == 'listing'),
                    'id': 'detached'}
        admissions['documents'] = [*admissions['documents'], detached]
        index = EvidenceIndex(registry, admissions, case.root)
        catalog = index.program(program['id'])['claims'][0]
        self.assertEqual(catalog['status'], 'verified')
        identity = next(ref for ref in catalog['evidence'] if ref['document_id'] == 'listing')
        self.assertEqual(identity['verification'], 'structured_fields')
        self.assertEqual(identity['detail_url'], case.entry['url'])
        self.assertEqual(index._source('detached')['verification'], 'invalid_source')

    def test_json_raw_value_path_and_quoted_prose_forgery_cannot_look_verified(self):
        case, registry, admissions, program = self.build()
        original = copy.deepcopy(program['evidence'])
        changes = [{'field_path': '$[999]'}, {'structured_fields': {'organName': '另一学院'}},
                   {'quote': '伪造正文逐字引文'}]
        for change in changes:
            program['evidence'] = [{**original[0], **change}, *copy.deepcopy(original[1:])]
            result = EvidenceIndex(registry, admissions, case.root).program(program['id'])
            ref = next(row for row in result['claims'][0]['evidence'] if row['document_id'] == 'listing')
            self.assertNotEqual(ref['verification'], 'structured_fields')
            self.assertFalse(ref.get('structured_fields'))
            self.assertFalse(ref.get('quote'))

    def test_self_reported_get_link_is_ignored_and_wrong_page_proof_is_rejected(self):
        case, registry, admissions, program = self.build()
        leaf = next(doc for doc in admissions['documents'] if doc['id'] == 'leaf')
        leaf['detail_url'] = 'https://evil.example/fake'
        ref = EvidenceIndex(registry, admissions, case.root).program(program['id'])['claims'][1]['evidence'][0]
        self.assertEqual(ref['detail_url'], case.page['url'])
        page = next(doc for doc in admissions['documents'] if doc['id'] == 'page')
        page['url'] = 'https://foreign.edu.cn/fake'
        result = EvidenceIndex(registry, admissions, case.root).program(program['id'])
        self.assertNotEqual(result['claims'][1]['status'], 'verified')
        self.assertTrue(all(not ref.get('detail_url') for ref in result['claims'][1]['evidence']))

    def test_a_post_page_cannot_be_advertised_as_a_verified_get_reproduction(self):
        case, registry, admissions, program = self.build()
        page = next(doc for doc in admissions['documents'] if doc['id'] == 'page')
        page['request_method'] = 'POST'
        result = EvidenceIndex(registry, admissions, case.root).program(program['id'])
        self.assertTrue(all(not ref.get('detail_url') for ref in result['claims'][1]['evidence']))

    def test_source_publication_derives_links_only_with_real_proof_and_keeps_raw_request_facts(self):
        case, registry, admissions, program = self.build()
        for doc in admissions['documents']:
            doc.update(detail_url='https://evil.example/fake', detail_url_kind='query_page',
                       detail_document_id='forged', detail_sha256='f' * 64)
        index = CatalogIndex(case.root / 'data/runtime/catalog-index.sqlite')
        index.sync(registry, admissions, {'release_ready': False}, 'v1', verifier=SourceVerifier(case.root))
        source = next(row for row in PublishedCatalog(case.root / 'data').sources()['sources'] if row['id'] == 'leaf')
        self.assertEqual(source['detail_url'], case.page['url'])
        self.assertEqual(source['detail_url_kind'], 'query_page')
        self.assertEqual(source['request_method'], 'POST')
        self.assertEqual(source['request_url'], case.leaf['url'])
        self.assertEqual(source['original_format'], 'structured_fields')
        self.assertNotIn('local_path', json.dumps(source))
        index.sync(registry, admissions, {'release_ready': False}, 'v2')
        without = next(row for row in PublishedCatalog(case.root / 'data').sources()['sources'] if row['id'] == 'leaf')
        self.assertFalse(without.get('detail_url'))
        self.assertNotIn('evil.example', json.dumps(without))

    def test_zju_get_json_keeps_paths_and_uses_the_actual_annual_query_page(self):
        case, registry, admissions, program = self.build('zju')
        result = EvidenceIndex(registry, admissions, case.root).program(program['id'])
        self.assertEqual(result['claims'][0]['status'], 'verified')
        self.assertEqual(result['claims'][1]['status'], 'verified')
        ref = result['claims'][0]['evidence'][0]
        self.assertEqual(ref['detail_url'], case.entry['url'])
        self.assertEqual(ref['request_method'], 'GET')
        public = public_value({'evidence': [ref], 'local_path': '/private/file'})
        self.assertEqual(public['evidence'][0]['field_path'], 'result[0]')
        self.assertNotIn('local_path', public)
        self.assertNotIn('field_path', public_value({'field_path': 'C:/private/path'}))

    def test_buaa_post_html_retains_exact_quotes_and_links_to_its_verified_get_form(self):
        case, registry, admissions, program = self.build('buaa')
        result = EvidenceIndex(registry, admissions, case.root).program(program['id'])
        self.assertEqual(result['claims'][0]['status'], 'verified')
        self.assertEqual(result['claims'][1]['status'], 'verified')
        source = next(ref for ref in result['claims'][1]['evidence'] if ref['document_id'] == 'leaf')
        self.assertEqual(source['verification'], 'exact_quote')
        self.assertTrue(source['quote'])
        self.assertEqual(source['detail_url'], case.entry['url'])
        self.assertEqual(source['request_method'], 'POST')
        self.assertNotEqual(source.get('original_format'), 'structured_fields')

    def test_office_national_original_is_framed_as_office_not_decoded_as_binary_html(self):
        case, registry, admissions, _ = self.build()
        buf = io.BytesIO()
        title = case.unit['name'] + '2027年硕士研究生招生专业目录'
        with zipfile.ZipFile(buf, 'w') as archive:
            archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:body><w:p><w:r><w:t>' + title + '</w:t></w:r></w:p></w:body></w:document>')
        raw = buf.getvalue()
        path = case.root / 'data/snapshots/binary.docx'
        path.write_bytes(raw)
        document = {'id': 'binary', 'unit_id': case.unit['id'], 'year': 2027, 'kind': 'catalog',
                    'status': 'parsed', 'url': case.unit['official_url'] + 'binary.docx',
                    'local_path': path.relative_to(case.root).as_posix(),
                    'sha256': hashlib.sha256(raw).hexdigest(), 'catalog_parser': 'office_national_v1'}
        index = EvidenceIndex(registry, {'programs': [], 'documents': [document]}, case.root)
        self.assertEqual(index.sources()['sources'][0]['verification'], 'snapshot_verified')


if __name__ == '__main__':
    unittest.main()
