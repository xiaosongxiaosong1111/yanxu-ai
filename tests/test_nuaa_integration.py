import unittest
from evidence import EvidenceIndex
from tests import test_catalog_nuaa_public as fixtures


class NuaaIntegrationTests(unittest.TestCase):
    def test_named_json_literal_source_opens_the_verified_human_directory_and_preserves_raw_fields(self):
        case=fixtures.NuaaPublicTests();case.setUp();self.addCleanup(case.doCleanups)
        rows=case.parse()['programs']
        admissions={'metadata':{'reference_year':2027},'programs':rows,'documents':list(case.docs.values())}
        index=EvidenceIndex({'units':[case.unit]},admissions,case.root)
        sources=index.sources()['sources'];source=next(s for s in sources if s['id']=='data')
        self.assertEqual(source['detail_url'],case.entry['url'])
        self.assertEqual(source['request_url'],case.document['url'])
        self.assertEqual(source['original_format'],'structured_fields')
        proof=index.program(rows[0]['id'])
        self.assertEqual(proof['claims'][0]['status'],'verified')
        ref=next(item for item in proof['claims'][1]['evidence'] if item.get('document_id')=='data')
        self.assertEqual(ref['verification'],'structured_fields')
        self.assertTrue(ref['field_path'].startswith('mainData.'))
        self.assertEqual(ref['detail_url'],case.entry['url'])


if __name__=='__main__':unittest.main()
