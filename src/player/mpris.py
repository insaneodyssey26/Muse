from __future__ import annotations
import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst
from mprisify.adapters import MprisAdapter
from mprisify.events import EventAdapter
from mprisify.base import Position, PlayState, Volume
from mprisify.enums import LoopStatus


class MuseMprisAdapter(MprisAdapter):
    def __init__(self, player):
        super().__init__(name="Mixtapes")
        self.player = player
        self._last_pos = 0

    # RootAdapter
    def can_quit(self) -> bool:
        return True

    def quit(self):
        self.player.stop()

    def can_raise(self) -> bool:
        return True

    def set_raise(self, value: bool):
        from gi.repository import GLib
        GLib.idle_add(self._do_raise)

    def _do_raise(self):
        from gi.repository import Gtk
        app = Gtk.Application.get_default()
        if app:
            win = app.get_active_window()
            if win:
                win.set_visible(True)
                win.present()
        return False

    def get_desktop_entry(self):
        return "com.pocoguy.Muse"

    def can_fullscreen(self) -> bool:
        return False

    def has_tracklist(self) -> bool:
        return False

    # PlayerAdapter
    def can_control(self) -> bool:
        return True

    def can_go_next(self) -> bool:
        return self.player.current_queue_index + 1 < len(self.player.queue)

    def can_go_previous(self) -> bool:
        # Check if we can seek back or go to previous track
        if self.player.current_queue_index > 0:
            return True

        try:
            ret, pos = self.player.player.query_position(Gst.Format.TIME)
            if ret and pos > 5 * Gst.SECOND:
                return True
        except:
            pass
        return False

    def can_pause(self) -> bool:
        return True

    def can_play(self) -> bool:
        return True

    def can_seek(self) -> bool:
        return self.player.duration > 0

    def next(self):
        self.player.next()

    def previous(self):
        self.player.previous()

    def pause(self):
        self.player.pause()

    def resume(self):
        self.player.play()

    def play(self):
        self.player.play()

    def stop(self):
        self.player.stop()

    def seek(self, time: Position, track_id=None):
        # Position is in microseconds
        self._last_pos = time
        self.player.seek(time / 1_000_000.0)

    def get_playstate(self) -> PlayState:
        try:
            state = self.player.get_state_string()
            if state == "playing":
                return PlayState.PLAYING
            elif state == "loading":
                # Reporting as PLAYING during loading for smoother UI transitions
                return PlayState.PLAYING
            elif state == "paused":
                return PlayState.PAUSED
            else:
                return PlayState.STOPPED
        except Exception as e:
            print(f"MPRIS: Error getting playstate: {e}")
            return PlayState.STOPPED

    def get_current_position(self) -> Position:
        # returns microseconds
        try:
            # Gst.Format.TIME is 3
            ret, pos = self.player.player.query_position(Gst.Format.TIME)
            if ret:
                self._last_pos = pos // 1000
                return self._last_pos
        except Exception as e:
            # Silently fail position query if player is busy/loading
            pass
        return self._last_pos

    def get_volume(self) -> Volume:
        try:
            return self.player.get_volume()
        except:
            return 1.0

    def set_volume(self, value: Volume):
        try:
            self.player.set_volume(value)
        except:
            pass

    def is_mute(self) -> bool:
        try:
            return self.player.get_mute()
        except:
            return False

    def set_mute(self, value: bool):
        try:
            self.player.set_mute(value)
        except:
            pass

    def get_shuffle(self) -> bool:
        try:
            return self.player.shuffle_mode
        except:
            return False

    def set_shuffle(self, value: bool):
        try:
            if value != self.player.shuffle_mode:
                self.player.shuffle_queue()
        except:
            pass

    def is_repeating(self) -> bool:
        return getattr(self.player, "repeat_mode", "none") != "none"

    def is_playlist(self) -> bool:
        return getattr(self.player, "repeat_mode", "none") == "all"

    def get_loop_status(self) -> LoopStatus:
        mode = getattr(self.player, "repeat_mode", "none")
        if mode == "track":
            return LoopStatus.TRACK
        elif mode == "all":
            return LoopStatus.PLAYLIST
        return LoopStatus.NONE

    def set_loop_status(self, value: LoopStatus):
        if value == LoopStatus.TRACK:
            self.player.set_repeat_mode("track")
        elif value == LoopStatus.PLAYLIST:
            self.player.set_repeat_mode("all")
        else:
            self.player.set_repeat_mode("none")

    def metadata(self):
        try:
            if self.player.current_queue_index == -1 or not self.player.queue:
                return {
                    "mpris:trackid": "/com/pocoguy/Muse/track/none",
                    "xesam:title": "",
                    "xesam:artist": [],
                    "xesam:album": "",
                }

            track = self.player.queue[self.player.current_queue_index]

            # Artist normalization
            artist = track.get("artist", "")
            if not artist and "artists" in track:
                artists_list = track.get("artists", [])
                if isinstance(artists_list, list):
                    artist = ", ".join(
                        [a.get("name", "") for a in artists_list if "name" in a]
                    )
                elif isinstance(artists_list, str):
                    artist = artists_list

            if not artist:
                artist = "Unknown Artist"

            # Thumb normalization
            thumb = track.get("thumb")
            if not thumb and "thumbnails" in track:
                thumbs = track.get("thumbnails", [])
                if thumbs:
                    thumb = thumbs[-1].get("url")

            # Sanitize videoId for D-Bus object path (hyphens -> underscores)
            video_id = track.get("videoId", "unknown")
            # D-Bus path components must not start with a digit and only contain [A-Z, a-z, 0-9, _]
            safe_id = video_id.replace("-", "_").replace(".", "_")
            if safe_id[0].isdigit():
                safe_id = "v" + safe_id
            
            album = track.get("album", "")
            if isinstance(album, dict):
                album = album.get("name", "")
            album = str(album or "")

            m = {
                "mpris:trackid": f"/com/pocoguy/Muse/track/{safe_id}",
                "mpris:length": int(self.player.duration * 1_000_000)
                if self.player.duration > 0
                else 0,
                "xesam:title": track.get("title", "Unknown Title"),
                "xesam:artist": [artist],
                "xesam:album": album,
            }

            art_url = getattr(self.player, "mpris_art_url", None)
            if art_url:
                m["mpris:artUrl"] = art_url
            elif thumb:
                m["mpris:artUrl"] = thumb

            return m
        except Exception as e:
            print(f"MPRIS: Error generating metadata: {e}")
            return {}


class MuseEventAdapter(EventAdapter):
    def emit_all(self):
        # Useful for a full refresh (e.g., when a new client connects)
        self.on_player_all()
        self.on_root_all()

    def on_track_changed(self):
        # Specifically tells D-Bus the Metadata property has changed
        self.emit_changes("Player", ["Metadata", "CanGoNext", "CanGoPrevious"])

    def on_status_changed(self):
        # Specifically tells D-Bus the PlaybackStatus property has changed
        self.emit_changes("Player", ["PlaybackStatus"])
