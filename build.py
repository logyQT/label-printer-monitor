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
SCHEMA_FILE = os.path.join(PROJECT_ROOT, "config", "config.json.schema")
EXAMPLE_FILE = os.path.join(PROJECT_ROOT, "config", "config.example.json")


def clean() -> None:
    for d in ("build", "dist", "main.build"):
        p = os.path.join(PROJECT_ROOT, d)
        if os.path.isdir(p):
            print(f"Removing {p}")
            shutil.rmtree(p)


def build() -> None:
    cmd = [
        sys.executable,
        "-m",
        "nuitka",
        "--standalone",
        # -- Standalone (directory) mode instead of --onefile.
        #    Onefile extracts to a temp dir on every launch (~1 s overhead).
        #    Standalone runs directly from its folder — near-instant startup.
        #    Zip the dist/main.dist/ folder for distribution if needed.
        "--output-filename=lpm.exe",
        "--output-dir=dist",
        # Include the entire src/ package (db, env, adapters, etc.)
        "--include-package=src",
        # ── Vendored config assets ─────────────────────────────────────
        #    config/*.json in the repo is the single source of truth; these
        #    land in dist/main.dist/config/ next to lpm.exe, found at
        #    runtime via env.bundled_config_dir() (init copies them,
        #    validate refreshes a stale %APPDATA% copy).
        f"--include-data-files={SCHEMA_FILE}=config/config.json.schema",
        f"--include-data-files={EXAMPLE_FILE}=config/config.example.json",
        # Windows console app
        "--windows-console-mode=force",
        # ── Strip unnecessary packages ────────────────────────────────
        # cryptography chain (16+ MB) — only needed for SNMPv3 encryption;
        # SNMP v1/v2c uses plaintext community strings, so this is dead weight.
        "--nofollow-import-to=cryptography,pysnmpcrypto,cffi,_cffi_backend,pycparser",
        # Unused transitive deps (requests, Jinja2, etc.)
        "--nofollow-import-to=charset_normalizer,certifi,markupsafe",
        # Unused stdlib C extensions — asyncio handles missing ssl gracefully
        "--nofollow-import-to=_ssl,_hashlib,_decimal,_bz2,_lzma,_wmi,_uuid,_multiprocessing,pyexpat",
        # Unused stdlib pure-Python modules
        "--nofollow-import-to=tkinter,unittest,pydoc,doctest,lib2to3,email,html,http,xml,py_compile,compileall",
        # Compiler optimizations
        "--lto=yes",  # link-time optimisation (smaller + faster)
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

    exe = os.path.join(DIST, "main.dist", "lpm.exe")
    if os.path.isfile(exe):
        size_mb = os.path.getsize(exe) / (1024 * 1024)
        dist_dir = os.path.join(DIST, "main.dist")
        total_mb = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fnames in os.walk(dist_dir) for f in fnames) / (
            1024 * 1024
        )
        print(f"\nBuild succeeded: {exe}")
        print(f"  lpm.exe:    {size_mb:.1f} MB")
        print(f"  Total dist: {total_mb:.1f} MB  (zip this folder for distribution)")
    else:
        print(f"\nBuild completed but {exe} not found", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    if "--clean" in sys.argv:
        clean()
    build()
