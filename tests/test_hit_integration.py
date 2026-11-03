import os
from unittest.mock import patch
import unittest

from collectors.catalog_batch import PARSERS
from collectors.catalog_semantic_audit import PARSER_FILES
from service_verification import SourceVerifier
from tests import test_catalog_hit_public as fixtures


class HitIntegrationTests(unittest.TestCase):
    def fixture(self):
        case=fixtures.HitPublicTests();case.setUp();self.addCleanup(case.doCleanups)
        patcher=patch('collectors.catalog_hit_public.extract_hit_public_layout',return_value=case.layout)
        patcher.start();self.addCleanup(patcher.stop)
        verifier=SourceVerifier(case.root)
        pages=case.layout['raw_text'].rstrip('\f').split('\f')
        patcher=patch.object(verifier,'_source_pages',return_value=pages)
        patcher.start();self.addCleanup(patcher.stop)
        documents={case.document['id']:case.document,case.parent['id']:case.parent}
        return case,verifier,documents,case.parse()['programs']

    def test_embedded_public_pdf_replays_all_rows_and_cannot_bypass_the_semantic_gate(self):
        self.assertIn('pdf_table_hit_v1',PARSERS)
        self.assertEqual(PARSER_FILES.get('pdf_table_hit_v1'),'catalog_hit_public.py')
        case,verifier,documents,rows=self.fixture()
        self.assertTrue(all(verifier.program(row,documents,case.unit) for row in rows))
        self.assertTrue(verifier.unit_reconciles(case.unit,2027,rows,documents))
        self.assertFalse(verifier.unit_reconciles(case.unit,2027,rows[:-1],documents))
        self.assertFalse(verifier.program({**rows[1],'province':'黑龙江'},documents,case.unit))
        self.assertFalse(verifier.program({**rows[1],'degree_type':'学术学位'},documents,case.unit))

    def test_restored_mtime_original_edit_revokes_a_warm_embedded_pdf_result(self):
        case,verifier,documents,rows=self.fixture()
        self.assertTrue(verifier.program(rows[0],documents,case.unit))
        path=case.root/case.document['local_path'];info=path.stat();raw=path.read_bytes()
        path.write_bytes(raw.replace(b'original',b'modified'))
        os.utime(path,ns=(info.st_atime_ns,info.st_mtime_ns))
        self.assertFalse(verifier.program(rows[0],documents,case.unit))


if __name__=='__main__':unittest.main()
