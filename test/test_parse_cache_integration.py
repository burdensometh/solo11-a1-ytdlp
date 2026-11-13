#!/usr/bin/env python3

# Allow direct execution
import os
import shutil
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


from test.helper import FakeYDL
from yt_dlp.extractor.common import InfoExtractor

TEST_URL = 'http://localhost/sample.mp4'


class _CountingYDL(FakeYDL):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dl_calls = 0

    def dl(self, *args, **kwargs):
        self.dl_calls += 1


class TestParseCacheIntegration(unittest.TestCase):
    def setUp(self):
        TEST_DIR = os.path.dirname(os.path.abspath(__file__))
        TESTDATA_DIR = os.path.join(TEST_DIR, 'testdata')
        os.makedirs(TESTDATA_DIR, exist_ok=True)
        self.test_dir = os.path.join(TESTDATA_DIR, 'parse_cache_integration_test')
        self.extract_calls = {'n': 0}
        self.tearDown()

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def _make_ydl(self, **params):
        ydl = _CountingYDL({'cachedir': self.test_dir, 'parse_cache': True, **params})
        calls = self.extract_calls

        class CountingIE(InfoExtractor):
            _VALID_URL = r'https?://example\.com/watch\?v=(?P<id>\d+)'

            def _real_extract(self, url):
                calls['n'] += 1
                video_id = self._match_id(url)
                return {
                    'id': video_id,
                    'title': f'Video {video_id}',
                    'formats': [
                        {'format_id': '720p', 'height': 720, 'vcodec': 'h264', 'acodec': 'mp4a',
                         'ext': 'mp4', 'url': f'{TEST_URL}/720'},
                        {'format_id': '1080p', 'height': 1080, 'vcodec': 'h264', 'acodec': 'mp4a',
                         'ext': 'mp4', 'url': f'{TEST_URL}/1080'},
                    ],
                }

        ydl.add_info_extractor(CountingIE(ydl))
        return ydl

    def test_same_params_hit_cache(self):
        url = 'https://example.com/watch?v=1'
        ydl = self._make_ydl(format='best[height<=1080]', skip_download=True)
        first = ydl.extract_info(url, download=False)
        # A second run with identical parameters, fresh instance / same cache dir
        ydl2 = self._make_ydl(format='best[height<=1080]', skip_download=True)
        second = ydl2.extract_info(url, download=False)
        self.assertEqual(self.extract_calls['n'], 1)
        self.assertEqual(first, second)
        self.assertEqual(second['format_id'], '1080p')

    def test_changed_quality_reparses(self):
        url = 'https://example.com/watch?v=2'
        ydl_720 = self._make_ydl(format='best[height<=720]', skip_download=True)
        result_720 = ydl_720.extract_info(url, download=False)
        ydl_1080 = self._make_ydl(format='best[height<=1080]', skip_download=True)
        result_1080 = ydl_1080.extract_info(url, download=False)
        # The extractor ran twice because the cache key includes quality
        self.assertEqual(self.extract_calls['n'], 2)
        self.assertEqual(result_720['format_id'], '720p')
        self.assertEqual(result_1080['format_id'], '1080p')
        self.assertNotEqual(result_720['format_id'], result_1080['format_id'])
        # And a repeat of the first quality is a cache hit again
        ydl_720_again = self._make_ydl(format='best[height<=720]', skip_download=True)
        ydl_720_again.extract_info(url, download=False)
        self.assertEqual(self.extract_calls['n'], 2)

    def test_changed_subtitle_langs_reparses(self):
        url = 'https://example.com/watch?v=3'
        ydl_en = self._make_ydl(skip_download=True, subtitleslangs=['en'])
        ydl_en.extract_info(url, download=False)
        ydl_zh = self._make_ydl(skip_download=True, subtitleslangs=['zh'])
        ydl_zh.extract_info(url, download=False)
        self.assertEqual(self.extract_calls['n'], 2)

    def test_audio_only_flag_participates_in_key(self):
        url = 'https://example.com/watch?v=4'
        ydl_av = self._make_ydl(skip_download=True, extractaudio=False)
        ydl_av.extract_info(url, download=False)
        ydl_audio = self._make_ydl(skip_download=True, extractaudio=True)
        ydl_audio.extract_info(url, download=False)
        self.assertEqual(self.extract_calls['n'], 2)

    def test_disabled_parse_cache_always_reparses(self):
        url = 'https://example.com/watch?v=5'
        ydl = _CountingYDL({'cachedir': self.test_dir, 'parse_cache': False, 'skip_download': True})

        class NoCacheIE(InfoExtractor):
            _VALID_URL = r'https?://example\.com/watch\?v=(?P<id>\d+)'
            extract_calls = 0

            def _real_extract(self, url):
                NoCacheIE.extract_calls += 1
                return {'id': self._match_id(url), 'title': 't',
                        'formats': [{'format_id': '720p', 'url': TEST_URL}]}

        ydl.add_info_extractor(NoCacheIE(ydl))
        ydl.extract_info(url, download=False)
        ydl.extract_info(url, download=False)
        self.assertEqual(NoCacheIE.extract_calls, 2)

    def test_playlist_is_not_cached(self):
        url = 'https://example.com/watch?v=6'
        ydl = self._make_ydl(skip_download=True)

        class PlaylistIE(InfoExtractor):
            _VALID_URL = r'playlist:'
            extract_calls = 0

            def _real_extract(self, url):
                PlaylistIE.extract_calls += 1
                return self.playlist_result([
                    {'_type': 'url', 'url': 'https://example.com/watch?v=6', 'ie_key': 'Counting'}])

        ydl.add_info_extractor(PlaylistIE(ydl))
        ydl.extract_info('playlist:', download=False)
        ydl.extract_info('playlist:', download=False)
        self.assertEqual(PlaylistIE.extract_calls, 2)


if __name__ == '__main__':
    unittest.main()
