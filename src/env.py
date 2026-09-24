r"""Environment detection and path resolution.

When running from source, all data lives next to the project tree
(config/, data/, logs/).  A Nuitka-compiled exe keeps its writable state
in a machine-wide location instead, resolved in this order:

  1. ``%LPM_HOME%`` when set - explicit per-machine override
  2. ``%ProgramData%``\ ``com.logy.lpm`` otherwise

Both are machine-scoped: the same path for every Windows account, present
without a loaded user profile, and identical between the interactive CLI and
a headless Task Scheduler run.  Per-user locations like ``%APPDATA%`` are
deliberately not used - a task registered by one account would otherwise
read a different config, database and log tree than the one an operator
edits interactively.

The read-only shipped assets (schema + example config) live next to the exe
instead - see bundled_config_dir().
"""

import ctypes
import os
import sys
from typing import Any

# ── frozen detection ───────────────────────────────────────────────
# Evaluated lazily via __getattr__ because Nuitka may set sys.frozen
# *after* this module is first imported during bootstrap.

_APP_DIR_NAME = "com.logy.lpm"
# %ProgramData% is a machine-wide variable (unlike the per-user %APPDATA%
# a headless task must not rely on), so it is present in every logon
# context.  The literal is the fallback for a task launched without it.
_DEFAULT_PROGRAMDATA = r"C:\ProgramData"


def _is_frozen() -> bool:
    # Nuitka does not set sys.frozen (unlike PyInstaller).  The
    # __compiled__ attribute is set on every compiled module.
    return getattr(sys.modules[__name__], "__compiled__", None) is not None


def _lpm_home_dir() -> str | None:
    r"""Return the %LPM_HOME% override directory, or None when unset/empty.

    The override *is* the data root: ``config/``, ``data/`` and ``logs/``
    live directly inside it.  Created if missing.
    """
    home = os.environ.get("LPM_HOME", "").strip()
    if not home:
        return None
    os.makedirs(home, exist_ok=True)
    return home


def _programdata_dir() -> str:
    r"""Return %ProgramData%\com.logy.lpm, creating it if needed."""
    base = os.environ.get("ProgramData") or _DEFAULT_PROGRAMDATA  # noqa: SIM112 - Windows spelling
    d = os.path.join(base, _APP_DIR_NAME)
    os.makedirs(d, exist_ok=True)
    return d


def _data_root() -> str:
    # Dev mode always stays in the repo tree; LPM_HOME only redirects an
    # installed build (a stray variable must not relocate the test suite).
    if not _is_frozen():
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    override = _lpm_home_dir()
    if override is not None:
        return override
    return _programdata_dir()


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


# ── running executable ─────────────────────────────────────────────


def _module_filename() -> str:
    """Path of this process's image, straight from the Windows loader."""
    buf = ctypes.create_unicode_buffer(32768)  # room for the extended path limit
    n = ctypes.windll.kernel32.GetModuleFileNameW(None, buf, len(buf))
    return buf.value[:n] if 0 < n < len(buf) else ""  # n == len(buf) means truncated


def exe_path() -> str:
    r"""Absolute path of the program running this process.

    Frozen (Nuitka standalone): ``sys.executable`` must not be used - the
    embedded interpreter bootstrap reports ``<install dir>\python.exe``
    there, a file that is never shipped (verified with Nuitka 4.2.1 on
    CPython 3.14: a binary named ``probe.exe`` reports
    ``...\probe.dist\python.exe``, while ``sys.argv[0]`` and
    ``sys._base_executable`` both name ``probe.exe``).  Pinning that
    phantom path - e.g. into a scheduled task's ``<Command>`` - registers
    a program that does not exist.  The Windows loader always knows the
    real image, so ask it.

    Dev mode: the interpreter running the app (``sys.executable``), an
    existing file; tests rely on this identity.
    """
    if _is_frozen():
        image = _module_filename()
        if image:
            return image
    return os.path.abspath(sys.executable)


# ── shipped config assets ──────────────────────────────────────────


def bundled_config_dir() -> str:
    r"""Directory of the config files shipped with the app.

    Dev mode: the repo ``config/`` directory. Frozen (Nuitka standalone)
    build: the ``config/`` folder next to lpm.exe, where build.py vendors
    config.json.schema + config.example.json via --include-data-files.
    ``config_dir()`` stays the writable destination (the data root when
    frozen).
    """
    if _is_frozen():
        # dirname(sys.executable) would *happen* to land in the install dir
        # (the phantom python.exe sits next to lpm.exe) but only rides on
        # that lie - take the directory from the real image instead.
        return os.path.join(os.path.dirname(exe_path()), "config")
    return os.path.join(_data_root(), "config")
