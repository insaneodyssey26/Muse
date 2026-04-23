"""
Stream URL cache for faster song playback.

Caches the resolved stream URLs from yt-dlp so replaying a recently
played song skips the expensive yt-dlp extraction (~5-10s) and starts
streaming immediately (<1s).

URLs expire after a configurable TTL since YouTube stream URLs are
time-limited.
"""

import os
import json
import time
import threading
from gi.repository import GLib


class StreamCache:
    # Max cached entries (LRU eviction)
    MAX_ENTRIES = 500
    # Stream URLs typically expire after ~6 hours, use 5h to be safe
    URL_TTL_SECONDS = 5 * 3600

    def __init__(self):
        cache_dir = os.path.join(GLib.get_user_cache_dir(), "muse", "streams")
        os.makedirs(cache_dir, exist_ok=True)
        self._cache_dir = cache_dir
        self._lock = threading.Lock()

    def _path_for(self, video_id):
        return os.path.join(self._cache_dir, f"{video_id}.json")

    def get(self, video_id):
        """Get a cached stream URL if it exists and hasn't expired.
        Returns the stream URL string or None."""
        if not video_id:
            return None
        path = self._path_for(video_id)
        try:
            if not os.path.exists(path):
                return None
            with self._lock:
                with open(path, "r") as f:
                    data = json.load(f)
            # Check expiry
            if time.time() - data.get("timestamp", 0) > self.URL_TTL_SECONDS:
                try:
                    os.remove(path)
                except OSError:
                    pass
                return None
            # Touch for LRU
            try:
                os.utime(path, None)
            except OSError:
                pass
            return data.get("url")
        except (OSError, json.JSONDecodeError):
            return None

    def put(self, video_id, stream_url):
        """Cache a stream URL."""
        if not video_id or not stream_url:
            return
        path = self._path_for(video_id)
        try:
            with self._lock:
                with open(path, "w") as f:
                    json.dump({
                        "url": stream_url,
                        "timestamp": time.time(),
                    }, f)
            self._evict_old()
        except OSError as e:
            print(f"[CACHE] Error saving stream URL for {video_id}: {e}")

    def invalidate(self, video_id):
        """Drop the cache entry for a video — called when a previously
        cached URL has 503'd mid-play so the next attempt re-resolves
        via yt-dlp instead of reusing the dead entry."""
        if not video_id:
            return
        path = self._path_for(video_id)
        try:
            with self._lock:
                if os.path.exists(path):
                    os.remove(path)
        except OSError:
            pass

    def _evict_old(self):
        """Remove oldest entries if cache exceeds MAX_ENTRIES."""
        try:
            entries = []
            for fname in os.listdir(self._cache_dir):
                fpath = os.path.join(self._cache_dir, fname)
                if os.path.isfile(fpath) and fname.endswith(".json"):
                    entries.append((os.path.getmtime(fpath), fpath))

            if len(entries) <= self.MAX_ENTRIES:
                return

            entries.sort()
            for _, fpath in entries[: len(entries) - self.MAX_ENTRIES]:
                try:
                    os.remove(fpath)
                except OSError:
                    pass
        except OSError:
            pass

    def clear(self):
        """Remove all cached stream URLs."""
        try:
            for fname in os.listdir(self._cache_dir):
                fpath = os.path.join(self._cache_dir, fname)
                if os.path.isfile(fpath) and fname.endswith(".json"):
                    os.remove(fpath)
        except OSError:
            pass
