"""SNMP v1/v2c client backed by pysnmp.

Provides sync get(), get_bulk() wrappers around pysnmp's async API.
Maintains the same interface as the old pure-Python client so adapters
don't need to change.
"""

import asyncio
from typing import Any

from pysnmp.hlapi.v1arch.asyncio import (
    CommunityData,
    ObjectIdentity,
    ObjectType,
    SnmpDispatcher,
    UdpTransportTarget,
)
from pysnmp.hlapi.v1arch.asyncio import (
    bulk_cmd as _async_bulk_cmd,
)
from pysnmp.hlapi.v1arch.asyncio import (
    get_cmd as _async_get_cmd,
)

# --- BER tag constants derived from pysnmp ---
from pysnmp.proto import rfc1902


def _ber_tag(pysnmp_type: Any) -> int:
    """Extract BER tag integer from a pysnmp type class."""
    t = pysnmp_type.tagSet[0]
    return int(t.tagClass | t.tagId)


TAG_INTEGER: int = _ber_tag(rfc1902.Integer)
TAG_OCTET_STRING: int = _ber_tag(rfc1902.OctetString)
TAG_NULL: int = _ber_tag(rfc1902.Null)
TAG_OID: int = _ber_tag(rfc1902.ObjectIdentifier)
TAG_COUNTER32: int = _ber_tag(rfc1902.Counter32)
TAG_GAUGE32: int = _ber_tag(rfc1902.Gauge32)
TAG_TIMETICKS: int = _ber_tag(rfc1902.TimeTicks)
TAG_IP_ADDRESS: int = _ber_tag(rfc1902.IpAddress)

VERSION_2C: int = 1


# --- Exceptions ---


class SnmpError(Exception):
    pass


class SnmpTimeout(SnmpError):
    pass


class SnmpAuthenticationError(SnmpError):
    pass


class SnmpNoSuchObject(SnmpError):
    pass


class SnmpNoSuchInstance(SnmpError):
    pass


class SnmpEndOfMibView(SnmpError):
    pass


class SnmpBadStatus(SnmpError):
    def __init__(self, error_status: Any, error_index: Any, oid: str | None = None) -> None:
        self.error_status = error_status
        self.error_index = error_index
        self.oid = oid
        super().__init__(f"SNMP error-status {error_status} at index {error_index}")


# --- pysnmp helpers ---


def _pysnmp_tag(value: Any) -> int:
    """Map pysnmp type to our legacy BER tag constants."""
    type_name = type(value).__name__
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
        "Null": TAG_NULL,
        "Bits": TAG_OCTET_STRING,
        "Opaque": TAG_OCTET_STRING,
    }
    return tag_map.get(type_name, TAG_OCTET_STRING)


def _extract_value(pysnmp_val: Any) -> Any:
    """Extract a plain Python value from a pysnmp object."""
    type_name = type(pysnmp_val).__name__
    if type_name in ("OctetString", "Bits", "Opaque"):
        raw = bytes(pysnmp_val)
        try:
            decoded = raw.decode("ascii")
            if all(32 <= ord(c) < 127 or c in "\r\n\t" for c in decoded):
                return raw  # return bytes for backward compat
        except Exception:
            pass
        return raw
    if type_name == "ObjectIdentifier":
        return str(pysnmp_val)
    if type_name == "IpAddress":
        return ".".join(str(b) for b in pysnmp_val)
    if type_name in (
        "Integer",
        "Integer32",
        "Counter32",
        "Counter64",
        "Gauge32",
        "Unsigned32",
        "TimeTicks",
    ):
        return int(pysnmp_val)
    return pysnmp_val


def _create_target(ip: str, port: int, timeout_sec: int | float, retries: int) -> Any:
    """Create a pysnmp UdpTransportTarget."""
    return UdpTransportTarget.create((ip, port), timeout=timeout_sec, retries=retries)


# --- Public API ---


async def _async_get(
    ip: str,
    oid: str,
    community: str = "public",
    timeout_sec: int = 3,
    retries: int = 2,
    port: int = 161,
    version: int = VERSION_2C,
) -> tuple[Any, int]:
    """Async SNMP GET. Returns (value, type_tag)."""
    dispatcher = SnmpDispatcher()
    target = await _create_target(ip, port, timeout_sec, retries)
    error_indication, error_status, error_index, var_binds = await _async_get_cmd(
        dispatcher,
        CommunityData(community, mpModel=version),
        target,
        ObjectType(ObjectIdentity(oid)),
    )
    if error_indication:
        raise SnmpTimeout(str(error_indication))
    if error_status:
        if error_status.prettyPrint() == "noSuchName":
            raise SnmpNoSuchInstance(f"noSuchName for OID {oid}")
        raise SnmpBadStatus(error_status, error_index, oid)
    if not var_binds:
        raise SnmpError("Empty response")
    val = _extract_value(var_binds[0][1])
    tag = _pysnmp_tag(var_binds[0][1])
    return val, tag


async def _async_get_bulk(
    ip: str,
    oid: str,
    community: str = "public",
    max_repetitions: int = 10,
    timeout_sec: int = 3,
    retries: int = 2,
    port: int = 161,
    version: int = VERSION_2C,
) -> list[tuple[str, Any, int]]:
    """Async SNMP GETBULK. Returns list of (oid, value, type_tag)."""
    dispatcher = SnmpDispatcher()
    target = await _create_target(ip, port, timeout_sec, retries)
    results: list[tuple[str, Any, int]] = []
    async for error_indication, error_status, error_index, var_binds in _async_bulk_cmd(
        dispatcher,
        CommunityData(community, mpModel=version),
        target,
        ObjectType(ObjectIdentity(oid)),
        max_repetitions,
        lexicographic_mode=False,
    ):
        if error_indication:
            raise SnmpTimeout(str(error_indication))
        if error_status:
            raise SnmpBadStatus(error_status, error_index, oid)
        for oid_obj, val_obj in var_binds:
            results.append((str(oid_obj), _extract_value(val_obj), _pysnmp_tag(val_obj)))
        if len(results) >= max_repetitions:
            break
    return results


def get(
    ip: str,
    oid: str,
    community: str = "public",
    timeout_sec: int = 3,
    retries: int = 2,
    port: int = 161,
    version: int = VERSION_2C,
) -> tuple[Any, int]:
    """SNMP GET. Returns (value, type_tag).

    Retries with exponential backoff on timeout.
    """
    last_error: SnmpTimeout | None = None
    for attempt in range(retries + 1):
        try:
            return asyncio.run(
                _async_get(ip, oid, community, timeout_sec, retries=0, port=port, version=version)
            )
        except SnmpTimeout as e:
            last_error = e
            if attempt < retries:
                import time

                time.sleep(0.5 * (2**attempt))
        except (SnmpNoSuchInstance, SnmpEndOfMibView, SnmpBadStatus, SnmpError):
            raise
    assert last_error is not None
    raise last_error


def get_bulk(
    ip: str,
    oid: str,
    community: str = "public",
    max_repetitions: int = 10,
    timeout_sec: int = 3,
    retries: int = 2,
    port: int = 161,
    version: int = VERSION_2C,
) -> list[tuple[str, Any, int]]:
    """SNMP GETBULK. Returns list of (oid, value, type_tag)."""
    return asyncio.run(
        _async_get_bulk(ip, oid, community, max_repetitions, timeout_sec, retries, port, version)
    )


# get_multiple kept for backward compat (delegates to individual gets)
def get_multiple(
    ip: str,
    oids: list[str],
    community: str = "public",
    timeout_sec: int = 3,
    retries: int = 2,
    port: int = 161,
) -> list[tuple[str, Any, int | None]]:
    """Multi-OID GET. Returns list of (oid, value, type_tag)."""
    results: list[tuple[str, Any, int | None]] = []
    for oid in oids:
        try:
            value, type_tag = get(ip, oid, community, timeout_sec, retries, port=port)
            results.append((oid, value, type_tag))
        except (SnmpTimeout, SnmpError):
            results.append((oid, None, None))
    return results
