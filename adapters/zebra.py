"""Zebra printer adapter. Supports ZT230, ZT411, GX430t, ZD621.

Uses SNMPv1 by default (more reliable on wireless printers).
Vendor OIDs under enterprise 10642.

Tested OIDs on ZT411:
- 10642.1.1.0 = Model name
- 10642.1.4.0 = Friendly name
- 10642.1.9.0 = Serial number (flaky on GET, works via GETBULK)
- 10642.3.1.1.0 = Centimeters NONRESET (1,411,527 CM on test unit)
- 10642.3.1.6.0 = Labels count NONRESET (302,318 on test unit)
- 10642.3.1.13.0 = Labels count RESET1
- 25.3.5.1.1.1 = hrPrinterStatus (idle/printing/etc)

Walk results (walk_zebra_zt411.py):
- Printer MIB (43.x) NOT IMPLEMENTED on ZT411 — all return noSuchName
- sysDescr TIMES OUT on wireless
- 10642.20 subtree is serial port config, NOT meters
- GETBULK works far better than sequential GETNEXT on this model
"""

from adapters.base import PrinterAdapter, TAG_COUNTER32, TAG_GAUGE32, TAG_INTEGER


# Zebra vendor OIDs (enterprise 10642) - WORKING on ZT411
OID_ZEBRA_MODEL_NAME = '1.3.6.1.4.1.10642.1.1.0'
OID_ZEBRA_FIRMWARE = '1.3.6.1.4.1.10642.1.2.0'
OID_ZEBRA_FRIENDLY_NAME = '1.3.6.1.4.1.10642.1.4.0'
OID_ZEBRA_SERIAL = '1.3.6.1.4.1.10642.1.9.0'
OID_ZEBRA_LABELS_NONRESET = '1.3.6.1.4.1.10642.3.1.6.0'
OID_ZEBRA_LABELS_RESET1 = '1.3.6.1.4.1.10642.3.1.13.0'
OID_ZEBRA_CM_NONRESET = '1.3.6.1.4.1.10642.3.1.1.0'  # centimeters, confirmed on ZT411

# Standard RFC 3805 / Host Resources - WORKING on ZT411
OID_HR_MODEL = '1.3.6.1.2.1.25.3.2.1.3.1'
OID_HR_STATUS = '1.3.6.1.2.1.25.3.5.1.1.1'

# OIDs that DO NOT WORK on ZT411 (confirmed via walk_zebra_zt411.py)
# - 10642.20.17.2.0 (QL series labels)
# - 10642.20.17.3.0 (QL series meters) -> noSuchName
# - 43.10.2.1.4.1.1 (prtMarkerLifeCount) -> noSuchName
# - 43.10.2.1.3.1 (prtMarkerCounterUnit) -> noSuchName
# - 43.5.1.1.17.1 (prtGeneralSerialNumber) -> noSuchName
# - 2.1.1.1.0 (sysDescr) -> timeout on wireless
# - Entire 43.x Printer MIB subtree -> noSuchName (not implemented)

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
        Batch fetches most OIDs in a single GET to reduce load on the printer.
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
        model_name, _ = self._snmp_get(OID_ZEBRA_MODEL_NAME, label='model')
        if model_name is None:
            # Fallback: try Host Resources model
            model_name, _ = self._snmp_get(OID_HR_MODEL, label='hr_model')
        if model_name is None:
            return result
        result['reachable'] = True

        if isinstance(model_name, bytes):
            model_name = model_name.decode('ascii', errors='replace')
        result['model_name'] = model_name or ''

        # Batch fetch: serial, status, labels, cm — single request
        batch_oids = [
            OID_ZEBRA_SERIAL,
            OID_HR_STATUS,
            OID_ZEBRA_LABELS_NONRESET,
            OID_ZEBRA_CM_NONRESET,
        ]
        batch_results = self._snmp_get_multiple(batch_oids, label='batch')

        oid_map = {oid: (val, tag) for oid, val, tag in batch_results}

        # Serial
        serial, _ = oid_map.get(OID_ZEBRA_SERIAL, (None, None))
        if isinstance(serial, bytes):
            serial = serial.decode('ascii', errors='replace')
        result['serial'] = serial or ''

        # Status
        status_code, _ = oid_map.get(OID_HR_STATUS, (None, None))
        result['status'] = self._status_from_code(status_code)

        # Labels
        labels, _ = oid_map.get(OID_ZEBRA_LABELS_NONRESET, (None, None))
        if labels is not None:
            result['labels_total'] = int(labels)
        else:
            # Fallback: try RESET counter individually
            labels, _ = self._snmp_get(OID_ZEBRA_LABELS_RESET1, label='labels_reset1')
            if labels is not None:
                result['labels_total'] = int(labels)

        # Meters (centimeters)
        cm_value, _ = oid_map.get(OID_ZEBRA_CM_NONRESET, (None, None))
        if cm_value is not None:
            result['meters_total'] = float(cm_value)
            result['meter_unit'] = 'cm'
        else:
            result['meters_total'] = None
            result['meter_unit'] = 'unknown'

        return result

    def is_reachable(self) -> bool:
        """Check if printer responds to SNMP."""
        model_name, _ = self._snmp_get(OID_ZEBRA_MODEL_NAME)
        if model_name is None:
            model_name, _ = self._snmp_get(OID_HR_MODEL)
        return model_name is not None
