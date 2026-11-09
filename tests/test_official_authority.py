"""Ownership grants replay real links and raw proofs without widening parent domains."""
from copy import deepcopy
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path
import unittest

from official_authority import OfficialAuthority, registered_scopes, scope_allows


UNIT = {'id': '123', 'name': '样本大学', 'unit_code': '10001',
        'official_url': 'https://www.sample.edu.cn/'}
TARGET = 'https://grad.other.ac.cn/'
CHSI = 'https://yz.chsi.com.cn/zsgs/zsjz/detail.action?schCode=10001&zsgsId=7'


def document(url, body, *, media='text/html; charset=utf-8', **extra):
    raw = body.encode('utf-8')
    return {'url': url, 'raw': raw, 'content_type': media,
            'sha256': hashlib.sha256(raw).hexdigest(), 'network_verified': True, **extra}


class OfficialAuthorityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='official-authority-')
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name)
        self.now = datetime(2026, 11, 9, tzinfo=timezone.utc)
        self.store = OfficialAuthority(self.data, clock=lambda: self.now)
        self.docs = {UNIT['official_url']: document(UNIT['official_url'],
            f'<title>样本大学</title><nav><a href="{TARGET}">研究生招生</a></nav>'),
            TARGET: document(TARGET, '<title>样本大学研究生院</title><h1>样本大学研究生招生</h1>')}
        self.calls = []

    def fetch(self, url, scope):
        self.assertTrue(scope_allows(url, scope), (url, scope))
        self.assertNotIn('official_url', scope)
        self.calls.append((url, deepcopy(scope)))
        return deepcopy(self.docs[url])

    def verify(self, unit=None, target=TARGET, **kwargs):
        return self.store.verify_candidate(unit or UNIT, target, fetcher=self.fetch, **kwargs)

    def test_cross_registered_domain_requires_actual_link_and_target_identity(self):
        result = self.verify()
        self.assertEqual(result['status'], 'verified', result)
        grant = result['authority']
        self.assertEqual(grant['scope']['host'], 'grad.other.ac.cn')
        self.assertTrue(self.store.allows(UNIT, TARGET + 'info/1.htm'))
        self.assertFalse(self.store.allows(UNIT, 'https://peer.other.ac.cn/info/1.htm'))
        self.assertFalse(self.store.allows(UNIT, 'https://other.ac.cn/'))
        again = OfficialAuthority(self.data, clock=lambda: self.now)
        self.assertEqual(again.bindings(UNIT)[0]['id'], grant['id'])

    def test_self_claimed_title_or_academic_suffix_alone_never_establishes_ownership(self):
        self.docs[UNIT['official_url']] = document(UNIT['official_url'], '<title>样本大学</title>')
        result = self.verify()
        self.assertEqual(result['status'], 'needs_review')
        self.assertEqual(result['code'], 'ownership_unproven')
        self.assertFalse(self.store.allows(UNIT, TARGET))

    def test_foreign_primary_identity_is_rejected_despite_trusted_crosslink(self):
        self.docs[TARGET] = document(TARGET, '<title>其他大学研究生院</title>样本大学')
        result = self.verify()
        self.assertEqual(result['code'], 'target_unit_mismatch')

    def test_generic_friendship_link_does_not_authorize_self_claimed_school(self):
        self.docs[UNIT['official_url']] = document(UNIT['official_url'],
            f'<title>样本大学</title><a href="{TARGET}">友情链接</a>')
        self.assertEqual(self.verify()['status'], 'needs_review')

    def test_hash_mismatch_and_network_unverified_proof_rejected(self):
        for update in ({'sha256': '0' * 64}, {'network_verified': False}):
            with self.subTest(update=update):
                prior = self.docs[UNIT['official_url']]
                self.docs[UNIT['official_url']] = {**prior, **update}
                self.assertEqual(self.verify()['status'], 'needs_review')
                self.docs[UNIT['official_url']] = prior

    def test_registered_source_discovery_still_checks_document_identity(self):
        target = UNIT['official_url'] + 'notice.htm'
        self.docs[target] = document(target, '<title>其他大学2027招生章程</title>')
        self.assertEqual(self.verify(target=target)['code'], 'target_unit_mismatch')

    def test_exact_host_registered_scope_does_not_inherit_parent_siblings(self):
        unit = {**UNIT, 'official_url': 'https://institute-b.parent.ac.cn/'}
        scopes = registered_scopes(unit)
        self.assertTrue(scope_allows(unit['official_url'], scopes[0]))
        self.assertFalse(scope_allows('https://institute-a.parent.ac.cn/', scopes[0]))
        self.assertFalse(self.store.allows(unit, 'https://parent.ac.cn/'))

    def test_path_scope_never_grants_other_school_paths(self):
        target = 'https://shared.example.org/schools/sample/'
        self.docs[UNIT['official_url']] = document(UNIT['official_url'],
            f'<a href="{target}">样本大学研究生招生网站</a>')
        self.docs[target] = document(target, '<title>样本大学研究生院</title>')
        self.assertEqual(self.verify(target=target)['status'], 'verified')
        self.assertTrue(self.store.allows(UNIT, target + '2027.html'))
        self.assertFalse(self.store.allows(UNIT, 'https://shared.example.org/schools/other/'))
        self.assertFalse(self.store.allows(UNIT, 'https://shared.example.org/schools/sample2/'))

    def test_shared_publisher_account_is_required_and_cannot_change(self):
        target = 'https://mp.weixin.qq.com/s?__biz=SAMPLE&mid=1'
        self.docs[UNIT['official_url']] = document(UNIT['official_url'],
            f'<a href="{target}">样本大学研究生招生官方公众号</a>')
        self.docs[target] = document(target,
            '<title>样本大学研究生招生</title><script>var biz="OTHER";</script>')
        self.assertEqual(self.verify(target=target)['code'], 'target_account_mismatch')
        self.docs[target] = document(target,
            '<title>样本大学研究生招生</title><script>var biz="SAMPLE";</script>')
        self.assertEqual(self.verify(target=target)['status'], 'verified')
        self.assertTrue(self.store.allows(UNIT, target))
        self.assertFalse(self.store.allows(UNIT, target.replace('SAMPLE', 'OTHER')))

    def test_account_host_without_account_id_is_not_granted(self):
        target = 'https://mp.weixin.qq.com/s/token'
        self.docs[UNIT['official_url']] = document(UNIT['official_url'],
            f'<a href="{target}">研究生招生官方公众号</a>')
        self.docs[target] = document(target, '<title>样本大学研究生招生</title>')
        self.assertEqual(self.verify(target=target)['code'], 'account_scope_missing')

    def test_sha_checked_same_unit_saved_chsi_declaration_is_reusable_offline(self):
        unit = {**UNIT, 'official_url': None}
        saved = document(CHSI, '<h1>样本大学2027年硕士研究生招生章程</h1>'
                         f'<p>学校研究生招生网站：<a href="{TARGET}">研究生招生网站</a></p>')
        self.assertEqual(self.verify(unit, saved_declarations=[saved])['status'], 'verified')
        self.assertNotIn(CHSI, [url for url, _ in self.calls])

    def test_saved_chsi_school_mismatch_and_hash_change_rejected(self):
        unit = {**UNIT, 'official_url': None}
        for saved in (document(CHSI.replace('10001', '99999'),
                       f'<h1>样本大学招生章程</h1><p>研究生院网站：{TARGET}</p>'),
                      document(CHSI, f'<h1>样本大学招生章程</h1><p>研究生院网站：{TARGET}</p>', sha256='0'*64)):
            self.assertEqual(self.verify(unit, saved_declarations=[saved])['status'], 'needs_review')

    def test_explicit_public_ip_and_port_exception_is_local_to_proven_scope(self):
        unit = {**UNIT, 'official_url': None}
        target = 'http://125.34.90.207:8088/admissions/'
        saved = document(CHSI, '<h1>样本大学2027年硕士研究生招生章程</h1>'
                         f'<p>本校研究生招生网站：<a href="{target}">本校研究生招生网站</a></p>')
        self.docs[target] = document(target, '<title>样本大学研究生招生</title>')
        result = self.verify(unit, target, saved_declarations=[saved])
        self.assertEqual(result['status'], 'verified', result)
        self.assertTrue(self.store.allows(unit, target + '2027.html'))
        self.assertFalse(self.store.allows(unit, target.replace(':8088', ':8090')))
        self.assertFalse(self.store.allows(unit, target.replace('/admissions/', '/other/')))

    def test_unproven_ports_private_ips_and_credentials_never_fetched(self):
        for target in ('http://125.34.90.207:8088/', 'http://127.0.0.1:8088/',
                       'http://10.1.1.1/', 'https://u:p@grad.other.ac.cn/',
                       'https://local.internal/', 'file:///tmp/abc'):
            with self.subTest(target=target):
                before = len(self.calls)
                self.assertEqual(self.verify(target=target)['status'], 'needs_review')
                self.assertNotIn(target, [url for url, _ in self.calls[before:]])

    def test_same_site_external_navigation_script_must_contain_actual_href(self):
        script = UNIT['official_url'] + 'nav.js'
        self.docs[UNIT['official_url']] = document(UNIT['official_url'],
            '<title>样本大学</title><script src="/nav.js"></script>')
        self.docs[script] = document(script,
            f'document.write(\'<a href="{TARGET}">研究生招生</a>\');', media='application/javascript')
        self.assertEqual(self.verify()['status'], 'verified')

    def test_plain_script_url_and_commented_anchor_are_not_ownership_proofs(self):
        for script in (f'var target="{TARGET}";',
                       f'// document.write(\'<a href="{TARGET}">研究生招生</a>\');'):
            self.docs[UNIT['official_url']] = document(UNIT['official_url'],
                '<title>样本大学</title><script>' + script + '</script>')
            self.assertEqual(self.verify()['status'], 'needs_review')

    def test_revocation_and_changed_source_hash_prevent_reuse(self):
        first = self.verify()['authority']
        self.store.revoke(first['id'], 'review revoked')
        self.assertFalse(self.store.allows(UNIT, TARGET))
        second = self.verify()['authority']
        snapshot = self.data / second['proofs'][0]['local_path']
        snapshot.write_bytes(b'changed source')
        self.assertFalse(self.store.allows(UNIT, TARGET))

    def test_changed_target_snapshot_and_unit_identity_prevent_adoption(self):
        grant = self.verify()['authority']
        self.assertFalse(self.store.allows({**UNIT, 'name': '另一个大学'}, TARGET))
        snapshot = self.data / grant['target']['local_path']
        snapshot.write_bytes(b'changed target')
        self.assertEqual(self.store.replay(UNIT, grant['id'])['status'], 'needs_review')

    def test_changed_live_proof_revokes_grant(self):
        grant = self.verify()['authority']
        self.docs[UNIT['official_url']] = document(UNIT['official_url'], '<title>样本大学</title>')
        replay = self.store.replay(UNIT, grant['id'], fetcher=self.fetch)
        self.assertEqual(replay['code'], 'proof_changed')
        self.assertFalse(self.store.allows(UNIT, TARGET))

    def test_expired_replay_and_changed_registered_seed_prevent_adoption(self):
        grant = self.verify()['authority']
        self.now += timedelta(days=31)
        self.assertFalse(self.store.allows(UNIT, TARGET))
        self.now -= timedelta(days=31)
        self.assertFalse(self.store.allows({**UNIT, 'official_url': 'https://new.sample.edu.cn/'}, TARGET))

    def test_enrichment_keeps_scoped_grants_out_of_flat_official_urls(self):
        self.verify()
        enriched = self.store.enrich(UNIT)
        self.assertNotIn('official_urls', enriched)
        self.assertTrue(any(row['scope']['host'] == 'grad.other.ac.cn'
                            for row in enriched['official_authorities']))
        self.assertEqual(UNIT['official_url'], 'https://www.sample.edu.cn/')

    def test_curated_two_bindings_still_offer_exact_scopes(self):
        for name, expected in [('中国科学院大学', 'admission.ucas.ac.cn'),
                               ('中国社会科学院大学', 'skdzs.ucass.edu.cn')]:
            unit = {'id': name, 'name': name}
            self.assertIn(expected, {row['host'] for row in registered_scopes(unit)})

    def test_nested_same_site_route_is_bounded_and_replayed(self):
        intermediate = UNIT['official_url'] + 'graduate/index.html'
        self.docs[UNIT['official_url']] = document(UNIT['official_url'],
            '<a href="/graduate/index.html">研究生教育</a>')
        self.docs[intermediate] = document(intermediate,
            f'<title>样本大学研究生教育</title><a href="{TARGET}">研究生招生</a>')
        result = self.verify()
        self.assertEqual(result['status'], 'verified', result)
        self.assertGreaterEqual(len(result['authority']['proofs']), 2)

    def test_persisted_derived_authority_can_start_next_candidate_without_original_seed(self):
        unit = {**UNIT, 'official_url': None}
        saved = document(CHSI, '<h1>样本大学硕士招生章程</h1>'
                         f'<p>学校研招网站：<a href="{TARGET}">研究生招生网站</a></p>')
        self.assertEqual(self.verify(unit, saved_declarations=[saved])['status'], 'verified')
        candidate = TARGET + '2027.htm'
        self.docs[candidate] = document(candidate, '<title>样本大学2027招生章程</title>')
        self.assertEqual(self.verify(unit, candidate)['status'], 'verified')

    def test_revoking_parent_authority_revokes_derived_child_scope(self):
        child_url = 'https://second.example.org/'
        self.docs[TARGET] = document(TARGET,
            f'<title>样本大学研究生院</title><a href="{child_url}">研究生招生网站</a>')
        parent = self.verify()['authority']
        self.docs[child_url] = document(child_url, '<title>样本大学研究生招生</title>')
        child = self.verify(target=child_url)
        self.assertEqual(child['status'], 'verified', child)
        self.store.revoke(parent['id'])
        self.assertFalse(self.store.allows(UNIT, child_url))

    def test_registry_scope_or_chain_tampering_fails_replay(self):
        grant = self.verify()['authority']
        grant['scope']['path'] = '/other/'
        with closing(sqlite3.connect(self.store.path)) as connection:
            with connection:
                connection.execute('UPDATE authority SET payload=? WHERE id=?',
                                   (json.dumps(grant), grant['id']))
        self.assertEqual(self.store.replay(UNIT, grant['id'])['status'], 'needs_review')

    def test_foreign_registered_root_title_does_not_prove_candidate_ownership(self):
        self.docs[UNIT['official_url']] = document(UNIT['official_url'],
            f'<title>其他大学</title><a href="{TARGET}">研究生招生</a>')
        self.assertEqual(self.verify()['code'], 'proof_unit_mismatch')

    def test_live_replay_fetch_failure_stops_adoption_without_raising(self):
        grant = self.verify()['authority']
        def unavailable(url, scope):
            raise RuntimeError('unavailable')
        result = self.store.replay(UNIT, grant['id'], fetcher=unavailable)
        self.assertEqual(result['status'], 'needs_review')

    def test_script_hrefs_do_not_activate_from_comment_or_inert_strings(self):
        for script in (f'/* document.write(\'<a href="{TARGET}">研究生招生</a>\'); */',
                       f'var inert = \'document.write("<a href=\\"{TARGET}\\">研究生招生</a>")\';'):
            self.docs[UNIT['official_url']] = document(UNIT['official_url'],
                '<title>样本大学</title><script>' + script + '</script>')
            self.assertEqual(self.verify()['status'], 'needs_review')

    def test_recorded_chsi_short_notice_and_ip_declaration_replay(self):
        fixture = Path(__file__).parent / 'fixtures/official_discovery'
        cases = json.loads((fixture / 'cases32.json').read_text('utf-8'))['cases']
        for identifier, target in [('368369', 'https://yz.cug.edu.cn/'),
                                    ('788359951', 'http://125.34.90.207:8088/')]:
            with self.subTest(unit_id=identifier):
                case = next(row for row in cases if row['unit']['id'] == identifier)
                unit = {**case['unit'], 'official_url': None}
                source = case['saved_declarations'][0]
                saved = {**source, 'raw': (fixture / source['raw_path']).read_bytes()}
                self.docs[target] = document(target, '<title>' + unit['name'] + '研究生招生</title>')
                result = self.verify(unit, target, saved_declarations=[saved])
                self.assertEqual(result['status'], 'verified', result)
                self.assertTrue(self.store.allows(unit, target))

    def test_saved_chsi_referenced_other_school_site_is_not_promoted(self):
        unit = {**UNIT, 'official_url': None}
        saved = document(CHSI, '<h1>样本大学硕士招生章程</h1>'
            f'<p>参考其他大学研究生招生网站：<a href="{TARGET}">研究生招生信息网</a></p>')
        self.assertEqual(self.verify(unit, saved_declarations=[saved])['status'], 'needs_review')

    def test_declaration_candidates_expose_pointers_without_granting_authority(self):
        saved = document(CHSI, '<h1>样本大学硕士招生章程</h1>'
                         f'<p>学校研招网站：<a href="{TARGET}">研究生招生网站</a></p>')
        rows = self.store.declaration_candidates(UNIT, [saved])
        self.assertEqual(rows[0]['url'], TARGET)
        self.assertEqual(rows[0]['proof_url'], CHSI)
        self.assertFalse(self.store.allows(UNIT, TARGET))

    def test_registered_http_has_scoped_https_upgrade_but_https_never_downgrades(self):
        scopes = registered_scopes({**UNIT, 'official_url': 'http://www.sample.edu.cn/graduate/'})
        self.assertTrue(any(scope_allows('https://www.sample.edu.cn/graduate/', row) for row in scopes))
        self.assertFalse(any(scope_allows('https://www.sample.edu.cn/other/', row) for row in scopes))
        self.assertFalse(any(scope_allows('http://www.sample.edu.cn/', row) for row in registered_scopes(UNIT)))

    def test_explicit_http_upgrade_permits_only_same_host_path_standard_tls_port(self):
        scope = registered_scopes({**UNIT, 'official_url': 'http://www.sample.edu.cn/graduate/'})[0]
        self.assertTrue(scope.get('https_upgrade'))
        self.assertTrue(scope_allows('https://www.sample.edu.cn/graduate/2027.htm', scope))
        for url in ('https://peer.sample.edu.cn/graduate/', 'https://www.sample.edu.cn/other/',
                    'https://www.sample.edu.cn:8088/graduate/', 'http://www.sample.edu.cn:443/graduate/'):
            self.assertFalse(scope_allows(url, scope), url)
        self.assertFalse(scope_allows('https://www.sample.edu.cn/graduate/',
                                     {**scope, 'https_upgrade': False}))

    def test_same_host_http_redirect_proof_is_replayed_using_actual_https_url(self):
        unit = {**UNIT, 'official_url': 'http://www.sample.edu.cn/'}
        self.docs[unit['official_url']] = document(UNIT['official_url'],
            f'<title>样本大学</title><a href="{TARGET}">研究生招生</a>')
        self.docs.pop(UNIT['official_url'])
        result = self.verify(unit)
        self.assertEqual(result['status'], 'verified', result)
        self.assertEqual(result['authority']['proofs'][0]['url'], UNIT['official_url'])
        self.assertEqual(self.store.replay(unit, result['authority']['id'])['status'], 'verified')

    def test_malformed_registry_fails_closed(self):
        self.store.path.parent.mkdir(parents=True)
        self.store.path.write_bytes(b'not sqlite')
        self.assertFalse(self.store.allows(UNIT, TARGET))
        self.assertEqual(self.store.replay(UNIT, 'missing')['status'], 'needs_review')

    def test_invalid_unit_arguments_fail_closed(self):
        self.assertFalse(self.store.allows(None, TARGET))
        self.assertEqual(self.store.bindings(None), ())

    def test_future_review_timestamp_does_not_replay(self):
        grant = self.verify()['authority']
        self.now -= timedelta(days=1)
        self.assertEqual(self.store.replay(UNIT, grant['id'])['status'], 'needs_review')

    def test_inert_script_control_flow_or_json_cannot_prove_ownership(self):
        published = f'document.write(\'<a href="{TARGET}">研究生招生</a>\');'
        bodies = ['<script>if(false){' + published + '}</script>',
                  '<script>function neverCalled(){' + published + '}</script>',
                  '<script type="application/json">' + published + '</script>',
                  '<script>false && ' + published + '</script>']
        for body in bodies:
            with self.subTest(body=body):
                self.docs[UNIT['official_url']] = document(UNIT['official_url'], body)
                self.assertEqual(self.verify()['status'], 'needs_review')

    def test_shared_cms_school_query_is_part_of_verified_scope(self):
        target = 'https://shared.example.org/admissions.php?school=sample'
        self.docs[UNIT['official_url']] = document(UNIT['official_url'],
            f'<a href="{target}">样本大学研究生招生网站</a>')
        self.docs[target] = document(target, '<title>样本大学研究生招生</title>')
        result = self.verify(target=target)
        self.assertEqual(result['status'], 'verified', result)
        self.assertTrue(self.store.allows(UNIT, target))
        self.assertFalse(self.store.allows(UNIT, target.replace('sample', 'other')))
        self.assertFalse(self.store.allows(UNIT, target.split('?')[0]))

    def test_registered_query_entry_is_preserved_when_following_ownership_links(self):
        entry = 'https://shared.example.org/admissions.php?school=sample'
        unit = {**UNIT, 'official_url': entry}
        self.docs[entry] = document(entry,
            f'<title>样本大学</title><a href="{TARGET}">研究生招生网站</a>')
        self.assertIn(entry, self.store.seed_urls(unit))
        self.assertEqual(self.verify(unit)['status'], 'verified')

    def test_replay_rejects_changed_actual_proof_origin_even_when_relative_chain_matches(self):
        unit = {**UNIT, 'official_url': 'https://www.sample.edu.cn/institution/'}
        target = 'https://www.sample.edu.cn/grad/'
        self.docs[unit['official_url']] = document(unit['official_url'], '<a href="/grad/">研究生招生</a>')
        self.docs[target] = document(target, '<title>样本大学研究生招生</title>')
        grant = self.verify(unit, target)['authority']
        evil = 'https://evil.example.org/grad/'
        grant['proofs'][0]['url'] = 'https://evil.example.org/institution/'
        grant['proofs'][0]['next_url'] = evil
        grant['scope']['host'] = 'evil.example.org'
        grant['candidate_url'] = evil
        grant['target']['url'] = evil
        with closing(sqlite3.connect(self.store.path)) as connection:
            with connection:
                connection.execute('UPDATE authority SET payload=? WHERE id=?',
                                   (json.dumps(grant), grant['id']))
        self.assertEqual(self.store.replay(unit, grant['id'])['status'], 'needs_review')
        self.assertFalse(self.store.allows(unit, evil))

    def test_real_qualified_school_uses_same_unit_pointer_and_visible_full_name(self):
        fixture = Path(__file__).parent / 'fixtures/official_discovery'
        case = next(row for row in json.loads((fixture / 'cases32.json').read_text('utf-8'))['cases']
                    if row['unit']['id'] == '368369')
        unit = {**case['unit'], 'official_url': None}
        source = case['saved_declarations'][0]
        saved = {**source, 'raw': (fixture / source['raw_path']).read_bytes()}
        target = 'https://yz.cug.edu.cn/'
        raw = (fixture / 'navigation/cug-index.html').read_bytes()
        self.docs[target] = {'url': target, 'raw': raw, 'sha256': hashlib.sha256(raw).hexdigest(),
                             'network_verified': True, 'content_type': 'text/html; charset=utf-8'}
        result = self.verify(unit, target, saved_declarations=[saved])
        self.assertEqual(result['status'], 'verified', result)
        self.assertEqual(self.store.replay(unit, result['authority']['id'])['status'], 'verified')
        self.assertFalse(self.store.allows({**unit, 'name': '中国地质大学(北京)', 'unit_code': '11415'}, target))

    def test_omitted_campus_qualifier_needs_visible_full_identity_or_own_unit_code(self):
        unit = {**UNIT, 'name': '样本大学(甲地)'}
        self.docs[UNIT['official_url']] = document(UNIT['official_url'],
            f'<title>样本大学(甲地)</title><a href="{TARGET}">研究生招生</a>')
        for body, expected in [('<p>样本大学(甲地)2027年硕士招生章程</p>', 'verified'),
                               ('<p>招生单位代码：10001</p>', 'verified'),
                               ('<p>样本大学(乙地)2027年硕士招生章程</p>', 'needs_review'),
                               ('<p>欢迎报考研究生。</p>', 'needs_review')]:
            with self.subTest(body=body):
                self.docs[TARGET] = document(TARGET, '<title>样本大学研究生招生信息网</title>' + body)
                self.assertEqual(self.verify(unit)['status'], expected)

    def test_explicit_wrong_campus_primary_title_wins_over_body_mentions(self):
        unit = {**UNIT, 'name': '样本大学(甲地)'}
        self.docs[UNIT['official_url']] = document(UNIT['official_url'],
            f'<title>样本大学(甲地)</title><a href="{TARGET}">研究生招生</a>')
        self.docs[TARGET] = document(TARGET,
            '<title>样本大学(乙地)研究生院</title><p>参考样本大学(甲地)招生章程。招生单位代码：10001</p>')
        self.assertEqual(self.verify(unit)['code'], 'target_unit_mismatch')

    def test_complete_qualified_name_in_hidden_script_is_not_visible_identity(self):
        unit = {**UNIT, 'name': '样本大学(甲地)'}
        self.docs[UNIT['official_url']] = document(UNIT['official_url'],
            f'<title>样本大学(甲地)</title><a href="{TARGET}">研究生招生</a>')
        self.docs[TARGET] = document(TARGET, '<title>样本大学研究生院</title>'
            '<script>var example="样本大学(甲地)2027招生章程";</script><p hidden>样本大学(甲地)</p>')
        self.assertEqual(self.verify(unit)['status'], 'needs_review')

    def test_real_chsi_plain_site_fields_and_named_publication_pointers_are_retained(self):
        fixture = Path(__file__).parent / 'fixtures/official_discovery'
        cases = json.loads((fixture / 'cases32.json').read_text('utf-8'))['cases']
        expected = {'367863': 'http://yanzhao.bisu.edu.cn/', '3274413879': 'http://yjsxy.ccsu.cn/',
                    '678715': 'https://yjsc.ylnu.edu.cn/', '368477': 'https://www.swun.edu.cn/',
                    '2807892330': 'https://yjszs.cqwu.edu.cn/article_434346.html',
                    '368439': 'https://ha.hainanu.edu.cn/gs/'}
        for identifier, target in expected.items():
            with self.subTest(unit_id=identifier):
                case = next(row for row in cases if row['unit']['id'] == identifier)
                unit = {**case['unit'], 'official_url': None}
                saved = [{**row, 'raw': (fixture / row['raw_path']).read_bytes()}
                         for row in case['saved_declarations']]
                self.assertIn(target, [row['url'] for row in self.store.declaration_candidates(unit, saved)])
                self.docs[target] = document(target, '<title>' + unit['name'] + '研究生招生</title>')
                result = self.verify(unit, target, saved_declarations=saved)
                self.assertEqual(result['status'], 'verified', result)
                self.assertEqual(self.store.replay(unit, result['authority']['id'])['status'], 'verified')
                if identifier == '2807892330':
                    self.assertFalse(self.store.allows(unit, 'https://yjszs.cqwu.edu.cn/'))

    def test_trusted_navigation_target_may_identify_its_school_by_visible_logo(self):
        self.docs[TARGET] = document(TARGET, '<title>招生网</title>'
            '<div class="css-logo"><a href="/"><img alt="样本大学招生网"></a></div>')
        self.assertEqual(self.verify()['status'], 'verified')

    def test_logo_identity_needs_ownership_chain_and_cannot_override_foreign_title(self):
        for body in ('<title>其他大学招生网</title><header><img alt="样本大学招生网"></header>',
                     '<title>招生网</title><div hidden class="logo"><img alt="样本大学招生网"></div>',
                     '<title>招生网</title><main><img alt="样本大学招生网"></main>'):
            with self.subTest(body=body):
                self.docs[TARGET] = document(TARGET, body)
                self.assertEqual(self.verify()['status'], 'needs_review')
        self.docs[TARGET] = document(TARGET, '<title>招生网</title><header><img alt="样本大学招生网"></header>')
        self.docs[UNIT['official_url']] = document(UNIT['official_url'], '<title>样本大学</title>')
        self.assertEqual(self.verify()['status'], 'needs_review')

    def test_unrelated_plain_text_urls_and_email_are_not_own_site_declarations(self):
        unit = {**UNIT, 'official_url': None}
        for body in (f'<p>参考其他大学研究生招生网站：{TARGET}</p>',
                     f'<p>查阅其他大学2027年硕士招生章程：</p><p>{TARGET}</p>',
                     f'<p>网址：{TARGET}</p>', '<p>学校网站邮箱：office@www.other.ac.cn</p>'):
            saved = document(CHSI, '<h1>样本大学2027招生章程</h1>' + body)
            self.assertEqual(self.store.declaration_candidates(unit, [saved]), [])


if __name__ == '__main__':
    unittest.main()
