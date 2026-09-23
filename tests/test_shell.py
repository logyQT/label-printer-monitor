"""Tests for src/shell.py - the interactive `lpm >` shell.

The shell is driven with a scripted input() so each test plays a sequence of
lines and asserts on the captured stdout/stderr.  Handlers are patched on
`main` before `run_shell` builds the parser, so no real command ever runs.
"""

import contextlib
import io
import unittest
from collections.abc import Callable, Sequence
from unittest.mock import patch

import main
from src.shell import run_shell


def _scripted_input(lines: Sequence[str]) -> Callable[[str], str]:
    """Return an input() replacement replaying *lines*, then raising EOFError."""

    remaining = list(lines)

    def fake(prompt: str) -> str:
        if not remaining:
            raise EOFError
        return remaining.pop(0)

    return fake


class RunShellHarness(unittest.TestCase):
    """Base class: run the shell over scripted lines, capture both streams."""

    def _run(self, lines: Sequence[str]) -> tuple[str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            run_shell(main.build_parser, input_func=_scripted_input(lines))
        return out.getvalue(), err.getvalue()


class TestShellLoop(RunShellHarness):
    """Prompt loop: entry banner, exit paths, blank lines."""

    def test_exit_returns_to_caller(self) -> None:
        out, _ = self._run(["exit"])
        self.assertIn("lpm interactive shell", out)

    def test_quit_is_an_alias_for_exit(self) -> None:
        out, _ = self._run(["quit"])
        self.assertIn("lpm interactive shell", out)

    def test_eof_ends_the_shell(self) -> None:
        out, _ = self._run([])  # input() raises EOFError immediately
        self.assertIn("lpm interactive shell", out)

    def test_blank_lines_are_ignored(self) -> None:
        out, err = self._run(["", "   ", "exit"])
        self.assertNotIn("ERROR", err)
        self.assertIn("lpm interactive shell", out)

    def test_ctrl_c_at_prompt_keeps_shell_alive(self) -> None:
        calls = {"count": 0}

        def fake(prompt: str) -> str:
            calls["count"] += 1
            if calls["count"] == 1:
                raise KeyboardInterrupt
            return "exit"

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            run_shell(main.build_parser, input_func=fake)
        self.assertIn("^C", out.getvalue())
        self.assertEqual(err.getvalue(), "")


class TestShellDispatch(RunShellHarness):
    """Commands at the prompt reuse the one-shot parser exactly."""

    def test_dispatches_command_with_flags(self) -> None:
        with patch("main._handle_collect") as handler:
            _, err = self._run(["collect -v --dry", "exit"])
        handler.assert_called_once()
        namespace = handler.call_args.args[0]
        self.assertTrue(namespace.verbose)
        self.assertTrue(namespace.dry)
        self.assertEqual(err, "")

    def test_dispatches_multiple_commands_in_sequence(self) -> None:
        with patch("main._handle_validate") as validate, patch("main._handle_report") as report:
            _, err = self._run(["validate --network", "report --from 2026-09-01", "exit"])
        validate.assert_called_once()
        report.assert_called_once()
        self.assertTrue(validate.call_args.args[0].network)
        self.assertEqual(report.call_args.args[0].from_date, "2026-09-01")
        self.assertEqual(err, "")

    def test_unknown_command_is_reported_and_shell_continues(self) -> None:
        with patch("main._handle_collect") as handler:
            _, err = self._run(["frobnicate", "collect", "exit"])
        self.assertIn("frobnicate", err)  # argparse: invalid choice
        handler.assert_called_once()  # the next line still ran

    def test_bad_flag_keeps_shell_alive(self) -> None:
        _, err = self._run(["report --verbose", "exit"])  # -v is not report's
        self.assertIn("unrecognized arguments", err)

    def test_unterminated_quote_is_reported(self) -> None:
        _, err = self._run(['report --from "2026-09-01', "exit"])
        self.assertIn("ERROR:", err)


class TestShellContainment(RunShellHarness):
    """Handlers ending the process must not end the shell."""

    def test_system_exit_does_not_kill_shell(self) -> None:
        with patch("main._handle_validate", side_effect=SystemExit(1)), patch("main._handle_collect") as handler:
            _, err = self._run(["validate", "collect", "exit"])
        handler.assert_called_once()  # shell reached the line after the failure
        self.assertEqual(err, "")  # int exit codes carry no undelivered message

    def test_system_exit_string_message_is_printed(self) -> None:
        with patch("main._handle_report", side_effect=SystemExit("ERROR: Database integrity check failed: x")):
            _, err = self._run(["report", "exit"])
        self.assertIn("ERROR: Database integrity check failed", err)

    def test_unexpected_exception_is_contained(self) -> None:
        with patch("main._handle_init", side_effect=RuntimeError("boom")):
            _, err = self._run(["init", "exit"])
        self.assertIn("ERROR: boom", err)

    def test_ctrl_c_during_command_aborts_only_the_command(self) -> None:
        with (
            patch("main._handle_schedule", side_effect=KeyboardInterrupt),
            patch("main._handle_collect") as handler,
        ):
            out, err = self._run(["schedule", "collect", "exit"])
        self.assertIn("Aborted.", out)
        handler.assert_called_once()  # shell recovered and ran the next line
        self.assertEqual(err, "")


class TestShellHelp(RunShellHarness):
    """`help` dispatches as a normal subcommand to main._handle_help."""

    def test_help_shows_every_command_with_options(self) -> None:
        out, _ = self._run(["help", "exit"])
        self.assertIn("command details:", out)
        self.assertIn("--network", out)
        self.assertIn("--csv", out)
        for name in ("init", "collect", "validate", "report", "schedule", "help", "shell"):
            with self.subTest(command=name):
                self.assertIn(name, out)

    def test_help_topic_shows_only_that_command(self) -> None:
        out, _ = self._run(["help report", "exit"])
        self.assertIn("--from", out)
        self.assertIn("--csv", out)
        self.assertNotIn("--network", out)


if __name__ == "__main__":
    unittest.main()
