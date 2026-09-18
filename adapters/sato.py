"""Sato printer adapter. Supports CL4NX Plus.

Uses standard RFC 3805 Printer MIB OIDs.
Sato does not expose vendor-specific enterprise OIDs.

Tested OIDs on CL4NX Plus:
- 43.10.2.1.4.1.1 = prtMarkerLifeCount (total labels/length)
- 43.10.2.1.3.1.1 = prtMarkerCounterUnit (17 = vendor-specific)
- 43.5.1.1.16.1 = prtGeneralPrinterName
- 43.5.1.1.17.1 = prtGeneralSerialNumber
- 25.3.5.1.1.1 = hrPrinterStatus
"""

from adapters.base import PrinterAdapter, TAG_INTEGER, TAG_COUNTER32


# Standard RFC 3805 Printer MIB OIDs - WORKING on CL4NX Plus
OID_PRINTER_NAME = '1.3.6.1.2.1.43.5.1.1.16.1'
OID_SERIAL = '1.3.6.1.2.1.43.5.1.1.17.1'
OID_MARKER_LIFE_COUNT = '1.3.6.1.2.1.43.10.2.1.4.1.1'
OID_MARKER_COUNTER_UNIT = '1.3.6.1.2.1.43.10.2.1.3.1.1'
OID_HR_STATUS = '1.3.6.1.2.1.25.3.5.1.1.1'


class SatoAdapter(PrinterAdapter):
    """Adapter for Sato printers (CL4NX Plus).

    Uses SNMPv1 by default.
    prtMarkerLifeCount returns total media length.
    prtMarkerCounterUnit returns vendor code (17 = mapped via unit_map).
    """

    OIDS = {
        'model_name': OID_PRINTER_NAME,
        'serial': OID_SERIAL,
        'meters_total': OID_MARKER_LIFE_COUNT,
        'counter_unit': OID_MARKER_COUNTER_UNIT,
        'status': OID_HR_STATUS,
    }

    def __init__(self, ip, community='public', timeout_sec=5, retries=2,
                 version=1, unit_map=None, **kwargs):
        super().__init__(ip, community, timeout_sec, retries, version, **kwargs)
        self.unit_map = unit_map or {}

    def get_counters(self) -> dict:
        """Query Sato printer counters via SNMP.

        Returns:
            dict with meters_total, model_name, serial, status.
            labels_total is not available on Sato via SNMP.
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

        # Reachability check: try multiple OIDs (Sato is flaky)
        model_name, _ = self._snmp_get(OID_PRINTER_NAME, label='model')
        if model_name is None:
            # Fallback: try serial
            serial_check, _ = self._snmp_get(OID_SERIAL, label='serial_check')
            if serial_check is None:
                # Fallback: try life count
                life_check, _ = self._snmp_get(OID_MARKER_LIFE_COUNT, label='life_check')
                if life_check is None:
                    return result
            else:
                if isinstance(serial_check, bytes):
                    serial_check = serial_check.decode('ascii', errors='replace')
                result['serial'] = serial_check or ''
        else:
            if isinstance(model_name, bytes):
                model_name = model_name.decode('ascii', errors='replace')
            result['model_name'] = model_name or ''

        result['reachable'] = True

        # Batch fetch: serial, status, meters, unit — single request
        batch_oids = [
            OID_SERIAL,
            OID_HR_STATUS,
            OID_MARKER_LIFE_COUNT,
            OID_MARKER_COUNTER_UNIT,
        ]
        batch_results = self._snmp_get_multiple(batch_oids, label='batch')

        oid_map = {oid: (val, tag) for oid, val, tag in batch_results}

        # Serial
        serial, _ = oid_map.get(OID_SERIAL, (None, None))
        if isinstance(serial, bytes):
            serial = serial.decode('ascii', errors='replace')
        result['serial'] = serial or ''

        # Status
        status_code, _ = oid_map.get(OID_HR_STATUS, (None, None))
        result['status'] = self._status_from_code(status_code)

        # Meters total (prtMarkerLifeCount)
        meters, _ = oid_map.get(OID_MARKER_LIFE_COUNT, (None, None))
        if meters is not None:
            result['meters_total'] = float(meters)

        # Unit code
        unit_code, _ = oid_map.get(OID_MARKER_COUNTER_UNIT, (None, None))
        if unit_code is not None:
            code_str = str(int(unit_code))
            result['meter_unit'] = self.unit_map.get(code_str, f'unit_code:{code_str}')
        elif meters is not None:
            result['meter_unit'] = 'unknown'

        return result

    def is_reachable(self) -> bool:
        """Check if printer responds to SNMP."""
        model_name, _ = self._snmp_get(OID_PRINTER_NAME)
        if model_name is not None:
            return True
        serial, _ = self._snmp_get(OID_SERIAL)
        if serial is not None:
            return True
        life_count, _ = self._snmp_get(OID_MARKER_LIFE_COUNT)
        return life_count is not None
