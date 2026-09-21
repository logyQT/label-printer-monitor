# Label Printer Monitor

SNMP print counter collection for Zebra and Sato label printers. SQLite storage. Weekly reports.

## Setup

```bash
pip install -r requirements.txt          # runtime
pip install -r requirements-dev.txt      # + linting, testing, building
python main.py --init
```

Edit `config/config.json`:

```json
{
  "db": { "filename": "printer_stats.db" },
  "log_dir": "logs",
  "snmp": { "community": "public", "timeout_sec": 3, "retries": 2 },
  "collection": { "max_concurrency": 20 },
  "printers": [
    { "ip": "10.0.1.10", "model": "Sato CL4NX Plus", "location": "Linia 1" },
    { "ip": "10.0.1.11", "model": "Zebra ZT411", "location": "Linia 2" },
    { "ip": "10.0.1.12", "model": "Zebra GX430t", "location": "Linia 3" }
  ]
}
```

All commands accept `--config <path>` for alternate configs.

## Usage

```bash
python main.py --collect                    # collect from all printers
python main.py --collect --verbose           # SNMP debug output
python main.py --report                      # current week
python main.py --report --from 2026-09-01 --to 2026-09-30
python main.py --report --csv
python main.py --validate                    # config + schema + DB checks
python main.py --validate --network          # + ping printers over SNMP
python main.py --test
```

Lint: `ruff check .` / `ruff format .` / `mypy`

## Compiled binary

Build with [Nuitka](https://nuitka.net/):

```bash
python build.py            # produces dist/lpm.exe
```

Move `dist/lpm.exe` somewhere permanent, then add that directory to PATH.

**Windows PowerShell:**

```powershell
$currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
[Environment]::SetEnvironmentVariable("Path", "$currentPath;C:\Tools\lpm", "User")
```

Usage is identical, replace `python main.py` with `lpm`:

```bash
lpm --collect
lpm --report --csv
lpm --validate --network
```

## Scheduling

### Linux (cron)

```
0 5 * * 1-5  lpm --collect
0 15 * * 1-5 lpm --collect
```

### Windows (Task Scheduler)

```powershell
$action = New-ScheduledTaskAction -Execute "lpm" -Argument "--collect"
$trigger1 = New-ScheduledTaskTrigger -Daily -At "05:00"
$trigger2 = New-ScheduledTaskTrigger -Daily -At "15:00"
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 2)

Register-ScheduledTask -TaskName "PrinterStatsAM" -Action $action -Trigger $trigger1 -Settings $settings
Register-ScheduledTask -TaskName "PrinterStatsPM" -Action $action -Trigger $trigger2 -Settings $settings
```

## Supported printers

All values stored in **meters** (converted at collection time).

| Printer         | Labels | Odometer |
| --------------- | ------ | -------- |
| Zebra ZT411     | yes    | yes      |
| Zebra GX430t    | -      | yes      |
| Sato CL4NX Plus | -      | yes      |

## Adding an adapter

Copy any file in `src/adapters/`, declare OIDs and converters:

```python
from adapters.base import PrinterAdapter, Metric

class ZebraZD621Adapter(PrinterAdapter):
    model_prefixes = ("zebra zd621",)
    snmp_version = 1
    reachability_oid = "1.3.6.1.4.1.10642.1.1.0"
    metrics = (
        Metric("labels_total", oid="1.3.6.1.4.1.10642.3.1.6.0", convert="int"),
        Metric("meters_total", oid="1.3.6.1.4.1.10642.3.1.1.0", convert="float", unit="cm"),
    )
```

Register in `src/adapters/__init__.py` and add the model to `config/config.json.schema`.

## Database

`snapshots` table:

| Column       | Type    | Description                |
| ------------ | ------- | -------------------------- |
| printer_ip   | TEXT    | Printer IP                 |
| timestamp    | INTEGER | Unix epoch                 |
| labels_total | INTEGER | Labels printed             |
| meters_total | REAL    | Media length in meters     |
| model_name   | TEXT    | Printer model              |

## Project structure

```
main.py                 # entry point
config/
  config.json           # printer list + SNMP settings (gitignored)
  config.example.json   # starter config
  config.json.schema    # JSON Schema
data/
  printer_stats.db      # SQLite database (gitignored)
  backups/              # auto-backup before each run (gitignored)
logs/                   # per-run log files (gitignored)
src/
  adapters/             # printer adapters (declarative SNMP specs)
  converters.py         # unit conversion (cm/in/ft/mm -> meters)
  db.py                 # SQLite storage
  report.py             # weekly report generator
  snmp_client.py        # pysnmp wrapper
  validate.py           # setup validation
build.py                # Nuitka build script
tools/
  snmpget.py            # single OID query (standalone)
  snmpwalk.py           # OID subtree walker (standalone)
```
