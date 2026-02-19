from gi.repository import Gtk, Adw, GObject, Gdk

class PlayerBar(Gtk.Box):
    def __init__(self, player, on_artist_click=None, on_queue_click=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.player = player
        self.on_artist_click = on_artist_click
        self.on_queue_click = on_queue_click
        self.add_css_class("background") # Generic background
        self.add_css_class("player-bar") # Custom class for specific styling
        
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
        
        # 2. Main Content Area (Horizontal)
        content_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        content_box.set_margin_top(12) 
        content_box.set_margin_bottom(8)
        content_box.set_margin_start(12)
        content_box.set_margin_end(12)
        self.append(content_box)
        
        # Cover Art
        from ui.utils import AsyncImage
        self.cover_img = AsyncImage(size=48)
        self.cover_img.set_pixel_size(48)
        content_box.append(self.cover_img)
        
        # Metadata (Vertical)
        meta_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        meta_box.set_valign(Gtk.Align.CENTER)
        meta_box.set_hexpand(True)
        
        self.title_label = Gtk.Label(label="Not Playing")
        self.title_label.set_halign(Gtk.Align.START)
        self.title_label.set_ellipsize(3) # END
        self.title_label.set_width_chars(1) # Allow shrinking
        self.title_label.add_css_class("heading")
        
        self.artist_btn = Gtk.Button()
        self.artist_btn.add_css_class("flat")
        self.artist_btn.add_css_class("link-btn")
        self.artist_btn.set_halign(Gtk.Align.START)
        self.artist_btn.set_has_frame(False)
        self.artist_btn.connect("clicked", self._on_artist_btn_clicked)
        
        self.artist_label = Gtk.Label(label="")
        self.artist_label.set_ellipsize(3) # END
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
        
        # Play/Pause
        self.play_btn = Gtk.Button(icon_name="media-playback-start-symbolic")
        self.play_btn.set_valign(Gtk.Align.CENTER)
        self.play_btn.add_css_class("circular")
        self.play_btn.connect("clicked", self.on_play_clicked)
        controls_box.append(self.play_btn)
        
        # Next
        self.next_btn = Gtk.Button(icon_name="media-skip-forward-symbolic")
        self.next_btn.set_valign(Gtk.Align.CENTER)
        self.next_btn.add_css_class("flat")
        self.next_btn.connect("clicked", lambda x: self.player.next())
        controls_box.append(self.next_btn)
        
        # Queue Button
        self.queue_btn = Gtk.ToggleButton(icon_name="view-list-symbolic")
        self.queue_btn.set_valign(Gtk.Align.CENTER)
        self.queue_btn.add_css_class("flat")
        self.queue_btn.set_tooltip_text("Toggle Queue")
        
        if self.on_queue_click:
             self.queue_btn.connect("clicked", lambda x: self.on_queue_click())
        
        controls_box.append(self.queue_btn)
        
        content_box.append(controls_box)
        self.content_box = content_box
        self.controls_box = controls_box

        # Connect signals
        self.player.connect("state-changed", self.on_state_changed)
        self.player.connect("progression", self.on_progression)
        self.player.connect("metadata-changed", self.on_metadata_changed)

    def set_queue_active(self, active):
        if self.queue_btn.get_active() != active:
            self.queue_btn.set_active(active)

    def set_compact(self, compact):
        if compact:
            self.timings_label.set_visible(False)
            self.content_box.set_spacing(6)
            self.controls_box.set_spacing(6)
            self.content_box.set_margin_start(6)
            self.content_box.set_margin_end(6)
        else:
            self.timings_label.set_visible(True)
            self.content_box.set_spacing(12)
            self.controls_box.set_spacing(12)
            self.content_box.set_margin_start(12)
            self.content_box.set_margin_end(12)


        
    def _on_artist_btn_clicked(self, btn):
        if self.on_artist_click:
            self.on_artist_click()

    def on_scale_change_value(self, scale, scroll, value):
        if self.duration > 0:
            self.player.seek(value)

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
        provider.load_from_data(css.encode('utf-8'))
        
        display = Gdk.Display.get_default()
        if display:
             Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def on_play_clicked(self, btn):
        self.player.play()

    def on_metadata_changed(self, player, title, artist, thumbnail_url):
        self.title_label.set_label(title)
        self.artist_label.set_label(artist)
        if thumbnail_url:
            self.cover_img.load_url(thumbnail_url)
        else:
            self.cover_img.load_url(None)

    def on_state_changed(self, player, text):
        #print(f"State: {text}")
        if text == "loading":
             self.scale.set_value(0)
             self.scale.set_sensitive(False)
             self.timings_label.set_label("0:00 / 0:00")
             
        elif text == "playing":
            self.scale.set_sensitive(True)
            self.play_btn.set_icon_name("media-playback-pause-symbolic")
            try:
                self.play_btn.disconnect_by_func(self.on_play_clicked)
            except TypeError:
                pass
            if not self.play_btn.handler_is_connected(self.play_btn.connect("clicked", self.on_pause_clicked)):
                 pass 

            try:
                self.play_btn.disconnect_by_func(self.on_pause_clicked)
            except TypeError:
                pass
            self.play_btn.connect("clicked", self.on_pause_clicked)
            
        elif text == "paused" or text == "stopped":
            # Keep sensitive for paused/stopped? usually yes for paused.
            if text == "paused":
                self.scale.set_sensitive(True)
            self.play_btn.set_icon_name("media-playback-start-symbolic")
            try:
                self.play_btn.disconnect_by_func(self.on_pause_clicked)
            except TypeError:
                pass
            try:
                self.play_btn.disconnect_by_func(self.on_play_clicked)
            except TypeError:
                pass
            self.play_btn.connect("clicked", self.on_play_clicked)

    def on_pause_clicked(self, btn):
        self.player.pause()

    def _format_time(self, seconds):
        if seconds < 0: return "0:00"
        m = int(seconds // 60)
        s = int(seconds % 60)
        return f"{m}:{s:02d}"

    def on_progression(self, player, pos, dur):
        self.scale.set_range(0, dur)
        self.scale.set_value(pos)
        t = f"{self._format_time(pos)} / {self._format_time(dur)}"
        self.timings_label.set_label(t)

    def on_scale_change_value(self, scale, scroll, value):
        self.player.seek(value)
        return False
