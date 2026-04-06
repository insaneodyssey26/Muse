"""
Windows SMTC integration via the native MixtapesBridge.exe subprocess.
Communicates via stdin/stdout JSON messages.
"""

import sys
import os
import json
import subprocess
import threading
import atexit

if sys.platform != "win32":
    raise ImportError("smtc is Windows-only")


class SMTCAdapter:
    """Bridges the GStreamer player to Windows SMTC via MixtapesBridge.exe."""

    def __init__(self, player):
        self.player = player
        self._proc = None
        self._reader_thread = None
        self._ready = False
        self._start_bridge()

    def _find_bridge(self):
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        candidates = [
            os.path.join(base, "windows", "MixtapesBridge.exe"),
            os.path.join(base, "MixtapesBridge.exe"),
        ]
        for c in candidates:
            if os.path.exists(c):
                return c
        return None

    def _start_bridge(self):
        bridge = self._find_bridge()
        if not bridge:
            print("SMTC: MixtapesBridge.exe not found")
            return

        try:
            # CREATE_NO_WINDOW prevents a console flash on Windows
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self._proc = subprocess.Popen(
                [bridge],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                creationflags=creationflags,
            )
            self._reader_thread = threading.Thread(target=self._read_events, daemon=True)
            self._reader_thread.start()
            atexit.register(self.shutdown)
            print(f"SMTC: Bridge started (pid={self._proc.pid})")
        except Exception as e:
            print(f"SMTC: Failed to start bridge: {e}")
            self._proc = None

    def _send(self, msg):
        if not self._proc or self._proc.poll() is not None:
            return
        try:
            line = json.dumps(msg)
            self._proc.stdin.write(line + "\n")
            self._proc.stdin.flush()
        except Exception as e:
            print(f"SMTC send error: {e}")

    def _read_events(self):
        """Read events from bridge stdout and dispatch to player."""
        from gi.repository import GLib

        try:
            for line in self._proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                evt_type = event.get("event")
                if evt_type == "ready":
                    print("SMTC: Bridge ready")
                elif evt_type == "smtc_ready":
                    self._ready = True
                    print("SMTC: Controls active")
                elif evt_type == "error":
                    print(f"SMTC: Bridge error: {event.get('message')}")
                elif evt_type == "button":
                    button = event.get("button")
                    if button == "play":
                        GLib.idle_add(self.player.play)
                    elif button == "pause":
                        GLib.idle_add(self.player.pause)
                    elif button == "next":
                        GLib.idle_add(self.player.next)
                    elif button == "previous":
                        GLib.idle_add(self.player.previous)
                    elif button == "stop":
                        GLib.idle_add(self.player.stop)
        except Exception:
            pass  # Bridge process ended

    def update_playback_status(self, state):
        self._send({"cmd": "update_status", "status": state})

    def update_metadata(self, title, artist, thumbnail_url=None):
        msg = {"cmd": "update_metadata", "title": title or "", "artist": artist or ""}
        if thumbnail_url and thumbnail_url.startswith("http"):
            msg["thumbnail"] = thumbnail_url
        self._send(msg)

    def update_timeline(self, position_secs, duration_secs):
        if duration_secs > 0:
            self._send({
                "cmd": "update_timeline",
                "position": position_secs,
                "duration": duration_secs,
            })

    def update_controls(self, can_next=True, can_previous=True):
        self._send({
            "cmd": "update_controls",
            "can_next": can_next,
            "can_previous": can_previous,
        })

    def shutdown(self):
        if self._proc and self._proc.poll() is None:
            self._send({"cmd": "quit"})
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
