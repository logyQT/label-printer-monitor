"""Windows Task Scheduler COM calls (create, query, delete) for the schedule command.

All win32com usage lives here. win32com is imported lazily - inside
``connect()`` and behind the module-level ``__getattr__`` - so importing
this module never requires pywin32 until a COM call actually runs.
"""

import os
from contextlib import suppress
from typing import Any

from src.schedule.meta import TASK_FOLDER, TASK_NAME, TASK_PATH

# The task runs as the LOCAL SYSTEM account - queried off the working task
# (\LPM\LPM_Collect), the only principal it has ever run under:
#   Definition.Principal -> UserId 'SYSTEM', LogonType 5, RunLevel 0
#   exported XML         -> <UserId>S-1-5-18</UserId>, no <LogonType>,
#                           no <RunLevel> (both serialize only when non-default)
# TASK_LOGON_SERVICE_ACCOUNT is the matching registration logon type: SYSTEM
# needs no password (password must be VT_NULL), runs whether or not any user
# is logged on, and - unlike S4U - keeps network and encrypted-file access.
SYSTEM_SID = "S-1-5-18"
TASK_LOGON_SERVICE_ACCOUNT = 5

__all__ = [
    "SYSTEM_SID",
    "TASK_LOGON_SERVICE_ACCOUNT",
    "build_task_xml",
    "connect",
    "delete_task",
    "escape_xml",
    "folder_exists",
    "get_task_xml",
    "register_task",
    "unescape_xml",
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


# These two replace xml.sax.saxutils: the frozen build ships without the
# stdlib ``xml`` package (build.py --nofollow-import-to=...,xml,...), so an
# import of it compiles fine and then explodes at runtime in lpm.exe.

_XML_ENTITIES: tuple[tuple[str, str], ...] = (
    ("&", "&amp;"),  # first, so the references inserted below stay intact
    ("<", "&lt;"),
    (">", "&gt;"),
    ('"', "&quot;"),
    ("'", "&apos;"),
)


def escape_xml(text: str) -> str:
    """Escape *text* so it is safe as XML element content (mirrors saxutils)."""
    for char, ref in _XML_ENTITIES:
        text = text.replace(char, ref)
    return text


def unescape_xml(text: str) -> str:
    """Reverse escape_xml() (mirrors xml.sax.saxutils.unescape).

    ``&amp;`` is resolved last so that ``&amp;lt;`` - an escaped literal
    ``&lt;`` - comes back as ``&lt;`` and not as ``<``.
    """
    for ref, char in (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&apos;", "'")):
        text = text.replace(ref, char)
    return text.replace("&amp;", "&")


def build_task_xml(times: list[str], weekdays_only: bool, exe_path: str) -> str:
    r"""Build the task definition XML for \\LPM\\LPM_Collect (one trigger per time).

    *exe_path* is the absolute path of lpm.exe and becomes the task's
    ``<Command>`` (with ``<WorkingDirectory>`` set to its folder).  A bare
    ``lpm`` would be resolved by the Task Scheduler service against whatever
    PATH that service sees at fire time - a stale one, right after an install
    that only just added the install dir to the machine PATH - which silently
    breaks every headless run.
    """
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
        f"      <UserId>{SYSTEM_SID}</UserId>\n"
        "    </Principal>\n"
        "  </Principals>\n"
        "  <Settings>\n"
        "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n"
        "    <WakeToRun>true</WakeToRun>\n"
        "    <Enabled>true</Enabled>\n"
        "  </Settings>\n"
        '  <Actions Context="Author">\n'
        "    <Exec>\n"
        f"      <Command>{escape_xml(exe_path)}</Command>\n"
        "      <Arguments>collect</Arguments>\n"
        f"      <WorkingDirectory>{escape_xml(os.path.dirname(exe_path))}</WorkingDirectory>\n"
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
        folder.RegisterTask(TASK_NAME, xml, 0, None, None, TASK_LOGON_SERVICE_ACCOUNT)
    except Exception:
        # Task Scheduler can reject principal changes (S4U/Interactive->SYSTEM)
        # on update - fall back to the plan's delete + recreate.
        with suppress(Exception):
            folder.DeleteTask(TASK_NAME, 0)
        folder.RegisterTask(TASK_NAME, xml, 0, None, None, TASK_LOGON_SERVICE_ACCOUNT)


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
