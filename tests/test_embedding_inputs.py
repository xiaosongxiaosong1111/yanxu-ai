"""Window preparation keeps exact source spans inside the real token budget."""
import hashlib
from pathlib import Path
import unittest

from embedding_inputs import prepare_inputs
from provider_catalog import TransportError

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = str(ROOT / 'data')
LOCAL = {'provider': 'local_bge', 'document_prefix': '背景：', '_data_dir': DATA_DIR}
REMOTE = {'provider': 'custom', 'base_url': 'https://embedding.example.com/v1',
          'api_key': 'fixture-window-key', 'model': 'embed-model'}
HEAD = '计算机学院关于硕士研究生招生的说明'


def segment(text, start=1000):
    return {'id': 'ks_fixture000000000000000000', 'text': text, 'char_start': start,
            'char_end': start + len(text), 'kind': 'paragraph'}


def context():
    return [{'char_start': 0, 'char_end': len(HEAD), 'text': HEAD, 'role': 'heading'}]


def token_length(text):
    import tokenizers
    tokenizer = tokenizers.Tokenizer.from_file(str(
        ROOT / 'data/runtime/embedding-models/bge-small-zh-v1.5' /
        '46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59/tokenizer.json'))
    return len(tokenizer.encode(text).ids)


class EmbeddingInputTests(unittest.TestCase):
    def test_windows_partition_exact_spans_and_bind_sha_to_model_input(self):
        text = '本院只招收全日制硕士研究生。'*20 + '但专项计划考生须另行确认。'
        windows = prepare_inputs(segment(text), context(), LOCAL)
        self.assertTrue(windows)
        self.assertEqual(windows[0]['char_start'], 1000)
        self.assertEqual(windows[-1]['char_end'], 1000 + len(text))
        for index, window in enumerate(windows):
            start, end = window['char_start'], window['char_end']
            self.assertTrue(1000 <= start < end <= 1000 + len(text))
            self.assertEqual(window['text'], text[start-1000:end-1000])
            if index:
                self.assertEqual(window['char_start'], windows[index-1]['char_end'])
            digest = hashlib.sha256(('背景：' + window['embedding_text']).encode('utf-8')).hexdigest()
            self.assertEqual(window['input_sha'], digest)
        self.assertEqual(len({window['window_id'] for window in windows}), len(windows))
        self.assertEqual(windows, prepare_inputs(segment(text), context(), LOCAL))

    def test_tail_exception_of_long_segment_survives_inside_token_budget(self):
        text = '本学院只招收全日制硕士研究生。'*120 + '但少数民族高层次骨干人才计划考生限报非全日制专业。'
        windows = prepare_inputs(segment(text), [], LOCAL)
        self.assertGreater(len(windows), 1)
        self.assertEqual(''.join(window['text'] for window in windows), text)
        for window in windows:
            length = token_length('背景：' + window['embedding_text'])
            self.assertLessEqual(length, 512)
        self.assertTrue(windows[-1]['text'].endswith('限报非全日制专业。'))
        self.assertIn('骨干', windows[-1]['text'])

    def test_context_and_prefix_share_the_budget_and_context_is_real_text(self):
        text = '跨专业考生须加试两门本科主干课程。'
        windows = prepare_inputs(segment(text), context(), LOCAL)
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0]['embedding_text'], HEAD + '\n' + text)
        self.assertLessEqual(token_length('背景：' + windows[0]['embedding_text']), 512)
        plain = prepare_inputs(segment(text), [], {'provider': 'local_bge', '_data_dir': DATA_DIR})
        self.assertEqual(plain[0]['embedding_text'], text)

    def test_remote_windows_use_conservative_character_budget(self):
        text = '招生说明：考生应当符合国家规定的报考条件，如实填写各项信息。'*100
        windows = prepare_inputs(segment(text), context(), REMOTE)
        self.assertGreater(len(windows), 1)
        self.assertEqual(''.join(window['text'] for window in windows), text)
        for window in windows:
            self.assertLessEqual(len('' + window['embedding_text']), REMOTE and 512)
            digest = hashlib.sha256(window['embedding_text'].encode('utf-8')).hexdigest()
            self.assertEqual(window['input_sha'], digest)

    def test_invalid_segments_contexts_or_surrogates_are_rejected(self):
        text = '有效的招生条款。'
        bad_segments = [None, {}, {'text': text, 'char_start': 0, 'char_end': len(text)+5},
                        {'text': text, 'char_start': 5, 'char_end': 2},
                        {'text': '甲\ud800乙', 'char_start': 0, 'char_end': 4},
                        {'text': '甲\x00乙', 'char_start': 0, 'char_end': 3}]
        for value in bad_segments:
            with self.subTest(value=repr(value)[:40]), self.assertRaises(TransportError):
                prepare_inputs(value, [], LOCAL)
        with self.assertRaises(TransportError):
            prepare_inputs(segment(text), 'not-a-list', LOCAL)
        with self.assertRaises(TransportError):
            prepare_inputs(segment(text), [{'char_start': 0, 'char_end': 2}], LOCAL)
        with self.assertRaises(TransportError):
            prepare_inputs(segment(text), [], {'provider': 'deepseek', 'api_key': 'k', 'model': 'm'})


    def test_long_prefix_must_terminate_and_cover_the_tail(self):
        """Independent subprocess: a long legal prefix with repeated public text used to
        loop forever in _hard_split; it must finish fast, stay in budget, keep the tail."""
        import os
        import subprocess
        import sys
        script = (
            "import sys;sys.path.insert(0, r'%s');"
            "from embedding_inputs import prepare_inputs;"
            "t='本院只招收全日制硕士研究生，接受跨专业考生。'*8;"
            "segment={'id':'ks_x','text':t,'char_start':0,'char_end':len(t)};"
            "rows=prepare_inputs(segment,[],{'provider':'local_bge','document_prefix':'背'*256,'_data_dir':r'%s'});"
            "assert rows and rows[-1]['text'].endswith('跨专业考生。'), 'tail lost';"
            "print('ok', len(rows))" % (ROOT, ROOT / 'data')
        )
        done = subprocess.run([sys.executable, '-X', 'utf8', '-c', script], capture_output=True,
                              text=True, timeout=8, env={**os.environ, 'PYTHONPATH': str(ROOT)})
        self.assertEqual(done.returncode, 0, done.stderr[-2000:])
        self.assertIn('ok', done.stdout)

    def test_prefix_that_leaves_no_room_fails_fast_not_by_looping(self):
        import time
        started = time.monotonic()
        config = {'provider': 'local_bge', 'document_prefix': '背' * 40, 'input_limit': 32,
                  '_data_dir': DATA_DIR}
        with self.assertRaises(TransportError) as caught:
            prepare_inputs(segment('本院只招收全日制硕士研究生。'), [], config)
        self.assertEqual(caught.exception.code, 'embedding_input_too_long')
        self.assertLess(time.monotonic() - started, 3)


if __name__ == '__main__':
    unittest.main()
