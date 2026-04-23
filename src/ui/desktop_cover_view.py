from gi.repository import Gtk, Adw, GObject

from ui.utils import AsyncPicture


class DesktopCoverView(Adw.Bin):
    """Full-window "cover art" view for desktop. Intentionally minimal:
    the queue lives in the right-side OverlaySplitView sidebar, and
    every transport control / like button / title & artist label lives
    in the persistent player bar — so this view is just a big cover.

    Built on Adw.ToolbarView so the page has an opaque background
    without hand-rolled CSS. A plain Gtk.Box with a ``background-color``
    rule was rendering transparently during the OVER_UP slide,
    revealing the browser content behind it.
    """

    __gsignals__ = {
        "dismiss": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, player):
        super().__init__()
        self.player = player

        toolbar = Adw.ToolbarView()
        toolbar.set_hexpand(True)
        toolbar.set_vexpand(True)
        self.set_child(toolbar)

        # Cover art. AspectFrame + obey_child=False keeps it square
        # regardless of the window's aspect ratio.
        self.cover_img = AsyncPicture(crop_to_square=True, player=self.player)
        self.cover_img.add_css_class("rounded")
        self.cover_img.set_content_fit(Gtk.ContentFit.COVER)
        self.cover_img.set_hexpand(True)
        self.cover_img.set_vexpand(True)

        cover_frame = Gtk.AspectFrame(ratio=1.0, obey_child=False)
        cover_frame.set_halign(Gtk.Align.CENTER)
        cover_frame.set_valign(Gtk.Align.CENTER)
        cover_frame.set_vexpand(True)
        cover_frame.set_hexpand(True)
        cover_frame.set_overflow(Gtk.Overflow.HIDDEN)
        cover_frame.set_child(self.cover_img)
        # Breathing room around the cover: 48px all around plus an
        # Adw.Clamp so the cover doesn't stretch past a comfortable
        # reading size on wide monitors.
        cover_frame.set_margin_top(48)
        cover_frame.set_margin_bottom(48)
        cover_frame.set_margin_start(48)
        cover_frame.set_margin_end(48)

        clamp = Adw.Clamp()
        clamp.set_maximum_size(500)
        clamp.set_child(cover_frame)

        toolbar.set_content(clamp)

        # Keep the cover in sync with the currently-playing track.
        self.player.connect("metadata-changed", self._on_metadata_changed)

    def _on_metadata_changed(self, player, title, artist, thumb_url,
                             video_id, like_status):
        if thumb_url:
            self.cover_img.video_id = video_id
            self.cover_img.load_url(thumb_url)
        else:
            self.cover_img.set_paintable(None)
