"""A named admissions portal is a role, not a guessed domain alias."""
import hashlib
import unittest

from collectors.official_bindings import binding_candidates


UNIT = {'id': '367929', 'name': '中国科学院大学', 'unit_code': '14430'}
URL = 'https://yz.chsi.com.cn/zsgs/zsjz/detail.action?schCode=14430&zsgsId=7'


def candidates(body):
    raw = ('<article><h1>中国科学院大学2027年硕士研究生招生章程</h1>'
           + body + '</article>').encode('utf-8')
    return binding_candidates(UNIT, {'url': URL, 'sha256': hashlib.sha256(raw).hexdigest(),
        'content_type': 'text/html;charset=utf-8'}, raw,
        foreign_names=['中国科学院大学', '其他大学', '河北大学'])


class InformationNetworkBindingTests(unittest.TestCase):
    def test_named_portal_beside_chsi_is_proved_from_its_nearest_owner(self):
        rows = candidates('<p>可通过中国研究生招生信息网（https://yz.chsi.com.cn）'
            '或中国科学院大学招生信息网（https://admission.ucas.edu.cn）查询。</p>')
        self.assertEqual([row['url'] for row in rows], ['https://admission.ucas.edu.cn/'])
        self.assertEqual(rows[0]['status'], 'declared_official_origin')
        self.assertEqual(rows[0]['evidence']['quote'], 'https://admission.ucas.edu.cn')

    def test_named_portal_anchor_and_explicit_role_forms_are_supported(self):
        for label in ['中国科学院大学招生信息网', '中国科学院大学研究生招生网', '我校研招网']:
            with self.subTest(label=label):
                rows = candidates('<p><a href="https://admission.ucas.edu.cn">' + label + '</a></p>')
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]['status'], 'declared_official_origin')

    def test_trailing_sentence_mark_does_not_erase_another_owner(self):
        for role in ['研究生院官网', '招生信息网']:
            with self.subTest(role=role):
                self.assertFalse(candidates('<p>其他大学' + role + '：https://grad.peer.edu.cn/。</p>'))

    def test_earlier_own_name_does_not_override_the_nearest_foreign_owner(self):
        for body in ['<p>中国科学院大学提醒考生查询河北大学招生信息网：https://grad.peer.edu.cn/。</p>',
                     '<p>我校参照其他大学招生信息网：https://grad.peer.edu.cn/。</p>',
                     '<p>参考我校招生信息网：https://admission.ucas.edu.cn/。</p>']:
            with self.subTest(body=body):
                self.assertFalse(candidates(body))

    def test_hidden_and_script_declarations_do_not_prove_an_origin(self):
        for tag in ['<p hidden>{}</p>', '<div inert>{}</div>', '<script>{}</script>', '<template>{}</template>']:
            with self.subTest(tag=tag):
                self.assertFalse(candidates(tag.format('中国科学院大学招生信息网：https://admission.ucas.edu.cn/。')))

    def test_platform_recommendation_and_foreign_pronoun_are_not_own_portals(self):
        for body in ['<p>教育部推荐的招生信息网：https://grad.peer.edu.cn。</p>',
                     '<p>河北大学（详情见https://www.hbu.edu.cn）或该校招生信息网'
                     '（https://grad.hbu.edu.cn）查询。</p>']:
            with self.subTest(body=body):
                self.assertFalse(candidates(body))

    def test_a_previous_own_declaration_does_not_prove_the_next_sentence(self):
        own = '中国科学院大学招生信息网：https://admission.ucas.edu.cn。'
        other = '教育部推荐的招生信息网：https://grad.peer.edu.cn。'
        for body in ['<p>' + own + '</p><p>' + other + '</p>',
                     '<p>' + own + other + '</p>']:
            with self.subTest(body=body):
                self.assertEqual([row['url'] for row in candidates(body)],
                                 ['https://admission.ucas.edu.cn/'])

    def test_an_unlisted_foreign_institution_is_not_granted_a_legacy_site_role(self):
        self.assertFalse(candidates('<p>珠海理工学院研究生院官网：https://grad.peer.edu.cn/。</p>'))


if __name__ == '__main__':
    unittest.main()
