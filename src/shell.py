"""Interactive ``lpm`` shell - a read-eval-print loop over the CLI subcommands.

Entered by running bare ``lpm`` (on a TTY) or ``lpm shell``.  The shell reuses
the exact argparse parser built by ``main.build_parser()``, so every command
and flag behaves the same as in one-shot mode.  ``help`` is an ordinary
subcommand, so it dispatches like anything else.

Failures inside a command - ``sys.exit``, argparse usage errors, unexpected
exceptions, Ctrl+C mid-run - are contained: diagnostics are shown and the
``lpm >`` prompt comes back instead of the process ending.
"""

import argparse
import shlex
import sys
from collections.abc import Callable

PROMPT = "lpm > "

# main.build_parser() -> (top-level parser, {command name: subparser}).
type ParserFactory = Callable[[], tuple[argparse.ArgumentParser, dict[str, argparse.ArgumentParser]]]


def _execute(parser: argparse.ArgumentParser, line: str) -> None:
    """Parse and run one shell line, containing every failure mode."""
    try:
        words = shlex.split(line)
    except ValueError as e:  # e.g. unterminated quote
        print(f"ERROR: {e}", file=sys.stderr)
        return

    try:
        args = parser.parse_args(words)
    except SystemExit:
        return  # argparse already printed the usage error (exit 2)

    handler = getattr(args, "func", None)
    if handler is None:
        print(f'Unknown command: {words[0]!r} - type "help" for commands.', file=sys.stderr)
        return

    try:
        handler(args)
    except SystemExit as e:
        # Handlers print their own diagnostics before sys.exit(...); only a
        # string code (e.g. SystemExit("ERROR: ...")) still needs rendering.
        if isinstance(e.code, str):
            print(e.code, file=sys.stderr)
    except KeyboardInterrupt:
        print("\nAborted.")
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)


def run_shell(
    build_parser: ParserFactory,
    *,
    input_func: Callable[[str], str] = input,
) -> None:
    """Run the interactive ``lpm >`` prompt until exit/quit or EOF.

    *build_parser* is injected (``main.build_parser``) so this module never
    imports the entry point - which would be a circular import, and wouldn't
    resolve in the frozen build anyway.  *input_func* exists for tests.
    """
    parser, _ = build_parser()
    print('lpm interactive shell - type "help" for commands, "exit" to leave.')
    while True:
        try:
            line = input_func(PROMPT)
        except EOFError:  # Ctrl+D / Ctrl+Z or redirected stdin ending
            print()
            return
        except KeyboardInterrupt:  # Ctrl+C at the prompt clears the line only
            print("^C")
            continue

        line = line.strip()
        if not line:
            continue
        if line in {"exit", "quit"}:
            return
        _execute(parser, line)
