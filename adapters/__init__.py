"""Adapter registry: maps model prefixes to adapter classes."""

from adapters.zebra import ZebraAdapter
from adapters.sato import SatoAdapter

ADAPTER_REGISTRY = {
    'zebra': ZebraAdapter,
    'sato': SatoAdapter,
}


def get_adapter_class(model):
    """Resolve adapter class by model string prefix.

    Args:
        model: Printer model string like 'Zebra ZT230'.

    Returns:
        Adapter class (subclass of PrinterAdapter).

    Raises:
        ValueError: No adapter found for the model.
    """
    model_lower = model.lower()
    for prefix, adapter_cls in ADAPTER_REGISTRY.items():
        if model_lower.startswith(prefix):
            return adapter_cls
    raise ValueError(f'No adapter found for model: {model}')


def create_adapter(model, ip, community='public', timeout_sec=5, retries=2,
                   version=0, **kwargs):
    """Create an adapter instance for the given model and IP.

    Args:
        model: Printer model string.
        ip: Printer IP address.
        community: SNMP community string.
        timeout_sec: SNMP timeout.
        retries: SNMP retry count.
        version: SNMP version (0=SNMPv1, 1=SNMPv2c). Default: 0 (v1).
        **kwargs: Additional adapter-specific arguments (e.g. unit_map).

    Returns:
        PrinterAdapter instance.
    """
    cls = get_adapter_class(model)
    return cls(ip=ip, community=community, timeout_sec=timeout_sec,
               retries=retries, version=version, **kwargs)
