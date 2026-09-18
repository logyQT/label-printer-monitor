"""Printer statistics collection main executor.

Usage:
    python main.py                # collect from all printers
    python main.py --verbose      # with SNMP debug output
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime

import db
from adapters import create_adapter

log = logging.getLogger('printer_stats')


def load_config(config_path='config.json'):
    if not os.path.exists(config_path):
        print(f'ERROR: Config file not found: {config_path}', file=sys.stderr)
        sys.exit(2)
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(f'ERROR: Invalid JSON in config: {e}', file=sys.stderr)
        sys.exit(2)


def setup_logging(log_dir='logs', verbose=False):
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
    db_path = config['db_path']
    conn = db.init_db(db_path)
    snmp_config = config.get('snmp', {})
    community = snmp_config.get('community', 'public')
    timeout = snmp_config.get('timeout_sec', 3)
    retries = snmp_config.get('retries', 2)
    sato_unit_map = config.get('sato_unit_map', {})

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
                unit_map=sato_unit_map,
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


def main():
    parser = argparse.ArgumentParser(description='Printer statistics collector')
    parser.add_argument('--config', default='config.json', help='Path to config.json')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Show detailed SNMP request/response logs')
    args = parser.parse_args()

    config = load_config(args.config)
    log_file = setup_logging(config.get('log_dir', 'logs'), verbose=args.verbose)
    log.info(f"Log file: {log_file}")

    run_collection(config)


if __name__ == '__main__':
    main()
