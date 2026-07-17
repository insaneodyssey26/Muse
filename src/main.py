import sys
import os

# ── glibc malloc arena cap (Linux only) ───────────────────────────────────
# GStreamer decodes audio on short-lived streaming threads, and glibc gives
# each thread its own malloc "arena" (default cap: 8 × CPU cores). Those
# arenas grow to peak usage but never shrink, so every track change stranded
# ~tens of MB of freed-but-retained decode buffers in a fresh arena — RSS
# climbed ~100 MB per skip and never came back (malloc_trim can't reach
# per-thread arenas). Capping arenas at 2 forces threads to share and reuse
# freed space, which flattens the growth to a few MB.
#
# glibc reads this only at heap init, so it MUST be in the environment before
# the process touches the heap. Setting os.environ here would be too late, so
# we re-exec once with the variable applied. This also covers frozen/AppImage
# builds where start.sh's environment never runs. The guard (var-not-present)
# makes the re-exec fire at most once; the child inherits the var and skips it.
if sys.platform.startswith("linux") and "MALLOC_ARENA_MAX" not in os.environ:
    os.environ["MALLOC_ARENA_MAX"] = "2"
    if os.environ.get("MUSE_NO_ARENA_REEXEC") != "1":
        try:
            # Decide how to re-exec by looking at what is ACTUALLY running us,
            # not at build-system markers (sys.frozen is PyInstaller-only;
            # Nuitka's __compiled__ isn't reliably visible in globals() across
            # build modes). /proc/self/exe is the real executable regardless of
            # how we were launched (PATH name, or a symlink like
            # /usr/bin/mixtapes):
            #   • an interpreter (python…): argv[0] is our *script*, so re-exec
            #     sys.executable (preserves a virtualenv) with the full argv.
            #   • our own binary (Nuitka/PyInstaller/cx_Freeze): argv[0] is the
            #     program itself, so re-exec that binary and drop argv[0].
            # Re-execing sys.executable in the compiled case would be fatal:
            # Nuitka points it at the system Python, which would try to run our
            # ELF as a .py and die on "source code cannot contain null bytes".
            _exe = os.path.realpath("/proc/self/exe")
            if os.path.basename(_exe).lower().startswith("python"):
                os.execv(sys.executable, [sys.executable] + sys.argv)
            else:
                os.execv(_exe, [_exe] + sys.argv[1:])
        except Exception as _e:
            # Re-exec failed (no exec perms, odd launcher) — carry on. The var
            # is still set for threads spawned later, which helps partially.
            print(f"[MALLOC] arena re-exec skipped: {_e}")

# Raise the open-file (FD) soft limit to maybe resolve the issue when things eventually stop loading
if not sys.platform.startswith("win"):
    try:
        import resource
        _soft, _hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        _target = min(_hard, 65536) if _hard != resource.RLIM_INFINITY else 65536
        if _soft < _target:
            resource.setrlimit(resource.RLIMIT_NOFILE, (_target, _hard))
            print(f"[FD] raised open-file soft limit {_soft} -> {_target}")
    except Exception as _e:
        print(f"[FD] could not raise open-file limit: {_e}")

# On Windows, set AppUserModelID so the taskbar shows our icon, not Python's
if sys.platform == "win32":
    try:
        import ctypes
        _SetAppID = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        _SetAppID.argtypes = [ctypes.c_wchar_p]
        _SetAppID.restype = ctypes.HRESULT
        _SetAppID("com.pocoguy.Muse")
    except Exception:
        pass

# On Windows, install bundled font per-user so fontconfig can find it
if sys.platform == "win32":
    _base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _fonts_dir = os.path.join(_base, "fonts")
    if os.path.isdir(_fonts_dir):
        # Copy to Windows per-user fonts dir (fontconfig scans this)
        _win_fonts = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts")
        if _win_fonts and os.path.isdir(os.path.dirname(_win_fonts)):
            os.makedirs(_win_fonts, exist_ok=True)
            for _f in os.listdir(_fonts_dir):
                if _f.endswith((".ttf", ".otf")):
                    _src = os.path.join(_fonts_dir, _f)
                    _dst = os.path.join(_win_fonts, _f)
                    if not os.path.exists(_dst):
                        try:
                            import shutil
                            shutil.copy2(_src, _dst)
                            print(f"Installed font: {_f}")
                        except Exception as _e:
                            print(f"Could not install font {_f}: {_e}")

# Force Pango's FontConfig/FreeType backend on Windows so text is laid out
# and rasterized the same way as the Linux build (slight hinting, grayscale
# AA, GNOME-style metrics) — not the OS-default `win32` Pango backend which
# follows Windows' DirectWrite/ClearType pipeline and gives a visibly
# different feel.
#
# Must run BEFORE `import gi`: Pango reads PANGOCAIRO_BACKEND once, when its
# default font map is created at module-init time. Setting it after gi is
# already loaded is a no-op.
#
# Falls back silently to win32 if the GTK build doesn't include the fc
# backend (older bundles), so this is safe to set unconditionally on
# Windows. Respect an existing override if the user has already set the
# env var themselves — useful for A/B testing.
if sys.platform == "win32" and not os.environ.get("PANGOCAIRO_BACKEND"):
    os.environ["PANGOCAIRO_BACKEND"] = "fc"

# Point FontConfig at our bundled windows/fonts.conf. Same timing
# constraint as PANGOCAIRO_BACKEND — FontConfig reads FONTCONFIG_FILE
# at the first FcInit() call, which happens deep inside Pango's import
# path. Without this, FontConfig on Windows falls back to a minimal
# built-in config that doesn't know about WINDOWSFONTDIR or the per-user
# fonts dir where we just installed Adwaita Sans, and text renders with
# whatever happens to be hardcoded as a last-resort fallback.
if sys.platform == "win32" and not os.environ.get("FONTCONFIG_FILE"):
    _proj_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _fc_path = os.path.join(_proj_root, "windows", "fonts.conf")
    if os.path.isfile(_fc_path):
        os.environ["FONTCONFIG_FILE"] = _fc_path

# Apply the user-chosen GSK renderer *before* GTK loads. Some NVIDIA driver
# versions crash inside the default renderer (libnvidia-glcore + gsk_renderer_render);
# users can override via Preferences → Application.
def _apply_gsk_renderer_pref():
    if os.environ.get("GSK_RENDERER"):
        return  # explicit env var wins
    try:
        import json
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        prefs_path = os.path.join(base, "muse", "prefs.json")
        if not os.path.exists(prefs_path):
            return
        with open(prefs_path) as f:
            value = json.load(f).get("gsk_renderer", "default")
        if value and value != "default":
            os.environ["GSK_RENDERER"] = value
    except Exception:
        pass

_apply_gsk_renderer_pref()

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, Gio, Gdk
from ui.window import MainWindow
import logger

logger.setup_logging()


class MusicApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id="com.pocoguy.Muse", flags=Gio.ApplicationFlags.FLAGS_NONE
        )

        # Load GResource
        try:
            resource_path = os.path.join(os.path.dirname(__file__), "muse.gresource")
            resource = Gio.Resource.load(resource_path)
            resource._register()

            # Add icon resource path
            Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_resource_path("/com/pocoguy/muse/icons")
        except Exception as e:
            print(f"Failed to load GResource: {e}")

    def do_startup(self):
        Adw.Application.do_startup(self)

        # Prepend project icons to theme search path (for running from source)
        # Must be first so it takes priority over system-installed Flatpak icons
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        assets_icons = os.path.join(project_root, "assets", "icons")
        if os.path.isdir(assets_icons):
            theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
            theme.set_search_path([assets_icons] + theme.get_search_path())

        Gtk.Window.set_default_icon_name("com.pocoguy.Muse")

    def do_activate(self):
        # Load CSS
        css_provider = Gtk.CssProvider()
        css_path = os.path.join(os.path.dirname(__file__), "ui", "style.css")
        css_provider.load_from_path(css_path)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        # On Windows, use Adwaita Sans if installed. We also explicitly
        # set the global font name (including size) — Windows' GTK default
        # is a smaller `Segoe UI 9` and the previous CSS rule only overrode
        # the family, so Adwaita Sans rendered at Windows' 9pt instead of
        # the 11pt the Linux/GNOME stack uses by default. That made the
        # whole UI feel ~80% the size of the Linux build at 100% scaling.
        if sys.platform == "win32":
            settings = Gtk.Settings.get_default()
            if settings is not None:
                settings.set_property("gtk-font-name", "Adwaita Sans 11")
                # Match GNOME's text-rendering defaults so the look is
                # closer to the Linux build: slight hinting (not full),
                # grayscale antialias (not ClearType subpixel), 96 dpi.
                # These map onto Cairo font options when GTK4 paints text,
                # so they apply on Windows too as long as the GTK build's
                # Cairo backend honors the antialias / hint options
                # (the gvsbuild-style packaging does).
                settings.set_property("gtk-xft-antialias", 1)
                settings.set_property("gtk-xft-hinting", 1)
                settings.set_property("gtk-xft-hintstyle", "hintslight")
                settings.set_property("gtk-xft-rgba", "none")
                # 96 dpi * 1024 — GTK stores xft-dpi in 1024ths of a pt.
                settings.set_property("gtk-xft-dpi", 98304)
            font_css = Gtk.CssProvider()
            font_css.load_from_string(
                "* { font-family: 'Adwaita Sans Text', 'Adwaita Sans', 'Segoe UI', sans-serif; }"
            )
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(),
                font_css,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1,
            )

        win = self.props.active_window
        if not win:
            win = MainWindow(application=self)
        win.present()


def main():
    app = MusicApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
