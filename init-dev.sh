#!/usr/bin/env sh
# ============================================================
#  init-dev.sh - Set up the development virtualenv (.venv)
#
#  Idempotent: reuses the existing venv if present.
#  Requires Python 3.13 (project target) or newer.
# ============================================================
set -eu

cd "$(dirname "$0")"

VENV_DIR="$(pwd)/.venv"
PYTHON="${VENV_DIR}/bin/python"

if [ ! -x "$PYTHON" ]; then
    echo "Creating virtualenv: .venv ..."
    python3 -m venv "$VENV_DIR"
fi

echo "Installing dev dependencies ..."
"$PYTHON" -m pip install --upgrade pip
"$PYTHON" -m pip install -r requirements-dev.txt

echo
echo "Development environment ready."
echo "  Activate : source .venv/bin/activate"
echo "  Init app : python main.py --init"
echo "  Tests    : pytest"
echo "  Lint     : ruff check .   /   ruff format ."
echo "  Typecheck: mypy"