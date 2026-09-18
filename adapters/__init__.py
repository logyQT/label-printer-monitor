"""Adapter registry: maps model names to adapter classes."""

from adapters.zebra_zt411 import ZebraZT411Adapter
from adapters.zebra_gx430t import ZebraGX430tAdapter
from adapters.sato_cl4nx_plus import SatoCL4NXPlusAdapter

ADAPTER_REGISTRY = {
    'zebra zt411': ZebraZT411Adapter,
    'zebra gx430t': ZebraGX430tAdapter,
    'sato cl4nx plus': SatoCL4NXPlusAdapter,
}


def get_adapter_class(model):
    """Resolve adapter class by model string (case-insensitive).

    Args:
        model: Printer model string like 'Zebra ZT411'.

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
    """Create an adapter instance for the given model and IP."""
    cls = get_adapter_class(model)
    return cls(ip=ip, community=community, timeout_sec=timeout_sec,
               retries=retries, version=version, **kwargs)
