"""Sato CL4NX Plus adapter.

Two SNMP requests total:
1. Poke (GET) — check reachability
2. Meters + unit with exponential backoff retry

No label count available via SNMP on Sato.
"""

from adapters.base import PrinterAdapter


OID_REACHABILITY = '1.3.6.1.2.1.43.5.1.1.16.1'  # printer name
OID_METERS = '1.3.6.1.2.1.43.10.2.1.4.1.1'     # prtMarkerLifeCount
OID_UNIT = '1.3.6.1.2.1.43.10.2.1.3.1.1'        # prtMarkerCounterUnit


class SatoCL4NXPlusAdapter(PrinterAdapter):

    OIDS = {
        'model_name': OID_REACHABILITY,
        'meters_total': OID_METERS,
    }

    def __init__(self, ip, community='public', timeout_sec=5, retries=2,
                 version=1, unit_map=None, **kwargs):
        super().__init__(ip, community, timeout_sec, retries, version, **kwargs)
        self.unit_map = unit_map or {}

    def get_counters(self) -> dict:
        result = {
            'labels_total': None, 'meters_total': None, 'meter_unit': 'unknown',
            'model_name': '', 'reachable': False,
        }

        model, _ = self._snmp_get(OID_REACHABILITY, label='poke')
        if model is None:
            return result
        result['reachable'] = True
        if isinstance(model, bytes):
            model = model.decode('ascii', errors='replace')
        result['model_name'] = model or ''

        meters, _ = self._snmp_get_retry(OID_METERS, label='meters')
        if meters is not None:
            result['meters_total'] = float(meters)

        unit_code, _ = self._snmp_get_retry(OID_UNIT, label='unit')
        if unit_code is not None:
            code_str = str(int(unit_code))
            result['meter_unit'] = self.unit_map.get(code_str, f'unit_code:{code_str}')
        elif meters is not None:
            result['meter_unit'] = 'unknown'

        return result

    def is_reachable(self) -> bool:
        model, _ = self._snmp_get(OID_REACHABILITY)
        return model is not None
