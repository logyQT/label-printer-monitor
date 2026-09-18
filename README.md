# Printer Statistics Collector

Collects print counters from Zebra and Sato label printers via SNMP, stores them in SQLite, and generates weekly reports.

## Dependencies

```
pysnmp==7.1.29
```

## Setup

```bash
pip install -r requirements.txt
```

Edit `config/config.json` with your printers:

```json
{
  "db": {
    "filename": "printer_stats.db"
  },
  "log_dir": "logs",
  "snmp": {
    "community": "public",
    "timeout_sec": 3,
    "retries": 2
  },
  "printers": [
    { "ip": "10.0.1.10", "model": "Sato CL4NX Plus", "location": "Linia 1" },
    { "ip": "10.0.1.11", "model": "Zebra ZT411", "location": "Linia 2" },
    { "ip": "10.0.1.12", "model": "Zebra GX430t", "location": "Linia 3" }
  ]
}
```

## Usage

`main.py` is the single entry point. Run with no flags to see help.

### Collect data

```bash
python main.py --collect            # collect from all printers
python main.py --collect --verbose  # with SNMP debug output
```

### Generate report

```bash
python main.py --report                                    # current week
python main.py --report --from 2026-09-01 --to 2026-09-30  # date range
python main.py --report --csv                              # export CSV
```

### Run tests

```bash
python main.py --test
```

## Automated collection

Schedule two collections per day to cover both shifts:

### Linux (cron)

```bash
crontab -e
```

```
0 5 * * 1-5  cd /path/to/statystki-drukarki && python main.py --collect
0 15 * * 1-5 cd /path/to/statystki-drukarki && python main.py --collect
```

### Windows (Task Scheduler)

```powershell
$action = New-ScheduledTaskAction -Execute "python" -Argument "main.py --collect" -WorkingDirectory "C:\path\to\statystki-drukarki"
$trigger1 = New-ScheduledTaskTrigger -Daily -At "05:00"
$trigger2 = New-ScheduledTaskTrigger -Daily -At "15:00"
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 2)

Register-ScheduledTask -TaskName "PrinterStatsAM" -Action $action -Trigger $trigger1 -Settings $settings
Register-ScheduledTask -TaskName "PrinterStatsPM" -Action $action -Trigger $trigger2 -Settings $settings
```

## What gets collected

| Printer | Labels | Odometer |
|---------|--------|----------|
| Zebra ZT411 | yes | yes (cm -> m) |
| Zebra GX430t | - | yes (in -> m) |
| Sato CL4NX Plus | - | yes (m) |

- Zebra ZT411: vendor OIDs under enterprise 10642
- Zebra GX430t: usage string from 10642.200.17.7.0
- Sato: standard Printer MIB (RFC 3805)
- Sato does not expose label counts via SNMP

## Database

Single `snapshots` table:

| Column | Type | Description |
|--------|------|-------------|
| printer_ip | TEXT | Printer IP address |
| timestamp | INTEGER | Unix epoch |
| labels_total | INTEGER | Labels printed this period |
| meters_total | REAL | Media length (raw unit) |
| meter_unit | TEXT | Unit from printer (cm/m) |
| model_name | TEXT | Printer model |

## Project structure

```
main.py                 # single entry point (--collect / --report / --test)
requirements.txt        # pinned dependencies
config/
  config.json           # printer list + SNMP settings (gitignored)
  config.json.schema    # JSON Schema for config validation
data/
  printer_stats.db      # SQLite database (gitignored)
  backups/              # auto-backup before each run (gitignored)
logs/                   # per-run log files (gitignored)
src/
  adapters/
    __init__.py         # adapter registry
    base.py             # abstract adapter with SNMP helpers
    zebra_zt411.py      # Zebra ZT411 (labels + meters)
    zebra_gx430t.py     # Zebra GX430t (meters only)
    sato_cl4nx_plus.py  # Sato CL4NX Plus (meters only, built-in unit map)
  _tests_/              # unit tests
  db.py                 # SQLite storage layer
  report.py             # weekly report generator (library)
  snmp_client.py        # pysnmp wrapper
  discover_units.py     # one-time meter unit detection tool
  seed_fake_data.py     # test data seeder
  snmpget.py            # single OID query tool
  snmpwalk.py           # OID subtree walker
  run_tests.py          # test runner (standalone)
```
