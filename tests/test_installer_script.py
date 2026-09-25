"""Guard: the uninstaller's cleanup contract with `lpm purge` (src/purge.py).

lpm.nsi has no functional test harness, so these assertions pin the wiring
that matters: the scheduled task is removed unconditionally, the config/
data/logs prunes are opt-in, and the program files go last while lpm.exe is
still on disk for the purge calls to use.
"""

import os
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

with open(os.path.join(REPO_ROOT, "installer", "lpm.nsi"), encoding="utf-8") as _f:
    NSI: str = _f.read()


def _section_body(decl: str) -> str:
    """Text between the section declaration *decl* and its SectionEnd."""
    start = NSI.index(decl)
    end = NSI.index("SectionEnd", start)
    return NSI[start:end]


class UninstallPages(unittest.TestCase):
    def test_components_page_replaces_the_plain_confirm_page(self) -> None:
        # The cleanup checkboxes double as the confirmation step.
        self.assertIn("!insertmacro MUI_UNPAGE_COMPONENTS", NSI)
        self.assertNotIn("!insertmacro MUI_UNPAGE_CONFIRM", NSI)

    def test_descriptions_exist_for_every_cleanup_section(self) -> None:
        self.assertIn("!insertmacro MUI_UNFUNCTION_DESCRIPTION_BEGIN", NSI)
        for sec in ("SecTask", "SecPruneConfig", "SecPruneData", "SecPruneLogs", "SecProgram"):
            with self.subTest(section=sec):
                self.assertIn(f"MUI_DESCRIPTION_TEXT ${{{sec}}}", NSI)


class MandatoryTaskRemoval(unittest.TestCase):
    """The scheduled task must never survive an uninstall."""

    TASK_DECL = 'Section "un.Scheduled collection task" SecTask'

    def test_task_section_is_read_only_and_selected(self) -> None:
        body = _section_body(self.TASK_DECL)
        self.assertIn("SectionIn RO", body)
        self.assertNotIn("Section /o", body)

    def test_task_section_runs_before_program_files_are_deleted(self) -> None:
        # Declaration order == execution order; lpm.exe must still exist.
        self.assertLess(NSI.index(self.TASK_DECL), NSI.index('Section "un.Program files'))

    def test_task_section_delegates_to_lpm_purge_with_schtasks_fallback(self) -> None:
        body = _section_body(self.TASK_DECL)
        self.assertIn("purge --tasks", body)
        self.assertIn("/Delete /TN", body)  # broken-install fallback
        self.assertIn("/Query /TN", body)  # verify + warn when still present


class OptionalPrunes(unittest.TestCase):
    """config/data/logs are opt-in checkboxes, off by default."""

    def test_all_three_prune_sections_are_optional(self) -> None:
        for name in ("configuration", "data", "logs"):
            with self.subTest(prune=name):
                self.assertIn(f'Section /o "un.Prune {name}"', NSI)

    def test_each_prune_delegates_to_its_purge_flag(self) -> None:
        # Each section inserts the shared macro with its own directory name;
        # the macro in turn calls `lpm purge --<flag>`.
        self.assertIn("purge --${FLAG}", NSI)
        for flag, name in (("config", "configuration"), ("data", "data"), ("logs", "logs")):
            with self.subTest(flag=flag):
                start = NSI.index(f'Section /o "un.Prune {name}"')
                end = NSI.index("SectionEnd", start)
                self.assertIn(f"PruneDataDir {flag}", NSI[start:end])

    def test_prunes_run_before_program_files_go(self) -> None:
        program_index = NSI.index('Section "un.Program files')
        for name in ("configuration", "data", "logs"):
            with self.subTest(prune=name):
                self.assertLess(NSI.index(f'Section /o "un.Prune {name}"'), program_index)

    def test_program_files_section_is_read_only(self) -> None:
        body = _section_body('Section "un.Program files, PATH and registry" SecProgram')
        self.assertIn("SectionIn RO", body)
        self.assertIn('RMDir /r "$INSTDIR"', body)


class SilentMode(unittest.TestCase):
    def test_purge_switch_prechecks_every_prune_box(self) -> None:
        # uninstall.exe /S /PURGE is the only way to prune in silent mode.
        self.assertIn('${GetOptions} "$R0" "/PURGE"', NSI)
        for sec in ("SecPruneConfig", "SecPruneData", "SecPruneLogs"):
            with self.subTest(section=sec):
                self.assertIn(f"SectionSetFlags ${{{sec}}} ${{SF_SELECTED}}", NSI)

    def test_display_names_override_the_un_prefix(self) -> None:
        # Sections need the 'un.' prefix to compile as uninstaller sections;
        # un.onInit rewrites the labels shown in the components tree.
        for sec, label in (
            ("SecTask", "Scheduled collection task"),
            ("SecPruneConfig", "Prune configuration"),
            ("SecPruneData", "Prune data"),
            ("SecPruneLogs", "Prune logs"),
        ):
            with self.subTest(section=sec):
                self.assertIn(f'SectionSetText ${{{sec}}} "{label}"', NSI)


if __name__ == "__main__":
    unittest.main()
