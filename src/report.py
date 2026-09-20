"""Weekly printer statistics report.

Library module — called via ``main.py --report``.
"""

import csv
import os
from datetime import UTC, datetime, timedelta
from typing import Any, TypedDict

import db

_HERE: str = os.path.dirname(os.path.abspath(__file__))
_ROOT: str = os.path.dirname(_HERE)


class WeekRow(TypedDict):
    """One printer's delta for one week."""

    ip: str
    model: str
    labels: int | None
    meters: int | float | None


# week label ("2026-W37") -> list of printer rows
Weeks = dict[str, list[WeekRow]]


def _to_epoch(date_str: str) -> int:
    return int(datetime.fromisoformat(date_str).timestamp())


def _convert_to_meters(value: int | float | None, unit: str) -> int | float | None:
    if value is None:
        return None
    return {"cm": value / 100.0, "m": value, "mm": value / 1000.0, "in": value * 0.0254}.get(
        unit, value
    )


def _week_label(epoch: int) -> str:
    dt = datetime.fromtimestamp(epoch, tz=UTC)
    monday = dt - timedelta(days=dt.weekday())
    iso = monday.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _week_dates(label: str) -> tuple[str, str]:
    """Return (monday_str, sunday_str) for a week label like '2026-W37'."""
    year, week = label.split("-W")
    monday = datetime.fromisocalendar(int(year), int(week), 1)
    sunday = monday + timedelta(days=6)
    return monday.strftime("%Y-%m-%d"), sunday.strftime("%Y-%m-%d")


def compute_weekly(config: dict[str, Any], from_date: str, to_date: str) -> Weeks:
    """Returns {week_label: [{ip, model, labels, meters}, ...]}"""
    filename = config.get("db", {}).get("filename", "printer_stats.db")
    db_path = filename if filename == ":memory:" else os.path.join(_ROOT, "data", filename)
    conn = db.init_db(db_path)
    from_ep = _to_epoch(from_date)
    to_ep = _to_epoch(to_date) + 86399

    ips = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT printer_ip FROM snapshots WHERE timestamp >= ? AND timestamp <= ?",
            (from_ep, to_ep),
        ).fetchall()
    ]

    # Model lookup
    model_map: dict[str, str] = {p["ip"]: p["model"] for p in config.get("printers", [])}
    for ip in ips:
        if ip not in model_map:
            row = conn.execute(
                "SELECT model_name FROM snapshots WHERE printer_ip = ? AND model_name IS NOT NULL LIMIT 1",
                (ip,),
            ).fetchone()
            if row:
                model_map[ip] = row[0]

    weeks: Weeks = {}
    for ip in ips:
        model = model_map.get(ip, "Unknown")
        rows = conn.execute(
            """SELECT timestamp, labels_total, meters_total, meter_unit
               FROM snapshots WHERE printer_ip = ? AND timestamp >= ? AND timestamp <= ?
               ORDER BY timestamp""",
            (ip, from_ep, to_ep),
        ).fetchall()
        if not rows:
            continue

        # Group by week
        by_week: dict[str, dict[str, tuple[Any, Any, Any]]] = {}
        for row in rows:
            ts, labels, meters, unit = row[0], row[1], row[2], row[3]
            wk = _week_label(ts)
            by_week.setdefault(
                wk, {"first": (labels, meters, unit), "last": (labels, meters, unit)}
            )
            by_week[wk]["last"] = (labels, meters, unit)

        for wk, data in by_week.items():
            first, last = data["first"], data["last"]
            unit = first[2] or last[2] or "unknown"

            labels_d = (last[0] - first[0]) if first[0] is not None and last[0] is not None else None
            m_s = _convert_to_meters(first[1], unit)
            m_e = _convert_to_meters(last[1], unit)
            meters_d = (m_e - m_s) if m_s is not None and m_e is not None else None

            weeks.setdefault(wk, []).append(
                {
                    "ip": ip,
                    "model": model,
                    "labels": labels_d,
                    "meters": meters_d,
                }
            )

    db.close_db(conn)
    return weeks


def print_report(weeks: Weeks, config: dict[str, Any]) -> None:
    model_full: dict[str, str] = {p["ip"]: p["model"] for p in config.get("printers", [])}
    for printers in weeks.values():
        for r in printers:
            if r["ip"] not in model_full:
                model_full[r["ip"]] = r["model"]

    has_labels = any(r["labels"] is not None for printers in weeks.values() for r in printers)

    for wk in sorted(weeks.keys()):
        printers = weeks[wk]
        mon, sun = _week_dates(wk)

        print(f"\n{'=' * 80}")
        print(f"  WEEK {wk}  ({mon} - {sun})")
        print(f"{'=' * 80}")

        if has_labels:
            print(f"  {'IP':<15} {'Model':<22} {'Labels':>10} {'Meters (m)':>12}")
            print(f"  {'-' * 65}")
        else:
            print(f"  {'IP':<15} {'Model':<22} {'Meters (m)':>12}")
            print(f"  {'-' * 55}")

        t_labels = 0
        t_meters = 0.0
        for r in sorted(printers, key=lambda x: x["ip"]):
            model = model_full.get(r["ip"], r["model"])
            if has_labels:
                labels_col = f"{r['labels']:,}" if r["labels"] is not None else "-"
                m = f"{r['meters']:,.1f}" if r["meters"] is not None else "-"
                print(f"  {r['ip']:<15} {model:<22} {labels_col:>10} {m:>12}")
            else:
                m = f"{r['meters']:,.1f}" if r["meters"] is not None else "-"
                print(f"  {r['ip']:<15} {model:<22} {m:>12}")
            if r["labels"] is not None:
                t_labels += int(r["labels"])
            if r["meters"] is not None:
                t_meters += r["meters"]

        print(f"  {'-' * 65 if has_labels else '-' * 55}")
        if has_labels:
            print(f"  {'WEEK TOTAL':<38} {t_labels:>10,} {t_meters:>12,.1f}")
        else:
            print(f"  {'WEEK TOTAL':<38} {t_meters:>12,.1f}")

    # Grand total
    all_labels = sum(r["labels"] or 0 for printers in weeks.values() for r in printers)
    all_meters = sum(r["meters"] or 0 for printers in weeks.values() for r in printers)

    print(f"\n{'=' * 80}")
    print("  GRAND TOTAL")
    print(f"{'=' * 80}")
    if has_labels:
        print(f"  {'':38} {all_labels:>10,} {all_meters:>12,.1f}")
    else:
        print(f"  {'':38} {all_meters:>12,.1f}")
    print()


def export_csv(weeks: Weeks, config: dict[str, Any], path: str) -> None:
    model_full: dict[str, str] = {p["ip"]: p["model"] for p in config.get("printers", [])}
    for printers in weeks.values():
        for r in printers:
            if r["ip"] not in model_full:
                model_full[r["ip"]] = r["model"]

    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Week", "IP", "Model", "Labels Delta", "Meters Delta (m)"])
        for wk in sorted(weeks.keys()):
            for r in sorted(weeks[wk], key=lambda x: x["ip"]):
                w.writerow(
                    [
                        wk,
                        r["ip"],
                        model_full.get(r["ip"], r["model"]),
                        r["labels"] if r["labels"] is not None else "",
                        round(r["meters"], 1) if r["meters"] is not None else "",
                    ]
                )
    print(f"CSV saved to: {path}")