import gi
import threading

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, GObject, Pango, Gdk, Gio, GLib


class QueueItem(GObject.Object):
    __gtype_name__ = "QueueItem"

    @GObject.Property(type=bool, default=False)
    def is_playing(self):
        return self._is_playing

    @is_playing.setter
    def is_playing(self, value):
        self._is_playing = value

    @GObject.Property(type=bool, default=False)
    def is_paused(self):
        return self._is_paused

    @is_paused.setter
    def is_paused(self, value):
        self._is_paused = value

    def __init__(self, track, index, is_playing, is_paused=False):
        super().__init__()
        self.track = track
        self.index = index
        self._is_playing = is_playing
        self._is_paused = is_paused


class QueueRowWidget(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.add_css_class("queue-row")

        self.model_item = None  # QueueItem
        self.panel = None  # QueuePanel reference

        # Drag Handle
        self.handle = Gtk.Image.new_from_icon_name("list-drag-handle-symbolic")
        self.handle.add_css_class("dim-label")
        self.handle.add_css_class("drag-handle")
        self.append(self.handle)

        # Setup Drag Source
        drag_source = Gtk.DragSource()
        drag_source.set_actions(Gdk.DragAction.MOVE)
        drag_source.connect("prepare", self.on_drag_prepare)
        drag_source.connect("drag-begin", self.on_drag_begin)
        self.handle.add_controller(drag_source)

        # Indicator / Index
        self.indicator_stack = Gtk.Stack()
        self.indicator_lbl = Gtk.Label()
        self.indicator_lbl.add_css_class("dim-label")
        self.indicator_lbl.set_width_chars(3)
        self.indicator_icon = Gtk.Image.new_from_icon_name(
            "media-playback-start-symbolic"  # Default
        )
        self.indicator_icon.add_css_class("accent")

        self.indicator_stack.add_named(self.indicator_lbl, "index")
        self.indicator_stack.add_named(self.indicator_icon, "playing")
        self.append(self.indicator_stack)

        # Info Box
        info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        info_box.set_hexpand(True)

        self.title_lbl = Gtk.Label()
        self.title_lbl.set_halign(Gtk.Align.START)
        self.title_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        self.title_lbl.add_css_class("body")

        self.artist_lbl = Gtk.Label()
        self.artist_lbl.set_halign(Gtk.Align.START)
        self.artist_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        self.artist_lbl.add_css_class("caption")
        self.artist_lbl.add_css_class("dim-label")

        info_box.append(self.title_lbl)
        info_box.append(self.artist_lbl)
        self.append(info_box)

        # Drop Target
        drop_target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        drop_target.connect("drop", self.on_drop)
        self.add_controller(drop_target)

    def bind(self, item, panel):
        if self.model_item:
            try:
                self.model_item.disconnect_by_func(self._on_item_property_changed)
            except Exception:
                pass

        self.model_item = item
        self.panel = panel

        item.connect("notify::is-playing", self._on_item_property_changed)
        item.connect("notify::is-paused", self._on_item_property_changed)
        self._update_playing_ui()

    def _on_item_property_changed(self, item, pspec):
        self._update_playing_ui()

    def _update_playing_ui(self):
        item = self.model_item
        if not item:
            return

        track = item.track
        self.title_lbl.set_label(track.get("title", "Unknown"))

        artist_txt = track.get("artist")
        if isinstance(artist_txt, list):
            artist_txt = ", ".join([a.get("name", "") for a in artist_txt])
        elif not artist_txt and "artists" in track:
            artist_txt = ", ".join(
                [a.get("name", "") for a in track.get("artists", [])]
            )
        if not artist_txt:
            artist_txt = "Unknown"
        self.artist_lbl.set_label(artist_txt)

        if item.is_playing:
            self.add_css_class("playing")
            if item.is_paused:
                self.indicator_icon.set_from_icon_name("media-playback-start-symbolic")
            else:
                self.indicator_icon.set_from_icon_name("media-playback-pause-symbolic")
            self.indicator_stack.set_visible_child_name("playing")
        else:
            self.remove_css_class("playing")
            # For non-playing items, we just show the index
            self.indicator_lbl.set_label(str(item.index + 1))
            self.indicator_stack.set_visible_child_name("index")

    def on_drag_prepare(self, source, x, y):
        if self.model_item:
            value = GObject.Value(str, str(self.model_item.index))
            return Gdk.ContentProvider.new_for_value(value)
        return None

    def on_drag_begin(self, source, drag):
        paintable = Gtk.WidgetPaintable.new(self)
        source.set_icon(paintable, 0, 0)

    def on_drop(self, target, value, x, y):
        try:
            source_index = int(value)
            if self.model_item and source_index != self.model_item.index:
                if self.panel:
                    self.panel._on_row_move(source_index, self.model_item.index)
            return True
        except ValueError:
            return False


class QueuePanel(Gtk.Box):
    def __init__(self, player):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.player = player
        self.set_size_request(320, -1)  # Minimum width sidebar
        self.add_css_class("background")
        self.add_css_class("queue-panel")  # For potential styling

        # Header
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        header.set_margin_top(12)
        header.set_margin_bottom(12)
        header.set_margin_start(12)
        header.set_margin_end(12)

        title = Gtk.Label(label="Queue")
        title.add_css_class("title-4")
        title.set_hexpand(True)
        title.set_halign(Gtk.Align.START)
        header.append(title)

        # Shuffle Toggle
        self.shuffle_btn = Gtk.ToggleButton(icon_name="media-playlist-shuffle-symbolic")
        self.shuffle_btn.set_tooltip_text("Shuffle Queue")
        self.shuffle_btn.connect("clicked", self._on_shuffle_clicked)
        header.append(self.shuffle_btn)

        # Repeat Toggle
        self.repeat_btn = Gtk.Button(icon_name="media-playlist-consecutive-symbolic")
        self.repeat_btn.set_tooltip_text("Repeat Mode")
        self.repeat_btn.connect("clicked", self._on_repeat_clicked)
        header.append(self.repeat_btn)

        clear_btn = Gtk.Button(label="Clear")
        clear_btn.connect("clicked", lambda x: self.player.clear_queue())
        header.append(clear_btn)

        # More Menu
        self.action_group = Gio.SimpleActionGroup()
        self.insert_action_group("queue", self.action_group)

        action_add = Gio.SimpleAction.new(
            "add_all_to_playlist", GLib.VariantType.new("s")
        )
        action_add.connect("activate", self._on_add_all_to_playlist)
        self.action_group.add_action(action_add)

        self.more_btn = Gtk.MenuButton(icon_name="view-more-symbolic")
        self.more_btn.set_tooltip_text("More Options")

        self.more_menu_model = Gio.Menu()
        self.playlist_menu = Gio.Menu()
        self.more_menu_model.append_submenu("Add all to Playlist", self.playlist_menu)
        self.more_btn.set_menu_model(self.more_menu_model)

        header.append(self.more_btn)

        self.append(header)

        # ListView Setup
        self.store = Gio.ListStore(item_type=QueueItem)
        self.selection_model = Gtk.SingleSelection(model=self.store)
        self.selection_model.set_autoselect(False)
        self.selection_model.connect("selection-changed", self._on_selection_changed)

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_factory_setup)
        factory.connect("bind", self._on_factory_bind)

        self.list_view = Gtk.ListView(model=self.selection_model, factory=factory)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_child(self.list_view)
        self.append(scrolled)

        # Signals
        self.player.connect("state-changed", self._on_player_update)
        self.player.connect("metadata-changed", self._on_player_update)
        self.connect("map", self._on_map)  # Refresh when visible

        self._programmatic_update = False

        # Initial Populate
        self._populate()
        self._update_shuffle_state()
        self._update_repeat_state()

    def _refresh_playlists_menu(self):
        self.playlist_menu.remove_all()
        playlists = self.player.client.get_editable_playlists()
        for p in playlists:
            title = p.get("title", "Untitled")
            pid = p.get("playlistId")
            if pid:
                # Use a specific action name that includes the playlist ID
                self.playlist_menu.append(title, f"queue.add_all_to_playlist('{pid}')")

    def _on_add_all_to_playlist(self, action, param):
        playlist_id = param.get_string()
        video_ids = [t.get("videoId") for t in self.player.queue if t.get("videoId")]

        if not video_ids:
            return

        def thread_func():
            success = self.player.client.add_playlist_items(playlist_id, video_ids)
            if success:
                msg = f"Added {len(video_ids)} tracks to playlist"
                print(msg)
                GLib.idle_add(self._show_toast, msg)
            else:
                GLib.idle_add(self._show_toast, "Failed to add tracks")

        threading.Thread(target=thread_func, daemon=True).start()

    def _on_map(self, *args):
        # Refresh list when sidebar becomes visible - but only if count changed
        if self.store.get_n_items() != len(self.player.queue):
            self._populate()
        else:
            self._update_item_states()

        self._refresh_playlists_menu()
        self._update_shuffle_state()
        self._update_repeat_state()
        GLib.idle_add(self._scroll_to_current)

    def _scroll_to_current(self):
        idx = self.player.current_queue_index
        if idx >= 0 and idx < self.store.get_n_items():
            # Scroll to item
            self.list_view.scroll_to(
                idx, Gtk.ListScrollFlags.FOCUS | Gtk.ListScrollFlags.SELECT, None
            )

    def _on_shuffle_clicked(self, btn):
        self.player.shuffle_queue()
        self._scroll_to_current()

    def _update_shuffle_state(self):
        if self.player.shuffle_mode != self.shuffle_btn.get_active():
            self.shuffle_btn.set_active(self.player.shuffle_mode)

        if self.player.shuffle_mode:
            self.shuffle_btn.add_css_class("accent")
        else:
            self.shuffle_btn.remove_css_class("accent")

    def _update_repeat_state(self):
        mode = getattr(self.player, "repeat_mode", "none")
        if mode == "track":
            self.repeat_btn.set_icon_name("media-playlist-repeat-song-symbolic")
            self.repeat_btn.add_css_class("accent")
        elif mode == "all":
            self.repeat_btn.set_icon_name("media-playlist-repeat-symbolic")
            self.repeat_btn.add_css_class("accent")
        else:
            self.repeat_btn.set_icon_name("media-playlist-consecutive-symbolic")
            self.repeat_btn.remove_css_class("accent")

    def _on_repeat_clicked(self, btn):
        mode = getattr(self.player, "repeat_mode", "none")
        if mode == "none":
            self.player.set_repeat_mode("all")
        elif mode == "all":
            self.player.set_repeat_mode("track")
        else:
            self.player.set_repeat_mode("none")

    def _populate(self):
        self._programmatic_update = True
        try:
            queue = self.player.queue
            current_idx = self.player.current_queue_index

            items = []
            player_state = self.player.get_state_string()
            is_paused = player_state in ("paused", "stopped")

            for i, track in enumerate(queue):
                items.append(QueueItem(track, i, i == current_idx, is_paused))

            self.store.splice(0, self.store.get_n_items(), items)
            self._last_queue_len = len(queue)

            # Restore selection to current index
            if current_idx >= 0 and current_idx < len(items):
                self.selection_model.set_selected(current_idx)

            if self.get_mapped():
                GLib.idle_add(self._scroll_to_current)
        finally:
            self._programmatic_update = False

    def _on_factory_setup(self, factory, list_item):
        widget = QueueRowWidget()
        list_item.set_child(widget)

    def _on_factory_bind(self, factory, list_item):
        widget = list_item.get_child()
        item = list_item.get_item()
        widget.bind(item, self)

    def _on_selection_changed(self, model, position, n_items):
        if self._programmatic_update:
            return

        item = model.get_selected_item()
        if item:
            # Prevent re-playing current track if clicked
            if item.index == self.player.current_queue_index:
                return

            self.player.play_queue_index(item.index)

    def _on_row_move(self, old_index, new_index):
        if self.player.move_queue_item(old_index, new_index):
            pass

    def _on_player_update(self, player, *args):
        self._update_shuffle_state()
        self._update_repeat_state()

        # Determine if this is a state-changed or metadata-changed signal
        # state-changed (player, state) -> args = (state,)
        # metadata-changed (player, title, artist, thumb, ...) -> args = (title, ...)

        state = args[0] if len(args) == 1 else None

        # If queue length changed OR structural update requested, we MUST repopulate
        if state == "queue-updated" or self.store.get_n_items() != len(
            self.player.queue
        ):
            self._populate()
        else:
            # Otherwise, just update indicators (very efficient!)
            self._update_item_states()

    def _update_item_states(self):
        current_idx = self.player.current_queue_index
        n = self.store.get_n_items()

        self._programmatic_update = True
        try:
            player_state = self.player.get_state_string()
            is_paused_global = player_state in ("paused", "stopped")

            for i in range(n):
                item = self.store.get_item(i)
                was_playing = item.is_playing
                was_paused = item.is_paused
                is_playing = i == current_idx

                if was_playing != is_playing or was_paused != is_paused_global:
                    item.is_playing = is_playing
                    item.is_paused = is_paused_global

            if current_idx >= 0 and current_idx < n:
                self.selection_model.set_selected(current_idx)

            if self.get_mapped():
                GLib.idle_add(self._scroll_to_current)
        finally:
            self._programmatic_update = False

    def _show_toast(self, message):
        root = self.get_root()
        if hasattr(root, "add_toast"):
            root.add_toast(message)
