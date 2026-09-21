"""Adapter registry: maps model names to adapter classes."""

from typing import Any

from src.adapters.base import PrinterAdapter
from src.adapters.sato_cl4nx_plus import SatoCL4NXPlusAdapter
from src.adapters.zebra_gx430t import ZebraGX430tAdapter
from src.adapters.zebra_zt411 import ZebraZT411Adapter

ADAPTER_CLASSES: tuple[type[PrinterAdapter], ...] = (
    ZebraZT411Adapter,
    ZebraGX430tAdapter,
    SatoCL4NXPlusAdapter,
)

# Backward-compat alias: model prefix -> adapter class, derived from the
# model_prefixes each adapter declares.
ADAPTER_REGISTRY: dict[str, type[PrinterAdapter]] = {
    prefix: cls for cls in ADAPTER_CLASSES for prefix in cls.model_prefixes
}


def get_adapter_class(model: str) -> type[PrinterAdapter]:
    """Resolve adapter class by model string (case-insensitive).

    Args:
        model: Printer model string like 'Zebra ZT411'.

    Returns:
        Adapter class (subclass of PrinterAdapter).

    Raises:
        ValueError: No adapter found for the model.
    """
    model_lower = model.lower()
    for cls in ADAPTER_CLASSES:
        if any(model_lower.startswith(prefix) for prefix in cls.model_prefixes):
            return cls
    raise ValueError(f"No adapter found for model: {model}")


def create_adapter(
    model: str,
    ip: str,
    community: str = "public",
    timeout_sec: int = 5,
    retries: int = 2,
    version: int | None = None,
    **kwargs: Any,
) -> PrinterAdapter:
    """Create an adapter instance for the given model and IP.

    The SNMP version is the adapter's own choice (snmp_version) unless an
    explicit version is passed.
    """
    cls = get_adapter_class(model)
    return cls(
        ip=ip,
        community=community,
        timeout_sec=timeout_sec,
        retries=retries,
        version=version,
        **kwargs,
    )
