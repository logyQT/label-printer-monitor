"""Seed 60 printers (30 Zebra, 30 Sato) with 5 days of fake data."""
import random
import db
from datetime import datetime, timezone

random.seed(42)

import os
if os.path.exists('printer_stats.db'):
    os.remove('printer_stats.db')

conn = db.init_db('printer_stats.db')

# Build 60 printers
printers = {}

# 30 Zebra printers
zebra_models = ['ZTC ZT411-300dpi ZPL', 'ZTC ZT230-200dpi ZPL', 'ZTC ZD621-203dpi ZPL', 'ZTC GX430t-203dpi ZPL']
zebra_locs = ['Linia 1', 'Linia 2', 'Linia 3', 'Linia 4', 'Magazyn', 'Biuro', 'Przesylki', 'Nadruk']
for i in range(30):
    ip = f'10.0.{(i // 25) + 1}.{100 + i}'
    printers[ip] = {
        'model': random.choice(zebra_models),
        'unit': 'cm',
        'labels': random.randint(50000, 500000),
        'meters': random.randint(500000, 3000000),
        'location': random.choice(zebra_locs),
    }

# 30 Sato printers
sato_models = ['CL4NX Plus 305dpi', 'CL4NX Plus 203dpi', 'CL6NX Plus 305dpi']
sato_locs = ['Linia 1', 'Linia 2', 'Linia 3', 'Linia 4', 'Magazyn', 'Biuro', 'Ekspedycja', 'Kontrola']
for i in range(30):
    ip = f'10.0.{(i // 25) + 17}.{100 + i}'
    printers[ip] = {
        'model': random.choice(sato_models),
        'unit': 'm',
        'labels': None,
        'meters': random.randint(10000, 200000),
        'location': random.choice(sato_locs),
    }

SHIFTS = [
    {'name': 'Morning', 'start_h': 6, 'end_h': 14},
    {'name': 'Afternoon', 'start_h': 16, 'end_h': 22},
]

print(f'Seeding {len(printers)} printers × 5 days × 2 shifts × 2 snapshots...')

count = 0
for day_offset in range(5):
    base_date = datetime(2026, 9, 13 + day_offset, tzinfo=timezone.utc)
    for shift in SHIFTS:
        start_ep = int(base_date.replace(hour=shift['start_h'], minute=0).timestamp())
        end_ep = int(base_date.replace(hour=shift['end_h'], minute=0).timestamp())
        for ip, p in printers.items():
            # Start snapshot
            m_start = p['meters'] + random.uniform(0, 500)
            db.save_snapshot(conn, ip, p['labels'], m_start, p['unit'], p['model'], timestamp=start_ep)
            count += 1

            # End snapshot
            m_inc = random.uniform(100, 3000)
            l_inc = random.randint(50, 800) if p['labels'] is not None else None
            m_end = m_start + m_inc
            l_end = p['labels'] + l_inc if p['labels'] is not None else None
            db.save_snapshot(conn, ip, l_end, m_end, p['unit'], p['model'], timestamp=end_ep)
            count += 1

            p['labels'] = l_end
            p['meters'] = m_end

db.close_db(conn)
print(f'Done: {count} snapshots')
