# Printer Statistics Collector

Collects print counters from Zebra and Sato label printers via SNMP, stores them in SQLite, and generates weekly reports.

## Dependencies

```
pysnmp
```

## Setup

```bash
pip install -r requirements.txt
```

Edit `config.json` with your printers:

```json
{
  "db_path": "printer_stats.db",
  "snmp": { "community": "public", "timeout_sec": 3, "retries": 2 },
  "sato_unit_map": { "17": "m" },
  "printers": [
    { "ip": "10.0.1.10", "model": "Sato CL4NX Plus", "location": "Linia 1" },
    { "ip": "10.0.1.11", "model": "Zebra ZT411", "location": "Linia 2" },
    { "ip": "10.0.1.12", "model": "Zebra GX430t", "location": "Linia 3" }
  ]
}
```

## Usage

### Collect data

```bash
python main.py                # collect from all printers
python main.py --verbose      # with SNMP debug output
```

### Generate report

```bash
python report.py --from 2026-09-01 --to 2026-09-30          # weekly report
python report.py --from 2026-09-01 --to 2026-09-30 --csv    # export CSV
```

### SNMP tools

```bash
python snmpget.py 10.0.1.11 1.3.6.1.4.1.10642.1.1.0
python snmpwalk.py 10.0.1.11 1.3.6.1.4.1.10642 --max 50
```

## Automated collection

Schedule two collections per day to cover both shifts:

### Linux (cron)

```bash
crontab -e
```

```
0 5 * * 1-5  cd /path/to/statystki-drukarki && python main.py
0 15 * * 1-5 cd /path/to/statystki-drukarki && python main.py
```

### Windows (Task Scheduler)

```powershell
$action = New-ScheduledTaskAction -Execute "python" -Argument "main.py" -WorkingDirectory "C:\path\to\statystki-drukarki"
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
main.py               # collection orchestrator
report.py             # weekly report generator
db.py                 # SQLite storage
adapters/
  base.py             # abstract adapter with SNMP helpers
  zebra_zt411.py      # Zebra ZT411 (labels + meters)
  zebra_gx430t.py     # Zebra GX430t (meters only)
  sato_cl4nx_plus.py  # Sato CL4NX Plus (meters only)
snmp_client.py        # pysnmp wrapper
snmpget.py            # single OID query tool
snmpwalk.py           # OID subtree walker
config.json           # printer list + SNMP settings
```
