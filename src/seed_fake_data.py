"""Seed 60 printers (30 Zebra, 30 Sato) with 5 days of fake data."""

import os
import random
import sqlite3
from datetime import UTC, datetime
from typing import TypedDict

import db


class _PrinterSeed(TypedDict):
    """Mutable per-printer state while seeding."""

    model: str
    unit: str
    labels: int | None
    meters: int | float
    location: str


class _Shift(TypedDict):
    name: str
    start_h: int
    end_h: int


random.seed(42)

_here: str = os.path.dirname(os.path.abspath(__file__))
_data: str = os.path.join(os.path.dirname(_here), "data")
_db_path: str = os.path.join(_data, "printer_stats.db")

if os.path.exists(_db_path):
    os.remove(_db_path)

conn: sqlite3.Connection = db.init_db(_db_path)

# Build 60 printers
printers: dict[str, _PrinterSeed] = {}

# 30 Zebra printers
zebra_models: list[str] = [
    "ZTC ZT411-300dpi ZPL",
    "ZTC ZT230-200dpi ZPL",
    "ZTC ZD621-203dpi ZPL",
    "ZTC GX430t-203dpi ZPL",
]
zebra_locs: list[str] = [
    "Linia 1",
    "Linia 2",
    "Linia 3",
    "Linia 4",
    "Magazyn",
    "Biuro",
    "Przesylki",
    "Nadruk",
]
for i in range(30):
    ip = f"10.0.{(i // 25) + 1}.{100 + i}"
    printers[ip] = {
        "model": random.choice(zebra_models),
        "unit": "cm",
        "labels": random.randint(50000, 500000),
        "meters": random.randint(500000, 3000000),
        "location": random.choice(zebra_locs),
    }

# 30 Sato printers
sato_models: list[str] = ["CL4NX Plus 305dpi", "CL4NX Plus 203dpi", "CL6NX Plus 305dpi"]
sato_locs: list[str] = [
    "Linia 1",
    "Linia 2",
    "Linia 3",
    "Linia 4",
    "Magazyn",
    "Biuro",
    "Ekspedycja",
    "Kontrola",
]
for i in range(30):
    ip = f"10.0.{(i // 25) + 17}.{100 + i}"
    printers[ip] = {
        "model": random.choice(sato_models),
        "unit": "m",
        "labels": None,
        "meters": random.randint(10000, 200000),
        "location": random.choice(sato_locs),
    }

SHIFTS: list[_Shift] = [
    {"name": "Morning", "start_h": 6, "end_h": 14},
    {"name": "Afternoon", "start_h": 16, "end_h": 22},
]

print(f"Seeding {len(printers)} printers × 5 days × 2 shifts × 2 snapshots...")

count: int = 0
for day_offset in range(5):
    base_date = datetime(2026, 9, 13 + day_offset, tzinfo=UTC)
    for shift in SHIFTS:
        start_ep = int(base_date.replace(hour=shift["start_h"], minute=0).timestamp())
        end_ep = int(base_date.replace(hour=shift["end_h"], minute=0).timestamp())
        for ip, p in printers.items():
            # Start snapshot
            m_start = p["meters"] + random.uniform(0, 500)
            db.save_snapshot(
                conn, ip, p["labels"], m_start, p["unit"], p["model"], timestamp=start_ep
            )
            count += 1

            # End snapshot
            m_inc = random.uniform(100, 3000)
            labels_cur = p["labels"]
            l_inc = random.randint(50, 800) if labels_cur is not None else None
            m_end = m_start + m_inc
            l_end = labels_cur + l_inc if labels_cur is not None and l_inc is not None else None
            db.save_snapshot(conn, ip, l_end, m_end, p["unit"], p["model"], timestamp=end_ep)
            count += 1

            p["labels"] = l_end
            p["meters"] = m_end

db.close_db(conn)
print(f"Done: {count} snapshots")