"""Printer statistics collection & reporting entry point.

Usage:
    python main.py --init                   # create config from the example
    python main.py --collect                # collect from all printers
    python main.py --collect --verbose      # with SNMP debug output
    python main.py --collect --config path/to/config.json  # alternate config
    python main.py --report                 # weekly report (current week)
    python main.py --report --from 2026-09-01 --to 2026-09-30
    python main.py --report --csv           # export CSV
    python main.py --validate               # check that everything is set up
    python main.py --validate --network     # also check SNMP reachability
    python main.py                          # show this help message
"""

import argparse
import concurrent.futures
import contextlib
import json
import logging
import os
import shutil
import sys
from datetime import datetime
from typing import Any

# When running from source, put the project root on sys.path so that
# ``import src.db`` etc. resolve correctly.  In a frozen Nuitka build
# the ``src`` package is compiled into the binary and sys.path is not
# needed.
_HERE: str = os.path.dirname(os.path.abspath(__file__))
# In dev mode src/ is a sibling of main.py; in a Nuitka onefile build
# __file__ points at the temp extraction dir where src/ doesn't exist.
if os.path.isdir(os.path.join(_HERE, "src")):
    sys.path.insert(0, _HERE)

from src import db  # noqa: E402
from src.adapters import create_adapter  # noqa: E402
from src.adapters.base import CounterResult, PrinterAdapter  # noqa: E402
from src.env import (  # noqa: E402
    DATA_ROOT,
    EMBEDDED_CONFIG_EXAMPLE,
    EMBEDDED_SCHEMA,
    FROZEN,
    backups_dir,
    config_dir,
    data_dir,
    logs_dir,
)

log: logging.Logger = logging.getLogger("printer_stats")

# Runtime-validated against config.json.schema; see validate.py.
type Config = dict[str, Any]


# ── path helpers ─────────────────────────────────────────────────────


def _project_root() -> str:
    r"""Return the base directory for relative path resolution.

    In dev mode this is the repo root (same as _HERE).
    In frozen/exe mode this is %APPDATA%\com.logy.lpm.
    All config-relative paths (log_dir, db filename, etc.) resolve
    against this.
    """
    return DATA_ROOT


def _config_path() -> str:
    return os.path.join(config_dir(), "config.json")


def _schema_path() -> str:
    """Path to config.json.schema inside the config directory."""
    return os.path.join(config_dir(), "config.json.schema")


def _db_path(config: Config) -> str:
    """Resolve the DB path from config['db']['filename'] -> data/<filename>.

    ':memory:' is passed through as-is for in-memory SQLite databases.
    """
    filename = config.get("db", {}).get("filename", "printer_stats.db")
    return filename if filename == ":memory:" else os.path.join(data_dir(), filename)


# ── helpers ──────────────────────────────────────────────────────────


def load_config(path: str | None = None) -> Config:
    path = path or _config_path()
    if not os.path.exists(path):
        print(f"ERROR: Config file not found: {path}", file=sys.stderr)
        sys.exit(2)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)  # type: ignore[no-any-return]
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON in config: {e}", file=sys.stderr)
        sys.exit(2)


def backup_data(config_path: str | None = None) -> str:
    """Snapshot config + db into data/backups/<timestamp>/ before a run."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(backups_dir(), ts)
    os.makedirs(dest, exist_ok=True)

    # Backup config
    src = config_path or _config_path()
    if os.path.exists(src):
        shutil.copy2(src, os.path.join(dest, "config.json"))

    # Backup schema
    schema = _schema_path()
    if os.path.exists(schema):
        shutil.copy2(schema, os.path.join(dest, "config.json.schema"))

    return dest


def setup_logging(log_dir: str, verbose: bool = False) -> str:
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    now = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(log_dir, f"run_{now}.log")

    level = logging.DEBUG if verbose else logging.INFO
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.WARNING)
    for h in root.handlers[:]:
        root.removeHandler(h)
    root.addHandler(logging.NullHandler())

    app_logger = logging.getLogger("printer_stats")
    app_logger.setLevel(logging.DEBUG)
    app_logger.propagate = False
    app_logger.addHandler(file_handler)
    app_logger.addHandler(console_handler)
    return log_file


# ── init ──────────────────────────────────────────────────────────────


def _handle_init() -> None:
    """Bootstrap the project: create config from the example + runtime dirs."""
    from src.validate import find_schema_violation, resolve_schema_path

    c_dir = config_dir()
    target = os.path.join(c_dir, "config.json")

    if os.path.exists(target):
        print(f"Config already exists: {target}")
        print("Leaving it untouched. Edit it to match your printers.")
        return

    # Ensure the config directory exists
    os.makedirs(c_dir, exist_ok=True)

    if FROZEN:
        # Frozen exe: write config.example.json + schema from baked-in strings
        example_path = os.path.join(c_dir, "config.example.json")
        with open(example_path, "w", encoding="utf-8") as f:
            f.write(EMBEDDED_CONFIG_EXAMPLE)
        schema_path = os.path.join(c_dir, "config.json.schema")
        with open(schema_path, "w", encoding="utf-8") as f:
            f.write(EMBEDDED_SCHEMA)
        print(f"Wrote embedded config example to {example_path}")
    else:
        example_path = os.path.join(_HERE, "config", "config.example.json")
        if not os.path.exists(example_path):
            print(f"ERROR: Example config not found: {example_path}", file=sys.stderr)
            sys.exit(2)
        # Also copy schema into config_dir so validation can find it
        src_schema = os.path.join(_HERE, "config", "config.json.schema")
        dst_schema = os.path.join(c_dir, "config.json.schema")
        if os.path.exists(src_schema) and not os.path.exists(dst_schema):
            shutil.copy2(src_schema, dst_schema)

    shutil.copy2(example_path, target)

    # Runtime dirs are needed before db/collection work
    for d in (data_dir(), logs_dir(), backups_dir()):
        os.makedirs(d, exist_ok=True)

    print(f"Created {target}")
    print(f"Created runtime directories: {data_dir()}/, {backups_dir()}/, {logs_dir()}/")

    # The copied config carries a $schema link - confirm it validates
    schema_path = resolve_schema_path(target, _schema_path())
    try:
        with open(target, encoding="utf-8") as f:
            cfg = json.load(f)
        with open(schema_path, encoding="utf-8") as f:
            schema = json.load(f)
        violation = find_schema_violation(cfg, schema)
        if violation:
            print(f"WARNING: copied config does not match schema: {violation.message}")
        else:
            print(f"Config validates against {os.path.basename(schema_path)}")
    except Exception as e:
        print(f"WARNING: could not validate copied config: {e}")

    if FROZEN:
        print(f"\nEdit {target} with your printers, then run:")
        print("  lpm --validate  # check everything is set up")
        print("  lpm --collect")
    else:
        print("\nEdit config/config.json with your printers, then run:")
        print("  python main.py --validate  # check everything is set up")
        print("  python main.py --collect")


# ── collect ──────────────────────────────────────────────────────────


def collect_printer(
    adapter: PrinterAdapter, printer_cfg: dict[str, Any]
) -> CounterResult | None:
    try:
        counters = adapter.get_counters()
        if not counters.get("reachable"):
            log.warning(f"Printer {printer_cfg['ip']} ({printer_cfg['location']}) not reachable")
            return None
        return counters
    except Exception as e:
        log.error(f"Error collecting from {printer_cfg['ip']}: {e}")
        return None


def _collect_one(
    printer_cfg: dict[str, Any], community: str, timeout: int, retries: int
) -> CounterResult | None:
    """Collect counters from a single printer (runs in a worker thread).

    Returns a counters dict, or None when the printer is unreachable or the
    query fails. Raises on config errors (e.g. unknown model) so the caller
    can count them as failures.
    """
    adapter = create_adapter(
        printer_cfg["model"],
        printer_cfg["ip"],
        community=community,
        timeout_sec=timeout,
        retries=retries,
    )
    return collect_printer(adapter, printer_cfg)


def run_collection(config: Config) -> tuple[int, int, int]:
    db_path = _db_path(config)
    conn = db.init_db(db_path)
    snmp_config = config.get("snmp", {})
    community = snmp_config.get("community", "public")
    timeout = snmp_config.get("timeout_sec", 3)
    retries = snmp_config.get("retries", 2)
    max_concurrency = max(1, int(config.get("collection", {}).get("max_concurrency", 20)))

    success = 0
    fail = 0
    total = len(config["printers"])

    log.info(f"Starting collection for {total} printers (max concurrency: {max_concurrency})")

    # Collect all printers in parallel so a dead printer's timeout doesn't
    # stall the rest. DB writes stay on the main thread because sqlite3
    # connections are not safe to share across threads.
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_concurrency) as executor:
        futures: dict[concurrent.futures.Future[CounterResult | None], dict[str, Any]] = {
            executor.submit(_collect_one, printer_cfg, community, timeout, retries): printer_cfg
            for printer_cfg in config["printers"]
        }
        for future in concurrent.futures.as_completed(futures):
            printer_cfg = futures[future]
            ip = printer_cfg["ip"]
            model = printer_cfg["model"]
            location = printer_cfg["location"]

            try:
                counters = future.result()
            except ValueError as e:
                log.error(f"Config error for {ip}: {e}")
                fail += 1
                continue
            except Exception as e:
                log.error(f"Unexpected error for {ip}: {e}")
                fail += 1
                continue

            if counters is None:
                fail += 1
                continue

            db.save_snapshot(
                conn,
                printer_ip=ip,
                labels_total=counters.get("labels_total"),
                meters_total=counters.get("meters_total"),
                meter_unit="m",
                model_name=counters.get("model_name", ""),
            )
            success += 1
            labels = counters.get("labels_total")
            meters = counters.get("meters_total")
            labels_str = f"{labels:,}" if labels is not None else "n/a"
            meters_str = f"{meters:,.1f} m" if meters is not None else "n/a"
            log.info(f"{model} ({ip}) [{location}] - labels: {labels_str}, odometer: {meters_str}")

    db.close_db(conn)
    log.info(f"Collection complete: {success}/{total} success, {fail}/{total} failed")
    return success, fail, total


def _handle_collect(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    backup_data(args.config)
    log_dir = os.path.join(_project_root(), config.get("log_dir", "logs"))
    log_file = setup_logging(log_dir, verbose=args.verbose)
    log.info(f"Log file: {log_file}")
    run_collection(config)


# ── validate ──────────────────────────────────────────────────────────


def _handle_validate(args: argparse.Namespace) -> None:
    from src.validate import Issue, validate_network, validate_setup

    config_path = args.config or _config_path()

    issues: list[Issue] = validate_setup(config_path, _project_root())

    if args.network:
        with contextlib.suppress(SystemExit):
            issues += validate_network(load_config(args.config))

    for level, message in issues:
        print(f"[{level}] {message}")

    fails = [i for i in issues if i.level == "FAIL"]
    if fails:
        print(f"\n{len(fails)} problem(s) found. Fix them, then re-run validation.")
        sys.exit(1)
    print("\nSetup looks good.")


# ── report ───────────────────────────────────────────────────────────


def _handle_report(args: argparse.Namespace) -> None:
    from src.report import compute_weekly, export_csv, print_report

    config = load_config(args.config)
    backup_data(args.config)
    from_date = args.from_date or datetime.now().strftime("%Y-%m-%d")
    to_date = args.to_date or datetime.now().strftime("%Y-%m-%d")

    weeks = compute_weekly(config, from_date, to_date, root=_project_root())

    if args.csv:
        path = os.path.join(_project_root(), f"report_{from_date}_to_{to_date}.csv")
        export_csv(weeks, config, path)
    else:
        print_report(weeks, config)


# ── entry point ──────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="lpm" if FROZEN else None,
        description="Printer statistics - collect & report",
        epilog="Run with no flags to see this help message.",
    )

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--init", action="store_true", help="Create config from the example"
    )
    mode.add_argument("--collect", action="store_true", help="Collect statistics from all printers")
    mode.add_argument("--report", action="store_true", help="Generate a weekly statistics report")
    mode.add_argument(
        "--validate", action="store_true", help="Check that the project is set up correctly"
    )

    # Collect-specific flags
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="(collect) Show detailed SNMP debug output"
    )

    # Config selection (all modes)
    parser.add_argument("--config", help="Path to a config JSON file (default: config/config.json)")

    # Report-specific flags
    parser.add_argument("--from", dest="from_date", help="(report) Start date (YYYY-MM-DD)")
    parser.add_argument("--to", dest="to_date", help="(report) End date (YYYY-MM-DD)")
    parser.add_argument("--csv", action="store_true", help="(report) Export report as CSV")

    # Validate-specific flags
    parser.add_argument(
        "--network",
        action="store_true",
        help="(validate) Also check live SNMP reachability of each printer",
    )

    args = parser.parse_args()

    if args.init:
        _handle_init()
    elif args.collect:
        _handle_collect(args)
    elif args.report:
        _handle_report(args)
    elif args.validate:
        _handle_validate(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
