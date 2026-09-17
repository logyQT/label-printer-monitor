"""Zebra printer adapter. Supports ZT230, ZT411, GX430t, ZD621.

Uses SNMPv1 by default (more reliable on wireless printers).
Vendor OIDs under enterprise 10642.

Tested OIDs on ZT411:
- 10642.1.1.0 = Model name
- 10642.1.4.0 = Friendly name
- 10642.1.9.0 = Serial number
- 10642.3.1.6.0 = Labels count (NONRESET counter)
- 10642.3.1.13.0 = Labels count (RESET counter)
- 25.3.5.1.1.1 = hrPrinterStatus (idle/printing/etc)
"""

from adapters.base import PrinterAdapter, TAG_COUNTER32, TAG_GAUGE32, TAG_INTEGER


# Zebra vendor OIDs (enterprise 10642) - WORKING on ZT411
OID_ZEBRA_MODEL_NAME = '1.3.6.1.4.1.10642.1.1.0'
OID_ZEBRA_FIRMWARE = '1.3.6.1.4.1.10642.1.2.0'
OID_ZEBRA_FRIENDLY_NAME = '1.3.6.1.4.1.10642.1.4.0'
OID_ZEBRA_SERIAL = '1.3.6.1.4.1.10642.1.9.0'
OID_ZEBRA_LABELS_NONRESET = '1.3.6.1.4.1.10642.3.1.6.0'
OID_ZEBRA_LABELS_RESET1 = '1.3.6.1.4.1.10642.3.1.13.0'

# Standard RFC 3805 / Host Resources - WORKING on ZT411
OID_HR_MODEL = '1.3.6.1.2.1.25.3.2.1.3.1'
OID_HR_STATUS = '1.3.6.1.2.1.25.3.5.1.1.1'

# OIDs that DO NOT WORK on ZT411 (timeouts)
# - 10642.20.17.2.0 (QL series labels)
# - 10642.20.17.3.0 (QL series meters) -> noSuchObject
# - 43.10.2.1.4.1.1 (prtMarkerLifeCount) -> timeout
# - 43.10.2.1.3.1 (prtMarkerCounterUnit) -> noSuchObject
# - 43.5.1.1.17.1 (prtGeneralSerialNumber) -> noSuchObject
# - 2.1.1.1.0 (sysDescr) -> timeout on wireless

# prtMarkerCounterUnit values (RFC 3805)
UNIT_MAP = {
    0: 'other',
    1: 'tenThousandthsOfSheets',
    2: 'impressions',
    3: 'sheets',
    4: 'linearFeet',
    5: 'linearMeters',
}


class ZebraAdapter(PrinterAdapter):
    """Adapter for Zebra printers (ZT230, ZT411, GX430t, ZD621).

    Uses SNMPv1 by default for better wireless reliability.
    """

    OIDS = {
        'model_name': OID_ZEBRA_MODEL_NAME,
        'serial': OID_ZEBRA_SERIAL,
        'labels_total': OID_ZEBRA_LABELS_NONRESET,
        'status': OID_HR_STATUS,
    }

    def __init__(self, ip, community='public', timeout_sec=5, retries=2,
                 version=0, **kwargs):
        super().__init__(ip, community, timeout_sec, retries, version, **kwargs)

    def get_counters(self) -> dict:
        """Query Zebra printer counters via SNMP.

        Uses vendor OIDs under enterprise 10642.
        Falls back to Host Resources MIB for status/model.
        """
        result = {
            'labels_total': None,
            'meters_total': None,
            'meter_unit': 'unknown',
            'model_name': '',
            'serial': '',
            'status': 'offline',
            'reachable': False,
        }

        # Quick reachability check via model name
        model_name, _ = self._snmp_get(OID_ZEBRA_MODEL_NAME)
        if model_name is None:
            # Fallback: try Host Resources model
            model_name, _ = self._snmp_get(OID_HR_MODEL)
        if model_name is None:
            return result
        result['reachable'] = True

        if isinstance(model_name, bytes):
            model_name = model_name.decode('ascii', errors='replace')
        result['model_name'] = model_name or ''

        # Get serial (vendor OID)
        serial, _ = self._snmp_get(OID_ZEBRA_SERIAL)
        if isinstance(serial, bytes):
            serial = serial.decode('ascii', errors='replace')
        result['serial'] = serial or ''

        # Get status
        status_code, status_tag = self._snmp_get(OID_HR_STATUS)
        result['status'] = self._status_from_code(status_code)

        # Get labels total (vendor OID - NONRESET counter)
        labels, labels_tag = self._snmp_get(OID_ZEBRA_LABELS_NONRESET)
        if labels is not None:
            result['labels_total'] = int(labels)
        else:
            # Fallback: try RESET counter
            labels, _ = self._snmp_get(OID_ZEBRA_LABELS_RESET1)
            if labels is not None:
                result['labels_total'] = int(labels)

        # Meters not available via SNMP on ZT411
        # The printer shows 555,719 IN / 1,411,527 CM on its LCD
        # but these OIDs timeout: 3.1.7.0, 3.1.8.0, 3.1.9.0
        result['meters_total'] = None
        result['meter_unit'] = 'unknown'

        return result

    def is_reachable(self) -> bool:
        """Check if printer responds to SNMP."""
        model_name, _ = self._snmp_get(OID_ZEBRA_MODEL_NAME)
        if model_name is None:
            model_name, _ = self._snmp_get(OID_HR_MODEL)
        return model_name is not None
