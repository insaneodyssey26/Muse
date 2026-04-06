"""
Windows System Media Transport Controls (SMTC) integration.
Uses pywinrt to expose playback state to Windows media overlay (Win+G, keyboard media keys, etc.)
"""

import sys
import asyncio
import threading

if sys.platform != "win32":
    raise ImportError("smtc is Windows-only")

from winrt.windows.media import (
    SystemMediaTransportControls,
    SystemMediaTransportControlsButton,
    SystemMediaTransportControlsButtonPressedEventArgs,
    SystemMediaTransportControlsDisplayUpdater,
    MediaPlaybackType,
    MediaPlaybackStatus,
    SystemMediaTransportControlsTimelineProperties,
)
from winrt.windows.media.playback import MediaPlayer as WinRTMediaPlayer
from winrt.windows.storage.streams import RandomAccessStreamReference
from winrt.windows.foundation import Uri


class SMTCAdapter:
    """Bridges the GStreamer player to Windows SMTC."""

    def __init__(self, player):
        self.player = player
        self._smtc = None
        self._winrt_player = None
        self._setup()

    def _setup(self):
        # Create a dummy WinRT MediaPlayer to get SMTC access
        self._winrt_player = WinRTMediaPlayer()
        self._smtc = self._winrt_player.system_media_transport_controls

        # Disable auto-integration (we control everything manually)
        self._winrt_player.command_manager.is_enabled = False

        # Enable controls
        self._smtc.is_enabled = True
        self._smtc.is_play_enabled = True
        self._smtc.is_pause_enabled = True
        self._smtc.is_next_enabled = True
        self._smtc.is_previous_enabled = True
        self._smtc.is_stop_enabled = True

        self._smtc.playback_status = MediaPlaybackStatus.CLOSED

        # Handle button presses
        self._smtc.add_button_pressed(self._on_button_pressed)

    def _on_button_pressed(self, sender, args):
        from gi.repository import GLib

        button = args.button
        if button == SystemMediaTransportControlsButton.PLAY:
            GLib.idle_add(self.player.play)
        elif button == SystemMediaTransportControlsButton.PAUSE:
            GLib.idle_add(self.player.pause)
        elif button == SystemMediaTransportControlsButton.NEXT:
            GLib.idle_add(self.player.next)
        elif button == SystemMediaTransportControlsButton.PREVIOUS:
            GLib.idle_add(self.player.previous)
        elif button == SystemMediaTransportControlsButton.STOP:
            GLib.idle_add(self.player.stop)

    def update_playback_status(self, state):
        if not self._smtc:
            return

        status_map = {
            "playing": MediaPlaybackStatus.PLAYING,
            "loading": MediaPlaybackStatus.CHANGING,
            "paused": MediaPlaybackStatus.PAUSED,
            "stopped": MediaPlaybackStatus.STOPPED,
        }
        self._smtc.playback_status = status_map.get(state, MediaPlaybackStatus.STOPPED)

    def update_metadata(self, title, artist, thumbnail_url=None):
        if not self._smtc:
            return

        updater = self._smtc.display_updater
        updater.type = MediaPlaybackType.MUSIC
        updater.music_properties.title = title or "Unknown Title"
        updater.music_properties.artist = artist or "Unknown Artist"

        if thumbnail_url:
            try:
                updater.thumbnail = RandomAccessStreamReference.create_from_uri(
                    Uri(thumbnail_url)
                )
            except Exception:
                pass

        updater.update()

    def update_timeline(self, position_secs, duration_secs):
        if not self._smtc or duration_secs <= 0:
            return

        try:
            import winrt.windows.foundation as wf

            props = SystemMediaTransportControlsTimelineProperties()
            # TimeSpan is in 100-nanosecond units
            ticks_per_sec = 10_000_000
            props.start_time = wf.TimeSpan(0)
            props.min_seek_time = wf.TimeSpan(0)
            props.position = wf.TimeSpan(int(position_secs * ticks_per_sec))
            props.max_seek_time = wf.TimeSpan(int(duration_secs * ticks_per_sec))
            props.end_time = wf.TimeSpan(int(duration_secs * ticks_per_sec))

            self._smtc.update_timeline_properties(props)
        except Exception as e:
            print(f"SMTC timeline update error: {e}")

    def update_controls(self, can_next=True, can_previous=True):
        if not self._smtc:
            return
        self._smtc.is_next_enabled = can_next
        self._smtc.is_previous_enabled = can_previous
