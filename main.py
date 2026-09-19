"""Printer statistics collection & reporting entry point.

Usage:
    python main.py --init                   # create config from the example
    python main.py --collect                # collect from all printers
    python main.py --collect --verbose      # with SNMP debug output
    python main.py --report                 # weekly report (current week)
    python main.py --report --from 2026-09-01 --to 2026-09-30
    python main.py --report --csv           # export CSV
    python main.py --test                   # run all tests
    python main.py --validate               # check that everything is set up
    python main.py --validate --network     # also check SNMP reachability
    python main.py                          # show this help
"""

import argparse
import json
import logging
import os
import shutil
import sys
from datetime import datetime

# Ensure src/ is on the path so library imports work
_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, 'src')
sys.path.insert(0, _SRC)

import db
from adapters import create_adapter

log = logging.getLogger('printer_stats')


# ── path helpers ─────────────────────────────────────────────────────

def _project_root():
    return _HERE


def _config_path():
    return os.path.join(_HERE, 'config', 'config.json')


def _db_path(config):
    """Resolve the DB path from config['db']['filename'] → data/<filename>.

    ':memory:' is passed through as-is for in-memory SQLite databases.
    """
    filename = config.get('db', {}).get('filename', 'printer_stats.db')
    if filename == ':memory:':
        return filename
    return os.path.join(_HERE, 'data', filename)


def _backups_dir():
    d = os.path.join(_HERE, 'data', 'backups')
    os.makedirs(d, exist_ok=True)
    return d


# ── helpers ──────────────────────────────────────────────────────────

def load_config():
    path = _config_path()
    if not os.path.exists(path):
        print(f'ERROR: Config file not found: {path}', file=sys.stderr)
        sys.exit(2)
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(f'ERROR: Invalid JSON in config: {e}', file=sys.stderr)
        sys.exit(2)


def backup_data():
    """Snapshot config + db into data/backups/<timestamp>/ before a run."""
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    dest = os.path.join(_backups_dir(), ts)
    os.makedirs(dest, exist_ok=True)

    # Backup config
    src = _config_path()
    if os.path.exists(src):
        shutil.copy2(src, os.path.join(dest, 'config.json'))

    # Backup schema
    schema = os.path.join(_HERE, 'config', 'config.json.schema')
    if os.path.exists(schema):
        shutil.copy2(schema, os.path.join(dest, 'config.json.schema'))

    return dest


def setup_logging(log_dir, verbose=False):
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    now = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = os.path.join(log_dir, f'run_{now}.log')

    level = logging.DEBUG if verbose else logging.INFO
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')

    file_handler = logging.FileHandler(log_file, encoding='utf-8')
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

    app_logger = logging.getLogger('printer_stats')
    app_logger.setLevel(logging.DEBUG)
    app_logger.propagate = False
    app_logger.addHandler(file_handler)
    app_logger.addHandler(console_handler)
    return log_file


# ── init ──────────────────────────────────────────────────────────────

def _handle_init():
    """Bootstrap the project: create config from the example + runtime dirs."""
    from validate import find_schema_violation, resolve_schema_path

    example = os.path.join(_HERE, 'config', 'config.example.json')
    target = _config_path()

    if os.path.exists(target):
        print(f'Config already exists: {target}')
        print('Leaving it untouched. Edit it to match your printers.')
        return

    if not os.path.exists(example):
        print(f'ERROR: Example config not found: {example}', file=sys.stderr)
        sys.exit(2)

    shutil.copy2(example, target)

    # Runtime dirs are gitignored and needed before db/collection work
    for d in (os.path.join(_HERE, 'data'),
              os.path.join(_HERE, 'logs'),
              _backups_dir()):
        os.makedirs(d, exist_ok=True)

    print(f'Created {target}')
    print('Created runtime directories: data/, data/backups/, logs/')

    # The copied config carries a $schema link - confirm it validates
    schema_path = resolve_schema_path(target)
    try:
        with open(target, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        with open(schema_path, 'r', encoding='utf-8') as f:
            schema = json.load(f)
        violation = find_schema_violation(cfg, schema)
        if violation:
            print(f'WARNING: copied config does not match schema: {violation.message}')
        else:
            print(f'Config validates against {os.path.basename(schema_path)}')
    except Exception as e:
        print(f'WARNING: could not validate copied config: {e}')

    print('Edit config/config.json with your printers, then run:')
    print('  python main.py --validate  # check everything is set up')
    print('  python main.py --collect')


# ── collect ──────────────────────────────────────────────────────────

def collect_printer(adapter, printer_cfg):
    try:
        counters = adapter.get_counters()
        if not counters.get('reachable'):
            log.warning(f"Printer {printer_cfg['ip']} ({printer_cfg['location']}) not reachable")
            return None
        return counters
    except Exception as e:
        log.error(f"Error collecting from {printer_cfg['ip']}: {e}")
        return None


def run_collection(config):
    db_path = _db_path(config)
    conn = db.init_db(db_path)
    snmp_config = config.get('snmp', {})
    community = snmp_config.get('community', 'public')
    timeout = snmp_config.get('timeout_sec', 3)
    retries = snmp_config.get('retries', 2)

    success = 0
    fail = 0
    total = len(config['printers'])

    log.info(f"Starting collection for {total} printers")

    for printer_cfg in config['printers']:
        ip = printer_cfg['ip']
        model = printer_cfg['model']
        location = printer_cfg['location']

        try:
            adapter = create_adapter(
                model, ip,
                community=community,
                timeout_sec=timeout,
                retries=retries,
                version=0,
            )
            counters = collect_printer(adapter, printer_cfg)

            if counters is None:
                fail += 1
                continue

            db.save_snapshot(
                conn,
                printer_ip=ip,
                labels_total=counters.get('labels_total'),
                meters_total=counters.get('meters_total'),
                meter_unit=counters.get('meter_unit', 'unknown'),
                model_name=counters.get('model_name', ''),
            )
            success += 1
            labels = counters.get('labels_total')
            meters = counters.get('meters_total')
            unit = counters.get('meter_unit', '')
            labels_str = f"{labels:,}" if labels is not None else "n/a"
            meters_str = f"{meters:,.1f} {unit}" if meters is not None else "n/a"
            log.info(
                f"{model} ({ip}) [{location}] - "
                f"labels: {labels_str}, odometer: {meters_str}"
            )

        except ValueError as e:
            log.error(f"Config error for {ip}: {e}")
            fail += 1
        except Exception as e:
            log.error(f"Unexpected error for {ip}: {e}")
            fail += 1

    db.close_db(conn)
    log.info(f"Collection complete: {success}/{total} success, {fail}/{total} failed")
    return success, fail, total


def _handle_collect(args):
    config = load_config()
    backup_data()
    log_dir = os.path.join(_project_root(), config.get('log_dir', 'logs'))
    log_file = setup_logging(log_dir, verbose=args.verbose)
    log.info(f"Log file: {log_file}")
    run_collection(config)


# ── validate ──────────────────────────────────────────────────────────

def _handle_validate(args):
    from validate import validate_setup, validate_network

    config_path = _config_path()

    issues = validate_setup(config_path, _HERE)

    if args.network:
        try:
            issues += validate_network(load_config())
        except SystemExit:
            pass  # config missing – the setup issues already say so

    for level, message in issues:
        print(f'[{level}] {message}')

    fails = [i for i in issues if i.level == 'FAIL']
    if fails:
        print(f'\n{len(fails)} problem(s) found. Fix them, then re-run validation.')
        sys.exit(1)
    print('\nSetup looks good.')


# ── report ───────────────────────────────────────────────────────────

def _handle_report(args):
    from report import compute_weekly, print_report, export_csv

    config = load_config()
    backup_data()
    from_date = args.from_date or datetime.now().strftime('%Y-%m-%d')
    to_date = args.to_date or datetime.now().strftime('%Y-%m-%d')

    weeks = compute_weekly(config, from_date, to_date)

    if args.csv:
        path = os.path.join(_project_root(), f'report_{from_date}_to_{to_date}.csv')
        export_csv(weeks, config, path)
    else:
        print_report(weeks, config)


# ── test ─────────────────────────────────────────────────────────────

def _handle_test():
    import unittest
    test_dir = os.path.join(_SRC, '_tests_')
    if not os.path.isdir(test_dir):
        print(f'ERROR: Test directory not found: {test_dir}', file=sys.stderr)
        sys.exit(2)
    loader = unittest.TestLoader()
    suite = loader.discover(test_dir, pattern='test_*.py',
                            top_level_dir=_SRC)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    failed = len(result.failures)
    errors = len(result.errors)
    sys.exit(1 if (failed or errors) else 0)


# ── entry point ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Printer statistics – collect & report',
        epilog='Run with no flags to see this help message.',
    )

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--init', action='store_true',
                      help='Create config/config.json from the example')
    mode.add_argument('--collect', action='store_true',
                      help='Collect statistics from all printers')
    mode.add_argument('--report', action='store_true',
                      help='Generate a weekly statistics report')
    mode.add_argument('--test', action='store_true',
                      help='Run all tests')
    mode.add_argument('--validate', action='store_true',
                      help='Check that the project is set up correctly')

    # Collect-specific flags
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='(collect) Show detailed SNMP debug output')

    # Report-specific flags
    parser.add_argument('--from', dest='from_date',
                        help='(report) Start date (YYYY-MM-DD)')
    parser.add_argument('--to', dest='to_date',
                        help='(report) End date (YYYY-MM-DD)')
    parser.add_argument('--csv', action='store_true',
                        help='(report) Export report as CSV')

    # Validate-specific flags
    parser.add_argument('--network', action='store_true',
                        help='(validate) Also check live SNMP reachability of each printer')

    args = parser.parse_args()

    if args.init:
        _handle_init()
    elif args.collect:
        _handle_collect(args)
    elif args.report:
        _handle_report(args)
    elif args.test:
        _handle_test()
    elif args.validate:
        _handle_validate(args)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
