"""CSV report generation for printer statistics.

Usage:
    python report.py                          # last 7 days
    python report.py --from 2026-09-10 --to 2026-09-17
    python report.py --shifts                 # include shift breakdown
"""

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timedelta
from typing import Optional

import db


def load_config(config_path='config.json'):
    """Load configuration from JSON file."""
    if not os.path.exists(config_path):
        print(f'ERROR: Config file not found: {config_path}', file=sys.stderr)
        sys.exit(2)
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def generate_weekly_report(config, from_date=None, to_date=None, include_shifts=False):
    """Generate a weekly CSV report.

    Args:
        config: Configuration dict.
        from_date: Start date string (YYYY-MM-DD).
        to_date: End date string (YYYY-MM-DD).
        include_shifts: If True, include per-shift breakdown.

    Returns:
        Path to generated CSV file.
    """
    db_path = config['db_path']
    conn = db.init_db(db_path)

    if from_date is None:
        from_date = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    if to_date is None:
        to_date = datetime.now().strftime('%Y-%m-%d')

    # Build lookups
    location_map = {p['ip']: p['location'] for p in config['printers']}
    model_map = {p['ip']: p['model'] for p in config['printers']}

    # Get all printer IPs with data in range
    cursor = conn.execute(
        """SELECT DISTINCT printer_ip FROM snapshots
           WHERE timestamp >= ? AND timestamp <= ?""",
        (from_date, to_date + 'T23:59:59')
    )
    printer_ips = [row[0] for row in cursor.fetchall()]

    # Generate filename
    script_dir = os.path.dirname(os.path.abspath(__file__))
    report_name = f"report_{from_date}_to_{to_date}.csv"
    report_path = os.path.join(script_dir, report_name)

    with open(report_path, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.writer(csvfile)

        if include_shifts:
            writer.writerow([
                'IP Address', 'Location', 'Model', 'Serial',
                'Shift', 'Date', 'Labels (start)', 'Labels (end)',
                'Labels Delta', 'Meters (start)', 'Meters (end)',
                'Meters Delta', 'Meter Unit', 'Status'
            ])
        else:
            writer.writerow([
                'IP Address', 'Location', 'Model', 'Serial',
                'Date', 'Labels Total', 'Meters Total',
                'Meter Unit', 'Status'
            ])

        for ip in printer_ips:
            history = db.get_history(conn, ip, from_date, to_date)
            location = location_map.get(ip, ip)
            model = model_map.get(ip, '')
            serial = history[0].get('serial', '') if history else ''

            if include_shifts:
                _write_shift_rows(writer, ip, location, model, serial, history, config)
            else:
                _write_summary_rows(writer, ip, location, model, serial, history)

    db.close_db(conn)
    print(f"Report saved to: {report_path}")
    return report_path


def _write_shift_rows(writer, ip, location, model, serial, history, config):
    """Write per-shift breakdown rows."""
    shifts = config.get('shifts', [])

    for snap in history:
        ts = snap.get('timestamp', '')
        if not ts:
            continue

        # Determine shift from timestamp
        shift_name = _get_shift_for_time(ts, shifts)

        writer.writerow([
            ip,
            location,
            model,
            serial,
            shift_name,
            ts[:10],  # date only
            '',  # labels start
            snap.get('labels_total', ''),
            '',  # labels delta
            '',  # meters start
            snap.get('meters_total', ''),
            '',  # meters delta
            snap.get('meter_unit', ''),
            snap.get('status', ''),
        ])


def _write_summary_rows(writer, ip, location, model, serial, history):
    """Write summary rows (one per snapshot)."""
    for snap in history:
        writer.writerow([
            ip,
            location,
            model,
            serial,
            snap.get('timestamp', '')[:10],
            snap.get('labels_total', ''),
            snap.get('meters_total', ''),
            snap.get('meter_unit', ''),
            snap.get('status', ''),
        ])


def _get_shift_for_time(timestamp_str, shifts):
    """Determine which shift a timestamp belongs to."""
    try:
        time_part = timestamp_str[11:16]  # HH:MM
    except (IndexError, ValueError):
        return 'Unknown'

    for shift in shifts:
        start = shift['start']
        end = shift['end']
        name = shift['name']

        if start <= end:
            if start <= time_part <= end:
                return name
        else:
            # Overnight shift
            if time_part >= start or time_part <= end:
                return name

    return 'Unknown'


def generate_summary(config, from_date=None, to_date=None):
    """Generate a text summary to stdout.

    Args:
        config: Configuration dict.
        from_date: Start date.
        to_date: End date.
    """
    db_path = config['db_path']
    conn = db.init_db(db_path)

    if from_date is None:
        from_date = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    if to_date is None:
        to_date = datetime.now().strftime('%Y-%m-%d')

    location_map = {p['ip']: p['location'] for p in config['printers']}

    cursor = conn.execute(
        """SELECT DISTINCT printer_ip FROM snapshots
           WHERE timestamp >= ? AND timestamp <= ?""",
        (from_date, to_date + 'T23:59:59')
    )
    printer_ips = [row[0] for row in cursor.fetchall()]

    total_labels = 0
    total_meters = 0.0

    print(f"\n{'='*70}")
    print(f"PRINTER STATISTICS REPORT: {from_date} to {to_date}")
    print(f"{'='*70}")
    print(f"{'Location':<30} {'Labels':>10} {'Meters':>10} {'Unit':<10}")
    print(f"{'-'*70}")

    for ip in printer_ips:
        history = db.get_history(conn, ip, from_date, to_date)
        if not history:
            continue

        location = location_map.get(ip, ip)
        labels = history[-1].get('labels_total', 0) or 0
        meters = history[-1].get('meters_total', 0) or 0
        unit = history[-1].get('meter_unit', '')

        total_labels += labels
        total_meters += meters

        print(f"{location:<30} {labels:>10,} {meters:>10,.1f} {unit:<10}")

    print(f"{'-'*70}")
    print(f"{'TOTAL':<30} {total_labels:>10,} {total_meters:>10,.1f}")
    print(f"{'='*70}\n")

    db.close_db(conn)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description='Printer statistics report generator')
    parser.add_argument('--config', default='config.json', help='Path to config.json')
    parser.add_argument('--from', dest='from_date', help='Start date (YYYY-MM-DD)')
    parser.add_argument('--to', dest='to_date', help='End date (YYYY-MM-DD)')
    parser.add_argument('--shifts', action='store_true', help='Include shift breakdown')
    parser.add_argument('--summary', action='store_true', help='Print summary to stdout')
    args = parser.parse_args()

    config = load_config(args.config)

    if args.summary:
        generate_summary(config, args.from_date, args.to_date)
    else:
        generate_weekly_report(config, args.from_date, args.to_date, args.shifts)


if __name__ == '__main__':
    main()
