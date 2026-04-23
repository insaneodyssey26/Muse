"""
Download manager for offline playback.

Downloads songs with full ID3 metadata and cover art, stores them in
~/Music/YouTube Music/Artist/Album/NN - Title.ext

Never overwrites existing files. Tracks downloads in SQLite.
Stores videoId and other YTM identifiers in metadata custom tags.
"""

import os
import re
import json
import sqlite3
import threading
import time
import requests
from gi.repository import GLib, GObject

# Format config
FORMATS = {
    "opus": {"ext": "opus", "ydl_format": "bestaudio/best", "ffmpeg_codec": "libopus"},
    "mp3": {"ext": "mp3", "ydl_format": "bestaudio/best", "ffmpeg_codec": "libmp3lame"},
    "m4a": {"ext": "m4a", "ydl_format": "bestaudio/best", "ffmpeg_codec": "aac"},
    "flac": {"ext": "flac", "ydl_format": "bestaudio/best", "ffmpeg_codec": "flac"},
    "ogg": {"ext": "ogg", "ydl_format": "bestaudio/best", "ffmpeg_codec": "libvorbis"},
}

DEFAULT_FORMAT = "opus"

# Folder layout options for downloaded files, relative to the music dir.
FOLDER_STRUCTURES = ("artist_album", "artist", "flat")
DEFAULT_FOLDER_STRUCTURE = "artist_album"

# Temp-dir prefix lets us identify our own crash debris at startup without
# trampling unrelated files in /tmp.
TMP_PREFIX = "mixtapes_dl_"
TMP_STALE_SECONDS = 3600  # only sweep dirs untouched for an hour — avoids
                          # clobbering a parallel running instance's work.


def _sanitize_filename(name):
    """Remove characters that are invalid in filenames."""
    if not name:
        return "Unknown"
    name = re.sub(r'[<>:"/\\|?*]', "", name)
    name = name.strip(". ")
    return name or "Unknown"


def _get_prefs():
    path = os.path.join(GLib.get_user_data_dir(), "muse", "prefs.json")
    try:
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_prefs(prefs):
    path = os.path.join(GLib.get_user_data_dir(), "muse", "prefs.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(prefs, f)


def get_preferred_format():
    return _get_prefs().get("download_format", DEFAULT_FORMAT)


def set_preferred_format(fmt):
    if fmt in FORMATS:
        prefs = _get_prefs()
        prefs["download_format"] = fmt
        _save_prefs(prefs)


def get_folder_structure():
    val = _get_prefs().get("download_folder_structure", DEFAULT_FOLDER_STRUCTURE)
    return val if val in FOLDER_STRUCTURES else DEFAULT_FOLDER_STRUCTURE


def set_folder_structure(structure):
    """Returns True if the structure pref was actually changed."""
    if structure not in FOLDER_STRUCTURES:
        return False
    prefs = _get_prefs()
    if prefs.get("download_folder_structure", DEFAULT_FOLDER_STRUCTURE) == structure:
        return False
    prefs["download_folder_structure"] = structure
    _save_prefs(prefs)
    return True


def _build_download_dir(music_dir, artist_str, album, structure):
    """Build the destination directory based on the chosen folder structure.

    Falls back gracefully when album metadata is missing: 'artist_album'
    degrades to 'artist' rather than creating an 'Unknown' bucket."""
    if structure == "flat":
        return music_dir
    artist_dir = _sanitize_filename(artist_str.split(",")[0].strip())
    if structure == "artist_album" and album:
        return os.path.join(music_dir, artist_dir, _sanitize_filename(album))
    return os.path.join(music_dir, artist_dir)


def get_music_dir():
    """Get the download directory: ~/Music/YouTube Music/"""
    music = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_MUSIC)
    if not music:
        music = os.path.expanduser("~/Music")
    return os.path.join(music, "YouTube Music")


_download_db_instance = None


def get_download_db():
    """Get the shared DownloadDB singleton."""
    global _download_db_instance
    if _download_db_instance is None:
        _download_db_instance = DownloadDB()
    return _download_db_instance


class DownloadDB:
    """SQLite database for tracking downloads."""

    def __init__(self):
        db_dir = os.path.join(get_music_dir(), ".mixtapes")
        os.makedirs(db_dir, exist_ok=True)
        self._db_path = os.path.join(db_dir, "library.db")
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self):
        """Create a DB connection, ensuring the parent directory exists."""
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        return sqlite3.connect(self._db_path)

    def _init_db(self):
        with self._lock:
            conn = self._connect()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS downloads (
                    video_id TEXT PRIMARY KEY,
                    title TEXT,
                    artist TEXT,
                    album TEXT,
                    album_id TEXT,
                    track_number INTEGER,
                    duration_seconds INTEGER,
                    file_path TEXT,
                    cover_path TEXT,
                    thumbnail_url TEXT,
                    downloaded_at TEXT,
                    file_size INTEGER,
                    format TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS library_cache (
                    playlist_id TEXT PRIMARY KEY,
                    title TEXT,
                    author TEXT,
                    track_count INTEGER,
                    last_synced TEXT,
                    tracks_json TEXT,
                    meta_json TEXT
                )
            """)
            # For DBs created before meta_json existed:
            try:
                conn.execute("ALTER TABLE library_cache ADD COLUMN meta_json TEXT")
            except sqlite3.OperationalError:
                pass
            conn.commit()
            conn.close()

    def is_downloaded(self, video_id):
        if not video_id:
            return False
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT file_path FROM downloads WHERE video_id = ?", (video_id,)
            ).fetchone()
            conn.close()
            if row and row[0]:
                return os.path.exists(row[0])
            return False

    def get_local_path(self, video_id):
        if not video_id:
            return None
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT file_path FROM downloads WHERE video_id = ?", (video_id,)
            ).fetchone()
            conn.close()
            if row and row[0] and os.path.exists(row[0]):
                return row[0]
            return None

    def add_download(self, video_id, title, artist, album, album_id,
                     track_number, duration_seconds, file_path, cover_path,
                     thumbnail_url, file_size, fmt):
        with self._lock:
            conn = self._connect()
            conn.execute("""
                INSERT OR REPLACE INTO downloads
                (video_id, title, artist, album, album_id, track_number,
                 duration_seconds, file_path, cover_path, thumbnail_url,
                 downloaded_at, file_size, format)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (video_id, title, artist, album, album_id, track_number,
                  duration_seconds, file_path, cover_path, thumbnail_url,
                  time.strftime("%Y-%m-%dT%H:%M:%S"), file_size, fmt))
            conn.commit()
            conn.close()

    def get_all_downloads(self):
        with self._lock:
            conn = self._connect()
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM downloads ORDER BY downloaded_at DESC").fetchall()
            conn.close()
            return [dict(r) for r in rows]

    def remove_download(self, video_id):
        with self._lock:
            conn = self._connect()
            conn.execute("DELETE FROM downloads WHERE video_id = ?", (video_id,))
            conn.commit()
            conn.close()

    def update_file_path(self, video_id, new_path):
        with self._lock:
            conn = self._connect()
            conn.execute(
                "UPDATE downloads SET file_path = ? WHERE video_id = ?",
                (new_path, video_id),
            )
            conn.commit()
            conn.close()

    def get_video_for_path(self, file_path):
        """Return the video_id of whichever row owns `file_path`, or None."""
        if not file_path:
            return None
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT video_id FROM downloads WHERE file_path = ?", (file_path,)
            ).fetchone()
            conn.close()
            return row[0] if row else None

    # ── Library cache ────────────────────────────────────────────────────────

    def _ensure_meta_json_column(self, conn):
        """Add the meta_json column if this DB was created before we stored
        rich playlist metadata. Safe to call repeatedly."""
        try:
            conn.execute("ALTER TABLE library_cache ADD COLUMN meta_json TEXT")
        except sqlite3.OperationalError:
            pass  # already exists

    def cache_playlist(self, playlist_id, title, author, track_count, tracks, meta=None):
        """Cache a playlist's track listing + rich metadata for offline
        browsing and optimistic render on next open.

        The cache is a prefetch — it must never regress. If an entry
        already exists with MORE tracks than the incoming write, we keep
        the existing one. That protects against partial fetches (e.g. an
        initial limit=200 call overwriting a previously-stored full 810
        track set). Callers that genuinely want to shrink the cache
        (after a playlist edit) should explicitly invalidate first.

        `meta` is an optional dict. If provided it's JSON-serialized into
        the `meta_json` column and should contain fields like `description`,
        `year`, `privacy`, `duration_seconds`, `thumbnails`, and
        `author_raw` (the original artist-dict list)."""
        # Never write the cache when offline — offline responses are
        # themselves reconstructed from the cache, and persisting them
        # would trigger the regression guard for legitimately-shrunken
        # data (playlist edits) and overwrite meta fields with stale
        # values.
        try:
            from ui.utils import is_online as _is_online
            if not _is_online():
                return
        except Exception:
            pass
        with self._lock:
            conn = self._connect()
            self._ensure_meta_json_column(conn)

            # Regression check — look up what's already cached.
            existing_count = 0
            try:
                row = conn.execute(
                    "SELECT tracks_json FROM library_cache WHERE playlist_id = ?",
                    (playlist_id,),
                ).fetchone()
                if row and row[0]:
                    existing_count = len(json.loads(row[0]))
            except (json.JSONDecodeError, sqlite3.OperationalError):
                existing_count = 0

            new_count = len(tracks) if tracks else 0
            if existing_count > new_count:
                print(
                    f"[DB] cache_playlist: skipping regression "
                    f"({new_count} < existing {existing_count}) for {playlist_id}"
                )
                conn.close()
                return

            try:
                meta_json = json.dumps(meta or {}, ensure_ascii=False)
            except (TypeError, ValueError):
                meta_json = "{}"
            conn.execute("""
                INSERT OR REPLACE INTO library_cache
                (playlist_id, title, author, track_count, last_synced, tracks_json, meta_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                playlist_id, title, author, track_count,
                time.strftime("%Y-%m-%dT%H:%M:%S"),
                json.dumps(tracks, ensure_ascii=False) if tracks else "[]",
                meta_json,
            ))
            conn.commit()
            conn.close()

    def invalidate_playlist_cache(self, playlist_id):
        """Drop a playlist's cache row entirely. Callers that shrink the
        playlist (delete songs, remove playlist from library) should call
        this before writing a smaller entry — cache_playlist() ignores
        regression writes otherwise."""
        if not playlist_id:
            return
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "DELETE FROM library_cache WHERE playlist_id = ?", (playlist_id,)
                )
                conn.commit()
            finally:
                conn.close()

    def get_cached_playlist(self, playlist_id):
        """Get cached playlist data. Returns dict or None. Dict has the
        standard columns plus `tracks` (list) and `meta` (dict)."""
        with self._lock:
            conn = self._connect()
            self._ensure_meta_json_column(conn)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM library_cache WHERE playlist_id = ?", (playlist_id,)
            ).fetchone()
            conn.close()
            if row:
                result = dict(row)
                try:
                    result["tracks"] = json.loads(result.get("tracks_json", "[]"))
                except (json.JSONDecodeError, TypeError):
                    result["tracks"] = []
                try:
                    result["meta"] = json.loads(result.get("meta_json") or "{}")
                except (json.JSONDecodeError, TypeError):
                    result["meta"] = {}
                return result
            return None

    def get_all_cached_playlists(self):
        """Get all cached playlists for offline library browsing."""
        with self._lock:
            conn = self._connect()
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT playlist_id, title, author, track_count, last_synced FROM library_cache ORDER BY title"
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]

    def cache_library_playlists(self, playlists):
        """Cache the list of library playlists (not their tracks)."""
        with self._lock:
            conn = self._connect()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS library_playlists_cache (
                    id INTEGER PRIMARY KEY,
                    data_json TEXT,
                    last_synced TEXT
                )
            """)
            conn.execute("DELETE FROM library_playlists_cache")
            conn.execute("""
                INSERT INTO library_playlists_cache (id, data_json, last_synced)
                VALUES (1, ?, ?)
            """, (
                json.dumps(playlists, ensure_ascii=False),
                time.strftime("%Y-%m-%dT%H:%M:%S"),
            ))
            conn.commit()
            conn.close()

    def get_cached_library_playlists(self):
        """Get cached library playlists list for offline browsing."""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT data_json FROM library_playlists_cache WHERE id = 1"
                ).fetchone()
                conn.close()
                if row:
                    return json.loads(row[0])
            except Exception:
                conn.close()
            return None

    def cache_history(self, tracks):
        """Cache the listening-history track list so HistoryPage can
        render instantly on next open while the fresh fetch runs."""
        try:
            from ui.utils import is_online as _is_online
            if not _is_online():
                return
        except Exception:
            pass
        with self._lock:
            conn = self._connect()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS history_cache (
                    id INTEGER PRIMARY KEY,
                    data_json TEXT,
                    last_synced TEXT
                )
            """)
            conn.execute("DELETE FROM history_cache")
            conn.execute("""
                INSERT INTO history_cache (id, data_json, last_synced)
                VALUES (1, ?, ?)
            """, (
                json.dumps(tracks, ensure_ascii=False),
                time.strftime("%Y-%m-%dT%H:%M:%S"),
            ))
            conn.commit()
            conn.close()

    def get_cached_history(self):
        """Return cached history tracks, or None if nothing is cached."""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT data_json FROM history_cache WHERE id = 1"
                ).fetchone()
                conn.close()
                if row:
                    return json.loads(row[0])
            except Exception:
                conn.close()
            return None

    def remove_from_history_cache(self, video_id):
        """Drop a single track from the cached history list. Used after
        a user removes a history entry so the cache doesn't resurrect
        the removed track on the next cold open."""
        if not video_id:
            return
        cached = self.get_cached_history()
        if not cached:
            return
        filtered = [t for t in cached if t.get("videoId") != video_id]
        if len(filtered) == len(cached):
            return
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("""
                    UPDATE history_cache
                    SET data_json = ?, last_synced = ?
                    WHERE id = 1
                """, (
                    json.dumps(filtered, ensure_ascii=False),
                    time.strftime("%Y-%m-%dT%H:%M:%S"),
                ))
                conn.commit()
            finally:
                conn.close()

    def cache_library_albums(self, albums):
        """Cache the list of library albums."""
        with self._lock:
            conn = self._connect()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS library_albums_cache (
                    id INTEGER PRIMARY KEY,
                    data_json TEXT,
                    last_synced TEXT
                )
            """)
            conn.execute("DELETE FROM library_albums_cache")
            conn.execute("""
                INSERT INTO library_albums_cache (id, data_json, last_synced)
                VALUES (1, ?, ?)
            """, (
                json.dumps(albums, ensure_ascii=False),
                time.strftime("%Y-%m-%dT%H:%M:%S"),
            ))
            conn.commit()
            conn.close()

    def get_cached_library_albums(self):
        """Get cached library albums list for offline browsing."""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT data_json FROM library_albums_cache WHERE id = 1"
                ).fetchone()
                conn.close()
                if row:
                    return json.loads(row[0])
            except Exception:
                conn.close()
            return None

    def cache_library_artists(self, artists):
        """Cache the list of library subscriptions."""
        with self._lock:
            conn = self._connect()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS library_artists_cache (
                    id INTEGER PRIMARY KEY,
                    data_json TEXT,
                    last_synced TEXT
                )
            """)
            conn.execute("DELETE FROM library_artists_cache")
            conn.execute("""
                INSERT INTO library_artists_cache (id, data_json, last_synced)
                VALUES (1, ?, ?)
            """, (
                json.dumps(artists, ensure_ascii=False),
                time.strftime("%Y-%m-%dT%H:%M:%S"),
            ))
            conn.commit()
            conn.close()

    def get_cached_library_artists(self):
        """Get cached library artists for offline browsing."""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT data_json FROM library_artists_cache WHERE id = 1"
                ).fetchone()
                conn.close()
                if row:
                    return json.loads(row[0])
            except Exception:
                conn.close()
            return None

    # ── Uploads cache ────────────────────────────────────────────────────────

    def cache_uploads(self, albums, artists):
        """Cache the list of uploaded albums + artists."""
        with self._lock:
            conn = self._connect()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS uploads_cache (
                    id INTEGER PRIMARY KEY,
                    albums_json TEXT,
                    artists_json TEXT,
                    last_synced TEXT
                )
            """)
            conn.execute("DELETE FROM uploads_cache")
            conn.execute("""
                INSERT INTO uploads_cache (id, albums_json, artists_json, last_synced)
                VALUES (1, ?, ?, ?)
            """, (
                json.dumps(albums or [], ensure_ascii=False),
                json.dumps(artists or [], ensure_ascii=False),
                time.strftime("%Y-%m-%dT%H:%M:%S"),
            ))
            conn.commit()
            conn.close()

    def get_cached_uploads(self):
        """Returns (albums, artists) lists from cache, or (None, None)."""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT albums_json, artists_json FROM uploads_cache WHERE id = 1"
                ).fetchone()
                conn.close()
                if row:
                    return (
                        json.loads(row[0]) if row[0] else [],
                        json.loads(row[1]) if row[1] else [],
                    )
            except Exception:
                conn.close()
            return None, None


class DownloadManager(GObject.Object):
    """Manages song downloads with metadata tagging."""

    __gsignals__ = {
        "progress": (GObject.SignalFlags.RUN_FIRST, None, (int, int, str)),  # done, total, current_title
        "complete": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "item-done": (GObject.SignalFlags.RUN_FIRST, None, (str, bool, str)),  # video_id, success, message
        "item-progress": (GObject.SignalFlags.RUN_FIRST, None, (str, float)),  # video_id, fraction 0.0-1.0
        "item-queued": (GObject.SignalFlags.RUN_FIRST, None, (str,)),  # video_id
        "download-removed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),  # video_id
    }

    def __init__(self, client):
        super().__init__()
        self.client = client
        self.db = DownloadDB()
        self._queue = []
        self._lock = threading.Lock()
        self._migration_lock = threading.Lock()
        self._downloading = False
        self._total = 0
        self._done = 0
        self._pending_playlists = []  # [{id, title, tracks, thumb_url}]
        self._sweep_stale_tmp_dirs()

    @staticmethod
    def _sweep_stale_tmp_dirs():
        """Clean up mkdtemp debris from prior crashes. Skips anything touched
        within TMP_STALE_SECONDS so a parallel running instance isn't disturbed."""
        import shutil
        import tempfile
        tmp_root = tempfile.gettempdir()
        now = time.time()
        try:
            names = os.listdir(tmp_root)
        except OSError:
            return
        for name in names:
            if not name.startswith(TMP_PREFIX):
                continue
            path = os.path.join(tmp_root, name)
            try:
                if not os.path.isdir(path):
                    continue
                if now - os.path.getmtime(path) < TMP_STALE_SECONDS:
                    continue
            except OSError:
                continue
            shutil.rmtree(path, ignore_errors=True)

    def is_downloaded(self, video_id):
        return self.db.is_downloaded(video_id)

    def is_queued(self, video_id):
        """Check if a video is currently in the download queue."""
        with self._lock:
            return any(q["videoId"] == video_id for q in self._queue)

    def get_local_path(self, video_id):
        return self.db.get_local_path(video_id)

    def queue_track(self, track, album_title=None, album_id=None, track_number=None):
        """Add a track to the download queue."""
        vid = track.get("videoId")
        if not vid:
            return
        # Skip if already downloaded
        if self.db.is_downloaded(vid):
            return

        # Get album from the track itself, not the playlist name
        track_album = ""
        track_album_id = ""
        if isinstance(track.get("album"), dict):
            track_album = track["album"].get("name", "")
            track_album_id = track["album"].get("id", "")
        elif isinstance(track.get("album"), str):
            track_album = track["album"]

        item = {
            "videoId": vid,
            "title": track.get("title", "Unknown"),
            "artists": track.get("artists", []),
            "album": track_album,  # Real album, not playlist name
            "album_id": track_album_id or album_id or "",
            "playlist_title": album_title or "",  # Keep playlist name separately for m3u8
            "track_number": track_number,
            "duration_seconds": track.get("duration_seconds", 0),
            "thumbnails": track.get("thumbnails", []),
            "thumbnail_url": track.get("thumbnails", [{}])[-1].get("url", "") if track.get("thumbnails") else track.get("thumb", ""),
        }

        with self._lock:
            # Avoid duplicates in queue
            if not any(q["videoId"] == vid for q in self._queue):
                self._queue.append(item)
                self._total += 1
                GLib.idle_add(self.emit, "item-queued", vid)

    def queue_tracks(self, tracks, album_title=None, album_id=None):
        """Queue multiple tracks (album/playlist)."""
        for i, t in enumerate(tracks):
            self.queue_track(t, album_title, album_id, track_number=i + 1)

    def start(self):
        """Start processing the download queue."""
        if self._downloading:
            return
        if not self._queue:
            return
        self._downloading = True
        self._done = 0
        self._total = len(self._queue)
        threading.Thread(target=self._process_queue, daemon=True).start()

    def _make_cookie_file(self):
        """Create a temporary Netscape cookie file from ytmusicapi auth. Returns path or None."""
        import tempfile
        if not self.client.is_authenticated() or not self.client.api:
            return None
        cookie_str = self.client.api.headers.get("Cookie", "")
        if not cookie_str:
            return None
        cookie_fd, cookie_file = tempfile.mkstemp(suffix=".txt")
        now = int(time.time()) + 3600 * 24 * 365
        with os.fdopen(cookie_fd, "w") as f:
            f.write("# Netscape HTTP Cookie File\n")
            for part in cookie_str.split(";"):
                if "=" in part:
                    pair = part.strip().split("=", 1)
                    if len(pair) == 2:
                        f.write(f".youtube.com\tTRUE\t/\tTRUE\t{now}\t{pair[0]}\t{pair[1]}\n")
        return cookie_file

    def _download_one(self, item, fmt_key, fmt, music_dir):
        """Download and tag a single track. Called from worker threads."""
        from yt_dlp import YoutubeDL
        from ui.utils import get_high_res_url
        import shutil
        import tempfile

        vid = item["videoId"]
        title = item["title"]
        artist_list = item.get("artists", [])
        artist_str = ", ".join(a.get("name", "") for a in artist_list if isinstance(a, dict))
        if not artist_str:
            artist_str = "Unknown Artist"
        album = item.get("album", "") or ""
        track_num = None
        thumb_url = item.get("thumbnail_url", "")
        if thumb_url:
            thumb_url = get_high_res_url(thumb_url) or thumb_url

        album_artist = ""
        release_year = ""
        track_total = 0

        # Fetch real metadata from YTM if album or other info is missing
        if not album or not thumb_url or not artist_str or artist_str == "Unknown Artist":
            try:
                wp = self.client.get_watch_playlist(video_id=vid)
                if wp and wp.get("tracks"):
                    wt = wp["tracks"][0]
                    if not album:
                        wt_album = wt.get("album")
                        if isinstance(wt_album, dict):
                            album = wt_album.get("name", "")
                            if not item.get("album_id") and wt_album.get("id"):
                                item["album_id"] = wt_album["id"]
                        elif isinstance(wt_album, str):
                            album = wt_album
                    if not artist_str or artist_str == "Unknown Artist":
                        wt_artists = wt.get("artists", [])
                        if wt_artists:
                            artist_str = ", ".join(
                                a.get("name", "") for a in wt_artists if isinstance(a, dict)
                            ) or artist_str
                    if not title or title == "Unknown":
                        title = wt.get("title", title)
                    if not thumb_url:
                        wt_thumbs = wt.get("thumbnail")
                        if isinstance(wt_thumbs, list) and wt_thumbs:
                            thumb_url = get_high_res_url(wt_thumbs[-1].get("url", "")) or wt_thumbs[-1].get("url", "")
                        elif isinstance(wt_thumbs, dict):
                            wt_thumb_list = wt_thumbs.get("thumbnails", [])
                            if wt_thumb_list:
                                thumb_url = get_high_res_url(wt_thumb_list[-1]["url"]) or wt_thumb_list[-1]["url"]
            except Exception:
                pass

        # Fetch album-level metadata
        album_id = item.get("album_id", "")
        if album_id and album_id.startswith("MPRE"):
            try:
                album_data = self.client.get_album(album_id)
                if album_data:
                    ab_artists = album_data.get("artists", [])
                    if ab_artists:
                        album_artist = ", ".join(
                            a.get("name", "") for a in ab_artists if isinstance(a, dict)
                        )
                    release_year = album_data.get("year", "") or ""
                    track_total = album_data.get("trackCount", 0) or 0
                    ab_tracks = album_data.get("tracks", [])
                    for idx, at in enumerate(ab_tracks):
                        if at.get("videoId") == vid:
                            track_num = idx + 1
                            break
                    if track_num is None and ab_tracks:
                        for idx, at in enumerate(ab_tracks):
                            if at.get("title", "").lower() == title.lower():
                                track_num = idx + 1
                                break
                    if not album:
                        album = album_data.get("title", "")
                    if not thumb_url:
                        ab_thumbs = album_data.get("thumbnails", [])
                        if ab_thumbs:
                            thumb_url = get_high_res_url(ab_thumbs[-1]["url"]) or ab_thumbs[-1]["url"]
            except Exception:
                pass

        # Destination path honours the user-chosen folder structure.
        song_name = _sanitize_filename(title)
        filename = f"{song_name}.{fmt['ext']}"
        dir_path = _build_download_dir(
            music_dir, artist_str, album, get_folder_structure()
        )
        file_path = os.path.join(dir_path, filename)

        # Never overwrite. If the path is taken by a different video
        # (common in "flat" mode with two same-titled songs), disambiguate
        # by appending the videoId. If nobody owns it, claim it.
        if os.path.exists(file_path):
            owner = self.db.get_video_for_path(file_path)
            if owner and owner != vid:
                suffixed = f"{song_name} [{vid}].{fmt['ext']}"
                file_path = os.path.join(dir_path, suffixed)
                filename = suffixed
                if os.path.exists(file_path):
                    # The disambiguated path is also taken → claim as existing.
                    self.db.add_download(
                        vid, title, artist_str, album, item.get("album_id", ""),
                        track_num, item.get("duration_seconds", 0),
                        file_path, None, thumb_url,
                        os.path.getsize(file_path), fmt_key,
                    )
                    return vid, True, "Already exists", title
            else:
                self.db.add_download(
                    vid, title, artist_str, album, item.get("album_id", ""),
                    track_num, item.get("duration_seconds", 0),
                    file_path, None, thumb_url,
                    os.path.getsize(file_path), fmt_key,
                )
                return vid, True, "Already exists", title

        os.makedirs(dir_path, exist_ok=True)

        # Prefix lets _sweep_stale_tmp_dirs() identify crash debris at startup.
        tmp_dir = tempfile.mkdtemp(prefix=TMP_PREFIX)
        try:
            tmp_file = os.path.join(tmp_dir, f"download.{fmt['ext']}")

            def _ydl_progress_hook(d, _vid=vid):
                if d.get("status") == "downloading":
                    total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                    downloaded = d.get("downloaded_bytes", 0)
                    if total > 0:
                        frac = min(downloaded / total, 1.0)
                        GLib.idle_add(self.emit, "item-progress", _vid, frac)

            ydl_opts = {
                "format": fmt["ydl_format"],
                "outtmpl": tmp_file.rsplit(".", 1)[0] + ".%(ext)s",
                "quiet": True,
                "no_warnings": True,
                "js_runtimes": {"node": {}},
                "progress_hooks": [_ydl_progress_hook],
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": fmt_key if fmt_key != "m4a" else "aac",
                    "preferredquality": "0",
                }],
            }
            if self._shared_cookie_file:
                ydl_opts["cookiefile"] = self._shared_cookie_file
            if self.client.is_authenticated() and self.client.api:
                ua = self.client.api.headers.get("User-Agent")
                if ua:
                    ydl_opts["user_agent"] = ua

            url = f"https://music.youtube.com/watch?v={vid}"
            with YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

            # Find the downloaded file
            actual_file = None
            for f in os.listdir(tmp_dir):
                if f.startswith("download"):
                    actual_file = os.path.join(tmp_dir, f)
                    break

            if not actual_file or not os.path.exists(actual_file):
                raise Exception("Download produced no output file")

            actual_ext = actual_file.rsplit(".", 1)[-1]
            if actual_ext != fmt["ext"]:
                filename = f"{song_name}.{actual_ext}"
                file_path = os.path.join(dir_path, filename)

            # Download cover art for embedding (try fallbacks for video thumbnails)
            # YouTube returns a tiny 120x90 placeholder JPEG (~1KB) for missing qualities
            # instead of a real 404, so we check size > 5000 to skip those
            cover_data = None
            if thumb_url:
                from ui.utils import get_ytimg_fallbacks
                urls_to_try = [thumb_url] + get_ytimg_fallbacks(thumb_url)
                for try_url in urls_to_try:
                    try:
                        resp = requests.get(try_url, timeout=15)
                        if resp.status_code == 200 and len(resp.content) > 5000:
                            cover_data = resp.content
                            break
                    except Exception:
                        continue

            # Tag metadata
            self._tag_file(actual_file, actual_ext, title, artist_str, album,
                           track_num, track_total, vid, item.get("album_id", ""),
                           cover_data, item.get("duration_seconds", 0),
                           album_artist=album_artist, release_year=release_year)

            # Move to final location
            if not os.path.exists(file_path):
                shutil.move(actual_file, file_path)
            else:
                os.remove(actual_file)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        # Register in DB
        file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
        self.db.add_download(
            vid, title, artist_str, album, item.get("album_id", ""),
            track_num, item.get("duration_seconds", 0),
            file_path, None, thumb_url, file_size, actual_ext,
        )

        return vid, True, "Downloaded", title

    def _process_queue(self):
        from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
        # Pre-import yt-dlp on the main thread to avoid concurrent plugin registration
        import yt_dlp  # noqa: F401

        fmt_key = get_preferred_format()
        fmt = FORMATS.get(fmt_key, FORMATS[DEFAULT_FORMAT])
        music_dir = get_music_dir()
        max_workers = 3

        self._shared_cookie_file = self._make_cookie_file()

        try:
            # Pump work through the pool so items not yet picked up stay in
            # self._queue and can still be cancelled. Also means tracks added
            # mid-batch get processed in the same run.
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                in_flight = {}  # future -> item
                while True:
                    with self._lock:
                        while len(in_flight) < max_workers and self._queue:
                            item = self._queue.pop(0)
                            fut = pool.submit(
                                self._download_one, item, fmt_key, fmt, music_dir
                            )
                            in_flight[fut] = item

                    if not in_flight:
                        break

                    done_set, _ = wait(in_flight.keys(), return_when=FIRST_COMPLETED)
                    for fut in done_set:
                        item = in_flight.pop(fut)
                        vid = item["videoId"]
                        try:
                            vid, success, msg, title = fut.result()
                            self._done += 1
                            GLib.idle_add(self.emit, "progress", self._done, self._total, title)
                            GLib.idle_add(self.emit, "item-done", vid, success, msg)
                            if success:
                                self._update_playlists_for(vid)
                        except Exception as e:
                            self._done += 1
                            GLib.idle_add(self.emit, "progress", self._done, self._total, item.get("title", ""))
                            GLib.idle_add(self.emit, "item-done", vid, False, str(e)[:60])
                            print(f"[DOWNLOAD] Error downloading {item.get('title')}: {e}")
        finally:
            self._downloading = False
            self._total = 0
            self._done = 0
            self._pending_playlists = []
            # Clean up shared cookie file
            if self._shared_cookie_file and os.path.exists(self._shared_cookie_file):
                try:
                    os.remove(self._shared_cookie_file)
                except OSError:
                    pass
                self._shared_cookie_file = None
            GLib.idle_add(self.emit, "complete")

    def _tag_file(self, filepath, ext, title, artist, album, track_num,
                  track_total, video_id, album_id, cover_data_or_path,
                  duration_seconds, album_artist="", release_year=""):
        """Write ID3/Vorbis/MP4 metadata including YTM identifiers.
        cover_data_or_path can be bytes (raw image data) or a file path string."""
        try:
            import mutagen
            from mutagen.oggopus import OggOpus
            from mutagen.oggvorbis import OggVorbis
            from mutagen.flac import FLAC, Picture
            from mutagen.mp3 import MP3
            from mutagen.mp4 import MP4
            from mutagen.id3 import ID3, TIT2, TPE1, TPE2, TALB, TRCK, APIC, COMM, TXXX, TDRC

            cover_data = None
            if isinstance(cover_data_or_path, bytes):
                cover_data = cover_data_or_path
            elif isinstance(cover_data_or_path, str) and os.path.exists(cover_data_or_path):
                with open(cover_data_or_path, "rb") as f:
                    cover_data = f.read()

            # Custom comment with YTM identifiers
            ytm_comment = json.dumps({
                "videoId": video_id,
                "albumId": album_id,
                "source": "YouTube Music (Mixtapes)",
            })

            # Track number string: "3/12" or "3"
            trck_str = ""
            if track_num:
                trck_str = str(track_num)
                if track_total:
                    trck_str = f"{track_num}/{track_total}"

            if ext == "mp3":
                audio = MP3(filepath, ID3=ID3)
                try:
                    audio.add_tags()
                except mutagen.id3.error:
                    pass
                audio.tags.add(TIT2(encoding=3, text=title))
                audio.tags.add(TPE1(encoding=3, text=artist))
                audio.tags.add(TALB(encoding=3, text=album))
                if album_artist:
                    audio.tags.add(TPE2(encoding=3, text=album_artist))
                if trck_str:
                    audio.tags.add(TRCK(encoding=3, text=trck_str))
                if release_year:
                    audio.tags.add(TDRC(encoding=3, text=release_year))
                audio.tags.add(COMM(encoding=3, lang="eng", desc="ytm_metadata", text=ytm_comment))
                audio.tags.add(TXXX(encoding=3, desc="YTMUSIC_VIDEO_ID", text=video_id))
                if cover_data:
                    audio.tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=cover_data))
                audio.save()

            elif ext == "m4a" or ext == "aac":
                audio = MP4(filepath)
                audio["\xa9nam"] = title
                audio["\xa9ART"] = artist
                audio["\xa9alb"] = album
                if album_artist:
                    audio["aART"] = [album_artist]
                if track_num:
                    audio["trkn"] = [(track_num, track_total or 0)]
                if release_year:
                    audio["\xa9day"] = [release_year]
                audio["\xa9cmt"] = ytm_comment
                if cover_data:
                    from mutagen.mp4 import MP4Cover
                    audio["covr"] = [MP4Cover(cover_data, imageformat=MP4Cover.FORMAT_JPEG)]
                audio.save()

            elif ext in ("opus", "ogg"):
                if ext == "opus":
                    audio = OggOpus(filepath)
                else:
                    audio = OggVorbis(filepath)
                audio["title"] = title
                audio["artist"] = artist
                audio["album"] = album
                if album_artist:
                    audio["albumartist"] = album_artist
                if track_num:
                    audio["tracknumber"] = str(track_num)
                if track_total:
                    audio["tracktotal"] = str(track_total)
                if release_year:
                    audio["date"] = release_year
                audio["comment"] = ytm_comment
                audio["ytmusic_video_id"] = video_id
                if cover_data:
                    import base64
                    pic = Picture()
                    pic.data = cover_data
                    pic.type = 3
                    pic.mime = "image/jpeg"
                    pic.desc = "Cover"
                    audio["metadata_block_picture"] = base64.b64encode(pic.write()).decode("ascii")
                audio.save()

            elif ext == "flac":
                audio = FLAC(filepath)
                audio["title"] = title
                audio["artist"] = artist
                audio["album"] = album
                if album_artist:
                    audio["albumartist"] = album_artist
                if track_num:
                    audio["tracknumber"] = str(track_num)
                if track_total:
                    audio["tracktotal"] = str(track_total)
                if release_year:
                    audio["date"] = release_year
                audio["comment"] = ytm_comment
                audio["ytmusic_video_id"] = video_id
                if cover_data:
                    pic = Picture()
                    pic.data = cover_data
                    pic.type = 3
                    pic.mime = "image/jpeg"
                    pic.desc = "Cover"
                    audio.add_picture(pic)
                audio.save()

        except Exception as e:
            print(f"[DOWNLOAD] Tagging error for {filepath}: {e}")

    def register_playlist(self, playlist_id, title, tracks, thumb_url=None):
        """Register a playlist for incremental m3u8 generation.

        If the same playlist is registered again mid-download (user adds
        more tracks from the same playlist while the first batch is still
        processing), the new tracks are MERGED into the existing entry
        rather than replacing it. Replacing would leave the earlier
        batch's item-done completions looking themselves up in a track
        list that no longer contains them — the m3u writer would skip
        them, and those songs would never appear in the m3u.

        Additionally, if a cached copy of the full playlist exists (from
        an earlier PlaylistPage open), we hydrate the pending track list
        from it. That way a single-song download still has access to the
        full playlist order, which the m3u writer needs."""
        new_tracks = list(tracks or [])

        # Hydrate from cache so the pending playlist reflects the full
        # track set, not just what the caller happened to pass in.
        cached_tracks = []
        if playlist_id:
            try:
                cached = self.db.get_cached_playlist(playlist_id)
                if cached and cached.get("tracks"):
                    cached_tracks = cached["tracks"]
            except Exception:
                pass

        def _union_by_vid(base, extra):
            seen = {t.get("videoId") for t in base if t.get("videoId")}
            for t in extra:
                vid = t.get("videoId")
                if vid and vid not in seen:
                    base.append(t)
                    seen.add(vid)
                elif not vid:
                    base.append(t)
            return base

        merged = False
        if playlist_id:
            for existing in self._pending_playlists:
                if existing.get("id") == playlist_id:
                    existing["title"] = title or existing.get("title", "")
                    if thumb_url:
                        existing["thumb_url"] = thumb_url
                    _union_by_vid(existing["tracks"], cached_tracks)
                    _union_by_vid(existing["tracks"], new_tracks)
                    merged = True
                    # Swap local reference so the _write_playlist_m3u call
                    # below sees the fully-merged track list.
                    tracks = existing["tracks"]
                    break
        if not merged:
            combined = list(cached_tracks) if cached_tracks else []
            _union_by_vid(combined, new_tracks)
            self._pending_playlists.append({
                "id": playlist_id or "",
                "title": title,
                "tracks": combined,
                "thumb_url": thumb_url,
            })
            tracks = combined
        # Refresh the m3u now so re-requesting an already-downloaded
        # playlist still produces a playable m3u (no item-done signals fire
        # in that case).
        self._write_playlist_m3u(title, tracks, playlist_id)
        # The playlist cover is owned by PlaylistPage — it caches on open
        # and overwrites on every open. Downloading covers here would
        # fight that flow (and has produced bugs where a single-song
        # download wrote the song's thumbnail as the playlist cover).

    def _write_playlist_m3u(self, title, tracks, playlist_id=None):
        """Write the m3u for a playlist from scratch, in the playlist's
        default order, including only tracks already downloaded locally.

        No merging with the prior m3u — tracks removed from the source
        playlist must disappear from the m3u, and reordering must
        propagate. When `playlist_id` resolves to a cached playlist row
        we use that ordered list (authoritative); `tracks` is only the
        fallback when no cache row exists.

        Albums (MPRE*, OLAK*) are skipped — they're not playlists.
        """
        if playlist_id and (
            playlist_id.startswith("MPRE") or playlist_id.startswith("OLAK")
        ):
            return

        ordered_tracks = None
        if playlist_id:
            try:
                cached = self.db.get_cached_playlist(playlist_id)
                if cached and cached.get("tracks"):
                    ordered_tracks = cached["tracks"]
            except Exception:
                pass
        if not ordered_tracks:
            ordered_tracks = tracks or []

        music_dir = get_music_dir()
        playlists_dir = os.path.join(music_dir, "playlists")
        os.makedirs(playlists_dir, exist_ok=True)
        safe_name = _sanitize_filename(title)
        m3u_path = os.path.join(playlists_dir, f"{safe_name}.m3u8")

        entries = []
        for t in ordered_tracks:
            vid = t.get("videoId")
            if not vid:
                continue
            local = self.db.get_local_path(vid)
            if not (local and os.path.exists(local)):
                continue
            rel_path = os.path.relpath(local, playlists_dir)
            dur = t.get("duration_seconds", 0)
            song_title = t.get("title", "Unknown")
            artists = t.get("artists", [])
            artist = ", ".join(
                a.get("name", "") for a in artists if isinstance(a, dict)
            ) if artists else ""
            entries.append((f"#EXTINF:{dur},{artist} - {song_title}", rel_path))

        try:
            with open(m3u_path, "w", encoding="utf-8") as f:
                f.write("#EXTM3U\n")
                f.write(f"#PLAYLIST:{title}\n")
                for extinf, path in entries:
                    f.write(f"{extinf}\n")
                    f.write(f"{path}\n")
        except Exception as e:
            print(f"[DOWNLOAD] Error updating playlist {title}: {e}")

    def _update_playlists_for(self, video_id):
        """Update m3us for any pending playlist that contains this video."""
        for pl in self._pending_playlists:
            if not any(t.get("videoId") == video_id for t in pl.get("tracks", [])):
                continue
            self._write_playlist_m3u(pl["title"], pl["tracks"], pl.get("id"))

    # ── Cancel / Delete / Migrate ─────────────────────────────────────────

    def cancel_queued(self, video_id):
        """Remove an item that hasn't started downloading yet. Returns True
        if something was actually removed. Items already handed to the worker
        pool can't be cancelled mid-flight — yt_dlp has no cancellation hook.
        """
        if not video_id:
            return False
        removed = False
        with self._lock:
            new_queue = [q for q in self._queue if q["videoId"] != video_id]
            if len(new_queue) < len(self._queue):
                self._queue = new_queue
                # Keep _total >= _done so the progress fraction stays sane.
                self._total = max(self._total - 1, self._done)
                removed = True
        if removed:
            GLib.idle_add(self.emit, "item-done", video_id, False, "Cancelled")
            GLib.idle_add(self.emit, "progress", self._done, self._total, "")
        return removed

    def delete_download(self, video_id):
        """Remove a completed download from disk, the DB, and any m3u files."""
        if not video_id:
            return False
        file_path = self.db.get_local_path(video_id)
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError as e:
                print(f"[DOWNLOAD] Failed to remove {file_path}: {e}")
        if file_path:
            self._prune_empty_dirs(os.path.dirname(file_path))
        self.db.remove_download(video_id)
        self._prune_m3us_missing_files()
        GLib.idle_add(self.emit, "download-removed", video_id)
        return True

    def _prune_empty_dirs(self, start_dir):
        """Delete empty ancestor dirs up to (but not including) the music root."""
        music_dir = os.path.normpath(get_music_dir())
        d = os.path.normpath(start_dir) if start_dir else ""
        while d and os.path.isdir(d) and d != music_dir:
            try:
                # commonpath() raises if the paths aren't on the same drive;
                # treat that as "outside the music root" and stop.
                if os.path.commonpath([d, music_dir]) != music_dir:
                    break
            except ValueError:
                break
            try:
                os.rmdir(d)
            except OSError:
                break
            d = os.path.dirname(d)

    def _prune_m3us_missing_files(self):
        """Remove entries from all m3u files whose target no longer exists."""
        playlists_dir = os.path.join(get_music_dir(), "playlists")
        if not os.path.isdir(playlists_dir):
            return
        for name in os.listdir(playlists_dir):
            if not name.lower().endswith((".m3u", ".m3u8")):
                continue
            m3u_path = os.path.join(playlists_dir, name)
            try:
                with open(m3u_path, "r", encoding="utf-8") as f:
                    lines = [ln.rstrip("\n") for ln in f]
                out = []
                i = 0
                while i < len(lines):
                    line = lines[i]
                    if line.startswith("#EXTINF:") and i + 1 < len(lines):
                        rel = lines[i + 1]
                        abs_path = os.path.normpath(os.path.join(playlists_dir, rel))
                        if os.path.exists(abs_path):
                            out.append(line)
                            out.append(rel)
                        i += 2
                    else:
                        out.append(line)
                        i += 1
                with open(m3u_path, "w", encoding="utf-8") as f:
                    for ln in out:
                        f.write(ln + "\n")
            except Exception as e:
                print(f"[DOWNLOAD] Error pruning m3u {m3u_path}: {e}")

    def migrate_folder_structure(self):
        """Reorganize existing downloads to match the current folder_structure
        pref. Blocks; call from a background thread. Returns (moved, errors).
        Serialized so overlapping pref toggles don't race each other."""
        if not self._migration_lock.acquire(blocking=False):
            return 0, 0
        try:
            return self._migrate_folder_structure_locked()
        finally:
            self._migration_lock.release()

    def _migrate_folder_structure_locked(self):
        import shutil

        structure = get_folder_structure()
        music_dir = get_music_dir()
        path_map = {}
        moved = 0
        errors = 0

        for row in self.db.get_all_downloads():
            vid = row.get("video_id")
            old_path = row.get("file_path")
            if not old_path or not os.path.exists(old_path):
                continue
            artist = row.get("artist") or "Unknown Artist"
            album = row.get("album") or ""
            new_dir = _build_download_dir(music_dir, artist, album, structure)
            filename = os.path.basename(old_path)
            new_path = os.path.join(new_dir, filename)

            if os.path.normpath(new_path) == os.path.normpath(old_path):
                continue

            # Don't clobber an unrelated file already at the destination.
            if os.path.exists(new_path):
                base, ext = os.path.splitext(filename)
                suffixed = f"{base} [{vid}]{ext}" if vid else filename
                new_path = os.path.join(new_dir, suffixed)
                if os.path.exists(new_path):
                    print(f"[DOWNLOAD] Skipping migration for {old_path}: destination taken")
                    errors += 1
                    continue

            try:
                os.makedirs(new_dir, exist_ok=True)
                shutil.move(old_path, new_path)
                self.db.update_file_path(vid, new_path)
                path_map[os.path.normpath(old_path)] = os.path.normpath(new_path)
                self._prune_empty_dirs(os.path.dirname(old_path))
                moved += 1
            except Exception as e:
                print(f"[DOWNLOAD] Migration failed for {old_path}: {e}")
                errors += 1

        if path_map:
            self._rewrite_m3us_with_map(path_map)

        return moved, errors

    def _rewrite_m3us_with_map(self, path_map):
        """Update m3u relative paths to point at the new locations."""
        playlists_dir = os.path.join(get_music_dir(), "playlists")
        if not os.path.isdir(playlists_dir):
            return
        for name in os.listdir(playlists_dir):
            if not name.lower().endswith((".m3u", ".m3u8")):
                continue
            m3u_path = os.path.join(playlists_dir, name)
            try:
                with open(m3u_path, "r", encoding="utf-8") as f:
                    lines = [ln.rstrip("\n") for ln in f]
                out = []
                changed = False
                i = 0
                while i < len(lines):
                    line = lines[i]
                    if line.startswith("#EXTINF:") and i + 1 < len(lines):
                        out.append(line)
                        rel = lines[i + 1]
                        abs_old = os.path.normpath(os.path.join(playlists_dir, rel))
                        if abs_old in path_map:
                            out.append(os.path.relpath(path_map[abs_old], playlists_dir))
                            changed = True
                        else:
                            out.append(rel)
                        i += 2
                    else:
                        out.append(line)
                        i += 1
                if changed:
                    with open(m3u_path, "w", encoding="utf-8") as f:
                        for ln in out:
                            f.write(ln + "\n")
            except Exception as e:
                print(f"[DOWNLOAD] Error rewriting m3u {m3u_path}: {e}")

    @staticmethod
    def extract_cover_from_file(filepath):
        """Extract embedded cover art from an audio file. Returns bytes or None."""
        if not filepath or not os.path.exists(filepath):
            return None
        try:
            ext = filepath.rsplit(".", 1)[-1].lower()

            if ext == "mp3":
                from mutagen.mp3 import MP3
                from mutagen.id3 import ID3
                audio = MP3(filepath, ID3=ID3)
                for tag in audio.tags.values():
                    if hasattr(tag, 'data') and hasattr(tag, 'mime') and 'image' in str(getattr(tag, 'mime', '')):
                        return tag.data

            elif ext in ("m4a", "aac"):
                from mutagen.mp4 import MP4
                audio = MP4(filepath)
                covrs = audio.get("covr", [])
                if covrs:
                    return bytes(covrs[0])

            elif ext in ("opus", "ogg"):
                import base64
                from mutagen.oggopus import OggOpus
                from mutagen.oggvorbis import OggVorbis
                from mutagen.flac import Picture
                try:
                    audio = OggOpus(filepath) if ext == "opus" else OggVorbis(filepath)
                except Exception:
                    return None
                pics = audio.get("metadata_block_picture", [])
                if pics:
                    pic = Picture(base64.b64decode(pics[0]))
                    return pic.data

            elif ext == "flac":
                from mutagen.flac import FLAC
                audio = FLAC(filepath)
                if audio.pictures:
                    return audio.pictures[0].data

        except Exception as e:
            print(f"[DOWNLOAD] Cover extraction error for {filepath}: {e}")
        return None
