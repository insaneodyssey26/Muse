import os
import threading
import urllib.request
import collections
import re
from gi.repository import Gtk, Gdk, GObject, GLib, GdkPixbuf

print("DEBUG: Loading ui/utils.py v1.1 (Placeholder fix)")


def is_online():
    """Check if we have network connectivity. Respects force-offline setting."""
    import json
    prefs_path = os.path.join(GLib.get_user_data_dir(), "muse", "prefs.json")
    try:
        if os.path.exists(prefs_path):
            with open(prefs_path) as f:
                prefs = json.load(f)
            if prefs.get("force_offline"):
                return False
    except Exception:
        pass
    from gi.repository import Gio
    monitor = Gio.NetworkMonitor.get_default()
    return monitor.get_network_available()

# Bounded LRU Cache to prevent memory leaks (max 100 images). The cache is
# read/written by multiple worker threads and the main thread, so every
# mutation is serialized through IMG_CACHE_LOCK — concurrent check-then-modify
# sequences were corrupting LRU state and evicting pixbufs that other threads
# were still wiring up into textures.
IMG_CACHE = collections.OrderedDict()
MAX_CACHE_SIZE = 100
IMG_CACHE_LOCK = threading.Lock()


# ── Persistent thumbnail cache on disk ─────────────────────────────────────
# Avoids the placeholder-icon flash when returning to the library, and makes
# subsequent launches render covers instantly. Files are raw image bytes
# under XDG_CACHE/muse/thumbs/<sha1-of-url>.
def _thumb_cache_dir():
    path = os.path.join(GLib.get_user_cache_dir(), "muse", "thumbs")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass
    return path


def _thumb_cache_key(url):
    import hashlib
    return hashlib.sha1(url.encode("utf-8", errors="replace")).hexdigest()


def _thumb_cache_path(url):
    if not url or url.startswith("file://"):
        return None
    return os.path.join(_thumb_cache_dir(), _thumb_cache_key(url))


def read_thumb_cache(url):
    path = _thumb_cache_path(url)
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def write_thumb_cache(url, data):
    if not url or not data:
        return
    path = _thumb_cache_path(url)
    if not path:
        return
    try:
        # Write to a sibling tmp file then rename so partial writes can never
        # be read as valid cached bytes.
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except OSError:
        pass


def cache_pixbuf(url, pixbuf):
    if not url or not pixbuf:
        return
    # Scale down very large images before caching to save massive amounts of RAM
    # 1600px is more than enough for any UI element (including expanded player)
    w = pixbuf.get_width()
    h = pixbuf.get_height()
    max_dim = 1600
    if w > max_dim or h > max_dim:
        scale = max_dim / max(w, h)
        pixbuf = pixbuf.scale_simple(
            int(w * scale), int(h * scale), GdkPixbuf.InterpType.BILINEAR
        )

    with IMG_CACHE_LOCK:
        if url in IMG_CACHE:
            IMG_CACHE.move_to_end(url)
            return
        IMG_CACHE[url] = pixbuf
        if len(IMG_CACHE) > MAX_CACHE_SIZE:
            IMG_CACHE.popitem(last=False)


def get_high_res_url(url, target_size=None):
    """Rewrites Google Image URLs to request a high resolution (800x800).
    Also strips sqp and rs parameters which constrain resolution, UNLESS it's a locker track.
    """
    if not url:
        return url

    # 1. Clean up parameters that constrain resolution
    # Strip sqp and rs which are often used to force small/safe thumbnails
    # CRITICAL: Locker track thumbnails (vi_locker) REQUIRE the rs parameter.
    if "vi_locker" not in url:
        clean_url = re.sub(r"([?&])(sqp|rs)=[^&]*&?", r"\1", url)
        clean_url = clean_url.replace("?&", "?").rstrip("?&")
    else:
        clean_url = url

    # 2. Upgrade resolution/quality based on domain
    if "i.ytimg.com" in clean_url:
        for q in _YTIMG_QUALITIES:
            if q in clean_url:
                return clean_url.replace(q, "maxresdefault")
        return clean_url

    if "googleusercontent.com" in clean_url or "ggpht.com" in clean_url:
        # If it has w/h, only update those and ignore s
        if re.search(r"([=-])w\d+-h\d+", clean_url):
            return re.sub(r"([=-])w\d+-h\d+", r"\1w800-h800", clean_url)
        # Otherwise update s
        return re.sub(r"([=-])s\d+(?=-|$)", r"\1s800", clean_url)

    return clean_url


_YTIMG_QUALITIES = ["maxresdefault", "sddefault", "hqdefault", "mqdefault", "default"]


def get_ytimg_fallbacks(url):
    """For YouTube video thumbnail URLs (i.ytimg.com/vi/...), generate
    a fallback chain from the current quality downward.
    Returns a list of fallback URLs (excluding the primary URL).
    """
    if not url or "i.ytimg.com/vi/" not in url:
        return []

    # Find which quality is currently in the URL
    current_idx = -1
    for i, q in enumerate(_YTIMG_QUALITIES):
        if q in url:
            current_idx = i
            break

    if current_idx < 0:
        # If no known quality is in the URL, provide the full chain
        # try to guess where in the path the quality name would be
        # (usually after /vi/VIDEO_ID/)
        match = re.search(r"/vi/[^/]+/", url)
        if match:
            base = url[: match.end()]
            return [f"{base}{q}.jpg" for q in _YTIMG_QUALITIES]
        return []

    # Generate fallbacks from the next quality downward
    fallbacks = []
    current_q = _YTIMG_QUALITIES[current_idx]
    for q in _YTIMG_QUALITIES[current_idx + 1 :]:
        fallbacks.append(url.replace(current_q, q))
    return fallbacks


# In-flight de-duplication so the library grid doesn't fire N parallel
# downloads for the same cover on every refresh.
_COVER_DL_INFLIGHT = set()
_COVER_DL_LOCK = threading.Lock()


def save_playlist_cover_async(player, title, url):
    """Download a playlist's cover to <music_dir>/playlists/<title>.jpg so
    future opens (from anywhere — library grid, playlist page) can render
    it instantly and offline. Silently no-ops on failure.

    Custom-playlist covers (i.ytimg.com/pl_c/...) require YT auth cookies,
    which a naked `requests.get` doesn't carry. We pass the signed-in
    client's Cookie header so those URLs resolve.

    Overwrites the existing file so edits on YT propagate.
    """
    if not title or not url:
        return
    try:
        from player.downloads import get_music_dir, _sanitize_filename

        cover_dir = os.path.join(get_music_dir(), "playlists")
        os.makedirs(cover_dir, exist_ok=True)
        cover_path = os.path.join(cover_dir, f"{_sanitize_filename(title)}.jpg")
    except Exception:
        return

    key = (title, url)
    with _COVER_DL_LOCK:
        if key in _COVER_DL_INFLIGHT:
            return
        _COVER_DL_INFLIGHT.add(key)

    def _dl():
        try:
            import requests

            headers = {"User-Agent": "Mozilla/5.0"}
            try:
                if player and hasattr(player, "client"):
                    client = player.client
                    if (
                        client
                        and client.is_authenticated()
                        and any(
                            d in url
                            for d in (
                                "ytimg.com",
                                "googleusercontent.com",
                                "ggpht.com",
                            )
                        )
                    ):
                        cookie = client.api.headers.get("Cookie")
                        if cookie:
                            headers["Cookie"] = cookie
            except Exception:
                pass
            # Try the high-res upgrade first, then walk the ytimg quality
            # chain down, and finally the original URL — not every video
            # has a maxresdefault.jpg generated.
            candidates = []
            hi = get_high_res_url(url)
            if hi:
                candidates.append(hi)
            candidates.extend(get_ytimg_fallbacks(hi or url))
            if url not in candidates:
                candidates.append(url)

            saved = False
            last_status = None
            for candidate in candidates:
                try:
                    resp = requests.get(candidate, headers=headers, timeout=15)
                except Exception as e:
                    print(f"[COVER] {candidate} errored: {e}")
                    continue
                last_status = resp.status_code
                if resp.status_code == 200 and len(resp.content) > 1000:
                    with open(cover_path, "wb") as f:
                        f.write(resp.content)
                    print(
                        f"[COVER] saved {cover_path} from {candidate} "
                        f"({len(resp.content)} bytes)"
                    )
                    saved = True
                    break
            if not saved:
                print(
                    f"[COVER] all candidates failed for {title} "
                    f"(last HTTP {last_status})"
                )
        except Exception as e:
            print(f"[COVER] exception for {title}: {e}")
        finally:
            with _COVER_DL_LOCK:
                _COVER_DL_INFLIGHT.discard(key)

    threading.Thread(target=_dl, daemon=True).start()


def copy_to_clipboard(text):
    """Copies the given text to the default system clipboard."""
    if not text:
        return
    display = Gdk.Display.get_default()
    if display:
        clipboard = display.get_clipboard()
        clipboard.set(text)
        print(f"[DEBUG] Copied to clipboard: {text}")


def get_yt_music_link(item_id, is_album=False, audio_playlist_id=None):
    """
    Constructs a YouTube Music link for a playlist or album.
    Albums use /playlist?list=OLAK... (the audio playlist ID).
    MPRE browse IDs are internal and not shareable.
    """
    if not item_id:
        return ""
    if item_id.startswith("OLAK"):
        return f"https://music.youtube.com/playlist?list={item_id}"
    if is_album or item_id.startswith("MPRE"):
        # MPRE is a browse ID, not a shareable URL.
        # Use the audio_playlist_id if available, otherwise fall back to browse URL.
        if audio_playlist_id:
            return f"https://music.youtube.com/playlist?list={audio_playlist_id}"
        return f"https://music.youtube.com/browse/{item_id}"
    return f"https://music.youtube.com/playlist?list={item_id}"


def parse_item_metadata(item):
    """
    Robustly extracts metadata (year, type, is_explicit) from ytmusicapi item formats.
    Handles standard keys and fallbacks to subtitle runs/badges.
    """
    metadata = {
        "year": str(item.get("year", "")),
        "type": str(item.get("type", "")),
        "is_explicit": bool(item.get("isExplicit") or item.get("explicit")),
    }

    # Fallback for explicit (badges)
    if not metadata["is_explicit"]:
        badges = item.get("badges", [])
        for badge in badges:
            # Check for label in the badge itself or inside a music_inline_badge_renderer
            label = ""
            if isinstance(badge, dict):
                label = badge.get("label", "") or badge.get(
                    "musicInlineBadgeRenderer", {}
                ).get("accessibilityData", {}).get("accessibilityData", {}).get(
                    "label", ""
                )
            if not label and isinstance(badge, str):
                label = badge

            label = str(label).lower()
            if "explicit" in label or label == "e":
                metadata["is_explicit"] = True
                break

    # Fallback for year/type (subtitle runs)
    subtitle = item.get("subtitle", "")
    runs = []
    if isinstance(subtitle, list):
        runs = subtitle
    elif isinstance(item.get("subtitles"), list):
        runs = item.get("subtitles")
    elif isinstance(subtitle, dict) and "runs" in subtitle:
        runs = subtitle["runs"]

    if runs:
        for run in runs:
            if not isinstance(run, dict):
                continue
            text = run.get("text", "")
            if not text:
                continue

            # Look for 4-digit years
            year_match = re.search(r"\d{4}", text)
            if year_match and not metadata["year"]:
                metadata["year"] = year_match.group(0)

            # Common types
            type_lower = text.lower()
            if (
                "single" in type_lower
                or "ep" in type_lower
                or "album" in type_lower
                or "video" in type_lower
            ):
                if not metadata["type"]:
                    metadata["type"] = text

    # Final cleanup: if year is not numeric, it's likely a type
    year_val = metadata["year"]
    is_numeric_year = bool(re.search(r"\d{4}", year_val))
    if year_val and not is_numeric_year:
        if not metadata["type"]:
            metadata["type"] = year_val
        metadata["year"] = ""

    return metadata


class AsyncImage(Gtk.Image):
    def __init__(
        self,
        url=None,
        size=None,
        width=None,
        height=None,
        circular=False,
        player=None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.player = player

        # Determine target dimensions
        self.target_w = width if width else size
        self.target_h = height if height else size
        self._is_placeholder = True

        if not self.target_w:
            self.target_w = 48
        if not self.target_h:
            self.target_h = 48

        # Set pixel size if provided (limits size for icons).
        if size:
            self.set_pixel_size(size)
        else:
            # Rely on pixbuf scaling for explicit width/height.
            pass

        self.set_from_icon_name("image-missing-symbolic")  # Placeholder
        self._is_placeholder = True
        self.url = url
        self.circular = circular

        if url:
            self.load_url(url)

    @staticmethod
    def _get_local_cover(video_id):
        if not video_id:
            return None
        try:
            from player.downloads import get_download_db, DownloadManager
            db = get_download_db()
            audio_path = db.get_local_path(video_id)
            if audio_path:
                cover_data = DownloadManager.extract_cover_from_file(audio_path)
                if cover_data:
                    cache_dir = os.path.join(GLib.get_user_cache_dir(), "muse", "covers")
                    os.makedirs(cache_dir, exist_ok=True)
                    cover_path = os.path.join(cache_dir, f"{video_id}.jpg")
                    if not os.path.exists(cover_path):
                        with open(cover_path, "wb") as f:
                            f.write(cover_data)
                    return f"file://{cover_path}"
        except Exception:
            pass
        return None

    def load_url(self, url, **kwargs):
        orig_url = url
        url = get_high_res_url(url, self.target_w)
        self.url = url

        vid = getattr(self, 'video_id', None)

        # Always try local cover first for instant display
        local = self._get_local_cover(vid) if vid else None
        if local and local in IMG_CACHE:
            self.url = local
            self._apply_pixbuf(IMG_CACHE[local], local)
            IMG_CACHE.move_to_end(local)
            if not is_online():
                return
        elif local:
            self.url = local
            threading.Thread(
                target=self._fetch_image, args=(local, [], None), daemon=True
            ).start()
            if not is_online():
                return

        if not url:
            if local:
                return  # local cover thread already started above
            self.set_from_icon_name("image-missing-symbolic")
            return

        cached_pixbuf = IMG_CACHE.get(url)
        if cached_pixbuf:
            IMG_CACHE.move_to_end(url)
        else:
            # Only show placeholder if we don't already have a valid image
            # and no local cover is loading
            if not local and (not self.get_paintable() or self._is_placeholder):
                self.set_from_icon_name("image-missing-symbolic")
                self._is_placeholder = True

        fallbacks = kwargs.get("fallbacks") or get_ytimg_fallbacks(url)
        if url != orig_url and orig_url not in fallbacks:
            fallbacks.append(orig_url)

        self.url = url  # Update so _apply_pixbuf accepts the web result
        thread = threading.Thread(
            target=self._fetch_image, args=(url, fallbacks, cached_pixbuf)
        )
        thread.daemon = True
        thread.start()

    def _fetch_image(self, url, fallbacks=None, cached_pixbuf=None):
        try:
            pixbuf = cached_pixbuf
            if not pixbuf:
                if url.startswith("file://"):
                    import os
                    path = url[7:]
                    if os.path.exists(path):
                        with open(path, "rb") as f:
                            data = f.read()
                    else:
                        return
                else:
                    # Persistent disk cache first — skips the network hop
                    # entirely for covers we've already fetched, which makes
                    # library re-entry flash-free.
                    data = read_thumb_cache(url)
                    if not data:
                        headers = {"User-Agent": "Mozilla/5.0"}
                        if self.player and hasattr(self.player, "client"):
                            client = self.player.client
                            if client and client.is_authenticated():
                                if any(d in url for d in ["youtube.com", "ytimg.com", "googleusercontent.com", "ggpht.com"]):
                                    cookie = client.api.headers.get("Cookie")
                                    if cookie:
                                        headers["Cookie"] = cookie

                        import requests
                        resp = requests.get(url, headers=headers, timeout=10)
                        resp.raise_for_status()
                        data = resp.content
                        write_thumb_cache(url, data)

                loader = GdkPixbuf.PixbufLoader()
                loader.write(data)
                loader.close()
                pixbuf = loader.get_pixbuf()

                if pixbuf:
                    # Cache the original full-res (scaled to max 1600) pixbuf
                    # We increase max_dim here to 1600 for better header quality
                    w = pixbuf.get_width()
                    h = pixbuf.get_height()
                    max_dim = 1600
                    if w > max_dim or h > max_dim:
                        scale = max_dim / max(w, h)
                        pixbuf = pixbuf.scale_simple(
                            int(w * scale),
                            int(h * scale),
                            GdkPixbuf.InterpType.BILINEAR,
                        )

                    cache_pixbuf(url, pixbuf)

            if pixbuf:
                # Now perform the widget-specific scaling and cropping in the background thread
                # To support HiDPI (e.g. 200% scale), we double the target pixel density
                # GTK will scale the texture back down smoothly, keeping it crisp.
                tw = self.target_w * 2
                th = self.target_h * 2

                w = pixbuf.get_width()
                h = pixbuf.get_height()

                # Calculate scale to fill the target size (cover)
                scale = max(tw / w, th / h)
                new_w = int(w * scale)
                new_h = int(h * scale)

                # Scale properly
                scaled = pixbuf.scale_simple(
                    new_w, new_h, GdkPixbuf.InterpType.BILINEAR
                )

                # Center crop to target dimensions
                final_pixbuf = scaled
                if new_w > tw or new_h > th:
                    offset_x = max(0, (new_w - tw) // 2)
                    offset_y = max(0, (new_h - th) // 2)
                    cw = min(tw, new_w - offset_x)
                    ch = min(th, new_h - offset_y)
                    if cw > 0 and ch > 0:
                        try:
                            final_pixbuf = scaled.new_subpixbuf(
                                offset_x, offset_y, cw, ch
                            )
                        except Exception as e:
                            print(f"Pixbuf crop error: {e}")

                # Apply on main thread
                GLib.idle_add(self._apply_pixbuf, final_pixbuf, url)

        except Exception:
            if fallbacks and self.url == url:
                next_url = fallbacks.pop(0)
                self.url = next_url  # Update current URL to match the fallback

                # If we have a player, notify it about the working fallback URL
                # when it finally succeeds. This is handled in _apply_pixbuf.

                print(f"Trying fallback: {next_url}")
                self._fetch_image(next_url, fallbacks)

    def _apply_pixbuf(self, pixbuf, url=None):
        # Race condition check: only apply if the URL hasn't changed since request
        if url and self.url != url:
            return

        # Notify player of working URL if it's different from what we started with
        if self.player and url and "ytimg.com" in url:
            # We only want to notify if this is a fallback that worked
            # or if the URL was resolved from a 404.
            # We'll rely on the player to handle the update logic.
            GLib.idle_add(self._sync_player_url, url)

        texture = Gdk.Texture.new_for_pixbuf(pixbuf)
        self.set_from_paintable(texture)
        self._is_placeholder = False

    def _sync_player_url(self, url):
        if not self.player or not url:
            return
        # Find current track and update its thumb if it matches
        if hasattr(self.player, "update_track_thumbnail"):
            # We don't know the video_id here easily without storing it,
            # but usually the image loading is for the 'currently playing' or 'item in list'.
            # To be safe, we'll only sync if this widget was explicitly given a video_id.
            video_id = getattr(self, "video_id", None)
            if video_id:
                self.player.update_track_thumbnail(video_id, url)

    def set_from_file(self, file):
        """Optimistically set image from a local file object (GFile)"""
        try:
            # We must load into a pixbuf first to handle scaling correctly
            path = file.get_path()
            # Multiplying by 2 to support HiDPI displays
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                path, self.target_w * 2, self.target_h * 2, True
            )
            print(f"[IMAGE-LOAD] AsyncImage path={path}")
            self.set_from_pixbuf(pixbuf)
            # Nullify URL so subsequent async loads don't overwrite this immediately
            self.url = f"file://{path}"
        except Exception as e:
            print(f"Error setting from file: {e}")


def subprocess_pixbuf(pixbuf, x, y, w, h):
    # bindings helper
    return pixbuf.new_subpixbuf(x, y, w, h)


class AsyncPicture(Gtk.Picture):
    # Added crop_to_square parameter
    def __init__(
        self,
        url=None,
        crop_to_square=False,
        icon_name=None,
        target_size=None,
        player=None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.player = player
        self.set_content_fit(Gtk.ContentFit.COVER)
        self.crop_to_square = crop_to_square
        self.target_size = target_size
        self.url = url
        self.video_id = None
        self._is_placeholder = True

        # Constrain the picture widget to target_size so it doesn't
        # request more space when a non-square texture is loaded
        if target_size:
            self.set_size_request(target_size, target_size)
            self.set_hexpand(False)
            self.set_vexpand(False)

        if icon_name:
            self.set_from_icon_name(icon_name)
        else:
            self.set_from_icon_name("image-missing-symbolic")
            self._is_placeholder = True
            if url:
                self.load_url(url)

    def do_measure(self, orientation, for_size):
        """Clamp natural size so the texture doesn't inflate the parent.
        Uses _current_size which is updated by set_compact()."""
        minimum, natural, min_baseline, nat_baseline = Gtk.Picture.do_measure(
            self, orientation, for_size
        )
        size = getattr(self, '_current_size', self.target_size)
        if size and natural > size:
            natural = size
            minimum = min(minimum, size)
        return minimum, natural, -1, -1

    def _get_local_cover(self):
        """Extract cover from downloaded audio file for offline display."""
        if not self.video_id:
            return None
        try:
            from player.downloads import get_download_db, DownloadManager
            db = get_download_db()
            audio_path = db.get_local_path(self.video_id)
            if audio_path:
                cover_data = DownloadManager.extract_cover_from_file(audio_path)
                if cover_data:
                    # Save to a temp file for GStreamer/GTK to load
                    cache_dir = os.path.join(GLib.get_user_cache_dir(), "muse", "covers")
                    os.makedirs(cache_dir, exist_ok=True)
                    cover_path = os.path.join(cache_dir, f"{self.video_id}.jpg")
                    if not os.path.exists(cover_path):
                        with open(cover_path, "wb") as f:
                            f.write(cover_data)
                    return f"file://{cover_path}"
        except Exception:
            pass
        return None

    def set_compact(self, compact):
        """Switch between desktop and mobile sizing."""
        if self.target_size:
            self._current_size = 44 if compact else self.target_size
            self.set_size_request(self._current_size, self._current_size)
            self.queue_resize()

    def set_from_icon_name(self, icon_name):
        if not icon_name:
            self.set_paintable(None)
            return

        display = Gdk.Display.get_default()
        theme = Gtk.IconTheme.get_for_display(display)

        # 256 is a good high-res baseline for icons to be scaled by GTK
        icon_paintable = theme.lookup_icon(
            icon_name, None, 256, 1, Gtk.TextDirection.NONE, Gtk.IconLookupFlags.PRELOAD
        )
        if icon_paintable:
            self.set_paintable(icon_paintable)
            self._is_placeholder = ("image-missing" in icon_name)
        else:
            self.set_paintable(None)
            self._is_placeholder = True

    def load_url(self, url, **kwargs):
        orig_url = url
        url = get_high_res_url(url, self.target_size)
        self.url = url

        target_size = self.target_size
        crop = self.crop_to_square

        # Always try local cover first for instant display
        local = self._get_local_cover() if self.video_id else None
        if local and local in IMG_CACHE:
            self.url = local
            self._apply_pixbuf(IMG_CACHE[local], local)
            IMG_CACHE.move_to_end(local)
            if not is_online():
                return
        elif local:
            self.url = local
            threading.Thread(
                target=self._fetch_image, args=(local, target_size, crop, []),
                daemon=True,
            ).start()
            if not is_online():
                return

        if not url:
            if local:
                return  # local cover thread already started
            self.set_paintable(None)
            return

        # Check cache
        if url in IMG_CACHE:
            pixbuf = IMG_CACHE[url]
            GLib.idle_add(self._apply_pixbuf, pixbuf, url)
            return

        # Only show placeholder if no local cover is loading
        if not local and (not self.get_paintable() or self._is_placeholder):
            self.set_from_icon_name("image-missing-symbolic")
            self._is_placeholder = True

        fallbacks = kwargs.get("fallbacks") or get_ytimg_fallbacks(url)
        if url != orig_url and orig_url not in fallbacks:
            fallbacks.append(orig_url)

        self.url = url  # Update so _apply_pixbuf accepts the web result
        threading.Thread(
            target=self._fetch_image,
            args=(url, target_size, crop, fallbacks),
            daemon=True,
        ).start()

    def _fetch_image(self, url, target_size=None, crop=False, fallbacks=None):
        try:
            if url.startswith("file://"):
                # Local file
                import os
                path = url[7:]
                if os.path.exists(path):
                    with open(path, "rb") as f:
                        data = f.read()
                else:
                    return
            else:
                # Persistent disk cache first.
                data = read_thumb_cache(url)
                if not data:
                    # Download image data
                    headers = {"User-Agent": "Mozilla/5.0"}
                    if self.player and hasattr(self.player, "client"):
                        client = self.player.client
                        if client and client.is_authenticated():
                            if any(d in url for d in ["youtube.com", "ytimg.com", "googleusercontent.com", "ggpht.com"]):
                                cookie = client.api.headers.get("Cookie")
                                if cookie:
                                    headers["Cookie"] = cookie

                    import requests
                    resp = requests.get(url, headers=headers, timeout=10)
                    resp.raise_for_status()
                    data = resp.content
                    write_thumb_cache(url, data)

            loader = GdkPixbuf.PixbufLoader()
            loader.write(data)
            loader.close()
            pixbuf = loader.get_pixbuf()

            if pixbuf:
                w = pixbuf.get_width()
                h = pixbuf.get_height()

                # Scale to max_dim=1600 for high-quality caching
                max_dim = 1600
                if w > max_dim or h > max_dim:
                    scale = max_dim / max(w, h)
                    pixbuf = pixbuf.scale_simple(
                        int(w * scale),
                        int(h * scale),
                        GdkPixbuf.InterpType.BILINEAR,
                    )
                    w = pixbuf.get_width()
                    h = pixbuf.get_height()

                # Cache the high-res version BEFORE potential thumbnail downscaling
                cache_pixbuf(url, pixbuf)

                if target_size:
                    # Scale to 2x for HiDPI quality (this is the widget-specific version)
                    tw = target_size * 2
                    th = target_size * 2
                    if w > tw or h > th:
                        scale = max(tw / w, th / h)
                        pixbuf = pixbuf.scale_simple(
                            int(w * scale),
                            int(h * scale),
                            GdkPixbuf.InterpType.BILINEAR,
                        )

            GLib.idle_add(self._apply_pixbuf, pixbuf, url)

        except Exception:
            if fallbacks and self.url == url:
                next_url = fallbacks.pop(0)
                self.url = next_url
                self._fetch_image(next_url, target_size, crop, fallbacks)
            else:
                # Last resort: try local cover for downloaded songs
                try:
                    local = self._get_local_cover()
                    if local and local != url:
                        self._fetch_image(local, target_size, crop, [])
                except Exception:
                    pass

    def _apply_pixbuf(self, pixbuf, url=None):
        # Race condition check
        if url and self.url != url:
            return

        if not pixbuf:
            self.set_paintable(None)
            return

        # Notify player of working URL
        if self.player and url and "ytimg.com" in url:
            GLib.idle_add(self._sync_player_url, url)

        # Crop to center square if requested
        if self.crop_to_square and pixbuf:
            w = pixbuf.get_width()
            h = pixbuf.get_height()
            if w != h:
                size = min(w, h)
                x_off = (w - size) // 2
                y_off = (h - size) // 2
                pixbuf = pixbuf.new_subpixbuf(x_off, y_off, size, size)

        # Convert to Texture and paint
        texture = Gdk.Texture.new_for_pixbuf(pixbuf)
        self.set_paintable(texture)
        self._is_placeholder = False

    def _sync_player_url(self, url):
        if not self.player or not url:
            return
        if hasattr(self.player, "update_track_thumbnail"):
            video_id = getattr(self, "video_id", None)
            if video_id:
                self.player.update_track_thumbnail(video_id, url)


class MarqueeLabel(Gtk.ScrolledWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.NEVER)
        self.set_hexpand(True)

        self.box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=60)
        self.label1 = Gtk.Label()
        self.label2 = Gtk.Label()
        
        self.box.append(self.label1)
        self.box.append(self.label2)
        self.set_child(self.box)

        self._tick_id = 0
        self._loop_spacing = 60
        self._is_animating = False

        self.connect("map", self._start_marquee)
        self.connect("unmap", self._stop_marquee)

    def add_css_class(self, class_name):
        self.label1.add_css_class(class_name)
        self.label2.add_css_class(class_name)

    def _start_marquee(self, *args):
        if self._tick_id == 0:
            self._tick_id = self.add_tick_callback(self._on_tick)

    def _stop_marquee(self, *args):
        if self._tick_id != 0:
            self.remove_tick_callback(self._tick_id)
            self._tick_id = 0

    def _on_tick(self, widget, frame_clock):
        width = self.get_width()
        label_w = self.label1.get_width()

        # If it fits, don't animate and keep centered/start aligned
        if label_w <= width:
            self.label2.set_visible(False)
            self.get_hadjustment().set_value(0)
            self._is_animating = False
            return True

        # Otherwise, animate
        self.label2.set_visible(True)
        self._is_animating = True

        frame_time = frame_clock.get_frame_time()
        if not hasattr(self, "_last_frame_time"):
            self._last_frame_time = frame_time
            return True

        delta = (frame_time - self._last_frame_time) / 1_000_000.0
        self._last_frame_time = frame_time

        adj = self.get_hadjustment()
        speed = 40.0  # px/s
        new_val = adj.get_value() + (speed * delta)

        # Seamless loop point
        loop_point = label_w + self._loop_spacing
        if new_val >= loop_point:
            new_val -= loop_point

        adj.set_value(new_val)
        return True

    def set_label(self, text):
        self.label1.set_label(text)
        self.label2.set_label(text)
        # Reset scroll on text change
        self.get_hadjustment().set_value(0)
        if hasattr(self, "_last_frame_time"):
            delattr(self, "_last_frame_time")


class LikeButton(Gtk.Button):
    def __init__(self, client, video_id, initial_status="INDIFFERENT", **kwargs):
        super().__init__(**kwargs)
        self.client = client
        self.video_id = video_id
        self.status = initial_status

        self.add_css_class("flat")
        self.add_css_class("circular")
        self.set_valign(Gtk.Align.CENTER)

        self.update_icon()
        self.connect("clicked", self.on_clicked)

    def update_icon(self):
        if self.status == "LIKE":
            self.set_icon_name("starred-symbolic")
            self.add_css_class("liked-button")  # For potential CSS styling
            self.set_tooltip_text("Unlike")
        elif self.status == "DISLIKE":
            self.set_icon_name(
                "view-restore-symbolic"
            )  # Placeholder or specific icon if found
            self.set_tooltip_text("Disliked")
        else:
            self.set_icon_name("non-starred-symbolic")
            self.remove_css_class("liked-button")
            self.set_tooltip_text("Like")

    def on_clicked(self, btn):
        # Toggle: LIKE -> INDIFFERENT, others -> LIKE
        new_status = "INDIFFERENT" if self.status == "LIKE" else "LIKE"

        # Optimistic update
        old_status = self.status
        self.status = new_status
        self.update_icon()

        def do_rate():
            success = self.client.rate_song(self.video_id, new_status)
            if not success:
                # Revert on failure
                GLib.idle_add(self.revert, old_status)

        thread = threading.Thread(target=do_rate)
        thread.daemon = True
        thread.start()

    def revert(self, status):
        self.status = status
        self.update_icon()

    def set_data(self, video_id, status):
        self.video_id = video_id
        self.status = status
        self.update_icon()
        self.set_visible(bool(video_id))
