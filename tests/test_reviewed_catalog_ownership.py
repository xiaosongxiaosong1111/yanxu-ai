from pathlib import Path
import tempfile
import unittest

from official_authority import OfficialAuthority
from official_search import preferred_site
from public_sources import document_attribution, official_source_url
from unit_bindings import enrich


class ReviewedCatalogOwnershipTests(unittest.TestCase):
    def test_school_registry_uses_exact_reviewed_admissions_scopes(self):
        schools = (
            ('367984', '10216', '燕山大学', 'zsjyc.ysu.edu.cn',
             'https://zsjyc.ysu.edu.cn/info/1255/4496.htm', 'https://other.ysu.edu.cn/'),
            ('368392', '10537', '湖南农业大学', 'yjsy.hunau.edu.cn',
             'https://nxy.hunau.edu.cn/gksx/ssszs/202610/P020261013532536855478.pdf',
             'https://other.hunau.edu.cn/gksx/ssszs/'),
            ('368423', '11078', '广州大学', 'yjsy.gzhu.edu.cn',
             'https://environ.gzhu.edu.cn/info/1063/8489.htm', 'https://other.gzhu.edu.cn/info/1063/8489.htm'),
            ('368252', '10392', '福建医科大学', 'yjsy.fjmu.edu.cn',
             'https://yjsy.fjmu.edu.cn/2026/0929/c2437a220123/page.htm', 'https://other.fjmu.edu.cn/'),
            ('368491', '10651', '西南财经大学', 'yz.swufe.edu.cn',
             'https://yzcx.swufe.edu.cn/chaxun/yzb/ss/2027/目录.htm',
             'https://yzcx.swufe.edu.cn/chaxun/yzb/bs/2027/目录.htm'),
            ('368450', '10650', '四川外国语大学', 'graduate.sisu.edu.cn',
             'https://graduate.sisu.edu.cn/docs/2026-10/01f7522ce3134fd79fb81c74031ed4ec.pdf',
             'https://other.sisu.edu.cn/'),
            ('368032', '10140', '辽宁大学', 'grs.lnu.edu.cn',
             'https://grs.lnu.edu.cn/2027sszsml.pdf', 'https://other.lnu.edu.cn/2027sszsml.pdf'),
            ('368376', '10520', '中南财经政法大学', 'yzb.zuel.edu.cn',
             'https://yzb.zuel.edu.cn/2026/0929/c4640a440252/page.htm', 'https://other.zuel.edu.cn/'),
            ('368560', '10704', '西安科技大学', 'yjs.xust.edu.cn',
             'https://yjs.xust.edu.cn/info/1154/7727.htm', 'https://other.xust.edu.cn/'),
        )
        with tempfile.TemporaryDirectory() as directory:
            authority = OfficialAuthority(Path(directory))
            for identifier, code, name, host, allowed, denied in schools:
                with self.subTest(name=name):
                    unit = enrich({'id': identifier, 'unit_code': code, 'name': name, 'official_url': ''})
                    scoped = authority.enrich(unit)
                    self.assertEqual(preferred_site(scoped), host)
                    self.assertTrue(official_source_url(allowed, scoped))
                    self.assertFalse(official_source_url(denied, scoped))
                    self.assertFalse(official_source_url('https://' + host + '.example.com/', scoped))

    def test_njtc_publisher_matches_the_exact_verified_department_host(self):
        unit = enrich({'id': '3274440987', 'unit_code': '10640', 'name': '内江师范学院',
                       'official_url': 'https://yjs.njtc.edu.cn/'})
        article = 'https://gc.njtc.edu.cn/info/1861/39371.htm'
        title = '内江师范学院物理与电子信息工程学院 2027年硕士研究生招生专业目录'
        with tempfile.TemporaryDirectory() as directory:
            scoped = OfficialAuthority(Path(directory)).enrich(unit)
            self.assertEqual(preferred_site(scoped), 'yjs.njtc.edu.cn')
            self.assertTrue(official_source_url(article, scoped))
            self.assertFalse(official_source_url('https://example.njtc.edu.cn/info/1861/39371.htm', scoped))
            self.assertEqual(document_attribution(article, scoped, document_title=title)['status'], 'verified')

    def test_joint_publisher_title_attribution_is_bound_to_reviewed_college_host(self):
        college = '环境科学与工程学院'
        publisher_title = f'{college}、大湾区环境研究院2027年硕士研究生招生简章'
        college_url = 'https://environ.gzhu.edu.cn/info/1063/8489.htm'
        with tempfile.TemporaryDirectory() as directory:
            unit = enrich({'id': '368423', 'unit_code': '11078', 'name': '广州大学', 'official_url': ''})
            scoped = OfficialAuthority(Path(directory)).enrich(unit)
            self.assertEqual(document_attribution(college_url, scoped, document_title=publisher_title)['status'], 'verified')
            self.assertEqual(document_attribution(college_url, scoped, document_title=f'{publisher_title}-{college}')['status'], 'verified')
            for wrong_title in (f'{publisher_title}-其他大学', f'其他大学{publisher_title}',
                                f'{publisher_title}转载自其他大学'):
                with self.subTest(title=wrong_title):
                    self.assertEqual(document_attribution(college_url, scoped, document_title=wrong_title)['status'], 'wrong_unit')
            self.assertEqual(document_attribution('https://yjsy.gzhu.edu.cn/other.htm', scoped,
              document_title=publisher_title)['status'], 'wrong_unit')


if __name__ == '__main__':
    unittest.main()
