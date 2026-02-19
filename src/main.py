import sys
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, Gio, Gdk
from ui.window import MainWindow
import os

class MusicApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id='com.pocoguy.Muse',
                         flags=Gio.ApplicationFlags.FLAGS_NONE)
        
        # Load GResource
        try:
            resource_path = os.path.join(os.path.dirname(__file__), "muse.gresource")
            resource = Gio.Resource.load(resource_path)
            resource._register()
        except Exception as e:
            print(f"Failed to load GResource: {e}")

    def do_activate(self):
        # Load CSS
        css_provider = Gtk.CssProvider()
        css_path = os.path.join(os.path.dirname(__file__), 'ui', 'style.css')
        css_provider.load_from_path(css_path)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css_provider, 
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        win = self.props.active_window
        if not win:
            win = MainWindow(application=self)
        win.present()

def main():
    app = MusicApp()
    return app.run(sys.argv)

if __name__ == '__main__':
    sys.exit(main())
