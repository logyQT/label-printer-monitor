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


# ── embedded assets ────────────────────────────────────────────────

EMBEDDED_CONFIG_EXAMPLE: str = """\
{
  "$schema": "./config.json.schema",
  "db": {
    "filename": "printer_stats.db"
  },
  "log_dir": "logs",
  "snmp": {
    "community": "public",
    "timeout_sec": 3,
    "retries": 2
  },
  "collection": {
    "max_concurrency": 20
  },
  "printers": [
    { "ip": "10.0.1.10", "model": "Sato CL4NX Plus", "location": "Linia 1" },
    { "ip": "10.0.1.11", "model": "Zebra ZT411", "location": "Linia 2" },
    { "ip": "10.0.1.12", "model": "Zebra GX430t", "location": "Linia 3" }
  ]
}
"""

EMBEDDED_SCHEMA: str = """\
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "Printer Statistics Configuration",
  "description": "Configuration for the printer statistics collection system",
  "type": "object",
  "required": ["db", "log_dir", "snmp", "printers"],
  "properties": {
    "$schema": {
      "type": "string",
      "description": "Link to this schema, used by editors and tools for validation"
    },
    "db": {
      "type": "object",
      "description": "Database settings",
      "required": ["filename"],
      "properties": {
        "filename": {
          "type": "string",
          "description": "SQLite database filename (stored in data/ directory)"
        }
      }
    },
    "log_dir": {
      "type": "string",
      "description": "Directory for log files"
    },
    "snmp": {
      "type": "object",
      "description": "SNMP connection settings",
      "required": ["community", "timeout_sec", "retries"],
      "properties": {
        "community": {
          "type": "string",
          "description": "SNMP v1/v2c community string",
          "default": "public"
        },
        "timeout_sec": {
          "type": "integer",
          "description": "Socket timeout in seconds",
          "minimum": 1,
          "maximum": 30,
          "default": 3
        },
        "retries": {
          "type": "integer",
          "description": "Number of retry attempts per query",
          "minimum": 0,
          "maximum": 10,
          "default": 2
        }
      }
    },
    "collection": {
      "type": "object",
      "description": "Collection run settings",
      "properties": {
        "max_concurrency": {
          "type": "integer",
          "description": "Maximum number of printers collected in parallel",
          "minimum": 1,
          "default": 20
        }
      }
    },
    "printers": {
      "type": "array",
      "description": "List of printers to monitor",
      "items": {
        "type": "object",
        "required": ["ip", "model", "location"],
        "properties": {
          "ip": {
            "type": "string",
            "description": "Printer IP address",
            "pattern": "^(\\\\d{1,3}\\\\.){3}\\\\d{1,3}$"
          },
          "model": {
            "type": "string",
            "description": "Printer model (used to select adapter)",
            "enum": [
              "Zebra ZT230",
              "Zebra ZT411",
              "Zebra GX430t",
              "Zebra ZD621",
              "Sato CL4NX Plus"
            ]
          },
          "location": {
            "type": "string",
            "description": "Physical location of the printer"
          }
        }
      }
    }
  },
  "additionalProperties": false
}
"""
