"""Weekly printer statistics report.

Usage:
    python report.py                          # current week (Mon-Sun)
    python report.py --from 2026-09-01 --to 2026-09-30
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
    return int(datetime.fromisoformat(date_str).timestamp())


def _convert_to_meters(value, unit):
    if value is None:
        return None
    return {
        'cm': value / 100.0,
        'm': value,
        'mm': value / 1000.0,
        'in': value * 0.0254,
    }.get(unit, value)


def _get_week_label(epoch):
    """Return 'YYYY-Www' for the Monday of that week."""
    dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
    monday = dt - timedelta(days=dt.weekday())
    iso = monday.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _get_week_range(date_str):
    """Return (monday_epoch, sunday_end_epoch) for the week containing date_str."""
    dt = datetime.fromisoformat(date_str)
    monday = dt - timedelta(days=dt.weekday())
    sunday = monday + timedelta(days=6)
    start_ep = int(monday.replace(hour=0, minute=0, second=0, tzinfo=timezone.utc).timestamp())
    end_ep = int(sunday.replace(hour=23, minute=59, second=59, tzinfo=timezone.utc).timestamp())
    return start_ep, end_ep


def compute_weekly_report(config, from_date, to_date):
    """Compute weekly totals per printer.

    Returns list of dicts:
        ip, model, week_label, week_start, labels_total, meters_total
    """
    db_path = config['db_path']
    conn = db.init_db(db_path)

    from_ep = _to_epoch(from_date)
    to_ep = _to_epoch(to_date) + 86399

    # Get all printers with data
    printer_ips = [row[0] for row in conn.execute(
        "SELECT DISTINCT printer_ip FROM snapshots WHERE timestamp >= ? AND timestamp <= ?",
        (from_ep, to_ep)
    ).fetchall()]

    # Model lookup: config first, then DB
    model_map = {p['ip']: p['model'] for p in config.get('printers', [])}
    for ip in printer_ips:
        if ip not in model_map:
            row = conn.execute(
                "SELECT model_name FROM snapshots WHERE printer_ip = ? AND model_name IS NOT NULL LIMIT 1",
                (ip,)
            ).fetchone()
            if row:
                model_map[ip] = row[0]

    results = []

    for ip in printer_ips:
        model = model_map.get(ip, 'Unknown')

        # Get all snapshots for this printer in range
        rows = conn.execute(
            """SELECT timestamp, labels_total, meters_total, meter_unit
               FROM snapshots WHERE printer_ip = ? AND timestamp >= ? AND timestamp <= ?
               ORDER BY timestamp""",
            (ip, from_ep, to_ep)
        ).fetchall()

        if not rows:
            continue

        # Group by week
        weeks = {}
        for ts, labels, meters, unit in rows:
            wk = _get_week_label(ts)
            if wk not in weeks:
                weeks[wk] = {'first': None, 'last': None, 'unit': unit}
            weeks[wk]['last'] = (ts, labels, meters)
            if weeks[wk]['first'] is None:
                weeks[wk]['first'] = (ts, labels, meters)

        for wk, data in sorted(weeks.items()):
            first = data['first']
            last = data['last']
            unit = data['unit'] or 'unknown'

            # Labels delta
            l_start = first[1]
            l_end = last[1]
            if l_start is not None and l_end is not None:
                labels_delta = l_end - l_start
            else:
                labels_delta = None

            # Meters delta (convert to meters)
            m_start = _convert_to_meters(first[2], unit)
            m_end = _convert_to_meters(last[2], unit)
            if m_start is not None and m_end is not None:
                meters_delta = m_end - m_start
            else:
                meters_delta = None

            results.append({
                'ip': ip,
                'model': model,
                'week': wk,
                'labels': labels_delta,
                'meters': meters_delta,
            })

    db.close_db(conn)
    return results


def print_report(results, config):
    """Print weekly report to stdout."""
    model_full = {p['ip']: p['model'] for p in config.get('printers', [])}
    for r in results:
        if r['ip'] not in model_full:
            model_full[r['ip']] = r['model']

    # Separate Zebra and Sato
    zebra = [r for r in results if any(k in model_full.get(r['ip'], '').lower()
             for k in ('ztc', 'zebra'))]
    sato = [r for r in results if any(k in model_full.get(r['ip'], '').lower()
            for k in ('cl4', 'cl6', 'sato'))]
    unknown = [r for r in results if r not in zebra and r not in sato]

    if zebra:
        _print_group('ZEBRA — Labels + Meters (m)', zebra, model_full, show_labels=True)
    if sato:
        _print_group('SATO — Meters (m)', sato, model_full, show_labels=False)
    if unknown:
        _print_group('OTHER', unknown, model_full, show_labels=True)

    if not results:
        print("\n  No data found in the specified date range.\n")


def _print_group(title, rows, model_full, show_labels=True):
    """Print a group of printers."""
    print(f"\n{'='*80}")
    print(f"  {title}")
    print(f"{'='*80}")

    if show_labels:
        print(f"  {'IP':<15} {'Model':<22} {'Week':<10} {'Labels':>10} {'Meters (m)':>12}")
        print(f"  {'-'*76}")
    else:
        print(f"  {'IP':<15} {'Model':<22} {'Week':<10} {'Meters (m)':>12}")
        print(f"  {'-'*76}")

    # Sort by IP then week
    for r in sorted(rows, key=lambda x: (x['ip'], x['week'])):
        model = model_full.get(r['ip'], r['model'])
        if show_labels:
            labels = f"{r['labels']:,}" if r['labels'] is not None else '-'
            meters = f"{r['meters']:,.1f}" if r['meters'] is not None else '-'
            print(f"  {r['ip']:<15} {model:<22} {r['week']:<10} {labels:>10} {meters:>12}")
        else:
            meters = f"{r['meters']:,.1f}" if r['meters'] is not None else '-'
            print(f"  {r['ip']:<15} {model:<22} {r['week']:<10} {meters:>12}")

    # Totals
    total_labels = sum(r['labels'] or 0 for r in rows)
    total_meters = sum(r['meters'] or 0 for r in rows)

    print(f"  {'-'*76}")
    if show_labels:
        print(f"  {'TOTAL':<48} {total_labels:>10,} {total_meters:>12,.1f}")
    else:
        print(f"  {'TOTAL':<48} {total_meters:>12,.1f}")
    print()


def export_csv(results, config, path):
    """Export weekly report to CSV."""
    model_full = {p['ip']: p['model'] for p in config.get('printers', [])}
    for r in results:
        if r['ip'] not in model_full:
            model_full[r['ip']] = r['model']

    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['IP', 'Model', 'Week', 'Labels Delta', 'Meters Delta (m)'])
        for r in sorted(results, key=lambda x: (x['ip'], x['week'])):
            writer.writerow([
                r['ip'],
                model_full.get(r['ip'], r['model']),
                r['week'],
                r['labels'] if r['labels'] is not None else '',
                round(r['meters'], 1) if r['meters'] is not None else '',
            ])
    print(f"CSV saved to: {path}")


def main():
    parser = argparse.ArgumentParser(description='Weekly printer statistics report')
    parser.add_argument('--config', default='config.json')
    parser.add_argument('--from', dest='from_date', help='Start date (YYYY-MM-DD)')
    parser.add_argument('--to', dest='to_date', help='End date (YYYY-MM-DD)')
    parser.add_argument('--csv', action='store_true', help='Export CSV')
    args = parser.parse_args()

    config = load_config(args.config)

    from_date = args.from_date or datetime.now().strftime('%Y-%m-%d')
    to_date = args.to_date or datetime.now().strftime('%Y-%m-%d')

    results = compute_weekly_report(config, from_date, to_date)

    if args.csv:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(script_dir, f'report_{from_date}_to_{to_date}.csv')
        export_csv(results, config, path)
    else:
        print_report(results, config)


if __name__ == '__main__':
    main()
