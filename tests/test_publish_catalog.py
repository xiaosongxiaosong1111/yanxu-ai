import copy
import json
import os
import hashlib
from pathlib import Path
import unittest
from unittest.mock import patch

from catalog_index import CatalogIndex
from tests import test_service as fixtures


class PublishCatalogTests(unittest.TestCase):
    def test_revision_hash_streams_the_same_canonical_json_without_a_complete_serialized_copy(self):
        from collectors.publish_catalog import _checked_revision
        value={'unicode':'专业原文','scope':[2027,True,None,0.25]}
        expected=hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False,
            separators=(',',':')).encode()).hexdigest()[:24]
        with patch('collectors.publish_catalog.json.dumps',side_effect=MemoryError('complete catalogue serialization')):
            self.assertEqual(_checked_revision(value),expected)

    setUp=fixtures.ServiceTests.setUp
    write=fixtures.ServiceTests.write

    def publish(self):
        from collectors.publish_catalog import publish
        return publish(self.data)

    def annual(self):
        self.registry['metadata']['annual_scope']={'status':'verified','year':2027,
            'official_total':1,'confirmed_units':1,'not_listed_index_records':1}
        self.registry['units']=[{**row,'annual_year':2027,
            'annual_status':'confirmed' if row['id']=='123' else 'not_listed'} for row in self.registry['units']]
        self.write()
        annual={'metadata':{'year':2027,'complete':True,'annual_scope_status':'verified',
                'official_total':1,'confirmed_units':1,'not_listed_index_records':1,'unconfirmed_index_units':0},
            'units':[{'unit_id':row['id'],'annual_status':row['annual_status']} for row in self.registry['units']],
            'documents':[]}
        (self.data/'annual_registry_2027.json').write_text(json.dumps(annual),'utf-8')
        return annual

    def test_explicit_publication_replays_sources_preserves_json_and_never_collects(self):
        before={path:path.read_bytes() for path in self.data.rglob('*') if path.is_file()}
        with patch('public_sources.fetch_official_document',side_effect=AssertionError('network forbidden')):
            result=self.publish()
        self.assertEqual(result['program_count'],1)
        self.assertEqual(result['invalid_program_count'],0)
        self.assertIn('catalog_partial',result['invalid_flags'])
        self.assertIn('annual_scope_unverified',result['invalid_flags'])
        index=CatalogIndex(self.data/'runtime/catalog-index.sqlite')
        self.assertEqual(index.metadata()['revision'],result['revision'])
        self.assertFalse(index.metadata()['coverage']['catalog_complete'])
        self.assertEqual(index.get_program('a-1')['subjects'][-1]['code'],'408')
        self.assertTrue(all(path.read_bytes()==raw for path,raw in before.items()))
        self.assertFalse(self.publish()['updated'])

    def test_declared_verified_annual_scope_requires_raw_replay_before_touching_old_index(self):
        first=self.publish()
        self.annual()
        from collectors.publish_catalog import PublicationError
        with patch('collectors.publish_catalog.verify_annual_registry',return_value=False) as verify:
            with self.assertRaises(PublicationError): self.publish()
        self.assertEqual(verify.call_count,1)
        self.assertEqual(CatalogIndex(self.data/'runtime/catalog-index.sqlite').metadata()['revision'],first['revision'])

    def test_verified_annual_census_is_distinct_from_program_completeness(self):
        self.annual()
        with patch('collectors.publish_catalog.verify_annual_registry',return_value=True):
            result=self.publish()
        coverage=CatalogIndex(self.data/'runtime/catalog-index.sqlite').metadata()['coverage']
        self.assertEqual(coverage['index_total'],2)
        self.assertEqual(coverage['annual_total'],1)
        self.assertEqual(coverage['annual_not_listed_unit_ids'],['456'])
        self.assertEqual(coverage['missing_unit_ids'],['123'])
        self.assertEqual(coverage['missing_units_count'],1)
        self.assertFalse(coverage['catalog_complete'])
        self.assertEqual(result['annual_status'],'verified')
        self.assertEqual(result['verified_catalog_units'],0)

    def test_unverified_annual_census_cannot_publish_nationwide_complete_even_when_catalog_declares_it(self):
        fixtures.ServiceTests.verified(self)
        self.publish()
        coverage=CatalogIndex(self.data/'runtime/catalog-index.sqlite').metadata()['coverage']
        self.assertFalse(coverage['catalog_complete'])
        self.assertFalse(coverage['release_ready'])
        self.assertEqual(coverage['verified_unit_ids'],['123'])

    def test_missing_forged_counts_year_and_status_mismatch_never_publish_verified_annual_label(self):
        from collectors.publish_catalog import PublicationError
        annual=self.annual()
        path=self.data/'annual_registry_2027.json'
        for key,value in [('official_total',931),('year',2026),('complete',False)]:
            changed=copy.deepcopy(annual);changed['metadata'][key]=value
            path.write_text(json.dumps(changed),'utf-8')
            with self.subTest(key=key),patch('collectors.publish_catalog.verify_annual_registry',return_value=True):
                with self.assertRaises(PublicationError): self.publish()
        changed=copy.deepcopy(annual);changed['units'][0]['annual_status']='not_listed'
        path.write_text(json.dumps(changed),'utf-8')
        with patch('collectors.publish_catalog.verify_annual_registry',return_value=True):
            with self.assertRaises(PublicationError): self.publish()
        path.unlink()
        with self.assertRaises(PublicationError): self.publish()
        self.assertFalse((self.data/'runtime/catalog-index.sqlite').exists())

    def test_tampered_original_is_reported_without_raising_its_verified_flag(self):
        path=self.data/self.document['local_path']
        path.write_bytes(path.read_bytes().replace(b'408',b'409'))
        result=self.publish()
        self.assertEqual(result['invalid_program_count'],1)
        self.assertEqual(result['invalid_program_ids'],['a-1'])
        self.assertIn('invalid_program_originals',result['invalid_flags'])

    def test_changed_verification_result_republishes_even_when_original_bytes_are_unchanged(self):
        first=self.publish()
        with patch('service_verification.SourceVerifier.program',return_value=False):
            second=self.publish()
        self.assertTrue(second['updated'])
        self.assertNotEqual(first['revision'],second['revision'])
        self.assertEqual(CatalogIndex(self.data/'runtime/catalog-index.sqlite').metadata()['coverage']['invalid_program_count'],1)

    def test_source_publication_format_change_updates_the_client_revision_with_unchanged_originals(self):
        from catalog_index import SCHEMA_VERSION
        first = self.publish()
        with patch('collectors.publish_catalog.SCHEMA_VERSION', SCHEMA_VERSION + 1):
            second = self.publish()
        self.assertTrue(second['updated'])
        self.assertNotEqual(first['revision'], second['revision'])

    def pdf_candidate(self):
        import hashlib
        root=self.data.parent
        (root/'collectors').mkdir(exist_ok=True)
        (root/'collectors/catalog_pdf_tables.py').write_text('# approved adapter fixture\n','utf-8')
        path=self.data/'snapshots/semantic.pdf';path.write_bytes(b'%PDF-1.7 saved semantic fixture')
        document={**self.document,'catalog_parser':'pdf_table_v1',
            'local_path':'data/snapshots/semantic.pdf','sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        self.admissions['documents'][0]=document
        self.write()
        rows=[copy.deepcopy(self.program),{**copy.deepcopy(self.program),'id':'another-source-row'}]
        return document,rows

    def approve_pdf(self,document,rows):
        from collectors.catalog_semantic_audit import semantic_parser_sha256,semantic_programs_sha256
        approval={'status':'approved','source_sha256':document['sha256'],
            'parser_sha256':semantic_parser_sha256(document,self.data.parent),
            'programs_sha256':semantic_programs_sha256(rows),'reviewer':'root independent fixture review',
            'reviewed_at':'2026-10-30T00:00:00+00:00'}
        path=self.data/'catalog_semantic_audits.json'
        path.write_text(json.dumps({'audits':[approval]}),'utf-8')
        return path

    def test_unapproved_pdf_table_cannot_be_directly_published_over_the_old_index(self):
        from collectors.publish_catalog import PublicationError
        first=self.publish()
        document,rows=self.pdf_candidate()
        with patch('service_verification.SourceVerifier._source_programs',return_value={'programs':rows}):
            with self.assertRaises(PublicationError):self.publish()
        self.assertEqual(CatalogIndex(self.data/'runtime/catalog-index.sqlite').metadata()['revision'],first['revision'])

    def test_pdf_approval_replays_the_whole_source_and_revocation_with_restored_mtime_preserves_old_index(self):
        from collectors.publish_catalog import PublicationError
        from collectors.catalog_semantic_audit import check_semantic_audit
        document,rows=self.pdf_candidate()
        path=self.approve_pdf(document,rows)
        with patch('service_verification.SourceVerifier._source_programs',return_value={'programs':rows}):
            with patch('collectors.publish_catalog.check_semantic_audit',wraps=check_semantic_audit) as check:
                first=self.publish()
            self.assertEqual(len(check.call_args.args[1]),2)
            self.assertEqual(check.call_args.args[2],self.data.parent.resolve())
            self.assertEqual(first['pdf_semantic_sources'],1)
            path.write_text(path.read_text('utf-8')+' ','utf-8')
            second=self.publish()
            self.assertNotEqual(first['revision'],second['revision'])
            stamp=path.stat()
            path.write_bytes(path.read_bytes().replace(b'approved',b'revoked!'))
            os.utime(path,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
            with self.assertRaises(PublicationError):self.publish()
        self.assertEqual(CatalogIndex(self.data/'runtime/catalog-index.sqlite').metadata()['revision'],second['revision'])

    def test_unused_pdf_table_and_legacy_row_sources_are_not_blocked_by_the_new_gate(self):
        self.admissions['documents'].append({**self.document,'id':'unused','catalog_parser':'pdf_table_v2'})
        self.write()
        result=self.publish()
        self.assertEqual(result['pdf_semantic_sources'],0)

    def test_revocation_during_final_program_replay_cannot_publish_a_stale_approval(self):
        from collectors.publish_catalog import PublicationError
        first=self.publish()
        document,rows=self.pdf_candidate()
        path=self.approve_pdf(document,rows)
        def revoke(*_args):
            path.write_bytes(path.read_bytes().replace(b'approved',b'revoked!'))
            return True
        with patch('service_verification.SourceVerifier._source_programs',return_value={'programs':rows}):
            with patch('service_verification.SourceVerifier.program',side_effect=revoke):
                with self.assertRaises(PublicationError):self.publish()
        self.assertEqual(CatalogIndex(self.data/'runtime/catalog-index.sqlite').metadata()['revision'],first['revision'])

    def test_cli_reports_json_and_clear_failure_without_traceback(self):
        from collectors.publish_catalog import main
        with patch('builtins.print') as output:
            self.assertEqual(main(['--data-dir',str(self.data)]),0)
        self.assertEqual(json.loads(output.call_args.args[0])['program_count'],1)
        with patch('builtins.print') as output:
            self.assertEqual(main(['--data-dir',str(self.data/'missing')]),1)
        self.assertIn('error',json.loads(output.call_args.args[0]))


if __name__=='__main__': unittest.main()
