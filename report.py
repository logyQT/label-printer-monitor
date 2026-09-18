"""Weekly printer statistics report.

Usage:
    python report.py                          # current week
    python report.py --from 2026-09-01 --to 2026-09-30
    python report.py --csv
"""

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import db


def load_config(path='config.json'):
    if not os.path.exists(path):
        print(f'ERROR: Config not found: {path}', file=sys.stderr)
        sys.exit(2)
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def _to_epoch(date_str):
    return int(datetime.fromisoformat(date_str).timestamp())


def _convert_to_meters(value, unit):
    if value is None:
        return None
    return {'cm': value / 100.0, 'm': value, 'mm': value / 1000.0, 'in': value * 0.0254}.get(unit, value)


def _week_label(epoch):
    dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
    monday = dt - timedelta(days=dt.weekday())
    iso = monday.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _week_dates(label):
    """Return (monday_str, sunday_str) for a week label like '2026-W37'."""
    year, week = label.split('-W')
    monday = datetime.fromisocalendar(int(year), int(week), 1)
    sunday = monday + timedelta(days=6)
    return monday.strftime('%Y-%m-%d'), sunday.strftime('%Y-%m-%d')


def compute_weekly(config, from_date, to_date):
    """Returns {week_label: [{ip, model, labels, meters}, ...]}"""
    conn = db.init_db(config['db_path'])
    from_ep = _to_epoch(from_date)
    to_ep = _to_epoch(to_date) + 86399

    ips = [r[0] for r in conn.execute(
        "SELECT DISTINCT printer_ip FROM snapshots WHERE timestamp >= ? AND timestamp <= ?",
        (from_ep, to_ep)
    ).fetchall()]

    # Model lookup
    model_map = {p['ip']: p['model'] for p in config.get('printers', [])}
    for ip in ips:
        if ip not in model_map:
            row = conn.execute(
                "SELECT model_name FROM snapshots WHERE printer_ip = ? AND model_name IS NOT NULL LIMIT 1",
                (ip,)
            ).fetchone()
            if row:
                model_map[ip] = row[0]

    weeks = {}
    for ip in ips:
        model = model_map.get(ip, 'Unknown')
        rows = conn.execute(
            """SELECT timestamp, labels_total, meters_total, meter_unit
               FROM snapshots WHERE printer_ip = ? AND timestamp >= ? AND timestamp <= ?
               ORDER BY timestamp""",
            (ip, from_ep, to_ep)
        ).fetchall()
        if not rows:
            continue

        # Group by week
        by_week = {}
        for ts, labels, meters, unit in rows:
            wk = _week_label(ts)
            by_week.setdefault(wk, {'first': (labels, meters, unit), 'last': (labels, meters, unit)})
            by_week[wk]['last'] = (labels, meters, unit)

        for wk, data in by_week.items():
            fl, ll = data['first'], data['last']
            unit = fl[2] or ll[2] or 'unknown'

            labels_d = (ll[0] - fl[0]) if fl[0] is not None and ll[0] is not None else None
            m_s = _convert_to_meters(fl[1], unit)
            m_e = _convert_to_meters(ll[1], unit)
            meters_d = (m_e - m_s) if m_s is not None and m_e is not None else None

            weeks.setdefault(wk, []).append({
                'ip': ip, 'model': model, 'labels': labels_d, 'meters': meters_d,
            })

    db.close_db(conn)
    return weeks


def print_report(weeks, config):
    model_full = {p['ip']: p['model'] for p in config.get('printers', [])}
    for printers in weeks.values():
        for r in printers:
            if r['ip'] not in model_full:
                model_full[r['ip']] = r['model']

    has_labels = any(r['labels'] is not None for printers in weeks.values() for r in printers)

    for wk in sorted(weeks.keys()):
        printers = weeks[wk]
        mon, sun = _week_dates(wk)

        print(f"\n{'='*80}")
        print(f"  WEEK {wk}  ({mon} - {sun})")
        print(f"{'='*80}")

        if has_labels:
            print(f"  {'IP':<15} {'Model':<22} {'Labels':>10} {'Meters (m)':>12}")
            print(f"  {'-'*65}")
        else:
            print(f"  {'IP':<15} {'Model':<22} {'Meters (m)':>12}")
            print(f"  {'-'*55}")

        t_labels = 0
        t_meters = 0.0
        for r in sorted(printers, key=lambda x: x['ip']):
            model = model_full.get(r['ip'], r['model'])
            if has_labels:
                l = f"{r['labels']:,}" if r['labels'] is not None else '-'
                m = f"{r['meters']:,.1f}" if r['meters'] is not None else '-'
                print(f"  {r['ip']:<15} {model:<22} {l:>10} {m:>12}")
            else:
                m = f"{r['meters']:,.1f}" if r['meters'] is not None else '-'
                print(f"  {r['ip']:<15} {model:<22} {m:>12}")
            if r['labels'] is not None:
                t_labels += r['labels']
            if r['meters'] is not None:
                t_meters += r['meters']

        print(f"  {'-'*65 if has_labels else '-'*55}")
        if has_labels:
            print(f"  {'WEEK TOTAL':<38} {t_labels:>10,} {t_meters:>12,.1f}")
        else:
            print(f"  {'WEEK TOTAL':<38} {t_meters:>12,.1f}")

    # Grand total
    all_labels = sum(r['labels'] or 0 for printers in weeks.values() for r in printers)
    all_meters = sum(r['meters'] or 0 for printers in weeks.values() for r in printers)

    print(f"\n{'='*80}")
    print(f"  GRAND TOTAL")
    print(f"{'='*80}")
    if has_labels:
        print(f"  {'':38} {all_labels:>10,} {all_meters:>12,.1f}")
    else:
        print(f"  {'':38} {all_meters:>12,.1f}")
    print()


def export_csv(weeks, config, path):
    model_full = {p['ip']: p['model'] for p in config.get('printers', [])}
    for printers in weeks.values():
        for r in printers:
            if r['ip'] not in model_full:
                model_full[r['ip']] = r['model']

    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['Week', 'IP', 'Model', 'Labels Delta', 'Meters Delta (m)'])
        for wk in sorted(weeks.keys()):
            for r in sorted(weeks[wk], key=lambda x: x['ip']):
                w.writerow([
                    wk, r['ip'], model_full.get(r['ip'], r['model']),
                    r['labels'] if r['labels'] is not None else '',
                    round(r['meters'], 1) if r['meters'] is not None else '',
                ])
    print(f"CSV saved to: {path}")


def main():
    parser = argparse.ArgumentParser(description='Weekly printer statistics')
    parser.add_argument('--config', default='config.json')
    parser.add_argument('--from', dest='from_date', help='Start date (YYYY-MM-DD)')
    parser.add_argument('--to', dest='to_date', help='End date (YYYY-MM-DD)')
    parser.add_argument('--csv', action='store_true', help='Export CSV')
    args = parser.parse_args()

    config = load_config(args.config)
    from_date = args.from_date or datetime.now().strftime('%Y-%m-%d')
    to_date = args.to_date or datetime.now().strftime('%Y-%m-%d')

    weeks = compute_weekly(config, from_date, to_date)

    if args.csv:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            f'report_{from_date}_to_{to_date}.csv')
        export_csv(weeks, config, path)
    else:
        print_report(weeks, config)


if __name__ == '__main__':
    main()
