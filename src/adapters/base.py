"""Abstract base class for printer adapters.

Adapters are declarative: a subclass declares which OIDs to read and how to
convert the raw values via class attributes (``reachability_oid``, ``metrics``,
``snmp_version``, ``model_prefixes``). The generic engine below implements the
common collect / reachability flow that every adapter used to copy-paste.
"""

import logging
import re
import time
from abc import ABC
from dataclasses import dataclass
from typing import Callable, Optional, Tuple, Union

log = logging.getLogger('printer_stats')

# Keys every get_counters() result must contain — the contract with
# main.py / db.py / report.py.
METRIC_KEYS = ('labels_total', 'meters_total', 'meter_unit', 'model_name', 'reachable')


def _to_str(value):
    """Decode an SNMP octet string; never raises on driver garbage."""
    if isinstance(value, bytes):
        return value.decode('ascii', errors='replace')
    if isinstance(value, str):
        return value
    return str(value)


def convert_regex(raw, pattern):
    """Parse a usage string like '89667 INCHES, 227775 CENTIMETERS'.

    Prefers centimeters over inches when both are present (centimeters are the
    more precise reading). Returns meters, or None when nothing parses.
    """
    matches = list(re.finditer(pattern, _to_str(raw), re.IGNORECASE))
    if not matches:
        return None
    matches.sort(key=lambda m: (m.group(2).upper() != 'CENTIMETERS', m.start()))
    amount = int(matches[0].group(1).replace(',', ''))
    if matches[0].group(2).upper() == 'CENTIMETERS':
        return amount / 100.0
    return amount * 0.0254


def convert_unit_map(raw, mapping, fallback='unit_code:{}'):
    """Map a prtMarkerCounterUnit code to a readable unit name."""
    code = str(int(raw))
    return mapping.get(code, fallback.format(code))


_CONVERTERS = {
    None: lambda value: value,
    'int': lambda value: int(value),
    'float': lambda value: float(value),
    'str': _to_str,
}


def convert_value(raw, convert):
    """Apply a converter spec to a raw SNMP value.

    Supported specs:
        None            -> pass the raw value through
        'int'/'float'   -> cast to a number
        'str'           -> decode bytes to ascii (errors='replace')
        ('regex', PAT)  -> parse a usage string to meters
        ('map', MAP[, FALLBACK]) -> map a unit code to a unit name
        callable        -> called with the raw value
    """
    if isinstance(convert, tuple):
        kind = convert[0]
        if kind == 'regex':
            return convert_regex(raw, convert[1])
        if kind == 'map':
            return convert_unit_map(raw, convert[1],
                                    convert[2] if len(convert) > 2 else 'unit_code:{}')
        raise ValueError(f'Unknown converter kind: {kind!r}')
    if callable(convert):
        return convert(raw)
    handler = _CONVERTERS.get(convert)
    if handler is None:
        raise ValueError(f'Unknown converter: {convert!r}')
    return handler(raw)


@dataclass(frozen=True)
class Metric:
    """Declarative spec for one SNMP read on an adapter.

    Attributes:
        key: Output key in the get_counters() result dict.
        oid: OID to read.
        retry: Use _snmp_get_retry (exponential backoff) instead of a single
            GET. Defaults to True.
        label: Log label for the read (defaults to key).
        convert: Converter spec (see convert_value).
        unit: Fixed unit to report when the read succeeds (e.g. 'cm').
    """

    key: str
    oid: str
    retry: bool = True
    label: Optional[str] = None
    convert: Union[None, str, Callable, Tuple] = None
    unit: Optional[str] = None


class PrinterAdapter(ABC):
    """Base class for printer adapters.

    Subclasses declare their SNMP behavior via class attributes:
        model_prefixes   - model strings this adapter serves (registry)
        snmp_version     - SNMP version the printer speaks (0 = v1, 1 = v2c)
        reachability_oid - OID checked to detect the printer (model name)
        metrics          - tuple of Metric specs to collect
    """

    model_prefixes: Tuple[str, ...] = ()
    snmp_version: int = 1
    reachability_oid: str = ''
    metrics: Tuple[Metric, ...] = ()

    # Backward-compat: metric key -> OID, recomputed for each subclass.
    OIDS = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        cls.OIDS = {m.key: m.oid for m in cls.metrics}

    def __init__(self, ip, community='public', timeout_sec=5, retries=2,
                 version=None, **kwargs):
        self.ip = ip
        self.community = community
        self.timeout_sec = timeout_sec
        self.retries = retries
        # The adapter owns its protocol version; an explicit caller
        # version still wins (kept for tests / one-off tooling).
        self.version = self.snmp_version if version is None else version

    # -- public API ----------------------------------------------------

    def get_counters(self) -> dict:
        """Query the printer and return counters.

        Always returns the METRIC_KEYS contract keys. Reachability is checked
        first; metrics are only polled once the printer responds.
        """
        result = {key: None for key in METRIC_KEYS}
        result.update({'meter_unit': 'unknown', 'model_name': '', 'reachable': False})

        model, _ = self._snmp_get(self.reachability_oid, label='poke')
        if model is None:
            return result
        result['reachable'] = True
        result['model_name'] = _to_str(model) or ''

        for spec in self.metrics:
            raw, _ = self._fetch(spec)
            if raw is None:
                continue
            value = convert_value(raw, spec.convert)
            if value is None:
                continue
            result[spec.key] = value
            if spec.unit and result.get('meter_unit') == 'unknown':
                result['meter_unit'] = spec.unit

        self._collect_extra(result)
        return result

    def is_reachable(self) -> bool:
        """Check if the printer is reachable via SNMP."""
        model, _ = self._snmp_get(self.reachability_oid)
        return model is not None

    # -- extension hooks ------------------------------------------------

    def _collect_extra(self, result):
        """Hook for adapter-specific additions to the counters result.

        Called at the end of get_counters() on reachable printers.
        """

    # -- SNMP helpers ----------------------------------------------------

    def _fetch(self, spec):
        """Read one metric according to its retry policy."""
        if spec.retry:
            return self._snmp_get_retry(spec.oid, label=spec.label or spec.key)
        return self._snmp_get(spec.oid, label=spec.label or spec.key)

    def _snmp_get(self, oid, label=None):
        """Single SNMP GET. Returns (value, type_tag) or (None, None)."""
        from snmp_client import get, SnmpTimeout, SnmpError
        tag = label or oid
        try:
            log.debug(f"  SNMP GET  {self.ip} {oid}  [{tag}]")
            value, type_tag = get(
                self.ip, oid,
                community=self.community,
                timeout_sec=self.timeout_sec,
                retries=self.retries,
                version=self.version,
            )
            log.debug(f"  SNMP RESP {self.ip} {oid} = {value!r} (tag=0x{type_tag:02x})  [{tag}]")
            return value, type_tag
        except (SnmpTimeout, SnmpError) as e:
            log.debug(f"  SNMP FAIL {self.ip} {oid}  [{tag}]: {e}")
            return None, None

    def _snmp_get_retry(self, oid, label=None, attempts=3):
        """SNMP GET with exponential backoff retry."""
        for attempt in range(attempts):
            value, type_tag = self._snmp_get(oid, label=label)
            if value is not None:
                return value, type_tag
            if attempt < attempts - 1:
                delay = 0.5 * (2 ** attempt)
                log.debug(f"  RETRY {self.ip} {label} attempt {attempt + 2}/{attempts} in {delay}s")
                time.sleep(delay)
        return None, None

    @staticmethod
    def _status_from_code(code):
        if code is None:
            return 'offline'
        return {1: 'other', 2: 'unknown', 3: 'idle', 4: 'printing',
                5: 'warmup', 6: 'stopping', 7: 'down'}.get(code, 'unknown')