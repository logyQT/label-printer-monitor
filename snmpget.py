"""Simple SNMP query utility.

Usage:
    python snmpget.py <ip> <oid>
    python snmpget.py <ip> <oid> --community private
    python snmpget.py <ip> <oid> --version 1
    python snmpget.py <ip> <oid> --timeout 10
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from snmp_client import get, SnmpTimeout, SnmpError, VERSION_1, VERSION_2C


def main():
    if len(sys.argv) < 3:
        print('Usage: python snmpget.py <ip> <oid> [options]')
        print()
        print('Options:')
        print('  --community <str>  SNMP community (default: public)')
        print('  --version <1|2>    SNMP version (default: 2)')
        print('  --timeout <sec>    Timeout in seconds (default: 5)')
        print('  --retries <n>      Retry count (default: 2)')
        print()
        print('Examples:')
        print('  python snmpget.py 192.168.40.249 1.3.6.1.4.1.10642.1.1.0')
        print('  python snmpget.py 192.168.40.249 1.3.6.1.4.1.10642.3.1.6.0 --timeout 10')
        print('  python snmpget.py 192.168.40.249 1.3.6.1.2.1.1.1.0 --version 1')
        sys.exit(1)

    ip = sys.argv[1]
    oid = sys.argv[2]
    community = 'public'
    version = VERSION_2C
    timeout = 5
    retries = 2

    i = 3
    while i < len(sys.argv):
        if sys.argv[i] == '--community' and i + 1 < len(sys.argv):
            community = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == '--version' and i + 1 < len(sys.argv):
            version = VERSION_1 if sys.argv[i + 1] == '1' else VERSION_2C
            i += 2
        elif sys.argv[i] == '--timeout' and i + 1 < len(sys.argv):
            timeout = int(sys.argv[i + 1])
            i += 2
        elif sys.argv[i] == '--retries' and i + 1 < len(sys.argv):
            retries = int(sys.argv[i + 1])
            i += 2
        else:
            print(f'Unknown option: {sys.argv[i]}')
            sys.exit(1)

    tag_names = {
        0x02: 'INTEGER', 0x04: 'OCTET_STRING', 0x05: 'NULL',
        0x06: 'OID', 0x40: 'IP_ADDRESS', 0x41: 'COUNTER32',
        0x42: 'GAUGE32', 0x43: 'TIMETICKS',
    }

    try:
        value, tag = get(ip, oid, community, timeout, retries, version=version)
        tag_name = tag_names.get(tag, f'0x{tag:02x}')
        if isinstance(value, bytes):
            try:
                decoded = value.decode('ascii', errors='replace')
                print(f'{tag_name}: {value!r} -> "{decoded}"')
            except Exception:
                print(f'{tag_name}: {value!r}')
        else:
            print(f'{tag_name}: {value}')
    except SnmpTimeout as e:
        print(f'TIMEOUT: {e}')
        sys.exit(1)
    except SnmpError as e:
        print(f'ERROR: {e}')
        sys.exit(1)


if __name__ == '__main__':
    main()
