"""Abstract base class for printer adapters.

All printer adapters must implement this interface.
"""

from abc import ABC, abstractmethod
from typing import Optional

from snmp_client import TAG_COUNTER32, TAG_GAUGE32, TAG_INTEGER, TAG_OCTET_STRING


class PrinterAdapter(ABC):
    """Abstract adapter for querying a printer via SNMP.

    Subclasses must implement get_counters() and define OIDS dict.
    """

    OIDS = {}  # Subclasses override this

    def __init__(self, ip, community='public', timeout_sec=5, retries=2,
                 version=0, **kwargs):
        self.ip = ip
        self.community = community
        self.timeout_sec = timeout_sec
        self.retries = retries
        self.version = version  # 0=SNMPv1, 1=SNMPv2c

    @abstractmethod
    def get_counters(self) -> dict:
        """Query the printer and return counters.

        Returns:
            dict with keys:
                labels_total (int): Total labels printed (lifetime).
                meters_total (float): Total media length printed (lifetime).
                meter_unit (str): Unit for meters_total ('cm', 'mm', 'in', etc.).
                model_name (str): Printer model from SNMP.
                serial (str): Serial number.
                status (str): Current status ('idle', 'printing', 'error', 'offline').
                reachable (bool): Whether the printer responded.

        On failure, returns dict with reachable=False and other fields as None/empty.
        """
        ...

    @abstractmethod
    def is_reachable(self) -> bool:
        """Check if the printer is reachable via SNMP."""
        ...

    def _snmp_get(self, oid):
        """Helper: single SNMP GET. Returns (value, type_tag) or (None, None)."""
        from snmp_client import get, SnmpTimeout, SnmpError
        try:
            value, type_tag = get(
                self.ip, oid,
                community=self.community,
                timeout_sec=self.timeout_sec,
                retries=self.retries,
                version=self.version,
            )
            return value, type_tag
        except (SnmpTimeout, SnmpError):
            return None, None

    def _snmp_get_multiple(self, oids):
        """Helper: multi-OID SNMP GET. Returns list of (oid, value, type_tag)."""
        from snmp_client import get_multiple, SnmpTimeout, SnmpError
        try:
            return get_multiple(
                self.ip, oids,
                community=self.community,
                timeout_sec=self.timeout_sec,
                retries=self.retries,
            )
        except (SnmpTimeout, SnmpError):
            return [(oid, None, None) for oid in oids]

    @staticmethod
    def _status_from_code(code):
        """Convert hrPrinterStatus integer to string."""
        if code is None:
            return 'offline'
        status_map = {
            1: 'other',
            2: 'unknown',
            3: 'idle',
            4: 'printing',
            5: 'warmup',
            6: 'stopping',
            7: 'down',
        }
        return status_map.get(code, 'unknown')
