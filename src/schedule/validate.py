r"""Schedule config validation and the Task Scheduler check for the validate command.

Implements the plan's Cases 1 & 2, plus a disabled guard:

- Case 0: "schedule" exists but enabled=false -> stray-task detection only.
- Case 1: no "schedule" section in config -> stray-task detection.
- Case 2: "schedule" section exists -> task presence, executable, triggers.
"""

from __future__ import annotations

import json
import re
import shutil
from typing import TYPE_CHECKING, Any

from src.schedule.commands import LOGON_TYPE, connect, get_task_xml
from src.validate import FAIL, OK, WARN, Issue

if TYPE_CHECKING:
    from main import Config

TIME_PATTERN: re.Pattern[str] = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_TRIGGER_TIME: re.Pattern[str] = re.compile(r"<StartBoundary>[^T]+T(\d\d:\d\d):")
# What build_task_xml() writes for the principal; a task registered before the
# S4U change has InteractiveToken here and must be re-registered.
EXPECTED_LOGON_XML = f"<LogonType>{LOGON_TYPE}</LogonType>"
# What build_task_xml() writes as the task action; a task registered before the
# subcommand change runs `lpm --collect`, which the new CLI rejects - it must
# be re-registered (otherwise every scheduled run fails at argument parsing).
EXPECTED_ARGS_XML = "<Arguments>collect</Arguments>"
_STRAY_TASK_MESSAGE = "Stray scheduled task found: \\LPM\\LPM_Collect. Run lpm schedule --remove to clean it up."


def validate_schedule_config(config: Config) -> list[str]:
    """Validate config['schedule']; returns error messages ([] when valid)."""
    schedule = config.get("schedule")
    if not isinstance(schedule, dict):
        return ["'schedule' section must be an object"]

    errors: list[str] = []
    if not isinstance(schedule.get("enabled"), bool):
        errors.append("'schedule.enabled' must be a boolean")

    times = schedule.get("times")
    if not isinstance(times, list) or not times:
        errors.append("'schedule.times' must be a non-empty list of HH:MM strings")
    else:
        for time_str in times:
            if not isinstance(time_str, str) or TIME_PATTERN.match(time_str) is None:
                errors.append(f"Invalid time {time_str!r} in 'schedule.times' (expected HH:MM)")

    if not isinstance(schedule.get("weekdays_only", True), bool):
        errors.append("'schedule.weekdays_only' must be a boolean")

    return errors


def extract_task_triggers(task_xml: str) -> tuple[list[str], bool]:
    """Pull (sorted unique HH:MM trigger times, weekdays-only flag) from task XML."""
    times = sorted(set(_TRIGGER_TIME.findall(task_xml)))
    return times, "<DaysOfWeek>" in task_xml


def _matches_config(task_xml: str, schedule: dict[str, Any]) -> bool:
    """True when the task's triggers, principal, AND action match the config."""
    task_times, task_weekdays = extract_task_triggers(task_xml)

    cfg_times = schedule.get("times")
    expected_times = sorted(t for t in cfg_times if isinstance(t, str)) if isinstance(cfg_times, list) else []

    weekdays_only = schedule.get("weekdays_only", True)
    if not isinstance(weekdays_only, bool):
        weekdays_only = True

    return (
        task_times == expected_times
        and task_weekdays == weekdays_only
        and EXPECTED_LOGON_XML in task_xml
        and EXPECTED_ARGS_XML in task_xml
    )


def check_schedule(config_path: str, project_root: str) -> list[Issue]:
    """Scheduled-task check for the validate command (plan Cases 1 & 2).

    *project_root* is accepted for symmetry with validate_setup(); the
    config is read from *config_path*.

    Returns a single Issue; COM failures degrade to a WARN.
    """
    del project_root  # reserved for symmetry with validate_setup()

    config: Config = {}
    try:
        with open(config_path, encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            config = loaded
    except (OSError, json.JSONDecodeError):
        pass

    schedule_section = config.get("schedule")

    try:
        scheduler = connect()
        task_xml = get_task_xml(scheduler)
    except Exception as e:
        return [Issue(WARN, f"Task Scheduler not available: {e}")]

    # Case 0: schedule present but disabled - no task should exist either.
    if isinstance(schedule_section, dict) and schedule_section.get("enabled") is False:
        if task_xml is not None:
            return [Issue(WARN, _STRAY_TASK_MESSAGE)]
        return [Issue(OK, "Scheduled collection disabled in config")]

    # Case 1: no schedule section in config - only stray tasks matter.
    if not isinstance(schedule_section, dict):
        if task_xml is not None:
            return [Issue(WARN, _STRAY_TASK_MESSAGE)]
        return [Issue(OK, "No scheduled tasks configured")]

    # Case 2: schedule section exists - the task must exist and match.
    if task_xml is None:
        return [Issue(WARN, "No scheduled task found. Run lpm schedule to create one.")]
    if shutil.which("lpm") is None:
        return [Issue(FAIL, "Scheduled task points to missing executable")]
    if _matches_config(task_xml, schedule_section):
        return [Issue(OK, "Scheduled task matches config")]
    return [Issue(WARN, "Scheduled task differs from config. Run lpm schedule to update.")]
