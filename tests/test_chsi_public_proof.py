from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from urllib.parse import urlencode

from collectors.chsi_public_proof import verify_public_detail
from collectors.catalog_batch import collect_school, ingest_verified_batch
from service_verification import SourceVerifier
from tests.test_catalog_chsi import CONTEXT, UNIT, ROW, detail_html, payload


CSS_URL = 'https://t2.chei.com.cn/yz/zsml/assets/pc/css/app-1.0.1.css'
TEMPLATE = '''<link rel="stylesheet" href="''' + CSS_URL + '''">
<div v-for="item in list"><a @click="getListDetail(item)">展开</a>
<i-table :data="item.data"><template v-for="ite,ind in params.row.kskmz">
{{ite.km1Vo.kskmdm}}{{ite.km2Vo.kskmdm}}{{ite.km3Vo.kskmdm}}{{ite.km4Vo.kskmdm}}
</template><a :href="'/zsml/yjfxdetail?id=' + params.row.id">详情</a></i-table>
<div v-if="!login && item.totalCount > item.pageSize"></div>
<div v-if="!login && form.totalPage > 1"></div></div><script>
login: !!null,
</script>'''
CSS = '.need-login{width:100%;height:80px;position:absolute;bottom:20px}.need-login,.need-login-small{display:flex;align-items:center;justify-content:center}.need-login-small{height:46px;bottom:12px}'


class ChsiPublicProofTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.context = self.save('context', 'https://yz.chsi.com.cn/zsml/dwzy.do?' + urlencode(
            {'dwdm': UNIT['unit_code'], 'dwmc': UNIT['name'], 'schId': UNIT['id'], 'sign': 'raw-sign'}), CONTEXT + TEMPLATE)
        self.css = self.save('css', CSS_URL, CSS)
        unit_row = {'schId': UNIT['id'], 'dwdm': UNIT['unit_code'], 'dwmc': UNIT['name'], 'sign': 'raw-sign'}
        self.units = self.save('units', 'https://yz.chsi.com.cn/zsml/rs/dws.do',
            {'flag': True, 'msg': {'curPage': 1, 'list': [unit_row]}}, method='POST', body='start=0&curPage=1')
        self.programs = self.save('programs', 'https://yz.chsi.com.cn/zsml/rs/dwzys.do',
            {'flag': True, 'zsmlcxModel': {'dwdm': UNIT['unit_code']},
             'msg': {'curPage': 1, 'list': [{**unit_row, 'zydm': ROW['zydm'], 'zymc': ROW['zymc']}]}},
            method='POST', body='dwdm=10001&start=0&curPage=1')
        self.directions = self.save('directions', 'https://yz.chsi.com.cn/zsml/rs/yjfxs.do',
            {**payload(), 'flag': True}, method='POST', body='dwdm=10001&zydm=010101&start=0&pageSize=3')
        self.detail = {**self.save('detail', 'https://yz.chsi.com.cn/zsml/yjfxdetail?id=' + ROW['id'], detail_html()),
            'unit_id': UNIT['id'], 'year': 2027, 'kind': 'catalog', 'status': 'parsed',
            'catalog_parser': 'chsi_detail_v1', 'context_document': self.context,
            'public_gate_proof': {'version': 1, 'policy': 'first_public_item_only', 'unit_id': UNIT['id'],
                'year': 2027, 'unit_index': 0, 'program_index': 0, 'direction_index': 0, 'direction_id': ROW['id'],
                'context_document_id': 'context', 'units_document_id': 'units', 'programs_document_id': 'programs',
                'directions_document_id': 'directions', 'rules_document_ids': ['css'],
                'originals': [self.context, self.units, self.programs, self.directions, self.css]}}

    def save(self, identifier, url, value, method='GET', body=''):
        text = json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else value
        path = self.root / (identifier + '.txt')
        path.write_text(text, 'utf-8')
        return {'id': identifier, 'url': url, 'requested_url': url, 'method': method, 'request_body': body,
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'local_path': path.name,
            'content_type': 'text/html;charset=utf-8', 'status': 200}

    def verify(self, doc=None, unit=UNIT):
        return verify_public_detail(doc or self.detail, unit, 2027, project_root=self.root)

    def test_real_bound_first_item_and_get_detail_are_required(self):
        original = deepcopy(self.detail)
        result = self.verify()
        self.assertTrue(result['verified'], result.get('reason'))
        self.assertEqual(self.detail, original)
        self.assertEqual(result['context_html'], (self.root / self.context['local_path']).read_bytes().decode('utf-8'))
        self.assertEqual(len(result['documents']), 5)
        self.assertFalse(self.verify({**self.detail, 'method': 'POST'})['verified'])
        self.assertFalse(self.verify({**self.detail, 'url': self.detail['url'] + '&schId=other'})['verified'])
        for changes in [{'program_index': 1}, {'direction_index': 1}, {'unit_index': True},
                        {'unit_index': 1}, {'version': True}, {'direction_id': 'other'},
                        {'policy': 'all_raw_json_items'}, {'year': 2026}]:
            with self.subTest(changes=changes):
                self.assertFalse(self.verify({**self.detail, 'public_gate_proof': {**self.detail['public_gate_proof'], **changes}})['verified'])

    def test_each_original_is_hashed_even_after_mtime_restore(self):
        first = self.verify(); self.assertTrue(first['verified'])
        for source in [self.context, self.units, self.programs, self.directions, self.css, self.detail]:
            path = self.root / source['local_path']; raw = path.read_bytes(); state = path.stat()
            path.write_bytes(raw.replace(b'1', b'2', 1))
            os.utime(path, ns=(state.st_atime_ns, state.st_mtime_ns))
            self.assertFalse(self.verify()['verified'], source['id'])
            path.write_bytes(raw)
        changed = deepcopy(self.detail); changed['id'] = 'different-id'
        self.assertNotEqual(first['signature'], self.verify(changed)['signature'])

    def test_metadata_cannot_hide_other_school_year_or_nonpublic_rows(self):
        for identifier, transform in [('context', lambda raw: raw.replace('2027', '2026')),
                ('context', lambda raw: raw.replace('10001', '10002')),
                ('directions', lambda raw: raw.replace('"ksfsdm": "21"', '"ksfsdm": "23"')),
                ('programs', lambda raw: raw.replace('"flag": true', '"flag": false')),
                ('context', lambda raw: raw.replace('getListDetail(item)', 'unrelated(item)')),
                ('context', lambda raw: raw.replace('login: !!null', 'login: true')),
                ('css', lambda raw: raw.replace('bottom:20px', 'top:0'))]:
            with self.subTest(identifier=identifier):
                doc = deepcopy(self.detail)
                dependency = next(row for row in doc['public_gate_proof']['originals'] if row['id'] == identifier)
                path = self.root / dependency['local_path']; original = path.read_text('utf-8')
                path.write_text(transform(original), 'utf-8')
                dependency['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
                if identifier == 'context': doc['context_document'] = deepcopy(dependency)
                self.assertFalse(self.verify(doc)['verified'])
                path.write_text(original, 'utf-8')

    def test_batch_import_uses_real_detail_context_and_keeps_partial_scope(self):
        source = {**deepcopy(self.detail), 'parser': 'chsi_detail_v1'}
        source['source_path'] = str(self.root / source['local_path'])
        result = collect_school({'year': 2027, 'sources': [source]}, UNIT,
            self.root / 'snapshots', project_root=self.root, offline=True)
        self.assertEqual(len(result['programs']), 1, result.get('issues'))
        self.assertEqual(result['status']['status'], 'partial')
        self.assertEqual(len(result['documents']), 6)
        verifier = SourceVerifier(self.root)
        documents = {row['id']: row for row in result['documents']}
        self.assertTrue(verifier.program(result['programs'][0], documents, UNIT))
        merged = ingest_verified_batch({'units': [UNIT]}, [result], project_root=self.root)
        self.assertEqual(len(merged['programs']), 1)
        self.assertFalse(merged['metadata']['catalog_complete'])
        source.pop('public_gate_proof')
        failed = collect_school({'year': 2027, 'sources': [source]}, UNIT,
            self.root / 'rejected', project_root=self.root, offline=True)
        self.assertEqual(failed['programs'], [])
        self.assertEqual(failed['status']['status'], 'failed')

    def test_path_ownership_request_pagination_and_source_sets_fail_closed(self):
        proof = self.detail['public_gate_proof']
        for change in [{'originals': []}, {'originals': [*proof['originals'], proof['originals'][0]]},
                       {'unit_index': 9}, {'rules_document_ids': []}]:
            self.assertFalse(self.verify({**self.detail, 'public_gate_proof': {**proof, **change}})['verified'])
        for change in [{'local_path': '../outside.txt'}, {'sha256': 'invalid'}, {'status': 403},
                       {'request_body': 'dwdm=10001&zydm=010101&start=3&pageSize=3'},
                       {'request_body': 'dwdm=10002&zydm=010101&start=0&pageSize=3'},
                       {'request_body': 'dwdm=10001&zydm=010102&start=0&pageSize=3'},
                       {'url': self.directions['url'] + '?forged=1'}]:
            with self.subTest(change=change):
                doc = deepcopy(self.detail)
                doc['public_gate_proof']['originals'] = [{**row, **change} if row['id'] == 'directions' else row
                                                       for row in doc['public_gate_proof']['originals']]
                self.assertFalse(self.verify(doc)['verified'])
        doc = deepcopy(self.detail)
        doc['public_gate_proof']['originals'][2]['request_body'] += '&zydm=010101'
        self.assertFalse(self.verify(doc)['verified'])


if __name__ == '__main__': unittest.main()
