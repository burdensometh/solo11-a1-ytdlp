import contextlib
import hashlib
import json
import os
import shutil
import time


# Cache entries older than this many seconds are considered stale
DEFAULT_TTL = 24 * 60 * 60

# Filesystem section used for parsed extraction results.
# Must match the section validation regex in cache.Cache._get_cache_fn
SECTION = 'parse-results'


def make_cache_key(url, quality=None, subtitle_langs=None, audio_only=False):
    """Build a stable cache key from every parameter that affects the result.

    The same URL must produce a different key whenever the requested quality,
    subtitle languages or audio-only mode change, otherwise parsing results
    from one configuration get returned for another.
    """
    payload = {
        'url': url,
        'quality': quality,
        'subtitle_langs': list(subtitle_langs or []),
        'audio_only': bool(audio_only),
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(serialized.encode('utf-8')).hexdigest()


class ExtractionCache:
    """Filesystem cache of processed extraction (parse) results.

    Each entry is a JSON file whose key is a hash of the URL together with the
    quality, subtitle language and audio-only parameters.  Entries record the
    time they were written and expire automatically after `ttl` seconds
    (default 24 hours).  Expired entries can also be removed manually.
    """

    def __init__(self, cache, ttl=DEFAULT_TTL):
        self._cache = cache
        self._ttl = ttl

    @property
    def enabled(self):
        return self._cache.enabled

    def load(self, url, quality=None, subtitle_langs=None, audio_only=False):
        """Return a cached result dict, or None on miss/expiry/disabled."""
        if not self.enabled:
            return None

        key = make_cache_key(url, quality, subtitle_langs, audio_only)
        entry = self._cache.load(SECTION, key)
        if not isinstance(entry, dict) or not entry.get('_extraction_cache'):
            if entry is not None:
                self._cache._ydl.write_debug(f'Ignoring unrecognized cache entry {key}')
                self._remove_entry(key)
            return None

        written_at = entry.get('written_at')
        if not isinstance(written_at, (int, float)):
            self._cache._ydl.write_debug(f'Ignoring cache entry {key} without a write timestamp')
            self._remove_entry(key)
            return None

        age = time.time() - written_at
        if age > self._ttl:
            self._cache._ydl.write_debug(
                f'Cache entry {key} expired ({int(age)}s old, TTL {self._ttl}s)')
            self._remove_entry(key)
            return None

        self._cache._ydl.write_debug(f'Using cached parse result for {url}')
        return entry.get('data')

    def store(self, url, data, quality=None, subtitle_langs=None, audio_only=False):
        """Persist a parse result together with its write timestamp."""
        if not self.enabled:
            return

        key = make_cache_key(url, quality, subtitle_langs, audio_only)
        entry = {
            '_extraction_cache': True,
            'written_at': time.time(),
            'ttl': self._ttl,
            'params': {
                'url': url,
                'quality': quality,
                'subtitle_langs': list(subtitle_langs or []),
                'audio_only': bool(audio_only),
            },
            'data': data,
        }
        self._cache.store(SECTION, key, entry)

    def _remove_entry(self, key):
        fn = self._cache._get_cache_fn(SECTION, key, 'json')
        with contextlib.suppress(OSError):
            os.remove(fn)

    def _is_expired_file(self, fn):
        """Return True if this extraction-cache entry is stale.

        Files not written by this class (no `_extraction_cache` marker) are
        left untouched so other cache sections keep their own lifecycle.
        """
        try:
            with open(fn, encoding='utf-8') as cachef:
                payload = json.load(cachef)
            entry = payload.get('data') if isinstance(payload, dict) else None
            if not isinstance(entry, dict) or not entry.get('_extraction_cache'):
                return False
            written_at = entry.get('written_at')
            if not isinstance(written_at, (int, float)):
                return True
            return time.time() - written_at > self._ttl
        except (OSError, ValueError):
            # Unreadable JSON in our own section can only be a broken entry
            return fn.startswith(os.path.join(self._cache._get_root_dir(), SECTION))

    def remove_expired(self):
        """Delete expired entries across every cache section.

        Returns the number of files removed.  Only entries written by this
        class (carrying the `_extraction_cache` marker) are evaluated, so
        other sections' own cache files are never touched.  Entries without a
        write timestamp or with an unreadable payload are removed as well.
        """
        if not self.enabled:
            return 0

        root = self._cache._get_root_dir()
        if not os.path.isdir(root):
            return 0

        removed = 0
        for name in os.listdir(root):
            section_dir = os.path.join(root, name)
            if not os.path.isdir(section_dir):
                continue
            for filename in os.listdir(section_dir):
                if not filename.endswith('.json'):
                    continue
                fn = os.path.join(section_dir, filename)
                if self._is_expired_file(fn):
                    with contextlib.suppress(OSError):
                        os.remove(fn)
                        removed += 1
        return removed

    def remove(self):
        """Delete every cached parse result."""
        if not self.enabled:
            return
        section_dir = os.path.join(self._cache._get_root_dir(), SECTION)
        if os.path.isdir(section_dir):
            shutil.rmtree(section_dir)
