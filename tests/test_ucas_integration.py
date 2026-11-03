"""Training-unit identity and source restrictions survive the public service boundary."""
from copy import deepcopy
import os
import unittest

from collectors.catalog_batch import PARSERS,ingest_verified_batch,collect_school
from evidence import EvidenceIndex
from service_verification import SourceVerifier
from tests import test_catalog_ucas as ucas_fixtures


class UcasIntegrationTests(unittest.TestCase):
    def fixture(self):
        case=ucas_fixtures.UcasPublicTests();case.setUp();self.addCleanup(case.doCleanups)
        result=case.parse()
        return case,result

    def test_service_replays_full_training_unit_and_keeps_real_get_original(self):
        case,result=self.fixture();self.assertIn('ucas_public_v1',PARSERS)
        verifier=SourceVerifier(case.root)
        self.assertTrue(all(verifier.program(p,case.docs,case.unit) for p in result['programs']))
        self.assertTrue(verifier.unit_reconciles(case.unit,2027,result['programs'],case.docs))
        admissions={'metadata':{'reference_year':2027},'programs':result['programs'],'documents':list(case.docs.values())}
        proof=EvidenceIndex({'units':[case.unit]},admissions,case.root).program(result['programs'][0]['id'])
        claim=proof['claims'][0]
        self.assertEqual(claim['status'],'verified')
        leaf=next(ref for ref in claim['evidence'] if ref['document_id']=='leaf')
        self.assertEqual(leaf['detail_url'],case.leaf['url'])
        self.assertEqual(leaf['detail_sha256'],case.leaf['sha256'])

    def test_advisor_restrictions_and_location_cannot_be_forged_after_raw_replay(self):
        case,result=self.fixture();row=result['programs'][0];verifier=SourceVerifier(case.root)
        self.assertTrue(verifier.program(row,case.docs,case.unit))
        changed=deepcopy(row);changed['catalog_variants'][0]['notes']=['无报考限制']
        for value in [changed,{**row,'province':'北京'},{**row,'advisors':['伪造导师']},
                      {**row,'eligibility_evidence_complete':True},{**row,'requires_review':False}]:
            with self.subTest(changed=[k for k in value if value[k]!=row.get(k)]):
                self.assertFalse(verifier.program(value,case.docs,case.unit))

    def test_original_index_same_length_change_with_restored_mtime_revokes_warm_result(self):
        case,result=self.fixture();row=result['programs'][0];verifier=SourceVerifier(case.root)
        self.assertTrue(verifier.program(row,case.docs,case.unit))
        path=case.root/case.docs['index']['local_path'];info=path.stat();raw=path.read_bytes()
        changed=raw.replace(b'80002',b'80003');self.assertNotEqual(raw,changed)
        path.write_bytes(changed);os.utime(path,ns=(info.st_atime_ns,info.st_mtime_ns))
        self.assertFalse(verifier.program(row,case.docs,case.unit))

    def test_fetched_index_quote_requires_the_replayed_primary_binding(self):
        case,result=self.fixture()
        case.docs['index']={**case.docs['index'],'status':'fetched'}
        admissions={'metadata':{'reference_year':2027},'programs':result['programs'],'documents':list(case.docs.values())}
        index=EvidenceIndex({'units':[case.unit]},admissions,case.root)
        claim=index.program(result['programs'][0]['id'])['claims'][0]
        self.assertEqual(claim['status'],'verified')
        reference=next(r for r in claim['evidence'] if r['document_id']=='index')
        self.assertEqual(reference['verification'],'exact_quote')
        self.assertIn('数学与系统科学研究院',reference['quote'])
        # A saved index alone cannot grant itself a verified context role.
        detached=EvidenceIndex({'units':[case.unit]},{**admissions,'programs':[]},case.root)
        ref=next(r for r in result['programs'][0]['evidence'] if r['document_id']=='index')
        self.assertEqual(detached._ref(ref,result['programs'][0])['verification'],'invalid_source')

    def test_collector_accepts_saved_context_document_lists_and_reconciles_the_original_scope(self):
        case,parsed=self.fixture()
        contexts=[doc for doc in case.docs.values() if doc['kind']=='catalog_context']
        source={**case.leaf,'parser':'ucas_public_v1','context_documents':contexts,
                'source_path':str(case.root/case.leaf['local_path'])}
        result=collect_school({'year':2027,'sources':[source]},case.unit,case.root/'data/snapshots/formal',
                              project_root=case.root,offline=True)
        self.assertEqual(len(result['programs']),len(parsed['programs']))
        self.assertEqual(result['status']['status'],'verified',result['issues'])


if __name__=='__main__':unittest.main()
