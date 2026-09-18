"""Abstract base class for printer adapters.

All printer adapters must implement this interface.
"""

import logging
import time
from abc import ABC, abstractmethod


class PrinterAdapter(ABC):
    """Abstract adapter for querying a printer via SNMP."""

    OIDS = {}

    def __init__(self, ip, community='public', timeout_sec=5, retries=2,
                 version=0, **kwargs):
        self.ip = ip
        self.community = community
        self.timeout_sec = timeout_sec
        self.retries = retries
        self.version = version

    @abstractmethod
    def get_counters(self) -> dict:
        """Query the printer and return counters."""
        ...

    @abstractmethod
    def is_reachable(self) -> bool:
        """Check if the printer is reachable via SNMP."""
        ...

    def _snmp_get(self, oid, label=None):
        """Single SNMP GET. Returns (value, type_tag) or (None, None)."""
        from snmp_client import get, SnmpTimeout, SnmpError
        tag = label or oid
        try:
            logging.debug(f"  SNMP GET  {self.ip} {oid}  [{tag}]")
            value, type_tag = get(
                self.ip, oid,
                community=self.community,
                timeout_sec=self.timeout_sec,
                retries=self.retries,
                version=self.version,
            )
            logging.debug(f"  SNMP RESP {self.ip} {oid} = {value!r} (tag=0x{type_tag:02x})  [{tag}]")
            return value, type_tag
        except (SnmpTimeout, SnmpError) as e:
            logging.debug(f"  SNMP FAIL {self.ip} {oid}  [{tag}]: {e}")
            return None, None

    def _snmp_get_retry(self, oid, label=None, attempts=3):
        """SNMP GET with exponential backoff retry."""
        for attempt in range(attempts):
            value, type_tag = self._snmp_get(oid, label=label)
            if value is not None:
                return value, type_tag
            if attempt < attempts - 1:
                delay = 0.5 * (2 ** attempt)
                logging.debug(f"  RETRY {self.ip} {label} attempt {attempt + 2}/{attempts} in {delay}s")
                time.sleep(delay)
        return None, None

    @staticmethod
    def _status_from_code(code):
        if code is None:
            return 'offline'
        return {1: 'other', 2: 'unknown', 3: 'idle', 4: 'printing',
                5: 'warmup', 6: 'stopping', 7: 'down'}.get(code, 'unknown')
