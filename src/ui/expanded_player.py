import gi
from gi.repository import Gtk, Adw, GObject, GLib, Gdk, Pango
from ui.utils import AsyncPicture, LikeButton, MarqueeLabel
from ui.queue_panel import QueuePanel


class ExpandedPlayer(Gtk.Box):
    @GObject.Signal
    def dismiss(self):
        pass

    def _make_cover(self):
        img = AsyncPicture(crop_to_square=True)
        img.add_css_class("rounded")
        img.set_halign(Gtk.Align.FILL)
        img.set_valign(Gtk.Align.FILL)
        img.set_hexpand(False)
        img.set_vexpand(True)
        img.set_content_fit(Gtk.ContentFit.COVER)
        return img

    def __init__(self, player, on_artist_click=None, on_album_click=None, **kwargs):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, **kwargs)
        self.player = player
        self.on_artist_click = on_artist_click
        self.on_album_click = on_album_click

        # 1. The Stack: Transitions between Player and Queue
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.stack.set_vexpand(True)
        self.append(self.stack)

        # ==========================================
        # PAGE 1: THE PLAYER VIEW
        # ==========================================
        self.player_scroll = Gtk.ScrolledWindow()
        self.player_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.player_scroll.set_propagate_natural_height(True)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        main_box.set_margin_top(0)
        main_box.set_margin_bottom(16)
        main_box.set_margin_start(24)
        main_box.set_margin_end(24)

        # Header Spacer
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        header_box.set_margin_top(8)
        header_box.set_margin_bottom(8)
        main_box.append(header_box)

        self.covers = []
        self.cover_img = self._make_cover()  # fallback center

        self.carousel = Adw.Carousel()
        self.carousel.set_spacing(16)
        self.carousel.set_interactive(True)

        # The frame clips to show only the center cover
        cover_frame = Gtk.AspectFrame(ratio=1.0, obey_child=False)
        cover_frame.set_halign(Gtk.Align.CENTER)
        cover_frame.set_valign(Gtk.Align.CENTER)
        cover_frame.set_vexpand(True)
        cover_frame.set_hexpand(True)
        cover_frame.set_overflow(Gtk.Overflow.HIDDEN)
        cover_frame.set_child(self.carousel)
        self._cover_frame = cover_frame

        # Add tap gesture for album navigation
        cover_click = Gtk.GestureClick()
        cover_click.connect("pressed", self._on_cover_pressed)
        cover_click.connect("released", self._on_cover_tapped)
        cover_frame.add_controller(cover_click)

        self._ignore_page_change = False
        self.carousel.connect("notify::position", self._on_carousel_position_changed)
        self.connect("map", self._on_map)

        main_box.append(cover_frame)

        # Metadata & Like
        meta_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        meta_row.set_halign(Gtk.Align.FILL)

        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text_box.set_hexpand(True)
        text_box.set_valign(Gtk.Align.CENTER)

        # --- Marquee Title ---
        self.title_label = MarqueeLabel()
        self.title_label.set_label("Not Playing")
        self.title_label.add_css_class("title-3")

        self.artist_btn = Gtk.Button()
        self.artist_btn.add_css_class("flat")
        self.artist_btn.add_css_class("link-btn")
        self.artist_btn.set_halign(Gtk.Align.START)
        self.artist_btn.set_has_frame(False)
        self.artist_btn.connect("clicked", self._on_artist_btn_clicked)

        self.artist_label = Gtk.Label(label="")
        self.artist_label.add_css_class("heading")
        self.artist_label.set_opacity(0.7)
        self.artist_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.artist_label.set_halign(Gtk.Align.START)

        self.artist_btn.set_child(self.artist_label)

        text_box.append(self.title_label)
        text_box.append(self.artist_btn)

        self.like_btn = LikeButton(self.player.client, None)
        self.like_btn.set_visible(False)
        self.like_btn.set_valign(Gtk.Align.CENTER)

        meta_row.append(text_box)
        meta_row.append(self.like_btn)
        main_box.append(meta_row)

        # Progress Slider
        progress_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL)
        self.scale.set_range(0, 100)
        self.scale.connect("change-value", self.on_scale_change_value)
        progress_box.append(self.scale)

        timings_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.pos_label = Gtk.Label(label="0:00")
        self.pos_label.add_css_class("caption")
        self.pos_label.add_css_class("numeric")

        dur_spacer = Gtk.Box()
        dur_spacer.set_hexpand(True)

        self.dur_label = Gtk.Label(label="0:00")
        self.dur_label.add_css_class("caption")
        self.dur_label.add_css_class("numeric")

        timings_box.append(self.pos_label)
        timings_box.append(dur_spacer)
        timings_box.append(self.dur_label)
        progress_box.append(timings_box)
        main_box.append(progress_box)

        # Media Controls
        controls_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=24)
        controls_box.set_halign(Gtk.Align.CENTER)
        controls_box.set_margin_top(8)

        self.prev_btn = Gtk.Button(icon_name="media-skip-backward-symbolic")
        self.prev_btn.set_size_request(56, 56)
        self.prev_btn.add_css_class("circular")
        self.prev_btn.set_valign(Gtk.Align.CENTER)
        self.prev_btn.connect("clicked", lambda x: self.player.previous())

        self.play_btn = Gtk.Button()
        self.play_btn.set_size_request(80, 80)
        self.play_btn.add_css_class("circular")
        self.play_btn.add_css_class("suggested-action")
        self.play_btn.set_valign(Gtk.Align.CENTER)
        self.play_btn.connect("clicked", self.on_play_clicked)

        self.play_btn_stack = Gtk.Stack()
        self.play_btn_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.play_btn_stack.set_transition_duration(200)

        self.play_icon = Gtk.Image.new_from_icon_name("media-playback-start-symbolic")
        self.play_icon.set_pixel_size(32)
        self.play_btn_stack.add_named(self.play_icon, "icon")

        self.play_spinner = Adw.Spinner()
        self.play_spinner.set_size_request(32, 32)
        self.play_btn_stack.add_named(self.play_spinner, "spinner")

        self.play_btn.set_child(self.play_btn_stack)

        self.next_btn = Gtk.Button(icon_name="media-skip-forward-symbolic")
        self.next_btn.set_size_request(56, 56)
        self.next_btn.add_css_class("circular")
        self.next_btn.set_valign(Gtk.Align.CENTER)
        self.next_btn.connect("clicked", lambda x: self.player.next())

        controls_box.append(self.prev_btn)
        controls_box.append(self.play_btn)
        controls_box.append(self.next_btn)
        main_box.append(controls_box)

        # Bottom Row: Volume & Queue Toggle
        bottom_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        bottom_box.set_halign(Gtk.Align.FILL)
        bottom_box.set_margin_top(24)

        self.vol_icon = Gtk.Image.new_from_icon_name("audio-volume-high-symbolic")
        self.vol_icon.set_opacity(0.7)

        self.volume_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL)
        self.volume_scale.set_range(0, 1.0)
        self.volume_scale.set_hexpand(True)
        self.volume_scale.set_value(self.player.get_volume())
        self.volume_scale.connect("value-changed", self.on_volume_scale_changed)

        self.show_queue_btn = Gtk.Button(icon_name="view-list-symbolic")
        self.show_queue_btn.add_css_class("flat")
        self.show_queue_btn.add_css_class("circular")
        self.show_queue_btn.connect(
            "clicked", lambda x: self.stack.set_visible_child_name("queue")
        )

        bottom_box.append(self.vol_icon)
        bottom_box.append(self.volume_scale)
        bottom_box.append(self.show_queue_btn)
        main_box.append(bottom_box)

        self.player_scroll.set_child(main_box)
        self.stack.add_named(self.player_scroll, "player")

        # ==========================================
        # PAGE 2: THE QUEUE VIEW
        # ==========================================
        queue_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        queue_box.set_margin_top(12)

        q_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        q_header.set_margin_start(16)
        q_header.set_margin_end(16)
        q_header.set_margin_bottom(12)

        back_btn = Gtk.Button(icon_name="go-previous-symbolic")
        back_btn.add_css_class("flat")
        back_btn.add_css_class("circular")
        back_btn.connect(
            "clicked", lambda x: self.stack.set_visible_child_name("player")
        )

        q_title = Gtk.Label(label="")
        q_title.add_css_class("heading")
        q_title.set_hexpand(True)
        q_title.set_halign(Gtk.Align.CENTER)

        q_spacer = Gtk.Box()
        q_spacer.set_size_request(32, -1)

        q_header.append(back_btn)
        q_header.append(q_title)
        q_header.append(q_spacer)
        queue_box.append(q_header)

        self.queue_panel = QueuePanel(self.player)
        self.queue_panel.set_vexpand(True)
        queue_box.append(self.queue_panel)

        self.stack.add_named(queue_box, "queue")

        # Connect Signals
        self.player.connect("metadata-changed", self.on_metadata_changed)
        self.player.connect("progression", self.on_progression)
        self.player.connect("state-changed", self.on_state_changed)
        self.player.connect("volume-changed", self.on_volume_changed)

        # Initial state sync
        self._is_buffering_spinner = False
        self.on_state_changed(self.player, self.player.get_state_string())

    def _on_map(self, widget):
        GLib.idle_add(self._center_carousel)

    def _center_carousel(self):
        self._ignore_page_change = True
        self.carousel.scroll_to(self.cover_img, animate=False)
        self._ignore_page_change = False
        return False

    # --- SIGNAL HANDLERS ---
    def on_metadata_changed(
        self, player, title, artist, thumbnail_url, video_id, like_status
    ):
        self.title_label.set_label(title)
        self.artist_label.set_label(artist)

        if thumbnail_url:
            url_max = thumbnail_url.replace("w120-h120", "w640-h640").replace(
                "sddefault", "maxresdefault"
            )
            url_sd = url_max.replace("maxresdefault", "sddefault")
            url_hq = url_max.replace("maxresdefault", "hqdefault")
            self.cover_img.load_url(url_max, fallbacks=[url_hq, url_sd])
        else:
            self.cover_img.load_url(None)

        if video_id:
            self.like_btn.set_data(video_id, like_status)
            self.like_btn.set_visible(True)
        else:
            self.like_btn.set_visible(False)

        # Preload neighbor covers and sync queue
        self._sync_carousel_queue()

        # Show spinner when a new track starts loading
        if video_id and self.player.duration <= 0:
            self._is_buffering_spinner = True
            self.play_btn_stack.set_visible_child_name("spinner")
            self.play_btn.set_sensitive(False)

    def _get_track_thumb(self, index):
        """Get a thumbnail URL for a track at the given queue index."""
        if index < 0 or index >= len(self.player.queue):
            return None
        track = self.player.queue[index]
        thumb = track.get("thumb")
        if not thumb and "thumbnails" in track:
            thumbs = track.get("thumbnails", [])
            if thumbs:
                thumb = thumbs[-1]["url"]
        if thumb:
            return thumb.replace("w120-h120", "w640-h640").replace(
                "sddefault", "maxresdefault"
            )
        return None

    def _sync_carousel_queue(self):
        """Sync carousel sizing to match queue and lazy-load neighbors."""
        queue_len = len(self.player.queue)
        idx = self.player.current_queue_index

        if queue_len == 0:
            return

        self._ignore_page_change = True

        # Adjust covers array to match exact queue length
        while len(self.covers) > queue_len:
            cover = self.covers.pop()
            if cover.get_parent() == self.carousel:
                self.carousel.remove(cover)

        while len(self.covers) < queue_len:
            cover = self._make_cover()
            self.covers.append(cover)
            self.carousel.append(cover)

        if 0 <= idx < len(self.covers):
            self.cover_img = self.covers[idx]

        if 0 <= idx < len(self.covers):
            self.cover_img = self.covers[idx]

        self._last_lazy_idx = -1  # Force full reload on queue sync
        self._lazy_load_covers_around(idx)

        if 0 <= idx < len(self.covers):
            self.carousel.scroll_to(self.covers[idx], animate=False)

        GLib.timeout_add(200, self._allow_page_change)

    def _lazy_load_covers_around(self, center_idx):
        if center_idx == getattr(self, "_last_lazy_idx", -1):
            return
        self._last_lazy_idx = center_idx

        # Lazy load +/- 5 covers around the visual center
        for i, cover in enumerate(self.covers):
            in_range = abs(i - center_idx) <= 5

            if in_range:
                thumb = self._get_track_thumb(i)
                if thumb:
                    if not cover.get_visible():
                        cover.set_visible(True)

                    # Target High-Res for actively playing song
                    if i == self.player.current_queue_index:
                        url_max = thumb.replace("w120-h120", "w640-h640").replace(
                            "sddefault", "maxresdefault"
                        )
                        target_url = url_max
                        fallbacks = [
                            url_max.replace("maxresdefault", "hqdefault"),
                            url_max.replace("maxresdefault", "sddefault"),
                        ]
                    else:
                        target_url = thumb
                        fallbacks = [
                            thumb.replace("maxresdefault", "hqdefault"),
                            thumb.replace("maxresdefault", "sddefault"),
                        ]

                    if cover.url != target_url:
                        cover.load_url(target_url, fallbacks=fallbacks)
                else:
                    if cover.get_visible():
                        cover.set_visible(False)
                    if cover.url is not None:
                        cover.load_url(None)
            else:
                if not cover.get_visible():
                    cover.set_visible(True)
                if cover.url is not None:
                    cover.load_url(None)

    def _allow_page_change(self):
        self._ignore_page_change = False
        return False

    def on_progression(self, player, pos, dur):
        self.scale.set_range(0, dur)
        self.scale.set_value(pos)
        self.pos_label.set_label(self._format_time(pos))
        self.dur_label.set_label(self._format_time(dur))

        # Hide spinner once we have valid duration
        if getattr(self, "_is_buffering_spinner", False) and dur > 0:
            if self.player.get_state_string() == "playing":
                self._is_buffering_spinner = False
                self.play_btn.set_sensitive(True)
                self.play_btn_stack.set_visible_child_name("icon")
                self.play_icon.set_from_icon_name("media-playback-pause-symbolic")

    def on_scale_change_value(self, scale, scroll, value):
        if self.player.duration > 0:
            self.player.seek(value)
        return False

    def _format_time(self, seconds):
        if seconds < 0:
            return "0:00"
        m = int(seconds // 60)
        s = int(seconds % 60)
        return f"{m}:{s:02d}"

    def on_play_clicked(self, btn):
        if self.player.get_state_string() == "playing":
            self.player.pause()
        else:
            self.player.play()

    def on_state_changed(self, player, state):
        print(f"DEBUG-EXPANDED-STATE-START: state={state}")
        if state == "queue-updated":
            self._sync_carousel_queue()
            print("DEBUG-EXPANDED-STATE-END (queue-updated)")
            return

        if state == "loading":
            self.play_btn_stack.set_visible_child_name("spinner")
            self.play_btn.set_sensitive(False)
            self._is_buffering_spinner = True
            print("DEBUG-EXPANDED-STATE-END (loading)")
            return

        if state == "playing" and self.player.duration <= 0:
            # We are playing but buffering stream—keep spinner active until duration > 0
            self.play_btn_stack.set_visible_child_name("spinner")
            self.play_btn.set_sensitive(False)
            self._is_buffering_spinner = True
            print("DEBUG-EXPANDED-STATE-END (playing-buffering)")
            return

        if (
            self._is_buffering_spinner
            and self.player.duration <= 0
            and state in ("paused", "stopped")
        ):
            # Still buffering—keep spinner visible
            print("DEBUG-EXPANDED-STATE-END (still-buffering)")
            return

        self._is_buffering_spinner = False
        self.play_btn_stack.set_visible_child_name("icon")
        self.play_btn.set_sensitive(True)
        icon = (
            "media-playback-pause-symbolic"
            if state == "playing"
            else "media-playback-start-symbolic"
        )
        self.play_icon.set_from_icon_name(icon)
        print("DEBUG-EXPANDED-STATE-END")

    def on_volume_scale_changed(self, scale):
        self.player.set_volume(scale.get_value())

    def on_volume_changed(self, player, volume, muted):
        # Use apparent volume (0 if muted) for the scale to match MPRIS
        display_volume = 0.0 if muted else volume

        if abs(self.volume_scale.get_value() - display_volume) > 0.01:
            self.volume_scale.set_value(display_volume)

        # Update Icon
        if muted or volume == 0:
            self.vol_icon.set_from_icon_name("audio-volume-muted-symbolic")
        elif volume < 0.33:
            self.vol_icon.set_from_icon_name("audio-volume-low-symbolic")
        elif volume < 0.66:
            self.vol_icon.set_from_icon_name("audio-volume-medium-symbolic")
        else:
            self.vol_icon.set_from_icon_name("audio-volume-high-symbolic")

    def _on_artist_btn_clicked(self, btn):
        if self.on_artist_click:
            self.on_artist_click()
        self.emit("dismiss")

    def _on_cover_pressed(self, gesture, n_press, x, y):
        self._press_x = x
        self._press_y = y

    def _on_cover_tapped(self, gesture, n_press, x, y):
        # Ignore false clicks generated during a swiping drag
        if hasattr(self, "_press_x"):
            if abs(x - self._press_x) > 15 or abs(y - self._press_y) > 15:
                # User was swiping the carousel
                return

        if self.on_album_click:
            self.on_album_click()
        self.emit("dismiss")

    # --- ADW.CAROUSEL GESTURE HANDLERS ---

    def _on_carousel_position_changed(self, carousel, param):
        if getattr(self, "_ignore_page_change", False):
            return

        pos = carousel.get_position()
        idx = int(round(pos))

        # Dynamically load array ranges during scroll
        if 0 <= idx < len(self.covers):
            self._lazy_load_covers_around(idx)

        # Only trigger when the carousel float position essentially reaches the target page
        if abs(pos - idx) > 0.001:
            return

        active_page = carousel.get_nth_page(idx)

        try:
            new_idx = self.covers.index(active_page)
        except ValueError:
            return

        if new_idx != self.player.current_queue_index:
            print(f"DEBUG Carousel: Swiped directly to queue index {new_idx}")
            self._ignore_page_change = True

            if 0 <= new_idx < len(self.player.queue):

                def _do_jump(jump_idx):
                    self.player.current_queue_index = jump_idx
                    self.player._play_current_index()
                    self.player.emit("state-changed", "queue-updated")
                    return False

                GLib.idle_add(_do_jump, new_idx)
