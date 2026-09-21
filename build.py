"""Build lpm.exe with Nuitka.

Usage:
    python build.py            # standard build
    python build.py --clean    # remove build artifacts first
"""

import os
import shutil
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(PROJECT_ROOT, "main.py")
DIST = os.path.join(PROJECT_ROOT, "dist")


def clean() -> None:
    for d in ("build", "dist", "main.build"):
        p = os.path.join(PROJECT_ROOT, d)
        if os.path.isdir(p):
            print(f"Removing {p}")
            shutil.rmtree(p)


def build() -> None:
    cmd = [
        sys.executable, "-m", "nuitka",
        "--standalone",
        "--onefile",
        "--output-filename=lpm.exe",
        "--output-dir=dist",

        # Include the entire src/ package (db, env, adapters, etc.)
        "--include-package=src",

        # Windows console app
        "--windows-console-mode=force",

        "--assume-yes-for-downloads",

        # Entry point
        MAIN,
    ]

    print("Building lpm.exe...")
    print(f"  Command: {' '.join(cmd)}")
    print()

    result = subprocess.run(cmd, cwd=PROJECT_ROOT)
    if result.returncode != 0:
        print(f"\nBuild failed (exit code {result.returncode})", file=sys.stderr)
        sys.exit(result.returncode)

    exe = os.path.join(DIST, "lpm.exe")
    if os.path.isfile(exe):
        size_mb = os.path.getsize(exe) / (1024 * 1024)
        print(f"\nBuild succeeded: {exe} ({size_mb:.1f} MB)")
    else:
        print(f"\nBuild completed but {exe} not found", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    if "--clean" in sys.argv:
        clean()
    build()
