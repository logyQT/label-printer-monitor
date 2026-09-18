"""Printer statistics collection main executor.

Usage:
    python main.py                    # auto-detect shift, collect now
    python main.py --shift start      # record shift start snapshot
    python main.py --shift end        # record shift end + log delta
    python main.py --collect          # force collect (no shift logic)
    python main.py --report           # generate CSV for current week
    python main.py --report --from 2026-09-10 --to 2026-09-17
"""

import argparse
import csv
import json
import logging
import os
import sys
from datetime import datetime, timedelta
from typing import Optional

import db
from adapters import create_adapter

log = logging.getLogger('printer_stats')


def load_config(config_path='config.json'):
    """Load configuration from JSON file.

    Args:
        config_path: Path to config.json.

    Returns:
        Dict with configuration.

    Raises:
        SystemExit: If config file not found or invalid.
    """
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
    """Configure logging to file and console.

    Args:
        log_dir: Directory for log files.
        verbose: If True, set console to DEBUG level.
    """
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    now = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = os.path.join(log_dir, f'run_{now}.log')

    level = logging.DEBUG if verbose else logging.INFO

    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')

    # File handler always gets DEBUG
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    # Console handler respects verbose flag
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)

    # Root logger set to WARNING so pysnmp noise doesn't leak through
    root = logging.getLogger()
    root.setLevel(logging.WARNING)

    # Our logger gets everything, filtered by handlers
    app_logger = logging.getLogger('printer_stats')
    app_logger.setLevel(logging.DEBUG)
    app_logger.addHandler(file_handler)
    app_logger.addHandler(console_handler)
    return log_file


def detect_current_shift(shifts, now=None):
    """Detect which shift is currently active based on time.

    Args:
        shifts: List of shift dicts with 'name', 'start', 'end'.
        now: Current datetime (for testing).

    Returns:
        Tuple of (shift_dict, phase) where phase is 'start' or 'end'.
        Returns (None, None) if no shift detected.
    """
    if now is None:
        now = datetime.now()
    current_time = now.strftime('%H:%M')

    for shift in shifts:
        start = shift['start']
        end = shift['end']

        # Normal shift (e.g. 06:00-14:00)
        if start <= end:
            # Check if we're within 10 minutes of start or end
            if _within_minutes(current_time, start, 10):
                return shift, 'start'
        else:
            # Overnight shift (e.g. 22:00-06:00)
            if current_time >= start or current_time <= end:
                if _within_minutes(current_time, start, 10):
                    return shift, 'start'

    for shift in shifts:
        start = shift['start']
        end = shift['end']

        if start <= end:
            if _within_minutes(current_time, end, 10):
                return shift, 'end'
        else:
            if current_time >= start or current_time <= end:
                if _within_minutes(current_time, end, 10):
                    return shift, 'end'

    return None, None


def _within_minutes(current, target, minutes):
    """Check if current time is within N minutes of target time."""
    c_h, c_m = map(int, current.split(':'))
    t_h, t_m = map(int, target.split(':'))
    diff = abs((c_h * 60 + c_m) - (t_h * 60 + t_m))
    return diff <= minutes


def collect_printer(adapter, printer_cfg):
    """Collect counters from a single printer.

    Args:
        adapter: PrinterAdapter instance.
        printer_cfg: Printer config dict.

    Returns:
        Dict with counter data or None on failure.
    """
    try:
        counters = adapter.get_counters()
        if not counters.get('reachable'):
            log.warning(f"Printer {printer_cfg['ip']} ({printer_cfg['location']}) not reachable")
            return None
        return counters
    except Exception as e:
        log.error(f"Error collecting from {printer_cfg['ip']}: {e}")
        return None


def run_collection(config, shift_phase=None, shift_name=None):
    """Run a collection cycle for all printers.

    Args:
        config: Configuration dict.
        shift_phase: 'start' or 'end' or None.
        shift_name: Name of the shift (for logging).

    Returns:
        Tuple of (success_count, fail_count, total_count).
    """
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

    phase_label = f" ({shift_phase} of {shift_name})" if shift_phase else ""
    log.info(f"Starting collection{phase_label} for {total} printers")

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
                version=0,  # SNMPv1 - more reliable on wireless
                unit_map=sato_unit_map,
            )
            counters = collect_printer(adapter, printer_cfg)

            if counters is None:
                fail += 1
                continue

            # Save snapshot
            db.save_snapshot(
                conn,
                printer_ip=ip,
                labels_total=counters.get('labels_total'),
                meters_total=counters.get('meters_total'),
                meter_unit=counters.get('meter_unit', 'unknown'),
                model_name=counters.get('model_name', ''),
            )
            success += 1
            log.info(
                f"OK: {location} ({ip}) - "
                f"labels={counters.get('labels_total')}, "
                f"meters={counters.get('meters_total')} "
                f"{counters.get('meter_unit', '')}"
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


def calculate_shift_deltas(config, shift_name, shift_start, shift_end):
    """Calculate and log shift deltas for all printers.

    Args:
        config: Configuration dict.
        shift_name: Shift name.
        shift_start: Start timestamp (Unix epoch int).
        shift_end: End timestamp (Unix epoch int).
    """
    db_path = config['db_path']
    conn = db.init_db(db_path)

    log.info(f"Calculating shift deltas for {shift_name}")

    # Build location lookup
    location_map = {p['ip']: p['location'] for p in config['printers']}

    results = db.get_printers_by_shift(conn, shift_start, shift_end)
    for r in results:
        ip = r['printer_ip']
        location = location_map.get(ip, ip)
        labels = r.get('labels_delta', 0) or 0
        meters = r.get('meters_delta', 0) or 0
        unit = r.get('start_snapshot', {}).get('meter_unit', '')
        log.info(
            f"SHIFT {shift_name}: {location} ({ip}) - "
            f"labels={labels}, meters={meters} {unit}"
        )

    db.close_db(conn)


def generate_report(config, from_date=None, to_date=None):
    """Generate CSV report.

    Args:
        config: Configuration dict.
        from_date: Start date (ISO 8601).
        to_date: End date (ISO 8601).

    Returns:
        Path to generated CSV file.
    """
    db_path = config['db_path']
    conn = db.init_db(db_path)

    if from_date is None:
        from_date = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    if to_date is None:
        to_date = datetime.now().strftime('%Y-%m-%d')

    # Build location lookup
    location_map = {p['ip']: p['location'] for p in config['printers']}

    # Get all printer IPs
    cursor = conn.execute("SELECT DISTINCT printer_ip FROM snapshots")
    printer_ips = [row[0] for row in cursor.fetchall()]

    # Generate report filename
    report_name = f"report_{from_date}_to_{to_date}.csv"
    report_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), report_name)

    with open(report_path, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow([
            'IP Address', 'Location', 'Model',
            'Shift', 'Date', 'Labels Printed', 'Meters Printed',
            'Meter Unit',
        ])

        for ip in printer_ips:
            history = db.get_history(conn, ip, from_date, to_date)
            location = location_map.get(ip, ip)

            for snap in history:
                writer.writerow([
                    ip,
                    location,
                    snap.get('model_name', ''),
                    '',  # shift name (could be derived from timestamp)
                    snap.get('timestamp', ''),
                    snap.get('labels_total', ''),
                    snap.get('meters_total', ''),
                    snap.get('meter_unit', ''),
                ])

    db.close_db(conn)
    log.info(f"Report generated: {report_path}")
    print(f"Report saved to: {report_path}")
    return report_path


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description='Printer statistics collector')
    parser.add_argument('--config', default='config.json', help='Path to config.json')
    parser.add_argument('--collect', action='store_true', help='Force collection (no shift logic)')
    parser.add_argument('--shift', choices=['start', 'end'], help='Record shift start or end')
    parser.add_argument('--report', action='store_true', help='Generate CSV report')
    parser.add_argument('--from', dest='from_date', help='Report start date (YYYY-MM-DD)')
    parser.add_argument('--to', dest='to_date', help='Report end date (YYYY-MM-DD)')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Show detailed SNMP request/response logs')
    args = parser.parse_args()

    config = load_config(args.config)
    log_file = setup_logging(config.get('log_dir', 'logs'), verbose=args.verbose)
    log.info(f"Log file: {log_file}")

    if args.report:
        generate_report(config, args.from_date, args.to_date)
        return

    if args.collect:
        run_collection(config)
        return

    if args.shift:
        shift_phase = args.shift
        # Detect current shift
        now = datetime.now()
        shifts = config.get('shifts', [])

        if shift_phase == 'start':
            # Find which shift is starting
            for shift in shifts:
                if _within_minutes(now.strftime('%H:%M'), shift['start'], 15):
                    log.info(f"Recording START of shift: {shift['name']}")
                    run_collection(config, shift_phase='start', shift_name=shift['name'])
                    return
            log.info("No matching shift start detected, collecting anyway")
            run_collection(config, shift_phase='start')

        elif shift_phase == 'end':
            # Find which shift is ending
            for shift in shifts:
                if _within_minutes(now.strftime('%H:%M'), shift['end'], 15):
                    log.info(f"Recording END of shift: {shift['name']}")
                    run_collection(config, shift_phase='end', shift_name=shift['name'])
                    # Calculate deltas
                    start_time = now.replace(
                        hour=int(shift['start'].split(':')[0]),
                        minute=int(shift['start'].split(':')[1]),
                        second=0, microsecond=0
                    )
                    calculate_shift_deltas(
                        config, shift['name'],
                        int(start_time.timestamp()),
                        int(now.timestamp()),
                    )
                    return
            log.info("No matching shift end detected, collecting anyway")
            run_collection(config, shift_phase='end')
        return

    # Auto-detect: run collection
    run_collection(config)


if __name__ == '__main__':
    main()
