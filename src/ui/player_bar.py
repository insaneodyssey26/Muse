from gi.repository import Gtk, Adw, GObject, Gdk
from ui.queue_panel import QueuePanel


class PlayerBar(Gtk.Box):
    __gsignals__ = {"expand-requested": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(
        self, player, on_artist_click=None, on_queue_click=None, on_album_click=None
    ):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.player = player
        self.on_artist_click = on_artist_click
        self.on_queue_click = on_queue_click
        self.on_album_click = on_album_click
        self.add_css_class("background")  # Generic background
        self.add_css_class("player-bar")  # Custom class for specific styling

        # Load CSS for the player bar
        self._load_css()

        # 1. Progress Bar on Top ("Roof")
        # Scale
        self.scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL)
        self.scale.set_hexpand(True)
        self.scale.set_range(0, 100)
        self.scale.add_css_class("player-scale")
        self.scale.connect("change-value", self.on_scale_change_value)
        self.append(self.scale)

        # Scrolling to seek
        scroll_controller = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL
        )
        scroll_controller.connect("scroll", self.on_scale_scroll)
        self.scale.add_controller(scroll_controller)

        # 2. Main Content Area (Horizontal)
        content_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        content_box.set_margin_top(12)
        content_box.set_margin_bottom(8)
        content_box.set_margin_start(12)
        content_box.set_margin_end(12)
        self.append(content_box)

        # Cover Art
        from ui.utils import AsyncImage, LikeButton

        self.cover_btn = Gtk.Button()
        self.cover_btn.add_css_class("flat")
        self.cover_btn.add_css_class("link-btn")
        self.cover_btn.set_has_frame(False)
        self.cover_btn.connect("clicked", self._on_cover_btn_clicked)

        self.cover_img = AsyncImage(size=48)
        self.cover_img.set_pixel_size(48)
        self.cover_btn.set_child(self.cover_img)
        content_box.append(self.cover_btn)

        # Metadata (Vertical)
        meta_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        meta_box.set_valign(Gtk.Align.CENTER)
        meta_box.set_hexpand(True)

        self.title_label = Gtk.Label(label="Not Playing")
        self.title_label.set_halign(Gtk.Align.START)
        self.title_label.set_ellipsize(3)  # END
        self.title_label.set_width_chars(1)  # Allow shrinking
        self.title_label.add_css_class("heading")

        self.artist_btn = Gtk.Button()
        self.artist_btn.add_css_class("flat")
        self.artist_btn.add_css_class("link-btn")
        self.artist_btn.set_halign(Gtk.Align.START)
        self.artist_btn.set_has_frame(False)
        self.artist_btn.connect("clicked", self._on_artist_btn_clicked)

        self.artist_label = Gtk.Label(label="")
        self.artist_label.set_ellipsize(3)  # END
        self.artist_label.set_max_width_chars(30)
        self.artist_label.add_css_class("caption")

        self.artist_btn.set_child(self.artist_label)

        meta_box.append(self.title_label)
        meta_box.append(self.artist_btn)

        content_box.append(meta_box)

        # Controls
        controls_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        controls_box.set_valign(Gtk.Align.CENTER)

        # Timings Label
        self.timings_label = Gtk.Label(label="0:00 / 0:00")
        self.timings_label.add_css_class("caption")
        self.timings_label.set_valign(Gtk.Align.CENTER)
        self.timings_label.add_css_class("numeric")
        controls_box.append(self.timings_label)

        # Previous
        self.prev_btn = Gtk.Button(icon_name="media-skip-backward-symbolic")
        self.prev_btn.set_valign(Gtk.Align.CENTER)
        self.prev_btn.add_css_class("flat")
        self.prev_btn.connect("clicked", lambda x: self.player.previous())
        controls_box.append(self.prev_btn)

        # Play/Pause (with buffering spinner)
        self.play_btn = Gtk.Button()
        self.play_btn.set_valign(Gtk.Align.CENTER)
        self.play_btn.add_css_class("circular")
        self.play_btn.connect("clicked", self.on_play_clicked)

        self._play_stack = Gtk.Stack()
        self._play_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._play_stack.set_transition_duration(150)

        self._play_icon = Gtk.Image.new_from_icon_name("media-playback-start-symbolic")
        self._play_stack.add_named(self._play_icon, "icon")

        self._play_spinner = Adw.Spinner()
        self._play_spinner.set_size_request(16, 16)
        self._play_stack.add_named(self._play_spinner, "spinner")

        self.play_btn.set_child(self._play_stack)
        controls_box.append(self.play_btn)

        # Next
        self.next_btn = Gtk.Button(icon_name="media-skip-forward-symbolic")
        self.next_btn.set_valign(Gtk.Align.CENTER)
        self.next_btn.add_css_class("flat")
        self.next_btn.connect("clicked", lambda x: self.player.next())
        controls_box.append(self.next_btn)

        self.volume_btn = Gtk.Button(icon_name="audio-volume-high-symbolic")
        self.volume_btn.add_css_class("flat")
        self.volume_btn.connect("clicked", self.on_volume_btn_clicked)

        self.volume_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_RIGHT
        )
        self.volume_revealer.set_transition_duration(250)

        self.volume_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL)
        self.volume_scale.set_range(0, 1.0)
        self.volume_scale.set_value(self.player.get_volume())
        self.volume_scale.set_size_request(80, -1)
        self.volume_scale.connect("value-changed", self.on_volume_scale_changed)
        self.volume_revealer.set_child(self.volume_scale)

        self.volume_container = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=0
        )
        self.volume_container.set_valign(Gtk.Align.CENTER)
        self.volume_container.append(self.volume_btn)
        self.volume_container.append(self.volume_revealer)

        # Volume Hover Logic
        volume_hover_controller = Gtk.EventControllerMotion()
        volume_hover_controller.connect(
            "enter", lambda *args: self.volume_revealer.set_reveal_child(True)
        )
        volume_hover_controller.connect(
            "leave", lambda *args: self.volume_revealer.set_reveal_child(False)
        )
        self.volume_container.add_controller(volume_hover_controller)

        controls_box.append(self.volume_container)

        # Queue Button
        self.queue_btn = Gtk.ToggleButton(icon_name="view-list-symbolic")
        self.queue_btn.set_valign(Gtk.Align.CENTER)
        self.queue_btn.add_css_class("flat")
        self.queue_btn.set_tooltip_text("Toggle Queue")

        if self.on_queue_click:
            self.queue_btn.connect("clicked", lambda x: self.on_queue_click())

        controls_box.append(self.queue_btn)

        # Like Button (right side, always visible when track loaded)
        self.like_btn = LikeButton(self.player.client, None)
        self.like_btn.set_visible(False)
        self.like_btn.set_valign(Gtk.Align.CENTER)
        controls_box.append(self.like_btn)

        content_box.append(controls_box)
        self.content_box = content_box
        self.controls_box = controls_box

        # Connect signals
        self.player.connect("state-changed", self.on_state_changed)
        self.player.connect("progression", self.on_progression)
        self.player.connect("metadata-changed", self.on_metadata_changed)
        self.player.connect("volume-changed", self.on_volume_changed)

        # Initial state sync
        self._is_buffering_spinner = False
        self.on_state_changed(self.player, self.player.get_state_string())

        # Gestures for Expansion (Connected to content_box for wider hit area)
        self.is_compact = False

        # 1. The Drag Gesture (Strictly for swiping up)
        drag = Gtk.GestureDrag()
        drag.set_propagation_phase(Gtk.PropagationPhase.BUBBLE)
        drag.connect("drag-update", self.on_drag_update)
        # We no longer connect drag-end!
        self.content_box.add_controller(drag)

        # 2. The Click Gesture (Strictly for tapping the background)
        click = Gtk.GestureClick()
        click.set_propagation_phase(Gtk.PropagationPhase.BUBBLE)
        click.connect("released", self.on_bar_tapped)
        self.content_box.add_controller(click)

        # 3. Horizontal swipe for next/previous track
        swipe = Gtk.GestureSwipe()
        swipe.set_propagation_phase(Gtk.PropagationPhase.BUBBLE)
        swipe.connect("swipe", self._on_swipe)
        self.content_box.add_controller(swipe)
        self._skip_cooldown = False

    def set_queue_active(self, active):
        if self.queue_btn.get_active() != active:
            self.queue_btn.set_active(active)

    def set_compact(self, compact):
        self.is_compact = compact
        if compact:
            self.timings_label.set_visible(False)
            self.prev_btn.set_visible(False)
            self.next_btn.set_visible(False)
            self.volume_container.set_visible(False)
            self.queue_btn.set_visible(False)

            self.content_box.set_spacing(6)
            self.controls_box.set_spacing(6)
            self.content_box.set_margin_start(6)
            self.content_box.set_margin_end(6)
        else:
            self.timings_label.set_visible(True)
            self.prev_btn.set_visible(True)
            self.next_btn.set_visible(True)
            self.volume_container.set_visible(True)
            self.queue_btn.set_visible(True)
            self.like_btn.set_visible(bool(self.player.current_video_id))

            self.content_box.set_spacing(12)
            self.controls_box.set_spacing(12)
            self.content_box.set_margin_start(12)
            self.content_box.set_margin_end(12)

    def _on_artist_btn_clicked(self, btn):
        if self.on_artist_click:
            self.on_artist_click()

    def _on_cover_btn_clicked(self, btn):
        if self.on_album_click:
            self.on_album_click()

    def on_scale_change_value(self, scale, scroll, value):
        if self.player.duration > 0:
            self.player.seek(value)
        return False

    def on_scale_scroll(self, controller, dx, dy):
        if self.player.duration <= 0:
            return False

        adj = self.scale.get_adjustment()
        val = adj.get_value()

        # 2 seconds per tick
        step = 2.0
        new_val = val - (dy * step)

        new_val = max(0, min(new_val, self.player.duration))
        adj.set_value(new_val)

        # Debounce the seek and use non-flushing seek for smoothness
        if hasattr(self, "_scroll_seek_id") and self._scroll_seek_id:
            from gi.repository import GLib

            GLib.source_remove(self._scroll_seek_id)

        from gi.repository import GLib

        self._scroll_seek_id = GLib.timeout_add(100, self._do_scroll_seek, new_val)
        return True

    def _do_scroll_seek(self, value):
        # Using flush=True to ensure immediate response during scrolling
        self.player.seek(value, flush=True)
        self._scroll_seek_id = None
        return False

    def _load_css(self):
        css = """
        .player-bar {
            padding: 0px;
            background-color: @headerbar_bg_color;
            border-top: 1px solid @borders;
        }
        .link-btn {
            padding: 0px;
            margin: 0px;
            min-height: 0px;
            background: transparent;
            box-shadow: none;
        }
        .link-btn:hover {
            color: @accent_color;
        }
        .player-scale {
            margin-top: -10px; 
            margin-bottom: -10px; 
            min-height: 20px; /* Larger hit target */
            padding: 0px;
        }
        .player-scale trough {
            min-height: 4px; 
            margin-top: 8px; /* Center visual trough within 20px height */
            margin-bottom: 8px;
            padding: 0px;
        }
        .player-scale slider {
            min-height: 12px;
            min-width: 12px;
            margin: -4px; 
            background-color: white; 
            box-shadow: none; 
        }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode("utf-8"))

        display = Gdk.Display.get_default()
        if display:
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

    def on_metadata_changed(
        self, player, title, artist, thumbnail_url, video_id, like_status
    ):
        print(f"DEBUG-BAR-META-START: video_id={video_id}")
        self.current_title = title
        self.current_artist = artist
        self.title_label.set_label(title)
        self.artist_label.set_label(artist)
        if thumbnail_url:
            self.cover_img.load_url(thumbnail_url)
        else:
            self.cover_img.load_url(None)

        if video_id:
            self.like_btn.set_data(video_id, like_status)
        else:
            self.like_btn.set_visible(False)

        # Show spinner when a new track starts loading
        if video_id and self.player.duration <= 0:
            self._is_buffering_spinner = True
            self._play_stack.set_visible_child_name("spinner")
            self.play_btn.set_sensitive(False)
        print("DEBUG-BAR-META-END")

    def on_play_clicked(self, btn):
        if self.player.get_state_string() == "playing":
            self.player.pause()
        else:
            self.player.play()

    def on_state_changed(self, player, state):
        print(f"DEBUG-BAR-STATE-START: state={state}")
        if state == "loading":
            self.scale.set_value(0)
            self.scale.set_sensitive(False)
            self.timings_label.set_label("0:00 / 0:00")
            self._play_stack.set_visible_child_name("spinner")
            self.play_btn.set_sensitive(False)
            self._is_buffering_spinner = True
        elif state == "playing":
            if self.player.duration <= 0:
                # Buffering — show spinner until we have a valid duration
                self._is_buffering_spinner = True
                self._play_stack.set_visible_child_name("spinner")
                self.play_btn.set_sensitive(False)
                self.scale.set_sensitive(False)
            else:
                self._is_buffering_spinner = False
                self.scale.set_sensitive(True)
                self._play_icon.set_from_icon_name("media-playback-pause-symbolic")
                self._play_stack.set_visible_child_name("icon")
                self.play_btn.set_sensitive(True)
        elif state in ("paused", "stopped"):
            if self._is_buffering_spinner and self.player.duration <= 0:
                # Still buffering—keep spinner visible
                print("DEBUG-BAR-STATE-END")
                return
            if state == "paused":
                self.scale.set_sensitive(True)
            self._play_icon.set_from_icon_name("media-playback-start-symbolic")
            self._play_stack.set_visible_child_name("icon")
            self.play_btn.set_sensitive(True)
            self._is_buffering_spinner = False
        print("DEBUG-BAR-STATE-END")

    def _format_time(self, seconds):
        if seconds < 0:
            return "0:00"
        m = int(seconds // 60)
        s = int(seconds % 60)
        return f"{m}:{s:02d}"

    def on_progression(self, player, pos, dur):
        # Don't update the scale if we're actively scrolling to avoid jitter
        if getattr(self, "_scroll_seek_id", None):
            return
        self.scale.set_range(0, dur)
        self.scale.set_value(pos)
        t = f"{self._format_time(pos)} / {self._format_time(dur)}"
        self.timings_label.set_label(t)

        if getattr(self, "_is_buffering_spinner", False) and dur > 0:
            if self.player.get_state_string() == "playing":
                self._is_buffering_spinner = False
                self.scale.set_sensitive(True)
                self.play_btn.set_sensitive(True)
                self._play_stack.set_visible_child_name("icon")
                self._play_icon.set_from_icon_name("media-playback-pause-symbolic")

    def on_volume_btn_clicked(self, btn):
        is_muted = not self.player.get_mute()
        self.player.set_mute(is_muted)

    def on_volume_scale_changed(self, scale):
        val = scale.get_value()
        self.player.set_volume(val)

    def on_volume_changed(self, player, volume, muted):
        # Use apparent volume (0 if muted) for the scale to match MPRIS
        display_volume = 0.0 if muted else volume

        if abs(self.volume_scale.get_value() - display_volume) > 0.01:
            self.volume_scale.set_value(display_volume)

        # Update Icon
        if muted or volume == 0:
            self.volume_btn.set_icon_name("audio-volume-muted-symbolic")
        elif volume < 0.33:
            self.volume_btn.set_icon_name("audio-volume-low-symbolic")
        elif volume < 0.66:
            self.volume_btn.set_icon_name("audio-volume-medium-symbolic")
        else:
            self.volume_btn.set_icon_name("audio-volume-high-symbolic")

    def _on_swipe(self, gesture, vx, vy):
        if self._skip_cooldown:
            return
        if abs(vx) > abs(vy) and abs(vx) > 200:
            self._skip_cooldown = True
            if vx < 0:
                self.player.next()
            else:
                self.player.previous()
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            from gi.repository import GLib

            GLib.timeout_add(500, self._clear_skip_cooldown)

    def _clear_skip_cooldown(self):
        self._skip_cooldown = False
        return False

    def on_drag_update(self, gesture, offset_x, offset_y):
        # Trigger expansion immediately on upward drag
        if self.is_compact and offset_y < -15:
            self.emit("expand-requested")
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def on_bar_tapped(self, gesture, n_press, x, y):
        if self.is_compact:
            self.emit("expand-requested")
