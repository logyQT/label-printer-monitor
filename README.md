# Printer Statistics Collector

Collects print counters from Zebra and Sato label printers via SNMP, stores them in SQLite, and generates weekly reports.

## Dependencies

```
pysnmp==7.1.29
jsonschema==4.26.0
```

## Setup

```bash
pip install -r requirements.txt
```

Create your config from the example (only `config/config.json` is read; it is gitignored):

```bash
python main.py --init    # recommended: copies example -> config + creates data/, logs/
```

(Or copy `config/config.example.json` to `config/config.json` by hand with your platform's
copy command, then edit.)

The config links to its schema via a `$schema` key, so editors with JSON Schema support
(VS Code, IntelliJ, etc.) validate and autocomplete it; `python main.py --validate` uses
the same link when choosing which schema to check against.

Then edit `config/config.json` with your printers:

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
python main.py --init                       # first-time setup (config + dirs)
python main.py --collect                    # collect from all printers
python main.py --collect --verbose           # with SNMP debug output
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

### Validate setup

```bash
python main.py --validate            # local checks: config, schema, printers, DB
python main.py --validate --network  # also ping each printer over SNMP
```

Exit code is 0 when everything is OK, 1 when any check fails.

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
| Zebra GX430t | - | yes (cm -> m, in fallback) |
| Sato CL4NX Plus | - | yes (m) |

- Zebra ZT411: vendor OIDs under enterprise 10642
- Zebra GX430t: usage string from 10642.200.17.7.0 - centimeters preferred, inches only as fallback
- Sato: standard Printer MIB (RFC 3805)
- Sato does not expose label counts via SNMP

## Adding a new printer adapter

An adapter is a declarative class: it lists which OIDs to read and how to
convert them. Copy one of the files in `src/adapters/` and declare the specs:

```python
from adapters.base import PrinterAdapter, Metric

class ZebraZD621Adapter(PrinterAdapter):
    model_prefixes = ('zebra zd621',)          # model strings this adapter serves
    snmp_version = 1                           # 0 = SNMPv1, 1 = SNMPv2c
    reachability_oid = '1.3.6.1.4.1.10642.1.1.0'  # poke OID (model name)
    metrics = (
        Metric('labels_total', oid='1.3.6.1.4.1.10642.3.1.6.0', convert='int'),
        Metric('meters_total', oid='1.3.6.1.4.1.10642.3.1.1.0', convert='float',
               unit='cm'),
    )
```

`convert` accepts `'int'`, `'float'`, `'str'` (bytes-decoding), a
`('regex', ...)` usage-string parser, a `('map', ...)` unit-code table, or any
callable. The base class then handles the rest automatically: reachability
(`reachability_oid`), the metric loop, backoff retries, and the standard
`get_counters()` result (`labels_total`, `meters_total`, `meter_unit`,
`model_name`, `reachable`).

Then register the class in `src/adapters/__init__.py` (`ADAPTER_CLASSES`) and
add the model to the `model` enum in `config/config.json.schema` so
`--validate` knows about it.

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
  config.json           # printer list + SNMP settings (gitignored, copy from example)
  config.example.json   # starter config (safe to commit)
  config.json.schema    # JSON Schema for config validation
data/
  printer_stats.db      # SQLite database (gitignored)
  backups/              # auto-backup before each run (gitignored)
logs/                   # per-run log files (gitignored)
src/
  adapters/
    __init__.py         # adapter registry (ADAPTER_CLASSES, prefix matching)
    base.py             # declarative adapter engine (Metric, converters)
    zebra_zt411.py      # Zebra ZT411 (labels + meters)
    zebra_gx430t.py     # Zebra GX430t (meters only, usage string)
    sato_cl4nx_plus.py  # Sato CL4NX Plus (meters only, built-in unit map)
  _tests_/              # unit tests
  db.py                 # SQLite storage layer
  report.py             # weekly report generator (library)
  snmp_client.py        # pysnmp wrapper
  seed_fake_data.py     # test data seeder
  snmpget.py            # single OID query tool
  snmpwalk.py           # OID subtree walker
  validate.py           # setup validation (config, schema, printers, DB)
  run_tests.py          # test runner (standalone)
```
