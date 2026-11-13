#!/usr/bin/env python3

# Allow direct execution
import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


import shutil

from test.helper import FakeYDL
from yt_dlp.cache import Cache
from yt_dlp.extraction_cache import (
    DEFAULT_TTL,
    SECTION,
    ExtractionCache,
    make_cache_key,
)


URL = 'https://example.com/watch?v=1'


class TestExtractionCacheKey(unittest.TestCase):
    def test_same_params_same_key(self):
        self.assertEqual(
            make_cache_key(URL, 'bestvideo+bestaudio', ['en', 'zh'], False),
            make_cache_key(URL, 'bestvideo+bestaudio', ['en', 'zh'], False))

    def test_each_parameter_changes_key(self):
        base = make_cache_key(URL, '1080p', ['en'], False)
        # Same URL, different quality -> different key (the original bug)
        self.assertNotEqual(base, make_cache_key(URL, '720p', ['en'], False))
        # Different subtitle language -> different key
        self.assertNotEqual(base, make_cache_key(URL, '1080p', ['zh'], False))
        self.assertNotEqual(base, make_cache_key(URL, '1080p', ['en', 'zh'], False))
        # Audio-only flag -> different key
        self.assertNotEqual(base, make_cache_key(URL, '1080p', ['en'], True))
        # Different URL -> different key
        self.assertNotEqual(base, make_cache_key(URL + 'x', '1080p', ['en'], False))

    def test_key_is_order_independent_for_language_list_input(self):
        # The list content is part of the payload, order is significant but
        # equivalent values must serialize identically
        self.assertEqual(
            make_cache_key(URL, None, tuple(['en']), False),
            make_cache_key(URL, None, ['en'], False))

    def test_key_is_hex_string(self):
        key = make_cache_key(URL)
        self.assertRegex(key, r'^[0-9a-f]{64}$')


class TestExtractionCache(unittest.TestCase):
    def setUp(self):
        TEST_DIR = os.path.dirname(os.path.abspath(__file__))
        TESTDATA_DIR = os.path.join(TEST_DIR, 'testdata')
        os.makedirs(TESTDATA_DIR, exist_ok=True)
        self.test_dir = os.path.join(TESTDATA_DIR, 'extraction_cache_test')
        self.tearDown()

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def _cache(self, ttl=DEFAULT_TTL, cachedir=True):
        ydl = FakeYDL({'cachedir': self.test_dir if cachedir else False})
        return ExtractionCache(Cache(ydl), ttl=ttl), ydl

    def _entry_fn(self, ec, url, **params):
        key = make_cache_key(url, **params)
        return ec._cache._get_cache_fn(SECTION, key, 'json')

    def test_store_and_load_roundtrip(self):
        ec, _ = self._cache()
        self.assertIsNone(ec.load(URL, '1080p', ['en'], False))
        data = {'id': '1', 'formats': [{'url': 'http://x'}], 'title': 't'}
        ec.store(URL, data, '1080p', ['en'], False)
        self.assertEqual(ec.load(URL, '1080p', ['en'], False), data)

    def test_different_quality_does_not_collide(self):
        ec, _ = self._cache()
        ec.store(URL, {'quality': '1080p'}, '1080p')
        ec.store(URL, {'quality': '720p'}, '720p')
        self.assertEqual(ec.load(URL, '1080p'), {'quality': '1080p'})
        self.assertEqual(ec.load(URL, '720p'), {'quality': '720p'})
        # Two distinct files on disk
        section_dir = os.path.join(self.test_dir, SECTION)
        self.assertEqual(len(os.listdir(section_dir)), 2)

    def test_different_subtitle_langs_do_not_collide(self):
        ec, _ = self._cache()
        ec.store(URL, {'subs': 'en'}, subtitle_langs=['en'])
        ec.store(URL, {'subs': 'zh'}, subtitle_langs=['zh'])
        self.assertEqual(ec.load(URL, subtitle_langs=['en']), {'subs': 'en'})
        self.assertEqual(ec.load(URL, subtitle_langs=['zh']), {'subs': 'zh'})

    def test_audio_only_does_not_collide(self):
        ec, _ = self._cache()
        ec.store(URL, {'mode': 'av'}, audio_only=False)
        ec.store(URL, {'mode': 'audio'}, audio_only=True)
        self.assertEqual(ec.load(URL, audio_only=False), {'mode': 'av'})
        self.assertEqual(ec.load(URL, audio_only=True), {'mode': 'audio'})

    def test_entry_has_write_timestamp(self):
        ec, _ = self._cache()
        before = time.time()
        ec.store(URL, {'id': '1'})
        fn = self._entry_fn(ec, URL)
        with open(fn, encoding='utf-8') as f:
            payload = json.load(f)
        entry = payload['data']  # Cache.store wraps the entry under 'data'
        self.assertIn('written_at', entry)
        self.assertGreaterEqual(entry['written_at'], before)
        self.assertEqual(entry['data'], {'id': '1'})

    def test_expired_entry_is_ignored_and_removed(self):
        ec, _ = self._cache(ttl=10)
        ec.store(URL, {'id': 'old'})
        fn = self._entry_fn(ec, URL)
        self.assertTrue(os.path.exists(fn))

        # Backdate the entry beyond its TTL
        with open(fn, encoding='utf-8') as f:
            payload = json.load(f)
        payload['data']['written_at'] = time.time() - 11
        with open(fn, 'w', encoding='utf-8') as f:
            json.dump(payload, f)

        self.assertIsNone(ec.load(URL))
        # Expired entry was deleted on access
        self.assertFalse(os.path.exists(fn))

    def test_fresh_entry_within_ttl(self):
        ec, _ = self._cache(ttl=100)
        ec.store(URL, {'id': 'fresh'})
        self.assertEqual(ec.load(URL), {'id': 'fresh'})

    def test_entry_without_timestamp_is_discarded(self):
        ec, _ = self._cache()
        ec.store(URL, {'id': '1'})
        fn = self._entry_fn(ec, URL)
        with open(fn, 'w', encoding='utf-8') as f:
            json.dump({'data': {'id': '1'}}, f)
        self.assertIsNone(ec.load(URL))
        self.assertFalse(os.path.exists(fn))

    def test_remove_expired_only_removes_stale_entries(self):
        ec, _ = self._cache(ttl=100)
        # Fresh entry in the parse-results section
        ec.store(URL, {'id': 'fresh'})
        fresh_fn = self._entry_fn(ec, URL)
        # Manually create an expired entry under a different key
        stale_key = make_cache_key('https://example.com/other')
        stale_fn = ec._cache._get_cache_fn(SECTION, stale_key, 'json')
        os.makedirs(os.path.dirname(stale_fn), exist_ok=True)
        with open(stale_fn, 'w', encoding='utf-8') as f:
            json.dump({
                'yt-dlp_version': '2026.10.26',
                'data': {
                    '_extraction_cache': True,
                    'written_at': time.time() - 1000,
                    'data': {'id': 'stale'},
                },
            }, f)
        # A corrupt entry that should be swept as well
        corrupt_fn = ec._cache._get_cache_fn(SECTION, make_cache_key('https://x/corrupt'), 'json')
        with open(corrupt_fn, 'w', encoding='utf-8') as f:
            f.write('{not json')

        removed = ec.remove_expired()
        self.assertEqual(removed, 2)
        self.assertTrue(os.path.exists(fresh_fn))
        self.assertFalse(os.path.exists(stale_fn))
        self.assertFalse(os.path.exists(corrupt_fn))
        # A second sweep has nothing to do
        self.assertEqual(ec.remove_expired(), 0)

    def test_remove_expired_leaves_foreign_cache_sections_untouched(self):
        ec, ydl = self._cache()
        # An entry in another cache section (e.g. an auth token) has no
        # extraction-cache marker and must never be swept by this cleanup
        other_dir = os.path.join(self.test_dir, 'some-site')
        os.makedirs(other_dir, exist_ok=True)
        other_fn = os.path.join(other_dir, 'token.json')
        with open(other_fn, 'w', encoding='utf-8') as f:
            json.dump({'yt-dlp_version': '2026.10.26', 'data': {'written_at': time.time() - DEFAULT_TTL - 1}}, f)
        self.assertEqual(ec.remove_expired(), 0)
        self.assertTrue(os.path.exists(other_fn))

    def test_remove_clears_parse_section(self):
        ec, _ = self._cache()
        ec.store(URL, {'id': '1'})
        ec.store(URL, {'id': '2'}, '720p')
        ec.remove()
        self.assertFalse(os.path.exists(os.path.join(self.test_dir, SECTION)))
        self.assertIsNone(ec.load(URL))

    def test_disabled_cache_is_noop(self):
        ec, _ = self._cache(cachedir=False)
        self.assertFalse(ec.enabled)
        ec.store(URL, {'id': '1'})  # must not raise or write anything
        self.assertIsNone(ec.load(URL))
        self.assertEqual(ec.remove_expired(), 0)

    def test_default_ttl_is_24_hours(self):
        self.assertEqual(DEFAULT_TTL, 24 * 60 * 60)


if __name__ == '__main__':
    unittest.main()
