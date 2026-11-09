"""New discovery sampling and HTTP measurements do not manufacture website success."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
import io
from types import SimpleNamespace
from unittest.mock import patch

from tools import audit_official_entries_live as audit
from tools.audit_official_entries_live import PublicInputs, phase_metrics, public_declarations, select_sample, strata


def unit(index, province, registered, school):
    return {'id': str(index), 'unit_code': str(10000+index), 'name': ('示例大学' if school else '示例研究所')+str(index),
            'province': province, 'official_url': 'https://example.edu.cn/' if registered else None}


class SamplingTests(unittest.TestCase):
    def test_academies_of_sciences_are_not_mislabelled_as_colleges(self):
        self.assertEqual(strata({'name':'广东省社会科学院','official_url':None}),'unregistered_research')
        self.assertEqual(strata({'name':'中国科学院大学','official_url':None}),'unregistered_school')

    def setUp(self):
        self.units = [unit(index+1, ['甲','乙','丙','丁'][index%4], bool(index%2), bool((index//2)%2))
                      for index in range(32)]
        self.annual = [{'unit_id': row['id'], 'unit_code': row['unit_code'], 'annual_status': 'confirmed', 'year':2027}
                       for row in self.units]

    def test_sample_is_deterministic_independent_of_input_order_and_does_not_mutate_inputs(self):
        before = deepcopy(self.units)
        a = select_sample(self.units, self.annual, {'1','2'}, 16, 'fixed')
        b = select_sample(list(reversed(self.units)), self.annual, {'2','1'}, 16, 'fixed')
        self.assertEqual(a,b);self.assertEqual(before,self.units)
        self.assertEqual(len({row['id'] for row in a}),16)
        self.assertFalse({'1','2'} & {row['id'] for row in a})

    def test_balances_registration_and_lexical_institution_groups_and_covers_provinces(self):
        found = select_sample(self.units,self.annual,set(),16,'fixed')
        self.assertEqual({row['province'] for row in found}, {'甲','乙','丙','丁'})
        self.assertEqual({group:sum(strata(row)==group for row in found) for group in {strata(row) for row in found}},
                         {'registered_school':4,'registered_research':4,'unregistered_school':4,'unregistered_research':4})

    def test_only_confirmed_requested_year_and_unique_annual_codes_are_eligible(self):
        self.annual[0]['annual_status']='unknown';self.annual[1]['year']=2026
        self.units.append({**self.units[2],'id':'duplicate'})
        found=select_sample(self.units,self.annual,set(),20,'fixed')
        self.assertFalse({'1','2','duplicate'} & {row['id'] for row in found})
        self.assertEqual(len({row['unit_code'] for row in found}),len(found))

    def test_small_strata_redistributes_capacity_but_insufficient_population_fails(self):
        found=select_sample(self.units[:10],self.annual,set(),10,'fixed')
        self.assertEqual(len(found),10)
        with self.assertRaises(ValueError):select_sample(self.units[:10],self.annual,set(),11,'fixed')


class PhaseMetricTests(unittest.TestCase):
    def setUp(self):
        self.raw=b'<title>example university</title>'
        self.source={'url':'https://example.edu.cn/','sha256':hashlib.sha256(self.raw).hexdigest(),
                     'status':'official_original_read','source_role':'official_entry'}
        self.record={**self.source,'case_id':'case1','status':200,'complete':True,'checked_at':'2026-11-09',
                     'integrity_verified':True}
        self.row={'case_id':'case1','metrics':{'entry_verified':True,'requested_body_read':False,'source_evidence_complete':False},
                  'result':{'entry_sources':[self.source],'document_sources':[],'navigation_sources':[]},'usage':{}}

    def test_registered_metadata_does_not_mean_an_http_read_succeeded(self):
        value=phase_metrics(self.row,[])
        self.assertTrue(value['known_independent_entry'])
        self.assertFalse(value['independent_website_http_read'])

    def test_live_read_needs_matching_sha_complete_200_and_verified_current_source(self):
        self.assertTrue(phase_metrics(self.row,[self.record])['independent_website_http_read'])
        for changed in ({'status':403},{'complete':False},{'sha256':'a'*64},{'integrity_verified':False}):
            with self.subTest(changed=changed):
                self.assertFalse(phase_metrics(self.row,[{**self.record,**changed}])['independent_website_http_read'])
        self.row['result']['entry_sources'][0]['authority_current']=False
        self.assertFalse(phase_metrics(self.row,[self.record])['independent_website_http_read'])

    def test_cached_official_account_and_zero_request_seed_gap_are_separate(self):
        self.row['result']['entry_sources']=[{**self.source,'url':'https://yz.chsi.com.cn/sch/viewZszc--schId-123.dhtml',
                                            'source_role':'official_account'}]
        value=phase_metrics(self.row,[])
        self.assertTrue(value['official_account_known']);self.assertFalse(value['known_independent_entry'])
        self.assertFalse(value['independent_website_http_read'])
        self.row['result']['entry_sources']=[];self.row['metrics']['entry_verified']=False
        value=phase_metrics(self.row,[])
        self.assertTrue(value['initial_seed_missing']);self.assertEqual(value['public_http_requests'],0)

    def test_request_count_is_unit_scoped_not_the_shared_transport_total(self):
        value=phase_metrics(self.row,[self.record,{**self.record,'case_id':'case2','url':'https://other.edu.cn/'}])
        self.assertEqual(value['public_http_requests'],1)

    def test_replay_has_zero_http_calls_and_keeps_recordings_and_transport_calls_separate(self):
        value=phase_metrics(self.row,[self.record],mode='replay',replay_transport_calls=2)
        self.assertEqual(value['public_http_requests'],0)
        self.assertEqual(value['recorded_response_count'],1)
        self.assertEqual(value['replay_transport_calls'],2)


class PublicInputTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.data=self.root/'data';self.data.mkdir()
        self.path='data/snapshots/charter.html'
        file=self.root/self.path;file.parent.mkdir();file.write_bytes(b'<h1>school</h1>')
        self.digest=hashlib.sha256(file.read_bytes()).hexdigest()
        self.inputs=PublicInputs(self.root,{'packages':[{'files':[{'path':self.path,
                    'sha256':self.digest,'size':file.stat().st_size}]}]})

    def test_public_input_cannot_read_runtime_or_an_unlisted_or_tampered_file(self):
        self.assertTrue(self.inputs.read(self.path))
        for name in ('data/runtime/settings.sqlite','../secret','data/unlisted.html'):
            with self.assertRaises(ValueError):self.inputs.read(name)
        (self.root/self.path).write_bytes(b'tampered')
        with self.assertRaises(ValueError):self.inputs.read(self.path)

    def test_saved_seed_comes_only_from_this_units_public_charter_with_a_matching_digest(self):
        row={'unit_id':'123','unit_code':'10001','evidence':[{'document_id':'good'},{'document_id':'other'}]}
        documents=[{'id':'good','url':'https://yz.chsi.com.cn/sch/viewZszc--schId-123.dhtml',
                    'local_path':self.path,'sha256':self.digest,'content_type':'text/html'},
                   {'id':'other','url':'https://yz.chsi.com.cn/sch/viewZszc--schId-999.dhtml',
                    'local_path':self.path,'sha256':self.digest}]
        seeds,issues=public_declarations({'id':'123','unit_code':'10001'},row,documents,self.inputs)
        self.assertEqual(len(seeds),1);self.assertEqual(seeds[0]['evidence_origin'],'public_bundle')
        self.assertEqual(Path(seeds[0]['raw_path']).read_bytes(),b'<h1>school</h1>')
        self.assertEqual(issues,[])
        documents[0]['sha256']='a'*64
        seeds,issues=public_declarations({'id':'123','unit_code':'10001'},row,documents,self.inputs)
        self.assertEqual(seeds,[]);self.assertTrue(issues)

    def test_windows_case_variants_of_runtime_and_bundles_are_rejected_before_read(self):
        for folder in ('Runtime','RUNTIME','bUnDlEs'):
            with self.subTest(folder=folder):
                name='data/'+folder+'/probe.bin';path=self.root/name
                path.parent.mkdir(exist_ok=True);path.write_bytes(b'probe')
                manifest={'packages':[{'files':[{'path':name,'size':5,'sha256':hashlib.sha256(b'probe').hexdigest()}]}]}
                with self.assertRaises(ValueError):PublicInputs(self.root,manifest).read(name)


class ReplayValidationTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)/'recordings';self.root.mkdir()
        (self.root/'sample.bin').write_bytes(b'original')
        self.record={'url':'https://yz.chsi.com.cn/sch/viewZszc--schId-123.dhtml','raw_path':'sample.bin',
                     'sha256':hashlib.sha256(b'original').hexdigest(),'status':200,'complete':True}

    def test_every_record_is_checked_even_if_cached_account_or_incomplete_and_never_requested(self):
        outside=self.root.parent/'outside-probe.bin';outside.write_bytes(b'probe')
        for fields in ({},{'status':0,'complete':False,'error_code':'timeout'}):
            with self.subTest(fields=fields):
                record={**self.record,**fields,'raw_path':'../outside-probe.bin','sha256':hashlib.sha256(b'probe').hexdigest()}
                with self.assertRaises(ValueError):audit.validate_replay_records([record],self.root)

    def test_size_sha_and_path_are_checked_without_an_unbounded_read(self):
        for fields in ({'sha256':'a'*64},{'response_bytes':999},{'raw_path':'/absolute'},
                       {'raw_path':'child\\escape.bin'},{'raw_path':'C:/private'}):
            with self.subTest(fields=fields),self.assertRaises(ValueError):
                audit.validate_replay_records([{**self.record,**fields}],self.root)
        with patch.object(audit,'MAX_REPLAY_BYTES',4),self.assertRaises(ValueError):
            audit.validate_replay_records([self.record],self.root)

    def test_legacy_missing_size_still_checks_bounds_sha_and_root_or_child_links(self):
        audit.validate_replay_records([self.record],self.root)
        for linked in (self.root,self.root/'sample.bin'):
            with self.subTest(linked=linked),patch.object(audit,'_is_link',side_effect=lambda path:path==linked),\
                 self.assertRaises(ValueError):audit.validate_replay_records([self.record],self.root)


class SourceManifestTests(unittest.TestCase):
    def test_shared_wire_transport_source_is_in_the_measured_version(self):
        self.assertEqual(audit._source_hashes().get('collectors/discovery.py'),
                         hashlib.sha256((audit.ROOT/'collectors/discovery.py').read_bytes()).hexdigest())


class CliTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);bundle=self.root/'data/bundles';bundle.mkdir(parents=True)
        (bundle/'manifest.json').write_text('{}','utf-8')
        self.case={'case_id':'round2-123','unit':unit(123,'甲',False,True),'saved_declarations':[],
                   'sampling_stratum':'unregistered_school','seed_issues':[]}
        self.calls=[];self.repeat=1

    def fake_case(self,case,transport,**options):
        self.calls.append(options)
        raw=b'<h1>example</h1>'
        if not options.get('replay'):
            for _ in range(self.repeat):
                transport.save({'url':'https://example.edu.cn/','status':200,'complete':True,
                                'checked_at':'2026-11-09','headers':{'Content-Type':'text/html'}},raw)
        else:
            for _ in range(self.repeat):
                resource=transport('https://example.edu.cn/',None,1)
                self.assertEqual(resource.read(),raw);resource.close()
        metrics={key:False for key in audit.base.METRICS}
        metrics.update(read_urls=[],stage='no_lead',error_codes=[])
        return {'case_id':case['case_id'],'unit_id':'123','name':'示例大学123','metrics':metrics,
                'result':{'entry_sources':[],'document_sources':[],'navigation_sources':[]},'usage':{}}

    def invoke(self,out,*args):
        with patch.object(audit,'PublicInputs',return_value=SimpleNamespace(root=self.root)),\
             patch.object(audit,'build_cases',return_value=([self.case],{'old'})),\
             patch.object(audit.base,'evaluate_case',side_effect=self.fake_case),redirect_stdout(io.StringIO()):
            return audit.main(['--out',str(out),*args])

    def test_default_mode_only_writes_the_sample_and_does_not_construct_a_network_transport(self):
        out=self.root/'sample'
        with patch.object(audit.base,'RecordingTransport',side_effect=AssertionError('network forbidden')):
            self.assertEqual(self.invoke(out),0)
        self.assertFalse(self.calls);self.assertFalse((out/'report.json').exists())

    def test_live_and_replay_have_case_scoped_recordings_and_fresh_read_metrics(self):
        live=self.root/'live';replay=self.root/'replay'
        self.assertEqual(self.invoke(live,'--live'),0)
        self.assertEqual(self.invoke(replay,'--replay',str(live)),0)
        records=json.loads((live/'recordings.json').read_text('utf-8'))
        self.assertEqual(records[0]['case_id'],'round2-123')
        self.assertEqual(records[0]['raw_path'],'round2-123/'+hashlib.sha256(b'<h1>example</h1>').hexdigest()+'.bin')
        for directory in (live,replay):
            report=json.loads((directory/'report.json').read_text('utf-8'))
            self.assertFalse(report['source_changed_during_run'])
            self.assertEqual(report['summary']['all']['public_http_requests'],1 if directory==live else 0)
            self.assertEqual(report['paid_calls'],0)

    def test_duplicate_url_recordings_do_not_turn_replay_responses_into_new_http_requests(self):
        self.repeat=2;live=self.root/'duplicate-live';replay=self.root/'duplicate-replay'
        self.invoke(live,'--live');self.invoke(replay,'--replay',str(live))
        a=json.loads((live/'report.json').read_text('utf-8'))['summary']['all']
        b=json.loads((replay/'report.json').read_text('utf-8'))['summary']['all']
        self.assertEqual((a['public_http_requests'],a['recorded_response_count'],a['replay_transport_calls']),(2,2,0))
        self.assertEqual((b['public_http_requests'],b['recorded_response_count'],b['replay_transport_calls']),(0,1,2))

    def test_invalid_pacing_and_overwriting_replay_are_rejected(self):
        with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):self.invoke(self.root/'bad','--interval','0.1')
            with self.assertRaises(SystemExit):self.invoke(self.root/'same','--replay',str(self.root/'same'))


if __name__=='__main__':unittest.main()
