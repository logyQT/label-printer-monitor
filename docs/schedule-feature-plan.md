# Schedule Feature Plan

## Overview

Add a `--schedule` command to LPM that sets up Windows Task Scheduler to run
`lpm --collect` automatically at configured times. Windows-only, frozen build only.

## Key decisions

- **Frozen build only**: `--schedule` is a no-op when not frozen. If someone runs it
  via `python main.py --schedule`, they get: _"ERROR: --schedule requires a built
  version (lpm.exe)."_
- **`lpm` must be on PATH**: The scheduled task action is `lpm --collect`. If `lpm`
  is not on PATH, we tell the user to add the build output directory to PATH.
- **COM API, not subprocess**: We call Windows Task Scheduler directly via
  `win32com.client` (`Schedule.Service` COM object). No PowerShell, no `schtasks`,
  no subprocess, no shell detection. Clean, structured, native.
- **Single task, multiple triggers**: One `\LPM\LPM_Collect` task with multiple
  daily triggers (one per configured time). No task-per-time sprawl.
- **`\LPM\` task folder**: Avoids requiring admin rights. Non-admin users can create
  tasks in their own scope.
- **No admin prompt**: Try creation, if it fails with access denied, report the
  error clearly. Don't silently elevate.
- **No Linux/cron**: This is a Windows .exe project. If Linux support ever comes,
  it's a separate concern.

---

## CLI

```
lpm --schedule              # Create or update scheduled task
lpm --schedule --remove     # Remove scheduled task
```

## File structure

```
src/
  schedule/
    __init__.py      # public API: install(), remove(), check()
    commands.py      # win32com COM calls (create, query, delete)
    validate.py      # Config validation, stray task detection for --validate
    meta.py          # Constants: task name, folder path
```

All Windows-specific, all self-contained. No cross-platform abstractions needed.

## Config schema addition

Added to `config/config.json.schema`:

```json
"schedule": {
  "type": "object",
  "description": "Windows Task Scheduler settings",
  "required": ["enabled", "times"],
  "properties": {
    "enabled": {
      "type": "boolean",
      "description": "Enable or disable scheduled collection"
    },
    "times": {
      "type": "array",
      "description": "Collection times in HH:MM format (24h)",
      "items": {
        "type": "string",
        "pattern": "^([01]\\d|2[0-3]):[0-5]\\d$"
      },
      "minItems": 1
    },
    "weekdays_only": {
      "type": "boolean",
      "description": "Only run Monday-Friday (default: true)",
      "default": true
    }
  },
  "additionalProperties": false
}
```

`schedule` is NOT in `required` at the top level — it's optional. The tool works
without it. Only `--schedule` and `--validate` care about it.

## Config example

```json
{
  "schedule": {
    "enabled": true,
    "times": ["05:00", "15:00"],
    "weekdays_only": true
  }
}
```

---

## Implementation: `--schedule`

### Flow

```
1. FROZEN?  NO  → "ERROR: --schedule requires a built version (lpm.exe)."
                   "Build with build.py first, then ensure lpm is on PATH."
                   exit(1)

2. shutil.which("lpm")?  NO  → "ERROR: 'lpm' not found on PATH."
                                 "Add the directory containing lpm.exe to your PATH."
                                 exit(1)

3. Load config → check "schedule" section exists.
   NO  → "ERROR: No 'schedule' section in config.json."
          "Add a schedule section, e.g.:"
          "  \"schedule\": { \"enabled\": true, \"times\": [\"05:00\", \"15:00\"], \"weekdays_only\": true }"
          exit(1)

4. Validate schedule config:
   - enabled: must be boolean
   - times: non-empty list, each matches ^([01]\d|2[0-3]):[0-5]\d$
   - weekdays_only: boolean if present, defaults to true

5. Check if task already exists:
   - Query \LPM\ folder via COM API
   - EXISTS → compare triggers against config
     MATCHES → "Schedule is up to date." exit(0)
     DIFFERS → delete + recreate ("Schedule updated")
   - NOT EXISTS → create ("Schedule created")

6. Create task via COM API (see below)
```

### Task creation via COM API

```python
import win32com.client

scheduler = win32com.client.Dispatch("Schedule.Service")
scheduler.Connect()

# Get or create the \LPM\ folder
folder = scheduler.GetFolder("\\LPM")

# Build task XML (triggers, action, settings)
# ...

# Create or update
folder.RegisterTask(
    "LPM_Collect",
    task_xml,
    0,                  # TASK_CREATE_OR_UPDATE
    None,               # current user
    None,               # no password needed for interactive token
    3,                  # TASK_LOGON_INTERACTIVE_TOKEN
)
```

### Task action

```
Execute:  lpm
Argument: --collect
```

That's it. `env.py` handles config resolution at runtime.

### Task triggers

One trigger per entry in `times`:

```xml
<Triggers>
  <CalendarTrigger>
    <StartBoundary>2026-01-01T05:00:00</StartBoundary>
    <Enabled>true</Enabled>
    <ScheduleByDay>
      <DaysInterval>1</DaysInterval>
    </ScheduleByDay>
    <DaysOfWeek>
      <Monday/>
      <Tuesday/>
      <Wednesday/>
      <Thursday/>
      <Friday/>
    </DaysOfWeek>
  </CalendarTrigger>
  <CalendarTrigger>
    <StartBoundary>2026-01-01T15:00:00</StartBoundary>
    ...
  </CalendarTrigger>
</Triggers>
```

When `weekdays_only` is false, omit the `<DaysOfWeek>` element (runs every day).

### Task settings

```xml
<Settings>
  <WakeToRun>true</WakeToRun>
  <DontStopOnIdleEnd>true</DontStopOnIdleEnd>
  <MultipleInstances>IgnoreNew</MultipleInstances>
</Settings>
```

- **WakeToRun**: wakes the machine if it's sleeping (factory machines may sleep overnight)
- **DontStopOnIdleEnd**: keeps running even if the machine goes idle
- **IgnoreNew**: if a previous run is still going, don't start another one

### Task security

- **RunLevel**: Limited (no elevation — SNMP doesn't need admin)
- **LogonType**: Interactive token (runs when user is logged in)
- **No password stored**: Using `TASK_LOGON_INTERACTIVE_TOKEN` avoids password handling

---

## Implementation: `--schedule --remove`

```
1. FROZEN?  NO  → same error as above

2. Connect to Task Scheduler via COM API

3. Try to get folder \LPM\
   NOT FOUND → "No LPM scheduled tasks found." exit(0)

4. Enumerate tasks in \LPM\ folder
   EMPTY → "No LPM scheduled tasks found." exit(0)

5. Delete each task:
   folder.DeleteTask("LPM_Collect", 0)

6. If folder is now empty, optionally remove it

7. Print "Schedule removed."
```

---

## Implementation: `--validate` enhancement

Add a new check to `validate_setup()` (or a separate function called from
`_handle_validate`):

```
[check N] Scheduled tasks

  Case 1: No "schedule" section in config
    - Query \LPM\ folder via COM API
    - Tasks found    → WARN "Stray scheduled task found: \LPM\LPM_Collect
                              Run lpm --schedule --remove to clean it up."
    - No tasks       → OK   "No scheduled tasks configured"

  Case 2: "schedule" section exists in config
    - Query \LPM\ folder via COM API
    - No tasks       → WARN "No scheduled task found. Run lpm --schedule to create one."
    - Task exists:
      - Action path doesn't exist → FAIL "Scheduled task points to missing executable"
      - Triggers don't match config → WARN "Scheduled task differs from config.
                                             Run lpm --schedule to update."
      - Triggers match config    → OK   "Scheduled task matches config"
```

---

## Edge cases handled

| Edge case | How it's handled |
|---|---|
| Not frozen (running from source) | No-op with clear error message |
| `lpm` not on PATH | No-op with clear error message, suggests PATH fix |
| No schedule section in config | Error with example config snippet |
| Invalid times format | Schema validation catches it before COM calls |
| Task already exists, matches | No-op: "Schedule is up to date" |
| Task already exists, differs | Delete + recreate: "Schedule updated" |
| Task exists but exe moved | `--validate` detects missing executable, FAIL |
| Stray task (config removed) | `--validate` detects orphaned task, WARN |
| Admin rights | `\LPM\` folder avoids needing admin. If it fails, clear error |
| Machine sleeping | `WakeToRun` setting wakes the machine |
| Concurrent collection runs | `MultipleInstances=IgnoreNew` — second run skipped |
| Machine reboot mid-collection | WAL mode ensures committed data safe. Partial data for that run |
| Service accounts / headless | Not in scope. `--schedule` requires interactive session |

---

## Testing

| Test | What it covers |
|---|---|
| `test_schedule_not_frozen` | No-op message when not frozen |
| `test_schedule_lpm_not_on_path` | Error when `lpm` not found via `shutil.which` |
| `test_schedule_no_config_section` | Error when `schedule` missing from config |
| `test_validate_times_format` | Valid/invalid `HH:MM` patterns |
| `test_validate_times_empty` | Empty times list rejected |
| `test_validate_weekdays_only_type` | Must be boolean |
| `test_task_creation` | Mock `win32com.client.Dispatch`, verify `RegisterTask` called correctly |
| `test_task_update` | Existing task with different triggers → delete + create |
| `test_task_up_to_date` | Existing task matches config → no-op |
| `test_task_removal` | Enumerate + delete all tasks in `\LPM\` |
| `test_task_removal_no_tasks` | Nothing to remove → clean exit |
| `test_validate_stray_task` | Task exists, no config section → WARN |
| `test_validate_no_task_no_config` | No task, no config section → OK |
| `test_validate_matches` | Task matches config → OK |
| `test_validate_differs` | Task differs from config → WARN |
| `test_validate_orphaned_exe` | Task points to missing exe → FAIL |
| `test_multi_time_triggers` | Multiple times → multiple triggers in one task |
| `test_weekdays_only_flag` | `weekdays_only: true` → only Mon-Fri triggers |

---

## What we're NOT doing

- **Linux/cron** — not needed, this is a Windows .exe project
- **Admin prompting** — `\LPM\` folder avoids needing it
- **PowerShell / subprocess** — COM API is cleaner, no external process
- **Shell detection** — irrelevant, COM API is direct
- **`--config` flag** — removed, config path resolved by `env.py`
- **Service accounts** — out of scope, document if asked
- **Multiple installations** — one task per machine, not per install
- **Email/notification on failure** — out of scope for MVP
- **Log rotation** — existing backup pruning + log file naming handles this
