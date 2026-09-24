"""Printer statistics collection & reporting entry point.

Usage (replace ``python main.py`` with ``lpm`` in a frozen build):
    python main.py                          # interactive shell: lpm > prompt
    python main.py shell                    # same, as an explicit subcommand
    python main.py help                     # full help: every command + its options
    python main.py help report              # options for a single command
    python main.py init                     # create config from the example
    python main.py collect                  # collect from all printers
    python main.py collect -v               # with SNMP debug output
    python main.py report                   # weekly report (current week)
    python main.py report --from 2026-09-01 --to 2026-09-30
    python main.py report --csv             # export CSV
    python main.py validate                 # check that everything is set up
    python main.py validate --network       # also check SNMP reachability
    python main.py schedule                 # manage the Windows scheduled task (frozen build only)
    python main.py schedule -r -v           # remove it, narrating each stage

Inside the shell, the same commands run at the ``lpm >`` prompt; ``help``
lists all of them, ``exit`` (or Ctrl+D) leaves.  With non-interactive stdin
(pipes, CI), bare ``lpm`` prints the full help instead of blocking on input.
"""

import argparse
import concurrent.futures
import contextlib
import ctypes
import json
import logging
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta
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
    FROZEN,
    backups_dir,
    bundled_config_dir,
    config_dir,
    data_dir,
    exe_path,
    logs_dir,
)
from src.shell import run_shell  # noqa: E402

log: logging.Logger = logging.getLogger("printer_stats")

# Runtime-validated against config.json.schema; see validate.py.
type Config = dict[str, Any]


# ── path helpers ─────────────────────────────────────────────────────


def _project_root() -> str:
    r"""Return the base directory for relative path resolution.

    In dev mode this is the repo root (same as _HERE).
    In frozen/exe mode this is the machine-wide data root
    (%ProgramData%\com.logy.lpm, or %LPM_HOME% when set).
    All config-relative paths (logs.dir, db filename, etc.) resolve
    against this.
    """
    return DATA_ROOT  # type: ignore[no-any-return]  # env.DATA_ROOT is lazy (module __getattr__ -> Any)


def _config_path() -> str:
    return os.path.join(config_dir(), "config.json")


def _schema_path() -> str:
    """Path to config.json.schema inside the config directory."""
    return os.path.join(config_dir(), "config.json.schema")


def _refresh_schema_copy() -> bool:
    """Copy the shipped schema over the config-dir one when missing or stale.

    The data root keeps a copy written at first `init`; without a refresh,
    schema changes never reach existing installs and `validate` fails with
    "Additional properties are not allowed". Byte-compare so the copy is only
    rewritten when it actually differs. No-op when both paths resolve to the
    same file (dev mode) or the shipped file is unavailable.
    """
    src = os.path.join(bundled_config_dir(), "config.json.schema")
    dst = _schema_path()
    if os.path.abspath(src) == os.path.abspath(dst):
        return False
    try:
        with open(src, "rb") as f:
            source = f.read()
    except OSError:
        return False
    try:
        with open(dst, "rb") as f:
            if f.read() == source:
                return False
    except OSError:
        pass
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "wb") as f:
        f.write(source)
    return True


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


def _prune_backups(keep: int) -> None:
    """Remove oldest backup directories when count exceeds *keep*.

    Backup directory names are timestamped (YYYYMMDD_HHMMSS) so
    a lexicographic sort gives chronological order.
    """
    root = backups_dir()
    entries = sorted(
        (e for e in os.scandir(root) if e.is_dir()),
        key=lambda e: e.name,
    )
    excess = len(entries) - keep
    if excess <= 0:
        return
    for entry in entries[:excess]:
        shutil.rmtree(entry.path)
        log.debug("Pruned old backup: %s", entry.name)


def _prune_logs(log_dir: str, retention_days: int) -> None:
    """Remove run log files older than *retention_days* days.

    Only files matching the ``run_YYYYMMDD_HHMMSS.log`` naming scheme are
    considered; the embedded timestamp gives the age without relying on
    mtime.  Anything else in the log directory is left untouched.
    ``retention_days`` of 0 (or less) disables pruning.
    """
    if retention_days <= 0 or not os.path.isdir(log_dir):
        return
    cutoff = datetime.now() - timedelta(days=retention_days)
    for entry in os.scandir(log_dir):
        name = entry.name
        if not (entry.is_file() and name.startswith("run_") and name.endswith(".log")):
            continue
        try:
            created = datetime.strptime(name[4:-4], "%Y%m%d_%H%M%S")
        except ValueError:
            continue  # not our naming scheme - never delete it
        if created < cutoff:
            os.remove(entry.path)
            log.debug("Pruned old log: %s", name)


def _verify_db(db_path: str) -> None:
    """Run SQLite integrity check; raise if the database is corrupted."""
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            result = conn.execute("PRAGMA integrity_check").fetchone()
            if result[0] != "ok":
                raise SystemExit(f"ERROR: Database integrity check failed: {result[0]}")
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        raise SystemExit(f"ERROR: Cannot open database: {exc}") from exc


def backup_data(config_path: str | None = None, config: Config | None = None) -> str:
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

    # Backup database (verify integrity first)
    cfg = config or {}
    db_file = cfg.get("db", {}).get("filename", "")
    if db_file and db_file != ":memory:":
        db_path = os.path.join(data_dir(), db_file)
        if os.path.exists(db_path):
            _verify_db(db_path)
            shutil.copy2(db_path, os.path.join(dest, db_file))

    # Prune old backups (default keep=50 if not configured)
    keep = cfg.get("backups", {}).get("keep", 50)
    _prune_backups(keep)

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


def _handle_init(args: argparse.Namespace) -> None:
    """Bootstrap the project: create config from the example + runtime dirs."""
    del args  # no init-specific flags (signature shared with the other handlers)
    from src.validate import find_schema_violation, resolve_schema_path

    c_dir = config_dir()
    target = os.path.join(c_dir, "config.json")

    if os.path.exists(target):
        print(f"Config already exists: {target}")
        print("Leaving it untouched. Edit it to match your printers.")
        return

    # Ensure the config directory exists
    os.makedirs(c_dir, exist_ok=True)

    # The shipped config files are the single source of truth: repo config/
    # in dev, the config/ folder vendored next to lpm.exe in builds
    # (build.py --include-data-files). Copy them into place.
    src_dir = bundled_config_dir()
    src_example = os.path.join(src_dir, "config.example.json")
    src_schema = os.path.join(src_dir, "config.json.schema")
    if not (os.path.exists(src_example) and os.path.exists(src_schema)):
        print(f"ERROR: Shipped config files not found in: {src_dir}", file=sys.stderr)
        print(
            "The installation looks incomplete - distribute the whole build folder (see build.py).",
            file=sys.stderr,
        )
        sys.exit(2)

    example_path = os.path.join(c_dir, "config.example.json")
    if os.path.abspath(src_example) != os.path.abspath(example_path):
        shutil.copy2(src_example, example_path)
        print(f"Wrote config example to {example_path}")

    if _refresh_schema_copy():
        print(f"Wrote schema to {_schema_path()}")

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
        print("  lpm validate  # check everything is set up")
        print("  lpm collect")
    else:
        print("\nEdit config/config.json with your printers, then run:")
        print("  python main.py validate  # check everything is set up")
        print("  python main.py collect")


# ── collect ──────────────────────────────────────────────────────────


def collect_printer(adapter: PrinterAdapter, printer_cfg: dict[str, Any]) -> CounterResult | None:
    try:
        counters = adapter.get_counters()
        if not counters.get("reachable"):
            log.warning(f"Printer {printer_cfg['ip']} ({printer_cfg['location']}) not reachable")
            return None
        return counters
    except Exception as e:
        log.error(f"Error collecting from {printer_cfg['ip']}: {e}")
        return None


def _collect_one(printer_cfg: dict[str, Any], community: str, timeout: int, retries: int) -> CounterResult | None:
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


def run_collection(config: Config, dry: bool = False) -> tuple[int, int, int]:
    db_path = _db_path(config)
    conn = db.init_db(db_path) if not dry else None
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

            # conn is None exactly when dry=True (see run_collection), so this
            # guard doubles as the mypy narrowing for save_snapshot.
            if conn is not None:
                db.save_snapshot(
                    conn,
                    printer_ip=ip,
                    labels_total=counters.get("labels_total"),
                    meters_total=counters.get("meters_total"),
                    model_name=counters.get("model_name", ""),
                )
            success += 1
            labels = counters.get("labels_total")
            meters = counters.get("meters_total")
            labels_str = f"{labels:,}" if labels is not None else "n/a"
            meters_str = f"{meters:,.1f} m" if meters is not None else "n/a"
            log.info(f"{model} ({ip}) [{location}] - labels: {labels_str}, odometer: {meters_str}")

    if conn:
        db.close_db(conn)
    log.info(f"Collection complete: {success}/{total} success, {fail}/{total} failed")
    return success, fail, total


def _handle_collect(args: argparse.Namespace) -> None:
    config = load_config()
    if not args.dry:
        backup_data(config=config)
    log_dir = os.path.join(_project_root(), config.get("logs", {}).get("dir", "logs"))
    log_file = setup_logging(log_dir, verbose=args.verbose)
    log.info(f"Log file: {log_file}")
    # Age out old run logs (after setup_logging so prunes are recorded here;
    # the current file is new and never matches the cutoff).
    retention_days = config.get("logs", {}).get("retention_days", 30)
    _prune_logs(log_dir, retention_days)
    if args.dry:
        log.info("DRY RUN - collecting but not saving to database")
    run_collection(config, dry=args.dry)


# ── validate ──────────────────────────────────────────────────────────


def _handle_validate(args: argparse.Namespace) -> None:
    from src.validate import Issue, validate_network, validate_setup

    config_path = _config_path()

    # Replace a stale schema copy in the data root before validating.
    if _refresh_schema_copy():
        print(f"Refreshed stale schema copy: {_schema_path()}")

    issues: list[Issue] = validate_setup(config_path, _project_root())

    from src.schedule.validate import check_schedule

    issues.extend(check_schedule(config_path, _project_root()))

    if args.network:
        with contextlib.suppress(SystemExit):
            issues += validate_network(load_config())

    # Without -v/--verbose, only show warnings and errors.
    if not args.verbose:
        issues = [i for i in issues if i.level != "OK"]

    for level, message in issues:
        print(f"[{level}] {message}")

    fails = [i for i in issues if i.level == "FAIL"]
    if fails:
        print(f"\n{len(fails)} problem(s) found. Fix them, then re-run validation.")
        sys.exit(1)
    print("\nSetup looks good.")


# ── schedule ─────────────────────────────────────────────────────────


def _is_admin() -> bool:
    """True when the process runs with full (UAC-elevated) admin rights."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _handle_schedule(args: argparse.Namespace) -> None:
    """Create, update, or remove the Windows scheduled collection task."""
    verbose: bool = args.verbose
    # Fail fast before any other gate or message: S4U registration is
    # denied outright from an unelevated (UAC-filtered) admin token.
    # (S4U/admin internals stay in this comment - end users just need the ask.)
    if not _is_admin():
        print("ERROR: lpm schedule requires an elevated session (Run as administrator).", file=sys.stderr)
        sys.exit(1)
    if verbose:
        print("Elevated session: ok")

    from src.schedule import install, remove

    if not FROZEN:
        print("ERROR: lpm schedule requires a built version (lpm.exe).", file=sys.stderr)
        print("Build with build.py first, then run lpm schedule as administrator.", file=sys.stderr)
        sys.exit(1)

    # Pin the task to this exact binary, by its own path - not the bare
    # name "lpm": the scheduled-task host resolves a bare command against
    # the environment ITS process was started with, which can predate the
    # PATH entry this very install just added (the Task Scheduler service
    # does not reload the registry environment), and it would pick up any
    # other lpm.exe sitting earlier on that PATH.  sys.executable is no
    # help either: in the frozen build it reports a phantom
    # <dir>\python.exe that is never shipped (see env.exe_path).
    exe = exe_path()
    if verbose:
        print(f"Running from: {exe}")

    config = load_config()
    if "schedule" not in config:
        print("ERROR: No 'schedule' section in config.json.", file=sys.stderr)
        print("Add a schedule section, e.g.:", file=sys.stderr)
        print(
            '  "schedule": { "enabled": true, "times": ["05:00", "15:00"], "weekdays_only": true }',
            file=sys.stderr,
        )
        sys.exit(1)

    if args.remove:
        remove(verbose=verbose)
        return

    if config["schedule"].get("enabled") is False:
        print("Schedule is disabled in config.")
        return

    install(config, exe_path=exe, verbose=verbose)


# ── report ───────────────────────────────────────────────────────────


def _handle_report(args: argparse.Namespace) -> None:
    from src.report import compute_weekly, export_csv, print_report

    config = load_config()
    backup_data(config=config)
    from_date = args.from_date or datetime.now().strftime("%Y-%m-%d")
    to_date = args.to_date or datetime.now().strftime("%Y-%m-%d")

    weeks = compute_weekly(config, from_date, to_date, root=_project_root())

    if args.csv:
        path = os.path.join(_project_root(), f"report_{from_date}_to_{to_date}.csv")
        export_csv(weeks, config, path)
    else:
        print_report(weeks, config)


# ── entry point ──────────────────────────────────────────────────────


def build_parser() -> tuple[argparse.ArgumentParser, dict[str, argparse.ArgumentParser]]:
    """Build the CLI parser.

    Returns ``(parser, subparsers)``: the subparser registry keyed by command
    name feeds ``print_full_help()`` so one call can render every command's
    full options, not just the one-line summaries.  Shared by one-shot mode,
    ``lpm help``, and the interactive shell so command definitions live in
    exactly one place.
    """
    parser = argparse.ArgumentParser(
        prog="lpm" if FROZEN else None,
        description="Printer statistics - collect & report",
        epilog="Run with no flags to open the interactive shell; 'lpm help' lists all commands.",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p_init = sub.add_parser("init", help="Create config from the example")
    p_init.set_defaults(func=_handle_init)

    p_collect = sub.add_parser("collect", help="Collect statistics from all printers")
    p_collect.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    p_collect.add_argument("--dry", action="store_true", help="Dry run: collect but don't save to database")
    p_collect.set_defaults(func=_handle_collect)

    p_validate = sub.add_parser("validate", help="Check that the project is set up correctly")
    p_validate.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    p_validate.add_argument("--network", action="store_true", help="Also check live SNMP reachability of each printer")
    p_validate.set_defaults(func=_handle_validate)

    p_report = sub.add_parser("report", help="Generate a weekly statistics report")
    p_report.add_argument("--from", dest="from_date", help="Start date (YYYY-MM-DD)")
    p_report.add_argument("--to", dest="to_date", help="End date (YYYY-MM-DD)")
    p_report.add_argument("--csv", action="store_true", help="Export report as CSV")
    p_report.set_defaults(func=_handle_report)

    p_schedule = sub.add_parser("schedule", help="Create or update the scheduled collection task")
    p_schedule.add_argument("-r", "--remove", action="store_true", help="Remove the scheduled task")
    p_schedule.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    p_schedule.set_defaults(func=_handle_schedule)

    p_help = sub.add_parser("help", help="Show full help for all commands (or one command)")
    p_help.add_argument("topic", nargs="?", metavar="COMMAND", help="Show help for a single command")
    p_help.set_defaults(func=_handle_help)

    p_shell = sub.add_parser("shell", help="Start the interactive shell (same as bare 'lpm')")
    p_shell.set_defaults(func=_handle_shell)

    return parser, dict(sub.choices)


def print_full_help(
    parser: argparse.ArgumentParser,
    subparsers: dict[str, argparse.ArgumentParser],
) -> None:
    """Print the top-level help followed by every command's full options.

    The default argparse listing only shows one-line summaries; this renders
    each subparser's usage and flags too, so a single `lpm help` (or bare
    ``lpm`` on a pipe) shows everything at once.
    """
    parser.print_help()
    print("\ncommand details:")
    for sub in subparsers.values():
        print()
        sub.print_help()


def _handle_help(args: argparse.Namespace) -> None:
    """``lpm help`` - all commands, or ``lpm help <command>`` - one command."""
    parser, subparsers = build_parser()
    topic: str | None = args.topic
    if topic is None:
        print_full_help(parser, subparsers)
    elif topic in subparsers:
        subparsers[topic].print_help()
    else:
        print(f"Unknown command: {topic!r}", file=sys.stderr)
        sys.exit(2)


def _handle_shell(args: argparse.Namespace) -> None:
    """``lpm shell`` - start the interactive shell (same as bare ``lpm``)."""
    del args  # no shell-specific flags (signature shared with the other handlers)
    run_shell(build_parser)


def main() -> None:
    parser, subparsers = build_parser()
    args = parser.parse_args()

    handler = getattr(args, "func", None)
    if handler is not None:
        handler(args)
    elif sys.stdin.isatty():
        run_shell(build_parser)
    else:
        # Piped/redirected stdin (scripts, CI): print help instead of
        # blocking forever on input().
        print_full_help(parser, subparsers)


if __name__ == "__main__":
    main()
