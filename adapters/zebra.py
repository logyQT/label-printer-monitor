"""Zebra printer adapter. Supports ZT230, ZT411, GX430t, ZD621.

All Zebra printers share the enterprise OID tree under 1.3.6.1.4.1.10642.
"""

from adapters.base import PrinterAdapter, TAG_COUNTER32, TAG_GAUGE32, TAG_INTEGER


# Standard RFC 3805 Printer MIB OIDs (work on all Zebra models)
OID_SYS_DESCR = '1.3.6.1.2.1.1.1.0'
OID_SYS_NAME = '1.3.6.1.2.1.1.5.0'
OID_SERIAL = '1.3.6.1.2.1.43.5.1.1.17.1'
OID_HR_MODEL = '1.3.6.1.2.1.25.3.2.1.3.1'
OID_HR_STATUS = '1.3.6.1.2.1.25.3.5.1.1.1'
OID_MARKER_LIFE_COUNT = '1.3.6.1.2.1.43.10.2.1.4.1.1'
OID_MARKER_COUNTER_UNIT = '1.3.6.1.2.1.43.10.2.1.3.1'

# Zebra vendor OIDs (enterprise 10642)
OID_ZEBRA_LABELS_TOTAL = '1.3.6.1.4.1.10642.20.17.2.0'
OID_ZEBRA_METERS_TOTAL = '1.3.6.1.4.1.10642.20.17.3.0'
OID_ZEBRA_MODEL_NAME = '1.3.6.1.4.1.10642.1.1.0'
OID_ZEBRA_FRIENDLY_NAME = '1.3.6.1.4.1.10642.20.3.5.0'
OID_ZEBRA_ALT_LABELS = '1.3.6.1.4.1.10642.3.1.6.0'

# prtMarkerCounterUnit values
UNIT_MAP = {
    0: 'other',
    1: 'tenThousandthsOfSheets',
    2: 'impressions',
    3: 'sheets',
    4: 'linearFeet',
    5: 'linearMeters',
}


class ZebraAdapter(PrinterAdapter):
    """Adapter for Zebra printers (ZT230, ZT411, GX430t, ZD621)."""

    OIDS = {
        'labels_total': OID_ZEBRA_LABELS_TOTAL,
        'meters_total': OID_ZEBRA_METERS_TOTAL,
        'model_name': OID_ZEBRA_MODEL_NAME,
        'serial': OID_SERIAL,
        'status': OID_HR_STATUS,
        'counter_unit': OID_MARKER_COUNTER_UNIT,
        'life_count': OID_MARKER_LIFE_COUNT,
    }

    def get_counters(self) -> dict:
        """Query Zebra printer counters via SNMP.

        Strategy:
        1. Try vendor-specific OIDs first (labels_total, meters_total)
        2. Fall back to RFC 3805 prtMarkerLifeCount if vendor OIDs fail
        3. Detect meter unit from prtMarkerCounterUnit or config override
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

        # Quick reachability check via sysDescr
        sys_descr, _ = self._snmp_get(OID_SYS_DESCR)
        if sys_descr is None:
            return result
        result['reachable'] = True

        # Get status
        status_code, status_tag = self._snmp_get(OID_HR_STATUS)
        result['status'] = self._status_from_code(status_code)

        # Get model name (try vendor first, then standard)
        model_name, _ = self._snmp_get(OID_ZEBRA_MODEL_NAME)
        if model_name is None:
            model_name, _ = self._snmp_get(OID_HR_MODEL)
        if isinstance(model_name, bytes):
            model_name = model_name.decode('ascii', errors='replace')
        result['model_name'] = model_name or ''

        # Get serial
        serial, _ = self._snmp_get(OID_SERIAL)
        if isinstance(serial, bytes):
            serial = serial.decode('ascii', errors='replace')
        result['serial'] = serial or ''

        # Get labels total (vendor OID)
        labels, labels_tag = self._snmp_get(OID_ZEBRA_LABELS_TOTAL)
        if labels is not None:
            result['labels_total'] = int(labels)
        else:
            # Fallback: use prtMarkerLifeCount
            life_count, lc_tag = self._snmp_get(OID_MARKER_LIFE_COUNT)
            if life_count is not None:
                result['labels_total'] = int(life_count)

        # Get meters total (vendor OID)
        meters, meters_tag = self._snmp_get(OID_ZEBRA_METERS_TOTAL)
        if meters is not None:
            result['meters_total'] = float(meters)
        else:
            # Fallback: no vendor OID, use life count (same as labels fallback)
            # Meters unknown if vendor OID not available
            result['meters_total'] = None

        # Detect meter unit
        unit_code, unit_tag = self._snmp_get(OID_MARKER_COUNTER_UNIT)
        if unit_code is not None:
            result['meter_unit'] = UNIT_MAP.get(unit_code, f'unknown({unit_code})')
        elif meters is not None:
            # Vendor OID responded but no unit OID — assume centimeters
            # (Zebra firmware typically returns centimeters for meters_total)
            result['meter_unit'] = 'cm (assumed)'

        return result

    def is_reachable(self) -> bool:
        """Check if printer responds to SNMP."""
        sys_descr, _ = self._snmp_get(OID_SYS_DESCR)
        return sys_descr is not None
