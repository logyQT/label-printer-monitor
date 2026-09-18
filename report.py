"""Shift-based printer statistics report.

Usage:
    python report.py                          # today's shifts
    python report.py --from 2026-09-10 --to 2026-09-18
    python report.py --csv                    # export CSV
"""

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import db


def load_config(config_path='config.json'):
    if not os.path.exists(config_path):
        print(f'ERROR: Config file not found: {config_path}', file=sys.stderr)
        sys.exit(2)
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def _to_epoch(date_str):
    """Convert 'YYYY-MM-DD' to epoch start of day."""
    return int(datetime.fromisoformat(date_str).timestamp())


def _epoch_to_shift(epoch, shifts):
    """Determine which shift an epoch timestamp belongs to."""
    dt = datetime.fromtimestamp(epoch)
    t = dt.strftime('%H:%M')
    for shift in shifts:
        s, e = shift['start'], shift['end']
        if s <= e:
            if s <= t <= e:
                return shift['name']
        else:
            if t >= s or t <= e:
                return shift['name']
    return None


def _convert_to_meters(value, unit):
    """Convert a value to meters based on unit."""
    if value is None:
        return None
    if unit == 'cm':
        return value / 100.0
    if unit == 'm':
        return value
    if unit == 'in':
        return value * 0.0254
    if unit == 'mm':
        return value / 1000.0
    return value  # unknown unit, pass through


def _get_snapshots_in_window(conn, ip, start_epoch, end_epoch):
    """Get snapshots for a printer within a time window."""
    rows = conn.execute(
        """SELECT timestamp, labels_total, meters_total, meter_unit
           FROM snapshots
           WHERE printer_ip = ? AND timestamp >= ? AND timestamp <= ?
           ORDER BY timestamp ASC""",
        (ip, start_epoch, end_epoch)
    ).fetchall()
    return rows


def compute_shift_deltas(config, from_date, to_date):
    """Compute shift deltas for all printers.

    Returns list of dicts with:
        ip, model, shift, date, labels_delta, meters_delta, meter_unit
    """
    db_path = config['db_path']
    conn = db.init_db(db_path)
    shifts = config.get('shifts', [])
    printer_map = {p['ip']: p for p in config.get('printers', [])}

    start_epoch = _to_epoch(from_date)
    end_epoch = _to_epoch(to_date) + 86399  # end of day

    results = []

    # Get all printer IPs that have data in range
    printer_ips = [row[0] for row in conn.execute(
        """SELECT DISTINCT printer_ip FROM snapshots
           WHERE timestamp >= ? AND timestamp <= ?""",
        (start_epoch, end_epoch)
    ).fetchall()]

    # Build model lookup: config first, then from DB snapshots
    model_map = {p['ip']: p['model'] for p in config.get('printers', [])}
    for ip in printer_ips:
        if ip not in model_map:
            row = conn.execute(
                "SELECT model_name FROM snapshots WHERE printer_ip = ? AND model_name IS NOT NULL AND model_name != '' LIMIT 1",
                (ip,)
            ).fetchone()
            if row:
                model_map[ip] = row[0]

    for ip in printer_ips:
        model = model_map.get(ip, 'Unknown')

        # For each shift, find first and last snapshot in the window
        for shift in shifts:
            shift_name = shift['name']
            s_h, s_m = map(int, shift['start'].split(':'))
            e_h, e_m = map(int, shift['end'].split(':'))

            # Skip overnight shifts for now
            if s_h > e_h or (s_h == e_h and s_m > e_m):
                continue

            # Iterate each day in range
            current_date = datetime.fromisoformat(from_date).date()
            end_date = datetime.fromisoformat(to_date).date()
            days_processed = 0

            while current_date <= end_date:
                shift_start = datetime(current_date.year, current_date.month, current_date.day,
                                       s_h, s_m, tzinfo=timezone.utc)
                shift_end = datetime(current_date.year, current_date.month, current_date.day,
                                     e_h, e_m, tzinfo=timezone.utc)

                shift_start_ep = int(shift_start.timestamp())
                shift_end_ep = int(shift_end.timestamp())

                window = _get_snapshots_in_window(conn, ip, shift_start_ep, shift_end_ep)
                if len(window) >= 2:
                    first = window[0]
                    last = window[-1]

                    # Labels delta
                    labels_start = first[1] or 0
                    labels_end = last[1] or 0
                    labels_delta = labels_end - labels_start if first[1] is not None and last[1] is not None else None

                    # Meters delta (convert to meters)
                    unit = first[3] or last[3] or 'unknown'
                    meters_start = _convert_to_meters(first[2], unit)
                    meters_end = _convert_to_meters(last[2], unit)
                    meters_delta = (meters_end - meters_start) if meters_start is not None and meters_end is not None else None

                    results.append({
                        'ip': ip,
                        'model': model,
                        'shift': shift_name,
                        'date': current_date.isoformat(),
                        'labels_delta': labels_delta,
                        'meters_delta': meters_delta,
                    })

                current_date += timedelta(days=1)

    db.close_db(conn)
    return results


def print_report(results, config):
    """Print a formatted shift report to stdout."""
    # Build model lookup: config first, then from results themselves
    model_full = {p['ip']: p['model'] for p in config.get('printers', [])}
    for r in results:
        if r['ip'] not in model_full:
            model_full[r['ip']] = r['model']

    # Separate Zebra (has labels) and Sato (meters only)
    zebra = [r for r in results if 'zebra' in model_full.get(r['ip'], r['model']).lower()
             or 'ztc' in model_full.get(r['ip'], r['model']).lower()]
    sato = [r for r in results if 'sato' in model_full.get(r['ip'], r['model']).lower()
            or 'cl4' in model_full.get(r['ip'], r['model']).lower()
            or 'cl6' in model_full.get(r['ip'], r['model']).lower()]

    if zebra:
        print(f"\n{'='*80}")
        print(f"  ZEBRA — Labels + Meters (converted to m)")
        print(f"{'='*80}")
        print(f"  {'IP':<15} {'Model':<20} {'Shift':<12} {'Date':<12} {'Labels':>10} {'Meters (m)':>12}")
        print(f"  {'-'*76}")
        for r in sorted(zebra, key=lambda x: (x['date'], x['shift'])):
            labels = f"{r['labels_delta']:,}" if r['labels_delta'] is not None else '-'
            meters = f"{r['meters_delta']:,.1f}" if r['meters_delta'] is not None else '-'
            model_short = model_full.get(r['ip'], r['model'])
            print(f"  {r['ip']:<15} {model_short:<20} {r['shift']:<12} {r['date']:<12} {labels:>10} {meters:>12}")

        total_labels = sum(r['labels_delta'] or 0 for r in zebra)
        total_meters = sum(r['meters_delta'] or 0 for r in zebra)
        print(f"  {'-'*76}")
        print(f"  {'TOTAL':<48} {total_labels:>10,} {total_meters:>12,.1f}")
        print()

    if sato:
        print(f"\n{'='*80}")
        print(f"  SATO — Meters")
        print(f"{'='*80}")
        print(f"  {'IP':<15} {'Model':<20} {'Shift':<12} {'Date':<12} {'Meters (m)':>12}")
        print(f"  {'-'*76}")
        for r in sorted(sato, key=lambda x: (x['date'], x['shift'])):
            meters = f"{r['meters_delta']:,.1f}" if r['meters_delta'] is not None else '-'
            model_short = model_full.get(r['ip'], r['model'])
            print(f"  {r['ip']:<15} {model_short:<20} {r['shift']:<12} {r['date']:<12} {meters:>12}")

        total_meters = sum(r['meters_delta'] or 0 for r in sato)
        print(f"  {'-'*76}")
        print(f"  {'TOTAL':<48} {total_meters:>12,.1f}")
        print()

    if not zebra and not sato:
        print("\n  No shift data found in the specified date range.")
        print("  Snapshots need to be collected during shift windows.\n")


def export_csv(results, config, path):
    """Export shift report to CSV."""
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['IP', 'Model', 'Shift', 'Date', 'Labels Delta', 'Meters Delta (m)'])
        for r in sorted(results, key=lambda x: (x['ip'], x['date'], x['shift'])):
            writer.writerow([
                r['ip'], r['model'], r['shift'], r['date'],
                r['labels_delta'] if r['labels_delta'] is not None else '',
                round(r['meters_delta'], 1) if r['meters_delta'] is not None else '',
            ])
    print(f"CSV saved to: {path}")


def main():
    parser = argparse.ArgumentParser(description='Printer shift report')
    parser.add_argument('--config', default='config.json')
    parser.add_argument('--from', dest='from_date', help='Start date (YYYY-MM-DD)')
    parser.add_argument('--to', dest='to_date', help='End date (YYYY-MM-DD)')
    parser.add_argument('--csv', action='store_true', help='Export CSV')
    args = parser.parse_args()

    config = load_config(args.config)

    from_date = args.from_date or datetime.now().strftime('%Y-%m-%d')
    to_date = args.to_date or datetime.now().strftime('%Y-%m-%d')

    results = compute_shift_deltas(config, from_date, to_date)

    if args.csv:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(script_dir, f'report_{from_date}_to_{to_date}.csv')
        export_csv(results, config, path)
    else:
        print_report(results, config)


if __name__ == '__main__':
    main()
