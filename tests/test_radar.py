from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from collectors.catalogs import parse_nankai_raw, parse_zzu_html
from radar import RadarService


HTML = '''<html><h1>2027年硕士招生专业目录</h1><table id="zsml-table"><tbody>
<tr class="zsml-talbe-yx-zy"><td>085404（专业学位）计算机技术（全国统考）</td><td rowspan="2">30</td><td rowspan="2">①101思想政治理论②204英语（二）③302数学（二）④408计算机学科专业基础</td></tr>
<tr class="zsml-talbe-yx-zy-yjfx"><td>01（全日制）智能软件</td></tr>
</tbody></table></html>'''
UNIT = {'id': 'u', 'name': '测试大学', 'official_url': 'https://test.edu.cn'}
URL = 'https://test.edu.cn/zsml/sszsml/zsmlYjfx/2027/035/085404?ksfs=21'


class RadarTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        raw = HTML.encode()
        (self.root / 'source.html').write_bytes(raw)
        self.document = {'id': 'd', 'url': URL, 'unit_id': 'u', 'year': 2027, 'kind': 'catalog',
                         'status': 'parsed', 'local_path': 'source.html', 'content_type': 'text/html',
                         'sha256': hashlib.sha256(raw).hexdigest()}
        self.programs = parse_zzu_html(HTML, UNIT, '035 计算机学院', 2027, self.document)['programs']
        self.ids = [p['id'] for p in self.programs]
        self.sources = {'programs': self.programs, 'documents': [self.document], 'units': [UNIT]}
        self.response = {'raw': raw, 'content_type': 'text/html', 'url': URL}
        self.fetches = 0
        def fetch(url, unit):
            self.fetches += 1
            if isinstance(self.response, Exception): raise self.response
            return self.response
        self.fetch = fetch
        self.radar = RadarService(self.root, fetcher=fetch, clock=lambda: '2026-10-28T00:00:00+00:00')

    def watch(self):
        return self.radar.watch(self.ids, **self.sources)

    def change(self, html):
        self.response = {**self.response, 'raw': html.encode()}

    def test_first_subscription_builds_verified_baseline_without_events_or_personal_data(self):
        original = deepcopy(self.sources)
        self.watch()
        state = self.radar.check(**self.sources)
        self.assertEqual(state['baseline_count'], 1)
        self.assertEqual(state['watch_count'], 1)
        self.assertEqual(state['events'], [])
        self.assertEqual(state['unread_count'], 0)
        self.assertEqual(self.sources, original)
        stored = json.loads((self.root / 'runtime/radar.json').read_text('utf8'))
        self.assertNotIn('profile', stored)
        self.assertEqual(len(list((self.root / 'runtime/radar_snapshots').glob('*'))), 1)

    def test_changed_subject_has_both_raw_evidence_versions_and_is_not_repeated(self):
        self.watch()
        self.change(HTML.replace('408计算机学科专业基础', '901软件基础'))
        state = self.radar.check(**self.sources)
        self.assertEqual(state['check_state'], 'complete')
        events = [e for e in state['events'] if e['type'] == 'program_changed']
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event['program_ids'], self.ids)
        self.assertIn('subjects', event['changes'])
        self.assertEqual(event['changes']['subjects']['before'][-1]['code'], '408')
        self.assertEqual(event['changes']['subjects']['after'][-1]['code'], '901')
        for side in ('before', 'after'):
            evidence = event['evidence'][side]
            raw = (self.root / evidence['local_path']).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), evidence['sha256'])
            self.assertEqual(evidence['url'], URL)
        self.assertEqual(len(self.radar.check(**self.sources)['events']), 1)
        self.assertEqual(self.programs[0]['subjects'][-1]['code'], '408')

    def test_whitespace_only_update_is_low_priority_and_persistent(self):
        self.watch()
        self.change(HTML + '\n')
        state = self.radar.check(**self.sources)
        self.assertEqual(state['events'][0]['type'], 'source_updated')
        self.assertEqual(state['unread_count'], 0)
        other = RadarService(self.root, fetcher=self.fetch)
        self.assertEqual(other.status()['events'], state['events'])

    def test_real_reversion_and_subsequent_repeated_change_each_notify_once(self):
        self.watch()
        for html in (HTML.replace('>30<', '>20<'), HTML, HTML.replace('>30<', '>20<')):
            self.change(html)
            self.radar.check(**self.sources)
        self.assertEqual(len(self.radar.status()['events']), 3)
        self.assertEqual(len(self.radar.check(**self.sources)['events']), 3)

    def test_failed_or_partial_parse_keeps_prior_observation_and_never_reports_removal(self):
        self.watch()
        self.change('<html>temporarily unavailable</html>')
        state = self.radar.check(**self.sources)
        self.assertEqual(state['check_state'], 'needs_review')
        self.assertEqual(state['events'][0]['type'], 'needs_review')
        self.assertEqual(state['baseline_count'], 1)
        self.assertEqual(len(self.radar.check(**self.sources)['events']), 1)
        self.response = OSError('unavailable')
        self.assertEqual(self.radar.check(**self.sources)['check_state'], 'check_failed')
        self.response = {'raw': HTML.replace('408计算机学科专业基础', '901软件基础').encode(), 'url': URL}
        changed = self.radar.check(**self.sources)
        self.assertEqual(len([e for e in changed['events'] if e['type'] == 'program_changed']), 1)

    def test_year_change_is_explicit_review_not_assumed_same_plan(self):
        self.watch()
        self.change(HTML.replace('2027年', '2028年'))
        state = self.radar.check(**self.sources)
        self.assertEqual(state['check_state'], 'needs_review')
        self.assertIn('年度', state['events'][0]['message'])

    def test_primary_2028_heading_with_2027_history_link_cannot_report_same_year_removal(self):
        self.watch()
        html = HTML.replace('2027年', '2028年').replace('01（全日制）智能软件', '02（全日制）新软件')
        self.change(html.replace('</html>', '<aside><a href="/2027/">2027年硕士招生专业目录</a></aside></html>'))
        state = self.radar.check(**self.sources)
        self.assertEqual(state['check_state'], 'needs_review')
        self.assertEqual({event['type'] for event in state['events']}, {'needs_review'})
        self.assertEqual(len(self.radar.check(**self.sources)['events']), 1)

    def test_current_primary_year_is_not_overridden_by_historical_links(self):
        self.watch()
        self.change(HTML.replace('>30<', '>20<').replace('</html>',
            '<a href="/2028/">2028年硕士招生专业目录</a><a href="/2026/">2026年硕士招生专业目录</a></html>'))
        state = self.radar.check(**self.sources)
        self.assertEqual(state['check_state'], 'complete')
        self.assertEqual(state['events'][0]['type'], 'program_changed')

    def test_unknown_pdf_year_requires_review_for_semantic_changes_but_allows_whitespace(self):
        text = ('考试方式 院系所 专业 研究方向 学习方式 专业研究方向备注 考试科目\n'
                '21全国统考 035计算机学院 085404计算机技术 01智能软件 (1)全日制\n'
                '①101思想政治理论②204英语（二）③302数学（二）④408计算机学科专业基础\n')
        raw = b'%PDF-original'
        (self.root / 'source.pdf').write_bytes(raw)
        doc = {**self.document, 'url': 'https://test.edu.cn/catalog.pdf', 'local_path': 'source.pdf',
               'content_type': 'application/pdf', 'sha256': hashlib.sha256(raw).hexdigest()}
        programs = parse_nankai_raw(text, UNIT, 2027, doc)['programs']
        sources = {'programs': programs, 'documents': [doc], 'units': [UNIT]}
        def extract(path):
            return text.replace('408计算机学科专业基础', '901软件基础') if b'changed' in Path(path).read_bytes() else text
        with patch('collectors.documents.extract_pdf_text', side_effect=extract):
            self.radar.watch([programs[0]['id']], **sources)
            self.response = {'raw': raw + b'\n', 'url': doc['url'], 'content_type': 'application/pdf'}
            state = self.radar.check(**sources)
            self.assertEqual(state['events'][0]['type'], 'source_updated')
            self.response = {**self.response, 'raw': b'%PDF-changed'}
            state = self.radar.check(**sources)
            self.assertEqual(state['check_state'], 'needs_review')
            self.assertEqual(state['events'][0]['type'], 'needs_review')
            self.assertIn('年度', state['events'][0]['message'])
            self.assertNotIn('program_changed', {event['type'] for event in state['events']})

    def test_identical_bytes_cannot_hide_a_cross_year_redirect(self):
        self.watch()
        self.response = {**self.response, 'url': URL.replace('/2027/', '/2028/')}
        state = self.radar.check(**self.sources)
        self.assertEqual(state['check_state'], 'needs_review')

    def test_validation_and_scope_count_do_not_silently_drop_unknown_or_previous_watches(self):
        self.watch()
        for invalid in (['unknown'], self.ids + ['unknown'], 'string', [True], self.ids * 101):
            with self.assertRaises(ValueError): self.radar.watch(invalid, **self.sources)
        self.assertEqual(self.radar.status()['watch_count'], 1)
        self.assertEqual(self.radar.status(program_ids=['unknown'])['watch_count'], 0)
        self.assertEqual(self.radar.status(program_ids=self.ids)['watch_count'], 1)
        self.assertEqual(self.radar.check(programs=[], documents=[], units=[])['watch_count'], 1)
        self.assertEqual(self.radar.watch([], **self.sources)['watch_count'], 0)

    def test_stale_or_modified_source_cannot_establish_verified_baseline(self):
        (self.root / 'source.html').write_text('modified', encoding='utf8')
        state = self.watch()
        self.assertEqual(state['baseline_count'], 0)
        self.assertEqual(state['check_state'], 'needs_review')

    def test_parallel_instances_write_valid_state_and_emit_one_notification(self):
        self.watch()
        self.change(HTML.replace('>30<', '>20<'))
        other = RadarService(self.root, fetcher=self.fetch)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda service: service.check(**self.sources), [self.radar, other] * 3))
        state = self.radar.status()
        self.assertEqual(len([e for e in state['events'] if e['type'] == 'program_changed']), 1)
        self.assertEqual(json.loads((self.root / 'runtime/radar.json').read_text('utf8'))['schema_version'], 1)

    def test_shared_program_quota_change_notifies_every_affected_direction(self):
        html = HTML.replace('rowspan="2"', 'rowspan="3"').replace('</tbody>',
            '<tr class="zsml-talbe-yx-zy-yjfx"><td>02（全日制）软件系统</td></tr></tbody>')
        raw = html.encode()
        (self.root / 'source.html').write_bytes(raw)
        self.document = {**self.document, 'sha256': hashlib.sha256(raw).hexdigest()}
        self.programs = parse_zzu_html(html, UNIT, '035 计算机学院', 2027, self.document)['programs']
        self.ids = [p['id'] for p in self.programs]
        self.sources = {**self.sources, 'programs': self.programs, 'documents': [self.document]}
        self.watch()
        self.change(html.replace('>30<', '>20<'))
        events = self.radar.check(**self.sources)['events']
        self.assertEqual(set(events[0]['program_ids']), set(self.ids))
        self.assertEqual(len(events), 1)

    def test_valid_complete_catalog_can_report_removed_and_added_directions(self):
        self.watch()
        self.change(HTML.replace('01（全日制）智能软件', '02（全日制）新软件'))
        events = self.radar.check(**self.sources)['events']
        self.assertEqual({e['type'] for e in events}, {'direction_missing', 'directions_added'})
        self.assertEqual(self.radar.check(**self.sources)['events'], events)

    def test_bad_baseline_recovers_only_after_local_catalog_is_verified(self):
        (self.root / 'source.html').write_text('bad', encoding='utf8')
        self.assertEqual(self.watch()['baseline_count'], 0)
        self.assertEqual(self.radar.check(**self.sources)['baseline_count'], 0)
        (self.root / 'source.html').write_bytes(HTML.encode())
        state = self.radar.check(**self.sources)
        self.assertEqual(state['baseline_count'], 1)
        self.assertEqual(state['events'], [])

    def test_forged_source_signature_cannot_create_baseline(self):
        self.programs[0]['name'] = '伪造专业'
        self.assertEqual(self.watch()['baseline_count'], 0)

    def test_cross_year_redirect_and_old_extraction_metadata_are_not_reused(self):
        self.document = {**self.document, 'text_path': 'old.txt', 'text_sha256': 'old', 'text_pages': [{'page': 1}]}
        self.sources = {**self.sources, 'documents': [self.document]}
        self.watch()
        self.response = {**self.response, 'url': URL.replace('/2027/', '/2028/'),
                         'raw': HTML.replace('<h1>2027年硕士招生专业目录</h1>', '').encode()}
        state = self.radar.check(**self.sources)
        self.assertEqual(state['check_state'], 'needs_review')
        after = state['events'][0]['evidence']['after']
        self.assertEqual(after['final_url'], URL.replace('/2027/', '/2028/'))
        self.assertNotIn('text_path', after)
        self.assertNotIn('text_pages', after)
        self.assertNotEqual(after['id'], state['events'][0]['evidence']['before']['id'])

    def test_invalid_runtime_is_preserved_and_check_lock_is_released(self):
        self.watch()
        self.radar.path.write_text('[]', encoding='utf8')
        with self.assertRaises(ValueError): self.radar.check(**self.sources)
        self.assertFalse(self.radar.context['running'])
        self.radar.path.unlink()
        self.assertEqual(self.radar.check(**self.sources)['watch_count'], 0)

    def test_invalid_fetch_and_wrong_official_redirect_keep_verified_baseline(self):
        self.watch()
        for response in ({'raw': b''}, {'raw': b'official', 'url': 'https://evil.edu.cn/catalog'}):
            self.response = response
            state = self.radar.check(**self.sources)
            self.assertEqual(state['check_state'], 'needs_review')
            self.assertEqual(state['baseline_count'], 1)

    def test_stale_or_missing_official_sources_are_rejected_without_clearing_watch(self):
        self.watch()
        for docs in ([], [{**self.document, 'catalog_active': False}], [{**self.document, 'url': 'https://evil.edu.cn'}]):
            with self.assertRaises(ValueError): self.radar.watch(self.ids, **{**self.sources, 'documents': docs})
            self.assertEqual(self.radar.status()['watch_count'], 1)

    def test_radar_calls_shared_public_fetcher_by_default(self):
        from unittest.mock import patch
        self.watch()
        other = RadarService(self.root)
        with patch('public_sources.fetch_official_document', return_value=self.response) as fetch:
            state = other.check(**self.sources)
        self.assertEqual(state['check_state'], 'complete')
        fetch.assert_called_once_with(URL, UNIT)

    def test_shutdown_cancels_between_bounded_fetches_without_publishing_changes(self):
        self.watch()
        stop = threading.Event()
        stop.set()
        state = self.radar.check(**self.sources, stop_event=stop)
        self.assertEqual(state['check_state'], 'cancelled')
        self.assertEqual(self.fetches, 0)
        stop.clear()
        def fetch(url, unit):
            stop.set()
            return {**self.response, 'raw': HTML.replace('>30<', '>20<').encode()}
        other = RadarService(self.root, fetcher=fetch)
        self.assertEqual(other.check(**self.sources, stop_event=stop)['events'], [])
        self.assertFalse(other.status()['running'])


if __name__ == '__main__':
    unittest.main()
