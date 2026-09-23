"""Comprehensive Link-OS MIB walker for Zebra printers.

Walks every registered ZEBRA-MIB / Link-OS branch under ``1.3.6.1.4.1.10642``
(the whole ``200`` range included -- that is where the zql-zebra-ql objects
live, e.g. ``1.3.6.1.4.1.10642.200.17`` = zql-ql-odometer with the label /
print-length counters) plus the standard MIBs a Link-OS printer exposes
(system, interfaces, TCP/IP, SNMP, Printer-MIB, HOST-RESOURCES-MIB).

Targets can be single hosts, wildcards (``200.10.5.*``), CIDR ranges
(``200.10.5.0/24``) or the printer list from ``config/config.json``.

Examples
--------
Walk every known MIB on one printer:

    python tools/linkos_walk.py 10.0.1.11

Link-OS branches only, forced to SNMPv1 (GX430t-style agents):

    python tools/linkos_walk.py 10.0.1.12 -v1 --mib zebra,zql

Only the odometer branch (200.17):

    python tools/linkos_walk.py 10.0.1.11 --root 1.3.6.1.4.1.10642.200.17

The "200" range across a subnet, artifacts on disk, summary only on screen:

    python tools/linkos_walk.py 200.10.5.0/24 --mib zql,odometer --quiet --json walk.json --csv walk.csv

Every ZT411 from the config:

    python tools/linkos_walk.py --config --model "Zebra ZT411"

Show the MIB catalogue:

    python tools/linkos_walk.py --list-mibs
"""

import argparse
import asyncio
import contextlib
import csv
import ipaddress
import itertools
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from pysnmp.hlapi.v1arch.asyncio import (
    CommunityData,
    ObjectIdentity,
    ObjectType,
    SnmpDispatcher,
    UdpTransportTarget,
    bulk_walk_cmd,
    get_cmd,
    walk_cmd,
)

# --- Link-OS OID catalogue ---------------------------------------------------

ENTERPRISE = "1.3.6.1.4.1.10642"  # Zebra Technologies / Link-OS (ZEBRA-MIB)
ZQL = f"{ENTERPRISE}.200"  # zql-zebra-ql: the "200" range
ODOMETER = f"{ZQL}.17"  # zql-ql-odometer: label / print-length counters

# Registered children of 1.3.6.1.4.1.10642 (ZEBRA-MIB top-level branches).
ZEBRA_BRANCHES: dict[str, str] = {
    "1": "zbrGeneralInfo",
    "2": "zbrPlatform",
    "3": "zbrOptions",
    "5": "zbrNetmanage",
    "6": "zbrPrint",
    "7": "zbrControl",
    "8": "zbrSettings",
    "9": "zbrFile",
    "10": "zbrDevice",
    "15": "zbrSensor",
    "20": "zbrInterfaces",
    "21": "zbrSerialNumbers",
    "30": "zbrJobLogging",
    "31": "zbrDataCapture",
    "32": "zbrWeblink",
    "40": "zbrBasicInterpreter",
    "41": "zbrUsbHost",
    "42": "zbrUsbMirror",
    "43": "zbrUsbOtg",
    "45": "zbrApl",
    "46": "zbrNfc",
    "47": "zbrRibbon",
    "48": "zbrSupplies",
    "49": "zbrLinkOs",
    "50": "zbrMedia",
    "200": "zql-zebra-ql",
}

# Registered children of 1.3.6.1.4.1.10642.200 (the "200" range).
ZQL_BRANCHES: dict[str, str] = {
    "1": "zql-ql-bluetooth",
    "2": "zql-ql-card",
    "3": "zql-ql-comm",
    "4": "zql-ql-head",
    "5": "zql-ql-ip",
    "6": "zql-ql-label",
    "7": "zql-ql-motor",
    "8": "zql-ql-media",
    "9": "zql-ql-sensor",
    "10": "zql-ql-srf",
    "11": "zql-ql-wlan",
    "12": "zql-ql-display",
    "13": "zql-ql-memory",
    "14": "zql-ql-power",
    "15": "zql-ql-file",
    "17": "zql-ql-odometer",
    "18": "zql-ql-appl",
    "19": "zql-ql-device",
    "20": "zql-ql-input",
    "21": "zql-ql-print",
    "23": "zql-ql-rtc",
    "25": "zql-ql-usb",
    "27": "zql-ql-log",
    "28": "zql-ql-netmanage",
    "41": "zql-ql-zpl",
    "46": "zql-ql-rfid",
    "49": "zql-ql-event-log",
    "52": "zql-ql-line-print",
    "53": "zql-ql-apl",
}

# Children of 1.3.6.1.4.1.10642.200.17 (zql-ql-odometer). ".7" is not in the
# public OID registry but is confirmed live on GX430t / Link-OS printers as
# 10642.200.17.7.0 = "89667 INCHES, 227775 CENTIMETERS".
ODOMETER_BRANCHES: dict[str, str] = {
    "1": "zql-odometer-user-label-count",
    "2": "zql-odometer-total-label-count",
    "3": "zql-odometer-total-print-length",
    "5": "zql-odometer-latch-open-count",
    "6": "zql-odometer-label-dot-length",
    "7": "zql-odometer-total-usage",
    "11": "zql-odometer-rfid",
}

# Root OID -> branch table, used by --per-branch and for walk labels.
BRANCH_TABLES: dict[str, dict[str, str]] = {
    ENTERPRISE: ZEBRA_BRANCHES,
    ZQL: ZQL_BRANCHES,
    ODOMETER: ODOMETER_BRANCHES,
}

# Well-known scalar objects; longest-prefix match decorates the text output.
KNOWN_OBJECTS: dict[str, str] = {
    "1.3.6.1.2.1.1.1.0": "sysDescr",
    "1.3.6.1.2.1.1.2.0": "sysObjectID",
    "1.3.6.1.2.1.1.3.0": "sysUpTime",
    "1.3.6.1.2.1.1.4.0": "sysContact",
    "1.3.6.1.2.1.1.5.0": "sysName",
    "1.3.6.1.2.1.1.6.0": "sysLocation",
    "1.3.6.1.4.1.10642.1.1.0": "zbrGeneralModel",
    "1.3.6.1.4.1.10642.1.2.0": "zbrGeneralFirmwareVersion",
    "1.3.6.1.4.1.10642.1.18.0": "zbrGeneralLINKOSVersion",
    "1.3.6.1.4.1.10642.3.1.1.0": "printMetersNonReset (cm)",
    "1.3.6.1.4.1.10642.3.1.6.0": "printLabelsNonReset",
    "1.3.6.1.4.1.10642.10.31.1.2": "zbrTrackedAlertsSeverity",
    "1.3.6.1.4.1.10642.10.31.1.3": "zbrTrackedAlertsTrainingLevel",
    "1.3.6.1.4.1.10642.10.31.1.4": "zbrTrackedAlertsGroup",
    "1.3.6.1.4.1.10642.10.31.1.5": "zbrTrackedAlertsCode",
    "1.3.6.1.4.1.10642.200.14.5.0": "zql-power-low-battery-shutdown",
    "1.3.6.1.4.1.10642.200.17.1.0": "zql-odometer-user-label-count",
    "1.3.6.1.4.1.10642.200.17.2.0": "zql-odometer-total-label-count",
    "1.3.6.1.4.1.10642.200.17.3.0": "zql-odometer-total-print-length",
    "1.3.6.1.4.1.10642.200.17.5.0": "zql-odometer-latch-open-count",
    "1.3.6.1.4.1.10642.200.17.6.0": "zql-odometer-label-dot-length",
    "1.3.6.1.4.1.10642.200.17.7.0": "zql-odometer-total-usage (string)",
    "1.3.6.1.4.1.10642.200.17.11.0": "zql-odometer-rfid",
}

SYSDESCR_OID = "1.3.6.1.2.1.1.1.0"
SYSOBJECTID_OID = "1.3.6.1.2.1.1.2.0"


@dataclass(frozen=True)
class MibGroup:
    """One selectable MIB group (``--mib`` key)."""

    key: str
    title: str
    note: str
    roots: tuple[str, ...]


MIB_GROUPS: tuple[MibGroup, ...] = (
    MibGroup("system", "SNMPv2-MIB :: system", "sysDescr, sysObjectID, uptime, name", ("1.3.6.1.2.1.1",)),
    MibGroup(
        "interfaces",
        "IF-MIB + ifXTable",
        "ports, speeds, operational status, counters",
        ("1.3.6.1.2.1.2", "1.3.6.1.2.1.31.1"),
    ),
    MibGroup(
        "tcpip",
        "IP / ICMP / TCP / UDP MIB",
        "addresses, routes, sockets",
        ("1.3.6.1.2.1.4", "1.3.6.1.2.1.5", "1.3.6.1.2.1.6", "1.3.6.1.2.1.7"),
    ),
    MibGroup("snmp", "SNMPv2-MIB :: snmp", "engine stats, discards, traps", ("1.3.6.1.2.1.11",)),
    MibGroup("printer", "Printer-MIB (RFC 3805)", "inputs, supply, markers, channels", ("1.3.6.1.2.1.43",)),
    MibGroup("host", "HOST-RESOURCES-MIB", "devices, storage, software", ("1.3.6.1.2.1.25",)),
    MibGroup(
        "zebra",
        "ZEBRA-MIB (Link-OS)",
        f"every registered Link-OS branch under {ENTERPRISE}",
        (ENTERPRISE,),
    ),
    MibGroup("zql", "ZQL branch (Link-OS .200)", f"the whole '200' range ({ZQL})", (ZQL,)),
    MibGroup("odometer", "zql-ql-odometer (Link-OS .200.17)", "label / print-length odometers", (ODOMETER,)),
)

MIB_BY_KEY: dict[str, MibGroup] = {g.key: g for g in MIB_GROUPS}

# SNMP type name -> how we render it in text/CSV output.
TYPE_NAMES: dict[str, str] = {
    "Integer": "INTEGER",
    "Integer32": "INTEGER",
    "Counter32": "COUNTER32",
    "Counter64": "COUNTER64",
    "Gauge32": "GAUGE32",
    "Unsigned32": "GAUGE32",
    "TimeTicks": "TIMETICKS",
    "IpAddress": "IP_ADDRESS",
    "ObjectIdentifier": "OBJECT_ID",
    "OctetString": "OCTET_STRING",
    "Bits": "BITS",
    "Opaque": "OPAQUE",
    "Null": "NULL",
    "NoSuchInstance": "NO_SUCH_INSTANCE",
    "NoSuchObject": "NO_SUCH_OBJECT",
    "EndOfMibView": "END_OF_MIB_VIEW",
    "Unspecified": "UNSPECIFIED",
}

# Value types that terminate a walk instead of being recorded.
TERMINAL_TYPES = frozenset({"Null", "NoSuchInstance", "NoSuchObject", "EndOfMibView", "Unspecified"})

V1 = 0  # mpModel 0 == SNMPv1
V2C = 1  # mpModel 1 == SNMPv2c


# --- data model --------------------------------------------------------------


@dataclass(frozen=True)
class RootSpec:
    """A single OID subtree to walk."""

    mib: str  # MIB group key ("custom" for --root)
    oid: str
    label: str


@dataclass
class VarBind:
    """One walked OID."""

    oid: str
    value_type: str
    value: Any
    symbol: str | None = None


@dataclass
class SubtreeReport:
    """Result of walking one subtree on one host."""

    mib: str
    root: str
    label: str
    binds: list[VarBind] = field(default_factory=list)
    error: str | None = None
    truncated: bool = False
    elapsed: float = 0.0

    @property
    def count(self) -> int:
        return len(self.binds)


@dataclass
class HostReport:
    """Result of a full walk on one host."""

    ip: str
    reachable: bool = False
    version: int = V2C
    sysdescr: str = ""
    sysobjectid: str = ""
    error: str | None = None
    subtrees: list[SubtreeReport] = field(default_factory=list)
    elapsed: float = 0.0

    @property
    def total_oids(self) -> int:
        return sum(s.count for s in self.subtrees)

    @property
    def errors(self) -> list[str]:
        out = [f"{s.label}: {s.error}" for s in self.subtrees if s.error]
        if self.error:
            out.insert(0, self.error)
        return out


# --- helpers -----------------------------------------------------------------


def oid_tuple(oid: str) -> tuple[int, ...]:
    """'1.3.6.1.2.1.1' -> (1, 3, 6, 1, 2, 1, 1) for ordering / comparison."""
    try:
        return tuple(int(part) for part in oid.split("."))
    except ValueError:
        return ()


def is_within(oid: str, prefix: str) -> bool:
    """True if ``oid`` equals ``prefix`` or lives under it."""
    return oid == prefix or oid.startswith(prefix + ".")


def symbol_for(oid: str) -> str | None:
    """Longest-prefix lookup in KNOWN_OBJECTS (boundary-safe)."""
    best: str | None = None
    for known in KNOWN_OBJECTS:
        if is_within(oid, known) and (best is None or len(known) > len(best)):
            best = known
    return KNOWN_OBJECTS[best] if best else None


def plain_value(value: Any) -> Any:
    """Convert a pysnmp value object to a plain Python value."""
    name = type(value).__name__
    if name in ("OctetString", "Bits", "Opaque"):
        return bytes(value)
    if name == "ObjectIdentifier":
        return str(value)
    if name == "IpAddress":
        return ".".join(str(b) for b in value)
    if name in ("Integer", "Integer32", "Counter32", "Counter64", "Gauge32", "Unsigned32", "TimeTicks"):
        return int(value)
    return str(value)


def format_value(value: Any, value_type: str) -> str:
    """Render one value the way tools/snmpget.py does."""
    if isinstance(value, bytes):
        decoded = value.decode("ascii", errors="replace")
        return f'{value_type}: {value!r} -> "{decoded}"'
    if value is None:
        return f"{value_type}: NULL"
    return f"{value_type}: {value}"


def json_value(value: Any) -> str | int | None:
    """JSON-safe rendering of a walked value."""
    if isinstance(value, bytes):
        try:
            return value.decode("ascii")
        except UnicodeDecodeError:
            return value.hex()
    if isinstance(value, int):
        return value
    return str(value) if value is not None else None


def render_value(value: Any) -> str:
    """CSV-safe rendering of a walked value."""
    if isinstance(value, bytes):
        return value.decode("ascii", errors="replace")
    return "" if value is None else str(value)


def version_name(version: int) -> str:
    return "SNMPv1" if version == V1 else "SNMPv2c"


# --- target expansion --------------------------------------------------------


def expand_target(spec: str) -> list[str]:
    """Expand one target spec: IP, host, '200.10.5.*' wildcard or CIDR."""
    spec = spec.strip()
    if not spec:
        return []
    if "/" in spec:
        network = ipaddress.ip_network(spec, strict=False)
        if network.version != 4:
            return [str(network.network_address)]
        hosts = [str(host) for host in network.hosts()]
        return hosts or [str(network.network_address)]
    if "*" in spec:
        parts = spec.split(".")
        if len(parts) != 4:
            raise ValueError(f"wildcard must have 4 octets: {spec!r}")
        ranges: list[range] = []
        for part in parts:
            if part == "*":
                ranges.append(range(256))
            elif part.isdigit() and 0 <= int(part) <= 255:
                ranges.append(range(int(part), int(part) + 1))
            else:
                raise ValueError(f"bad wildcard octet {part!r} in {spec!r}")
        return [f"{a}.{b}.{c}.{d}" for a, b, c, d in itertools.product(*ranges)]
    return [spec]


def expand_targets(specs: list[str]) -> list[str]:
    """Expand + de-duplicate a list of target specs, keeping order."""
    seen: set[str] = set()
    out: list[str] = []
    for spec in specs:
        for host in expand_target(spec):
            if host not in seen:
                seen.add(host)
                out.append(host)
    return out


def read_targets_file(path: Path) -> list[str]:
    """Read target specs from a file (one per line, '#' comments allowed)."""
    specs: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        entry = line.split("#", 1)[0].strip()
        if entry:
            specs.append(entry)
    return specs


# --- MIB selection -----------------------------------------------------------


def select_roots(keys: set[str], custom: list[str], per_branch: bool) -> list[RootSpec]:
    """Turn ``--mib`` keys + ``--root`` OIDs into a de-duplicated walk plan.

    Overlapping selections are walked once, from the broadest root (walking
    1.3.6.1.4.1.10642 already covers 1.3.6.1.4.1.10642.200 and ...200.17).
    """
    specs: list[RootSpec] = []
    for group in MIB_GROUPS:
        if group.key in keys:
            specs.extend(RootSpec(group.key, root, group.title) for root in group.roots)
    for root in custom:
        specs.append(RootSpec("custom", root, f"custom root {root}"))

    ordered = sorted(specs, key=lambda spec: oid_tuple(spec.oid))
    kept: list[RootSpec] = []
    for spec in ordered:
        if any(is_within(spec.oid, ancestor.oid) for ancestor in kept):
            continue
        kept.append(spec)

    if not per_branch:
        return kept

    expanded: list[RootSpec] = []
    for spec in kept:
        table = BRANCH_TABLES.get(spec.oid)
        if table is None:
            expanded.append(spec)
            continue
        for suffix, label in sorted(table.items(), key=lambda item: int(item[0])):
            expanded.append(RootSpec(spec.mib, f"{spec.oid}.{suffix}", label))
    return expanded


def parse_mib_keys(spec: str) -> set[str]:
    """Parse ``--mib all`` / ``--mib zebra,zql`` / ``--mib none`` into group keys."""
    spec = spec.strip().lower()
    if spec in ("", "all"):
        return {group.key for group in MIB_GROUPS}
    if spec in ("none", "-"):
        return set()
    keys: set[str] = set()
    for part in spec.split(","):
        key = part.strip().lower()
        if not key:
            continue
        if key not in MIB_BY_KEY:
            known = ", ".join(group.key for group in MIB_GROUPS)
            raise ValueError(f"unknown MIB group {key!r} (known: {known}, or 'all'/'none')")
        keys.add(key)
    if not keys:
        raise ValueError("--mib did not select any MIB group")
    return keys


def validate_root(root: str) -> str:
    """Accept only numeric OID roots for ``--root``."""
    root = root.strip().rstrip(".")
    if not root or any(not part.isdigit() for part in root.split(".")):
        raise ValueError(f"--root must be a numeric OID, got {root!r}")
    return root


def print_mib_catalogue() -> None:
    """--list-mibs output."""
    print("MIB groups (--mib, comma-separated; default: all)")
    print()
    for group in MIB_GROUPS:
        print(f"  {group.key:<10} {group.title}")
        print(f"  {'':<10} {group.note}")
        for root in group.roots:
            print(f"  {'':<10}   walks {root}")
        print()
    print("Link-OS branch tables used by --per-branch:")
    print(f"  {ENTERPRISE}  -> {len(ZEBRA_BRANCHES)} registered branches (ZEBRA-MIB)")
    print(f"  {ZQL}  -> {len(ZQL_BRANCHES)} registered branches (the '200' range)")
    print(f"  {ODOMETER}  -> {len(ODOMETER_BRANCHES)} registered objects (odometer)")
    print()
    print("Any extra subtree can be walked with --root <numeric OID>.")


# --- SNMP session ------------------------------------------------------------


@dataclass
class SnmpSession:
    """One SNMP conversation with one printer (shared dispatcher)."""

    ip: str
    port: int
    community: str
    version: int
    timeout: float
    retries: int
    dispatcher: SnmpDispatcher
    auth: CommunityData
    target: Any

    def set_version(self, version: int) -> None:
        self.version = version
        self.auth = CommunityData(self.community, mpModel=version)


async def open_session(
    ip: str,
    port: int,
    community: str,
    version: int,
    timeout: float,
    retries: int,
    dispatcher: SnmpDispatcher,
) -> SnmpSession:
    """Open a UDP transport to one printer."""
    target = await UdpTransportTarget.create((ip, port), timeout=timeout, retries=retries)
    return SnmpSession(
        ip=ip,
        port=port,
        community=community,
        version=version,
        timeout=timeout,
        retries=retries,
        dispatcher=dispatcher,
        auth=CommunityData(community, mpModel=version),
        target=target,
    )


async def probe(session: SnmpSession) -> tuple[bool, str, str, str]:
    """GET sysDescr + sysObjectID in one PDU. Returns (reachable, descr, oid, error)."""
    var_binds = (
        ObjectType(ObjectIdentity(SYSDESCR_OID)),
        ObjectType(ObjectIdentity(SYSOBJECTID_OID)),
    )
    error_indication, _error_status, _error_index, response = await get_cmd(
        session.dispatcher,
        session.auth,
        session.target,
        *var_binds,
    )
    if error_indication:
        return False, "", "", str(error_indication)

    def read_at(index: int) -> str:
        if index >= len(response):
            return ""
        _oid, value = response[index]
        if type(value).__name__ in TERMINAL_TYPES:
            return ""
        plain = plain_value(value)
        if isinstance(plain, bytes):
            return plain.decode("ascii", errors="replace").strip()
        return str(plain)

    return True, read_at(0), read_at(1), ""


# --- walking -----------------------------------------------------------------


async def walk_subtree(session: SnmpSession, spec: RootSpec, max_oids: int, repetitions: int) -> SubtreeReport:
    """Walk one OID subtree (GETBULK on v2c, GETNEXT on v1)."""
    report = SubtreeReport(mib=spec.mib, root=spec.oid, label=spec.label)
    started = time.monotonic()
    prefix = spec.oid
    seen: set[str] = set()
    last = oid_tuple(prefix)

    try:
        if session.version == V1:
            rows = walk_cmd(
                session.dispatcher,
                session.auth,
                session.target,
                ObjectType(ObjectIdentity(prefix)),
                lexicographicMode=False,
                lookupMib=False,
                ignoreNonIncreasingOid=True,
            )
        else:
            rows = bulk_walk_cmd(
                session.dispatcher,
                session.auth,
                session.target,
                0,
                repetitions,
                ObjectType(ObjectIdentity(prefix)),
                lexicographicMode=False,
                lookupMib=False,
                ignoreNonIncreasingOid=True,
            )

        async for error_indication, error_status, error_index, var_binds in rows:
            if error_indication:
                report.error = str(error_indication)
                break
            if error_status:
                status = error_status.prettyPrint() if hasattr(error_status, "prettyPrint") else str(error_status)
                report.error = f"error-status {status} at index {error_index}"
                break
            if not var_binds:
                break

            stop = False
            for oid_obj, value_obj in var_binds:
                oid_str = str(oid_obj)
                value_type = TYPE_NAMES.get(type(value_obj).__name__, type(value_obj).__name__)

                if not is_within(oid_str, prefix):
                    stop = True  # walked past the subtree
                    break
                if value_type in TERMINAL_TYPES:
                    stop = True  # endOfMibView / noSuchInstance: subtree exhausted
                    break
                if oid_str in seen:
                    report.error = f"repeated OID {oid_str}, stopping"
                    stop = True
                    break

                current = oid_tuple(oid_str)
                if current <= last:
                    report.error = f"non-increasing OID {oid_str}, stopping"
                    stop = True
                    break
                seen.add(oid_str)
                last = current

                report.binds.append(VarBind(oid_str, value_type, plain_value(value_obj), symbol_for(oid_str)))
                if len(report.binds) >= max_oids:
                    report.truncated = True
                    stop = True
                    break

            if stop:
                break
            if not var_binds:
                break
    except Exception as exc:  # never let one subtree kill the whole host walk
        report.error = f"{type(exc).__name__}: {exc}"

    report.elapsed = time.monotonic() - started
    return report


TIMEOUT_MARKERS = ("timeout", "timed out", "no snmp response")


def is_timeout_error(message: str | None) -> bool:
    lowered = (message or "").lower()
    return any(marker in lowered for marker in TIMEOUT_MARKERS)


async def walk_host(
    ip: str,
    index: int,
    roots: list[RootSpec],
    *,
    port: int,
    community: str,
    version: int,
    timeout: float,
    retries: int,
    repetitions: int,
    max_oids: int,
    v1_fallback: bool,
    do_probe: bool,
    semaphore: asyncio.Semaphore,
) -> tuple[int, HostReport]:
    """Walk every planned subtree on one host (never raises).

    Each host gets its own SnmpDispatcher: pysnmp shares ONE UDP socket per
    dispatcher across all destinations and keys in-flight requests by
    requestId alone, so concurrent hosts on a shared dispatcher can lose
    each other's responses (a dead peer's ICMP error is enough). One
    dispatcher per host keeps every conversation isolated -- and requests
    within a host are sequential, so request ids never collide either.
    """
    started = time.monotonic()
    report = HostReport(ip=ip, version=version)
    dispatcher = SnmpDispatcher()

    async with semaphore:
        try:
            session = await open_session(ip, port, community, version, timeout, retries, dispatcher)

            if do_probe:
                reachable, sysdescr, sysobjectid, probe_error = await probe(session)
                if not reachable and v1_fallback and version == V2C:
                    # Some Link-OS / legacy agents (GX430t) answer v1 only.
                    session.set_version(V1)
                    reachable, sysdescr, sysobjectid, probe_error = await probe(session)
                    if reachable:
                        report.version = V1
                    else:
                        session.set_version(V2C)  # keep the header honest
                if not reachable:
                    report.error = f"probe failed: {probe_error}"
                    report.elapsed = time.monotonic() - started
                    return index, report
                report.reachable = True
                report.sysdescr = sysdescr
                report.sysobjectid = sysobjectid
            else:
                report.reachable = True

            timeouts = 0
            for spec in roots:
                if timeouts >= 2:
                    report.subtrees.append(
                        SubtreeReport(
                            mib=spec.mib, root=spec.oid, label=spec.label, error="skipped (target stopped responding)"
                        )
                    )
                    continue
                subtree = await walk_subtree(session, spec, max_oids, repetitions)
                report.subtrees.append(subtree)
                if subtree.error and is_timeout_error(subtree.error):
                    timeouts += 1
                else:
                    timeouts = 0
        except Exception as exc:  # transport setup failures, DNS errors, ...
            report.error = f"{type(exc).__name__}: {exc}"
        finally:
            report.elapsed = time.monotonic() - started
            with contextlib.suppress(Exception):
                dispatcher.close()  # frees the per-host UDP socket

    return index, report


# --- rendering ---------------------------------------------------------------


def linkos_branches(report: HostReport) -> list[tuple[str, str, int]]:
    """Bucket Link-OS OIDs by their 1.3.6.1.4.1.10642.<n> branch."""
    counts: dict[str, int] = {}
    prefix = ENTERPRISE + "."
    for subtree in report.subtrees:
        for bind in subtree.binds:
            if bind.oid.startswith(prefix):
                suffix = bind.oid[len(prefix) :].split(".", 1)[0]
                counts[suffix] = counts.get(suffix, 0) + 1
    return [
        (suffix, ZEBRA_BRANCHES.get(suffix, f"branch {suffix}"), count)
        for suffix, count in sorted(counts.items(), key=lambda item: int(item[0]))
    ]


def render_host(report: HostReport, quiet: bool, index: int, total: int) -> str:
    """Render one host block for stdout."""
    lines: list[str] = []
    header = f"=== [{index}/{total}] {report.ip}  {version_name(report.version)} ==="
    lines.append(header)
    lines.append("-" * len(header))

    if not report.reachable:
        lines.append(f"UNREACHABLE: {report.error or 'no response'}")
        lines.append("")
        return "\n".join(lines)

    lines.append(f"sysDescr    : {report.sysdescr or '(empty)'}")
    lines.append(f"sysObjectID : {report.sysobjectid or '(empty)'}")
    if report.sysobjectid.startswith(ENTERPRISE):
        lines.append(f"vendor      : Zebra Link-OS (enterprise {ENTERPRISE})")
    if report.error:
        lines.append(f"host error  : {report.error}")

    for subtree in report.subtrees:
        status = f"{subtree.count} OIDs"
        if subtree.error:
            status += f" | ERROR: {subtree.error}"
        if subtree.truncated:
            status += " | TRUNCATED (--max-oids)"
        if subtree.count == 0 and not subtree.error:
            status = "empty (not implemented by this agent)"
        lines.append("")
        lines.append(f"-- {subtree.mib}: {subtree.label} [{subtree.root}] {status} in {subtree.elapsed:.2f}s --")
        if quiet:
            continue
        for bind in subtree.binds:
            line = f"{bind.oid} = {format_value(bind.value, bind.value_type)}"
            if bind.symbol:
                line += f"   # {bind.symbol}"
            lines.append(line)

    branches = linkos_branches(report)
    if branches:
        lines.append("")
        lines.append(f"Link-OS branch coverage on {report.ip} ({ENTERPRISE}.x):")
        for suffix, label, count in branches:
            marker = "   <- the '200' range" if suffix == "200" else ""
            lines.append(f"  .{suffix:<4} {label:<28} {count:>5} OIDs{marker}")

    lines.append("")
    return "\n".join(lines)


def render_summary(reports: list[HostReport], roots: list[RootSpec]) -> str:
    """Final table across every host."""
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append("Summary")
    lines.append("=" * 78)
    lines.append(f"{'Host':<16} {'Proto':<8} {'Reach':<6} {'OIDs':>6} {'Got/All':>9} {'Time':>8}  Notes")
    for report in reports:
        ok = sum(1 for s in report.subtrees if not s.error and s.count)
        notes = "; ".join(report.errors)
        if len(notes) > 60:
            notes = notes[:57] + "..."
        lines.append(
            f"{report.ip:<16} {version_name(report.version):<8} "
            f"{('yes' if report.reachable else 'NO'):<6} {report.total_oids:>6} "
            f"{f'{ok}/{len(roots)}':>9} {report.elapsed:>7.1f}s  {notes}"
        )

    reachable = [r for r in reports if r.reachable]
    total_oids = sum(r.total_oids for r in reachable)
    zql_oids = sum(count for r in reachable for suffix, _label, count in linkos_branches(r) if suffix == "200")
    odometer_oids = sum(
        1 for r in reachable for subtree in r.subtrees for bind in subtree.binds if bind.oid.startswith(ODOMETER + ".")
    )
    lines.append("")
    lines.append(f"hosts reachable : {len(reachable)}/{len(reports)}")
    lines.append(f"OIDs collected   : {total_oids}")
    lines.append(f"Link-OS .200    : {zql_oids} OIDs (the '200' range)")
    lines.append(f"odometer .200.17: {odometer_oids} OIDs")
    return "\n".join(lines)


def write_json(path: Path, settings: dict[str, Any], reports: list[HostReport]) -> None:
    """Write the full structured result."""
    payload = {
        "generated": datetime.now().astimezone().isoformat(timespec="seconds"),
        "tool": "tools/linkos_walk.py",
        "settings": settings,
        "hosts": [
            {
                "ip": report.ip,
                "reachable": report.reachable,
                "version": version_name(report.version),
                "sysdescr": report.sysdescr,
                "sysobjectid": report.sysobjectid,
                "error": report.error,
                "elapsed": round(report.elapsed, 3),
                "oid_count": report.total_oids,
                "linkos_branches": [
                    {"branch": suffix, "name": label, "count": count}
                    for suffix, label, count in linkos_branches(report)
                ],
                "subtrees": [
                    {
                        "mib": subtree.mib,
                        "root": subtree.root,
                        "label": subtree.label,
                        "count": subtree.count,
                        "error": subtree.error,
                        "truncated": subtree.truncated,
                        "elapsed": round(subtree.elapsed, 3),
                        "binds": [
                            {
                                "oid": bind.oid,
                                "type": bind.value_type,
                                "value": json_value(bind.value),
                                "symbol": bind.symbol,
                            }
                            for bind in subtree.binds
                        ],
                    }
                    for subtree in report.subtrees
                ],
            }
            for report in reports
        ],
    }
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")


def write_csv(path: Path, reports: list[HostReport]) -> None:
    """Write one row per walked OID."""
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["host", "mib", "root", "oid", "type", "value", "symbol", "error"])
        for report in reports:
            for subtree in report.subtrees:
                if not subtree.binds and subtree.error:
                    writer.writerow([report.ip, subtree.mib, subtree.root, "", "", "", "", subtree.error])
                    continue
                for bind in subtree.binds:
                    writer.writerow(
                        [
                            report.ip,
                            subtree.mib,
                            subtree.root,
                            bind.oid,
                            bind.value_type,
                            render_value(bind.value),
                            bind.symbol or "",
                            "",
                        ]
                    )


# --- config ------------------------------------------------------------------


def default_config_path() -> Path:
    return Path(__file__).resolve().parents[1] / "config" / "config.json"


def load_config(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} does not contain a JSON object")
    return data


def config_printer_ips(config: dict[str, Any], model: str | None) -> list[str]:
    """IPs from config, optionally filtered by model substring."""
    printers = config.get("printers", [])
    ips: list[str] = []
    model_lower = model.lower() if model else None
    for printer in printers:
        if not isinstance(printer, dict):
            continue
        ip = str(printer.get("ip", "")).strip()
        printer_model = str(printer.get("model", ""))
        if not ip:
            continue
        if model_lower and model_lower not in printer_model.lower():
            continue
        ips.append(ip)
    return ips


# --- CLI ---------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="linkos_walk.py",
        description="Walk the known Link-OS / ZEBRA-MIB OIDs of a Zebra printer (ZT411 & friends).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python tools/linkos_walk.py 10.0.1.11\n"
            "  python tools/linkos_walk.py 10.0.1.11 --mib zebra,zql\n"
            "  python tools/linkos_walk.py 10.0.1.11 --root 1.3.6.1.4.1.10642.200.17\n"
            "  python tools/linkos_walk.py 200.10.5.0/24 --mib zql --quiet --json walk.json\n"
            '  python tools/linkos_walk.py --config --model "Zebra ZT411"\n'
            "  python tools/linkos_walk.py --list-mibs\n"
        ),
    )
    parser.add_argument("targets", nargs="*", help="IPs, hosts, wildcards (200.10.5.*) or CIDR ranges")
    parser.add_argument(
        "--targets-file", action="append", default=[], metavar="FILE", help="file with one target per line"
    )
    parser.add_argument(
        "--config",
        nargs="?",
        const=str(default_config_path()),
        default=None,
        metavar="PATH",
        help="read printers + SNMP defaults from config/config.json (auto when no targets given)",
    )
    parser.add_argument("--model", metavar="SUBSTR", help="filter config printers by model substring")
    parser.add_argument("--mib", default=None, metavar="LIST", help="MIB groups, comma-separated, or 'all' (default)")
    parser.add_argument(
        "--root",
        action="append",
        default=[],
        metavar="OID",
        help="walk only this OID subtree (repeatable); overrides the --mib default",
    )
    parser.add_argument("--per-branch", action="store_true", help="split Link-OS roots into their registered branches")
    parser.add_argument("--list-mibs", action="store_true", help="print the MIB catalogue and exit")

    version = parser.add_mutually_exclusive_group()
    version.add_argument("-v1", action="store_const", dest="version", const=V1, help="use SNMPv1")
    version.add_argument("-v2c", action="store_const", dest="version", const=V2C, help="use SNMPv2c (default)")
    parser.set_defaults(version=V2C)
    parser.add_argument("--no-v1-fallback", action="store_true", help="do not retry the probe with SNMPv1")
    parser.add_argument("--community", default=None, help="SNMP community (default: config, else public)")
    parser.add_argument("--port", type=int, default=161, help="SNMP UDP port (default: 161)")
    parser.add_argument("--timeout", type=float, default=None, help="timeout per request in seconds (default: 3)")
    parser.add_argument("--retries", type=int, default=None, help="transport retries per request (default: 1)")
    parser.add_argument("--repetitions", type=int, default=25, help="GETBULK max-repetitions (default: 25)")
    parser.add_argument("--max-oids", type=int, default=10000, help="cap per subtree (default: 10000)")
    parser.add_argument("--concurrency", type=int, default=8, help="parallel hosts (default: 8)")
    parser.add_argument("--max-hosts", type=int, default=2048, help="refuse ranges bigger than this (default: 2048)")
    parser.add_argument("--no-probe", action="store_true", help="skip the sysDescr/sysObjectID reachability probe")
    parser.add_argument("-q", "--quiet", action="store_true", help="do not print individual OIDs, only summaries")
    parser.add_argument("--json", metavar="PATH", help="write the full result as JSON")
    parser.add_argument("--csv", metavar="PATH", help="write one row per OID as CSV")
    parser.add_argument("--out", metavar="PATH", help="also write the text report to a file")
    return parser


async def run(args: argparse.Namespace, roots: list[RootSpec], targets: list[str]) -> list[HostReport]:
    """Walk every target concurrently; print each host as it completes."""
    semaphore = asyncio.Semaphore(max(1, args.concurrency))
    tasks = [
        asyncio.create_task(
            walk_host(
                ip,
                index,
                roots,
                port=args.port,
                community=args.community,
                version=args.version,
                timeout=args.timeout,
                retries=args.retries,
                repetitions=args.repetitions,
                max_oids=args.max_oids,
                v1_fallback=not args.no_v1_fallback,
                do_probe=not args.no_probe,
                semaphore=semaphore,
            )
        )
        for index, ip in enumerate(targets, start=1)
    ]

    reports: list[HostReport | None] = [None] * len(targets)
    for task in asyncio.as_completed(tasks):
        index, report = await task
        reports[index - 1] = report
        print(render_host(report, args.quiet, index, len(targets)), flush=True)

    # Return reports in target order so --out / summaries read top to bottom.
    return [report for report in reports if report is not None]


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.list_mibs:
        print_mib_catalogue()
        return 0

    # --- plan: which subtrees -----------------------------------------------
    # --mib defaults to "all", but an explicit --root on its own means
    # "walk exactly this subtree".
    try:
        custom_roots = [validate_root(root) for root in args.root]
        mib_spec = args.mib if args.mib is not None else ("none" if custom_roots else "all")
        keys = parse_mib_keys(mib_spec)
    except ValueError as exc:
        parser.error(str(exc))
        return 2  # pragma: no cover - parser.error() exits
    if not custom_roots and not keys:
        parser.error("nothing to walk")
        return 2  # pragma: no cover

    roots = select_roots(keys, custom_roots, args.per_branch)

    # --- plan: which hosts ---------------------------------------------------
    spec_list: list[str] = [*args.targets]
    for target_file in args.targets_file:
        path = Path(target_file)
        if not path.is_file():
            parser.error(f"--targets-file not found: {path}")
            return 2  # pragma: no cover
        spec_list.extend(read_targets_file(path))
    for entry in [s for s in spec_list if "," in s]:
        spec_list.remove(entry)
        spec_list.extend(part for part in entry.split(",") if part.strip())

    config: dict[str, Any] | None = None
    config_path: Path | None = Path(args.config) if args.config else None
    if not spec_list and config_path is None:
        config_path = default_config_path()  # no targets -> fall back to the config

    if config_path is not None:
        if config_path.is_file():
            try:
                config = load_config(config_path)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                print(f"warning: cannot read {config_path}: {exc}", file=sys.stderr)
        else:
            print(f"warning: config not found: {config_path}", file=sys.stderr)

    if not spec_list:
        if config is None:
            parser.error("no targets given and no readable config/config.json")
            return 2  # pragma: no cover
        spec_list = config_printer_ips(config, args.model)
        if not spec_list:
            parser.error(
                f"config {config_path} has no printers" + (f" matching --model {args.model!r}" if args.model else "")
            )
            return 2  # pragma: no cover

    try:
        targets = expand_targets(spec_list)
    except ValueError as exc:
        parser.error(str(exc))
        return 2  # pragma: no cover
    if not targets:
        parser.error("target expansion produced no hosts")
        return 2  # pragma: no cover
    if len(targets) > args.max_hosts:
        parser.error(f"{len(targets)} hosts exceeds --max-hosts={args.max_hosts}")
        return 2  # pragma: no cover

    # --- SNMP defaults: CLI > config > built-in -------------------------------
    snmp_config = (config or {}).get("snmp", {}) if isinstance(config, dict) else {}
    if not isinstance(snmp_config, dict):
        snmp_config = {}
    args.community = args.community or str(snmp_config.get("community", "public"))
    if args.timeout is None:
        args.timeout = float(snmp_config.get("timeout_sec", 3))
    if args.retries is None:
        args.retries = int(snmp_config.get("retries", 1))

    mib_label = ",".join(dict.fromkeys(spec.mib for spec in roots))
    print(
        f"Link-OS walk: {len(targets)} host(s), {len(roots)} subtree(s) [{mib_label}], "
        f"{version_name(args.version)}, community={args.community}, "
        f"timeout={args.timeout:g}s, retries={args.retries}",
        file=sys.stderr,
    )

    reports = asyncio.run(run(args, roots, targets))

    if args.out:
        text_report = "\n".join(render_host(report, args.quiet, i, len(reports)) for i, report in enumerate(reports, 1))
        Path(args.out).write_text(text_report + render_summary(reports, roots) + "\n", encoding="utf-8")
    if args.json:
        settings = {
            "targets": targets,
            "roots": [{"mib": s.mib, "root": s.oid, "label": s.label} for s in roots],
            "version": version_name(args.version),
            "community": args.community,
            "port": args.port,
            "timeout": args.timeout,
            "retries": args.retries,
            "repetitions": args.repetitions,
            "max_oids": args.max_oids,
        }
        write_json(Path(args.json), settings, reports)
    if args.csv:
        write_csv(Path(args.csv), reports)

    reachable = sum(1 for report in reports if report.reachable)
    print(render_summary(reports, roots))
    if reachable == 0:
        print("no host answered SNMP", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
