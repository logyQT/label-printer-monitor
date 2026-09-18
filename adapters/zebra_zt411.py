"""Zebra ZT411 adapter.

Uses vendor OIDs under enterprise 10642.
Labels + meters (cm) confirmed on ZT411.
"""

from adapters.base import PrinterAdapter


OID_REACHABILITY = '1.3.6.1.4.1.10642.1.1.0'  # model name
OID_LABELS = '1.3.6.1.4.1.10642.3.1.6.0'       # labels NONRESET
OID_METERS = '1.3.6.1.4.1.10642.3.1.1.0'        # centimeters NONRESET


class ZebraZT411Adapter(PrinterAdapter):

    OIDS = {'labels_total': OID_LABELS}

    def __init__(self, ip, community='public', timeout_sec=5, retries=2,
                 version=1, **kwargs):
        super().__init__(ip, community, timeout_sec, retries, version, **kwargs)

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

        labels, _ = self._snmp_get_retry(OID_LABELS, label='labels')
        if labels is not None:
            result['labels_total'] = int(labels)

        cm, _ = self._snmp_get_retry(OID_METERS, label='meters')
        if cm is not None:
            result['meters_total'] = float(cm)
            result['meter_unit'] = 'cm'

        return result

    def is_reachable(self) -> bool:
        model, _ = self._snmp_get(OID_REACHABILITY)
        return model is not None
