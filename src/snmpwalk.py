"""Simple SNMP walk utility.

Usage:
    python snmpwalk.py <ip> <oid>
    python snmpwalk.py <ip> <oid> --community private
    python snmpwalk.py <ip> <oid> --timeout 10 --max 200
"""

import argparse
import asyncio
import sys
from typing import Any

from pysnmp.hlapi.v1arch.asyncio import (
    CommunityData,
    ObjectIdentity,
    ObjectType,
    SnmpDispatcher,
    UdpTransportTarget,
    get_cmd,
)

from snmp_client import (
    TAG_COUNTER32,
    TAG_GAUGE32,
    TAG_INTEGER,
    TAG_IP_ADDRESS,
    TAG_OCTET_STRING,
    TAG_OID,
    TAG_TIMETICKS,
)

TAG_NAMES: dict[int, str] = {
    TAG_INTEGER: "INTEGER",
    TAG_OCTET_STRING: "OCTET_STRING",
    TAG_COUNTER32: "COUNTER32",
    TAG_GAUGE32: "GAUGE32",
    TAG_TIMETICKS: "TIMETICKS",
    TAG_IP_ADDRESS: "IP_ADDRESS",
}


def format_value(value: Any, type_tag: int | None) -> str:
    tag_name = TAG_NAMES.get(type_tag, f"0x{type_tag:02x}") if type_tag else "ERROR"
    if isinstance(value, bytes):
        try:
            decoded = value.decode("ascii", errors="replace")
            return f'{tag_name}: {value!r} -> "{decoded}"'
        except Exception:
            return f"{tag_name}: {value!r}"
    if value is None:
        return f"{tag_name}: NULL"
    return f"{tag_name}: {value}"


def pysnmp_tag(val: Any) -> int:
    type_name = type(val).__name__
    tag_map: dict[str, int] = {
        "Integer": TAG_INTEGER,
        "Integer32": TAG_INTEGER,
        "Counter32": TAG_COUNTER32,
        "Counter64": TAG_COUNTER32,
        "Gauge32": TAG_GAUGE32,
        "Unsigned32": TAG_GAUGE32,
        "TimeTicks": TAG_TIMETICKS,
        "OctetString": TAG_OCTET_STRING,
        "ObjectIdentifier": TAG_OID,
        "IpAddress": TAG_IP_ADDRESS,
    }
    return tag_map.get(type_name, TAG_OCTET_STRING)


def _plain_value(val_obj: Any) -> str | bytes | int:
    """Convert a pysnmp value object to a plain Python value."""
    if type(val_obj).__name__ == "OctetString":
        return bytes(val_obj)
    if hasattr(val_obj, "__int__") and type(val_obj).__name__ in (
        "Integer",
        "Integer32",
        "Counter32",
        "Counter64",
        "Gauge32",
        "Unsigned32",
        "TimeTicks",
    ):
        return int(val_obj)
    return str(val_obj)


async def walk(
    ip: str,
    start_oid: str,
    community: str = "public",
    max_oids: int = 500,
    timeout_sec: int = 5,
    retries: int = 2,
) -> list[tuple[str, str | bytes | int, int]]:
    """Walk an OID subtree using GETNEXT."""
    dispatcher = SnmpDispatcher()
    target = await UdpTransportTarget.create((ip, 161), timeout=timeout_sec, retries=retries)

    results: list[tuple[str, str | bytes | int, int]] = []
    current_oid = start_oid
    seen: set[str] = set()

    for _ in range(max_oids):
        error_indication, error_status, error_index, var_binds = await get_cmd(
            dispatcher,
            CommunityData(community),
            target,
            ObjectType(ObjectIdentity(current_oid)),
        )
        if error_indication:
            print(f"  Error: {error_indication}", file=sys.stderr)
            break
        if error_status:
            print(f"  Error: {error_status.prettyPrint()}", file=sys.stderr)
            break

        for oid_obj, val_obj in var_binds:
            oid_str = str(oid_obj)
            if oid_str in seen:
                return results
            if not oid_str.startswith(start_oid.rstrip(".") + ".") and oid_str != start_oid:
                return results
            seen.add(oid_str)
            tag = pysnmp_tag(val_obj)
            if tag in (0x80, 0x81, 0x82):
                return results

            val = _plain_value(val_obj)
            results.append((oid_str, val, tag))
            current_oid = oid_str

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="SNMP walk utility")
    parser.add_argument("ip", help="Target IP address")
    parser.add_argument("oid", help="Starting OID")
    parser.add_argument("--community", default="public", help="SNMP community (default: public)")
    parser.add_argument(
        "--timeout", type=int, default=5, help="Timeout per OID in seconds (default: 5)"
    )
    parser.add_argument("--retries", type=int, default=2, help="Retry count (default: 2)")
    parser.add_argument("--max", type=int, default=500, help="Max OIDs to walk (default: 500)")
    args = parser.parse_args()

    print(f"Walking {args.oid} on {args.ip} (max={args.max})...")
    print()

    results = asyncio.run(
        walk(
            args.ip,
            args.oid,
            args.community,
            args.max,
            args.timeout,
            args.retries,
        )
    )

    for oid_str, value, type_tag in results:
        print(f"{oid_str} = {format_value(value, type_tag)}")

    print(f"\n{len(results)} OIDs found")


if __name__ == "__main__":
    main()