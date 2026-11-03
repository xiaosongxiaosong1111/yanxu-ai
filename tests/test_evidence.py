import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from evidence import EvidenceIndex, apply_evidence_safety


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.unit = {'id': 'u', 'name': '测试大学', 'official_url': 'https://www.a.edu.cn'}
        self.registry = {'units': [self.unit]}
        self.documents = []
        self.fee = self.document('fees', '测试大学2027级学费 学硕专业 8000 标准(元/生·年)')
        self.duration = self.document('duration', '测试大学2027级学制 全日制 001 计算机学院 081200 计算机科学与技术 3')
        self.program = {'id': 'p', 'unit_id': 'u', 'unit_name': '测试大学', 'year': 2027,
                        'department_code': '001', 'department': '计算机学院', 'code': '081200',
                        'name': '计算机科学与技术', 'direction': '00 不区分方向', 'study_mode': '全日制',
                        'degree_type': '学术学位',
                        'tuition': {'amount': 8000, 'period': 'year', 'currency': 'CNY', 'evidence': [
                            self.ref(self.fee, '学硕专业 8000'), self.ref(self.fee, '标准(元/生·年)')]},
                        'duration_years': 3, 'duration_evidence': [self.ref(self.duration, '全日制 001 计算机学院 081200 计算机科学与技术 3')],
                        'eligibility_evidence_complete': False, 'requires_review': True, 'subjects': []}
        self.admissions = {'metadata': {'reference_year': 2027, 'last_completed_admission_year': 2026},
                           'documents': self.documents, 'programs': [self.program], 'admitted_statistics': [],
                           'score_lines': [], 'transfer_rules': [], 'retest_rules': []}

    def document(self, kind, text, year=2027, unit_id='u', identity=None):
        name = identity or f'{kind}-{len(self.documents)}'
        path = self.root / f'{name}.html'
        path.write_text(text, 'utf-8')
        document = {'id': name, 'unit_id': unit_id, 'year': year, 'kind': kind, 'title': f'测试大学{year}年{kind}',
                    'url': f'https://grad.a.edu.cn/{name}.html', 'local_path': path.name,
                    'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'status': 'verified',
                    'published_at': '2026-10-01', 'checked_at': '2026-10-29T00:00:00Z'}
        self.documents.append(document)
        return document

    @staticmethod
    def ref(document, quote, **extra):
        return {'document_id': document['id'], 'url': document['url'], 'quote': quote, 'page': 1, **extra}

    def index(self):
        return EvidenceIndex(self.registry, self.admissions, self.root)

    def chsi(self):
        from collectors.catalog_chsi import parse_chsi_detail_html
        from tests.test_catalog_chsi import UNIT,ROW,CONTEXT,detail_html
        self.unit=copy.deepcopy(UNIT)
        self.registry={'units':[self.unit]}
        raw=detail_html().encode()
        path=self.root/'chsi-detail.html';path.write_bytes(raw)
        self.chsi_document={'id':'chsi-detail','unit_id':UNIT['id'],'year':2027,'kind':'catalog','status':'parsed',
            'url':'https://yz.chsi.com.cn/zsml/yjfxdetail?id='+ROW['id'], 'local_path':path.name,
            'sha256':hashlib.sha256(raw).hexdigest(),'catalog_parser':'chsi_detail_v1'}
        self.program=parse_chsi_detail_html(raw.decode(),self.unit,2027,self.chsi_document,context_html=CONTEXT)['programs'][0]
        self.admissions={**self.admissions,'documents':[self.chsi_document],'programs':[self.program]}
        return patch('service_verification.SourceVerifier._chsi_proof',return_value={
            'verified':True,'signature':'unit-test-public-gate','context_html':CONTEXT,'documents':[]})

    def test_chsi_structured_original_fields_have_raw_values_paths_and_stable_detail_link(self):
        with self.chsi():
            index=self.index()
            result=index.program(self.program['id'])
            catalog=next(row for row in result['claims'] if row['id']=='catalog')
            subjects=next(row for row in result['claims'] if row['id']=='subjects')
            quota=next(row for row in result['claims'] if row['id']=='enrollment')
            self.assertEqual([row['status'] for row in (catalog,subjects,quota)],['verified']*3)
            item=catalog['evidence'][0]
            self.assertEqual(item['verification'],'structured_fields')
            self.assertEqual(item['field_path'],'detail.labels')
            self.assertEqual(item['structured_fields']['招生单位'],'(10001)北京大学')
            self.assertEqual(item['detail_url'],self.chsi_document['url'])
            self.assertEqual(item['quote'],'')
            self.assertIsNone(item['page'])
            self.assertEqual(subjects['evidence'][0]['structured_fields']['kskm'][0]['km1Vo']['kskmdm'],'101')
            self.assertEqual(result['summary']['source_count'],1)
            source=index.sources()['sources'][0]
            self.assertEqual(source['verification'],'snapshot_verified')
            self.assertEqual(source['original_format'],'structured_fields')
            self.assertNotIn('local_path',json.dumps(result))
            item['structured_fields']['招生单位']='mutated'
            self.assertEqual(index.program(self.program['id'])['claims'][0]['evidence'][0]['structured_fields']['招生单位'],'(10001)北京大学')

    def test_chsi_wrong_value_path_school_or_unproven_public_gate_never_displays_verified_raw_fields(self):
        with self.chsi():
            original=copy.deepcopy(self.program['evidence'])
            for key,value in [('field_path','detail.labels.其他'),('structured_fields',{'招生单位':'(10002)其他大学'})]:
                self.program['evidence']=[{**original[0],key:value}]
                result=self.index().program(self.program['id'])
                item=result['claims'][0]['evidence'][0]
                self.assertNotEqual(item['verification'],'structured_fields')
                self.assertFalse(item.get('structured_fields'))
                self.assertFalse(item['quote'])
            self.program['evidence']=original
            self.chsi_document['unit_id']='wrong-school'
            self.assertNotEqual(self.index().program(self.program['id'])['claims'][0]['status'],'verified')
        with patch('service_verification.SourceVerifier._chsi_proof',return_value={'verified':False}):
            source=self.index().sources()['sources'][0]
            self.assertEqual(source['verification'],'invalid_source')
            self.assertEqual(source['url'],'')

    def test_chsi_structured_field_references_are_distinct_and_quota_mutation_invalidates_cached_claim(self):
        with self.chsi():
            index=self.index()
            self.assertEqual(index.program(self.program['id'])['claims'][4]['status'],'verified')
            refs=[subject['evidence'] for subject in self.program['subjects']]
            values=index._references(refs,self.program)
            self.assertEqual(len(values),4)
            self.assertEqual(len({item['id'] for item in values}),4)
            self.program['planned_enrollment']={**self.program['planned_enrollment'],'count':99}
            self.assertNotEqual(index.program(self.program['id'])['claims'][4]['status'],'verified')

    def test_chsi_saved_public_chain_replays_without_mock_and_rejects_changed_gate_original(self):
        from tests import test_chsi_public_proof as proof_fixture
        from collectors.catalog_batch import collect_school
        fixture=proof_fixture.ChsiPublicProofTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        unit=proof_fixture.UNIT
        source={**fixture.detail,'parser':'chsi_detail_v1',
                'source_path':str(fixture.root/fixture.detail['local_path'])}
        parsed=collect_school({'year':2027,'sources':[source]},unit,fixture.root/'snapshots',
                              project_root=fixture.root,offline=True)
        admissions={**self.admissions,'programs':parsed['programs'],'documents':parsed['documents']}
        value=EvidenceIndex({'units':[unit]},admissions,fixture.root).program(parsed['programs'][0]['id'])
        selected=[row for row in value['claims'] if row['id'] in {'catalog','subjects','enrollment'}]
        self.assertTrue(all(row['status']=='verified' for row in selected))
        self.assertTrue(all(item['verification']=='structured_fields' and item['field_path']
                            and item['structured_fields'] and not item['quote']
                            for row in selected for item in row['evidence']))
        path=fixture.root/fixture.css['local_path']
        before=path.stat()
        path.write_bytes(path.read_bytes().replace(b'20px',b'21px'))
        import os
        os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns))
        rejected=EvidenceIndex({'units':[unit]},admissions,fixture.root).program(parsed['programs'][0]['id'])
        self.assertFalse(any(row['status']=='verified' for row in rejected['claims']))

    def claim(self, field):
        return next(item for item in self.index().program('p')['claims'] if item['id'] == field)

    def test_per_field_exact_original_and_official_metadata(self):
        value = self.claim('tuition')
        self.assertEqual(value['status'], 'verified')
        self.assertEqual(value['source_count'], 1)
        self.assertEqual(value['value']['amount'], 8000)
        self.assertEqual(value['evidence'][0]['quote'], '学硕专业 8000')
        self.assertEqual(value['evidence'][0]['verification'], 'exact_quote')
        self.assertEqual(value['evidence'][0]['publisher'], '测试大学')
        self.assertEqual(self.claim('duration')['status'], 'verified')
        self.assertEqual(self.claim('eligibility')['status'], 'needs_review')
        self.assertNotIn('local_path', json.dumps(self.index().program('p')))

    def test_repeated_quotes_and_mirrored_url_are_one_original_source(self):
        mirror = {**self.fee, 'id': 'mirror', 'url': 'https://yz.a.edu.cn/fees.html'}
        self.documents.append(mirror)
        item = self.ref(mirror, '学硕专业 8000')
        self.program['tuition']['evidence'].extend([item, item])
        claim = self.claim('tuition')
        self.assertEqual(claim['source_count'], 1)
        self.assertEqual(len([e for e in claim['evidence'] if e['quote'] == '学硕专业 8000']), 1)
        overview = self.index().sources()
        self.assertEqual(overview['total'], 2)
        self.assertEqual(next(row for row in overview['sources'] if row['kind'] == 'fees')['related_program_ids'], ['p'])

    def test_bad_quote_and_source_hash_never_claim_verified_original(self):
        self.program['tuition']['evidence'][0]['quote'] = '学硕专业 9000'
        value = self.claim('tuition')
        self.assertEqual(value['status'], 'needs_review')
        self.assertFalse(any(e['quote'] == '学硕专业 9000' for e in value['evidence']))
        (self.root / self.fee['local_path']).write_text('tampered', 'utf-8')
        self.assertEqual(self.claim('tuition')['status'], 'needs_review')

    def test_only_exact_same_scope_same_period_conflicts_and_blocks_algorithm_copy(self):
        other = self.document('fees', '测试大学2027级学费 学硕专业 9000元/年')
        self.program['tuition']['evidence'].append(self.ref(other, '学硕专业 9000元/年'))
        before = copy.deepcopy(self.admissions)
        self.assertEqual(self.claim('tuition')['status'], 'conflict')
        safe = apply_evidence_safety(self.registry, self.admissions, self.root)
        self.assertIsNone(safe['programs'][0]['tuition'])
        self.assertEqual(safe['programs'][0]['evidence_conflicts']['tuition']['amount'], 8000)
        self.assertEqual(self.admissions, before)
        self.assertEqual(next(c for c in EvidenceIndex(self.registry, safe, self.root).program('p')['claims'] if c['id'] == 'tuition')['status'], 'conflict')
        self.program['tuition']['evidence'].pop()
        self.assertEqual(apply_evidence_safety(self.registry, self.admissions, self.root)['programs'][0]['tuition']['amount'], 8000)

    def test_different_year_school_mode_direction_or_period_is_not_conflict(self):
        for extra, year, unit, quote in [({}, 2026, 'u', '学硕专业 9000'),
                                         ({}, 2027, 'other', '学硕专业 9000'),
                                         ({'scope': {'study_mode': '非全日制'}}, 2027, 'u', '学硕专业 9000'),
                                         ({'scope': {'direction': '01 人工智能'}}, 2027, 'u', '学硕专业 9000'),
                                         ({}, 2027, 'u', '学费全程共9000元')]:
            with self.subTest(extra=extra, year=year, unit=unit, quote=quote):
                doc = self.document('fees', quote, year, unit)
                self.program['tuition']['evidence'].append(self.ref(doc, quote, **extra))
                self.assertNotEqual(self.claim('tuition')['status'], 'conflict')
                self.program['tuition']['evidence'].pop()

    def test_forged_typed_value_cannot_create_a_conflict(self):
        self.program['tuition']['evidence'].append(self.ref(self.fee, '学硕专业 8000', value=9000))
        self.assertNotEqual(self.claim('tuition')['status'], 'conflict')
        self.assertEqual(apply_evidence_safety(self.registry, self.admissions, self.root)['programs'][0]['tuition']['amount'], 8000)

    def test_quote_exists_does_not_prove_wrong_program_or_billing_unit(self):
        self.program['degree_type'] = '专业学位'
        self.assertEqual(self.claim('tuition')['status'], 'needs_review')
        self.program['degree_type'] = '学术学位'
        self.program['tuition']['evidence'].pop()
        self.assertEqual(self.claim('tuition')['status'], 'needs_review')

    def test_common_school_rule_is_never_labeled_direction_exclusive(self):
        quote = self.claim('tuition')['evidence'][0]
        self.assertEqual(quote['scope']['level'], 'unit')
        self.assertFalse(quote['scope']['direction_specific'])

    def test_exact_page_and_unknown_page_are_not_fabricated(self):
        self.program['tuition']['evidence'][0]['page'] = 2
        self.assertEqual(self.claim('tuition')['status'], 'needs_review')
        self.program['tuition']['evidence'][0]['page'] = None
        claim = self.claim('tuition')
        self.assertEqual(claim['status'], 'verified')
        self.assertIsNone(claim['evidence'][0]['page'])

    def test_sources_filter_and_current_historical_versions(self):
        self.fee['catalog_active'] = False
        self.fee['version_state'] = 'superseded'
        result = self.index().sources(unit_id='u', year=2027, kind='fees', q='测试')
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['sources'][0]['version_state'], 'superseded')
        self.assertEqual(self.index().sources(q='absent')['sources'], [])

    def test_wrong_chsi_school_and_external_sources_are_rejected(self):
        for url in ['https://evil.example/fee', 'https://yz.chsi.com.cn/sch/info--schId-999.dhtml']:
            with self.subTest(url=url):
                self.fee['url'] = url
                self.program['tuition']['evidence'] = [self.ref(self.fee, '学硕专业 8000')]
                claim = self.claim('tuition')
                self.assertEqual(claim['status'], 'needs_review')
                self.assertTrue(all(not item['url'] for item in claim['evidence']))

    def test_other_department_or_direction_cannot_erase_a_verified_value(self):
        for field, kind, quote in [('tuition', 'fees', '数学学院 学硕专业9000元/年'),
                                   ('duration_years', 'duration', '计算机学院 081200 01人工智能 学制2年')]:
            with self.subTest(field=field):
                document = self.document(kind, quote)
                refs = self.program['tuition']['evidence'] if field == 'tuition' else self.program['duration_evidence']
                refs.append(self.ref(document, quote))
                safe = apply_evidence_safety(self.registry, self.admissions, self.root)
                self.assertIsNotNone(safe['programs'][0][field])
                claim = self.claim('tuition' if field == 'tuition' else 'duration')
                self.assertEqual(claim['source_count'], 1)
                self.assertEqual(claim['evidence'][-1]['relation'], 'out_of_scope')
                refs.pop()

    def test_raw_title_year_overrides_incorrect_document_metadata(self):
        path = self.root / self.fee['local_path']
        path.write_text('<h1>测试大学2026级学费</h1><p>学硕专业 8000</p><p>标准(元/生·年)</p>', 'utf-8')
        self.fee['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertEqual(self.claim('tuition')['status'], 'needs_review')

    def test_score_scopes_and_original_numeric_values_are_checked(self):
        document = self.document('score', '<h1>测试大学2026年复试基本要求</h1><p>08 工学 总分300 政治40 外语40 业务课一60 业务课二60</p>', 2026)
        line = {'id': 'line', 'unit_id': 'u', 'year': 2026, 'kind': 'university', 'discipline_code': '08',
                'degree_type': '学术学位', 'total': 300, 'politics': 40, 'foreign_language': 40,
                'business1': 60, 'business2': 60,
                'evidence': self.ref(document, '08 工学 总分300 政治40 外语40 业务课一60 业务课二60')}
        self.admissions['score_lines'] = [line]
        claim = next(row for row in self.index().program('p')['claims'] if row['id'] == 'score:line')
        self.assertEqual(claim['status'], 'verified')
        line['total'] = 399
        claim = next(row for row in self.index().program('p')['claims'] if row['id'] == 'score:line')
        self.assertEqual(claim['status'], 'needs_review')
        line['discipline_code'] = '10'
        self.assertNotIn('score:line', [row['id'] for row in self.index().program('p')['claims']])
        line['discipline_code'] = '08'
        line['degree_type'] = '专业学位'
        self.assertNotIn('score:line', [row['id'] for row in self.index().program('p')['claims']])

    def test_unknown_degree_school_line_is_reference_and_prefixes_restrict_it(self):
        document = self.document('score', '测试大学2026年复试线 085工程 总分300', 2026)
        line = {'id': 's', 'unit_id': 'u', 'year': 2026, 'kind': 'university', 'code_prefixes': ['085', '086'],
                'degree_type': '专业学位', 'total': 300, 'evidence': self.ref(document, '085工程 总分300')}
        self.admissions['score_lines'] = [line]
        self.program['code'] = '085400'
        self.program['degree_type'] = None
        claim = next(row for row in self.index().program('p')['claims'] if row['id'] == 'score:s')
        self.assertEqual(claim['status'], 'needs_review')
        self.assertEqual(claim['applicability'], 'unknown')
        self.program['code'] = '081200'
        self.assertNotIn('score:s', [row['id'] for row in self.index().program('p')['claims']])

    def test_historical_statistics_replay_original_and_do_not_expose_people(self):
        from collectors.history import parse_results_html
        html = '<h1>测试大学2026年复试结果公示</h1><table><tr><th>复试专业</th><th>学习方式</th><th>姓名</th><th>总分</th><th>是否拟录取</th></tr>'
        html += '<tr><td>计算机科学与技术</td><td>全日制</td><td>PRIVATE_NAME</td><td>350</td><td>是</td></tr></table>'
        document = self.document('admission_results', html, 2026)
        row = parse_results_html(html, unit_id='u', unit_name='测试大学', department='计算机学院', year=2026,
                                 url=document['url'], document_id=document['id'])['admitted_statistics'][0]
        self.admissions['admitted_statistics'] = [row]
        claim = next(c for c in self.index().program('p')['claims'] if c['id'].startswith('history:'))
        self.assertEqual(claim['status'], 'derived')
        self.assertTrue(claim['evidence'])
        self.assertNotIn('PRIVATE_NAME', json.dumps(claim))
        self.assertTrue(all(e['quote'] in {'复试专业', '学习方式', '总分', '是否拟录取'} for e in claim['evidence']))
        row['initial']['min'] = 100
        changed = next(c for c in self.index().program('p')['claims'] if c['id'].startswith('history:'))
        self.assertEqual(changed['status'], 'needs_review')
        safe = apply_evidence_safety(self.registry, self.admissions, self.root)
        self.assertEqual(safe['admitted_statistics'][0]['evidence_status'], 'needs_review')
        self.assertEqual(safe['admitted_statistics'][0]['initial']['min'], 100)
        source = next(source for source in self.index().sources()['sources'] if source['id'] == document['id'])
        self.assertIn({'program_id': 'p', 'claim_id': 'history:' + row['id'], 'label': '2026年公示统计'}, source['related_claims'])

    def test_declared_score_column_order_and_original_header_are_required(self):
        text = '测试大学2026年复试线\n政治 外语 业务科一 业务科二 总分\n工学（08） 50 40 70 75 300'
        document = self.document('score', text, 2026)
        line = {'id': 'ordered', 'unit_id': 'u', 'year': 2026, 'kind': 'university', 'discipline_code': '08',
                'degree_type': '学术学位', 'source_scope_name': '工学（08）', 'total': 300, 'politics': 50,
                'foreign_language': 40, 'business1': 70, 'business2': 75,
                'column_order': ['politics', 'foreign_language', 'business1', 'business2', 'total'],
                'evidence': [self.ref(document, '工学（08） 50 40 70 75 300'),
                             self.ref(document, '政治 外语 业务科一 业务科二 总分', role='column_header', relation='context')]}
        self.admissions['score_lines'] = [line]
        safe = apply_evidence_safety(self.registry, self.admissions, self.root)
        self.assertEqual(safe['score_lines'][0]['evidence_status'], 'verified')
        line['column_order'] = ['total', 'politics', 'foreign_language', 'business1', 'business2']
        safe = apply_evidence_safety(self.registry, self.admissions, self.root)
        self.assertEqual(safe['score_lines'][0]['evidence_status'], 'needs_review')
        self.assertEqual(safe['score_lines'][0]['total'], 300)


class InlineCatalogEvidenceTests(unittest.TestCase):
    setUp = EvidenceTests.setUp
    document = EvidenceTests.document
    ref = staticmethod(EvidenceTests.ref)
    index = EvidenceTests.index

    def catalog(self):
        from collectors.catalog_html import parse_catalog_html
        from collectors.catalog_enrichment import apply_inline_policies
        html = '<h1>测试大学2027年硕士研究生招生章程及招生专业目录</h1>'
        html += '<table><tr><th>学院</th><th>专业</th><th>研究方向</th><th>学习方式</th><th>初试科目</th></tr>'
        html += '<tr><td>001计算机学院</td><td>085404计算机技术</td><td>00不区分方向</td><td>全日制</td>'
        html += '<td>①101思想政治理论②201英语（一）③301数学（一）④408计算机学科专业基础</td></tr></table>'
        html += '<table><tr><th>专业</th><th>学费（每生每年）</th><th>住宿费（每生每年）</th><th>学制</th><th>学习形式</th></tr>'
        html += '<tr><td>计算机技术</td><td>10000元</td><td>1200元</td><td>3年</td><td>全日制</td></tr>'
        html += '<tr><td>软件工程</td><td>12000元</td><td>1200元</td><td>2年</td><td>全日制</td></tr></table>'
        document = self.document('catalog', html)
        document['catalog_parser'] = 'html_table_v1'
        parsed = parse_catalog_html(html, self.unit, 2027, document)
        self.assertEqual(parsed['issues'], [])
        self.admissions['programs'] = apply_inline_policies(parsed['programs'], html, self.unit, 2027, document)
        self.program = self.admissions['programs'][0]
        return document

    def inline_claim(self, field):
        return next(item for item in self.index().program(self.program['id'])['claims'] if item['id'] == field)

    def test_original_subject_identity_conflict_keeps_catalog_and_raw_values_but_not_verified_subjects(self):
        from collectors.catalog_html import parse_catalog_html
        document=self.catalog()
        path=self.root/document['local_path']
        html=path.read_text('utf-8').replace('301数学（一）','301数学（二）')
        path.write_text(html,'utf-8')
        document['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
        parsed=parse_catalog_html(html,self.unit,2027,document)
        self.program=parsed['programs'][0]
        self.admissions['programs']=[self.program]
        value=self.index().program(self.program['id'])
        catalog=next(row for row in value['claims'] if row['id']=='catalog')
        subjects=next(row for row in value['claims'] if row['id']=='subjects')
        self.assertEqual(catalog['status'],'verified')
        self.assertEqual(subjects['status'],'needs_review')
        self.assertIn('代码与名称',subjects['note'])
        self.assertTrue(any(row['code']=='301' and row['name']=='数学（二）' for row in subjects['value']))
        self.assertTrue(any('301数学（二）' in ref['quote'] for ref in subjects['evidence']))

    def test_same_catalog_table_fields_replay_original_rows_and_headers(self):
        self.catalog()
        self.assertEqual(self.program['tuition']['amount'], 10000)
        self.assertEqual(self.inline_claim('tuition')['status'], 'verified')
        self.assertEqual(self.inline_claim('duration')['status'], 'verified')
        safe = apply_evidence_safety(self.registry, self.admissions, self.root)
        self.assertEqual(safe['programs'][0]['tuition']['amount'], 10000)
        self.assertEqual(safe['programs'][0]['duration_years'], 3)

    def test_other_row_number_or_incomplete_header_cannot_be_borrowed(self):
        document = self.catalog()
        before = copy.deepcopy(self.program)
        self.program['tuition'] = {**self.program['tuition'], 'amount': 12000,
            'evidence': [self.ref(document, '学费（每生每年）'), self.ref(document, '软件工程12000元')]}
        self.assertEqual(self.inline_claim('tuition')['status'], 'needs_review')
        self.program.clear()
        self.program.update(before)
        self.program['tuition']['evidence'] = self.program['tuition']['evidence'][1:]
        self.assertEqual(self.inline_claim('tuition')['status'], 'needs_review')

    def test_catalog_identity_currency_and_period_are_verified_independently(self):
        self.catalog()
        before = copy.deepcopy(self.program)
        for field, value in [('code', '085405'), ('direction', '01人工智能')]:
            with self.subTest(field=field):
                self.program[field] = value
                self.assertEqual(self.inline_claim('tuition')['status'], 'needs_review')
                self.program.clear()
                self.program.update(copy.deepcopy(before))
        for field, value in [('currency', 'USD'), ('period', 'total'), ('amount', 12000)]:
            with self.subTest(field=field):
                self.program['tuition'][field] = value
                self.assertEqual(self.inline_claim('tuition')['status'], 'needs_review')
                self.program['tuition'] = copy.deepcopy(before['tuition'])

    def test_replayed_catalog_values_still_conflict_with_same_scope_other_source(self):
        self.catalog()
        document = self.document('fees', '测试大学2027年学费 计算机技术9000元/年')
        self.program['tuition']['evidence'].append(self.ref(document, '计算机技术9000元/年'))
        self.assertEqual(self.inline_claim('tuition')['status'], 'conflict')
        self.assertIsNone(apply_evidence_safety(self.registry, self.admissions, self.root)['programs'][0]['tuition'])

    def test_zsml_duration_replays_the_school_tree_and_program_group_note(self):
        from collectors.catalog_zsml import parse_zsml_html
        from tests.test_catalog_zsml import HTML, TREE, UNIT
        self.registry = {'units': [UNIT]}
        html = HTML.replace('<tr><td>备注</td>', '<tr><td>学制及考试特殊用具说明</td><td colspan="3">学制三年，特殊用具无。</td></tr><tr><td>备注</td>')
        tree = self.document('catalog', json.dumps(TREE, ensure_ascii=False), unit_id=UNIT['id'])
        tree.update({'catalog_parser': 'zsml_tree', 'url': 'https://gsas.ncu.edu.cn/zsml/getTreeData'})
        document = self.document('catalog', html, unit_id=UNIT['id'])
        document.update({'catalog_parser': 'zsml_table', 'department': '001 法学院',
                         'url': 'https://gsas.ncu.edu.cn/zsml/sszsml/zsmlYjfx/2027/001/030100?ksfs=21'})
        parsed = parse_zsml_html(html, UNIT, '001 法学院', 2027, document)
        self.admissions['programs'] = parsed['programs']
        self.program = parsed['programs'][0]
        self.assertEqual(self.program['duration_years'], 3)
        self.assertEqual(self.inline_claim('duration')['status'], 'verified')
        self.program['duration_years'] = 2
        self.assertEqual(self.inline_claim('duration')['status'], 'needs_review')

    def test_superseded_catalog_does_not_verify_active_fields(self):
        document = self.catalog()
        document['catalog_active'] = False
        self.assertEqual(self.inline_claim('tuition')['status'], 'needs_review')

    def test_shared_field_checks_reuse_verification_without_reusing_changed_identity(self):
        self.catalog()
        index = self.index()
        with patch.object(index.verifier, 'program', wraps=index.verifier.program) as verify:
            index.program(self.program['id'])
            self.assertEqual(verify.call_count, 1)
            index.program(self.program['id'])
            self.assertEqual(verify.call_count, 1)
            self.program['subjects'][0]['code'] = '999'
            claims = index.program(self.program['id'])['claims']
            self.assertEqual(verify.call_count, 2)
            self.assertEqual(next(c for c in claims if c['id'] == 'tuition')['status'], 'needs_review')

    def test_verification_cache_rechecks_changed_original_references(self):
        self.catalog()
        index = self.index()
        with patch.object(index.verifier, 'program', wraps=index.verifier.program) as verify:
            index.program(self.program['id'])
            self.program['evidence'][0]['quote'] = '伪造原文'
            claims = index.program(self.program['id'])['claims']
            self.assertEqual(verify.call_count, 2)
            self.assertEqual(next(c for c in claims if c['id'] == 'catalog')['status'], 'needs_review')

    def test_verification_cache_rechecks_changed_primary_catalog_source(self):
        self.catalog()
        index = self.index()
        with patch.object(index.verifier, 'program', wraps=index.verifier.program) as verify:
            index.program(self.program['id'])
            self.program['catalog_document_id'] = 'wrong-original'
            claims = index.program(self.program['id'])['claims']
            self.assertEqual(verify.call_count, 2)
            self.assertEqual(next(c for c in claims if c['id'] == 'catalog')['status'], 'needs_review')

    def test_field_replay_reuses_the_independent_catalog_parse(self):
        from collectors.catalog_html import parse_catalog_html
        self.catalog()
        index = self.index()
        with patch('collectors.catalog_html.parse_catalog_html', wraps=parse_catalog_html) as parse:
            claims = index.program(self.program['id'])['claims']
            self.assertEqual(parse.call_count, 1)
            self.assertEqual(next(c for c in claims if c['id'] == 'tuition')['status'], 'verified')

    def test_unreplayed_structured_catalog_fields_never_use_numeric_text_fallback(self):
        document = self.catalog()
        document['catalog_parser'] = 'future_table_v9'
        self.assertEqual(self.inline_claim('tuition')['status'], 'needs_review')
        self.assertEqual(self.inline_claim('duration')['status'], 'needs_review')

    def test_office_source_uses_shared_original_text_extraction(self):
        from collectors.catalog_office import extract_office_html
        from tests.test_catalog_office import docx
        document = self.catalog()
        path = self.root / document['local_path']
        path.write_bytes(docx([['专业', '研究方向', '学习方式', '初试科目']]))
        document.update({'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                         'catalog_parser': 'office_table_v1'})
        index = self.index()
        with patch('collectors.catalog_office.extract_office_html', wraps=extract_office_html) as extract:
            self.assertEqual(index._source(document['id'])['verification'], 'snapshot_verified')
            self.assertEqual(extract.call_count, 1)

    def test_office_title_year_is_independent_of_historical_years_in_the_body(self):
        from tests.test_catalog_office import package
        document = self.catalog()
        xml = '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
        xml += '<w:p><w:r><w:t>测试大学2027年硕士研究生招生专业目录</w:t></w:r></w:p>'
        xml += '<w:p><w:r><w:t>2026年历史资料仅作参考。</w:t></w:r></w:p></w:body></w:document>'
        path = self.root / document['local_path']
        path.write_bytes(package({'word/document.xml': xml}))
        document.update({'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'catalog_parser': 'office_table_v1'})
        self.assertEqual(self.index()._source(document['id'])['verification'], 'snapshot_verified')

    def test_unreadable_office_original_has_a_source_status_instead_of_breaking_evidence(self):
        document = self.catalog()
        path = self.root / document['local_path']
        path.write_bytes(b'PK\x03\x04corrupt office file')
        document.update({'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'catalog_parser': 'office_table_v1'})
        self.assertEqual(self.index()._source(document['id'])['verification'], 'invalid_source')
        self.assertEqual(self.inline_claim('tuition')['status'], 'needs_review')

    def test_real_docx_catalog_claims_preserve_all_or_options_and_no_page_is_invented(self):
        from collectors.catalog_office import parse_catalog_office
        from tests.test_catalog_office import docx, CatalogOfficeTests
        document = self.catalog()
        path = self.root / document['local_path']
        raw = docx(CatalogOfficeTests().rows())
        path.write_bytes(raw)
        document.update({'sha256': hashlib.sha256(raw).hexdigest(), 'catalog_parser': 'office_table_v1'})
        self.admissions['programs'] = parse_catalog_office(raw, self.unit, 2027, document)['programs']
        self.program = self.admissions['programs'][0]
        claims = self.index().program(self.program['id'])['claims']
        self.assertTrue(all(next(c for c in claims if c['id'] == key)['status'] == 'verified'
                            for key in ['catalog', 'subjects']))
        refs = next(c for c in claims if c['id'] == 'subjects')['evidence']
        self.assertTrue(all(ref['page'] is None and ref['verification'] == 'exact_quote' for ref in refs))
        self.program['subjects'] = [s for s in self.program['subjects'] if s['code'] != '203']
        self.assertEqual(self.inline_claim('subjects')['status'], 'needs_review')

    def test_chsi_published_hidden_body_is_used_for_original_quotes_and_same_source_fields(self):
        from html import escape
        document = self.catalog()
        path = self.root / document['local_path']
        inner = path.read_text('utf-8')
        raw = '<h1>测试大学2027年硕士研究生招生专业目录</h1><input type="hidden" id="hid_content" value="' + escape(inner, quote=True) + '">'
        raw += '<div id="cnt_body_wrapper"></div><script>$("#cnt_body_wrapper").html($("#hid_content").val());</script>'
        path.write_text(raw, 'utf-8')
        old_url = document['url']
        document.update({'url': 'https://yz.chsi.com.cn/zsgs/zsjz/detail.action?id=1',
                         'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
        self.program = json.loads(json.dumps(self.program, ensure_ascii=False).replace(old_url, document['url']))
        self.admissions['programs'] = [self.program]
        claims = self.index().program(self.program['id'])['claims']
        self.assertTrue(all(next(c for c in claims if c['id'] == key)['status'] == 'verified'
                            for key in ['catalog', 'subjects', 'tuition', 'duration']))

    def test_unpublished_hidden_input_never_becomes_field_evidence(self):
        from html import escape
        document = self.catalog()
        path = self.root / document['local_path']
        inner = path.read_text('utf-8')
        raw = '<h1>测试大学2027年硕士研究生招生专业目录</h1><input type="hidden" id="hid_content" value="' + escape(inner, quote=True) + '">'
        path.write_text(raw, 'utf-8')
        old_url = document['url']
        document.update({'url': 'https://yz.chsi.com.cn/zsgs/zsjz/detail.action?id=1',
                         'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
        self.program = json.loads(json.dumps(self.program, ensure_ascii=False).replace(old_url, document['url']))
        self.admissions['programs'] = [self.program]
        claims = self.index().program(self.program['id'])['claims']
        self.assertTrue(all(next(c for c in claims if c['id'] == key)['status'] == 'needs_review'
                            for key in ['catalog', 'subjects', 'tuition', 'duration']))


if __name__ == '__main__':
    unittest.main()
