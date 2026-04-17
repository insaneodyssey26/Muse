import threading
import os
import tempfile
from gi.repository import Gtk, Adw, GObject, GLib, Pango, Gdk, Gio, GdkPixbuf
from api.client import MusicClient
from ui.utils import AsyncImage, LikeButton, get_yt_music_link
from ui.crop_dialog import ImageCropDialog

# ── GObject Models ────────────────────────────────────────────────────────────


class HeaderItem(GObject.Object):
    __gtype_name__ = "HeaderItem"

    def __init__(self):
        super().__init__()


class TrackItem(GObject.Object):
    __gtype_name__ = "TrackItem"

    def __init__(self, data: dict):
        super().__init__()
        self.data = data


# ── Page ──────────────────────────────────────────────────────────────────────


class PlaylistPage(Adw.Bin):
    __gsignals__ = {
        "header-title-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,))
    }

    def __init__(self, player, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.player = player
        self.connect("map", self._on_map)
        self.connect("unmap", self._on_unmap)
        self.client = MusicClient()
        self.playlist_id = None
        self.playlist_title_text = ""
        self.playlist_description_text = ""
        self._is_previewing_cover = False
        self.is_owned = False
        self.is_editable = False

        # ── 1. Header UI Container ────────────────────────────────────────────
        self.header_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.header_container.set_margin_top(24)
        self.header_container.set_margin_bottom(12)

        self.header_info_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=24
        )
        self.header_info_box.set_valign(Gtk.Align.START)

        self.cover_img = AsyncImage(size=200, player=self.player)
        self.cover_img.set_valign(Gtk.Align.START)

        self.cover_wrapper = Gtk.Box()
        self.cover_wrapper.set_overflow(Gtk.Overflow.HIDDEN)
        self.cover_wrapper.add_css_class("rounded")
        self.cover_wrapper.set_valign(Gtk.Align.START)
        self.cover_wrapper.set_size_request(200, 200)
        self.cover_wrapper.append(self.cover_img)
        self.header_info_box.append(self.cover_wrapper)

        cover_gesture = Gtk.GestureClick()
        cover_gesture.set_button(3)
        cover_gesture.connect("pressed", self.on_cover_right_click)
        self.cover_wrapper.add_controller(cover_gesture)

        # Long Press for touch
        lp = Gtk.GestureLongPress()
        lp.connect("pressed", lambda g, x, y: self.on_cover_right_click(g, 1, x, y))
        self.cover_wrapper.add_controller(lp)

        self.details_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.details_col.set_valign(Gtk.Align.CENTER)
        self.details_col.set_hexpand(True)

        self.playlist_name_label = Gtk.Label(label="Playlist Title")
        self.playlist_name_label.add_css_class("title-1")
        self.playlist_name_label.set_wrap(True)
        self.playlist_name_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.playlist_name_label.set_justify(Gtk.Justification.LEFT)
        self.playlist_name_label.set_halign(Gtk.Align.START)
        self.playlist_name_label.set_vexpand(False)
        self.playlist_name_label.set_hexpand(True)
        self.playlist_name_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.playlist_name_label.set_lines(3)
        self.details_col.append(self.playlist_name_label)

        self.description_label = Gtk.Label(label="")
        self.description_label.add_css_class("body")
        self.description_label.set_wrap(True)
        self.description_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.description_label.set_justify(Gtk.Justification.LEFT)
        self.description_label.set_halign(Gtk.Align.START)
        self.description_label.set_vexpand(False)
        self.description_label.set_hexpand(True)
        self._description_expanded = False
        self._full_description = ""

        self.read_more_btn = Gtk.Label()
        self.read_more_btn.set_use_markup(True)
        self.read_more_btn.set_markup("<a href='toggle'>Read more</a>")
        self.read_more_btn.add_css_class("caption")
        self.read_more_btn.set_halign(Gtk.Align.START)
        self.read_more_btn.set_visible(False)
        self.read_more_btn.connect("activate-link", self._on_read_more_clicked)

        # Group description + read more tightly without extra spacing
        self.desc_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.desc_box.append(self.description_label)
        self.desc_box.append(self.read_more_btn)
        self.desc_box.set_visible(False)
        self.details_col.append(self.desc_box)

        self.meta_label = Gtk.Label(label="")
        self.meta_label.add_css_class("caption")
        self.meta_label.set_wrap(True)
        self.meta_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.meta_label.set_justify(Gtk.Justification.LEFT)
        self.meta_label.set_halign(Gtk.Align.START)
        self.meta_label.set_hexpand(True)
        self.meta_label.set_use_markup(True)
        self.meta_label.connect("activate-link", self.on_meta_link_activated)
        self.details_col.append(self.meta_label)

        self.stats_label = Gtk.Label(label="")
        self.stats_label.add_css_class("caption")
        self.stats_label.set_wrap(True)
        self.stats_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.stats_label.set_justify(Gtk.Justification.LEFT)
        self.stats_label.set_halign(Gtk.Align.START)
        self.stats_label.set_hexpand(True)
        self.details_col.append(self.stats_label)

        actions_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        actions_box.set_margin_top(12)
        self.actions_box = actions_box

        play_btn = Gtk.Button(label="Play")
        play_btn.add_css_class("suggested-action")
        play_btn.add_css_class("pill")
        play_btn.connect("clicked", self.on_play_clicked)
        actions_box.append(play_btn)

        shuffle_btn = Gtk.Button()
        shuffle_btn.set_icon_name("media-playlist-shuffle-symbolic")
        shuffle_btn.add_css_class("circular")
        shuffle_btn.set_valign(Gtk.Align.CENTER)
        shuffle_btn.set_halign(Gtk.Align.CENTER)
        shuffle_btn.set_size_request(48, 48)
        shuffle_btn.connect("clicked", self.on_shuffle_clicked)
        actions_box.append(shuffle_btn)

        # Simplified Actions (Play/Shuffle only)

        # self.edit_btn and self.delete_btn are no longer in the main actions_box

        self.more_btn = Gtk.MenuButton(icon_name="view-more-symbolic")
        self.more_btn.add_css_class("circular")
        self.more_btn.set_size_request(48, 48)
        self.more_btn.set_tooltip_text("More Options")

        self.more_menu_model = Gio.Menu()
        self.playlist_menu = Gio.Menu()
        self.more_btn.set_menu_model(self.more_menu_model)
        actions_box.append(self.more_btn)

        # Actions Row
        self.action_group = Gio.SimpleActionGroup()
        self.insert_action_group("page", self.action_group)

        action_add = Gio.SimpleAction.new(
            "add_all_to_playlist", GLib.VariantType.new("s")
        )
        action_add.connect("activate", self._on_add_all_to_playlist)
        self.action_group.add_action(action_add)

        action_copy = Gio.SimpleAction.new("copy_link", None)
        action_copy.connect("activate", self.on_copy_link_clicked)
        self.action_group.add_action(action_copy)

        action_edit = Gio.SimpleAction.new("edit", None)
        action_edit.connect("activate", self.on_edit_clicked)
        self.action_group.add_action(action_edit)

        action_delete = Gio.SimpleAction.new("delete", None)
        action_delete.connect("activate", self.on_delete_clicked)

        action_sel_add = Gio.SimpleAction.new(
            "sel_add_to_playlist", GLib.VariantType.new("s")
        )
        action_sel_add.connect("activate", self._on_sel_add_to_playlist)
        self.action_group.add_action(action_sel_add)
        self.action_group.add_action(action_delete)

        action_save = Gio.SimpleAction.new("save_to_library", None)
        action_save.connect("activate", self._on_save_to_library)
        self.action_group.add_action(action_save)

        action_unsave = Gio.SimpleAction.new("remove_from_library", None)
        action_unsave.connect("activate", self._on_remove_from_library)
        self.action_group.add_action(action_unsave)

        action_dl_all = Gio.SimpleAction.new("download_all", None)
        action_dl_all.connect("activate", self._on_download_all)
        self.action_group.add_action(action_dl_all)

        action_start_radio = Gio.SimpleAction.new("start_radio", None)
        action_start_radio.connect("activate", self._on_start_radio)
        self.action_group.add_action(action_start_radio)

        self._is_saved_to_library = False

        # We need to track visibility of edit/delete in the menu
        # Gio.MenuItem doesn't have set_visible, so we might need to refresh the menu

        self.sort_dropdown = Gtk.DropDown.new_from_strings(
            ["Default", "Title (A-Z)", "Artist (A-Z)", "Album (A-Z)", "Duration"]
        )
        self.sort_dropdown.set_valign(Gtk.Align.CENTER)
        self.sort_dropdown.add_css_class("pill")
        self.sort_dropdown.add_css_class("sort-dropdown")
        self.sort_dropdown.connect("notify::selected", self.on_sort_changed)

        self.details_col.append(actions_box)
        self.header_info_box.append(self.details_col)
        self.header_container.append(self.header_info_box)

        self._sort_descending = False
        self.sort_dir_btn = Gtk.Button(icon_name="view-sort-ascending-symbolic")
        self.sort_dir_btn.add_css_class("flat")
        self.sort_dir_btn.add_css_class("circular")
        self.sort_dir_btn.set_tooltip_text("Toggle sort direction")
        self.sort_dir_btn.connect("clicked", self._on_sort_dir_clicked)

        self.sort_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.sort_row.set_margin_top(12)
        self.sort_row.add_css_class("playlist-sort-row")
        self.sort_row.append(self.sort_dropdown)
        self.sort_row.append(self.sort_dir_btn)
        # Select toggle button
        self.select_btn = Gtk.ToggleButton(icon_name="selection-mode-symbolic")
        self.select_btn.add_css_class("flat")
        self.select_btn.add_css_class("circular")
        self.select_btn.set_tooltip_text("Select multiple songs")
        self.select_btn.connect("toggled", self._on_select_toggled)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)

        self.sort_row.append(spacer)
        self.sort_row.append(self.select_btn)
        self.sort_row.set_visible(False)
        self.header_container.append(self.sort_row)

        # Selection action bar (hidden by default)
        self.selection_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.selection_bar.set_margin_top(8)
        self.selection_bar.set_margin_bottom(4)
        self.selection_bar.set_visible(False)

        self.selection_count_label = Gtk.Label(label="0 selected")
        self.selection_count_label.add_css_class("caption")
        self.selection_bar.append(self.selection_count_label)

        sel_all_btn = Gtk.Button(label="All")
        sel_all_btn.add_css_class("flat")
        sel_all_btn.add_css_class("caption")
        sel_all_btn.set_tooltip_text("Select all")
        sel_all_btn.connect("clicked", lambda b: self._select_all())
        self.selection_bar.append(sel_all_btn)

        sel_none_btn = Gtk.Button(label="None")
        sel_none_btn.add_css_class("flat")
        sel_none_btn.add_css_class("caption")
        sel_none_btn.set_tooltip_text("Deselect all")
        sel_none_btn.connect("clicked", lambda b: self._deselect_all())
        self.selection_bar.append(sel_none_btn)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        self.selection_bar.append(spacer)

        sel_play_btn = Gtk.Button(icon_name="media-playback-start-symbolic")
        sel_play_btn.add_css_class("flat")
        sel_play_btn.set_tooltip_text("Play selected")
        sel_play_btn.connect("clicked", self._on_sel_play)
        self.selection_bar.append(sel_play_btn)

        sel_add_btn = Gtk.MenuButton(icon_name="list-add-symbolic")
        sel_add_btn.add_css_class("flat")
        sel_add_btn.set_tooltip_text("Add selected to playlist")
        self.sel_add_menu = Gio.Menu()
        sel_add_btn.set_menu_model(self.sel_add_menu)
        self.selection_bar.append(sel_add_btn)

        self.sel_remove_btn = Gtk.Button(icon_name="user-trash-symbolic")
        self.sel_remove_btn.add_css_class("flat")
        self.sel_remove_btn.add_css_class("destructive-action")
        self.sel_remove_btn.set_tooltip_text("Remove selected from playlist")
        self.sel_remove_btn.connect("clicked", self._on_sel_remove)
        self.sel_remove_btn.set_visible(False)
        self.selection_bar.append(self.sel_remove_btn)

        sel_cancel_btn = Gtk.Button(label="Cancel")
        sel_cancel_btn.add_css_class("flat")
        sel_cancel_btn.connect("clicked", lambda b: self.select_btn.set_active(False))
        self.selection_bar.append(sel_cancel_btn)

        self.header_container.append(self.selection_bar)

        self.empty_label = Gtk.Label(label="This playlist has no songs")
        self.empty_label.add_css_class("dim-label")
        self.empty_label.set_margin_top(24)
        self.empty_label.set_halign(Gtk.Align.CENTER)
        self.empty_label.set_visible(False)
        self.header_container.append(self.empty_label)

        self.content_spinner = Adw.Spinner()
        self.content_spinner.set_size_request(32, 32)
        self.content_spinner.set_halign(Gtk.Align.CENTER)
        self.content_spinner.set_margin_top(24)
        self.content_spinner.set_visible(False)
        self.header_container.append(self.content_spinner)

        # ── 2. Models ─────────────────────────────────────────────────────────
        self.header_store = Gio.ListStore(item_type=HeaderItem)
        self.header_store.append(HeaderItem())

        self.track_store = Gio.ListStore(item_type=TrackItem)
        self.track_filter = Gtk.CustomFilter.new(self._track_filter_func, None)
        self.filter_model = Gtk.FilterListModel.new(self.track_store, self.track_filter)

        self.master_store = Gio.ListStore(item_type=Gio.ListModel)
        self.master_store.append(self.header_store)
        self.master_store.append(self.filter_model)

        self.flatten_model = Gtk.FlattenListModel.new(self.master_store)
        self._multi_select_mode = False
        self.selection_model = Gtk.NoSelection.new(self.flatten_model)

        # ── 3. List & ScrolledWindow ──────────────────────────────────────────
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._setup_list_item)
        factory.connect("bind", self._bind_list_item)
        factory.connect("unbind", self._unbind_list_item)
        factory.connect("teardown", self._teardown_list_item)

        self.songs_list = Gtk.ListView.new(self.selection_model, factory)
        self.songs_list.add_css_class("playlist-view")

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        self.vadjust = scrolled.get_vadjustment()
        self.vadjust.connect("value-changed", self._on_scroll)

        clamp = (
            Adw.ClampScrollable() if hasattr(Adw, "ClampScrollable") else Adw.Clamp()
        )
        clamp.set_maximum_size(1024)
        clamp.set_tightening_threshold(600)

        # Apply padding directly to the ListView so it remains Gtk.Scrollable
        self.songs_list.set_margin_start(12)
        self.songs_list.set_margin_end(12)
        self.songs_list.set_margin_bottom(0)

        # The ListView MUST be the direct child of the ClampScrollable
        clamp.set_child(self.songs_list)
        scrolled.set_child(clamp)

        # ── 4. Main & Page Stack ──────────────────────────────────────────────
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.main_box.append(scrolled)

        self.load_more_spinner = Adw.Spinner()
        self.load_more_spinner.set_size_request(24, 24)
        self.load_more_spinner.set_halign(Gtk.Align.CENTER)
        self.load_more_spinner.set_margin_top(12)
        self.load_more_spinner.set_margin_bottom(12)
        self.load_more_spinner.set_visible(False)
        self.main_box.append(self.load_more_spinner)

        self.stack = Adw.ViewStack()
        loading_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        loading_box.set_valign(Gtk.Align.CENTER)
        loading_box.set_halign(Gtk.Align.CENTER)
        self.spinner = Adw.Spinner()
        self.spinner.set_size_request(32, 32)
        loading_box.append(self.spinner)

        self.stack.add_named(loading_box, "loading")
        self.stack.add_named(self.main_box, "content")
        self.set_child(self.stack)

        self.current_tracks = []
        self.current_limit = 50
        self.is_loading_more = False
        self.current_filter_text = ""

    # ── Factory callbacks ─────────────────────────────────────────────────────

    def _setup_list_item(self, factory, list_item):
        bin_widget = Adw.Bin()
        bin_widget.add_css_class("list-item-bin")
        list_item.set_child(bin_widget)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.set_hexpand(True)
        row.add_css_class("song-row")

        # Checkbox for multi-select (hidden by default)
        check = Gtk.CheckButton()
        check.set_valign(Gtk.Align.CENTER)
        check.set_visible(False)
        row.append(check)
        row._lv_check = check

        from ui.utils import AsyncPicture

        img = AsyncPicture(crop_to_square=True, target_size=56, player=self.player)
        img.add_css_class("song-img")
        row.append(img)
        row._lv_img = img
        row._lv_player_handler = None

        # Track number label (for album view)
        track_num = Gtk.Label()
        track_num.add_css_class("dim-label")
        track_num.add_css_class("caption")
        track_num.set_valign(Gtk.Align.CENTER)
        track_num.set_halign(Gtk.Align.CENTER)
        track_num.set_size_request(40, 40)
        track_num.set_visible(False)
        row.append(track_num)
        row._lv_track_num = track_num

        # Main Title / Subtitle Box
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        vbox.set_valign(Gtk.Align.CENTER)
        vbox.set_hexpand(True)

        title_label = Gtk.Label()
        title_label.set_halign(Gtk.Align.START)
        title_label.set_ellipsize(Pango.EllipsizeMode.END)
        title_label.set_lines(1)
        row._title_label = title_label

        subtitle_label = Gtk.Label()
        subtitle_label.set_halign(Gtk.Align.START)
        subtitle_label.set_ellipsize(Pango.EllipsizeMode.END)
        subtitle_label.set_lines(1)
        subtitle_label.add_css_class("dim-label")
        subtitle_label.add_css_class("caption")
        row._subtitle_label = subtitle_label

        title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        title_box.append(title_label)
        row._title_label = title_label

        explicit_badge = Gtk.Label(label="E")
        explicit_badge.add_css_class("explicit-badge")
        explicit_badge.set_valign(Gtk.Align.CENTER)
        explicit_badge.set_visible(False)
        title_box.append(explicit_badge)
        row._lv_explicit_badge = explicit_badge

        dl_icon = Gtk.Image.new_from_icon_name("folder-download-symbolic")
        dl_icon.set_pixel_size(14)
        dl_icon.add_css_class("dim-label")
        dl_icon.set_valign(Gtk.Align.CENTER)
        dl_icon.set_visible(False)
        title_box.append(dl_icon)
        row._lv_dl_icon = dl_icon

        vbox.append(title_box)
        vbox.append(subtitle_label)
        row.append(vbox)

        dur_lbl = Gtk.Label()
        dur_lbl.add_css_class("caption")
        dur_lbl.set_valign(Gtk.Align.CENTER)
        dur_lbl.set_margin_end(6)
        row.append(dur_lbl)
        row._lv_dur_lbl = dur_lbl

        like_btn = LikeButton(self.client, None)
        like_btn.set_valign(Gtk.Align.CENTER)
        row.append(like_btn)
        row._lv_like_btn = like_btn

        gesture = Gtk.GestureClick()
        gesture.set_button(3)
        gesture.connect("released", self._on_row_right_click_gesture)
        row.add_controller(gesture)

        # Long Press for touch
        lp = Gtk.GestureLongPress()
        lp.connect(
            "pressed", lambda g, x, y: self._on_row_right_click_gesture(g, 1, x, y)
        )
        row.add_controller(lp)

        # Left Click Gesture instead of list_view activate
        left_click = Gtk.GestureClick()
        left_click.set_button(1)
        left_click.connect("pressed", self._on_row_left_pressed, row)
        left_click.connect("released", self._on_row_left_click, list_item)
        row.add_controller(left_click)

        row._lv_video_data = None
        row._lv_full_track = None

        bin_widget._lv_track_ui = row

    def _bind_list_item(self, factory, list_item):
        bin_widget = list_item.get_child()
        item = list_item.get_item()
        if not item:
            return

        if type(item).__name__ == "HeaderItem":
            list_item.set_selectable(False)
            list_item.set_activatable(False)
            bin_widget.set_child(self.header_container)
            return

        bin_widget.set_child(bin_widget._lv_track_ui)
        list_item.set_selectable(True)
        list_item.set_activatable(True)

        row = bin_widget._lv_track_ui
        t = item.data

        # Multi-select checkbox
        video_id = t.get("videoId")
        is_selected = video_id in getattr(self, "_selected_video_ids", set())
        if self._multi_select_mode:
            row._lv_check.set_visible(True)
            if hasattr(row, "_lv_check_handler") and row._lv_check_handler:
                row._lv_check.disconnect(row._lv_check_handler)
            row._lv_check.set_active(is_selected)
            row._lv_check_handler = row._lv_check.connect(
                "toggled",
                lambda cb, vid=video_id, r=row: self._toggle_track_selection(vid, r),
            )
        else:
            row._lv_check.set_visible(False)
            if hasattr(row, "_lv_check_handler") and row._lv_check_handler:
                row._lv_check.disconnect(row._lv_check_handler)
                row._lv_check_handler = None
        # Apply selection highlight
        self._apply_row_selection(row, is_selected and self._multi_select_mode)

        title = t.get("title", "Unknown")
        artist_list = t.get("artists", [])
        artist = ", ".join(a.get("name", "") for a in artist_list)

        row._title_label.set_label(title)
        row._subtitle_label.set_label(artist)

        thumbnails = t.get("thumbnails", [])
        thumb_url = thumbnails[-1]["url"] if thumbnails else None

        # Album view: show track number instead of thumbnail
        is_album = getattr(self, "_is_album_view", False)
        if is_album:
            position = list_item.get_position()
            # The list contains a header at index 0, so the first track is at index 1.
            # Using 'position' as the track number correctly gives us 1-based indexing.
            track_num = position
            row._lv_track_num.set_label(str(track_num))
            row._lv_track_num.set_visible(True)
            row._lv_img.set_visible(False)
        else:
            row._lv_track_num.set_visible(False)
            row._lv_img.set_visible(True)
            root = self.get_root()
            row._lv_img.set_compact(
                getattr(root, "_is_compact", False) if root else False
            )
            if thumb_url:
                row._lv_img.video_id = t.get("videoId")
                if row._lv_img.url != thumb_url:
                    row._lv_img.load_url(thumb_url)
            else:
                row._lv_img.video_id = None
                row._lv_img.set_from_icon_name("media-optical-symbolic")
                row._lv_img.url = None

        dur_sec = t.get("duration_seconds")
        dur_text = (
            f"{dur_sec // 60}:{dur_sec % 60:02d}" if dur_sec else t.get("duration", "")
        )
        row._lv_dur_lbl.set_label(dur_text or "")
        row._lv_dur_lbl.set_visible(bool(dur_text))

        is_explicit = t.get("isExplicit") or t.get("explicit", False)
        row._lv_explicit_badge.set_visible(bool(is_explicit))

        row._lv_video_id = t.get("videoId", "")
        if t.get("videoId"):
            vid = t["videoId"]
            row._lv_like_btn.set_data(vid, t.get("likeStatus", "INDIFFERENT"))
            row._lv_like_btn.set_visible(True)
            # Downloaded / queued indicator
            dm = self.player.download_manager
            if dm.is_downloaded(vid):
                row._lv_dl_icon.set_from_icon_name("folder-download-symbolic")
                row._lv_dl_icon.remove_css_class("queued-icon")
                row._lv_dl_icon.set_visible(True)
            elif dm.is_queued(vid):
                row._lv_dl_icon.set_from_icon_name("content-loading-symbolic")
                row._lv_dl_icon.add_css_class("queued-icon")
                row._lv_dl_icon.set_visible(True)
            else:
                row._lv_dl_icon.remove_css_class("queued-icon")
                row._lv_dl_icon.set_visible(False)
        else:
            row._lv_like_btn.set_visible(False)
            row._lv_dl_icon.remove_css_class("queued-icon")
            row._lv_dl_icon.set_visible(False)

        has_id = bool(t.get("videoId"))
        # Grey out songs unavailable offline
        from ui.utils import is_online

        if (
            has_id
            and not is_online()
            and not self.player.download_manager.is_downloaded(t["videoId"])
        ):
            row.set_sensitive(False)
            row.set_opacity(0.4)
        else:
            list_item.set_activatable(has_id)
            list_item.set_selectable(has_id)
            row.set_sensitive(has_id)
            row.set_opacity(1.0)

        row._lv_video_data = {
            "id": t.get("videoId"),
            "title": title,
            "artist": artist,
            "thumb": thumb_url,
            "setVideoId": t.get("setVideoId") or t.get("playlistId"),
        }
        row._lv_full_track = t

        # Playing indicator: check if this track is currently playing
        video_id = t.get("videoId")
        is_playing = bool(video_id and video_id == self.player.current_video_id)
        if is_playing:
            row.add_css_class("playing")
        else:
            row.remove_css_class("playing")

        # Connect to player metadata changes
        def on_meta_changed(player, *args, _row=row, _vid=video_id):
            if bool(_vid and _vid == player.current_video_id):
                _row.add_css_class("playing")
            else:
                _row.remove_css_class("playing")

        if getattr(row, "_lv_player_handler", None):
            self.player.disconnect(row._lv_player_handler)
        row._lv_player_handler = self.player.connect(
            "metadata-changed", on_meta_changed
        )

    def _unbind_list_item(self, factory, list_item):
        bin_widget = list_item.get_child()
        item = list_item.get_item()
        if not item:
            return

        if type(item).__name__ == "HeaderItem":
            bin_widget.set_child(None)
            return

        row = bin_widget._lv_track_ui
        # Disconnect player signal
        if row._lv_player_handler is not None:
            try:
                self.player.disconnect(row._lv_player_handler)
            except Exception:
                pass
            row._lv_player_handler = None
        row.remove_css_class("playing")

        row._title_label.set_label("")
        row._subtitle_label.set_label("")
        row._lv_img.set_paintable(None)
        row._lv_img.url = None
        row._lv_dur_lbl.set_label("")
        row._lv_dur_lbl.set_visible(False)
        row.remove_css_class("playing")
        row._lv_explicit_badge.set_visible(False)
        row._lv_like_btn.set_visible(False)
        row._lv_check.set_visible(False)
        row.remove_css_class("selected")
        if hasattr(row, "_lv_check_handler") and row._lv_check_handler:
            row._lv_check.disconnect(row._lv_check_handler)
            row._lv_check_handler = None
        row._lv_video_data = None
        row._lv_full_track = None

    def _teardown_list_item(self, factory, list_item):
        list_item.set_child(None)

    def _on_row_left_pressed(self, gesture, n_press, x, y, row):
        row._start_x = x
        row._start_y = y

    def _on_row_left_click(self, gesture, n_press, x, y, list_item):
        bin_widget = list_item.get_child()
        row = getattr(bin_widget, "_lv_track_ui", bin_widget)
        if hasattr(row, "_start_x"):
            dx = abs(x - row._start_x)
            dy = abs(y - row._start_y)
            if dx > 10 or dy > 10:
                return

        # In multi-select mode, toggle selection instead of playing
        if self._multi_select_mode:
            track = getattr(row, "_lv_full_track", None)
            vid = track.get("videoId") if track else None
            if vid:
                self._toggle_track_selection(vid, row)
                if hasattr(row, "_lv_check"):
                    row._lv_check.set_active(vid in self._selected_video_ids)
            return

        track = getattr(row, "_lv_full_track", None)
        if not track or not track.get("videoId"):
            return

        # Don't play unavailable offline songs
        video_id = track["videoId"]
        from ui.utils import is_online

        if not is_online() and not self.player.download_manager.is_downloaded(video_id):
            return

        # Prefer original_tracks when available (covers filtered results beyond lazy-loaded range)
        tracks_to_queue = (
            self.original_tracks
            if hasattr(self, "original_tracks") and self.original_tracks
            else self._best_queue()
        )
        # When offline, filter to only downloaded songs
        if not is_online():
            dm = self.player.download_manager
            tracks_to_queue = [
                t for t in tracks_to_queue if dm.is_downloaded(t.get("videoId"))
            ]

        start_index = -1
        for i, t in enumerate(tracks_to_queue):
            if t.get("videoId") == video_id:
                start_index = i
                break

        print(
            f"\033[95m[DEBUG-CLICK] vid={video_id} found_at={start_index} "
            f"queue_len={len(tracks_to_queue)} "
            f"using={'original_tracks' if hasattr(self, 'original_tracks') and self.original_tracks else '_best_queue'}\033[0m"
        )

        if start_index < 0:
            # Click target wasn't in tracks_to_queue (shouldn't happen, but
            # don't silently fall back to playing the first song).
            print("[DEBUG-CLICK] WARNING: click target not in queue, aborting")
            return

        self.player.set_queue(
            tracks_to_queue,
            start_index,
            source_id=self.playlist_id,
            is_infinite=self._is_inf(),
        )
        if getattr(self, "_is_background_fetching", False):
            self._pending_queue_append = True

    # ── Filter ────────────────────────────────────────────────────────────────

    def _track_filter_func(self, item, _user_data):
        if not self.current_filter_text:
            return True
        t = item.data
        title = t.get("title", "").lower()
        artist = ", ".join(a.get("name", "") for a in t.get("artists", [])).lower()
        return self.current_filter_text in title or self.current_filter_text in artist

    def filter_content(self, text):
        self.current_filter_text = text.lower().strip()

        # Search all data in original_tracks and rebuild the track store with matches
        if hasattr(self, "original_tracks") and self.original_tracks:
            # Disable the GTK filter during manual rebuilds to avoid double-filtering
            self.filter_model.set_filter(None)

            if self.current_filter_text:
                matches = []
                for t in self.original_tracks:
                    title = t.get("title", "").lower()
                    artist = ", ".join(
                        a.get("name", "") for a in t.get("artists", [])
                    ).lower()
                    album = ""
                    if isinstance(t.get("album"), dict):
                        album = t["album"].get("name", "").lower()
                    elif t.get("album"):
                        album = str(t["album"]).lower()
                    if (
                        self.current_filter_text in title
                        or self.current_filter_text in artist
                        or self.current_filter_text in album
                    ):
                        matches.append(t)
                matches = self._sort_tracks(matches)
                self.track_store.splice(
                    0, self.track_store.get_n_items(), [TrackItem(t) for t in matches]
                )
            else:
                # Filter cleared: restore from current_tracks (lazy-loaded subset)
                items = [TrackItem(t) for t in self.current_tracks]
                self.track_store.splice(0, self.track_store.get_n_items(), items)

            # Always re-enable the GTK filter so any tracks added later
            # (lazy-load, background fetch) are also filtered
            self.filter_model.set_filter(self.track_filter)
            if self._multi_select_mode:
                self._update_selection_count()
            return

        # Fallback: use the GTK filter
        self.track_filter.changed(Gtk.FilterChange.DIFFERENT)
        if self._multi_select_mode:
            self._update_selection_count()

    # ── Store helpers ─────────────────────────────────────────────────────────

    def _add_track_row(self, t):
        self.track_store.append(TrackItem(t))

    def _clear_track_store(self):
        self.track_store.remove_all()

    # ── Scroll / lazy load ────────────────────────────────────────────────────

    def _on_scroll(self, vadjust):
        val = vadjust.get_value()

        # Absolute position check for Window Title
        if val <= 50:
            self.emit("header-title-changed", "")
        else:
            self.emit("header-title-changed", self.playlist_title_text)

        max_val = vadjust.get_upper() - vadjust.get_page_size()
        if max_val > 0 and val >= max_val - 200:
            if (
                not self.is_loading_more
                and self.playlist_id
                and not getattr(self, "is_fully_loaded", False)
            ):
                self.load_more()

    def load_more(self):
        if getattr(self, "is_fully_fetched", False) and hasattr(
            self, "original_tracks"
        ):
            if len(self.current_tracks) < len(self.original_tracks):
                self.is_loading_more = True
                self.load_more_spinner.set_visible(True)

                start_index = len(self.current_tracks)
                end_index = min(start_index + 50, len(self.original_tracks))
                new_tracks = self.original_tracks[start_index:end_index]
                self.current_tracks.extend(new_tracks)

                if self.sort_dropdown.get_selected() != 0:
                    self.reorder_playlist(self.sort_dropdown.get_selected())
                else:
                    for t in new_tracks:
                        self._add_track_row(t)

                self.load_more_spinner.set_visible(False)
                self.is_loading_more = False
                return

        if getattr(self, "is_fully_loaded", False):
            return

        self.is_loading_more = True
        self.load_more_spinner.set_visible(True)
        self.current_limit = len(self.current_tracks) + 50
        print(f"Loading more... Limit now {self.current_limit}")

        thread = threading.Thread(
            target=self._fetch_playlist_details, args=(self.playlist_id, True)
        )
        thread.daemon = True
        thread.start()

    def _on_map(self, widget):
        if hasattr(self, "vadjust"):
            if self.vadjust.get_value() > 50:
                self.emit("header-title-changed", self.playlist_title_text)
            else:
                self.emit("header-title-changed", "")
        self._refresh_more_menu()
        # Connect download signals for live indicator updates
        dm = self.player.download_manager
        self._dl_queued_id = dm.connect("item-queued", self._on_dl_indicator_update)
        self._dl_done_id = dm.connect("item-done", self._on_dl_item_done)

    def _refresh_more_menu(self, is_owned=False):
        from ui.utils import is_online

        self.more_menu_model.remove_all()

        # 1. Add All to Playlist Submenu
        self.playlist_menu.remove_all()
        playlists = self.client.get_editable_playlists()
        for p in playlists:
            title = p.get("title", "Untitled")
            pid = p.get("playlistId")
            if pid:
                self.playlist_menu.append(title, f"page.add_all_to_playlist('{pid}')")

        self.more_menu_model.append_submenu("Add all to Playlist", self.playlist_menu)

        # 2. Start Radio (online only)
        if is_online() and (getattr(self, "_audio_playlist_id", None) or self.playlist_id):
            self.more_menu_model.append("Start Radio", "page.start_radio")

        # 3. Copy Link (Always shown)
        self.more_menu_model.append("Copy Link", "page.copy_link")

        # 3. Save/Unsave from Library (not for owned playlists - they're always in library)
        if not is_owned and self.client.is_authenticated():
            if self._is_saved_to_library:
                self.more_menu_model.append(
                    "Remove from Library", "page.remove_from_library"
                )
            else:
                self.more_menu_model.append("Add to Library", "page.save_to_library")

        # 4. Download All
        self.more_menu_model.append("Download All", "page.download_all")

        # 5. Edit/Delete (Only if owned/editable)
        if is_owned:
            self.more_menu_model.append("Edit Playlist", "page.edit")
            self.more_menu_model.append("Delete Playlist", "page.delete")

    def _on_start_radio(self, action, param):
        pid = getattr(self, "_audio_playlist_id", None) or self.playlist_id
        if not pid:
            return
        radio_id = pid if pid.startswith("RDAMPL") else f"RDAMPL{pid}"
        self.player.start_radio(playlist_id=radio_id)
        self._show_toast("Starting radio...")

    def _on_download_all(self, action, param):
        all_tracks = (
            self.original_tracks
            if hasattr(self, "original_tracks") and self.original_tracks
            else self.current_tracks
        )
        if not all_tracks:
            return
        root = self.get_root()
        if root and hasattr(root, "download_tracks"):
            thumb = self.cover_img.url if hasattr(self, "cover_img") else None
            root.download_tracks(
                all_tracks, self.playlist_title_text, self.playlist_id, thumb
            )
            self._show_toast(f"Downloading {len(all_tracks)} songs...")

    def _on_add_all_to_playlist(self, action, param):
        playlist_id = param.get_string()
        video_ids = [t.get("videoId") for t in self.current_tracks if t.get("videoId")]

        if not video_ids:
            return

        def thread_func():
            success = self.client.add_playlist_items(playlist_id, video_ids)
            if success:
                msg = f"Added {len(video_ids)} tracks to playlist"
                print(msg)
                GLib.idle_add(self._show_toast, msg)
            else:
                GLib.idle_add(self._show_toast, "Failed to add tracks")

        threading.Thread(target=thread_func, daemon=True).start()

    def _on_save_to_library(self, action, param):
        pid = self._audio_playlist_id or self.playlist_id
        if not pid:
            return

        def thread_func():
            success = self.client.rate_playlist(pid, "LIKE")
            if success:
                self._is_saved_to_library = True
                # Invalidate library cache so it refetches
                self.client._library_album_ids.clear()
                self.client._library_playlist_ids.clear()
                GLib.idle_add(self._show_toast, "Saved to library")
                GLib.idle_add(self._refresh_more_menu, self.is_owned)
                GLib.idle_add(self._refresh_library_page)
            else:
                GLib.idle_add(self._show_toast, "Failed to save")

        threading.Thread(target=thread_func, daemon=True).start()

    def _on_remove_from_library(self, action, param):
        pid = self._audio_playlist_id or self.playlist_id
        if not pid:
            return

        def thread_func():
            success = self.client.rate_playlist(pid, "INDIFFERENT")
            if success:
                self._is_saved_to_library = False
                self.client._library_album_ids.clear()
                self.client._library_playlist_ids.clear()
                GLib.idle_add(self._show_toast, "Removed from library")
                GLib.idle_add(self._refresh_more_menu, self.is_owned)
                GLib.idle_add(self._refresh_library_page)
            else:
                GLib.idle_add(self._show_toast, "Failed to remove")

        threading.Thread(target=thread_func, daemon=True).start()

    def _refresh_library_page(self):
        root = self.get_root()
        if root and hasattr(root, "library_page"):
            root.library_page.load_library()

    def _show_toast(self, message):
        root = self.get_root()
        if hasattr(root, "add_toast"):
            root.add_toast(message)

    def _remove_track_by_entity_id(self, entity_id):
        """Remove a track from local data by entityId and refresh the view."""
        self.original_tracks = [
            t for t in self.original_tracks if t.get("entityId") != entity_id
        ]
        self.current_tracks = [
            t for t in self.current_tracks if t.get("entityId") != entity_id
        ]
        self._clear_track_store()
        for t in self.current_tracks:
            self._add_track_row(t)
        self._update_duration_from_all_tracks()

    def _update_dl_icon_for(self, video_id, downloaded=False, queued=False):
        """Update the download indicator on visible rows matching video_id."""
        child = self.songs_list.get_first_child()
        while child:
            # ListView hierarchy: GtkListItemWidget → Adw.Bin → Gtk.Box (row)
            bin_widget = child.get_first_child()
            row = (
                bin_widget.get_child()
                if bin_widget and hasattr(bin_widget, "get_child")
                else None
            )
            if row and hasattr(row, "_lv_dl_icon") and hasattr(row, "_lv_video_id"):
                if row._lv_video_id == video_id:
                    if downloaded:
                        row._lv_dl_icon.set_from_icon_name("folder-download-symbolic")
                        row._lv_dl_icon.remove_css_class("queued-icon")
                        row._lv_dl_icon.set_visible(True)
                    elif queued:
                        row._lv_dl_icon.set_from_icon_name("content-loading-symbolic")
                        row._lv_dl_icon.add_css_class("queued-icon")
                        row._lv_dl_icon.set_visible(True)
            child = child.get_next_sibling()

    def _on_dl_indicator_update(self, dm, video_id):
        """A track was queued for download."""
        self._update_dl_icon_for(video_id, queued=True)

    def _on_dl_item_done(self, dm, video_id, success, msg):
        """A track finished downloading."""
        if success:
            self._update_dl_icon_for(video_id, downloaded=True)

    def _on_unmap(self, widget):
        self.emit("header-title-changed", "")
        # Disconnect download signals
        dm = self.player.download_manager
        if hasattr(self, "_dl_queued_id") and self._dl_queued_id:
            dm.disconnect(self._dl_queued_id)
            self._dl_queued_id = None
        if hasattr(self, "_dl_done_id") and self._dl_done_id:
            dm.disconnect(self._dl_done_id)
            self._dl_done_id = None

    # ── Load playlist ─────────────────────────────────────────────────────────

    def load_playlist(self, playlist_id, initial_data=None):
        if self.playlist_id != playlist_id:
            self.playlist_id = playlist_id
            self._audio_playlist_id = None
            self.playlist_title_text = ""
            self.current_limit = 50
            self.emit("header-title-changed", "")
            self.current_tracks = []
            self._is_previewing_cover = False
            self._clear_track_store()

        # ── Offline: use local DB only, no API calls ──
        from ui.utils import is_online

        if not is_online():
            self._load_playlist_offline(playlist_id, initial_data)
            return

        if initial_data:
            self.playlist_title_text = initial_data.get("title", "")
            self.playlist_name_label.set_label(self.playlist_title_text)
            self.description_label.set_label("")

            author = initial_data.get("author")
            if author and author != "Unknown":
                self.meta_label.set_label(f"{author} • Loading tracks...")
            else:
                self.meta_label.set_label("Loading tracks...")

            thumb = initial_data.get("thumb")
            if thumb:
                if self.cover_img.url != thumb:
                    self.cover_img.set_from_icon_name("media-playlist-audio-symbolic")
                    self.cover_img.load_url(thumb)
            else:
                self.cover_img.set_from_icon_name("media-playlist-audio-symbolic")
                self.cover_img.url = None

            self.stack.set_visible_child_name("content")
            self.content_spinner.set_visible(True)
        else:
            cached_tracks = self.client.get_cached_playlist_tracks(self.playlist_id)
            if cached_tracks is not None:
                print(
                    f"Loading playlist {playlist_id} from cache ({len(cached_tracks)} tracks)"
                )
                self.is_fully_loaded = True
                self.original_tracks = list(cached_tracks)
                self.current_tracks = list(cached_tracks)
                thread = threading.Thread(
                    target=self._fetch_playlist_details, args=(playlist_id,)
                )
                thread.daemon = True
                thread.start()
                return

            if self.stack.get_visible_child_name() != "content":
                self.stack.set_visible_child_name("loading")
                self.playlist_name_label.set_label("Loading...")
                self.description_label.set_label("")
                self.meta_label.set_label("")
                self.cover_img.set_from_icon_name("media-playlist-audio-symbolic")
                self.cover_img.url = None
                self.content_spinner.set_visible(True)
            else:
                self.content_spinner.set_visible(False)

        thread = threading.Thread(
            target=self._fetch_playlist_details, args=(playlist_id,)
        )
        thread.daemon = True
        thread.start()

    def _load_playlist_offline(self, playlist_id, initial_data):
        """Load a playlist entirely from local cache when offline."""
        from player.downloads import get_download_db

        db = get_download_db()

        # Try the offline playlist cache
        cached = db.get_cached_playlist(playlist_id)
        if cached and cached.get("tracks"):
            title = (
                cached.get("title")
                or (initial_data.get("title") if initial_data else "")
                or "Playlist"
            )
            tracks = cached["tracks"]
            self.playlist_title_text = title
            self.original_tracks = tracks
            self.current_tracks = tracks
            self.is_fully_loaded = True
            self.is_fully_fetched = True

            total_seconds = sum(t.get("duration_seconds", 0) for t in tracks)
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            dur = f"{hours} hr {minutes} min" if hours > 0 else f"{minutes} min"

            self.update_ui(
                title,
                "",
                "Offline",
                f"{len(tracks)} songs • {dur}",
                tracks[0].get("thumbnails", []) if tracks else [],
                tracks,
                False,
                len(tracks),
                False,
            )
        elif initial_data:
            # No cached tracks but we have metadata - show empty state
            title = initial_data.get("title", "Playlist")
            self.playlist_title_text = title
            self.playlist_name_label.set_label(title)
            self.meta_label.set_label("Offline - no cached data")
            self.stack.set_visible_child_name("content")
            self.empty_label.set_label(
                "This playlist hasn't been cached for offline use"
            )
            self.empty_label.set_visible(True)
            self.is_fully_loaded = True
        else:
            self.stack.set_visible_child_name("content")
            self.empty_label.set_label("Offline - no cached data")
            self.empty_label.set_visible(True)
            self.is_fully_loaded = True

    # ── Fetch ─────────────────────────────────────────────────────────────────

    def _fetch_playlist_details(self, playlist_id, is_incremental=False):
        try:
            # Virtual playlists (UPLOADS, DOWNLOADS) are fully loaded already
            if playlist_id.startswith("UPLOAD") or playlist_id == "DOWNLOADS":
                self.is_fully_loaded = True
                self.is_fully_fetched = True
                return

            # Skip API fetch when offline - use cached data only
            from ui.utils import is_online

            if not is_online():
                self.is_fully_loaded = True
                self.is_fully_fetched = True
                return

            if playlist_id.startswith("OLAK"):
                try:
                    new_id = self.client.get_album_browse_id(playlist_id)
                    if new_id and new_id.startswith("MPRE"):
                        print(f"Converted {playlist_id} to {new_id}")
                        playlist_id = new_id
                except Exception:
                    pass
                # If conversion didn't happen or failed, try raw parsing
                if playlist_id.startswith("OLAK"):
                    try:
                        tracks = self.client._raw_parse_playlist("VL" + playlist_id)
                        if tracks:
                            title = (
                                tracks[0].get("_playlist_title")
                                or self.playlist_title_text
                                or "Chart Playlist"
                            )
                            self.original_tracks = tracks
                            self.current_tracks = tracks
                            self.is_fully_fetched = True
                            self.is_fully_loaded = True
                            total_seconds = sum(
                                t.get("duration_seconds", 0) for t in tracks
                            )
                            hours = total_seconds // 3600
                            minutes = (total_seconds % 3600) // 60
                            dur = (
                                f"{hours} hr {minutes} min"
                                if hours > 0
                                else f"{minutes} min"
                            )
                            GObject.idle_add(
                                self.update_ui,
                                title,
                                "",
                                "Playlist",
                                f"{len(tracks)} songs • {dur}",
                                [],
                                tracks,
                                False,
                                len(tracks),
                                False,
                            )
                            return
                    except Exception as e:
                        print(f"Raw OLAK parse failed: {e}")

            count_str = None
            album_type = None

            if playlist_id.startswith("FEmusic_library_privately_owned"):
                # Uploaded album - use special API
                try:
                    data = self.client.get_library_upload_album(playlist_id)
                    if not data:
                        raise Exception("Failed to fetch uploaded album")
                    title = data.get("title", "Unknown Album")
                    description = ""
                    tracks = data.get("tracks", [])
                    thumbnails = data.get("thumbnails", [])
                    track_count = len(tracks)
                    year = data.get("year", "")
                    album_type = "Upload"

                    artists = data.get("artists", [])
                    # Fallback: check "artist" (singular string) field
                    if not artists and data.get("artist"):
                        artist_name = data["artist"]
                        if isinstance(artist_name, str):
                            artists = [{"name": artist_name}]

                    if isinstance(artists, list) and artists:
                        author = ", ".join(
                            GLib.markup_escape_text(a.get("name", ""))
                            for a in artists
                            if isinstance(a, dict)
                        )
                    else:
                        author = ""

                    song_text = "song" if track_count == 1 else "songs"
                    count_str = f"{track_count} {song_text}"

                    # Cross-reference with all uploaded songs to fill missing artist data
                    try:
                        all_songs = self.client.get_library_upload_songs(limit=None)
                        songs_by_id = {}
                        if all_songs:
                            for s in all_songs:
                                vid = s.get("videoId")
                                if vid:
                                    songs_by_id[vid] = s
                    except Exception:
                        songs_by_id = {}

                    # Fill in missing data on tracks
                    # Priority: all-songs data > album data > nothing
                    for track in tracks:
                        vid = track.get("videoId")
                        ref = songs_by_id.get(vid, {}) if vid else {}

                        # Thumbnails
                        if not track.get("thumbnails"):
                            track["thumbnails"] = ref.get("thumbnails") or thumbnails

                        # Artists: prefer all-songs data (most accurate), then album
                        if ref.get("artists"):
                            track["artists"] = ref["artists"]
                        elif not track.get("artists") and artists:
                            track["artists"] = artists

                        # Build artist string from artists list
                        track_artists = track.get("artists", [])
                        if track_artists:
                            track["artist"] = ", ".join(
                                a.get("name", "")
                                for a in track_artists
                                if isinstance(a, dict)
                            )
                        elif ref.get("artist"):
                            track["artist"] = ref["artist"]

                    is_owned = False
                except Exception as e:
                    print(f"Error fetching uploaded album: {e}")
                    return

            elif playlist_id == "LM":
                data = self.client.get_liked_songs(limit=self.current_limit)
                title = "Your Likes"
                description = "Your liked songs from YouTube Music."
                tracks = data.get("tracks", []) if isinstance(data, dict) else data
                track_count = (
                    data.get("trackCount", len(tracks))
                    if isinstance(data, dict)
                    else len(tracks)
                )
                song_text = "song" if track_count == 1 else "songs"
                count_str = f"{track_count} {song_text}"
                year = None
                author = "You"
                thumbnails = []
                if tracks:
                    first = tracks[0]
                    if first.get("thumbnails"):
                        thumbnails = first.get("thumbnails")
                        new_thumbs = []
                        for t in thumbnails:
                            if "url" in t:
                                nt = t.copy()
                                # Systematic upgrade handled by utils.py
                                new_thumbs.append(nt)
                        if new_thumbs:
                            thumbnails = new_thumbs
                is_owned = False

            elif playlist_id.startswith("MPRE"):
                try:
                    data = self.client.get_album(playlist_id)
                    self._audio_playlist_id = data.get("audioPlaylistId")
                    title = data.get("title", "Unknown Album")
                    description = data.get("description", "")
                    tracks = data.get("tracks", [])
                    thumbnails = data.get("thumbnails", [])
                    track_count = data.get("trackCount", len(tracks))
                    year = data.get("year", "")

                    if track_count == 1:
                        album_type = "Single"
                    elif 2 <= track_count <= 6:
                        album_type = "EP"
                    else:
                        album_type = "Album"

                    meta_parts = [album_type]
                    if year:
                        meta_parts.append(str(year))
                    song_text = "song" if track_count == 1 else "songs"
                    count_str = f"{track_count} {song_text}"
                    meta_parts.append(count_str)
                    count = " • ".join(meta_parts)

                    artist_data = data.get("artists", [])
                    if isinstance(artist_data, list):
                        parts = []
                        for a in artist_data:
                            name = GLib.markup_escape_text(a.get("name", "Unknown"))
                            aid = a.get("id")
                            parts.append(
                                f"<a href='artist:{aid}'>{name}</a>" if aid else name
                            )
                        author = ", ".join(parts)
                    else:
                        author = GLib.markup_escape_text(str(artist_data))

                    if thumbnails:
                        for t in thumbnails:
                            if "url" in t:
                                pass  # Systematic upgrade handled by utils.py
                        for track in tracks:
                            if not track.get("thumbnails"):
                                track["thumbnails"] = thumbnails
                    is_owned = self.client.is_own_playlist(
                        data, playlist_id=playlist_id
                    )
                except Exception as e:
                    print(f"Error fetching album details: {e}")
                    return
            else:
                try:
                    print(
                        f"Fetching playlist: {playlist_id} (Limit: {self.current_limit})"
                    )

                    # retry for brand new playlists (eventual consistency)
                    data = None
                    for attempt in range(3):
                        try:
                            data = self.client.get_playlist(
                                playlist_id, limit=self.current_limit
                            )
                            if data and data.get("title"):
                                break
                        except Exception as e:
                            print(f"Fetch attempt {attempt + 1} failed: {e}")

                        if attempt < 2:
                            import time

                            time.sleep(1.5)

                    if not data:
                        raise Exception("Failed to fetch playlist after retries")

                    title = (
                        data.get("title")
                        or self.playlist_title_text
                        or "Unknown Playlist"
                    )
                    description = data.get("description", "")
                    tracks = data.get("tracks", [])
                    thumbnails = data.get("thumbnails", [])

                    track_count = data.get("trackCount")
                    if track_count is None:
                        song_text = "Infinite"
                        count_str = "Infinite"
                    else:
                        song_text = "song" if track_count == 1 else "songs"
                        count_str = f"{track_count} {song_text}"

                    meta_parts = []
                    privacy = data.get("privacy")
                    is_owned = self.client.is_own_playlist(
                        data, playlist_id=playlist_id
                    )
                    self.playlist_privacy_text = privacy or "PUBLIC"
                    if privacy:
                        meta_parts.append(privacy.capitalize())
                    year = data.get("year")
                    if year:
                        meta_parts.append(str(year))
                    meta_parts.append(count_str)
                    duration = data.get("duration")
                    if duration:
                        meta_parts.append(duration)
                    count = " • ".join(meta_parts)

                    author_data = data.get("author")
                    if isinstance(author_data, list):
                        parts = []
                        for a in author_data:
                            name = GLib.markup_escape_text(a.get("name", ""))
                            aid = a.get("id")
                            parts.append(
                                f"<a href='artist:{aid}'>{name}</a>" if aid else name
                            )
                        author = ", ".join(parts)
                    elif isinstance(author_data, dict):
                        name = GLib.markup_escape_text(
                            author_data.get("name", "Unknown")
                        )
                        aid = author_data.get("id")
                        author = f"<a href='artist:{aid}'>{name}</a>" if aid else name
                    else:
                        author = (
                            GLib.markup_escape_text(str(author_data))
                            if author_data
                            else "Unknown"
                        )

                    if "Unknown" in author and not author.startswith("<a"):
                        collab = data.get("collaborators")
                        if collab and isinstance(collab, dict):
                            text = collab.get("text", "")
                            if text:
                                clean = text[3:] if text.startswith("by ") else text
                                author = GLib.markup_escape_text(clean)
                except Exception as e:
                    print(f"Error processing playlists: {e}")
                    data = {}
                    title = "Error Loading Playlist"
                    description = str(e)
                    tracks = []
                    thumbnails = []
                    author = "Error"
                    track_count = 0
                    song_text = "songs"
                    count_str = "0 songs"
                    year = None
                    album_type = None
                    is_owned = False

            total_seconds = 0
            if "duration_seconds" in data:
                total_seconds = data.get("duration_seconds")
            elif tracks and "track_count" in locals() and track_count is not None:
                total_seconds = sum(t.get("duration_seconds", 0) for t in tracks)

            if total_seconds and total_seconds > 0:
                hours = total_seconds // 3600
                minutes = (total_seconds % 3600) // 60
                seconds = total_seconds % 60
                duration_str = (
                    f"{hours} hr {minutes} min"
                    if hours > 0
                    else f"{minutes} min {seconds} sec"
                )
            else:
                duration_str = data.get("duration", "")

            meta1_parts = []
            if playlist_id.startswith("MPRE") or playlist_id.startswith("OLAK"):
                meta1_parts.append(album_type)
            else:
                privacy = (
                    self.playlist_privacy_text
                    if hasattr(self, "playlist_privacy_text")
                    else data.get("privacy")
                )
                meta1_parts.append(privacy.capitalize() if privacy else "Playlist")
            if year:
                meta1_parts.append(str(year))
            if author:
                meta1_parts.append(author)
            meta1 = " • ".join(meta1_parts)

            meta2_parts = []
            if count_str:
                meta2_parts.append(count_str)
            else:
                if "track_count" in locals() and track_count is None:
                    meta2_parts.append("Infinite")
                else:
                    meta2_parts.append(
                        f"{locals().get('track_count', 0)} {locals().get('song_text', 'songs')}"
                    )
            if duration_str:
                meta2_parts.append(duration_str)
            meta2 = " • ".join(meta2_parts)

            GObject.idle_add(
                self.update_ui,
                title,
                description,
                meta1,
                meta2,
                thumbnails,
                tracks,
                is_incremental,
                track_count,
                is_owned,
            )

            if (
                not is_incremental
                and track_count is not None
                and len(tracks) < track_count
            ):
                if not self.playlist_id.startswith(
                    "MPRE"
                ) and not self.playlist_id.startswith("OLAK"):
                    self._start_background_full_fetch()

        except Exception as e:
            print(f"Critical error fetching playlist: {e}")
            self.is_loading_more = False
            GObject.idle_add(self.load_more_spinner.set_visible, False)

    # ── Update UI ─────────────────────────────────────────────────────────────

    def update_ui(
        self,
        title,
        description,
        meta1,
        meta2,
        thumbnails,
        tracks,
        append=False,
        total_tracks=None,
        is_owned=False,
    ):
        self.stack.set_visible_child_name("content")
        self.content_spinner.set_visible(False)

        self.playlist_title_text = title
        self.playlist_description_text = description
        self.playlist_name_label.set_label(title)

        if description and description.strip():
            self._full_description = description
            self._description_expanded = False
            self.read_more_btn.set_markup("<a href='toggle'>Read more</a>")
            if len(description) > 200:
                truncated = description[:200].rsplit(" ", 1)[0] + "..."
                self.description_label.set_label(truncated)
                self.read_more_btn.set_visible(True)
            else:
                self.description_label.set_label(description)
                self.read_more_btn.set_visible(False)
            self.desc_box.set_visible(True)
        else:
            self.desc_box.set_visible(False)

        self.meta_label.set_markup(meta1)
        self.stats_label.set_label(meta2)

        is_album = self.playlist_id and (
            self.playlist_id.startswith("MPRE")
            or self.playlist_id.startswith("OLAK")
            or self.playlist_id.startswith("FEmusic_library_privately_owned")
        )
        self._is_album_view = is_album
        has_tracks = bool(tracks)

        self.empty_label.set_visible(not has_tracks)
        self.sort_row.set_visible(has_tracks and not is_album)

        self.is_owned = is_owned
        self.is_editable = self.client.is_authenticated() and not is_album and is_owned
        is_editable = self.is_editable

        # Check library status for save/unsave toggle
        check_id = getattr(self, "_audio_playlist_id", None) or self.playlist_id
        self._is_saved_to_library = is_owned or self.client.is_in_library(check_id)

        # Dynamically rebuild the menu to show/hide Edit/Delete
        self._refresh_more_menu(is_owned=is_editable)

        if thumbnails and not append:
            url = thumbnails[-1]["url"]
            if self.cover_img.url != url:
                self._is_previewing_cover = False
                # Check for locally saved playlist cover (from download)
                from player.downloads import get_music_dir, _sanitize_filename
                from ui.utils import is_online

                cover_path = os.path.join(
                    get_music_dir(), "playlists", f"{_sanitize_filename(title)}.jpg"
                )
                if os.path.exists(cover_path) and not is_online():
                    self.cover_img.load_url(f"file://{cover_path}")
                else:
                    self.cover_img.load_url(url)
        elif not thumbnails and not self.cover_img.url:
            if not self._is_previewing_cover:
                self.cover_img.set_from_icon_name("media-playlist-audio-symbolic")
                self.cover_img.url = None

        if append:
            start_index = len(self.current_tracks)
            new_tracks = tracks[start_index:]

            if not new_tracks:
                print("No new tracks found. Playlist fully loaded.")
                self.is_fully_loaded = True
                self.load_more_spinner.set_visible(False)
                self.is_loading_more = False
                return

            print(f"Appending {len(new_tracks)} new tracks (Total: {len(tracks)})")
            self.current_tracks.extend(new_tracks)
            if hasattr(self, "original_tracks"):
                self.original_tracks.extend(new_tracks)

            if self.sort_dropdown.get_selected() != 0:
                self.reorder_playlist(self.sort_dropdown.get_selected())
            else:
                for t in new_tracks:
                    self._add_track_row(t)

            self.load_more_spinner.set_visible(False)
            self.is_loading_more = False

            if len(tracks) < self.current_limit:
                print(
                    f"Playlist fully loaded ({len(tracks)} < limit {self.current_limit})"
                )
                self.is_fully_loaded = True
            elif total_tracks is not None and len(tracks) >= total_tracks:
                print(f"Playlist fully loaded ({len(tracks)} >= total {total_tracks})")
                self.is_fully_loaded = True
        else:
            if not getattr(self, "is_fully_loaded", False):
                self.is_fully_loaded = False
            if total_tracks is not None and len(tracks) >= total_tracks:
                self.is_fully_loaded = True
                self.is_fully_fetched = True
                self.client.set_cached_playlist_tracks(self.playlist_id, tracks)

            self.current_tracks = list(tracks)
            if not hasattr(self, "original_tracks") or not self.original_tracks:
                self.original_tracks = list(tracks)
            self.sort_dropdown.set_selected(0)

            self._clear_track_store()
            for t in tracks:
                self._add_track_row(t)

        if len(self.current_tracks) > 0 and len(self.current_tracks) == len(
            getattr(self, "original_tracks", [])
        ):
            self.is_fully_fetched = True

    # ── Background fetch ──────────────────────────────────────────────────────

    def _start_background_full_fetch(self):
        if getattr(self, "is_fully_fetched", False):
            return
        print(f"Starting background fetch for full playlist: {self.playlist_id}")

        def fetch_job():
            try:
                data = self.client.get_playlist(self.playlist_id, limit=5000)
                tracks = data.get("tracks", [])
                if tracks:
                    print(f"Background fetch complete. Fetched {len(tracks)} tracks.")
                    self.client.set_cached_playlist_tracks(self.playlist_id, tracks)
                    GObject.idle_add(self._on_background_fetch_complete, tracks)
            except Exception as e:
                print(f"Error in background fetch: {e}")

        self._is_background_fetching = True
        self._pending_queue_append = False
        thread = threading.Thread(target=fetch_job)
        thread.daemon = True
        thread.start()

    def _on_background_fetch_complete(self, tracks=None):
        if tracks is not None:
            self.original_tracks = tracks
        self.is_fully_fetched = True
        self._is_background_fetching = False

        # Re-sort with full data now available
        sort_type = self.sort_dropdown.get_selected()
        if sort_type != 0 or getattr(self, "_sort_descending", False):
            self.reorder_playlist(sort_type)

        if getattr(self, "_pending_queue_append", False):
            print("Background fetch complete, extending player queue.")
            start_index = len(self.current_tracks)
            new_tracks = self.original_tracks[start_index:]
            if new_tracks:
                self.player.extend_queue(new_tracks)
            self._pending_queue_append = False

        # Recalculate duration from all tracks now that we have the full data
        self._update_duration_from_all_tracks()

    def _update_duration_from_all_tracks(self):
        tracks = (
            self.original_tracks
            if hasattr(self, "original_tracks") and self.original_tracks
            else self.current_tracks
        )
        total_seconds = sum(t.get("duration_seconds", 0) for t in tracks)
        track_count = len(tracks)
        song_text = "song" if track_count == 1 else "songs"

        meta2_parts = [f"{track_count} {song_text}"]
        if total_seconds > 0:
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            seconds = total_seconds % 60
            duration_str = (
                f"{hours} hr {minutes} min"
                if hours > 0
                else f"{minutes} min {seconds} sec"
            )
            meta2_parts.append(duration_str)
        self.stats_label.set_label(" • ".join(meta2_parts))

    # ── Song activation ───────────────────────────────────────────────────────

    def on_copy_link_clicked(self, action, param):
        if not self.playlist_id:
            return
        is_album = self.playlist_id.startswith("MPRE") or self.playlist_id.startswith(
            "OLAK"
        )
        link = get_yt_music_link(
            self.playlist_id,
            is_album=is_album,
            audio_playlist_id=getattr(self, "_audio_playlist_id", None),
        )
        if link:
            clipboard = Gdk.Display.get_default().get_clipboard()
            clipboard.set(link)
            self._show_toast("Link copied to clipboard")
            print(f"Copied link: {link}")

    def on_song_activated(self, listview, position):
        item = self.flatten_model.get_item(position)
        if item is None or type(item).__name__ == "HeaderItem":
            return

        t = item.data
        if not t.get("videoId"):
            return

        tracks_to_queue = self._best_queue()
        start_index = 0
        for i, track in enumerate(tracks_to_queue):
            if track.get("videoId") == t.get("videoId"):
                start_index = i
                break

        print(
            f"\033[94m[DEBUG-PLAYLIST] on_song_activated. playlist_id={self.playlist_id}\033[0m"
        )
        self.player.set_queue(
            tracks_to_queue,
            start_index,
            source_id=self.playlist_id,
            is_infinite=self._is_inf(),
        )
        if getattr(self, "_is_background_fetching", False):
            self._pending_queue_append = True

    # ── Multi-select ──────────────────────────────────────────────────────────

    def _on_select_toggled(self, btn):
        self._multi_select_mode = btn.get_active()
        if self._multi_select_mode:
            self._selected_video_ids = set()
            self.selection_bar.set_visible(True)
            self.sel_remove_btn.set_visible(self.is_owned)
            self._refresh_sel_add_menu()
            self._update_selection_count()
        else:
            self._selected_video_ids = set()
            self.selection_bar.set_visible(False)
        self._refresh_all_row_visuals()

    def _toggle_track_selection(self, video_id, row=None):
        if not video_id:
            return
        if video_id in self._selected_video_ids:
            self._selected_video_ids.discard(video_id)
        else:
            self._selected_video_ids.add(video_id)
        self._update_selection_count()
        if row:
            is_sel = video_id in self._selected_video_ids
            self._apply_row_selection(row, is_sel)

    def _apply_row_selection(self, row, selected):
        if selected:
            row.add_css_class("selected")
        else:
            row.remove_css_class("selected")
        if hasattr(row, "_lv_check"):
            if hasattr(row, "_lv_check_handler") and row._lv_check_handler:
                row._lv_check.disconnect(row._lv_check_handler)
                row._lv_check_handler = None
            row._lv_check.set_active(selected)
            vid = (
                getattr(row, "_lv_full_track", {}).get("videoId")
                if hasattr(row, "_lv_full_track") and row._lv_full_track
                else None
            )
            if vid:
                row._lv_check_handler = row._lv_check.connect(
                    "toggled",
                    lambda cb, v=vid, r=row: self._toggle_track_selection(v, r),
                )

    def _refresh_all_row_visuals(self):
        """Walk all visible ListView rows and update checkbox/highlight state."""
        child = self.songs_list.get_first_child()
        while child:
            # child is the list row, its first child is the Adw.Bin
            bin_w = child.get_first_child() if child else None
            row = getattr(bin_w, "_lv_track_ui", None) if bin_w else None
            if row and hasattr(row, "_lv_full_track") and row._lv_full_track:
                vid = row._lv_full_track.get("videoId")
                is_sel = vid in self._selected_video_ids if vid else False
                if self._multi_select_mode:
                    row._lv_check.set_visible(True)
                    self._apply_row_selection(row, is_sel)
                else:
                    row._lv_check.set_visible(False)
                    row.remove_css_class("selected")
            child = child.get_next_sibling()

    def _get_visible_tracks(self):
        """Returns the tracks that should be considered 'visible' for selection.
        With a search filter: the filtered matches from the track_store.
        Without a filter: all available data (original_tracks if fully fetched,
        otherwise current_tracks)."""
        if self.current_filter_text:
            tracks = []
            for i in range(self.track_store.get_n_items()):
                item = self.track_store.get_item(i)
                if item and hasattr(item, "data"):
                    tracks.append(item.data)
            return tracks
        # No filter: use the most complete dataset available
        if hasattr(self, "original_tracks") and self.original_tracks:
            return self.original_tracks
        return self.current_tracks

    def _select_all(self):
        """Select all tracks matching the current filter/search/sort."""
        for t in self._get_visible_tracks():
            vid = t.get("videoId")
            if vid:
                self._selected_video_ids.add(vid)
        self._update_selection_count()
        self._refresh_all_row_visuals()

    def _deselect_all(self):
        self._selected_video_ids.clear()
        self._update_selection_count()
        self._refresh_all_row_visuals()

    def _update_selection_count(self):
        count = len(getattr(self, "_selected_video_ids", set()))
        total = len(self._get_visible_tracks())
        self.selection_count_label.set_label(f"{count} of {total} selected")

    def _get_selected_tracks(self):
        """Returns selected tracks in current sort order."""
        all_tracks = (
            self.original_tracks
            if hasattr(self, "original_tracks") and self.original_tracks
            else self.current_tracks
        )
        selected = [
            t for t in all_tracks if t.get("videoId") in self._selected_video_ids
        ]
        return self._sort_tracks(selected)

    def _refresh_sel_add_menu(self):
        self.sel_add_menu.remove_all()
        playlists = self.client.get_editable_playlists()
        for p in playlists:
            title = p.get("title", "Untitled")
            pid = p.get("playlistId")
            if pid:
                self.sel_add_menu.append(title, f"page.sel_add_to_playlist('{pid}')")

    def _on_sel_play(self, btn):
        tracks = self._get_selected_tracks()
        if tracks:
            self.player.set_queue(tracks, 0)

    def _on_sel_add_to_playlist(self, action, param):
        target_pid = param.get_string()
        tracks = self._get_selected_tracks()
        video_ids = [t.get("videoId") for t in tracks if t.get("videoId")]
        if not video_ids:
            return

        def thread_func():
            success = self.client.add_playlist_items(target_pid, video_ids)
            msg = (
                f"Added {len(video_ids)} tracks to playlist"
                if success
                else "Failed to add tracks"
            )
            GLib.idle_add(self._show_toast, msg)

        threading.Thread(target=thread_func, daemon=True).start()

    def _on_sel_remove(self, btn):
        tracks = self._get_selected_tracks()
        to_remove = [
            {"videoId": t.get("videoId"), "setVideoId": t.get("setVideoId")}
            for t in tracks
            if t.get("videoId") and t.get("setVideoId")
        ]
        if not to_remove:
            return

        def thread_func():
            success = self.client.remove_playlist_items(self.playlist_id, to_remove)
            if success:
                GLib.idle_add(self._show_toast, f"Removed {len(to_remove)} tracks")
                GLib.idle_add(self.load_playlist, self.playlist_id)
            else:
                GLib.idle_add(self._show_toast, "Failed to remove tracks")

        threading.Thread(target=thread_func, daemon=True).start()

    def _copy_selection_debug(self):
        """Copy selected track data to clipboard for debugging."""
        import json

        tracks = self._get_selected_tracks()
        debug_data = {
            "selected_count": len(tracks),
            "selected_video_ids": sorted(self._selected_video_ids),
            "current_tracks_count": len(self.current_tracks),
            "original_tracks_count": len(getattr(self, "original_tracks", [])),
            "tracks": [
                {
                    "videoId": t.get("videoId"),
                    "title": t.get("title"),
                    "artists": [a.get("name") for a in t.get("artists", [])],
                    "setVideoId": t.get("setVideoId"),
                    "duration_seconds": t.get("duration_seconds"),
                }
                for t in tracks
            ],
        }
        text = json.dumps(debug_data, indent=2, ensure_ascii=False)
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set(text)
        self._show_toast(f"Copied debug data for {len(tracks)} tracks")

    # ── Sort ──────────────────────────────────────────────────────────────────

    def _on_sort_dir_clicked(self, btn):
        self._sort_descending = not self._sort_descending
        btn.set_icon_name(
            "view-sort-descending-symbolic"
            if self._sort_descending
            else "view-sort-ascending-symbolic"
        )
        self.reorder_playlist(self.sort_dropdown.get_selected())

    def on_sort_changed(self, dropdown, pspec):
        self.reorder_playlist(dropdown.get_selected())

    def _sort_tracks(self, tracks):
        """Sort a list of track dicts according to current sort dropdown and direction."""
        sort_type = self.sort_dropdown.get_selected()
        reverse = getattr(self, "_sort_descending", False)
        result = list(tracks)
        if sort_type == 0:
            if reverse:
                result.reverse()
        elif sort_type == 1:
            result.sort(key=lambda x: x.get("title", "").lower(), reverse=reverse)
        elif sort_type == 2:
            result.sort(
                key=lambda x: (
                    x.get("artists", [{}])[0].get("name", "").lower()
                    if x.get("artists")
                    else "",
                    x.get("title", "").lower(),
                ),
                reverse=reverse,
            )
        elif sort_type == 3:
            result.sort(
                key=lambda x: (
                    x.get("album", {}).get("name", "").lower()
                    if isinstance(x.get("album"), dict)
                    else str(x.get("album") or "").lower(),
                    x.get("title", "").lower(),
                ),
                reverse=reverse,
            )
        elif sort_type == 4:
            result.sort(key=lambda x: x.get("duration_seconds", 0), reverse=reverse)
        return result

    def reorder_playlist(self, sort_type):
        # Sort all data (original_tracks), not just the lazy-loaded subset
        source = (
            list(self.original_tracks)
            if hasattr(self, "original_tracks") and self.original_tracks
            else list(self.current_tracks)
        )
        if not source:
            return

        reverse = getattr(self, "_sort_descending", False)

        if sort_type == 0:
            if not reverse:
                # Default order: restore original
                source = (
                    list(self.original_tracks)
                    if hasattr(self, "original_tracks") and self.original_tracks
                    else source
                )
            else:
                source = (
                    list(reversed(self.original_tracks))
                    if hasattr(self, "original_tracks") and self.original_tracks
                    else source
                )
        elif sort_type == 1:
            source.sort(key=lambda x: x.get("title", "").lower(), reverse=reverse)
        elif sort_type == 2:
            source.sort(
                key=lambda x: (
                    x.get("artists", [{}])[0].get("name", "").lower()
                    if x.get("artists")
                    else "",
                    x.get("title", "").lower(),
                ),
                reverse=reverse,
            )
        elif sort_type == 3:
            source.sort(
                key=lambda x: (
                    x.get("album", {}).get("name", "").lower()
                    if isinstance(x.get("album"), dict)
                    else str(x.get("album") or "").lower(),
                    x.get("title", "").lower(),
                ),
                reverse=reverse,
            )
        elif sort_type == 4:  # Duration
            source.sort(key=lambda x: x.get("duration_seconds", 0), reverse=reverse)

        self.current_tracks = source
        # Re-apply active filter if any, otherwise show all
        if self.current_filter_text:
            self.filter_content(self.current_filter_text)
        else:
            self._clear_track_store()
            for t in self.current_tracks:
                self._add_track_row(t)

    # ── Right-click ───────────────────────────────────────────────────────────

    def _on_row_right_click_gesture(self, gesture, n_press, x, y):
        row = gesture.get_widget()
        if not hasattr(row, "_lv_video_data") or row._lv_video_data is None:
            return

        data = row._lv_video_data
        full_track_data = row._lv_full_track
        vid = data.get("id") or data.get("videoId")

        group = Gio.SimpleActionGroup()
        row.insert_action_group("ctx", group)

        menu_model = Gio.Menu()

        # ── Section: Navigation ──
        nav_section = Gio.Menu()
        if full_track_data and full_track_data.get("artists"):
            artist = full_track_data["artists"][0]
            if artist.get("id"):
                nav_section.append("Go to Artist", "ctx.goto_artist")
                a = Gio.SimpleAction.new("goto_artist", None)
                a.connect(
                    "activate",
                    lambda act, p: (
                        self.get_root().open_artist(artist["id"], artist.get("name"))
                        if hasattr(self.get_root(), "open_artist")
                        else None
                    ),
                )
                group.add_action(a)

        if nav_section.get_n_items() > 0:
            menu_model.append_section(None, nav_section)

        # ── Section: Actions ──
        has_selection = self._multi_select_mode and self._selected_video_ids
        action_section = Gio.Menu()

        # Start Radio (single song only, not for multi-select, online only)
        from ui.utils import is_online

        _online = is_online()
        if vid and not has_selection and _online:
            action_section.append("Start Radio", "ctx.start_radio")
            a_radio = Gio.SimpleAction.new("start_radio", None)
            a_radio.connect(
                "activate",
                lambda act, p, v=vid: (
                    self.player.start_radio(video_id=v),
                    self._show_toast("Starting radio..."),
                ),
            )
            group.add_action(a_radio)

        if has_selection or vid:
            playlists = self.client.get_editable_playlists()
            if playlists:
                label = (
                    f"Add {len(self._selected_video_ids)} to Playlist"
                    if has_selection
                    else "Add to Playlist"
                )
                playlist_menu = Gio.Menu()
                for p in sorted(playlists, key=lambda x: x.get("title", "").lower()):
                    pid = p.get("playlistId")
                    if pid:
                        playlist_menu.append(
                            p.get("title", "?"), f"ctx.add_to_playlist('{pid}')"
                        )
                action_section.append_submenu(label, playlist_menu)

                a_add = Gio.SimpleAction.new(
                    "add_to_playlist", GLib.VariantType.new("s")
                )

                def _do_add(act, param):
                    target_pid = param.get_string()
                    if has_selection:
                        vids = [
                            t.get("videoId")
                            for t in self._get_selected_tracks()
                            if t.get("videoId")
                        ]
                    else:
                        vids = [vid] if vid else []
                    if vids:
                        n = len(vids)
                        threading.Thread(
                            target=lambda: (
                                self.client.add_playlist_items(target_pid, vids),
                                GLib.idle_add(
                                    self._show_toast,
                                    f"Added {n} track{'s' if n > 1 else ''} to playlist",
                                ),
                            ),
                            daemon=True,
                        ).start()

                a_add.connect("activate", _do_add)
                group.add_action(a_add)

        if self.is_owned:
            if has_selection:
                action_section.append(
                    f"Remove {len(self._selected_video_ids)} from Playlist",
                    "ctx.remove",
                )
                a_rm = Gio.SimpleAction.new("remove", None)

                def _do_remove_sel(act, p):
                    tracks = self._get_selected_tracks()
                    to_remove = [
                        {"videoId": t.get("videoId"), "setVideoId": t.get("setVideoId")}
                        for t in tracks
                        if t.get("videoId") and t.get("setVideoId")
                    ]
                    if to_remove:
                        n = len(to_remove)
                        threading.Thread(
                            target=lambda: (
                                self.client.remove_playlist_items(
                                    self.playlist_id, to_remove
                                ),
                                GLib.idle_add(self._show_toast, f"Removed {n} tracks"),
                                GLib.idle_add(self.load_playlist, self.playlist_id),
                            ),
                            daemon=True,
                        ).start()

                a_rm.connect("activate", _do_remove_sel)
                group.add_action(a_rm)
            elif data.get("setVideoId") and vid:
                action_section.append("Remove from Playlist", "ctx.remove")
                a_rm = Gio.SimpleAction.new("remove", None)
                a_rm.connect(
                    "activate",
                    lambda act, p, v=vid, sv=data["setVideoId"]: threading.Thread(
                        target=lambda: (
                            self.client.remove_playlist_items(
                                self.playlist_id, [{"videoId": v, "setVideoId": sv}]
                            ),
                            GLib.idle_add(self.load_playlist, self.playlist_id),
                        ),
                        daemon=True,
                    ).start(),
                )
                group.add_action(a_rm)

        # Delete uploaded song (for UPLOAD pseudo-playlists)
        is_upload_playlist = self.playlist_id and self.playlist_id.startswith("UPLOAD")
        if is_upload_playlist and full_track_data and full_track_data.get("entityId"):
            entity_id = full_track_data["entityId"]
            track_title = full_track_data.get("title", "this song")
            action_section.append("Delete Upload", "ctx.delete_upload")
            a_del = Gio.SimpleAction.new("delete_upload", None)

            def _do_delete_upload(act, p, eid=entity_id, t=track_title):
                dialog = Adw.MessageDialog(
                    transient_for=self.get_root(),
                    heading="Delete Upload?",
                    body=f'Are you sure you want to delete "{t}"?\nThis cannot be undone.',
                )
                dialog.add_response("cancel", "Cancel")
                dialog.add_response("delete", "Delete")
                dialog.set_response_appearance(
                    "delete", Adw.ResponseAppearance.DESTRUCTIVE
                )
                dialog.set_default_response("cancel")
                dialog.set_close_response("cancel")

                def on_resp(dg, resp):
                    if resp == "delete":

                        def _thread():
                            self.client.delete_upload_entity(eid)
                            GLib.idle_add(self._show_toast, f"Deleted {t}")
                            # Remove from local data
                            GLib.idle_add(self._remove_track_by_entity_id, eid)

                        threading.Thread(target=_thread, daemon=True).start()
                    dg.destroy()

                dialog.connect("response", on_resp)
                dialog.present()

            a_del.connect("activate", _do_delete_upload)
            group.add_action(a_del)

        # Download
        if vid and full_track_data:
            root = self.get_root()
            is_dl = (
                root
                and hasattr(root, "player")
                and root.player.download_manager.is_downloaded(vid)
            )
            if not is_dl:
                if has_selection:
                    action_section.append(
                        f"Download {len(self._selected_video_ids)} Songs",
                        "ctx.download",
                    )
                    a_dl = Gio.SimpleAction.new("download", None)

                    def _do_download_sel(act, p):
                        tracks = self._get_selected_tracks()
                        r = self.get_root()
                        if r and hasattr(r, "download_tracks"):
                            r.download_tracks(
                                tracks, self.playlist_title_text, self.playlist_id
                            )

                    a_dl.connect("activate", _do_download_sel)
                else:
                    action_section.append("Download", "ctx.download")
                    a_dl = Gio.SimpleAction.new("download", None)

                    def _do_download(act, p, t=full_track_data):
                        r = self.get_root()
                        if r and hasattr(r, "download_track"):
                            r.download_track(
                                t, self.playlist_title_text, self.playlist_id
                            )

                    a_dl.connect("activate", _do_download)
                group.add_action(a_dl)

        if action_section.get_n_items() > 0:
            menu_model.append_section(None, action_section)

        # ── Section: Selection (only in multi-select mode) ──
        if self._multi_select_mode:
            sel_section = Gio.Menu()
            is_selected = vid in self._selected_video_ids if vid else False
            if is_selected:
                sel_section.append("Deselect This", "ctx.toggle_sel")
            else:
                sel_section.append("Select This", "ctx.toggle_sel")
            sel_section.append("Select All", "ctx.select_all")
            sel_section.append("Deselect All", "ctx.deselect_all")

            a_toggle = Gio.SimpleAction.new("toggle_sel", None)
            a_toggle.connect(
                "activate",
                lambda act, p, v=vid, r=row: self._toggle_track_selection(v, r),
            )
            group.add_action(a_toggle)

            a_sel_all = Gio.SimpleAction.new("select_all", None)
            a_sel_all.connect("activate", lambda act, p: self._select_all())
            group.add_action(a_sel_all)

            a_desel = Gio.SimpleAction.new("deselect_all", None)
            a_desel.connect("activate", lambda act, p: self._deselect_all())
            group.add_action(a_desel)

            menu_model.append_section(None, sel_section)

        # ── Section: Clipboard / Debug ──
        clip_section = Gio.Menu()
        if vid and _online:
            clip_section.append("Copy Song Link", "ctx.copy_link")
            a_copy = Gio.SimpleAction.new("copy_link", None)
            a_copy.connect(
                "activate",
                lambda act, p, v=vid: (
                    Gdk.Display.get_default()
                    .get_clipboard()
                    .set(f"https://music.youtube.com/watch?v={v}")
                ),
            )
            group.add_action(a_copy)

        if self._multi_select_mode and self._selected_video_ids:
            clip_section.append("Copy Selection Data (Debug)", "ctx.copy_debug")
            a_debug = Gio.SimpleAction.new("copy_debug", None)
            a_debug.connect("activate", lambda act, p: self._copy_selection_debug())
            group.add_action(a_debug)

        if clip_section.get_n_items() > 0:
            menu_model.append_section(None, clip_section)

        if menu_model.get_n_items() > 0:
            popover = Gtk.PopoverMenu.new_from_model(menu_model)
            popover.set_parent(row)
            popover.set_has_arrow(False)
            rect = Gdk.Rectangle()
            rect.x = int(x)
            rect.y = int(y)
            rect.width = 1
            rect.height = 1
            popover.set_pointing_to(rect)
            popover.popup()

    # ── Meta link ─────────────────────────────────────────────────────────────

    def _on_read_more_clicked(self, label, uri):
        # Defer to avoid modifying the label during signal emission (causes segfault)
        GLib.idle_add(self._toggle_description)
        return True

    def _toggle_description(self):
        self._description_expanded = not self._description_expanded
        if self._description_expanded:
            self.description_label.set_label(self._full_description)
            text = "Show less"
        else:
            truncated = self._full_description[:200].rsplit(" ", 1)[0] + "..."
            self.description_label.set_label(truncated)
            text = "Read more"
        # Replace the label to avoid GTK's visited link color
        parent = self.read_more_btn.get_parent()
        parent.remove(self.read_more_btn)
        self.read_more_btn = Gtk.Label()
        self.read_more_btn.set_use_markup(True)
        self.read_more_btn.set_markup(f"<a href='toggle'>{text}</a>")
        self.read_more_btn.add_css_class("caption")
        self.read_more_btn.set_halign(Gtk.Align.START)
        self.read_more_btn.connect("activate-link", self._on_read_more_clicked)
        parent.append(self.read_more_btn)
        return False

    def on_meta_link_activated(self, label, uri):
        if uri.startswith("artist:"):
            aid = uri.split(":", 1)[1]
            root = self.get_root()
            if hasattr(root, "open_artist"):
                root.open_artist(aid, "Artist")
            return True
        return False

    # ── Play / Shuffle ────────────────────────────────────────────────────────

    def _offline_filter_queue(self, tracks):
        """When offline, filter queue to only downloaded songs."""
        from ui.utils import is_online

        if is_online():
            return tracks
        dm = self.player.download_manager
        return [t for t in tracks if dm.is_downloaded(t.get("videoId"))]

    def on_play_clicked(self, btn):
        if not self.current_tracks:
            return
        queue = self._offline_filter_queue(self._best_queue())
        if not queue:
            self._show_toast("No downloaded songs to play")
            return
        self.player.set_queue(
            queue,
            0,
            shuffle=False,
            source_id=self.playlist_id,
            is_infinite=self._is_inf(),
        )
        if getattr(self, "_is_background_fetching", False):
            self._pending_queue_append = True

    def on_shuffle_clicked(self, btn):
        if not self.current_tracks:
            return
        queue = self._offline_filter_queue(self._best_queue())
        if not queue:
            self._show_toast("No downloaded songs to shuffle")
            return
        self.player.set_queue(
            queue,
            -1,
            shuffle=True,
            source_id=self.playlist_id,
            is_infinite=self._is_inf(),
        )
        if getattr(self, "_is_background_fetching", False):
            self._pending_queue_append = True

    def _best_queue(self):
        if (
            getattr(self, "is_fully_fetched", False)
            and hasattr(self, "original_tracks")
            and self.sort_dropdown.get_selected() == 0
        ):
            return self.original_tracks
        return self.current_tracks

    def _is_inf(self):
        return bool(
            self.playlist_id
            and (
                self.playlist_id.startswith("RD") or self.playlist_id.startswith("VLRD")
            )
        )

    # ── Edit Playlist ─────────────────────────────────────────────────────────

    def on_delete_clicked(self, *args):
        dialog = Adw.MessageDialog(
            transient_for=self.get_root(),
            heading="Delete Playlist?",
            body=f'Are you sure you want to delete "{self.playlist_title_text}"?\nThis action cannot be undone.',
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(dg, response_id):
            if response_id == "delete":
                self._delete_playlist_confirmed()
            dg.destroy()

        dialog.connect("response", on_response)
        dialog.present()

    def _delete_playlist_confirmed(self):
        GLib.idle_add(self.content_spinner.set_visible, True)
        GLib.idle_add(self.stack.set_visible_child_name, "loading")

        def thread_func():
            success = self.client.delete_playlist(self.playlist_id)
            if success:
                print(f"Playlist {self.playlist_id} deleted successfully.")
                # Refresh library through MainWindow

                # Navigate back
                nav = self.get_ancestor(Adw.NavigationView)
                if nav:
                    GLib.idle_add(nav.pop)
            else:
                print(f"Failed to delete playlist {self.playlist_id}")
                GLib.idle_add(self.stack.set_visible_child_name, "content")
                GLib.idle_add(self.content_spinner.set_visible, False)

        import threading

        threading.Thread(target=thread_func, daemon=True).start()

    def on_edit_clicked(self, *args):
        self._show_edit_dialog()

    def on_cover_right_click(self, gesture, n_press, x, y):
        url = getattr(self.cover_img, "url", None)
        can_edit = self.is_editable

        if not url and not can_edit:
            return

        menu = Gio.Menu()
        if url:
            menu.append("Copy Cover URL", "cover.copy_url")
        if can_edit:
            menu.append("Edit Playlist", "cover.edit_playlist")

        from ui.utils import copy_to_clipboard

        group = Gio.SimpleActionGroup()

        # Copy URL action
        if url:
            action = Gio.SimpleAction.new("copy_url", None)
            action.set_enabled(True)
            action.connect("activate", lambda *_: copy_to_clipboard(url))
            group.add_action(action)

        # Edit playlist action
        if can_edit:
            action = Gio.SimpleAction.new("edit_playlist", None)
            action.set_enabled(True)
            action.connect("activate", lambda *_: self._show_edit_dialog())
            group.add_action(action)

        self.cover_wrapper.insert_action_group("cover", group)

        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.set_parent(self.cover_wrapper)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = x, y, 1, 1
        popover.set_pointing_to(rect)
        popover.popup()

    def _show_edit_dialog(self):
        dialog = Adw.Dialog()
        dialog.set_title("Edit Playlist")
        dialog.set_content_width(500)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        dialog.set_child(main_box)

        header = Adw.HeaderBar()
        header.add_css_class("flat")
        main_box.append(header)

        save_btn = Gtk.Button(label="Save")
        save_btn.add_css_class("suggested-action")
        header.pack_start(save_btn)

        page = Adw.PreferencesPage()
        main_box.append(page)

        group = Adw.PreferencesGroup(title="Playlist Details")
        group.set_margin_start(12)
        group.set_margin_end(12)
        group.set_margin_top(12)
        group.set_margin_bottom(12)
        page.add(group)

        # Title
        title_row = Adw.EntryRow(title="Title")
        title_row.set_text(self.playlist_title_text or "")
        group.add(title_row)

        # Description
        desc_row = Adw.EntryRow(title="Description")
        desc_row.set_text(self.playlist_description_text or "")
        group.add(desc_row)

        # Privacy
        privacy_row = Adw.ComboRow(title="Visibility")
        privacy_options = ["Public", "Private", "Unlisted"]
        privacy_model = Gtk.StringList.new(privacy_options)
        privacy_row.set_model(privacy_model)

        # Map current privacy to index
        current_privacy = getattr(self, "playlist_privacy_text", "PUBLIC").upper()
        privacy_map = {"PUBLIC": 0, "PRIVATE": 1, "UNLISTED": 2}
        privacy_row.set_selected(privacy_map.get(current_privacy, 0))
        group.add(privacy_row)

        # Cover Art
        cover_row = Adw.ActionRow(title="Playlist Cover")
        cover_row.set_subtitle("No file selected")
        group.add(cover_row)

        self._selected_cover_path = None

        def on_choose_file_clicked(btn):
            file_dialog = Gtk.FileDialog(title="Select Cover Image")
            filter_img = Gtk.FileFilter()
            filter_img.set_name("Images")
            filter_img.add_mime_type("image/jpeg")
            filter_img.add_mime_type("image/png")

            filters = Gio.ListStore.new(Gtk.FileFilter)
            filters.append(filter_img)
            file_dialog.set_filters(filters)

            def on_file_selected(dialog_inner, result):
                try:
                    file = dialog_inner.open_finish(result)
                    if file:
                        path = file.get_path()
                        print(f"[IMAGE-LOAD] Local cover file selected path={path}")
                        # Load pixbuf
                        pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)

                        # Open crop dialog
                        crop_dg = ImageCropDialog(self.get_root(), pixbuf)

                        def on_crop_response(dg, response_id):
                            if response_id == Gtk.ResponseType.OK:
                                result_pixbuf = dg.result_pixbuf
                                if result_pixbuf:
                                    # Save to temp file as PNG
                                    temp_dir = tempfile.gettempdir()
                                    temp_path = os.path.join(
                                        temp_dir, f"mixtape_crop_{os.getpid()}.png"
                                    )
                                    result_pixbuf.savev(temp_path, "png", [], [])

                                    self._selected_cover_path = temp_path
                                    cover_row.set_subtitle(
                                        f"Cropped PNG: {file.get_basename()}"
                                    )

                        crop_dg.connect("response", on_crop_response)
                        crop_dg.present()
                except Exception as e:
                    print(f"Error selecting or cropping file: {e}")

            # Use the actual application window as parent
            parent = self.get_root()
            if not isinstance(parent, Gtk.Window):
                parent = self.get_native()

            file_dialog.open(parent, None, on_file_selected)

        choose_btn = Gtk.Button(label="Choose File...")
        choose_btn.set_valign(Gtk.Align.CENTER)
        choose_btn.connect("clicked", on_choose_file_clicked)
        cover_row.add_suffix(choose_btn)

        def on_save_clicked(btn):
            new_title = title_row.get_text()
            new_desc = desc_row.get_text()
            new_privacy_idx = privacy_row.get_selected()
            privacy_api_values = ["PUBLIC", "PRIVATE", "UNLISTED"]
            new_privacy = privacy_api_values[new_privacy_idx]
            img_path = getattr(self, "_selected_cover_path", None)

            # Store original values for the background job comparison
            old_title = self.playlist_title_text
            old_desc = self.playlist_description_text
            old_privacy = getattr(self, "playlist_privacy_text", "PUBLIC").upper()

            # Optimistic UI Update
            self.playlist_name_label.set_label(new_title)
            self.playlist_title_text = new_title
            if new_desc and new_desc.strip():
                self.description_label.set_label(new_desc)
                self.description_label.set_visible(True)
            else:
                self.description_label.set_visible(False)

            self.playlist_description_text = new_desc

            if img_path:
                print(f"Optimistically showing local image: {img_path}")
                self._is_previewing_cover = True
                self.cover_img.set_from_file(Gio.File.new_for_path(img_path))

            def save_job():
                try:
                    # 1. Update Metadata
                    # Strip to avoid whitespace-only differences
                    clean_title = new_title.strip()
                    clean_desc = new_desc.strip()

                    desc_to_compare = old_desc.strip() if old_desc else ""
                    title_to_compare = old_title.strip() if old_title else ""
                    if (
                        clean_title != title_to_compare
                        or clean_desc != desc_to_compare
                        or new_privacy != old_privacy
                    ):
                        print(
                            f"DEBUG: Updating playlist metadata: '{clean_title}' (Privacy: {new_privacy})"
                        )
                        success = self.client.edit_playlist(
                            self.playlist_id,
                            title=clean_title,
                            description=clean_desc or " ",
                            privacy=new_privacy,
                        )
                        print(f"DEBUG: Metadata update success: {success}")

                    # 2. Update Image
                    if img_path:
                        print(f"DEBUG: Updating playlist thumbnail with {img_path}")
                        success = self.client.set_playlist_thumbnail(
                            self.playlist_id, img_path
                        )
                        print(f"DEBUG: Thumbnail update success: {success}")

                    # Refresh
                    # Clear cache and then reload
                    if hasattr(self.client, "_playlist_cache"):
                        if self.playlist_id in self.client._playlist_cache:
                            del self.client._playlist_cache[self.playlist_id]

                    GLib.idle_add(self.load_playlist, self.playlist_id)

                    # Update Library View if it exists
                    root = self.get_root()
                    if hasattr(root, "library_page"):
                        GLib.idle_add(root.library_page.load_library)
                except Exception as e:
                    import traceback

                    print(f"CRITICAL: Error in save_job thread: {e}")
                    traceback.print_exc()

            thread = threading.Thread(target=save_job, name="PlaylistSaveThread")
            thread.daemon = True
            thread.start()
            dialog.close()

        save_btn.connect("clicked", on_save_clicked)
        dialog.present(self.get_native())

    def _fetch_remaining_for_queue(self):
        if getattr(self, "is_fully_fetched", False):
            return
        print("Fetching remaining tracks for queue...")

        def fetch_job():
            try:
                existing_count = len(self.current_tracks)
                data = self.client.get_playlist(self.playlist_id, limit=5000)
                tracks = data.get("tracks", [])
                if len(tracks) > existing_count:
                    new_raw = tracks[existing_count:]
                    normalized = []
                    for t in new_raw:
                        artist = ", ".join(
                            a.get("name", "") for a in t.get("artists", [])
                        )
                        normalized.append(
                            {
                                "videoId": t.get("videoId"),
                                "title": t.get("title"),
                                "artist": artist,
                                "thumb": t.get("thumbnails", [])[-1]["url"]
                                if t.get("thumbnails")
                                else None,
                            }
                        )
                    if normalized:
                        GObject.idle_add(self.player.extend_queue, normalized)
                else:
                    print("DEBUG: No new tracks found.")
            except Exception as e:
                print(f"Error fetching remaining tracks: {e}")

        thread = threading.Thread(target=fetch_job)
        thread.daemon = True
        thread.start()

    # ── Compact mode ──────────────────────────────────────────────────────────

    def set_compact_mode(self, compact):
        # Propagate compact to all song row images
        self._compact = compact
        child = self.songs_list.get_first_child()
        while child:
            bin_w = child.get_first_child() if child else None
            row = getattr(bin_w, "_lv_track_ui", None) if bin_w else None
            if row and hasattr(row, "_lv_img") and hasattr(row._lv_img, "set_compact"):
                row._lv_img.set_compact(compact)
            child = child.get_next_sibling()

        if compact:
            self.add_css_class("compact")
            self.header_info_box.set_orientation(Gtk.Orientation.VERTICAL)
            self.header_info_box.set_halign(Gtk.Align.CENTER)
            self.cover_wrapper.set_halign(Gtk.Align.CENTER)
            self.details_col.set_halign(Gtk.Align.CENTER)
            self.playlist_name_label.set_halign(Gtk.Align.CENTER)
            self.playlist_name_label.set_justify(Gtk.Justification.CENTER)
            self.description_label.set_halign(Gtk.Align.CENTER)
            self.description_label.set_justify(Gtk.Justification.CENTER)
            self.meta_label.set_halign(Gtk.Align.CENTER)
            self.stats_label.set_halign(Gtk.Align.CENTER)
            self.actions_box.set_halign(Gtk.Align.CENTER)
        else:
            self.remove_css_class("compact")
            self.header_info_box.set_orientation(Gtk.Orientation.HORIZONTAL)
            self.header_info_box.set_halign(Gtk.Align.START)
            self.cover_wrapper.set_halign(Gtk.Align.START)
            self.details_col.set_halign(Gtk.Align.FILL)
            self.playlist_name_label.set_halign(Gtk.Align.START)
            self.playlist_name_label.set_justify(Gtk.Justification.LEFT)
            self.description_label.set_halign(Gtk.Align.START)
            self.description_label.set_justify(Gtk.Justification.LEFT)
            self.meta_label.set_halign(Gtk.Align.START)
            self.stats_label.set_halign(Gtk.Align.START)
            self.actions_box.set_halign(Gtk.Align.START)


# ── Utility ───────────────────────────────────────────────────────────────────


def _clear_box(box: Gtk.Box):
    child = box.get_first_child()
    while child:
        nxt = child.get_next_sibling()
        box.remove(child)
        child = nxt
