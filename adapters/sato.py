"""Sato printer adapter. Supports CL4NX Plus.

Uses standard RFC 3805 Printer MIB OIDs.
Sato does not expose vendor-specific enterprise OIDs.
"""

from adapters.base import PrinterAdapter, TAG_INTEGER, TAG_COUNTER32


# Standard RFC 3805 Printer MIB OIDs
OID_PRINTER_NAME = '1.3.6.1.2.1.43.5.1.1.16.1'
OID_SERIAL = '1.3.6.1.2.1.43.5.1.1.17.1'
OID_MARKER_LIFE_COUNT = '1.3.6.1.2.1.43.10.2.1.4.1.1'
OID_MARKER_COUNTER_UNIT = '1.3.6.1.2.1.43.10.2.1.3.1.1'


class SatoAdapter(PrinterAdapter):
    """Adapter for Sato printers (CL4NX Plus).

    Sato printers use standard RFC 3805 OIDs only.
    prtMarkerLifeCount returns total media length in meters.
    prtMarkerCounterUnit returns a vendor-specific code (17 = meters).
    """

    OIDS = {
        'model_name': OID_PRINTER_NAME,
        'meters_total': OID_MARKER_LIFE_COUNT,
        'counter_unit': OID_MARKER_COUNTER_UNIT,
    }

    def __init__(self, ip, community='public', timeout_sec=3, retries=2,
                 unit_map=None):
        super().__init__(ip, community, timeout_sec, retries)
        self.unit_map = unit_map or {}

    def get_counters(self) -> dict:
        """Query Sato printer counters via SNMP.

        Returns:
            dict with meters_total, model_name, reachable status.
            labels_total is always None (not available on Sato).
        """
        result = {
            'labels_total': None,
            'meters_total': None,
            'meter_unit': 'unknown',
            'model_name': '',
            'serial': '',
            'status': 'unknown',
            'reachable': False,
        }

        # Reachability check via printer name
        model_name, _ = self._snmp_get(OID_PRINTER_NAME)
        if model_name is None:
            return result
        result['reachable'] = True

        if isinstance(model_name, bytes):
            model_name = model_name.decode('ascii', errors='replace')
        result['model_name'] = model_name or ''

        # Get meters total
        meters, _ = self._snmp_get(OID_MARKER_LIFE_COUNT)
        if meters is not None:
            result['meters_total'] = float(meters)

        # Get unit code and map it
        unit_code, _ = self._snmp_get(OID_MARKER_COUNTER_UNIT)
        if unit_code is not None:
            code_str = str(int(unit_code))
            result['meter_unit'] = self.unit_map.get(code_str, f'unit_code:{code_str}')
        elif meters is not None:
            result['meter_unit'] = 'unknown'

        return result

    def is_reachable(self) -> bool:
        """Check if printer responds to SNMP."""
        model_name, _ = self._snmp_get(OID_PRINTER_NAME)
        return model_name is not None
