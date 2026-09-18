"""Zebra printer adapter. Supports ZT230, ZT411, GX430t, ZD621.

Two SNMP requests total:
1. Poke (GET) — check reachability
2. Labels + meters with exponential backoff retry
"""

from adapters.base import PrinterAdapter


# OIDs
OID_REACHABILITY = '1.3.6.1.4.1.10642.1.1.0'  # model name — quick poke
OID_LABELS = '1.3.6.1.4.1.10642.3.1.6.0'       # labels NONRESET counter
OID_METERS = '1.3.6.1.4.1.10642.3.1.1.0'        # centimeters NONRESET


class ZebraAdapter(PrinterAdapter):

    OIDS = {
        'labels_total': OID_LABELS,
    }

    def __init__(self, ip, community='public', timeout_sec=5, retries=2,
                 version=1, **kwargs):
        super().__init__(ip, community, timeout_sec, retries, version, **kwargs)

    def get_counters(self) -> dict:
        result = {
            'labels_total': None,
            'meters_total': None,
            'meter_unit': 'unknown',
            'model_name': '',
            'reachable': False,
        }

        # 1) Poke — is it alive?
        model, _ = self._snmp_get(OID_REACHABILITY, label='poke')
        if model is None:
            return result
        result['reachable'] = True
        if isinstance(model, bytes):
            model = model.decode('ascii', errors='replace')
        result['model_name'] = model or ''

        # 2) Labels — retry with backoff
        labels, _ = self._snmp_get_retry(OID_LABELS, label='labels')
        if labels is not None:
            result['labels_total'] = int(labels)

        # 3) Meters (cm) — retry with backoff
        cm, _ = self._snmp_get_retry(OID_METERS, label='meters')
        if cm is not None:
            result['meters_total'] = float(cm)
            result['meter_unit'] = 'cm'

        return result

    def is_reachable(self) -> bool:
        model, _ = self._snmp_get(OID_REACHABILITY)
        return model is not None
