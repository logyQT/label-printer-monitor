"""One-time meter unit detection script.

Queries a printer to determine the unit used for meters_total.
Updates config.json with the detected unit.

Usage:
    python discover_units.py 10.0.1.10
    python discover_units.py --all
"""

import argparse
import json
import os
import sys

from snmp_client import get, SnmpTimeout, SnmpError
from adapters.zebra_zt411 import OID_REACHABILITY as OID_ZEBRA_MODEL_NAME
from adapters.sato_cl4nx_plus import OID_UNIT as OID_MARKER_COUNTER_UNIT

OID_SYS_DESCR = '1.3.6.1.2.1.1.1.0'
OID_ZEBRA_METERS_TOTAL = '1.3.6.1.4.1.10642.20.17.3.0'


UNIT_MAP = {
    0: 'other',
    1: 'tenThousandthsOfSheets',
    2: 'impressions',
    3: 'sheets',
    4: 'linearFeet',
    5: 'linearMeters',
}


def load_config(config_path=None):
    """Load configuration from JSON file."""
    if config_path is None:
        _here = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(os.path.dirname(_here), 'data', 'config.json')
    if not os.path.exists(config_path):
        print(f'ERROR: Config file not found: {config_path}', file=sys.stderr)
        sys.exit(2)
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_config(config, config_path=None):
    """Save configuration to JSON file."""
    if config_path is None:
        _here = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(os.path.dirname(_here), 'data', 'config.json')
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def discover_printer(ip, community='public', timeout_sec=3, retries=2):
    """Query a single printer to detect its meter unit and model.

    Args:
        ip: Printer IP address.
        community: SNMP community string.
        timeout_sec: SNMP timeout.
        retries: SNMP retry count.

    Returns:
        Dict with discovery results or None on failure.
    """
    print(f"\nDiscovering printer at {ip}...")

    # Check reachability
    try:
        sys_descr, _ = get(ip, OID_SYS_DESCR, community, timeout_sec, retries)
        if sys_descr is None:
            print(f"  ERROR: Printer not reachable at {ip}")
            return None
        if isinstance(sys_descr, bytes):
            sys_descr = sys_descr.decode('ascii', errors='replace')
        print(f"  sysDescr: {sys_descr}")
    except (SnmpTimeout, SnmpError) as e:
        print(f"  ERROR: {e}")
        return None

    # Get model name
    model_name = ''
    try:
        model_val, _ = get(ip, OID_ZEBRA_MODEL_NAME, community, timeout_sec, retries)
        if model_val is not None:
            if isinstance(model_val, bytes):
                model_name = model_val.decode('ascii', errors='replace')
            else:
                model_name = str(model_val)
        print(f"  Model: {model_name}")
    except (SnmpTimeout, SnmpError):
        print(f"  WARNING: Could not read model name")

    # Get serial
    serial = ''
    try:
        serial_val, _ = get(ip, '1.3.6.1.4.1.10642.1.9.0', community, timeout_sec, retries)
        if serial_val is not None:
            if isinstance(serial_val, bytes):
                serial = serial_val.decode('ascii', errors='replace')
            else:
                serial = str(serial_val)
        print(f"  Serial: {serial}")
    except (SnmpTimeout, SnmpError):
        print(f"  WARNING: Could not read serial number")

    # Get meter unit from prtMarkerCounterUnit
    unit_code = None
    try:
        unit_val, unit_tag = get(ip, OID_MARKER_COUNTER_UNIT, community, timeout_sec, retries)
        if unit_val is not None:
            unit_code = int(unit_val)
            unit_name = UNIT_MAP.get(unit_code, f'unknown({unit_code})')
            print(f"  prtMarkerCounterUnit: {unit_code} = {unit_name}")
        else:
            print(f"  prtMarkerCounterUnit: not available")
    except (SnmpTimeout, SnmpError):
        print(f"  WARNING: Could not read prtMarkerCounterUnit")

    # Get meters total (vendor OID)
    meters_val = None
    try:
        meters_val, _ = get(ip, OID_ZEBRA_METERS_TOTAL, community, timeout_sec, retries)
        if meters_val is not None:
            print(f"  meters_total (vendor OID): {meters_val}")
        else:
            print(f"  meters_total (vendor OID): not available")
    except (SnmpTimeout, SnmpError):
        print(f"  meters_total (vendor OID): timeout")

    # Determine unit
    if unit_code is not None:
        unit = UNIT_MAP.get(unit_code, f'unknown({unit_code})')
    elif meters_val is not None:
        unit = 'cm (assumed)'
        print(f"  NOTE: Vendor OID available but no unit OID. Assuming centimeters.")
    else:
        unit = 'unknown'
        print(f"  WARNING: Could not determine meter unit")

    print(f"  RESULT: unit = {unit}")

    return {
        'ip': ip,
        'model_name': model_name,
        'serial': serial,
        'unit': unit,
        'unit_code': unit_code,
        'meters_available': meters_val is not None,
    }


def update_config(config, ip, unit):
    """Update config.json with discovered unit for a printer.

    Args:
        config: Config dict.
        ip: Printer IP.
        unit: Detected unit string.
    """
    for printer in config.get('printers', []):
        if printer['ip'] == ip:
            printer['meter_unit'] = unit
            return True
    return False


def discover_all(config, community='public', timeout_sec=3, retries=2):
    """Discover units for all printers in config.

    Args:
        config: Config dict.
        community: SNMP community.
        timeout_sec: SNMP timeout.
        retries: SNMP retries.
    """
    printers = config.get('printers', [])
    print(f"Discovering units for {len(printers)} printers...")

    results = []
    for printer in printers:
        result = discover_printer(printer['ip'], community, timeout_sec, retries)
        if result:
            results.append(result)
            update_config(config, printer['ip'], result['unit'])

    print(f"\n{'='*50}")
    print(f"Discovery complete: {len(results)}/{len(printers)} printers responded")
    print(f"{'='*50}")

    # Summary
    units_found = {}
    for r in results:
        unit = r['unit']
        if unit not in units_found:
            units_found[unit] = []
        units_found[unit].append(r['ip'])

    print("\nUnits found:")
    for unit, ips in units_found.items():
        print(f"  {unit}: {len(ips)} printers")

    return results


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description='Discover printer meter units')
    parser.add_argument('ip', nargs='?', help='Printer IP to discover')
    parser.add_argument('--all', action='store_true', help='Discover all printers in config')
    parser.add_argument('--config', default='config.json', help='Path to config.json')
    parser.add_argument('--community', default='public', help='SNMP community string')
    parser.add_argument('--timeout', type=int, default=3, help='SNMP timeout in seconds')
    parser.add_argument('--retries', type=int, default=2, help='SNMP retry count')
    parser.add_argument('--save', action='store_true', help='Save results to config.json')
    args = parser.parse_args()

    if not args.ip and not args.all:
        parser.print_help()
        sys.exit(1)

    config = load_config(args.config)

    if args.all:
        results = discover_all(config, args.community, args.timeout, args.retries)
        if args.save and results:
            save_config(config, args.config)
            print(f"\nConfig saved to {args.config}")
    else:
        result = discover_printer(args.ip, args.community, args.timeout, args.retries)
        if result and args.save:
            update_config(config, args.ip, result['unit'])
            save_config(config, args.config)
            print(f"\nConfig saved to {args.config}")


if __name__ == '__main__':
    main()
