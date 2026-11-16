import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from collectors import catalog_semantic_audit as audit


class JxauSemanticReplayTests(unittest.TestCase):
    def test_port_requires_same_dependency_original_publication_and_approved_subset(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            source = b'%PDF-fixture'
            (root / 'source.pdf').write_bytes(source)
            proof = b'Independent geometry review fixture'
            (root / 'review.json').write_bytes(proof)
            publication = b'<html>Official admissions directory attachment</html>'
            (root / 'publication.html').write_bytes(publication)
            publication_sha = hashlib.sha256(publication).hexdigest()
            document = {'catalog_parser':'pdf_table_academic_professional_v1','local_path':'source.pdf',
                        'unit_id':'school','sha256':hashlib.sha256(source).hexdigest(),
                        'publication_page':{'sha256':publication_sha,'local_path':'publication.html'}}
            programs = [{'id':'verified','code':'095131'},{'id':'pending','code':'071002'}]
            previous = {'status':'approved','reviewer':'reviewer','reviewed_at':'2026-11-11T08:09:00+00:00',
                        'unit_id':'school','source_sha256':document['sha256'],'parser_sha256':'b'*64,
                        'programs_sha256':audit.semantic_programs_sha256(programs),
                        'approved_program_ids':['verified'],
                        'approved_programs_sha256':audit.semantic_programs_sha256(programs[:1]),
                        'review_evidence':{'local_path':'review.json','sha256':hashlib.sha256(proof).hexdigest()}}
            binding = root / 'binding.json'
            full_replay = hashlib.sha256(audit._encoded(sorted(programs,key=lambda row:row['id']))).hexdigest()
            binding.write_text(json.dumps({audit.JXAU_PARSER:{'reviewed_approval_sha256':hashlib.sha256(audit._encoded(previous)).hexdigest(),
                                          'parser_sha256':'c'*64,'publication_sha256':publication_sha,
                                          'full_replay_sha256':full_replay}}),encoding='utf-8')
            with patch.object(audit,'REPLAY_BINDINGS',binding), \
                 patch.object(audit,'semantic_parser_sha256',return_value='c'*64) as fingerprint:
                def verify(doc=document, rows=programs, approvals=(previous,)):
                    return audit.explain_semantic_audit(doc,rows,root,list(approvals))
                decision = verify()
                self.assertEqual(decision.get('approved_program_ids'),['verified'],decision)
                self.assertFalse(verify({**document,'unit_id':'other'})['approved'])
                self.assertFalse(verify({**document,'publication_page':{'sha256':'z'*64}})['approved'])
                self.assertFalse(verify(rows=[{**programs[0],'code':'095132'},programs[1]])['approved'])
                self.assertFalse(verify(rows=[{**programs[0],'evidence':['different quote']},programs[1]])['approved'])
                self.assertFalse(verify(approvals=[previous,previous])['approved'])
                self.assertFalse(verify(approvals=[{**previous,'approved_program_ids':['pending']}])['approved'])
                fingerprint.return_value = 'd'*64
                self.assertFalse(verify()['approved'])
                fingerprint.return_value = 'c'*64
                (root / 'publication.html').write_bytes(b'changed publication')
                self.assertFalse(verify()['approved'])
                (root / 'publication.html').write_bytes(publication)
                (root / 'review.json').write_bytes(b'changed review')
                self.assertFalse(verify()['approved'])
                (root / 'review.json').write_bytes(proof)
                (root / 'source.pdf').write_bytes(b'%PDF-wrong bytes')
                self.assertFalse(verify()['approved'])
