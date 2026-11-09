"""Official discovery must retain scope, cancellation and source lifetimes."""
import hashlib
from pathlib import Path
import tempfile
import threading
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from grad_engine import validate_profile
from research_store import ResearchError
from research_tools import ResearchTools
import research_official_tools as official
from tests.test_research_service import FakeAdmission


UNIT = {'id': '123', 'name': '示例大学', 'official_url': 'https://www.example.edu.cn/'}
TITLE = '示例大学2027年硕士研究生招生章程'
BODY = ('<title>'+TITLE+'</title><main><h1>'+TITLE+'</h1><p>一、报考条件：申请人须符合硕士研究生招生规定。'
        '二、招生计划及报名办法：考试与复试要求按本校正式章程执行。</p>'+ '<p>各专业报考要求见原文。</p>'*50+'</main>').encode()


def document(url, raw):
    return {'url': url, 'raw': raw, 'sha256': hashlib.sha256(raw).hexdigest(),
            'content_type': 'text/html;charset=utf-8', 'checked_at': '2026-11-09T00:00:00Z',
            'network_verified': True, 'navigation_only': True}


class ResearchOfficialToolsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.data = Path(temp.name)
        self.admission = FakeAdmission()
        self.admission.units = [UNIT]
        self.task = {'id': 'r_'+'1'*32, 'query': UNIT['name'], 'follow_ups': [],
                     'profile': validate_profile({}), 'unit_ids': ['123'],
                     'reference_year': 2027, 'target_year': 2028}

    def tools(self, fetcher, event=None):
        return ResearchTools(self.admission, None, self.task, self.data,
                             navigation_fetcher=fetcher, stop_event=event)

    def test_relevant_navigation_at_position_21_survives_before_limit(self):
        tools = self.tools(lambda *a, **k: None)
        links = [{'url': UNIT['official_url']+'ordinary/'+str(n), 'label': '普通导航'} for n in range(20)]
        links.append({'url': UNIT['official_url']+'catalog', 'label': '2027年硕士招生专业目录'})
        result = tools._link_sources(links, UNIT)
        self.assertEqual(len(result), 20)
        self.assertEqual(result[0]['url'], UNIT['official_url']+'catalog')

    def test_cancelled_read_does_not_save_a_new_original_or_completed_discovery(self):
        stop = threading.Event()
        def fetch(url, unit, **kwargs):
            stop.set()
            return document(url, BODY)
        tools = self.tools(fetch, stop)
        with self.assertRaises(ResearchError):
            tools.run('discover_official_entry', {'unit_id': '123', 'kind': 'charter'})
        self.assertFalse(any(row.get('knowledge_saved') for row in tools.sources.values()))
        self.assertNotEqual(tools._official_discovery.get('123', {}).get('stage'), 'body_read')

    def test_revoked_new_authority_invalidates_discovery_and_model_source_view(self):
        graduate = 'https://grad.other.edu.cn/'
        home = ('<title>示例大学</title><h1>示例大学</h1><a href="'+graduate+'">研究生招生</a>').encode()
        tools = self.tools(lambda url, unit, **kw: document(url, home if url == UNIT['official_url'] else BODY))
        result = tools.run('discover_official_entry', {'unit_id': '123', 'kind': 'charter'})
        self.assertEqual(result['stage'], 'body_read')
        for authority in tools._official_authority.bindings(UNIT):
            tools._official_authority.revoke(authority['id'])
        report = tools.report()
        current = report['official_discovery'][0]
        self.assertNotEqual(current['stage'], 'body_read')
        self.assertNotIn(graduate, [row['url'] for row in current['document_sources']])
        self.assertTrue(all(row.get('knowledge_current') is False for row in report['evidence']
                            if row['url'] == graduate and row.get('knowledge_id')))
        again = tools.report()
        self.assertNotEqual(again['official_discovery'][0]['stage'], 'body_read')
        self.assertTrue(all(row.get('usable') is False for row in again['evidence']
                            if row['url'] == graduate and row.get('knowledge_id')))

    def discovered(self):
        graduate = 'https://grad.other.edu.cn/'
        home = ('<title>示例大学</title><h1>示例大学</h1><a href="'+graduate+'">研究生招生</a>').encode()
        tools = self.tools(lambda url, unit, **kw: document(url, home if url == UNIT['official_url'] else BODY))
        tools.run('discover_official_entry', {'unit_id': '123', 'kind': 'charter'})
        return tools, tools._official_authority.bindings(UNIT)[0], graduate

    def test_expired_or_tampered_proof_stops_current_discovery_without_deleting_original(self):
        tools, grant, graduate = self.discovered()
        saved = tools.report()
        proof = tools._official_authority._load_proof(grant['target'])
        with patch.object(tools._official_authority, 'clock',
                          return_value=datetime.now(timezone.utc)+timedelta(days=31)):
            current = tools.report()
        self.assertNotEqual(current['official_discovery'][0]['stage'], 'body_read')
        self.assertEqual(saved['official_discovery'][0]['stage'], 'body_read')
        self.assertEqual(tools._official_authority._load_proof(grant['target'])['raw'], proof['raw'])

    def test_tampered_ownership_edge_invalidates_the_document_view(self):
        tools, grant, graduate = self.discovered()
        path = self.data / grant['proofs'][0]['local_path']
        path.write_bytes(b'changed ownership edge')
        current = tools.report()
        self.assertNotEqual(current['official_discovery'][0]['stage'], 'body_read')
        self.assertTrue(any(row.get('authority_current') is False for row in current['evidence']
                            if row['url'] == graduate))

    def test_saved_task_projects_revocation_without_mutating_the_audit_or_fetching(self):
        tools, grant, graduate = self.discovered()
        saved = {'status': 'completed', 'report': tools.report()}
        audit = deepcopy(saved)
        tools._official_authority.revoke(grant['id'])
        with patch('research_official_tools.fetch_navigation_resource', side_effect=AssertionError('no network')):
            current = official.refresh_saved_discovery(saved, self.admission, self.data)
        self.assertEqual(saved, audit)
        self.assertEqual(current['status'], 'partial')
        self.assertNotEqual(current['report']['official_discovery'][0]['stage'], 'body_read')
        self.assertTrue(all(row.get('knowledge_current') is False for row in current['report']['evidence']
                            if row['url'] == graduate and row.get('knowledge_id')))

    def test_valid_saved_discovery_keeps_completed_status_and_inheritance_rechecks_authority(self):
        tools, grant, graduate = self.discovered()
        saved = {'status': 'completed', 'report': tools.report()}
        current = official.refresh_saved_discovery(saved, self.admission, self.data)
        self.assertEqual(current['status'], 'completed')
        self.assertEqual(current['report']['official_discovery'][0]['stage'], 'body_read')
        tools._official_authority.revoke(grant['id'])
        later = self.tools(lambda *a, **k: self.fail('inheritance must not request pages'))
        later.sources = {row['id']: deepcopy(row) for row in saved['report']['evidence']}
        official.inherit(later, saved['report'])
        self.assertNotEqual(later._official_discovery['123']['stage'], 'body_read')
        self.assertTrue(all(row.get('knowledge_current') is False for row in later.sources.values()
                            if row['url'] == graduate and row.get('knowledge_id')))

    def test_cancel_during_extraction_is_checked_before_source_save(self):
        stop = threading.Event()
        tools = self.tools(lambda url, unit, **kw: document(url, BODY), stop)
        extract = official.extract_saved_document
        def cancelled_extract(*args, **kwargs):
            result = extract(*args, **kwargs)
            stop.set()
            return result
        with patch('research_official_tools.extract_saved_document', side_effect=cancelled_extract):
            with self.assertRaises(ResearchError):
                tools.run('discover_official_entry', {'unit_id': '123', 'kind': 'charter'})
        self.assertFalse(any(row.get('knowledge_saved') for row in tools.sources.values()))
        self.assertNotIn('123', tools._official_discovery)

    def test_cancel_during_pdf_read_is_checked_before_pdf_adoption(self):
        stop = threading.Event()
        home = '<title>示例大学</title><a href="/catalog.pdf">2027硕士招生章程</a>'.encode()
        tools = self.tools(lambda url, unit, **kw: document(url, home), stop)
        def pdf(*args, **kwargs):
            stop.set()
            return document(args[0], b'%PDF-1.7 not extracted after cancellation')
        tools.source_fetcher = Mock(side_effect=pdf)
        with self.assertRaises(ResearchError):
            tools.run('discover_official_entry', {'unit_id': '123', 'kind': 'charter'})
        tools.source_fetcher.assert_called_once()
        self.assertFalse(any(row.get('knowledge_saved') for row in tools.sources.values()
                             if row['url'].endswith('.pdf')))

    def test_cached_chsi_article_uses_visible_article_header_without_chsi_requests(self):
        seen = []
        def fetch(url, unit, **kwargs):
            seen.append(url)
            self.assertNotIn('yz.chsi.com.cn', url)
            return document(url, '<title>示例大学</title><h1>示例大学</h1>'.encode())
        tools = self.tools(fetch)
        url = 'https://yz.chsi.com.cn/sch/viewZszc--infoId-1,schId-123.dhtml'
        raw = ('<title>示例大学_院校信息_中国研究生招生信息网</title>'
               '<h2 class="yxk-big-title">'+TITLE+'</h2><div><p>报考人员须符合硕士招生规定。</p>'
               '<p>学校公布报名办法及招生计划。</p>'*12+'</div>').encode()
        tools._saved_declarations = [{**document(url, raw), 'unit_id': '123'}]
        result = tools.run('discover_official_entry', {'unit_id': '123', 'kind': 'charter'})
        self.assertEqual(result['stage'], 'body_read')
        self.assertEqual(result['document_sources'][0]['url'], url)
        self.assertTrue(all('yz.chsi.com.cn' not in item for item in seen))

    def test_cached_chsi_list_or_hidden_article_title_never_becomes_requested_document(self):
        for path, header in [('schoolInfo--schId-123.dhtml', '<a href="/article">'+TITLE+'</a>'),
                             ('viewZszc--infoId-1,schId-123.dhtml', '<h2 class="yxk-big-title" hidden>'+TITLE+'</h2>')]:
            with self.subTest(path=path):
                tools = self.tools(lambda url, unit, **kw: document(url, '<title>示例大学</title>'.encode()))
                url = 'https://yz.chsi.com.cn/sch/'+path
                raw = ('<title>示例大学_院校信息_中国研究生招生信息网</title>'+header+
                       '<p>申请人须符合硕士招生规定。</p>'*15).encode()
                tools._saved_declarations = [{**document(url, raw), 'unit_id': '123', 'title': TITLE, 'year': 2027}]
                result = tools.run('discover_official_entry', {'unit_id': '123', 'kind': 'charter'})
                self.assertEqual(result['document_sources'], [])
                self.assertNotEqual(result['stage'], 'body_read')


if __name__ == '__main__':
    unittest.main()
