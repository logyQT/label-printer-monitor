"""Windows Task Scheduler COM calls (create, query, delete) for the schedule command.

All win32com usage lives here. win32com is imported lazily - inside
``connect()`` and behind the module-level ``__getattr__`` - so importing
this module never requires pywin32 until a COM call actually runs.
"""

from contextlib import suppress
from typing import Any

from src.schedule.meta import TASK_FOLDER, TASK_NAME, TASK_PATH

# TASK_LOGON_S4U: "run whether user is logged on or not" without storing a
# password (InteractiveToken would only run while the user is logged in).
LOGON_TYPE = "S4U"

__all__ = [
    "LOGON_TYPE",
    "build_task_xml",
    "connect",
    "delete_task",
    "folder_exists",
    "get_task_xml",
    "register_task",
]


def __getattr__(name: str) -> Any:
    r"""Expose ``win32com`` lazily (PEP 562).

    Keeps pywin32 out of module import while letting tests patch
    ``src.schedule.commands.win32com.client.Dispatch`` by attribute path
    (mock resolves the target via getattr, which triggers this hook).
    """
    if name == "win32com":
        import win32com.client

        return win32com
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def build_task_xml(times: list[str], weekdays_only: bool) -> str:
    """Build the task definition XML for \\LPM\\LPM_Collect (one trigger per time)."""
    triggers: list[str] = []
    for time_str in times:
        if weekdays_only:
            schedule = (
                "      <ScheduleByWeek>\n"
                "        <WeeksInterval>1</WeeksInterval>\n"
                "        <DaysOfWeek>\n"
                "          <Monday/><Tuesday/><Wednesday/><Thursday/><Friday/>\n"
                "        </DaysOfWeek>\n"
                "      </ScheduleByWeek>"
            )
        else:
            schedule = "      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>"
        triggers.append(
            "    <CalendarTrigger>\n"
            f"      <StartBoundary>2026-01-01T{time_str}:00</StartBoundary>\n"
            "      <Enabled>true</Enabled>\n"
            f"{schedule}\n"
            "    </CalendarTrigger>"
        )
    trigger_xml = "\n".join(triggers)
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
        "  <RegistrationInfo><Description>LPM scheduled collection</Description></RegistrationInfo>\n"
        "  <Triggers>\n"
        f"{trigger_xml}\n"
        "  </Triggers>\n"
        "  <Principals>\n"
        '    <Principal id="Author">\n'
        f"      <LogonType>{LOGON_TYPE}</LogonType>\n"
        "      <RunLevel>LeastPrivilege</RunLevel>\n"
        "    </Principal>\n"
        "  </Principals>\n"
        "  <Settings>\n"
        "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n"
        "    <WakeToRun>true</WakeToRun>\n"
        "    <Enabled>true</Enabled>\n"
        "  </Settings>\n"
        '  <Actions Context="Author">\n'
        "    <Exec>\n"
        "      <Command>lpm</Command>\n"
        "      <Arguments>collect</Arguments>\n"
        "    </Exec>\n"
        "  </Actions>\n"
        "</Task>"
    )


def connect() -> Any:
    """Connect to the Windows Task Scheduler service and return its COM object."""
    import win32com.client

    scheduler = win32com.client.Dispatch("Schedule.Service")
    scheduler.Connect()
    return scheduler


def folder_exists(scheduler: Any, path: str) -> bool:
    """True when *path* is an existing task folder (False on any COM error)."""
    try:
        scheduler.GetFolder(path)
    except Exception:
        return False
    return True


def get_task_xml(scheduler: Any) -> str | None:
    """Return the XML of \\LPM\\LPM_Collect, or None when the folder/task is missing."""
    try:
        folder = scheduler.GetFolder(TASK_FOLDER)
        task = folder.GetTask(TASK_NAME)
    except Exception:
        return None
    return task.Xml  # type: ignore[no-any-return]  # IRegisteredTask.Xml (2.0), not 1.0's XmlText


def register_task(scheduler: Any, xml: str) -> None:
    """Create or update \\LPM\\LPM_Collect, creating the \\LPM folder when missing."""
    try:
        folder = scheduler.GetFolder(TASK_FOLDER)
    except Exception:
        root = scheduler.GetFolder("\\")
        root.CreateFolder("LPM")
        folder = scheduler.GetFolder(TASK_FOLDER)
    try:
        folder.RegisterTask(TASK_NAME, xml, 0, None, None, 2)  # TASK_CREATE_OR_UPDATE, TASK_LOGON_S4U
    except Exception:
        # Task Scheduler can reject principal changes (Interactive->S4U) on
        # update - fall back to the plan's delete + recreate.
        with suppress(Exception):
            folder.DeleteTask(TASK_NAME, 0)
        folder.RegisterTask(TASK_NAME, xml, 0, None, None, 2)


def delete_task(scheduler: Any, verbose: bool = False) -> bool:
    r"""Delete \LPM\LPM_Collect (and \LPM when it ends up empty).

    Returns True when the task existed and was deleted, False otherwise.
    With *verbose*, narrate the folder cleanup decision.
    """
    try:
        folder = scheduler.GetFolder(TASK_FOLDER)
    except Exception:
        if verbose:
            print(f"{TASK_FOLDER}: task folder not found")
        return False
    folder.DeleteTask(TASK_NAME, 0)
    if verbose:
        print(f"{TASK_PATH}: deleted")
    if len(folder.GetTasks(0)) == 0:
        # Remove the folder too, but not at the cost of failing a successful
        # task deletion (it may still hold subfolders or a concurrent task).
        if verbose:
            print(f"{TASK_FOLDER}: no tasks left -> deleting folder")
        with suppress(Exception):
            scheduler.GetFolder("\\").DeleteFolder("LPM", 0)
    elif verbose:
        print(f"{TASK_FOLDER}: {len(folder.GetTasks(0))} other task(s) -> keeping folder")
    return True
