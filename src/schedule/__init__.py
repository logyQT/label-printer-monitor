"""Public API for the --schedule feature: install(), remove(), check()."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from src.schedule.commands import build_task_xml, connect, delete_task, get_task_xml, register_task
from src.schedule.validate import (
    EXPECTED_LOGON_XML,
    extract_task_triggers,
    validate_schedule_config,
)

if TYPE_CHECKING:
    from main import Config

__all__ = ["check", "install", "remove"]


def install(config: Config) -> None:
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

    scheduler = connect()
    task_xml = get_task_xml(scheduler)

    if task_xml is not None:
        task_times, task_weekdays = extract_task_triggers(task_xml)
        if task_times == sorted(times) and task_weekdays == weekdays_only and EXPECTED_LOGON_XML in task_xml:
            print("Schedule is up to date.")
            return

    register_task(scheduler, build_task_xml(times, weekdays_only))
    print("Schedule updated" if task_xml is not None else "Schedule created")


def remove() -> None:
    """Delete the \\LPM\\LPM_Collect task (and \\LPM when it ends up empty)."""
    if delete_task(connect()):
        print("Schedule removed.")
    else:
        print("No LPM scheduled tasks found.")


def check(config: Config) -> list[str]:
    """Alias of validate_schedule_config() (kept as public API for tests)."""
    return validate_schedule_config(config)
