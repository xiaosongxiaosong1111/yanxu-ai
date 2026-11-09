"""Offline replay of untouched, publicly published school originals.

These are two recorded HTML originals, not fabricated catalog rows or a live
Yuanbao/AI acceptance. The source school has no published rows in this fixture.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from grad_engine import validate_profile
from knowledge_store import KnowledgeStore
from public_sources import document_attribution, extract_saved_document
from research_candidate_tools import refresh_saved_candidates
from research_model_projection import project_segment_read
from research_tools import ResearchTools
from tests.test_source_candidate_tools import EmptyAdmission
from tests.test_research_service import FakeSettings


FIXTURE = Path(__file__).parent / 'fixtures/source_candidates/bift2027'
UNIT = {'id': '367889', 'name': '北京服装学院', 'code': '10012',
        'official_url': 'https://yjs.bift.edu.cn', 'province': '北京'}
EXAMS = '②201英语(一)③302数学(二)④902服装理论'
POLITICS = '1.考试科目一均为：①101思想政治理论。'
MODE = '我校所有专业均招收全日制硕士研究生。'


class RecordedOfficialCandidateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='bift-original-replay-')
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name)
        self.store = KnowledgeStore(self.data)
        self.admission = EmptyAdmission()
        self.admission.units = [deepcopy(UNIT)]
        self.sources = json.loads((FIXTURE / 'sources.json').read_text('utf-8'))
        self.entries = {}
        self.documents = {}
        for source in self.sources:
            raw = (FIXTURE / source['snapshot']).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), source['sha256'])
            document = extract_saved_document(raw, 'text/html', source['source_url'])
            attribution = document_attribution(document['url'], UNIT, document['text'],
                                               document['document_title'], document['document_heading'])
            self.assertEqual(attribution['status'], 'verified')
            entry = self.store.save_source({
                'url': source['source_url'], 'title': source['expected_title'],
                'unit_id': UNIT['id'], 'unit_name': UNIT['name'], 'year': source['admission_year'],
                'requested_year': 2027, 'status': 'official_original_read', 'sha256': source['sha256'],
                'checked_at': source['retrieved_at_utc']},
                document={**document, 'attribution_status': attribution['status']})
            self.entries[source['kind']] = entry
            self.documents[entry['id']] = document['extraction']['text']
        prepared = [{'code': code, 'scope': 'national' if code != '902' else UNIT['id'], 'exam_unit': index}
                    for index, code in enumerate(('101', '201', '302', '902'), 1)]
        self.task = {'id': 'r_' + 'b' * 32, 'query': '北京服装学院服装设计与工程，看看备考科目是否衔接',
                     'profile': validate_profile({'subjects': prepared}), 'unit_ids': [UNIT['id']],
                     'reference_year': 2027, 'target_year': 2028}
        self.tools = ResearchTools(self.admission, FakeSettings(), self.task, self.data,
                                   knowledge_store=self.store)

    def read(self, entry_kind, query):
        rows = self.tools.run('search_knowledge_segments', {'query': query,
                             'entry_id': self.entries[entry_kind]['id'], 'unit_id': UNIT['id']})['segments']
        self.assertTrue(rows, (entry_kind, query))
        row = next((item for item in rows if query in item['text']), rows[0])
        value = self.tools.run('read_knowledge_segment', {'segment_id': row['id'], 'max_chars': 8000})
        self.assertNotIn('error', value)
        for block in [value['segment'], *value['contexts'], *value['neighbors']]:
            self.assertEqual(self.documents[row['entry_id']][block['char_start']:block['char_end']], block['text'])
        delivered = project_segment_read(value)
        self.tools.confirm_segment_delivery(value, delivered)
        self.assertFalse(delivered['needs_narrower_read'])
        return value['segment']

    def citation(self, segment, quote=None):
        quote = segment['text'] if quote is None else quote
        offset = segment['text'].index(quote)
        return {'segment_id': segment['id'], 'char_start': segment['char_start'] + offset,
                'char_end': segment['char_start'] + offset + len(quote), 'quote': quote}

    def proposal(self):
        row = self.read('catalog', '082104')
        politics = self.read('catalog', '考试科目一均为')
        mode = self.read('charter', MODE)
        args = {'source_id': row['source_id'], 'segment_id': row['id'], 'drafts': [{
            'code': '082104', 'name': '服装设计与工程', 'department': '001服装艺术与工程学院',
            'direction': '01功能性服装研究', 'study_mode': '全日制',
            'row': self.citation(row), 'exams': self.citation(row, EXAMS),
            'supplements': [{'field': 'politics', 'citation': self.citation(politics, POLITICS)},
                            {'field': 'study_mode', 'citation': self.citation(mode, MODE)}]}]}
        return args, self.tools.run('propose_source_candidates', args)

    def test_both_originals_have_real_hash_year_current_segments_and_exact_offsets(self):
        self.assertEqual(self.admission.rows, [])
        for kind, entry in self.entries.items():
            with self.subTest(kind=kind):
                self.assertEqual(entry['quality']['state'], 'accepted')
                self.assertTrue(entry['original_available'])
                self.assertEqual(entry['year'], 2027)
                self.assertEqual(entry['extraction']['extraction_status'], 'complete')
                self.assertGreater(entry['extraction']['segment_count'], 0)
        self.read('catalog', '082104')
        self.read('charter', MODE)

    def test_unpublished_school_original_becomes_temporary_computable_direction(self):
        _, result = self.proposal()
        self.assertEqual(result['accepted_count'], 1, result)
        self.assertEqual(self.admission.rows, [])
        report = self.tools.report()
        candidate = report['candidates'][0]
        self.assertTrue(candidate['id'].startswith('tp_'))
        self.assertEqual(candidate['candidate_origin'], 'official_original')
        self.assertEqual(candidate['validation']['state'], 'computable')
        self.assertFalse(candidate['eligibility_evidence_complete'])
        self.assertEqual([item['code'] for item in candidate['subjects']], ['101', '201', '302', '902'])
        self.assertEqual({item['entry_id'] for item in candidate['evidence_refs']},
                         {entry['id'] for entry in self.entries.values()})
        matching = self.tools.compare({})
        self.assertEqual(matching['summary']['input_count'], 1)
        self.assertEqual(matching['needs_confirmation'][0]['subject_changes']['count'], 0)
        self.tools.routes({})
        final = self.tools.report()
        self.assertEqual(final['coverage']['published_count'], 0)
        self.assertEqual(final['coverage']['research_original_count'], 1)
        self.assertFalse(final['coverage']['national_complete'])
        self.assertEqual(self.admission.lookups, [])

    def test_revoked_global_clause_invalidates_historical_algorithms_without_rewriting_audit(self):
        _, proposed = self.proposal()
        self.assertEqual(proposed['accepted_count'], 1, proposed)
        self.tools.ensure_algorithms()
        report = self.tools.report()
        saved = {**self.task, 'status': 'completed', 'report': deepcopy(report)}
        self.store.change_validity(self.entries['charter']['id'], 'revoked', '验收：全局学习方式声明已撤信')
        current = refresh_saved_candidates(saved, self.admission, self.store, self.data)
        self.assertEqual(current['status'], 'partial')
        self.assertEqual(current['report']['candidates'], [])
        self.assertFalse(current['report']['algorithm'])
        self.assertEqual(current['report']['research_candidates'][0]['validation']['state'], 'stale')
        self.assertEqual(saved['report'], report)
        self.assertEqual(saved['report']['research_candidates'][0]['validation']['state'], 'computable')

    def test_changed_citation_cannot_enter_pool_even_though_quote_occurs_in_original(self):
        args, result = self.proposal()
        self.assertEqual(result['accepted_count'], 1, result)
        args['drafts'][0]['exams']['char_start'] += 1
        invalid = self.tools.run('propose_source_candidates', args)
        self.assertEqual(invalid['accepted_count'], 0)
        self.assertTrue(invalid['rejected'])
        self.assertEqual(len(self.tools._research_pool), 1)

    def test_tampered_recorded_original_removes_candidate_and_old_algorithm(self):
        _, result = self.proposal()
        self.assertEqual(result['accepted_count'], 1, result)
        self.tools.ensure_algorithms()
        original = self.store._blob_path(self.entries['catalog']['sha256'])
        original.write_bytes(original.read_bytes() + b'\n<!-- altered after read -->')
        report = self.tools.report()
        self.assertEqual(report['candidates'], [])
        self.assertFalse(report['algorithm'])
        self.assertEqual(report['research_candidates'][0]['validation']['state'], 'stale')


if __name__ == '__main__':
    unittest.main()
