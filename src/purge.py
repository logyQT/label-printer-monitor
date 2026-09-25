r"""``lpm purge`` - remove the scheduled task and/or config/data/logs.

The NSIS uninstaller delegates to this command while ``lpm.exe`` is still on
disk, so path resolution (``%LPM_HOME%``, ``%ProgramData%``), the Task
Scheduler COM call and the deletion rules live in exactly one place; the
uninstaller keeps a ``schtasks``/``RMDir`` fallback for the broken-install
case.  Running the command by hand gives zip distributions (no installer)
the same cleanup path.

Everything is flag-gated: purge never deletes anything implicitly, so a
bare ``lpm purge`` is a usage error, not a nuclear option.
"""

from __future__ import annotations

import os
import shutil
import sys

from src.env import data_root, exe_path, is_admin
from src.schedule.commands import connect, delete_task
from src.schedule.meta import TASK_PATH

__all__ = ["prune_dirs", "prune_task", "run_purge"]

_USAGE: str = (
    "usage: lpm purge (--tasks | --config | --data | --logs | --all) [--dry-run]\n"
    "  --tasks    remove the \\LPM\\LPM_Collect scheduled task\n"
    "  --config   delete <data root>\\config (config.json, schema, example)\n"
    "  --data     delete <data root>\\data (database and backups)\n"
    "  --logs     delete <data root>\\logs\n"
    "  --all      everything above\n"
    "  --dry-run  only print what would be removed"
)


def _refusal_reason(root: str) -> str | None:
    r"""Why *root* must never be purged, or None when it is safe to prune.

    The last line of defence behind the explicit flags: an %LPM_HOME% (or
    %ProgramData%) pointing somewhere catastrophic - a drive root, the
    Windows directory, the install dir - is refused outright rather than
    rmtree'd.
    """
    if not root.strip():
        return "it is an empty path"
    norm = os.path.normcase(os.path.abspath(root))
    _drive, tail = os.path.splitdrive(norm)
    if tail in (os.sep, "/", ""):
        # "C:\" (drive root), "C:" (drive-relative) or "\\server\share"
        # (UNC share root) - a root itself is never the data root.
        return "it is a drive or share root"
    windows = os.path.normcase(os.environ.get("WINDIR", r"C:\Windows"))
    if norm == windows or norm.startswith(windows + os.sep):
        return "it is inside the Windows directory"
    if norm == os.path.normcase(os.path.abspath(os.path.dirname(exe_path()))):
        return "it is the install directory"
    return None


def prune_dirs(root: str, targets: list[str], *, dry_run: bool) -> None:
    """Remove the named subdirectories of *root*, then the root if it emptied.

    Idempotent and chatty: every path is reported so an uninstaller's
    Details view (and a human at a terminal) sees exactly what happened.
    The root itself goes only when non-recursive ``rmdir`` succeeds, i.e.
    when the prunes emptied it - anything else in there is the operator's.
    """
    for name in targets:
        path = os.path.join(root, name)
        if not os.path.isdir(path):
            print(f"  not present: {path}")
        elif dry_run:
            print(f"  would remove: {path}")
        else:
            shutil.rmtree(path)
            print(f"  removed: {path}")
    if dry_run:
        return
    try:
        os.rmdir(root)
    except OSError:
        pass  # not empty (operator files) or already gone - keep as is
    else:
        print(f"  removed (empty): {root}")


def prune_task() -> bool:
    r"""Delete \LPM\LPM_Collect (and \LPM when it ends up empty).

    Returns False only when the task could not be *checked or removed*
    (not elevated, Task Scheduler unreachable) - the caller (the NSIS
    uninstaller) then falls back to ``schtasks /Delete``.  A task that was
    never registered counts as success: purging twice, or uninstalling a
    machine that never ran ``lpm schedule``, must stay a no-op.
    """
    if not is_admin():
        print(
            "ERROR: removing the scheduled task needs an elevated session (Run as administrator).",
            file=sys.stderr,
        )
        return False
    try:
        scheduler = connect()
        existed = delete_task(scheduler, verbose=True)
    except Exception as e:  # pywin32/COM problems - let the caller fall back
        print(f"ERROR: could not reach Task Scheduler: {e}", file=sys.stderr)
        return False
    print(f"Scheduled task removed ({TASK_PATH})." if existed else f"No scheduled task present ({TASK_PATH}).")
    return True


def run_purge(
    *,
    tasks: bool = False,
    config: bool = False,
    data: bool = False,
    logs: bool = False,
    dry_run: bool = False,
) -> int:
    """Run the requested cleanup; returns the process exit code.

    Nothing selected -> usage error (exit 2).  Each target is attempted
    independently, so one failure does not strand the rest; the worst
    outcome decides the exit code (0 = everything requested is gone).
    """
    if not (tasks or config or data or logs):
        print(_USAGE, file=sys.stderr)
        return 2

    code = 0
    if tasks:
        if dry_run:
            print(f"  would remove: scheduled task {TASK_PATH}")
        elif not prune_task():
            code = 1

    targets = [name for name, on in (("config", config), ("data", data), ("logs", logs)) if on]
    if targets:
        root = data_root(mkdir=False)
        reason = _refusal_reason(root)
        if reason is not None:
            print(f"ERROR: refusing to purge data root {root!r}: {reason}.", file=sys.stderr)
            code = 1
        else:
            print(f"Data root: {root}")
            try:
                prune_dirs(root, targets, dry_run=dry_run)
            except OSError as e:
                print(f"ERROR: could not purge {root}: {e}", file=sys.stderr)
                code = 1
    return code
