# Printer Statistics Collector

Collects print counters from Zebra and Sato label printers via SNMP, stores them in SQLite, and generates weekly reports.

## Setup

```bash
pip install -r requirements.txt
```

Edit `config.json` with your printers:

```json
{
  "db_path": "printer_stats.db",
  "snmp": { "community": "public", "timeout_sec": 3, "retries": 2 },
  "shifts": [
    { "name": "Morning", "start": "06:00", "end": "14:00" },
    { "name": "Afternoon", "start": "16:00", "end": "22:00" }
  ],
  "printers": [
    { "ip": "10.0.1.10", "model": "Sato CL4NX Plus", "location": "Linia 1" },
    { "ip": "10.0.1.11", "model": "Zebra ZT411", "location": "Linia 2" }
  ]
}
```

## Usage

### Collect data

```bash
python main.py --collect              # one-shot collection
python main.py --collect --verbose    # with SNMP debug output
```

### Shift-based collection

```bash
python main.py --shift start          # record shift start snapshot
python main.py --shift end            # record shift end + log delta
```

### Generate report

```bash
python report.py --from 2026-09-01 --to 2026-09-30    # weekly report
python report.py --from 2026-09-01 --to 2026-09-30 --csv  # export CSV
```

### SNMP tools

```bash
python snmpget.py 10.0.1.11 1.3.6.1.4.1.10642.1.1.0
python snmpwalk.py 10.0.1.11 1.3.6.1.4.1.10642 --max 50
```

## Automated collection

### Linux (cron)

Run every 30 minutes during shifts:

```bash
crontab -e
```

```
*/30 6-13 * * 1-5  cd /path/to/statystki-drukarki && python main.py --collect
*/30 16-21 * * 1-5 cd /path/to/statystki-drukarki && python main.py --collect
```

Or with shift-aware collection (records start/end automatically):

```
55 5  * * 1-5  cd /path/to/statystki-drukarki && python main.py --shift start
5  14 * * 1-5  cd /path/to/statystki-drukarki && python main.py --shift end
55 15 * * 1-5  cd /path/to/statystki-drukarki && python main.py --shift start
5  22 * * 1-5  cd path/to/statystki-drukarki && python main.py --shift end
```

### Windows (Task Scheduler)

Create a scheduled task via PowerShell:

```powershell
# Collect every 30 min on weekdays 06:00-22:00
$action = New-ScheduledTaskAction -Execute "python" -Argument "main.py --collect" -WorkingDirectory "C:\path\to\statystki-drukarki"
$trigger = New-ScheduledTaskTrigger -Daily -At "06:00"
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -MultipleInstances IgnoreNew

# Register with repetition
Register-ScheduledTask -TaskName "PrinterStats" -Action $action -Trigger $trigger -Settings $settings
```

Or use `schtasks`:

```cmd
schtasks /create /tn "PrinterStats" /tr "python main.py --collect" /sc daily /st 06:00 /ri 30 /du 16:00 /f
```

## What gets collected

| Printer | Labels | Meters |
|---------|--------|--------|
| Zebra   | ✓      | ✓ (cm → m) |
| Sato    | -      | ✓ (m)  |

- Zebra: vendor OIDs under enterprise 10642
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
main.py           # collection orchestrator
report.py         # weekly report generator
db.py             # SQLite storage
adapters/
  base.py         # abstract adapter with SNMP helpers
  zebra.py        # Zebra (enterprise 10642 OIDs)
  sato.py         # Sato (Printer MIB OIDs)
snmp_client.py    # pysnmp wrapper
snmpget.py        # single OID query tool
snmpwalk.py       # OID subtree walker
config.json       # printer list + SNMP settings
```
