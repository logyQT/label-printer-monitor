"""Simple SNMP GET utility.

Usage:
    python snmpget.py <ip> <oid>
    python snmpget.py <ip> <oid> --community private
    python snmpget.py <ip> <oid> --timeout 10
"""

import sys
import argparse

from snmp_client import get, SnmpTimeout, SnmpError, TAG_COUNTER32, TAG_GAUGE32, TAG_INTEGER, TAG_OCTET_STRING, TAG_TIMETICKS, TAG_IP_ADDRESS


TAG_NAMES = {
    TAG_INTEGER: 'INTEGER', TAG_OCTET_STRING: 'OCTET_STRING',
    TAG_COUNTER32: 'COUNTER32', TAG_GAUGE32: 'GAUGE32',
    TAG_TIMETICKS: 'TIMETICKS', TAG_IP_ADDRESS: 'IP_ADDRESS',
}


def format_value(value, type_tag):
    tag_name = TAG_NAMES.get(type_tag, f'0x{type_tag:02x}') if type_tag else 'ERROR'
    if isinstance(value, bytes):
        try:
            decoded = value.decode('ascii', errors='replace')
            return f'{tag_name}: {value!r} -> "{decoded}"'
        except Exception:
            return f'{tag_name}: {value!r}'
    elif value is None:
        return f'{tag_name}: NULL'
    else:
        return f'{tag_name}: {value}'


def main():
    parser = argparse.ArgumentParser(description='SNMP GET utility')
    parser.add_argument('ip', help='Target IP address')
    parser.add_argument('oid', help='OID to query')
    parser.add_argument('--community', default='public', help='SNMP community (default: public)')
    parser.add_argument('--timeout', type=int, default=5, help='Timeout in seconds (default: 5)')
    parser.add_argument('--retries', type=int, default=2, help='Retry count (default: 2)')
    args = parser.parse_args()

    try:
        value, tag = get(args.ip, args.oid, args.community, args.timeout, args.retries)
        print(format_value(value, tag))
    except SnmpTimeout as e:
        print(f'TIMEOUT: {e}')
        sys.exit(1)
    except SnmpError as e:
        print(f'ERROR: {e}')
        sys.exit(1)


if __name__ == '__main__':
    main()
