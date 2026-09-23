"""Public API for the schedule command: install(), remove(), check()."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from src.schedule.commands import LOGON_TYPE, build_task_xml, connect, delete_task, get_task_xml, register_task
from src.schedule.meta import TASK_PATH
from src.schedule.validate import (
    EXPECTED_ARGS_XML,
    EXPECTED_LOGON_XML,
    extract_task_triggers,
    validate_schedule_config,
)

if TYPE_CHECKING:
    from main import Config

__all__ = ["check", "install", "remove"]


def install(config: Config, verbose: bool = False) -> None:
    """Create or update the \\LPM\\LPM_Collect task from config['schedule']."""
    if "schedule" not in config:
        print("ERROR: No 'schedule' section in config.json.", file=sys.stderr)
        print("Add a schedule section, e.g.:", file=sys.stderr)
        print(
            '  "schedule": { "enabled": true, "times": ["05:00", "15:00"], "weekdays_only": true }',
            file=sys.stderr,
        )
        sys.exit(1)

    errors = validate_schedule_config(config)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)

    schedule = config["schedule"]
    times: list[str] = list(schedule["times"])
    weekdays_only: bool = schedule.get("weekdays_only", True)
    expected_times = sorted(times)

    if verbose:
        print(f"Config: times={times}, weekdays_only={weekdays_only}, enabled={schedule.get('enabled')}")
        print("Connecting to Task Scheduler...")

    scheduler = connect()
    task_xml = get_task_xml(scheduler)

    if task_xml is None:
        if verbose:
            print(f"No existing task at {TASK_PATH} -> creating")
    else:
        task_times, task_weekdays = extract_task_triggers(task_xml)
        mismatches: list[str] = []
        if task_times != expected_times:
            mismatches.append(f"trigger times {task_times or 'none'} != config {expected_times}")
        if task_weekdays != weekdays_only:
            mismatches.append(f"weekdays_only {task_weekdays} != config {weekdays_only}")
        if EXPECTED_LOGON_XML not in task_xml:
            mismatches.append(f"logon type is not {LOGON_TYPE}")
        if EXPECTED_ARGS_XML not in task_xml:
            mismatches.append("action arguments are not 'collect'")
        if not mismatches:
            if verbose:
                print(f"Existing task {TASK_PATH}: matches config")
            print("Schedule is up to date.")
            return
        if verbose:
            print(f"Existing task {TASK_PATH}: {'; '.join(mismatches)} -> re-registering")

    if verbose:
        print("Registering task XML")
    register_task(scheduler, build_task_xml(times, weekdays_only))
    print("Schedule updated" if task_xml is not None else "Schedule created")


def remove(verbose: bool = False) -> None:
    """Delete the \\LPM\\LPM_Collect task (and \\LPM when it ends up empty)."""
    if verbose:
        print("Connecting to Task Scheduler...")
    scheduler = connect()
    if verbose:
        task_xml = get_task_xml(scheduler)
        state = "found -> deleting" if task_xml is not None else "not present"
        print(f"Task {TASK_PATH}: {state}")
    if delete_task(scheduler, verbose=verbose):
        print("Schedule removed.")
    else:
        print("No LPM scheduled tasks found.")


def check(config: Config) -> list[str]:
    """Alias of validate_schedule_config() (kept as public API for tests)."""
    return validate_schedule_config(config)
