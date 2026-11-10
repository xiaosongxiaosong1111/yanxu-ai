"""Admission regressions found in frozen official sources, plus evaluator contracts."""
import unittest
from pathlib import Path
import hashlib
import json
import shutil
import tempfile
import zipfile
from unittest.mock import patch

from knowledge_quality import assess_quality
from tools.evaluate_knowledge_gold import CORPUS_SHA256, FREEZE_SHA256, covered, locate_quote, valid_span
from tools.evaluate_knowledge_gold import evaluate, load_fixture, PROJECT_ROOT, FIXTURE


class RealSourceAdmissionTests(unittest.TestCase):
    def assess(self, title, body, year=2027):
        source = {'title': title, 'year': year, 'unit_id': 'public-unit',
                  'status': 'official_original_read', 'text_origin': 'full_extraction'}
        return assess_quality(source, body, original_available=True, authority_verified=True)

    def test_fee_amounts_can_share_a_separate_explicit_unit_header(self):
        result = self.assess('2027级研究生学费收费公示表',
                             '收费项目 收费依据\n收费标准\n（元/生.学年）\n'
                             '全日制学术学位硕士研究生 政策依据 8000\n电子信息 政策依据 14000')
        self.assertEqual(result['state'], 'accepted')
        self.assertEqual(result['material_type'], 'fees')

    def test_fee_table_header_and_navigation_cannot_supply_an_actual_amount(self):
        for body in ('收费标准\n（元/生.学年）\n附件：收费标准2027.xlsx',
                     '收费标准\n（元/生.学年）\n发布时间 2027\n浏览次数 14000',
                     '收费标准\n（元/生.学年）\n津发改价费〔2014〕 93',
                     '收费标准\n（元/生.学年）\n政策依据 2027',
                     '收费标准\n（元/生.学年）\n津发改价费 2014\n文号 93',
                     '收费标准\n（元/生.学年）\n发文字号 93',
                     '收费标准\n（元/生.学年）\n批准文号 93',
                     '收费标准\n（元/生.学年）\n14000',
                     '收费标准\n全日制电子信息 14000'):
            with self.subTest(body=body):
                self.assertEqual(self.assess('2027级研究生学费收费公示表', body)['state'], 'pending')

    def test_cohort_title_and_main_heading_year_must_not_conflict_with_scope(self):
        body = '学费每生每年8000元。'
        for title, text in [('2026级研究生学费标准', body),
                            ('研究生学费标准', '2026级研究生学费标准\n' + body)]:
            with self.subTest(title=title):
                result = self.assess(title, text)
                self.assertEqual(result['state'], 'pending')
                self.assertIn('year_conflict', {r['code'] for r in result['reasons']})

    def test_unknown_year_is_not_filled_from_target_or_title_by_admission(self):
        result = self.assess('2027级研究生学费标准', '学费每生每年8000元。', year=None)
        self.assertEqual(result['state'], 'pending')
        self.assertIn('year_unknown', {r['code'] for r in result['reasons']})

    def test_official_initial_score_basic_requirement_is_a_score_material(self):
        title = '2026年全国硕士研究生招生考试考生进入复试的初试成绩基本要求'
        body = '门类 总分 政治 外国语 业务课一 业务课二\n工学 290 45 45 70 70'
        result = self.assess(title, body, year=2026)
        self.assertEqual(result['state'], 'accepted')
        self.assertEqual(result['material_type'], 'scores')

    def test_score_full_mark_header_alone_is_not_a_score_threshold(self):
        title = '2026年考生进入复试的初试成绩基本要求'
        for body in ('门类 总分 单科（满分=100分） 单科（满分>100分）\n详情见附件。',
                     '总分 单科满分100分 单科满分150分\n详情见附件。',
                     '总分 单科满分为100分 单科满分是150分\n详情见附件。',
                     '门类 总分 单科\n（满分=100\n分） 单科（满分>100\n分）\n详情见附件。'):
            with self.subTest(body=body):
                self.assertEqual(self.assess(title, body, year=2026)['state'], 'pending')

    def test_multiline_score_header_is_accepted_only_with_actual_scored_rows(self):
        body = ('学科门类（专业）\n单科要求\n总分\n要求\n政治/\n管理类\n综合\n外语\n'
                '业务\n课一\n业务\n课二\n学术学位\n工学（08） 50 45 70 75 290')
        self.assertEqual(self.assess('2026年初试成绩基本要求', body, year=2026)['state'], 'accepted')
        self.assertEqual(self.assess('2026年初试成绩基本要求', body.rsplit('\n', 1)[0], year=2026)['state'], 'pending')

    def test_score_labels_accept_bracketed_category_codes_without_absorbing_extra_cells(self):
        header = '专业 总分 政治 英语\n'
        for label in ('工学（08）', '工程类（085、086）', '技术转移（0260S1）', '085404 计算机技术'):
            with self.subTest(label=label):
                self.assertEqual(self.assess('2027年复试分数线', header + label + ' 300 50 50')['state'], 'accepted')
        for label in ('计算机科学与技术 2026', '计算机科学与技术 1', '工学（08） 2026',
                      '工学（2026 11 03 12）', '计算机科学与技术（2026）',
                      '计算机科学与技术（350）', '2026 计算机科学与技术'):
            with self.subTest(label=label):
                self.assertEqual(self.assess('2027年复试分数线', header + label + ' 11 03 12')['state'], 'pending')


class GoldEvaluationContractTests(unittest.TestCase):
    def setUp(self):
        self.extraction = {'text': '第1页\n相同条款。\n第2页\n相 同 条款。', 'page_kind': 'physical',
                           'pages': [{'page': 1, 'char_start': 0, 'char_end': 9},
                                     {'page': 2, 'char_start': 10, 'char_end': 21}]}

    def test_frozen_corpus_and_manifest_are_unchanged(self):
        fixture = Path(__file__).parent / 'fixtures/knowledge_gold'
        for name, expected in [('corpus.json', CORPUS_SHA256), ('manifest.json', FREEZE_SHA256)]:
            self.assertEqual(hashlib.sha256((fixture / name).read_bytes()).hexdigest(), expected)
        corpus = json.loads((fixture / 'corpus.json').read_text('utf-8'))
        self.assertEqual(len(corpus['questions']), 34)
        self.assertEqual(len({s['raw_sha256'] for s in corpus['sources']}), 29)

    def test_repeated_quote_resolves_with_actual_page_not_first_occurrence(self):
        span = locate_quote(self.extraction, {'quote': '相同条款。', 'page': 2})
        self.assertEqual(span, (14, 21))

    def test_duplicate_quote_without_locator_is_ambiguous(self):
        self.assertIsNone(locate_quote(self.extraction, {'quote': '相同条款。', 'page': None}))
        self.assertIsNone(locate_quote(self.extraction, {'quote': '不存在', 'page': 1}))

    def test_frozen_whole_text_offset_can_disambiguate_repeated_quote(self):
        evidence = {'quote': '相同条款。', 'page': None, 'char_start': 13, 'char_end': 18}
        frozen = '第1页\n相同条款。\n第2页相同条款。'
        self.assertEqual(locate_quote(self.extraction, evidence, frozen_text=frozen), (14, 21))
        self.assertIsNone(locate_quote(self.extraction, evidence, frozen_text='相同条款。重复的不完整原文'))

    def test_coverage_cannot_concatenate_disconnected_fragments_into_a_quote(self):
        self.assertTrue(covered((0, 5), [(0, 2), (2, 5)], '甲乙 丙丁'))
        self.assertFalse(covered((0, 5), [(0, 1), (3, 5)], '甲乙 丙丁'))

    def test_exact_runtime_span_checks_unicode_positions_and_real_pdf_page(self):
        value = {'text': '相 同 条款。', 'char_start': 14, 'char_end': 21,
                 'page_start': 2, 'page_end': 2}
        self.assertTrue(valid_span(value, self.extraction))
        self.assertFalse(valid_span({**value, 'page_start': 1}, self.extraction))
        self.assertFalse(valid_span({**value, 'text': '伪造的连续引文'}, self.extraction))


class GoldPublicInputFreezeTests(unittest.TestCase):
    """Package growth must not change the 29 reviewed originals or unit context."""

    @classmethod
    def setUpClass(cls):
        cls.corpus = json.loads((FIXTURE / 'corpus.json').read_bytes())
        cls.historical = json.loads((FIXTURE / 'public_bundle_manifest.json').read_bytes())
        cls.paths = {'data/units.json', *(source['original_member'] for source in cls.corpus['sources'])}
        cls.original = cls.corpus['sources'][0]['original_member']
        cls.base = tempfile.TemporaryDirectory(prefix='yanxu-gold-input-base-')
        cls.addClassCleanup(cls.base.cleanup)
        cls.base_root = Path(cls.base.name)
        packages = []
        for package in cls.historical['packages']:
            files = [item for item in package['files'] if item['path'] in cls.paths]
            if not files:
                continue
            packages.append({**package, 'files': files})
            archive = cls.base_root / 'data/bundles' / package['name']
            archive.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as zipped:
                for item in files:
                    destination = cls.base_root / item['path']
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(PROJECT_ROOT / item['path'], destination)
                    zipped.write(destination, item['path'])
        (cls.base_root / 'data/bundles/manifest.json').write_text(
            json.dumps({**cls.historical, 'packages': packages}), encoding='utf-8')

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='yanxu-gold-input-case-')
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name) / 'project'
        shutil.copytree(self.base_root, self.project)
        self.manifest_path = self.project / 'data/bundles/manifest.json'

    def manifest(self):
        return json.loads(self.manifest_path.read_bytes())

    def write_manifest(self, value):
        self.manifest_path.write_text(json.dumps(value), encoding='utf-8')

    def change_member(self, path, **changes):
        value = self.manifest()
        self.write_manifest({**value, 'packages': [
            {**package, 'files': [{**item, **changes} if item['path'] == path else item
                                 for item in package['files']]}
            for package in value['packages']]})

    def archive_for(self, path):
        package = next(package for package in self.manifest()['packages']
                       if any(item['path'] == path for item in package['files']))
        return self.project / 'data/bundles' / package['name']

    def rewrite_archive_member(self, path, replacement):
        archive = self.archive_for(path)
        with zipfile.ZipFile(archive) as zipped:
            entries = [(item.filename, zipped.read(item)) for item in zipped.infolist()
                       if item.filename != path]
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as zipped:
            for name, raw in entries:
                zipped.writestr(name, raw)
            if replacement is not None:
                zipped.writestr(path, replacement)

    def test_unrelated_dataset_packages_and_members_may_change(self):
        value = self.manifest()
        self.write_manifest({**value, 'dataset': {'programs': 99999}, 'packages': [
            *value['packages'], {'name': 'new-unrelated.zip', 'files': [
                {'path': 'data/new-catalog.json', 'sha256': 'unrelated', 'size': 100}]}]})
        result = load_fixture(self.project)
        self.assertEqual(result, self.corpus)
        self.assertEqual(len(result['questions']), 34)

    def test_current_real_public_bundle_keeps_the_same_gold_inputs(self):
        self.assertEqual(load_fixture(PROJECT_ROOT), self.corpus)

    def test_historical_manifest_is_bound_to_the_original_frozen_sha(self):
        frozen = json.loads((FIXTURE / 'manifest.json').read_bytes())
        self.assertEqual(hashlib.sha256((FIXTURE / 'public_bundle_manifest.json').read_bytes()).hexdigest(),
                         frozen['public_bundle_manifest_sha256'])
        temporary = tempfile.TemporaryDirectory(prefix='yanxu-gold-fixture-case-')
        self.addCleanup(temporary.cleanup)
        copied = Path(temporary.name) / 'fixture'
        shutil.copytree(FIXTURE, copied)
        (copied / 'public_bundle_manifest.json').write_bytes(b'{}')
        with self.assertRaises(ValueError):
            load_fixture(self.project, copied)

    def test_selected_manifest_sha_and_size_changes_are_rejected(self):
        for path in (self.original, 'data/units.json'):
            size = next(item['size'] for package in self.manifest()['packages']
                        for item in package['files'] if item['path'] == path)
            for changes in ({'sha256': '0' * 64}, {'size': 1}, {'size': True}, {'size': float(size)}):
                with self.subTest(path=path, changes=changes):
                    original = self.manifest_path.read_bytes()
                    self.change_member(path, **changes)
                    with self.assertRaises(ValueError):
                        load_fixture(self.project)
                    self.manifest_path.write_bytes(original)

    def test_missing_selected_manifest_member_is_rejected(self):
        value = self.manifest()
        self.write_manifest({**value, 'packages': [{**package, 'files': [
            item for item in package['files'] if item['path'] != self.original]}
            for package in value['packages']]})
        with self.assertRaises(ValueError):
            load_fixture(self.project)

    def test_conflicting_or_duplicate_selected_manifest_paths_are_rejected(self):
        value = self.manifest()
        member = next(item for package in value['packages'] for item in package['files']
                      if item['path'] == self.original)
        for duplicate in (member, {**member, 'sha256': 'f' * 64}):
            for first in (False, True):
                with self.subTest(conflict=duplicate != member, duplicate_first=first):
                    extra = {'name': 'conflicting.zip', 'files': [duplicate]}
                    packages = [extra, *value['packages']] if first else [*value['packages'], extra]
                    self.write_manifest({**value, 'packages': packages})
                    with self.assertRaises(ValueError):
                        load_fixture(self.project)

    def test_self_consistent_changes_to_current_original_or_units_do_not_redefine_gold(self):
        for path in (self.original, 'data/units.json'):
            raw = (self.project / path).read_bytes()
            manifest = self.manifest_path.read_bytes()
            archive = self.archive_for(path)
            archive_raw = archive.read_bytes()
            changed = raw + b'\n'
            (self.project / path).write_bytes(changed)
            self.rewrite_archive_member(path, changed)
            self.change_member(path, size=len(changed), sha256=hashlib.sha256(changed).hexdigest())
            with self.subTest(path=path), self.assertRaises(ValueError):
                load_fixture(self.project)
            (self.project / path).write_bytes(raw)
            self.manifest_path.write_bytes(manifest)
            archive.write_bytes(archive_raw)

    def test_changed_or_missing_expanded_original_and_units_are_rejected(self):
        for path in (self.original, 'data/units.json'):
            location = self.project / path
            raw = location.read_bytes()
            for replacement in (raw[:-1] + bytes([raw[-1] ^ 1]), raw + b'changed', None):
                with self.subTest(path=path, missing=replacement is None):
                    if replacement is None:
                        location.unlink()
                    else:
                        location.write_bytes(replacement)
                    with self.assertRaises((ValueError, FileNotFoundError)):
                        load_fixture(self.project)
                    location.write_bytes(raw)

    def test_changed_missing_and_duplicate_zipped_inputs_are_rejected(self):
        for path in (self.original, 'data/units.json'):
            archive = self.archive_for(path)
            original_archive = archive.read_bytes()
            raw = (self.project / path).read_bytes()
            for replacement in (raw[:-1] + bytes([raw[-1] ^ 1]), raw + b'changed', None):
                with self.subTest(path=path, missing=replacement is None):
                    self.rewrite_archive_member(path, replacement)
                    with self.assertRaises(ValueError):
                        load_fixture(self.project)
                    archive.write_bytes(original_archive)
            with zipfile.ZipFile(archive, 'a') as zipped:
                with self.assertWarns(UserWarning):
                    zipped.writestr(path, raw)
            with self.assertRaises(ValueError):
                load_fixture(self.project)
            archive.write_bytes(original_archive)

    def test_unreadable_or_missing_selected_archives_are_rejected(self):
        archive = self.archive_for(self.original)
        raw = archive.read_bytes()
        for replacement in (b'not a zip', None):
            with self.subTest(missing=replacement is None):
                if replacement is None:
                    archive.unlink()
                else:
                    archive.write_bytes(replacement)
                with self.assertRaises(ValueError):
                    load_fixture(self.project)
                archive.write_bytes(raw)

    def test_archive_paths_cannot_read_outside_public_bundles(self):
        value = self.manifest()
        self.write_manifest({**value, 'packages': [
            {**package, 'name': '../outside.zip'} for package in value['packages']]})
        with patch('tools.evaluate_knowledge_gold.zipfile.ZipFile') as archive:
            with self.assertRaises(ValueError):
                load_fixture(self.project)
            archive.assert_not_called()


@unittest.skipUnless(shutil.which('pdftotext') and (PROJECT_ROOT / 'data/units.json').is_file(),
                     'Real gold replay requires pdftotext and tools/prepare_data.py public data.')
class GoldPipelineIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch('socket.getaddrinfo', side_effect=AssertionError('Gold must stay offline')):
            with patch('socket.create_connection', side_effect=AssertionError('Gold must stay offline')):
                cls.result = evaluate()

    def test_reviewed_originals_reach_trusted_bounded_reading(self):
        metrics = self.result['metrics']
        self.assertEqual(metrics['trusted_recall_denominator'], 31)
        self.assertEqual(metrics['source_recall_hits'], 31)
        self.assertGreaterEqual(metrics['all_required_quotes_read'], 29)
        self.assertEqual(metrics['quotes_present_after_extraction'], 61)
        self.assertEqual(metrics['runtime_span_or_budget_violations'], 0)

    def test_actual_years_and_undated_challenges_keep_their_frozen_scope(self):
        self.assertTrue(all(source['actual_year'] == source['inferred_year'] for source in self.result['sources']))
        self.assertEqual(len(self.result['challenges']), 3)
        self.assertTrue(all(item['standalone_year_remains_unknown'] for item in self.result['challenges']))
        self.assertTrue(all(not item['publication_chain_runtime_supported'] for item in self.result['challenges']))

    def test_report_does_not_claim_model_accuracy_live_sources_or_blind_holdout(self):
        for key in ('network_calls', 'application_database_reads', 'model_calls'):
            self.assertEqual(self.result[key], 0)
        self.assertIn('not a blind', self.result['split_notice'])
        self.assertEqual(self.result['by_split']['holdout']['questions'], 10)
        self.assertEqual(self.result['by_split']['development']['questions'], 24)


if __name__ == '__main__':
    unittest.main()
