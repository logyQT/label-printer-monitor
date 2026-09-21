"""SQLite storage layer for printer statistics.

Handles snapshots, shift deltas, and idempotent inserts.
Zero external dependencies - uses Python's built-in sqlite3.
"""

import sqlite3
from collections.abc import Sequence
from datetime import datetime
from typing import Any, TypedDict

DB_SCHEMA: str = """
CREATE TABLE IF NOT EXISTS snapshots (
    printer_ip    TEXT NOT NULL,
    timestamp     INTEGER NOT NULL,
    labels_total  INTEGER,
    meters_total  REAL,
    meter_unit    TEXT,
    model_name    TEXT,
    PRIMARY KEY (printer_ip, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_snapshots_ip ON snapshots(printer_ip);
CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON snapshots(timestamp);
CREATE INDEX IF NOT EXISTS idx_snapshots_ip_ts ON snapshots(printer_ip, timestamp);
"""


class Snapshot(TypedDict):
    """One stored counter snapshot row (as returned by the query helpers)."""

    printer_ip: str
    timestamp: int
    labels_total: int | None
    meters_total: float | None
    meter_unit: str
    model_name: str


class ShiftDelta(TypedDict):
    """Counter deltas between the snapshots bracketing a shift."""

    labels_delta: int
    meters_delta: float
    start_snapshot: Snapshot
    end_snapshot: Snapshot


class PrinterShiftDelta(ShiftDelta):
    """Shift delta for one printer, keyed by printer IP."""

    printer_ip: str


def init_db(db_path: str) -> sqlite3.Connection:
    """Initialize SQLite database and create tables if needed.

    Args:
        db_path: Path to SQLite database file.

    Returns:
        sqlite3.Connection object.
    """
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(DB_SCHEMA)
    conn.commit()
    return conn


def close_db(conn: sqlite3.Connection | None) -> None:
    """Close database connection."""
    if conn:
        conn.close()


def save_snapshot(
    conn: sqlite3.Connection,
    printer_ip: str,
    labels_total: int | float | None,
    meters_total: int | float | None,
    meter_unit: str,
    model_name: str,
    timestamp: int | str | datetime | None = None,
) -> bool:
    """Save a printer counter snapshot. Idempotent via INSERT OR IGNORE.

    Args:
        conn: SQLite connection.
        printer_ip: Printer IP address.
        labels_total: Total labels printed.
        meters_total: Total meters/length printed.
        meter_unit: Unit for meters (e.g. 'cm', 'mm').
        model_name: Printer model string.
        timestamp: Unix epoch (int), 'YYYY-MM-DD'/'ISO 8601' string, or datetime.
            If None, uses current time rounded to 5min.

    Returns:
        True if inserted, False if duplicate (ignored).
    """
    if timestamp is None:
        timestamp = _round_timestamp(datetime.now())
    elif isinstance(timestamp, datetime):
        timestamp = _round_timestamp(timestamp)

    try:
        cursor = conn.execute(
            """INSERT OR IGNORE INTO snapshots
               (printer_ip, timestamp, labels_total, meters_total, meter_unit,
                model_name)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (printer_ip, timestamp, labels_total, meters_total, meter_unit, model_name),
        )
        conn.commit()
        return cursor.rowcount > 0
    except sqlite3.Error:
        conn.rollback()
        raise


def get_latest_snapshot(conn: sqlite3.Connection, printer_ip: str) -> Snapshot | None:
    """Get the most recent snapshot for a printer.

    Args:
        conn: SQLite connection.
        printer_ip: Printer IP address.

    Returns:
        Dict with snapshot data or None if no snapshots exist.
    """
    cursor = conn.execute(
        """SELECT printer_ip, timestamp, labels_total, meters_total,
                  meter_unit, model_name
           FROM snapshots
           WHERE printer_ip = ?
           ORDER BY timestamp DESC
           LIMIT 1""",
        (printer_ip,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return _row_to_dict(row)


def get_snapshot_at(conn: sqlite3.Connection, printer_ip: str, timestamp: int | str) -> Snapshot | None:
    """Get the snapshot closest to a specific timestamp.

    Args:
        conn: SQLite connection.
        printer_ip: Printer IP.
        timestamp: Target timestamp (Unix epoch int or 'YYYY-MM-DD' string).

    Returns:
        Dict with snapshot data or None.
    """
    cursor = conn.execute(
        """SELECT printer_ip, timestamp, labels_total, meters_total,
                  meter_unit, model_name
           FROM snapshots
           WHERE printer_ip = ? AND timestamp <= ?
           ORDER BY timestamp DESC
           LIMIT 1""",
        (printer_ip, timestamp),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return _row_to_dict(row)


def get_shift_delta(
    conn: sqlite3.Connection, printer_ip: str, shift_start: int | str, shift_end: int | str
) -> ShiftDelta | None:
    """Calculate labels and meters printed during a shift.

    Args:
        conn: SQLite connection.
        printer_ip: Printer IP.
        shift_start: Start timestamp (Unix epoch int or ISO 8601 string).
        shift_end: End timestamp (Unix epoch int or ISO 8601 string).

    Returns:
        Dict with delta values or None if data missing:
            labels_delta (int): Labels printed in shift.
            meters_delta (float): Meters printed in shift.
            start_snapshot (dict): Snapshot at shift start.
            end_snapshot (dict): Snapshot at shift end.
    """
    start_snap = get_snapshot_at(conn, printer_ip, shift_start)
    end_snap = get_snapshot_at(conn, printer_ip, shift_end)

    if start_snap is None or end_snap is None:
        return None

    labels_start = start_snap["labels_total"] or 0
    labels_end = end_snap["labels_total"] or 0
    meters_start = start_snap["meters_total"] or 0
    meters_end = end_snap["meters_total"] or 0

    return {
        "labels_delta": labels_end - labels_start,
        "meters_delta": meters_end - meters_start,
        "start_snapshot": start_snap,
        "end_snapshot": end_snap,
    }


def get_all_printers_latest(conn: sqlite3.Connection) -> list[Snapshot]:
    """Get latest snapshot for all printers.

    Args:
        conn: SQLite connection.

    Returns:
        List of dicts with latest snapshot per printer.
    """
    cursor = conn.execute(
        """SELECT s.printer_ip, s.timestamp, s.labels_total, s.meters_total,
                  s.meter_unit, s.model_name
           FROM snapshots s
           INNER JOIN (
               SELECT printer_ip, MAX(timestamp) as max_ts
               FROM snapshots
               GROUP BY printer_ip
           ) latest ON s.printer_ip = latest.printer_ip
                    AND s.timestamp = latest.max_ts"""
    )
    return [_row_to_dict(row) for row in cursor.fetchall()]


def get_printers_by_shift(
    conn: sqlite3.Connection, shift_start: int | str, shift_end: int | str
) -> list[PrinterShiftDelta]:
    """Get shift deltas for all printers.

    Args:
        conn: SQLite connection.
        shift_start: Shift start timestamp (ISO 8601).
        shift_end: Shift end timestamp (ISO 8601).

    Returns:
        List of dicts with shift delta data.
    """
    cursor = conn.execute("""SELECT DISTINCT printer_ip FROM snapshots""")
    printer_ips = [row[0] for row in cursor.fetchall()]

    results: list[PrinterShiftDelta] = []
    for ip in printer_ips:
        delta = get_shift_delta(conn, ip, shift_start, shift_end)
        if delta is not None:
            results.append({"printer_ip": ip, **delta})
    return results


def get_history(
    conn: sqlite3.Connection,
    printer_ip: str,
    start_date: int | str | None = None,
    end_date: int | str | None = None,
) -> list[Snapshot]:
    """Get snapshot history for a printer.

    Args:
        conn: SQLite connection.
        printer_ip: Printer IP.
        start_date: Start date as epoch int or 'YYYY-MM-DD' string.
        end_date: End date as epoch int or 'YYYY-MM-DD' string.

    Returns:
        List of snapshot dicts.
    """
    query = "SELECT * FROM snapshots WHERE printer_ip = ?"
    params: list[str | int] = [printer_ip]
    if start_date:
        params.append(_to_epoch(start_date))
        query += " AND timestamp >= ?"
    if end_date:
        # end of day: add 86399 seconds
        params.append(_to_epoch(end_date) + 86399)
        query += " AND timestamp <= ?"
    query += " ORDER BY timestamp ASC"

    cursor = conn.execute(query, params)
    return [_row_to_dict(row) for row in cursor.fetchall()]


def _to_epoch(date_val: int | str | datetime) -> int:
    """Convert a value to Unix epoch int.

    Args:
        date_val: int (already epoch), 'YYYY-MM-DD' string, or datetime.

    Returns:
        Unix epoch as int.
    """
    if isinstance(date_val, int):
        return date_val
    if isinstance(date_val, datetime):
        return int(date_val.timestamp())
    # 'YYYY-MM-DD' string
    return int(datetime.fromisoformat(date_val).timestamp())


def _round_timestamp(dt: datetime, interval_minutes: int = 5) -> int:
    """Round a datetime to the nearest interval and return Unix epoch (int).

    Args:
        dt: datetime object.
        interval_minutes: Rounding interval in minutes.

    Returns:
        Unix epoch as int (seconds since 1970-01-01 UTC).
    """
    minute = (dt.minute // interval_minutes) * interval_minutes
    rounded = dt.replace(minute=minute, second=0, microsecond=0)
    return int(rounded.timestamp())


def _row_to_dict(row: Sequence[Any]) -> Snapshot:
    """Convert a database row to a dictionary."""
    return {
        "printer_ip": row[0],
        "timestamp": row[1],
        "labels_total": row[2],
        "meters_total": row[3],
        "meter_unit": row[4],
        "model_name": row[5],
    }
