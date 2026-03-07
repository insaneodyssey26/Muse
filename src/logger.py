import builtins
import os
import json
from gi.repository import GLib

_original_print = builtins.print
_debug_enabled = False


def _get_config_path():
    data_dir = os.path.join(GLib.get_user_data_dir(), "muse")
    return os.path.join(data_dir, "config.json")


def _custom_print(*args, **kwargs):
    if _debug_enabled:
        _original_print(*args, **kwargs)


def setup_logging():
    global _debug_enabled
    config_path = _get_config_path()

    # Load config if exists
    if os.path.exists(config_path):
        try:
            with open(config_path, "r") as f:
                config = json.load(f)
                _debug_enabled = config.get("debug_logs", False)
        except Exception:
            pass

    # Override builtins.print
    builtins.print = _custom_print


def set_debug_logs(enabled):
    global _debug_enabled
    _debug_enabled = enabled

    config_path = _get_config_path()
    config = {}

    # Load existing config to not overwrite other settings if they exist
    if os.path.exists(config_path):
        try:
            with open(config_path, "r") as f:
                config = json.load(f)
        except Exception:
            pass

    config["debug_logs"] = enabled

    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    try:
        with open(config_path, "w") as f:
            json.dump(config, f)
    except Exception as e:
        _original_print(f"Failed to save settings: {e}")


def get_debug_logs():
    return _debug_enabled
