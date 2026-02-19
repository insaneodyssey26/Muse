from gi.repository import Gtk, Adw

class HomePage(Adw.Bin):
    def __init__(self, player, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.player = player
        
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        
        clamp = Adw.Clamp()
        
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(24)
        box.set_margin_bottom(24)
        box.set_margin_start(12)
        box.set_margin_end(12)
        
        self.status = Adw.StatusPage(
            icon_name="user-home-symbolic",
            title="Home",
            description="Your music feed will appear here."
        )
        box.append(self.status)
        
        test_btn = Gtk.Button(label="Play Test (Billy Idol)")
        test_btn.set_halign(Gtk.Align.CENTER)
        test_btn.connect("clicked", self.on_test_play)
        # box.append(test_btn)
        
        clamp.set_child(box)
        scroll.set_child(clamp)
        
        self.set_child(scroll)

    def on_test_play(self, btn):
        if self.player:
            # Video ID for Billy Idol
            self.player.load_video("x6R8HY6y2s0")
