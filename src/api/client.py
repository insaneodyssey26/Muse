import os
import json
from ytmusicapi import YTMusic
import ytmusicapi.navigation
from gi.repository import GLib

# Monkeypatch ytmusicapi.navigation.nav to handle UI changes like musicImmersiveHeaderRenderer
_original_nav = ytmusicapi.navigation.nav


def robust_nav(root, items, none_if_absent=False):
    if root is None:
        return None
    try:
        current = root
        for i, k in enumerate(items):
            # Fallback for musicVisualHeaderRenderer -> musicImmersiveHeaderRenderer
            if (
                k == "musicVisualHeaderRenderer"
                and isinstance(current, dict)
                and k not in current
                and "musicImmersiveHeaderRenderer" in current
            ):
                k = "musicImmersiveHeaderRenderer"
            # Fallback for musicDetailHeaderRenderer -> musicResponsiveHeaderRenderer
            if (
                k == "musicDetailHeaderRenderer"
                and isinstance(current, dict)
                and k not in current
                and "musicResponsiveHeaderRenderer" in current
            ):
                k = "musicResponsiveHeaderRenderer"
            if k == "runs" and isinstance(current, dict) and k not in current:
                if none_if_absent:
                    return None
                if i < len(items) - 1 and items[i + 1] == 0:
                    current = [{"text": ""}]
                    continue
                else:
                    current = []
                    continue

            current = current[k]
        return current
    except (KeyError, IndexError, TypeError):
        if none_if_absent:
            return None
        return _original_nav(root, items, none_if_absent)


ytmusicapi.navigation.nav = robust_nav


class MusicClient:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(MusicClient, cls).__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self):
        self.api = None
        data_dir = os.path.join(GLib.get_user_data_dir(), "muse")
        self.auth_path = os.path.join(data_dir, "headers_auth.json")
        self._is_authed = False
        self._playlist_cache = {}  # Cache fully-fetched playlists
        self._download_db = None  # Lazy-loaded DownloadDB for offline cache
        self._user_info = None  # Cache for account info
        self._subscribed_artists = set()  # Set of channel IDs
        self._library_playlists = []  # Cache for editable playlists
        self._library_playlist_ids = set()  # IDs of all library playlists
        self._library_album_ids = set()  # Browse IDs of all library albums
        self.try_login(skip_validation=True)

    @property
    def _offline_db(self):
        if self._download_db is None:
            try:
                from player.downloads import DownloadDB

                self._download_db = DownloadDB()
            except Exception:
                pass
        return self._download_db

    def try_login(self, skip_validation=False):
        # 1. Try saved headers_auth.json (Preferred)
        if os.path.exists(self.auth_path):
            try:
                print(f"Loading saved auth from {self.auth_path}")
                # Load headers to check/fix them before init
                with open(self.auth_path, "r") as f:
                    headers = json.load(f)

                # Normalize keys for ytmusicapi and remove Bearer tokens
                headers = self._normalize_headers(headers)

                self.api = YTMusic(auth=headers)
                if skip_validation:
                    # Assume valid for now; caller will validate asynchronously
                    print("Auth loaded (validation deferred).")
                    self._is_authed = True
                    return True
                if self.validate_session():
                    print("Authenticated via saved session.")
                    self._is_authed = True
                    return True
                else:
                    print("Saved session invalid.")
            except Exception as e:
                print(f"Failed to load saved session: {e}")

        # 2. Check for browser.json in cwd (Manually provided)
        browser_path = os.path.join(os.getcwd(), "browser.json")
        if os.path.exists(browser_path):
            print(f"Found browser.json at {browser_path}. Importing...")
            if self.login(browser_path):
                return True

        # 3. Fallback
        print("Falling back to unauthenticated mode.")
        self.api = YTMusic()
        self._is_authed = False
        return False

    def _normalize_headers(self, headers):
        """
        Ensures headers match what ytmusicapi expects for a browser session.
        Preserves Authorization (if not Bearer) and ensures required keys exist.
        """
        print("Standardizing headers for ytmusicapi...")
        normalized = {}
        for k, v in headers.items():
            lk = k.lower().replace("-", "_")

            # Whitelist standard browser headers with Title-Case
            if lk == "cookie":
                normalized["Cookie"] = v
            elif lk == "user_agent":
                normalized["User-Agent"] = v
            elif lk == "accept_language":
                normalized["Accept-Language"] = v
            elif lk == "content_type":
                normalized["Content-Type"] = v
            elif lk == "authorization":
                # Only keep if it's NOT an OAuth Bearer token
                if v.lower().startswith("bearer"):
                    print("  [Security] Dropping OAuth Bearer token.")
                else:
                    normalized["Authorization"] = v
            elif lk == "x_goog_authuser":
                normalized["X-Goog-AuthUser"] = v
            # Blacklist OAuth-triggering keys
            elif lk in [
                "oauth_credentials",
                "client_id",
                "client_secret",
                "access_token",
                "refresh_token",
                "token_type",
                "expires_at",
                "expires_in",
            ]:
                print(f"  [Security] Dropping OAuth-triggering field: {k}")
                continue
            else:
                # Title-Case other headers as a safe default
                nk = "-".join([part.capitalize() for part in k.split("-")])
                if nk.lower().startswith("x-"):
                    nk = k  # Preserve X-Goog etc. original casing
                normalized[nk] = v

        # Cleanup duplicates that might have been created by normalization
        final = {}
        for k, v in normalized.items():
            if k in [
                "Cookie",
                "User-Agent",
                "Accept-Language",
                "Content-Type",
                "Authorization",
                "X-Goog-AuthUser",
            ]:
                final[k] = v
            elif k.lower() not in [
                "cookie",
                "user-agent",
                "accept-language",
                "content-type",
                "authorization",
                "x-goog-authuser",
            ]:
                final[k] = v

        # Ensure minimal required headers for stability
        if "Accept-Language" not in final:
            final["Accept-Language"] = "en-US,en;q=0.9"
        if "Content-Type" not in final:
            final["Content-Type"] = "application/json"

        print(f"Finalized headers: {list(final.keys())}")
        return final

    def is_authenticated(self):
        return self._is_authed and self.api is not None

    def login(self, auth_input):
        """
        Robust login method for browser.json or headers dict.
        """
        try:
            headers = None
            if isinstance(auth_input, str):
                if os.path.exists(auth_input):
                    with open(auth_input, "r") as f:
                        headers = json.load(f)
                else:
                    # Try parsing as JSON string
                    try:
                        headers = json.loads(auth_input)
                    except json.JSONDecodeError:
                        # Legacy raw headers string support
                        from ytmusicapi.auth.browser import setup_browser

                        headers = json.loads(
                            setup_browser(filepath=None, headers_raw=auth_input)
                        )
            elif isinstance(auth_input, dict):
                headers = auth_input

            if not headers:
                print("Invalid auth input.")
                return False

            # CRITICAL: Enforce Headers for Stability
            # 1. Accept-Language must be English to avoid parsing errors
            headers["Accept-Language"] = "en-US,en;q=0.9"

            # 2. Ensure User-Agent is consistent/modern if missing
            if "User-Agent" not in headers:
                headers["User-Agent"] = (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
                )

            # 3. Content-Type often needed for JSON payloads
            if "Content-Type" not in headers:
                headers["Content-Type"] = "application/json; charset=UTF-8"

            # 4. Standardize headers and remove Bearer tokens
            headers = self._normalize_headers(headers)

            # Save to data/headers_auth.json (Overwrite)
            os.makedirs(os.path.dirname(self.auth_path), exist_ok=True)
            if os.path.exists(self.auth_path):
                try:
                    os.remove(self.auth_path)
                except Exception:
                    pass
            with open(self.auth_path, "w") as f:
                json.dump(headers, f)

            # Initialize API with dict directly
            print(f"Initializing YTMusic with headers: {list(headers.keys())}")
            # Drop any cached state from a previous session before the
            # new account's requests start landing — otherwise the
            # avatar menu, library indexes, and channel-handle map all
            # keep showing the old user's data.
            self._clear_account_state()
            self.api = YTMusic(auth=headers)

            # Validate
            if self.validate_session():
                self._is_authed = True
                print("Login successful and saved.")
                return True
            else:
                print("Login failed: Session invalid after init.")
                self.api = YTMusic()
                self._is_authed = False
                return False

        except Exception as e:
            import traceback

            print(f"Login exception: {e}")
            traceback.print_exc()
            self.api = YTMusic()
            self._is_authed = False
            return False

    def search(self, query, *args, **kwargs):
        if not self.api:
            return []
        return self.api.search(query, *args, **kwargs)

    def get_song(self, video_id):
        if not self.api:
            return None
        try:
            res = self.api.get_song(video_id)
            return res
        except Exception as e:
            print(f"Error getting song details: {e}")
            return None

    def get_history(self):
        """Return the authenticated user's listening history as a list of
        ytmusicapi track dicts. Empty list if unavailable.

        Writes through to the local cache on success so the next open
        (or an offline load) can render instantly from disk."""
        if not self.is_authenticated():
            if self._offline_db:
                cached = self._offline_db.get_cached_history()
                if cached:
                    return cached
            return []
        try:
            tracks = self.api.get_history() or []
            if tracks and self._offline_db:
                try:
                    self._offline_db.cache_history(tracks)
                except Exception as e:
                    print(f"[HISTORY] cache write failed: {e}")
            return tracks
        except Exception as e:
            print(f"Error getting history: {e}")
            if self._offline_db:
                cached = self._offline_db.get_cached_history()
                if cached:
                    return cached
            return []

    def get_cached_history(self):
        """Pull the cached history directly, bypassing the network. Used
        for the optimistic first paint of HistoryPage."""
        if self._offline_db:
            return self._offline_db.get_cached_history() or []
        return []

    def invalidate_history_cache_entry(self, video_id):
        """Remove a single track from the cached history list — used
        after an optimistic 'Remove from History' so the disk cache
        stays in sync with the server-side removal."""
        if self._offline_db and video_id:
            try:
                self._offline_db.remove_from_history_cache(video_id)
            except Exception as e:
                print(f"[HISTORY] cache invalidate failed: {e}")

    def add_history_item_async(self, video_id):
        """Fire-and-forget record of a listen to the signed-in YT Music
        account. Requires the song's `get_song` response — ytmusicapi's
        add_history_item uses the `videostatsPlaybackUrl` inside it to
        ping YT's playback tracker.

        Also optimistically prepends the track to the on-disk history
        cache so HistoryPage reflects the play before YT's server-side
        roll-up catches up."""
        if not video_id:
            print(f"[HISTORY] skip: no video id")
            return
        if not self.is_authenticated():
            print(f"[HISTORY] skip: not authenticated ({video_id})")
            return
        import threading

        def _record():
            try:
                song = self.api.get_song(video_id)
                if not song:
                    print(f"[HISTORY] get_song returned None for {video_id}")
                    return
                pb = song.get("playbackTracking") or {}
                if not pb.get("videostatsPlaybackUrl"):
                    print(
                        f"[HISTORY] {video_id} has no playbackTracking URL — "
                        f"can't add to history"
                    )
                    return
                resp = self.api.add_history_item(song)
                status = getattr(resp, "status_code", "?")
                print(
                    f"[HISTORY] add_history_item({video_id}) -> HTTP {status}"
                )
                # Server-side histories take a moment to include the new
                # entry. Prepend locally so the Verlauf-style UI reacts
                # immediately on the next open.
                try:
                    self._prepend_to_history_cache(video_id, song)
                except Exception as e:
                    print(f"[HISTORY] cache prepend failed: {e}")
            except Exception as e:
                print(f"[HISTORY] add_history_item failed for {video_id}: {e}")

        threading.Thread(target=_record, daemon=True).start()

    def _prepend_to_history_cache(self, video_id, song_data):
        """Patch the cached history with an optimistic 'Today' entry for
        the just-played track. Drops any existing entry for the same
        videoId so the track bubbles to the top instead of duplicating."""
        if not self._offline_db:
            return
        cached = self._offline_db.get_cached_history() or []

        # Build a minimal track dict from the get_song response. The
        # shape mirrors what `get_history` returns so HistoryPage can
        # render it without special-casing.
        video_details = song_data.get("videoDetails") or {}
        title = video_details.get("title", "")
        author = video_details.get("author", "")
        length_seconds = 0
        try:
            length_seconds = int(video_details.get("lengthSeconds", 0))
        except (TypeError, ValueError):
            length_seconds = 0
        thumbs = (
            video_details.get("thumbnail", {}).get("thumbnails", []) or []
        )

        new_entry = {
            "videoId": video_id,
            "title": title,
            "artists": [{"name": author, "id": video_details.get("channelId")}]
            if author else [],
            "album": {"name": "", "id": None},
            "duration": (
                f"{length_seconds // 60}:{length_seconds % 60:02d}"
                if length_seconds else ""
            ),
            "duration_seconds": length_seconds,
            "thumbnails": thumbs,
            "played": "Today",
            "likeStatus": "INDIFFERENT",
        }

        filtered = [t for t in cached if t.get("videoId") != video_id]
        filtered.insert(0, new_entry)
        try:
            self._offline_db.cache_history(filtered)
        except Exception as e:
            print(f"[HISTORY] cache_history write failed: {e}")

    def remove_history_items(self, feedback_tokens):
        """Remove history entries by their feedback tokens (each history
        item exposes one). Returns True on success."""
        if not self.is_authenticated() or not feedback_tokens:
            return False
        try:
            self.api.remove_history_items(feedback_tokens)
            return True
        except Exception as e:
            print(f"[HISTORY] remove failed: {e}")
            return False

    def get_library_playlists(self):
        if not self.is_authenticated():
            # Offline fallback
            if self._offline_db:
                cached = self._offline_db.get_cached_library_playlists()
                if cached:
                    return cached
            return []
        try:
            playlists = self.api.get_library_playlists()
            self._library_playlists = playlists
            self._library_playlist_ids = {
                p.get("playlistId") for p in playlists if p.get("playlistId")
            }
            # Cache for offline use
            if self._offline_db and playlists:
                self._offline_db.cache_library_playlists(playlists)
            return playlists
        except Exception as e:
            print(f"Error fetching library playlists: {e}")
            # Fall back to cache
            if self._offline_db:
                cached = self._offline_db.get_cached_library_playlists()
                if cached:
                    return cached
            return []

    def get_library_albums(self, limit=100):
        if not self.is_authenticated():
            if self._offline_db:
                cached = self._offline_db.get_cached_library_albums()
                if cached:
                    return cached
            return []
        try:
            albums = self.api.get_library_albums(limit=limit)
            self._library_album_ids = set()
            for a in albums:
                if a.get("browseId"):
                    self._library_album_ids.add(a["browseId"])
                if a.get("audioPlaylistId"):
                    self._library_album_ids.add(a["audioPlaylistId"])
                if a.get("playlistId"):
                    self._library_album_ids.add(a["playlistId"])
            if self._offline_db and albums:
                self._offline_db.cache_library_albums(albums)
            return albums
        except Exception as e:
            print(f"Error fetching library albums: {e}")
            if self._offline_db:
                cached = self._offline_db.get_cached_library_albums()
                if cached:
                    return cached
            return []

    def is_in_library(self, playlist_id):
        """Check if a playlist or album is saved in the user's library."""
        if not playlist_id or not self.is_authenticated():
            return False

        # Fetch library data if not cached yet
        if not self._library_playlist_ids:
            try:
                playlists = self.api.get_library_playlists()
                self._library_playlists = playlists
                self._library_playlist_ids = {
                    p.get("playlistId") for p in playlists if p.get("playlistId")
                }
            except Exception:
                pass
        if not self._library_album_ids:
            try:
                albums = self.api.get_library_albums(limit=100)
                self._library_album_ids = set()
                for a in albums:
                    if a.get("browseId"):
                        self._library_album_ids.add(a["browseId"])
                    if a.get("audioPlaylistId"):
                        self._library_album_ids.add(a["audioPlaylistId"])
                    if a.get("playlistId"):
                        self._library_album_ids.add(a["playlistId"])
            except Exception:
                pass

        pid = playlist_id
        if pid.startswith("VL"):
            pid = pid[2:]
        # Check playlists
        if pid in self._library_playlist_ids:
            return True
        # Check albums (by browseId, audioPlaylistId, or playlistId)
        if pid in self._library_album_ids:
            return True
        return False

    def rate_playlist(self, playlist_id, rating="LIKE"):
        """Rate a playlist/album: 'LIKE' to save, 'INDIFFERENT' to remove from library.
        Strips VL prefix and converts MPRE browse IDs to playlist IDs automatically."""
        if not self.is_authenticated():
            return False
        try:
            # Strip VL prefix (browse ID → playlist ID)
            pid = playlist_id
            if pid.startswith("VL"):
                pid = pid[2:]
            # Convert MPRE browse ID to audio playlist ID
            if pid.startswith("MPRE"):
                try:
                    album_data = self.api.get_album(pid)
                    pid = album_data.get("audioPlaylistId", pid)
                except Exception:
                    pass
            self.api.rate_playlist(pid, rating)
            return True
        except Exception as e:
            print(f"Error rating playlist: {e}")
            return False

    def edit_song_library_status(self, feedback_tokens):
        """Add/remove songs from library using feedback tokens."""
        if not self.is_authenticated():
            return False
        try:
            self.api.edit_song_library_status(feedback_tokens)
            return True
        except Exception as e:
            print(f"Error editing song library status: {e}")
            return False

    def get_library_upload_songs(self, limit=100, order=None):
        if not self.is_authenticated():
            return []
        try:
            return self.api.get_library_upload_songs(limit=limit, order=order)
        except Exception as e:
            print(f"Error fetching uploaded songs: {e}")
            return []

    def get_library_upload_albums(self, limit=100, order=None):
        if not self.is_authenticated():
            return []
        try:
            return self.api.get_library_upload_albums(limit=limit, order=order)
        except Exception as e:
            print(f"Error fetching uploaded albums: {e}")
            return []

    def get_library_upload_artists(self, limit=100, order=None):
        if not self.is_authenticated():
            return []
        try:
            return self.api.get_library_upload_artists(limit=limit, order=order)
        except Exception as e:
            print(f"Error fetching uploaded artists: {e}")
            return []

    def upload_song(self, filepath):
        """Upload a song file (mp3, m4a, wma, flac, ogg) to YouTube Music."""
        if not self.is_authenticated():
            return None
        try:
            return self.api.upload_song(filepath)
        except Exception as e:
            print(f"Error uploading song: {e}")
            return None

    def delete_upload_entity(self, entity_id):
        """Delete a previously uploaded song or album."""
        if not self.is_authenticated():
            return False
        try:
            self.api.delete_upload_entity(entity_id)
            return True
        except Exception as e:
            print(f"Error deleting upload: {e}")
            return False

    def get_library_upload_album(self, browse_id):
        """Get tracks for an uploaded album."""
        if not self.is_authenticated():
            return None
        try:
            return self.api.get_library_upload_album(browse_id)
        except Exception as e:
            print(f"Error fetching upload album: {e}")
            return None

    def get_library_upload_artist(self, browse_id, limit=100):
        """Get uploaded songs by a specific artist."""
        if not self.is_authenticated():
            return []
        try:
            return self.api.get_library_upload_artist(browse_id, limit=limit)
        except Exception as e:
            print(f"Error fetching upload artist songs: {e}")
            return []

    def get_library_subscriptions(self, limit=None):
        if not self.is_authenticated():
            if self._offline_db:
                cached = self._offline_db.get_cached_library_artists()
                if cached:
                    return cached
            return []
        try:
            subs = self.api.get_library_subscriptions(limit=limit)
            if subs:
                for s in subs:
                    bid = s.get("browseId")
                    if bid:
                        self._subscribed_artists.add(bid)
                if self._offline_db:
                    self._offline_db.cache_library_artists(subs)
            return subs
        except Exception as e:
            print(f"Error fetching library subscriptions: {e}")
            if self._offline_db:
                cached = self._offline_db.get_cached_library_artists()
                if cached:
                    return cached
            return []

    def get_account_info(self):
        """
        Fetches the current user's account info. Caches the result.
        """
        if not self.is_authenticated():
            return None
        if self._user_info:
            return self._user_info

        try:
            self._user_info = self.api.get_account_info()
            return self._user_info
        except Exception as e:
            print(f"Error fetching account info: {e}")
            return None

    def resolve_channel_handle(self, handle):
        """Turn a YT Music @handle into an artist browseId (UC…) via
        the internal `navigation/resolve_url` endpoint. Returns the
        browseId string, or None. Results are cached on the client
        instance to avoid repeated lookups."""
        if not handle or not self.api:
            return None
        if not hasattr(self, "_channel_handle_cache"):
            self._channel_handle_cache = {}
        if handle in self._channel_handle_cache:
            return self._channel_handle_cache[handle]

        h = handle.lstrip("@")
        try:
            resp = self.api._send_request(
                "navigation/resolve_url",
                {"url": f"https://music.youtube.com/@{h}"},
            )
            # The response's `endpoint.browseEndpoint.browseId` is the
            # channel id; dig defensively, the key nesting has shifted
            # between YT internal revisions.
            ep = (resp or {}).get("endpoint") or {}
            browse = ep.get("browseEndpoint") or ep.get("browse") or {}
            bid = browse.get("browseId")
            if bid:
                self._channel_handle_cache[handle] = bid
                return bid
        except Exception as e:
            print(f"[CLIENT] resolve_channel_handle({handle}) failed: {e}")
        return None

    def is_own_playlist(self, playlist_metadata, playlist_id=None):
        """
        Determines if a playlist is owned/editable by the current user.
        Excludes collaborative playlists where the user is only a collaborator.
        """
        if not self.is_authenticated():
            return False

        pid = (
            playlist_id
            or playlist_metadata.get("id")
            or playlist_metadata.get("playlistId")
            or ""
        )

        # 1. Liked Music and special system playlists are NOT owned
        if pid in ["LM", "SE", "VLLM"]:
            return False

        # 2. Strict prefix check: must start with PL or VL
        if not pid.startswith("PL") and not pid.startswith("VL"):
            return False

        author = playlist_metadata.get("author")

        if not author and not playlist_metadata.get("collaborators"):
            return True
        elif playlist_metadata.get("collaborators"):
            author = playlist_metadata.get("collaborators", {}).get("text", "")
        else:
            # Handle list or dict for author
            if isinstance(author, list) and len(author) > 0:
                author = author[0].get("name", "")
            elif isinstance(author, dict):
                author = author.get("name", "")
            else:
                author = str(author)

        user_info = self.get_account_info()
        user_name = user_info.get("accountName", "") if user_info else ""

        # If it contains user's name and is collaborators, it is owned
        if user_name and user_name in author and playlist_metadata.get("collaborators"):
            return True

        # If it matches the user's name, it is owned
        if author == user_name:
            return True

        return False

    def get_playlist(self, playlist_id, limit=None):
        if not self.api:
            # Offline: try cache
            if self._offline_db:
                cached = self._offline_db.get_cached_playlist(playlist_id)
                if cached:
                    return cached
            return None
        try:
            result = self.api.get_playlist(playlist_id, limit=limit)
            # Cache the full result for offline use
            if result and self._offline_db:
                raw_author = result.get("author", "")
                # ytmusicapi returns author as a dict {"name": ..., "id": ...}
                # for some playlists; str()-ing it produces "{'name': ...}"
                # which later gets dumped into the UI as literal JSON-looking
                # text. Normalize to the display name only.
                if isinstance(raw_author, dict):
                    author_str = raw_author.get("name", "")
                elif isinstance(raw_author, list):
                    author_str = ", ".join(
                        a.get("name", "") for a in raw_author if isinstance(a, dict)
                    )
                else:
                    author_str = str(raw_author or "")
                # Store the rich metadata so PlaylistPage can re-render the
                # full header (year, privacy, author-as-link, etc.) on the
                # next open before the live fetch completes.
                meta = {
                    "description": result.get("description", "") or "",
                    "year": result.get("year", "") or "",
                    "privacy": result.get("privacy", "") or "",
                    "duration_seconds": result.get("duration_seconds"),
                    "thumbnails": result.get("thumbnails", []) or [],
                    "author_raw": raw_author if isinstance(raw_author, (dict, list)) else None,
                }
                self._offline_db.cache_playlist(
                    playlist_id,
                    result.get("title", ""),
                    author_str,
                    result.get("trackCount", len(result.get("tracks", []))),
                    result.get("tracks", []),
                    meta,
                )
            return result
        except Exception as e:
            print(f"Error fetching playlist: {e}")
            if self._offline_db:
                cached = self._offline_db.get_cached_playlist(playlist_id)
                if cached:
                    return cached
            return None

    def get_watch_playlist(
        self, video_id=None, playlist_id=None, limit=25, radio=False
    ):
        if not self.api:
            return {}
        try:
            res = self.api.get_watch_playlist(
                videoId=video_id, playlistId=playlist_id, limit=limit, radio=radio
            )
            return res
        except Exception as e:
            print(f"Error getting watch playlist: {e}")
            return {}

    def get_cached_playlist_tracks(self, playlist_id):
        return self._playlist_cache.get(playlist_id)

    def set_cached_playlist_tracks(self, playlist_id, tracks):
        self._playlist_cache[playlist_id] = tracks

    def get_album(self, browse_id):
        if not self.api:
            if self._offline_db:
                cached = self._offline_db.get_cached_playlist(browse_id)
                if cached:
                    return cached
            return None
        try:
            result = self.api.get_album(browse_id)
            if result and self._offline_db:
                self._offline_db.cache_playlist(
                    browse_id,
                    result.get("title", ""),
                    str(result.get("artists", "")),
                    result.get("trackCount", len(result.get("tracks", []))),
                    result.get("tracks", []),
                )
            return result
        except Exception as e:
            print(f"Error fetching album: {e}")
            if self._offline_db:
                cached = self._offline_db.get_cached_playlist(browse_id)
                if cached:
                    return cached
            return None

    def get_artist(self, channel_id):
        if not self.api:
            return None
        try:
            res = self.api.get_artist(channel_id)
            return res
        except Exception as e:
            print(f"Error getting artist details: {e}")
            # Fallback: try as a regular YouTube channel
            try:
                user_data = self.api.get_user(channel_id)
                if user_data:
                    # Normalize to artist-like format
                    user_data["_is_channel"] = True
                    if "name" in user_data and "subscribers" not in user_data:
                        user_data["subscribers"] = ""
                    # Fetch avatar and banner from raw API
                    try:
                        raw = self.api._send_request("browse", {"browseId": channel_id})
                        header = raw.get("header", {})
                        for hkey in [
                            "musicVisualHeaderRenderer",
                            "musicImmersiveHeaderRenderer",
                        ]:
                            h = header.get(hkey, {})
                            if h:
                                # Avatar (foregroundThumbnail)
                                fg = (
                                    h.get("foregroundThumbnail", {})
                                    .get("musicThumbnailRenderer", {})
                                    .get("thumbnail", {})
                                    .get("thumbnails", [])
                                )
                                if fg:
                                    user_data["thumbnails"] = fg
                                # Banner (thumbnail)
                                bg = (
                                    h.get("thumbnail", {})
                                    .get("musicThumbnailRenderer", {})
                                    .get("thumbnail", {})
                                    .get("thumbnails", [])
                                )
                                if bg:
                                    user_data["banner"] = bg
                                # Subscriber count from subscriptionButton
                                sub_btn = h.get("subscriptionButton", {}).get(
                                    "subscribeButtonRenderer", {}
                                )
                                sub_count = sub_btn.get("subscriberCountText", {}).get(
                                    "runs", []
                                )
                                if sub_count:
                                    user_data["subscribers"] = sub_count[0].get(
                                        "text", ""
                                    )
                                break
                    except Exception:
                        pass
                    # Fallback: use first content thumbnail if no avatar found
                    if "thumbnails" not in user_data:
                        for section_key in ["playlists", "videos", "songs"]:
                            section = user_data.get(section_key, {})
                            results = (
                                section.get("results", [])
                                if isinstance(section, dict)
                                else (section if isinstance(section, list) else [])
                            )
                            if results and results[0].get("thumbnails"):
                                user_data["thumbnails"] = results[0]["thumbnails"]
                                break
                    return user_data
            except Exception as e2:
                print(f"Error getting channel details: {e2}")
            return None

    def get_artist_albums(self, channel_id, params=None, limit=100):
        if not self.api:
            return []
        try:
            result = self.api.get_artist_albums(channel_id, params=params, limit=limit)
            if result:
                return result
        except Exception:
            pass
        # Fallback: try as channel content
        try:
            result = self.api.get_user_playlists(channel_id, params)
            if result:
                return result
        except Exception:
            pass
        # Last resort: raw parse
        try:
            result = self._raw_parse_channel_content(channel_id, params)
            if result:
                return result
        except Exception:
            pass
        return []

    def get_playlist_full(self, playlist_id, limit=None):
        """Fetch a playlist's full track list. Uses ytmusicapi first, then
        our raw-continuation fallback, then yt_dlp's flat-playlist
        extraction as a last resort.

        ytmusicapi's internal paginator sometimes stops before hitting
        trackCount on large playlists (drops a continuation token mid-
        stream). The raw-continuation fallback hits the same pagination
        API though, so it often caps at the same point. yt_dlp uses a
        different underlying request flow that reliably walks YT's full
        playlist enumeration — the metadata it returns is lighter than
        ytmusicapi's, so we use it to fill IN the gaps (matching by
        videoId) rather than replacing what we have."""
        data = self.get_playlist(playlist_id, limit=limit) or {}
        tracks = list(data.get("tracks") or [])
        track_count = data.get("trackCount")
        print(
            f"[PLAYLIST] get_playlist_full: ytmusicapi returned "
            f"{len(tracks)}/{track_count} tracks for {playlist_id}"
        )
        # 5-song tolerance for off-by-one counting in the API's trackCount.
        if track_count and len(tracks) < track_count - 5:
            # Stage 1: raw-continuation on the VL<id> browse endpoint.
            try:
                browse_id = playlist_id if playlist_id.startswith("VL") else f"VL{playlist_id}"
                raw_items = self._raw_parse_playlist(browse_id) or []
                seen = {t.get("videoId") for t in tracks if t.get("videoId")}
                added = 0
                for item in raw_items:
                    vid = item.get("videoId")
                    if vid and vid not in seen:
                        tracks.append(item)
                        seen.add(vid)
                        added += 1
                print(
                    f"[PLAYLIST] raw-continuation added {added} tracks "
                    f"(total now {len(tracks)})"
                )
                data["tracks"] = tracks
            except Exception as e:
                print(f"[PLAYLIST] raw-continuation fallback failed: {e}")

        # Stage 2: if we're STILL short, use yt_dlp to enumerate all
        # videoIds on the playlist and fill in whatever we don't have
        # yet with minimal stubs. These will lack rich metadata but will
        # at least be queueable — the player fetches per-track details
        # on demand.
        if track_count and len(tracks) < track_count - 5:
            ytdlp_items = self._yt_dlp_flat_playlist(playlist_id)
            if ytdlp_items:
                seen = {t.get("videoId") for t in tracks if t.get("videoId")}
                added = 0
                for item in ytdlp_items:
                    vid = item.get("videoId")
                    if vid and vid not in seen:
                        tracks.append(item)
                        seen.add(vid)
                        added += 1
                print(
                    f"[PLAYLIST] yt_dlp added {added} tracks "
                    f"(total now {len(tracks)})"
                )
                data["tracks"] = tracks

        # Persist the (possibly-augmented) track list. The regression
        # guard in cache_playlist() prevents overwriting a richer cache
        # with a poorer one, so this is safe to call unconditionally.
        if self._offline_db and tracks:
            try:
                raw_author = data.get("author")
                if isinstance(raw_author, dict):
                    author_str = raw_author.get("name", "")
                elif isinstance(raw_author, list):
                    author_str = ", ".join(
                        a.get("name", "") for a in raw_author if isinstance(a, dict)
                    )
                else:
                    author_str = str(raw_author or "")
                meta = {
                    "description": data.get("description", "") or "",
                    "year": data.get("year", "") or "",
                    "privacy": data.get("privacy", "") or "",
                    "duration_seconds": data.get("duration_seconds"),
                    "thumbnails": data.get("thumbnails", []) or [],
                    "author_raw": raw_author,
                }
                self._offline_db.cache_playlist(
                    playlist_id,
                    data.get("title", ""),
                    author_str,
                    track_count or len(tracks),
                    tracks,
                    meta,
                )
            except Exception as e:
                print(f"[PLAYLIST] re-cache after augment failed: {e}")
        return data

    def _yt_dlp_flat_playlist(self, playlist_id):
        """Use yt_dlp's flat-playlist extraction to enumerate every
        videoId on a YouTube playlist. Returns minimal track dicts
        compatible with the rest of the app (videoId, title, duration,
        thumbnails, artists). Returns [] on any failure."""
        try:
            from yt_dlp import YoutubeDL
        except ImportError:
            return []

        url = f"https://music.youtube.com/playlist?list={playlist_id}"
        opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": "in_playlist",
            "skip_download": True,
            "ignoreerrors": True,
        }
        try:
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as e:
            print(f"[PLAYLIST] yt_dlp flat-playlist raised: {e}")
            return []
        if not info:
            return []
        result = []
        for entry in info.get("entries") or []:
            if not entry:
                continue
            vid = entry.get("id")
            if not vid:
                continue
            duration = entry.get("duration")
            thumbs = []
            for t in entry.get("thumbnails") or []:
                u = t.get("url") if isinstance(t, dict) else None
                if u:
                    thumbs.append({"url": u})
            artists = []
            uploader = entry.get("uploader") or entry.get("channel")
            if uploader:
                artists.append({"name": uploader})
            result.append({
                "videoId": vid,
                "title": entry.get("title", "") or "Unknown",
                "duration_seconds": int(duration) if duration else 0,
                "thumbnails": thumbs,
                "artists": artists,
            })
        return result

    def _raw_parse_playlist(self, browse_id):
        """Parse a playlist from raw API when ytmusicapi can't handle it (e.g. chart playlists)."""
        body = {"browseId": browse_id}
        response = self.api._send_request("browse", body)

        # Try secondaryContents (twoColumnBrowseResultsRenderer format)
        tcbr = response.get("contents", {}).get("twoColumnBrowseResultsRenderer", {})
        sc = (
            tcbr.get("secondaryContents", {})
            .get("sectionListRenderer", {})
            .get("contents", [])
        )

        items = []
        for section in sc:
            for rkey in ["musicPlaylistShelfRenderer", "musicShelfRenderer"]:
                renderer = section.get(rkey, {})
                if renderer:
                    for raw_item in renderer.get("contents", []):
                        cont = raw_item.get("continuationItemRenderer")
                        if cont:
                            token = (
                                cont.get("continuationEndpoint", {})
                                .get("continuationCommand", {})
                                .get("token")
                            )
                            if token:
                                items.extend(self._fetch_continuation(token))
                        else:
                            parsed = self._parse_channel_item(raw_item)
                            if parsed:
                                items.append(parsed)

        # Fallback: try singleColumnBrowseResultsRenderer
        if not items:
            items = self._raw_parse_channel_content(browse_id, None)

        # Get title from header
        header = response.get("header", {})
        for hkey in header:
            h = header[hkey]
            if isinstance(h, dict):
                title_runs = h.get("title", {}).get("runs", [])
                if title_runs:
                    for item in items:
                        item["_playlist_title"] = title_runs[0].get("text", "")
                    break

        return items

    def _raw_parse_channel_content(self, browse_id, params):
        """Parse channel content from raw API response when ytmusicapi can't."""
        body = {"browseId": browse_id}
        if params:
            body["params"] = params
        response = self.api._send_request("browse", body)

        tabs = (
            response.get("contents", {})
            .get("singleColumnBrowseResultsRenderer", {})
            .get("tabs", [])
        )
        if not tabs:
            return []

        sections = (
            tabs[0]
            .get("tabRenderer", {})
            .get("content", {})
            .get("sectionListRenderer", {})
            .get("contents", [])
        )
        items = []
        for section in sections:
            for renderer_key in [
                "gridRenderer",
                "musicShelfRenderer",
                "musicPlaylistShelfRenderer",
                "musicCarouselShelfRenderer",
            ]:
                renderer = section.get(renderer_key, {})
                content_key = "items" if "items" in renderer else "contents"
                for raw_item in renderer.get(content_key, []):
                    parsed = self._parse_channel_item(raw_item)
                    if parsed:
                        items.append(parsed)
                    # Check for continuation token
                    cont = raw_item.get("continuationItemRenderer", {})
                    if cont:
                        token = (
                            cont.get("continuationEndpoint", {})
                            .get("continuationCommand", {})
                            .get("token")
                        )
                        if token:
                            items.extend(self._fetch_continuation(token))
        return items

    def _fetch_continuation(self, token, max_pages=100):
        """Follow continuation tokens to get all paginated results."""
        items = []
        for _ in range(max_pages):
            if not token:
                break
            try:
                response = self.api._send_request("browse", {"continuation": token})
                token = None  # Reset for next iteration

                # Format 1: onResponseReceivedActions (common for playlists)
                for action in response.get("onResponseReceivedActions", []):
                    if not isinstance(action, dict):
                        continue
                    cont_action = action.get("appendContinuationItemsAction", {})
                    for raw_item in cont_action.get("continuationItems", []):
                        cont_item = raw_item.get("continuationItemRenderer")
                        if cont_item:
                            token = (
                                cont_item.get("continuationEndpoint", {})
                                .get("continuationCommand", {})
                                .get("token")
                            )
                        else:
                            parsed = self._parse_channel_item(raw_item)
                            if parsed:
                                items.append(parsed)

                # Format 2: continuationContents (older format)
                cont_contents = response.get("continuationContents", {})
                for renderer_key in [
                    "musicPlaylistShelfContinuation",
                    "gridContinuation",
                    "musicShelfContinuation",
                ]:
                    renderer = cont_contents.get(renderer_key, {})
                    if not renderer:
                        continue
                    for raw_item in renderer.get("contents", []) + renderer.get(
                        "items", []
                    ):
                        cont_item = raw_item.get("continuationItemRenderer")
                        if cont_item:
                            token = (
                                cont_item.get("continuationEndpoint", {})
                                .get("continuationCommand", {})
                                .get("token")
                            )
                        else:
                            parsed = self._parse_channel_item(raw_item)
                            if parsed:
                                items.append(parsed)
            except Exception:
                break
        return items

    def _parse_channel_item(self, raw_item):
        """Best-effort parse of a channel content item."""
        for item_key in ["musicTwoRowItemRenderer", "musicResponsiveListItemRenderer"]:
            renderer = raw_item.get(item_key)
            if not renderer:
                continue
            result = {}
            # Title - check both direct title.runs and flexColumns
            title_runs = renderer.get("title", {}).get("runs", [])
            if not title_runs:
                # flexColumns format (used in musicResponsiveListItemRenderer)
                for col in renderer.get("flexColumns", []):
                    col_renderer = col.get(
                        "musicResponsiveListItemFlexColumnRenderer", {}
                    )
                    runs = col_renderer.get("text", {}).get("runs", [])
                    if runs and not result.get("title"):
                        title_runs = runs
                        break

            if title_runs:
                result["title"] = title_runs[0].get("text", "")
                nav = title_runs[0].get("navigationEndpoint", {})
                browse_ep = nav.get("browseEndpoint", {})
                watch_ep_title = nav.get("watchEndpoint", {})
                if browse_ep.get("browseId"):
                    result["browseId"] = browse_ep["browseId"]
                if watch_ep_title.get("videoId"):
                    result["videoId"] = watch_ep_title["videoId"]
                    result["playlistId"] = watch_ep_title.get("playlistId", "")

            # Artists from flexColumns (second column usually)
            for col in renderer.get("flexColumns", [])[1:]:
                col_renderer = col.get("musicResponsiveListItemFlexColumnRenderer", {})
                runs = col_renderer.get("text", {}).get("runs", [])
                artists = []
                for r in runs:
                    browse_nav = r.get("navigationEndpoint", {}).get(
                        "browseEndpoint", {}
                    )
                    if browse_nav.get("browseId"):
                        artists.append(
                            {"name": r.get("text", ""), "id": browse_nav["browseId"]}
                        )
                    elif r.get("text", "").strip() and r["text"].strip() not in (
                        "•",
                        "&",
                        ",",
                    ):
                        artists.append({"name": r["text"].strip()})
                if artists:
                    result["artists"] = artists
                    break

            # Duration from fixedColumns
            for col in renderer.get("fixedColumns", []):
                col_renderer = col.get("musicResponsiveListItemFixedColumnRenderer", {})
                runs = col_renderer.get("text", {}).get("runs", [])
                if runs:
                    dur_text = runs[0].get("text", "")
                    result["duration"] = dur_text
                    # Convert "M:SS" or "H:MM:SS" to seconds
                    try:
                        parts = dur_text.split(":")
                        if len(parts) == 2:
                            result["duration_seconds"] = int(parts[0]) * 60 + int(
                                parts[1]
                            )
                        elif len(parts) == 3:
                            result["duration_seconds"] = (
                                int(parts[0]) * 3600
                                + int(parts[1]) * 60
                                + int(parts[2])
                            )
                    except (ValueError, IndexError):
                        pass
                    break

            # Thumbnail
            thumb_renderer = renderer.get("thumbnailRenderer", {}).get(
                "musicThumbnailRenderer", {}
            )
            if not thumb_renderer:
                thumb_renderer = renderer.get("thumbnail", {}).get(
                    "musicThumbnailRenderer", {}
                )
            thumbs = thumb_renderer.get("thumbnail", {}).get("thumbnails", [])
            if thumbs:
                result["thumbnails"] = thumbs

            # VideoId from overlay play button (fallback if not from title)
            if not result.get("videoId"):
                overlay = renderer.get("overlay", {}).get(
                    "musicItemThumbnailOverlayRenderer", {}
                )
                play_btn = overlay.get("content", {}).get("musicPlayButtonRenderer", {})
                watch_ep = play_btn.get("playNavigationEndpoint", {}).get(
                    "watchEndpoint", {}
                )
                if watch_ep.get("videoId"):
                    result["videoId"] = watch_ep["videoId"]
                    result["playlistId"] = watch_ep.get("playlistId", "")

            # Subtitle (for musicTwoRowItemRenderer)
            subtitle_runs = renderer.get("subtitle", {}).get("runs", [])
            if subtitle_runs:
                parts = [r.get("text", "") for r in subtitle_runs]
                result["subtitle"] = "".join(parts)
                if not result.get("artists"):
                    artists = []
                    for r in subtitle_runs:
                        nav = r.get("navigationEndpoint", {}).get("browseEndpoint", {})
                    if nav.get("browseId"):
                        artists.append({"name": r["text"], "id": nav["browseId"]})
                if artists:
                    result["artists"] = artists

            if result.get("title"):
                return result
        return None

    def get_liked_songs(self, limit=100):
        if not self.is_authenticated():
            return []
        # Liked songs is actually a playlist 'LM'
        res = self.api.get_liked_songs(limit=limit)
        return res

    def get_charts(self, country="US"):
        if not self.api:
            return {}
        return self.api.get_charts(country=country)

    def get_explore(self):
        if not self.api:
            return {}
        return self.api.get_explore()

    def get_mood_playlists(self, params):
        if not self.api:
            return []
        try:
            return self.api.get_mood_playlists(params=params)
        except Exception as e:
            print(f"Error fetching mood playlists: {e}")
            return []

    def get_mood_categories(self):
        if not self.api:
            return {}
        try:
            return self.api.get_mood_categories()
        except Exception as e:
            print(f"Error fetching mood categories: {e}")
            return {}

    def get_category_page(self, params):
        if not self.api:
            return []
        try:
            response = self.api._send_request(
                "browse",
                {"browseId": "FEmusic_moods_and_genres_category", "params": params},
            )

            sections = []
            if (
                "contents" in response
                and "singleColumnBrowseResultsRenderer" in response["contents"]
            ):
                tabs = response["contents"]["singleColumnBrowseResultsRenderer"]["tabs"]
                results = tabs[0]["tabRenderer"]["content"]["sectionListRenderer"][
                    "contents"
                ]

                for section in results:
                    if "musicCarouselShelfRenderer" in section:
                        carousel = section["musicCarouselShelfRenderer"]
                        title = carousel["header"][
                            "musicCarouselShelfBasicHeaderRenderer"
                        ]["title"]["runs"][0]["text"]
                        contents = carousel["contents"]

                        parsed_items = []
                        for item in contents:
                            try:
                                data = {}
                                if "musicResponsiveListItemRenderer" in item:
                                    renderer = item["musicResponsiveListItemRenderer"]
                                    runs = renderer["flexColumns"][0][
                                        "musicResponsiveListItemFlexColumnRenderer"
                                    ]["text"]["runs"]
                                    data["title"] = runs[0]["text"]

                                    if "navigationEndpoint" in renderer:
                                        ep = renderer["navigationEndpoint"]
                                        if "watchEndpoint" in ep:
                                            data["videoId"] = ep["watchEndpoint"][
                                                "videoId"
                                            ]
                                        elif "browseEndpoint" in ep:
                                            data["browseId"] = ep["browseEndpoint"][
                                                "browseId"
                                            ]
                                    elif "navigationEndpoint" in runs[0]:
                                        ep = runs[0]["navigationEndpoint"]
                                        if "watchEndpoint" in ep:
                                            data["videoId"] = ep["watchEndpoint"][
                                                "videoId"
                                            ]
                                        elif "browseEndpoint" in ep:
                                            data["browseId"] = ep["browseEndpoint"][
                                                "browseId"
                                            ]

                                    if "thumbnail" in renderer:
                                        data["thumbnails"] = renderer["thumbnail"][
                                            "musicThumbnailRenderer"
                                        ]["thumbnail"]["thumbnails"]

                                    if len(renderer["flexColumns"]) > 1:
                                        sub_runs = renderer["flexColumns"][1][
                                            "musicResponsiveListItemFlexColumnRenderer"
                                        ]["text"]["runs"]
                                        artists = []
                                        for r in sub_runs:
                                            if (
                                                "navigationEndpoint" in r
                                                and "browseEndpoint"
                                                in r["navigationEndpoint"]
                                            ):
                                                if (
                                                    "browseEndpointContextSupportedConfigs"
                                                    in r["navigationEndpoint"][
                                                        "browseEndpoint"
                                                    ]
                                                ):
                                                    if (
                                                        r["navigationEndpoint"][
                                                            "browseEndpoint"
                                                        ][
                                                            "browseEndpointContextSupportedConfigs"
                                                        ][
                                                            "browseEndpointContextMusicConfig"
                                                        ]["pageType"]
                                                        == "MUSIC_PAGE_TYPE_ARTIST"
                                                    ):
                                                        artists.append(
                                                            {
                                                                "name": r["text"],
                                                                "id": r[
                                                                    "navigationEndpoint"
                                                                ]["browseEndpoint"][
                                                                    "browseId"
                                                                ],
                                                            }
                                                        )
                                        data["artists"] = artists

                                elif "musicTwoRowItemRenderer" in item:
                                    renderer = item["musicTwoRowItemRenderer"]
                                    runs = renderer["title"]["runs"]
                                    data["title"] = runs[0]["text"]

                                    if "navigationEndpoint" in renderer:
                                        ep = renderer["navigationEndpoint"]
                                        if "watchEndpoint" in ep:
                                            data["videoId"] = ep["watchEndpoint"][
                                                "videoId"
                                            ]
                                        elif "browseEndpoint" in ep:
                                            data["browseId"] = ep["browseEndpoint"][
                                                "browseId"
                                            ]
                                    elif "navigationEndpoint" in runs[0]:
                                        ep = runs[0]["navigationEndpoint"]
                                        if "watchEndpoint" in ep:
                                            data["videoId"] = ep["watchEndpoint"][
                                                "videoId"
                                            ]
                                        elif "browseEndpoint" in ep:
                                            data["browseId"] = ep["browseEndpoint"][
                                                "browseId"
                                            ]

                                    if (
                                        "thumbnailRenderer" in renderer
                                        and "musicThumbnailRenderer"
                                        in renderer["thumbnailRenderer"]
                                    ):
                                        data["thumbnails"] = renderer[
                                            "thumbnailRenderer"
                                        ]["musicThumbnailRenderer"]["thumbnail"][
                                            "thumbnails"
                                        ]

                                    if (
                                        "subtitle" in renderer
                                        and "runs" in renderer["subtitle"]
                                    ):
                                        sub_runs = renderer["subtitle"]["runs"]
                                        artists = []
                                        year = None
                                        type_ = None
                                        for r in sub_runs:
                                            if (
                                                "navigationEndpoint" in r
                                                and "browseEndpoint"
                                                in r["navigationEndpoint"]
                                            ):
                                                ep = r["navigationEndpoint"][
                                                    "browseEndpoint"
                                                ]
                                                if (
                                                    "browseEndpointContextSupportedConfigs"
                                                    in ep
                                                ):
                                                    pt = ep[
                                                        "browseEndpointContextSupportedConfigs"
                                                    ][
                                                        "browseEndpointContextMusicConfig"
                                                    ]["pageType"]
                                                    if pt == "MUSIC_PAGE_TYPE_ARTIST":
                                                        artists.append(
                                                            {
                                                                "name": r["text"],
                                                                "id": ep["browseId"],
                                                            }
                                                        )
                                            elif (
                                                "text" in r and r["text"].strip() != "•"
                                            ):
                                                txt = r["text"].strip()
                                                if txt.isdigit() and len(txt) == 4:
                                                    year = txt
                                                elif txt in [
                                                    "Album",
                                                    "Single",
                                                    "EP",
                                                    "Playlist",
                                                ]:
                                                    type_ = txt
                                        data["artists"] = artists
                                        if year:
                                            data["year"] = year
                                        if type_:
                                            data["type"] = type_

                                if data:
                                    parsed_items.append(data)
                            except Exception as e:
                                print("Error parsing item in category page:", e)

                        if parsed_items:
                            sections.append({"title": title, "items": parsed_items})
            return sections
        except Exception as e:
            print(f"Error fetching category page: {e}")
            return []

    def get_album_browse_id(self, audio_playlist_id):
        if not self.api:
            return None
        return self.api.get_album_browse_id(audio_playlist_id)

    def rate_song(self, video_id, rating="LIKE"):
        """
        Rate a song: 'LIKE', 'DISLIKE', or 'INDIFFERENT'.
        """
        if not self.is_authenticated():
            return False
        try:
            self.api.rate_song(video_id, rating)
            return True
        except Exception as e:
            print(f"Error rating song: {e}")
            return False

    def validate_session(self):
        """
        Check if the current session is valid by attempting an authenticated request.
        """
        if self.api is None:
            return False

        try:
            # Try to fetch liked songs (requires auth)
            # Just metadata is enough
            self.api.get_liked_songs(limit=1)
            return True
        except Exception as e:
            print(f"Session validation failed: {e}")
            return False

    def logout(self):
        """
        Log out by deleting the saved auth file and resetting the API.
        """
        if os.path.exists(self.auth_path):
            try:
                os.remove(self.auth_path)
                print(f"Removed auth file at {self.auth_path}")
            except Exception as e:
                print(f"Could not remove auth file: {e}")

        self.api = YTMusic()
        self._is_authed = False
        self._clear_account_state()
        print("Logged out. API reset to unauthenticated mode.")
        return True

    def _clear_account_state(self):
        """Drop every per-account cache so account switches can't leak
        data from the previous user. Covers in-memory state (account
        info, library indexes, subscribed artists, playlist/channel
        caches) and the per-user on-disk caches (history + library
        playlists)."""
        self._user_info = None
        self._subscribed_artists = set()
        self._library_playlists = []
        self._library_playlist_ids = set()
        self._library_album_ids = set()
        self._playlist_cache = {}
        if hasattr(self, "_channel_handle_cache"):
            self._channel_handle_cache = {}

        # Wipe the on-disk per-user caches via DownloadDB helpers if
        # they're available. Silent on failure — this is a cleanup
        # pass, not a correctness-critical operation.
        db = self._offline_db
        if db is None:
            return
        try:
            import sqlite3
            with db._lock:
                conn = db._connect()
                try:
                    conn.execute("DELETE FROM history_cache")
                except sqlite3.OperationalError:
                    pass
                try:
                    conn.execute("DELETE FROM library_playlists_cache")
                except sqlite3.OperationalError:
                    pass
                conn.commit()
                conn.close()
        except Exception as e:
            print(f"[CLIENT] per-account cache wipe failed: {e}")

    def edit_playlist(
        self, playlist_id, title=None, description=None, privacy=None, moveItem=None
    ):
        if not self.is_authenticated():
            return False
        try:
            self.api.edit_playlist(
                playlist_id,
                title=title,
                description=description,
                privacyStatus=privacy,
                moveItem=moveItem,
            )
            return True
        except Exception as e:
            print(f"Error editing playlist: {e}")
            return False

    def delete_playlist(self, playlist_id):
        if not self.is_authenticated():
            return False
        try:
            self.api.delete_playlist(playlist_id)
            return True
        except Exception as e:
            print(f"Error deleting playlist: {e}")
            return False

    def add_playlist_items(self, playlist_id, video_ids, duplicates=False):
        if not self.is_authenticated():
            return False
        try:
            self.api.add_playlist_items(playlist_id, video_ids, duplicates=duplicates)
            return True
        except Exception as e:
            print(f"Error adding to playlist: {e}")
            return False

    def remove_playlist_items(self, playlist_id, videos):
        if not self.is_authenticated():
            return False
        try:
            self.api.remove_playlist_items(playlist_id, videos)
            return True
        except Exception as e:
            print(f"Error removing from playlist: {e}")
            return False

    def get_editable_playlists(self):
        """
        Returns a list of playlists that the user can add songs to.
        Includes owned playlists and collaborative playlists.
        """
        if not self.is_authenticated():
            return []
        try:
            playlists = (
                self._library_playlists
                if self._library_playlists
                else self.get_library_playlists()
            )

            user_info = self.get_account_info()
            user_name = user_info.get("accountName", "").lower() if user_info else ""

            editable = []
            for p in playlists:
                pid = p.get("playlistId") or ""
                # Exclude radio/mixes/system playlists
                if not pid.startswith("PL") and not pid.startswith("VL"):
                    continue
                if pid in ["LM", "SE", "VLLM"]:
                    continue

                # Ownership Check:
                # items created by the user often have author="You" or their name, or no author field.
                # items subscribed to have a specific author name.
                # collaborative ones might have both, but usually can be added to.

                author = p.get("author") or p.get("creator")
                if isinstance(author, list) and author:
                    author = author[0].get("name", "")
                elif isinstance(author, dict):
                    author = author.get("name", "")

                author_str = str(author or "").lower()

                # If author is missing, empty, "you", or your name, it's yours
                is_mine = False
                if (
                    not author_str
                    or author_str == "you"
                    or (user_name and author_str == user_name)
                ):
                    is_mine = True

                # Collaborative check: ytmusicapi identifies these in some objects,
                # but if we are following it and it's in the library, we can try.
                # Actually, the most reliable way in the library list is seeing if there is NOT an external author.

                if is_mine or p.get("collaborative"):
                    editable.append(p)
            return editable
        except Exception as e:
            print(f"Error filtering editable playlists: {e}")
            return []

    def subscribe_artist(self, channel_id):
        if not self.is_authenticated():
            return False
        try:
            self.api.subscribe_artists([channel_id])
            self._subscribed_artists.add(channel_id)
            return True
        except Exception as e:
            print(f"Error subscribing to artist: {e}")
            return False

    def unsubscribe_artist(self, channel_id):
        if not self.is_authenticated():
            return False
        try:
            self.api.unsubscribe_artists([channel_id])
            if channel_id in self._subscribed_artists:
                self._subscribed_artists.remove(channel_id)
            return True
        except Exception as e:
            print(f"Error unsubscribing from artist: {e}")
            return False

    def is_subscribed_artist(self, channel_id):
        """Checks if an artist is in the local subscription cache."""
        return channel_id in self._subscribed_artists

    def create_playlist(
        self, title, description="", privacy_status="PRIVATE", video_ids=None
    ):
        """
        Creates a new playlist.
        """
        if not self.is_authenticated():
            return None
        try:
            return self.api.create_playlist(
                title, description, privacy_status=privacy_status, video_ids=video_ids
            )
        except Exception as e:
            print(f"Error creating playlist: {e}")
            return None

    def set_playlist_thumbnail(self, playlist_id, image_path):
        """
        Sets a custom thumbnail for a playlist.
        Uses internal YouTube resumable upload endpoints. Resizes to 1024x1024 max.
        """
        if not self.is_authenticated():
            print("Not authenticated.")
            return False

        import requests

        try:
            with open(image_path, "rb") as f:
                img_data = f.read()

            print(f"DEBUG: Uploading thumbnail for {playlist_id}")

            # Use base ytmusicapi headers, but remove Content-Type for binary upload steps
            base_headers = self.api.headers.copy()
            base_headers.pop("Content-Type", None)

            # --- STEP 1: INITIATE UPLOAD ---
            headers_start = base_headers.copy()
            headers_start.update(
                {
                    "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
                    "Content-Length": "0",
                    "X-Goog-Upload-Command": "start",
                    "X-Goog-Upload-Protocol": "resumable",
                    "X-Goog-Upload-Header-Content-Length": str(len(img_data)),
                    "Origin": "https://music.youtube.com",
                    "Referer": f"https://music.youtube.com/playlist?list={playlist_id}",
                }
            )

            init_res = requests.post(
                "https://music.youtube.com/playlist_image_upload/playlist_custom_thumbnail",
                headers=headers_start,
                data=b"",
            )

            print(f"DEBUG STEP1: status={init_res.status_code}")
            print(f"DEBUG STEP1: response headers={dict(init_res.headers)}")
            print(f"DEBUG STEP1: body={init_res.text[:500]}")

            upload_id = init_res.headers.get("x-guploader-uploadid")

            if not upload_id:
                raise Exception(
                    f"Failed to obtain upload ID. Status={init_res.status_code}, Body={init_res.text[:500]}"
                )

            # --- STEP 2: UPLOAD BINARY DATA ---
            upload_url = init_res.headers.get("X-Goog-Upload-URL")

            headers_upload = base_headers.copy()
            headers_upload.update(
                {
                    "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
                    "X-Goog-Upload-Command": "upload, finalize",
                    "X-Goog-Upload-Offset": "0",
                    "Origin": "https://music.youtube.com",
                    "Referer": f"https://music.youtube.com/playlist?list={playlist_id}",
                }
            )
            # Remove any encoding headers that cause "Could not decompress" errors
            headers_upload.pop("Accept-Encoding", None)
            headers_upload.pop("Content-Encoding", None)

            import urllib.request

            req = urllib.request.Request(
                upload_url,
                data=img_data,
                headers=headers_upload,
                method="POST",
            )
            with urllib.request.urlopen(req) as resp:
                upload_body = resp.read().decode("utf-8")
                upload_status = resp.status

            print(f"DEBUG STEP2: status={upload_status}")
            print(f"DEBUG STEP2: body={upload_body[:500]}")

            if not upload_body.strip():
                raise Exception(
                    f"Upload returned empty response. Status={upload_status}"
                )

            import json as _json

            blob_data = _json.loads(upload_body)
            blob_id = blob_data.get("encryptedBlobId")

            if not blob_id:
                raise Exception(
                    f"Failed to obtain encryptedBlobId. Response: {blob_data}"
                )

            # --- STEP 3: BIND BLOB TO PLAYLIST ---
            clean_playlist_id = (
                playlist_id[2:] if playlist_id.startswith("VL") else playlist_id
            )

            payload = {
                "playlistId": clean_playlist_id,
                "actions": [
                    {
                        "action": "ACTION_SET_CUSTOM_THUMBNAIL",
                        "addedCustomThumbnail": {
                            "imageKey": {
                                "type": "PLAYLIST_IMAGE_TYPE_CUSTOM_THUMBNAIL",
                                "name": "studio_square_thumbnail",
                            },
                            "playlistScottyEncryptedBlobId": blob_id,
                        },
                    }
                ],
            }

            # _send_request natively handles putting "Content-Type: application/json" back
            edit_res = self.api._send_request("browse/edit_playlist", payload)

            if edit_res.get("status") == "STATUS_SUCCEEDED":
                print("Thumbnail successfully updated!")
                return True
            else:
                print(f"Failed to bind thumbnail. API Response: {edit_res}")
                return False

        except Exception as e:
            print(f"Error setting playlist thumbnail: {e}")
            return False
