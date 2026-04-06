"""
Standalone YouTube Music login helper for Windows.
Uses Edge WebView2 via pywebview to capture auth cookies,
then writes them to a JSON file for Mixtapes to import.

Usage:
  login_helper.exe [--output PATH]

Writes captured headers JSON to:
  --output PATH   (default: %LOCALAPPDATA%/Mixtapes/login_headers.json)
"""

import json
import os
import sys
import webview


OUTPUT_PATH = None


def get_default_output():
    appdata = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
    d = os.path.join(appdata, "Mixtapes")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "login_headers.json")


class LoginCapture:
    def __init__(self, window):
        self.window = window
        self.finished = False

    def on_loaded(self):
        if self.finished:
            return

        url = self.window.get_current_url() or ""

        if "music.youtube.com" in url and "accounts.google.com" not in url:
            cookies = self.window.evaluate_js("document.cookie")
            if cookies and ("SAPISID" in cookies or "__Secure-3PAPISID" in cookies):
                self.finished = True
                headers = {
                    "Cookie": cookies,
                    "User-Agent": self.window.evaluate_js("navigator.userAgent"),
                }

                output = OUTPUT_PATH or get_default_output()
                with open(output, "w") as f:
                    json.dump(headers, f)

                print(f"Login successful! Headers saved to: {output}")
                self.window.destroy()


def main():
    global OUTPUT_PATH

    # Parse --output arg
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--output" and i + 1 < len(args):
            OUTPUT_PATH = args[i + 1]

    window = webview.create_window(
        "Mixtapes - Login to YouTube Music",
        "https://accounts.google.com/ServiceLogin?ltmpl=music&service=youtube"
        "&uilel=3&passive=true"
        "&continue=https%3A%2F%2Fmusic.youtube.com%2Flibrary",
        width=700,
        height=600,
    )

    capture = LoginCapture(window)
    window.events.loaded += capture.on_loaded
    webview.start(private_mode=False)

    output = OUTPUT_PATH or get_default_output()
    if not os.path.exists(output):
        print("Login was cancelled or failed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
