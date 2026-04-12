import os
import sys
import threading
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Gdk, Adw, GObject, Gio, GLib, Pango
from player.player import Player

HAS_TRAY = False
if sys.platform == "win32":
    try:
        from ui.tray_win import TrayIcon
        HAS_TRAY = True
    except ImportError:
        pass


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.set_default_size(1000, 700)
        self.set_title("Mixtapes")
        self._is_compact = False


        # Add custom icons path relative to current file or project root

        project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        assets_path = os.path.join(project_root, "assets", "icons")

        icon_theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        # Add GResource path
        # Add GResource path
        # The resource prefix is /com/pocoguy/muse/icons
        # The content inside is hicolor/scalable/actions/compass2-symbolic.svg
        icon_theme.add_resource_path("/com/pocoguy/muse/icons")

        # Keep file path as backup/dev
        icon_theme.add_search_path(assets_path)

        # Setup Actions
        self.setup_actions()

        # Key Controller (Global Type to Search)
        # Use CAPTURE phase to ensure we see events before children (like SearchEntry) swallow them
        ctrl = Gtk.EventControllerKey()
        ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        ctrl.connect("key-pressed", self.on_window_key_pressed)
        self.add_controller(ctrl)

        # Menu (About/Preferences)
        menu = Gio.Menu()
        menu.append("Preferences", "win.preferences")
        menu.append("About Mixtapes", "win.about")
        menu.append("Quit", "win.quit")

        menu_btn = Gtk.MenuButton()
        menu_btn.set_icon_name("open-menu-symbolic")
        menu_btn.set_menu_model(menu)

        # Content setup: ViewStack
        self.view_stack = Adw.ViewStack()
        self.view_stack.connect("notify::visible-child-name", self.on_view_changed)

        # Toolbar View (Root) - Wraps EVERYTHING
        self.root_content_view = Adw.ToolbarView()

        # Global Header Setup
        self.header_bar = Adw.HeaderBar()

        # Back Button
        self.back_btn = Gtk.Button(icon_name="go-previous-symbolic")
        self.back_btn.set_visible(False)  # Hidden by default
        self.back_btn.connect("clicked", self.on_back_clicked)
        self.header_bar.pack_start(self.back_btn)

        # Center Widget (Switcher / Title)
        self.title_bin = Adw.Bin()

        self.switcher = Adw.ViewSwitcher()
        self.switcher.set_stack(self.view_stack)
        self.switcher.set_policy(Adw.ViewSwitcherPolicy.WIDE)

        self.title_widget = Adw.WindowTitle(title="Mixtapes")

        # Default to Desktop
        self.title_bin.set_child(self.switcher)
        self.header_bar.set_title_widget(self.title_bin)

        # Upload progress button (pie chart, hidden by default)
        self._upload_progress_btn = Gtk.Button()
        self._upload_progress_btn.add_css_class("flat")
        self._upload_progress_btn.set_tooltip_text("Upload Progress")
        self._upload_progress_btn.set_visible(False)

        self._upload_progress_fraction = 0.0
        self._pie_area = Gtk.DrawingArea()
        self._pie_area.set_size_request(16, 16)
        self._pie_area.set_halign(Gtk.Align.CENTER)
        self._pie_area.set_valign(Gtk.Align.CENTER)
        self._pie_area.set_can_target(False)
        self._pie_area.set_draw_func(self._draw_upload_pie)
        self._upload_progress_btn.set_child(self._pie_area)

        self._ul_popover = Gtk.Popover()
        self._ul_popover.set_size_request(300, -1)
        self._ul_popover.set_parent(self._upload_progress_btn)
        popover_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        popover_box.set_margin_top(8)
        popover_box.set_margin_bottom(8)
        popover_box.set_margin_start(8)
        popover_box.set_margin_end(8)
        self._upload_queue_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=4
        )
        popover_box.append(self._upload_queue_box)
        self._ul_popover.set_child(popover_box)
        self._upload_progress_btn.connect("clicked", lambda b: self._ul_popover.popup())

        # Download progress button (pie chart, hidden by default)
        self._download_progress_btn = Gtk.Button()
        self._download_progress_btn.add_css_class("flat")
        self._download_progress_btn.set_tooltip_text("Download Progress")
        self._download_progress_btn.set_visible(False)

        self._download_progress_fraction = 0.0
        self._dl_pie_area = Gtk.DrawingArea()
        self._dl_pie_area.set_size_request(16, 16)
        self._dl_pie_area.set_halign(Gtk.Align.CENTER)
        self._dl_pie_area.set_valign(Gtk.Align.CENTER)
        self._dl_pie_area.set_can_target(False)
        self._dl_pie_area.set_draw_func(self._draw_download_pie)
        self._download_progress_btn.set_child(self._dl_pie_area)

        self._dl_popover = Gtk.Popover()
        self._dl_popover.set_size_request(300, -1)
        self._dl_popover.set_parent(self._download_progress_btn)
        dl_scroll = Gtk.ScrolledWindow()
        dl_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        dl_scroll.set_max_content_height(400)
        dl_scroll.set_propagate_natural_height(True)
        dl_popover_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        dl_popover_box.set_margin_top(8)
        dl_popover_box.set_margin_bottom(8)
        dl_popover_box.set_margin_start(8)
        dl_popover_box.set_margin_end(8)
        self._download_queue_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=4
        )
        dl_popover_box.append(self._download_queue_box)
        dl_scroll.set_child(dl_popover_box)
        self._dl_popover.set_child(dl_scroll)
        self._download_progress_btn.connect(
            "clicked", lambda b: self._dl_popover.popup()
        )

        self.header_bar.pack_end(menu_btn)
        self.header_bar.pack_end(self._upload_progress_btn)
        self.header_bar.pack_end(self._download_progress_btn)

        # Search Button (Mobile/Contextual) - Toggle
        self.search_btn = Gtk.ToggleButton(icon_name="system-search-symbolic")
        self.header_bar.pack_start(self.search_btn)

        self.root_content_view.add_top_bar(self.header_bar)

        self.search_bar = Gtk.SearchBar()
        self.search_bar.set_key_capture_widget(self)  # Capture keys
        self.search_bar.connect(
            "notify::search-mode-enabled", self.on_search_mode_changed
        )

        # Ensure it stays in sync (Binding)
        # We need to bind self.search_btn.active <-> self.search_bar.search_mode_enabled
        # But Gtk.SearchBar property is 'search-mode-enabled'
        self.search_bar.bind_property(
            "search-mode-enabled",
            self.search_btn,
            "active",
            GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE,
        )

        # Configure Search Entry
        search_clamp = Adw.Clamp()
        search_clamp.set_maximum_size(600)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Search...")
        self.search_entry.set_hexpand(True)
        self.search_entry.connect("search-changed", self.on_global_search_changed)
        self.search_entry.connect("stop-search", self.on_search_stop)

        search_clamp.set_child(self.search_entry)
        self.search_bar.set_child(search_clamp)
        self.search_bar.connect_entry(self.search_entry)  # NOW it exists

        self.root_content_view.add_top_bar(self.search_bar)

        # Wrap content in OverlaySplitView for Sidebar (Nautilus-style)
        self.split_view = Adw.OverlaySplitView()
        self.split_view.set_sidebar_position(Gtk.PackType.START)  # Left side
        self.split_view.set_min_sidebar_width(250)
        self.split_view.set_max_sidebar_width(450)

        # Main Stack for switching between Browser and Player on desktop
        self.main_stack = Gtk.Stack()
        self.main_stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.main_stack.set_transition_duration(300)

        # Main Content Area (Scrolled Browser)
        self.content_bin = Gtk.ScrolledWindow()
        self.content_bin.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        self.content_bin.set_child(self.view_stack)

        self.main_stack.add_named(self.content_bin, "browser")

        # Queue Sidebar (Right Side)
        from ui.queue_panel import QueuePanel

        # Global Player (Init before queue panel)

        self.player = Player()

        # Connect download manager progress to UI
        self.player.download_manager.connect("progress", self._on_download_progress)
        self.player.download_manager.connect("complete", self._on_download_complete)
        self.player.download_manager.connect("item-done", self._on_download_item_done)
        self.player.download_manager.connect(
            "item-progress", self._on_download_item_progress
        )

        self.queue_panel = QueuePanel(self.player)

        # Sidebar Content
        self.queue_panel.add_css_class("sidebar")
        self.split_view.set_sidebar(self.queue_panel)

        # Set main_stack as content of root_content_view (ToolbarView)
        self.root_content_view.set_content(self.main_stack)
        self.split_view.set_content(self.root_content_view)

        self._sidebar_explicitly_opened = False
        self.split_view.set_show_sidebar(False)  # Hidden by default
        self.split_view.set_enable_show_gesture(False)
        self.split_view.set_enable_hide_gesture(False)

        # Signal for Sidebar visibility sync
        self.split_view.connect(
            "notify::show-sidebar", self._on_sidebar_visibility_changed
        )
        self.split_view.connect("notify::collapsed", self._on_split_view_collapsed)

        # 5. Initialize BottomSheet
        self.bottom_sheet = Adw.BottomSheet()
        self.bottom_sheet.set_show_drag_handle(True)
        self.bottom_sheet.set_open(False)  # Ensure it's closed by default
        self.bottom_sheet.set_content(self.split_view)
        # Mobile-only swipe? No, expanded player handles it.

        # Global Player Bar (Always Visible)
        from ui.player_bar import PlayerBar

        # Player already inited above
        self.player_bar = PlayerBar(
            self.player,
            on_artist_click=self.on_player_bar_artist_click,
            on_queue_click=self.toggle_queue,
            on_album_click=self.on_player_bar_album_click,
        )
        self.player_bar.connect("expand-requested", self.on_expand_requested)

        # Wrap in Revealer for autohide when queue is empty
        self.player_bar_revealer = Gtk.Revealer()
        self.player_bar_revealer.set_transition_type(
            Gtk.RevealerTransitionType.SLIDE_UP
        )
        self.player_bar_revealer.set_transition_duration(200)
        self.player_bar_revealer.set_reveal_child(len(self.player.queue) > 0)
        self.player_bar_revealer.set_overflow(Gtk.Overflow.VISIBLE)
        self.player_bar_revealer.set_child(self.player_bar)
        self.root_content_view.add_bottom_bar(self.player_bar_revealer)

        # Connect signals to auto-show/hide player bar
        self.player.connect("state-changed", self._on_player_bar_visibility)
        self.player.connect("metadata-changed", self._on_player_bar_visibility)

        # View Switcher Bar (Mobile) - Stacked above Player Bar?
        self.view_switcher_bar = Adw.ViewSwitcherBar()
        self.view_switcher_bar.set_stack(self.view_stack)
        self.view_switcher_bar.set_reveal(False)
        self.view_switcher_bar.set_visible(False)
        self.root_content_view.add_bottom_bar(self.view_switcher_bar)

        # Tab Re-click Gesture Setup
        self.switcher_click = Gtk.GestureClick()
        self.switcher_click.connect("pressed", self.on_switcher_reclick)
        self.switcher.add_controller(self.switcher_click)

        self.mobile_switcher_click = Gtk.GestureClick()
        self.mobile_switcher_click.connect("pressed", self.on_switcher_reclick)
        self.view_switcher_bar.add_controller(self.mobile_switcher_click)

        from ui.expanded_player import ExpandedPlayer

        # Initialize your ExpandedPlayer (now as a standalone Box/Widget)
        self.expanded_player = ExpandedPlayer(
            self.player,
            on_artist_click=self.on_player_bar_artist_click,
            on_album_click=self.on_player_bar_album_click,
        )
        self.expanded_player.add_css_class("player-drawer")
        self.expanded_player.set_vexpand(True)
        # Connect the dismiss signal to close the sheet
        self.expanded_player.connect("dismiss", self._on_player_dismissed)

        # Do NOT set sheet or add to stack yet, managed by breakpoint or expand request

        # Register with OverlaySplitView or ToastOverlay
        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_child(self.bottom_sheet)
        self.set_content(self.toast_overlay)

        # Initialize Pages (Must be before breakpoint)
        self.init_pages()

        # 6. Responsive Breakpoints

        # COLLAPSE SIDERBAR (< 750px)
        collapse_breakpoint = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse("max-width: 750px")
        )
        collapse_breakpoint.add_setter(self.split_view, "collapsed", True)
        self.add_breakpoint(collapse_breakpoint)

        # MOBILE UI (< 500px)
        mobile_breakpoint = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse("max-width: 500px")
        )
        mobile_breakpoint.add_setter(self.view_switcher_bar, "reveal", True)
        mobile_breakpoint.add_setter(self.view_switcher_bar, "visible", True)
        mobile_breakpoint.connect("apply", self._on_mobile_breakpoint_apply)
        mobile_breakpoint.connect("unapply", self._on_mobile_breakpoint_unapply)
        self.add_breakpoint(mobile_breakpoint)

        # 7. Initial Checks
        self.check_auth()

        # Monitor network connectivity
        self._was_online = None
        monitor = Gio.NetworkMonitor.get_default()
        monitor.connect("network-changed", self._on_network_changed)

    def _on_network_changed(self, monitor, available):
        if available and self._was_online is False:
            # Just came back online
            print("[NETWORK] Back online - refreshing library")
            self.add_toast("Back online")
            if hasattr(self, "library_page"):
                self.library_page.load_library()
            if hasattr(self, "search_page"):
                self.search_page.load_explore_data()
            # Re-validate auth if needed
            from api.client import MusicClient

            client = MusicClient()
            if not client.is_authenticated():
                threading.Thread(target=self._revalidate_auth, daemon=True).start()
        elif not available and self._was_online is not False:
            print("[NETWORK] Went offline")
            self.add_toast("Offline - downloaded songs still available")
            # Grey out unavailable items
            if hasattr(self, "library_page"):
                self.library_page._apply_offline_state()
            # Show offline message on explore
            if hasattr(self, "search_page"):
                self.search_page.load_explore_data()
        self._was_online = available

    def _revalidate_auth(self):
        from api.client import MusicClient

        client = MusicClient()
        client.try_login()
        if client.is_authenticated():
            GLib.idle_add(self.add_toast, "Signed in")
            if hasattr(self, "library_page"):
                GLib.idle_add(self.library_page.load_library)

    def add_toast(self, message):
        toast = Adw.Toast.new(message)
        self.toast_overlay.add_toast(toast)

    def _get_active_responsive_child(self):
        # Helper to find if visible view has responsive features (compact mode)
        nav = self.view_stack.get_visible_child()
        if isinstance(nav, Adw.NavigationView):
            page = nav.get_visible_page()
            if page:
                child = page.get_child()
                if isinstance(child, Adw.ToolbarView):
                    content = child.get_content()
                    if hasattr(content, "set_compact_mode"):
                        return content
                elif hasattr(child, "set_compact_mode"):
                    return child
        return None

    def _get_active_filterable_child(self):
        # Helper to find if currently visible child supports search filtering (Playlist, Album)
        active_nav = self.view_stack.get_visible_child()
        if isinstance(active_nav, Adw.NavigationView):
            nav_page = active_nav.get_visible_page()
            if nav_page:
                child = nav_page.get_child()
                if isinstance(child, Adw.ToolbarView):
                    content = child.get_content()
                    if hasattr(content, "filter_content"):
                        return content
                elif hasattr(child, "filter_content"):
                    return child
        return None

    def _on_mobile_breakpoint_apply(self, breakpoint):
        self.add_css_class("compact")
        was_expanded = (
            not self._is_compact
            and self.main_stack.get_visible_child_name() == "player"
        )
        self._is_compact = True
        self.player_bar.set_compact(True)
        self.expanded_player.set_compact_mode(True)
        page = self._get_active_responsive_child()
        if page:
            page.set_compact_mode(True)

        # Switch to Mobile Title
        if hasattr(self, "title_bin"):
            self.title_bin.set_child(self.title_widget)

        if was_expanded:
            self.main_stack.set_visible_child_name("browser")
            self.bottom_sheet.set_open(True)

    def _on_mobile_breakpoint_unapply(self, breakpoint):
        self.remove_css_class("compact")
        was_expanded = self._is_compact and self.bottom_sheet.get_open()
        self._is_compact = False
        self.player_bar.set_compact(False)
        self.expanded_player.set_compact_mode(False)
        page = self._get_active_responsive_child()
        if page:
            page.set_compact_mode(False)

        # Switch to Desktop Switcher
        if hasattr(self, "title_bin"):
            self.title_bin.set_child(self.switcher)

        # Close the bottom sheet when returning to desktop size
        if hasattr(self, "bottom_sheet") and self.bottom_sheet.get_open():
            self.bottom_sheet.set_open(False)

        if was_expanded:
            self.main_stack.set_visible_child_name("player")
            self.back_btn.set_visible(True)
            self.update_back_button_visibility()

    def on_switcher_reclick(self, gesture, n_press, x, y):
        # We want to detect if the user clicked the ALREADY active tab.
        # Adw.ViewSwitcher doesn't tell us which button was clicked easily.
        # But we can check if the visible child remains the same after a short delay.
        old_name = self.view_stack.get_visible_child_name()

        def check_reclick():
            new_name = self.view_stack.get_visible_child_name()
            if old_name == new_name:
                # Same tab clicked! Reset it to root.
                nav = self._get_active_nav_view()
                if nav:
                    nav.pop_to_tag("root")
            return False

        GLib.timeout_add(100, check_reclick)

    def _on_player_dismissed(self, player):
        """Called when the player is dismissed (tapped back on desktop or swiped down on mobile)."""
        if self._is_compact:
            self.bottom_sheet.set_open(False)
        else:
            self.main_stack.set_visible_child_name("browser")
            self.back_btn.set_visible(False)
            self.update_back_button_visibility()

    def on_view_changed(self, stack, param):
        visible_name = self.view_stack.get_visible_child_name()

        # Update Back Button for the new active tab
        self.update_back_button_visibility()

        # Auto-refresh library if selected
        if visible_name == "library" and hasattr(self, "library_page"):
            # Delay slightly to allow UI transition and background state settlement
            GLib.timeout_add(100, self.library_page.load_library)

        # Close Search Bar when switching tabs
        if self.search_bar.get_search_mode():
            if visible_name != "search":
                self.search_bar.set_search_mode(False)

    def on_playlist_header_title_changed(self, page, title):
        if hasattr(self, "title_widget"):
            self.title_widget.set_title(title if title else "Mixtapes")

    def update_back_button_visibility(self, *args):
        # On desktop, if player is expanded, show back button
        if (
            not self._is_compact
            and self.main_stack.get_visible_child_name() == "player"
        ):
            self.back_btn.set_visible(True)
            return

        nav = self._get_active_nav_view()
        if nav:
            visible_page = nav.get_visible_page()
            if visible_page and nav.get_previous_page(visible_page):
                self.back_btn.set_visible(True)
            else:
                self.back_btn.set_visible(False)
                # Reset title when back at root
                if hasattr(self, "title_widget"):
                    self.title_widget.set_title("Mixtapes")

                # Refresh library if we just returned to root of library tab
                if self.view_stack.get_visible_child_name() == "library" and hasattr(
                    self, "library_page"
                ):
                    self.library_page.load_library()
        else:
            self.back_btn.set_visible(False)

    def on_back_clicked(self, btn):
        if (
            not self._is_compact
            and self.main_stack.get_visible_child_name() == "player"
        ):
            self._on_player_dismissed(None)
            return

        nav = self._get_active_nav_view()
        if nav:
            nav.pop()

    def _get_active_nav_view(self):
        nav = self.view_stack.get_visible_child()
        if isinstance(nav, Adw.NavigationView):
            return nav
        return None

    def _draw_upload_pie(self, area, cr, width, height):
        import math

        cx, cy = width / 2, height / 2
        radius = min(cx, cy) - 1
        frac = self._upload_progress_fraction

        # Background circle
        style = area.get_style_context()
        color = style.lookup_color("theme_fg_color")
        if color[0]:
            cr.set_source_rgba(color[1].red, color[1].green, color[1].blue, 0.3)
        else:
            cr.set_source_rgba(1, 1, 1, 0.3)
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.fill()

        # Progress pie
        if color[0]:
            cr.set_source_rgba(color[1].red, color[1].green, color[1].blue, 1.0)
        else:
            cr.set_source_rgba(1, 1, 1, 1.0)
        cr.move_to(cx, cy)
        cr.arc(cx, cy, radius, -math.pi / 2, -math.pi / 2 + frac * 2 * math.pi)
        cr.close_path()
        cr.fill()

    def download_tracks(self, tracks, album_title=None, album_id=None, thumb_url=None):
        """Public API to queue tracks for download from anywhere in the app."""
        dm = self.player.download_manager
        dm.queue_tracks(tracks, album_title, album_id)

        # Register playlist for incremental m3u8 generation
        if album_title and tracks:
            pl_thumb = thumb_url or (
                tracks[0].get("thumbnails", [{}])[-1].get("url")
                if tracks[0].get("thumbnails")
                else None
            )
            dm.register_playlist(album_id, album_title, tracks, pl_thumb)

        # Add items to the popover queue
        for t in tracks:
            vid = t.get("videoId")
            if not vid or dm.db.is_downloaded(vid):
                continue
            title = t.get("title", "Unknown")
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            info.set_hexpand(True)
            info.set_margin_top(4)
            info.set_margin_bottom(4)
            lbl = Gtk.Label(label=title)
            lbl.set_halign(Gtk.Align.START)
            lbl.set_ellipsize(Pango.EllipsizeMode.END)
            lbl.add_css_class("caption")
            info.append(lbl)
            status = Gtk.Label(label="Queued")
            status.set_halign(Gtk.Align.START)
            status.add_css_class("caption")
            status.add_css_class("dim-label")
            info.append(status)
            progress = Gtk.ProgressBar()
            progress.set_visible(False)
            info.append(progress)
            row.append(info)
            row._video_id = vid
            row._status_label = status
            row._progress_bar = progress
            self._download_queue_box.append(row)

        self._download_progress_btn.set_visible(True)
        dm.start()

    def download_track(self, track, album_title=None, album_id=None):
        """Download a single track."""
        self.download_tracks([track], album_title, album_id)

    def _on_download_progress(self, dm, done, total, current_title):
        self._download_progress_fraction = done / max(total, 1)
        self._dl_pie_area.queue_draw()

        # Mark the current item as downloading
        child = self._download_queue_box.get_first_child()
        while child:
            status = getattr(child, "_status_label", None)
            bar = getattr(child, "_progress_bar", None)
            if status and status.get_label() == "Queued":
                status.set_label("Downloading...")
                if bar:
                    bar.set_visible(True)
                    bar.set_fraction(0)
                break
            child = child.get_next_sibling()

    def _on_download_item_progress(self, dm, video_id, fraction):
        """Update per-item progress bar with actual download percentage."""
        child = self._download_queue_box.get_first_child()
        while child:
            if getattr(child, "_video_id", None) == video_id:
                bar = getattr(child, "_progress_bar", None)
                status = getattr(child, "_status_label", None)
                if bar:
                    bar.set_visible(True)
                    bar.set_fraction(fraction)
                if status:
                    status.set_label(f"{int(fraction * 100)}%")
                break
            child = child.get_next_sibling()

    def _on_download_item_done(self, dm, video_id, success, message):
        child = self._download_queue_box.get_first_child()
        while child:
            if getattr(child, "_video_id", None) == video_id:
                if success:
                    child._status_label.set_label("Done")
                else:
                    child._status_label.set_label("Failed")
                bar = getattr(child, "_progress_bar", None)
                if bar:
                    if success:
                        bar.set_fraction(1.0)
                    bar.set_visible(False)
                break
            child = child.get_next_sibling()

    def _on_download_complete(self, dm):
        self.add_toast("Downloads complete")
        # Clear done items after delay
        GLib.timeout_add(5000, self._clear_download_queue)

    def _clear_download_queue(self):
        child = self._download_queue_box.get_first_child()
        while child:
            next_c = child.get_next_sibling()
            self._download_queue_box.remove(child)
            child = next_c
        self._download_progress_btn.set_visible(False)
        self._download_progress_fraction = 0.0
        self._dl_pie_area.queue_draw()
        return False

    def _draw_download_pie(self, area, cr, width, height):
        import math

        cx, cy = width / 2, height / 2
        radius = min(cx, cy) - 1
        frac = self._download_progress_fraction

        style = area.get_style_context()
        color = style.lookup_color("theme_fg_color")
        if color[0]:
            cr.set_source_rgba(color[1].red, color[1].green, color[1].blue, 0.3)
        else:
            cr.set_source_rgba(1, 1, 1, 0.3)
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.fill()

        if color[0]:
            cr.set_source_rgba(color[1].red, color[1].green, color[1].blue, 1.0)
        else:
            cr.set_source_rgba(1, 1, 1, 1.0)
        cr.move_to(cx, cy)
        cr.arc(cx, cy, radius, -math.pi / 2, -math.pi / 2 + frac * 2 * math.pi)
        cr.close_path()
        cr.fill()

    def setup_actions(self):
        # About Action
        action = Gio.SimpleAction.new("about", None)
        action.connect("activate", self.show_about)
        self.add_action(action)

        # Preferences Action
        pref_action = Gio.SimpleAction.new("preferences", None)
        pref_action.connect("activate", self.show_preferences)
        self.add_action(pref_action)

        # Quit Action (force quit even with songs in queue)
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", self._on_force_quit)
        self.add_action(quit_action)

        # Intercept window close to hide instead of quit when playing
        self.connect("close-request", self._on_close_request)

        # On Windows, manage tray icon when window visibility changes
        if HAS_TRAY:
            self.connect("notify::visible", self._on_visibility_changed)

    def _on_close_request(self, window):
        """Hide window instead of quitting if there are songs in the queue."""
        if self.player.queue and self.player.current_queue_index >= 0:
            self.set_visible(False)
            return True  # Prevent default close
        if HAS_TRAY and hasattr(self, "_tray_icon"):
            self._tray_icon.hide()
        return False  # Allow normal close

    def _on_visibility_changed(self, window, pspec):
        if self.get_visible():
            # Window shown — hide tray icon
            if hasattr(self, "_tray_icon"):
                self._tray_icon.hide()
                del self._tray_icon
        else:
            # Window hidden — show tray icon
            if not hasattr(self, "_tray_icon"):
                self._tray_icon = TrayIcon(self, self.player)
                self._tray_icon.show()

    def _on_force_quit(self, action, param):
        """Force quit the application."""
        self.player.stop()
        app = self.get_application()
        if app:
            app.quit()

    def show_about(self, action, param):
        about = Adw.AboutDialog()
        about.set_application_name("Mixtapes")
        about.set_developer_name("POCOGuy")
        about.set_version("2026-11-04.0")
        about.set_website("https://www.pocoguy.com/#!/mixtapes")
        about.set_copyright("© 2026 POCOGuy")
        about.set_license_type(Gtk.License.GPL_3_0)
        about.present(self)

    def show_preferences(self, action, param):
        prefs = Adw.PreferencesDialog()

        page = Adw.PreferencesPage()
        page.set_title("General")
        page.set_icon_name("settings-symbolic")
        prefs.add(page)

        app_group = Adw.PreferencesGroup()
        app_group.set_title("Application")
        page.add(app_group)

        import logger

        debug_row = Adw.SwitchRow()
        debug_row.set_title("Enable Debug Logs")
        debug_row.set_subtitle("Print diagnostic information to the terminal")
        debug_row.set_active(logger.get_debug_logs())
        debug_row.connect(
            "notify::active",
            lambda switch, param: logger.set_debug_logs(switch.get_active()),
        )
        app_group.add(debug_row)

        # Force offline mode
        import json as _json

        _prefs_path = os.path.join(GLib.get_user_data_dir(), "muse", "prefs.json")
        _prefs = {}
        try:
            if os.path.exists(_prefs_path):
                with open(_prefs_path) as f:
                    _prefs = _json.load(f)
        except Exception:
            pass

        offline_row = Adw.SwitchRow()
        offline_row.set_title("Force Offline Mode")
        offline_row.set_subtitle(
            "Disable all network requests and use only downloaded content"
        )
        offline_row.set_active(_prefs.get("force_offline", False))

        def on_offline_toggled(switch, pspec):
            _prefs["force_offline"] = switch.get_active()
            os.makedirs(os.path.dirname(_prefs_path), exist_ok=True)
            with open(_prefs_path, "w") as f:
                _json.dump(_prefs, f)
            if hasattr(self, "library_page"):
                self.library_page._apply_offline_state()
                self.library_page.load_library()
            if hasattr(self, "search_page"):
                self.search_page.load_explore_data()

        offline_row.connect("notify::active", on_offline_toggled)
        app_group.add(offline_row)

        from api.client import MusicClient

        is_authed = MusicClient().is_authenticated()

        group = Adw.PreferencesGroup()
        group.set_title("Account")
        page.add(group)

        # Sign Out Row
        row = Adw.ActionRow()
        row.set_title("Sign Out" if is_authed else "Sign In")
        row.set_subtitle(
            "Remove saved credentials and log out of YouTube Music"
            if is_authed
            else "Sign in to YouTube Music to access your library"
        )

        logout_btn = Gtk.Button(label="Sign Out" if is_authed else "Sign In")
        logout_btn.set_valign(Gtk.Align.CENTER)

        if is_authed:
            logout_btn.add_css_class("destructive-action")
            logout_btn.connect("clicked", self.on_logout_clicked, prefs)
        else:
            logout_btn.add_css_class("suggested-action")
            logout_btn.connect(
                "clicked", lambda b, p: (p.close(), self.check_auth()), prefs
            )

        row.add_suffix(logout_btn)
        group.add(row)

        # Downloads group
        dl_group = Adw.PreferencesGroup()
        dl_group.set_title("Downloads")
        page.add(dl_group)

        from player.downloads import (
            get_preferred_format,
            set_preferred_format,
            FORMATS,
            get_music_dir,
        )

        format_row = Adw.ComboRow()
        format_row.set_title("Audio Format")
        format_row.set_subtitle(f"Songs are saved to {get_music_dir()}")
        format_names = list(FORMATS.keys())
        format_labels = [
            "Opus (smallest)",
            "MP3 (universal)",
            "M4A (Apple)",
            "FLAC (lossless)",
            "OGG (Vorbis)",
        ]
        format_row.set_model(Gtk.StringList.new(format_labels))

        current_fmt = get_preferred_format()
        for i, name in enumerate(format_names):
            if name == current_fmt:
                format_row.set_selected(i)
                break

        def on_format_changed(row, pspec):
            idx = row.get_selected()
            if 0 <= idx < len(format_names):
                set_preferred_format(format_names[idx])

        format_row.connect("notify::selected", on_format_changed)
        dl_group.add(format_row)

        prefs.present(self)

    def on_logout_clicked(self, btn, prefs_window):
        from api.client import MusicClient

        client = MusicClient()
        if client.logout():
            prefs_window.close()
            # Clear library UI immediately
            if hasattr(self, "library_page"):
                self.library_page.clear()
            # Trigger auth check to show login dialog
            self.check_auth()

    def init_pages(self):
        # PlaylistPage imported at top level now

        # Create Pages
        # Refactored to Single Global Header architecture
        # Each tab is just a NavigationView wrapping the content

        def create_tab_nav(page_content, title, icon, name):
            # Nav Page & View
            # We wrap content in NavigationPage because NavigationView requires it
            nav_page = Adw.NavigationPage(child=page_content, title=title)
            nav_page.set_tag("root")  # Tag for resetting
            nav_view = Adw.NavigationView()
            nav_view.add(nav_page)

            # Connect to page changes to update Back Button
            nav_view.connect("notify::visible-page", self.update_back_button_visibility)

            return nav_view

        from ui.pages.home import HomePage
        from ui.pages.library import LibraryPage
        from ui.pages.search import SearchPage

        # Instantiate Pages
        home_page = HomePage(self.player)
        self.library_page = LibraryPage(self.player, self.open_playlist)
        search_page = SearchPage(self.player, self.open_playlist)
        self.search_page = search_page  # Store for global key controller

        self.tab_header_widgets = []  # Init list

        # Add to Stack and Configure Pages
        page_home = self.view_stack.add_named(
            create_tab_nav(home_page, "Home", "user-home-symbolic", "home"), "home"
        )
        page_home.set_title("Home")
        page_home.set_icon_name("user-home-symbolic")

        page_lib = self.view_stack.add_named(
            create_tab_nav(
                self.library_page, "Library", "media-optical-symbolic", "library"
            ),
            "library",
        )
        page_lib.set_title("Library")
        page_lib.set_icon_name("media-optical-symbolic")

        page_lib.set_icon_name("media-optical-symbolic")

        page_search = self.view_stack.add_named(
            create_tab_nav(search_page, "Explore", "compass2-symbolic", "search"),
            "search",
        )
        page_search.set_title("Explore")
        page_search.set_icon_name("compass2-symbolic")

        self.previous_view_stack_item = "home"

    def set_header_title(self, title):
        pass

    def _get_page_content(self, tab_name):
        # Helper to traverse: NavView -> NavPage -> ToolbarView -> Content
        nav_view = self.view_stack.get_child_by_name(tab_name)
        if isinstance(nav_view, Adw.NavigationView):
            # We assume the root page of the nav view is our tab page
            # We stored page instances in init_pages, so direct traversal is not needed for Search/Library.
            pass
        return None

    def on_window_key_pressed(self, controller, keyval, keycode, state):
        # Handle Escape key for Back / Close Search
        if keyval == Gdk.KEY_Escape:
            if self.search_bar.get_search_mode():
                # Manually close it and stop propagation
                self.search_bar.set_search_mode(False)
                # Clear focus from entry to ensure next keys are handled by the window
                self.grab_focus()
                return True

            if self.back_btn.get_visible():
                self.on_back_clicked(None)
                return True
            return False

        # Redirection logic for Global Search (Alphanumeric characters)
        # 1. Ignore if focus is in an entry
        focus = self.get_focus()
        if isinstance(focus, (Gtk.Entry, Gtk.SearchEntry, Gtk.TextView, Gtk.Editable)):
            return False

        if keyval == Gdk.KEY_space:
            self.player_bar.on_play_clicked(None)
            return True

        # 2. DECIDE if it's a searchable character
        uni = Gdk.keyval_to_unicode(keyval)
        if uni == 0:
            return False
        char = chr(uni)
        if not char.isprintable():
            return False

        # 3. Ignore control/alt/meta keys
        mask = state & (
            Gdk.ModifierType.CONTROL_MASK
            | Gdk.ModifierType.ALT_MASK
            | Gdk.ModifierType.META_MASK
        )
        if mask:
            return False

        # 4. Context-Aware Redirection: If NOT in a filterable playlist, switch tab first
        if not self._get_active_filterable_child():
            if self.view_stack.get_visible_child_name() != "search":
                # Ensure we switch tab before SearchBar captures the character
                self.view_stack.set_visible_child_name("search")

            # Ensure search tab is at root (results view)
            nav = self.view_stack.get_child_by_name("search")
            if isinstance(nav, Adw.NavigationView):
                root_page = nav.get_visible_page()
                if root_page and nav.get_previous_page(root_page):
                    nav.pop_to_tag("root")

            # Manually trigger search mode and insert the character
            # This avoids the "ignored first character" bug during tab switches
            self.search_bar.set_search_mode(True)
            self.search_entry.grab_focus()
            self.search_entry.set_text(char)
            self.search_entry.set_position(-1)  # Move cursor to end
            return True

        # Let the event propagate so GtkSearchBar can capture it
        return False

    def on_global_search_changed(self, entry):
        text = entry.get_text()

        # Context-Aware Search Logic (Double check redirection here too)
        filterable_child = self._get_active_filterable_child()
        if filterable_child:
            filterable_child.filter_content(text)
        else:
            # Global Search Redirection (Safety fallback)
            if self.view_stack.get_visible_child_name() != "search":
                GLib.idle_add(self.view_stack.set_visible_child_name, "search")

            nav = self.view_stack.get_child_by_name("search")
            if isinstance(nav, Adw.NavigationView):
                root_page = nav.get_visible_page()
                if root_page and nav.get_previous_page(root_page):
                    nav.pop_to_tag("root")

            if hasattr(self, "search_page"):
                self.search_page.on_external_search(text)

    def on_search_stop(self, entry):
        self.search_bar.set_search_mode(False)
        # Crucial: Clear focus so the next Esc goes to the Window Controller
        self.grab_focus()

        filterable_child = self._get_active_filterable_child()
        if filterable_child:
            filterable_child.filter_content("")

    def on_search_mode_changed(self, search_bar, param):
        mode = search_bar.get_search_mode()

        if mode:
            # Enabling search
            self.search_entry.grab_focus()

            # If we are NOT in a playlist, switch to Explore tab
            filterable = self._get_active_filterable_child()
            if not filterable:
                if self.view_stack.get_visible_child_name() != "search":
                    # Use idle_add to avoid issues with current signal processing
                    GLib.idle_add(self.view_stack.set_visible_child_name, "search")

                # Reset search view to root
                nav = self.view_stack.get_child_by_name("search")
                if isinstance(nav, Adw.NavigationView):
                    root_page = nav.get_visible_page()
                    if root_page and nav.get_previous_page(root_page):
                        nav.pop_to_tag("root")

    # on_search_btn_clicked removed (replaced by binding)

    def open_playlist(self, playlist_id, initial_data=None):
        # Close search bar when navigating to a detail page
        if self.search_bar.get_search_mode():
            self.search_bar.set_search_mode(False)

        # Find active navigation view
        active_nav = self.view_stack.get_visible_child()
        if not isinstance(active_nav, Adw.NavigationView):
            print("Error: Active view is not a NavigationView")
            return

        # Create fresh playlist page (to ensure clean state and avoid parent issues)
        # We need to pass self.network_client? No, PlaylistPage creates its own.
        # We need self.player.
        # We need self.player.
        from ui.pages.playlist import PlaylistPage

        playlist_page = PlaylistPage(self.player)

        # Wrap in NavigationPage
        # PlaylistPage already has a ToolbarView/Header internally.
        # Adw.NavigationView expects Adw.NavigationPage.
        # Adw.NavigationPage expects a child widget.
        nav_page = Adw.NavigationPage(child=playlist_page, title="Playlist")

        # Push to stack
        active_nav.push(nav_page)

        # Load data
        playlist_page.load_playlist(playlist_id, initial_data)

        # Connect title change signal
        playlist_page.connect(
            "header-title-changed", self.on_playlist_header_title_changed
        )

        # Check if we are in mobile mode (compact) - Force true if width < 500
        # self.view_switcher_bar.get_reveal() might be delayed?
        width = self.get_width()
        if width < 500:
            playlist_page.set_compact_mode(True)
        elif hasattr(self, "view_switcher_bar") and self.view_switcher_bar.get_reveal():
            playlist_page.set_compact_mode(True)

        # Connect tab re-click logic if not already done?
        # (This is handled globally in init_pages now)

        # Note: We don't need to manually update window title or back button.
        # Adw.NavigationView handles the transition.
        # PlaylistPage's internal header will show a back button IF it's an Adw.HeaderBar
        # AND we are using Adw.NavigationView.
        # BUT: PlaylistPage has `self.header_bar = Adw.HeaderBar()`.
        # When inside NavigationView, this header should automatically get a back button.
        pass

    def on_playlist_back(self):
        # Called when playlist internal back is triggered (if any)
        # We rely on NavView pop.
        pass

    def open_artist(self, channel_id, initial_name=None):
        # Uploaded artists can't be opened as regular artists
        if channel_id and channel_id.startswith("FEmusic_library_privately_owned"):
            self._open_upload_artist(channel_id, initial_name or "Artist")
            return

        # Close search bar when navigating to a detail page
        if self.search_bar.get_search_mode():
            self.search_bar.set_search_mode(False)

        # Find active navigation view
        active_nav = self.view_stack.get_visible_child()
        if not isinstance(active_nav, Adw.NavigationView):
            print("Error: Active view is not a NavigationView")
            return
        from ui.pages.artist import ArtistPage

        # Create fresh artist page
        artist_page = ArtistPage(self.player, self.open_playlist)

        nav_page = Adw.NavigationPage(
            child=artist_page, title=initial_name if initial_name else "Artist"
        )

        active_nav.push(nav_page)

        artist_page.load_artist(channel_id, initial_name)

        # Connect title change
        artist_page.connect(
            "header-title-changed", self.on_playlist_header_title_changed
        )  # Reuse same handler

    def open_discography(
        self, channel_id, title, browse_id=None, params=None, initial_items=None
    ):
        if self.search_bar.get_search_mode():
            self.search_bar.set_search_mode(False)

        active_nav = self.view_stack.get_visible_child()
        if not isinstance(active_nav, Adw.NavigationView):
            print("Error: Active view is not a NavigationView")
            return

        from ui.pages.discography import DiscographyPage

        disco_page = DiscographyPage(self.player, self.open_playlist)
        disco_page.connect(
            "header-title-changed", self.on_playlist_header_title_changed
        )

        nav_page = Adw.NavigationPage(child=disco_page, title=title)

        active_nav.push(nav_page)

        disco_page.load_discography(channel_id, title, browse_id, params, initial_items)

    def open_mood(self, params, title):
        if self.search_bar.get_search_mode():
            self.search_bar.set_search_mode(False)

        active_nav = self.view_stack.get_visible_child()
        if not isinstance(active_nav, Adw.NavigationView):
            print("Error: Active view is not a NavigationView")
            return

        from ui.pages.mood import MoodPage

        mood_page = MoodPage(self.player, self.open_playlist)
        mood_page.connect("header-title-changed", self.on_playlist_header_title_changed)

        nav_page = Adw.NavigationPage(child=mood_page, title=title)

        active_nav.push(nav_page)

        mood_page.load_mood(params, title)

    def open_all_moods(self, items, title):
        if self.search_bar.get_search_mode():
            self.search_bar.set_search_mode(False)

        active_nav = self.view_stack.get_visible_child()
        if not isinstance(active_nav, Adw.NavigationView):
            print("Error: Active view is not a NavigationView")
            return

        from ui.pages.all_moods import AllMoodsPage

        all_moods_page = AllMoodsPage(items, title)
        all_moods_page.connect(
            "header-title-changed", self.on_playlist_header_title_changed
        )

        display_title = f"All {title}"
        if title == "Moods & Moments":
            display_title = "All Moods & Moments"

        nav_page = Adw.NavigationPage(child=all_moods_page, title=display_title)
        active_nav.push(nav_page)

    def open_category(self, params, title):
        if self.search_bar.get_search_mode():
            self.search_bar.set_search_mode(False)

        active_nav = self.view_stack.get_visible_child()
        if not isinstance(active_nav, Adw.NavigationView):
            return

        from ui.pages.category import CategoryPage

        cat_page = CategoryPage(self.player, self.open_playlist)
        cat_page.connect("header-title-changed", self.on_playlist_header_title_changed)

        nav_page = Adw.NavigationPage(child=cat_page, title=title)
        active_nav.push(nav_page)

        cat_page.load_category(params, title)

    def on_player_bar_artist_click(self):
        # Try to get artist ID from the current queue track's data first
        idx = self.player.current_queue_index
        if 0 <= idx < len(self.player.queue):
            track = self.player.queue[idx]
            artists = track.get("artists", [])
            if artists and isinstance(artists, list):
                artist = artists[0]
                if isinstance(artist, dict) and artist.get("id"):
                    aid = artist["id"]
                    name = artist.get("name", "Artist")
                    # Upload artists can't be opened as regular artists
                    if aid.startswith("FEmusic_library_privately_owned"):
                        self._open_upload_artist(aid, name)
                    else:
                        self.open_artist(aid, name)
                    return

        # Fallback: resolve via get_song API (won't work for uploaded songs)
        vid = self.player.current_video_id
        if vid:
            threading.Thread(
                target=self._resolve_artist_from_player, daemon=True
            ).start()

    def _open_upload_artist(self, browse_id, name):
        """Open an uploaded artist as a pseudo-playlist."""
        if hasattr(self, "uploads_page"):
            # Use the UploadsPage's artist handler
            self.uploads_page._on_artist_activated(
                None,
                type(
                    "Row", (), {"artist_data": {"browseId": browse_id, "artist": name}}
                )(),
            )
        elif hasattr(self, "library_page") and hasattr(
            self.library_page, "uploads_page"
        ):
            self.library_page.uploads_page._on_artist_activated(
                None,
                type(
                    "Row", (), {"artist_data": {"browseId": browse_id, "artist": name}}
                )(),
            )

    def _resolve_artist_from_player(self):
        vid = self.player.current_video_id
        if not vid:
            return

        from api.client import MusicClient

        client = MusicClient()
        song_data = client.get_song(vid)
        if song_data and "videoDetails" in song_data:
            channel_id = song_data["videoDetails"].get("channelId")
            if channel_id:
                artist_name = song_data["videoDetails"].get("author", "Artist")
                GObject.idle_add(self.open_artist, channel_id, artist_name)

    def on_player_bar_album_click(self):
        print("Player Bar Album Clicked")
        threading.Thread(target=self._resolve_album_from_player).start()

    def _resolve_album_from_player(self):
        vid = self.player.current_video_id
        if not vid:
            return

        # First check if the current track object in queue has the album ID natively
        track = None
        if 0 <= self.player.current_queue_index < len(self.player.queue):
            track = self.player.queue[self.player.current_queue_index]

        album_id = None
        album_name = "Album"

        if track and "album" in track and track["album"]:
            album = track["album"]
            if isinstance(album, dict):
                album_id = album.get("id")
                album_name = album.get("name", album_name)
            elif isinstance(album, str):
                album_name = album

        if not album_id:
            # Fall back to fetching watch playlist to see if it belongs to an album
            from api.client import MusicClient

            client = MusicClient()
            if client.api:
                try:
                    res = client.api.get_watch_playlist(videoId=vid)
                    tracks = res.get("tracks", [])
                    if tracks and "album" in tracks[0] and tracks[0]["album"]:
                        album = tracks[0]["album"]
                        if isinstance(album, dict):
                            album_id = album.get("id")
                            album_name = album.get("name", "Album")
                        elif isinstance(album, str):
                            album_name = album
                except Exception as e:
                    print(f"Failed to resolve album: {e}")

        if album_id:
            # Check if it starts with 'MPREb'
            if album_id.startswith("MPREb_"):
                # Get album, then take the audioPlaylistId
                from api.client import MusicClient

                client = MusicClient()
                playlist_id = client.api.get_album(album_id).get("audioPlaylistId")
                GObject.idle_add(self.open_playlist, playlist_id, {"title": album_name})
            else:
                # It's an implied playlist ID or similar
                GObject.idle_add(self.open_playlist, album_id, {"title": album_name})
        else:
            print("No album found for the current track.")

    def on_sidebar_row_selected(self, box, row):
        if row:
            # Ensure we are not in playlist view (pop if needed)
            # Basic logic: If we are deep in nav stack, pop to root.
            # self.nav_view.pop_to_tag("root")? No, "root" isn't a tag in that sense.
            # pop_to_page(self.root_nav_page)
            self.nav_view.pop_to_page(self.root_nav_page)

            self.view_stack.set_visible_child_name(row.name_id)
            self.set_header_title("Mixtapes")

            if row.name_id == "library":
                self.library_page.load_library()

    def _is_online(self):
        """Quick check if we have network connectivity."""
        import socket

        try:
            socket.create_connection(("music.youtube.com", 443), timeout=3)
            return True
        except OSError:
            return False

    def check_auth(self):
        from api.client import MusicClient
        from ui.login import LoginDialog

        client = MusicClient()

        # If no auth file at all and we're online, show login
        if not client.is_authenticated():
            if self._is_online():
                print("Authentication missing. Showing login dialog.")
                GObject.timeout_add(500, lambda: self.show_login(LoginDialog))
            else:
                print("Offline and no auth. Running in offline mode.")
                self.add_toast("No internet - running in offline mode")
            return

        # Validate session in background, but only if online
        def _validate():
            if not self._is_online():
                print("Offline - skipping auth validation, using cached session.")
                GLib.idle_add(self.add_toast, "Offline mode - using cached library")
                return
            valid = client.validate_session()
            if not valid:
                client._is_authed = False
                GLib.idle_add(self._on_auth_invalid)

        threading.Thread(target=_validate, daemon=True).start()

    def _on_auth_invalid(self):
        from ui.login import LoginDialog

        print("Authentication invalid. Showing login dialog.")
        self.show_login(LoginDialog)

    def show_login(self, dialog_cls):
        dialog = dialog_cls(self)
        dialog.connect("close-request", self.on_login_close)  # Handle close if needed
        dialog.present()
        return False

    def on_login_close(self, dialog):
        # Refresh data
        if hasattr(self, "library_page"):
            self.library_page.load_library()

    def _on_mobile_breakpoint_apply(self, *args):
        print(f"[DEBUG-UI] Mobile breakpoint apply. Width: {self.get_width()}")
        self._is_compact = True
        self.add_css_class("compact")

        # Hide tabs, show title
        if hasattr(self, "title_bin") and hasattr(self, "title_widget"):
            self.title_bin.set_child(self.title_widget)

        if hasattr(self, "player_bar"):
            self.player_bar.set_compact(True)
        # Apply to current page
        self._sync_page_compact()
        # On mobile, we usually want sidebar closed by default
        if hasattr(self, "split_view"):
            # Set sidebar to False but DO NOT update self._sidebar_explicitly_opened
            self.split_view.set_show_sidebar(False)

        # Dynamic Reparenting for ExpandedPlayer
        if hasattr(self, "expanded_player"):
            parent = self.expanded_player.get_parent()
            if parent == self.main_stack:
                self.main_stack.remove(self.expanded_player)
            self.bottom_sheet.set_sheet(self.expanded_player)

    def _on_mobile_breakpoint_unapply(self, *args):
        print(f"[DEBUG-UI] Mobile breakpoint unapply. Width: {self.get_width()}")
        self._is_compact = False
        self.remove_css_class("compact")

        # Show tabs, hide title
        if hasattr(self, "title_bin") and hasattr(self, "switcher"):
            self.title_bin.set_child(self.switcher)

        if hasattr(self, "player_bar"):
            self.player_bar.set_compact(False)

        # Close BottomSheet when moving back to desktop
        if hasattr(self, "bottom_sheet"):
            self.bottom_sheet.set_open(False)

        # Apply to current page
        self._sync_page_compact()
        # Restore desktop state
        if hasattr(self, "split_view"):
            GLib.idle_add(self._restore_sidebar_state)

        # Dynamic Reparenting back to Stack for Desktop
        if hasattr(self, "expanded_player"):
            self.bottom_sheet.set_sheet(None)
            parent = self.expanded_player.get_parent()
            if parent != self.main_stack:
                self.main_stack.add_named(self.expanded_player, "player")

    def _restore_sidebar_state(self):
        if hasattr(self, "split_view"):
            has_queue = len(self.player.queue) > 0
            show = self._sidebar_explicitly_opened and has_queue
            print(
                f"[DEBUG-UI] _restore_sidebar_state: show={show}, explicitly_opened={self._sidebar_explicitly_opened}, has_queue={has_queue}, collapsed={self.split_view.get_collapsed()}"
            )
            self.split_view.set_show_sidebar(show)
        return False  # Run once

    def _sync_page_compact(self):
        # Notify current pages
        for page_name in ["home", "library", "search"]:
            if hasattr(self, f"{page_name}_page"):
                page = getattr(self, f"{page_name}_page")
                if hasattr(page, "set_compact_mode"):
                    page.set_compact_mode(self._is_compact)

        # Also notify any dynamic pages in navigation stacks?
        # For simplicity, we can look at the visible page of the navigation stack
        nav = self.view_stack.get_visible_child()
        if isinstance(nav, Adw.NavigationView):
            page = nav.get_visible_page()
            if page:
                child = page.get_child()
                # If it's a ToolbarView, look at content
                if isinstance(child, Adw.ToolbarView):
                    child = child.get_content()
                if hasattr(child, "set_compact_mode"):
                    child.set_compact_mode(self._is_compact)

    def _on_sidebar_visibility_changed(self, split_view, param):
        is_visible = split_view.get_show_sidebar()
        print(f"[DEBUG-UI] Sidebar visibility changed: {is_visible}")
        if hasattr(self, "player_bar"):
            self.player_bar.set_queue_active(is_visible)

    def _on_player_bar_visibility(self, player, *args):
        has_queue = len(self.player.queue) > 0
        self.player_bar_revealer.set_reveal_child(has_queue)

        # Also close sidebar if queue becomes empty
        if not has_queue and hasattr(self, "split_view"):
            if self.split_view.get_show_sidebar():
                print("[DEBUG-UI] Closing sidebar because queue is empty")
                self.split_view.set_show_sidebar(False)
                # Should we reset _sidebar_explicitly_opened?
                # Probably yes, as the "context" is gone.
                self._sidebar_explicitly_opened = False

    def _on_split_view_collapsed(self, split_view, param):
        collapsed = split_view.get_collapsed()
        print(f"[DEBUG-UI] _on_split_view_collapsed: {collapsed}")
        if not collapsed:
            # When uncollapsing (going back to desktop), force the state
            GLib.idle_add(self._restore_sidebar_state)

    def toggle_queue(self):
        """Toggles the visibility of the Queue Sidebar."""
        if hasattr(self, "split_view"):
            current = self.split_view.get_show_sidebar()
            new_state = not current
            print(
                f"[DEBUG-UI] toggle_queue. Current={current}, New={new_state}, Has queue={len(self.player.queue) > 0}"
            )

            if new_state and not self.player.queue:
                print(f"[DEBUG-UI] toggle_queue: Refusing to open empty queue")
                return False

            self.split_view.set_show_sidebar(new_state)

            # Persist state only when not collapsed (desktop view)
            # or if explicitly toggled in mobile overlay
            self._sidebar_explicitly_opened = new_state
            print(
                f"[DEBUG-UI] sidebar_explicitly_opened set to: {self._sidebar_explicitly_opened}"
            )

        # Refresh explore/search
        if hasattr(self, "search_page"):
            self.search_page.refresh_explore()

        return False

    def on_expand_requested(self, player_bar):
        # Sync Initial State (Metadata/Art)
        v_id = self.player.current_video_id
        if v_id:
            t = (
                self.player_bar.current_title
                if hasattr(self.player_bar, "current_title")
                else "Loading..."
            )
            a = (
                self.player_bar.current_artist
                if hasattr(self.player_bar, "current_artist")
                else "Unknown"
            )
            self.expanded_player.on_metadata_changed(
                self.player, t, a, self.player_bar.cover_img.url, v_id, "INDIFFERENT"
            )

        if self._is_compact:
            # Ensure it's correctly parented
            if self.expanded_player.get_parent() != self.bottom_sheet:
                # set_sheet handles this
                self.bottom_sheet.set_sheet(self.expanded_player)
            self.bottom_sheet.set_open(True)
        else:
            # Desktop stack navigation
            if self.expanded_player.get_parent() != self.main_stack:
                self.bottom_sheet.set_sheet(None)
                self.main_stack.add_named(self.expanded_player, "player")
            self.main_stack.set_visible_child_name("player")
            self.back_btn.set_visible(True)
