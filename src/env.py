r"""Environment detection and path resolution.

When running from source, all data lives next to the project tree
(config/, data/, logs/).  When running as a Nuitka-compiled exe the
app switches to %APPDATA%\com.logy.lpm\ so installs are self-contained.
"""

import os
import sys
from typing import Any

# ── frozen detection ───────────────────────────────────────────────
# Evaluated lazily via __getattr__ because Nuitka may set sys.frozen
# *after* this module is first imported during bootstrap.

_APPDATA_NAME = "com.logy.lpm"


def _is_frozen() -> bool:
    # Nuitka does not set sys.frozen (unlike PyInstaller).  The
    # __compiled__ attribute is set on every compiled module.
    return getattr(sys.modules[__name__], "__compiled__", None) is not None


def _appdata_dir() -> str:
    """Return %APPDATA%\\com.logy.lpm, creating it if needed."""
    base = os.environ.get("APPDATA") or os.path.expanduser("~\\AppData\\Roaming")
    d = os.path.join(base, _APPDATA_NAME)
    os.makedirs(d, exist_ok=True)
    return d


def _data_root() -> str:
    if _is_frozen():
        return _appdata_dir()
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def __getattr__(name: str) -> Any:
    if name == "FROZEN":
        return _is_frozen()
    if name == "DATA_ROOT":
        return _data_root()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def config_dir() -> str:
    return os.path.join(_data_root(), "config")


def data_dir() -> str:
    return os.path.join(_data_root(), "data")


def logs_dir() -> str:
    return os.path.join(_data_root(), "logs")


def backups_dir() -> str:
    d = os.path.join(data_dir(), "backups")
    os.makedirs(d, exist_ok=True)
    return d


# ── shipped config assets ──────────────────────────────────────────


def bundled_config_dir() -> str:
    """Directory of the config files shipped with the app.

    Dev mode: the repo ``config/`` directory. Frozen (Nuitka standalone)
    build: the ``config/`` folder next to lpm.exe, where build.py vendors
    config.json.schema + config.example.json via --include-data-files.
    ``config_dir()`` stays the writable destination (%APPDATA% when frozen).
    """
    if _is_frozen():
        return os.path.join(os.path.dirname(sys.executable), "config")
    return os.path.join(_data_root(), "config")
