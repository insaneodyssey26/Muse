import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GObject

from api.client import MusicClient

class LoginDialog(Adw.Window):
    def __init__(self, parent_window):
        super().__init__()
        self.set_modal(True)
        self.set_transient_for(parent_window)
        self.set_default_size(600, 500)
        self.set_title("Login to YouTube Music")
        
        # Main Layout: Toolbar View
        self.toolbar_view = Adw.ToolbarView()
        self.set_content(self.toolbar_view)
        
        # Header Bar
        header = Adw.HeaderBar()
        header.set_show_title(True)
        self.toolbar_view.add_top_bar(header)
        
        # Skip Button
        skip_btn = Gtk.Button(label="Skip")
        skip_btn.connect("clicked", lambda x: self.close())
        header.pack_end(skip_btn)
        
        # Access content via view stack
        self.stack = Adw.ViewStack()
        
        # 1. Browser View
        browser_view = self._build_browser_view()
        self.stack.add_titled(browser_view, "browser", "Browser Login")
        
        # 2. Manual Headers View
        manual_view = self._build_manual_view()
        self.stack.add_titled(manual_view, "manual", "Manual Headers")
        
        self.toolbar_view.set_content(self.stack)
        
        # View Switcher Bar
        switcher_bar = Adw.ViewSwitcherBar()
        switcher_bar.set_stack(self.stack)
        switcher_bar.set_reveal(True)
        self.toolbar_view.add_bottom_bar(switcher_bar)

    def _build_browser_view(self):
        # Status Page wrapper
        page = Adw.StatusPage()
        page.set_title("Browser Login")
        page.set_description("The most reliable way to login is via `browser.json`.")
        page.set_icon_name("web-browser-symbolic")
        
        # Content Clamp
        clamp = Adw.Clamp()
        clamp.set_maximum_size(500)
        
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        
        # Instructions
        lbl = Gtk.Label()
        lbl.set_wrap(True)
        lbl.set_justify(Gtk.Justification.LEFT)
        lbl.set_markup("""<span size='large'><b>Step 1:</b> Run this in your terminal:</span>
<tt>ytmusicapi browser</tt>

<span size='large'><b>Step 2:</b> Follow instructions to paste headers.</span>

<span size='large'><b>Step 3:</b> Click 'Import browser.json' below.</span>""")
        lbl.set_xalign(0)
        box.append(lbl)
        
        # Action Button
        self.btn_import = Gtk.Button(label="Import browser.json")
        self.btn_import.set_halign(Gtk.Align.CENTER)
        self.btn_import.add_css_class("pill")
        self.btn_import.add_css_class("suggested-action")
        self.btn_import.connect("clicked", self.on_import_clicked)
        box.append(self.btn_import)

        # Status Label
        self.lbl_status = Gtk.Label(label="")
        box.append(self.lbl_status)
        
        clamp.set_child(box)
        page.set_child(clamp)
        
        return page

    def _build_manual_view(self):
        page = Adw.StatusPage()
        page.set_title("Manual / Advanced")
        page.set_description("Paste headers JSON or oauth.json content directly.")
        
        clamp = Adw.Clamp()
        clamp.set_maximum_size(600)
        
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        
        # Text Area for headers
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_min_content_height(300) # Give it some height
        
        self.text_view = Gtk.TextView()
        self.text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.text_view.set_monospace(True)
        self.text_view.set_left_margin(12)
        self.text_view.set_right_margin(12)
        self.text_view.set_top_margin(12)
        self.text_view.set_bottom_margin(12)
        
        scrolled.set_child(self.text_view)
        
        frame = Gtk.Frame()
        frame.set_child(scrolled)
        box.append(frame)
        
        login_btn = Gtk.Button(label="Login with JSON")
        login_btn.set_halign(Gtk.Align.CENTER)
        login_btn.add_css_class("pill")
        login_btn.add_css_class("suggested-action")
        login_btn.connect("clicked", self.on_manual_login)
        box.append(login_btn)
        
        clamp.set_child(box)
        page.set_child(clamp)
        
        return page

    def on_import_clicked(self, btn):
        import os
        # Check for browser.json in CWD
        path = os.path.join(os.getcwd(), 'browser.json')
        if os.path.exists(path):
            self.lbl_status.set_text(f"Found {path}...")
            client = MusicClient()
            if client.login(path):
                self.lbl_status.set_markup("<span color='green'>Login Successful! Restarting app...</span>")
                # Close after delay?
                self.close()
            else:
                self.lbl_status.set_markup("<span color='red'>Login Failed. Check keys/headers.</span>")
        else:
            self.lbl_status.set_markup(f"<span color='orange'>File not found at {path}</span>")



    def on_manual_login(self, btn):
        buffer = self.text_view.get_buffer()
        start = buffer.get_start_iter()
        end = buffer.get_end_iter()
        text = buffer.get_text(start, end, True)
        
        client = MusicClient()
        success = client.login(text)
        
        if success:
            print("Login Successful")
            self.close()
        else:
            print("Login Failed")
