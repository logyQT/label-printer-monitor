"""Simple SNMP walk utility.

Usage:
    python snmpwalk.py <ip> <oid>
    python snmpwalk.py <ip> <oid> --version 1
    python snmpwalk.py <ip> <oid> --timeout 10 --max 200
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from snmp_client import get, SnmpTimeout, SnmpError, VERSION_1, VERSION_2C


def snmp_getnext(ip, oid, community='public', timeout_sec=5, retries=2, version=VERSION_2C):
    """SNMP GETNEXT for a single OID."""
    from snmp_client import _build_request, _generate_request_id, _parse_response
    import socket

    request_id = _generate_request_id()
    packet = _build_request(oid, request_id, community, version=version)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout_sec)
    try:
        last_error = None
        for attempt in range(retries + 1):
            try:
                sock.sendto(packet, (ip, 161))
                data, addr = sock.recvfrom(65535)
                varbinds, ver = _parse_response(data)
                if len(varbinds) == 0:
                    return None, None, None
                return varbinds[0]
            except socket.timeout:
                last_error = SnmpTimeout(f'Timeout (attempt {attempt + 1}/{retries + 1})')
                continue
            except SnmpError:
                raise
        raise last_error
    finally:
        sock.close()


def walk(ip, start_oid, community='public', version=VERSION_2C,
         max_oids=500, timeout_sec=5, retries=2):
    """Walk an OID subtree."""
    results = []
    current_oid = start_oid
    seen = set()

    for _ in range(max_oids):
        try:
            returned_oid, value, type_tag = snmp_getnext(
                ip, current_oid, community, timeout_sec, retries, version
            )
        except (SnmpTimeout, SnmpError):
            break

        if returned_oid is None:
            break
        if returned_oid in seen:
            break
        if not returned_oid.startswith(start_oid.rstrip('.') + '.') and returned_oid != start_oid:
            break
        if type_tag in (0x80, 0x81, 0x82):
            break

        seen.add(returned_oid)
        results.append((returned_oid, value, type_tag))
        current_oid = returned_oid

    return results


def format_value(value, type_tag):
    """Format a value for display."""
    tag_names = {
        0x02: 'INTEGER', 0x04: 'OCTET_STRING', 0x05: 'NULL',
        0x06: 'OID', 0x40: 'IP_ADDRESS', 0x41: 'COUNTER32',
        0x42: 'GAUGE32', 0x43: 'TIMETICKS',
    }
    tag_name = tag_names.get(type_tag, f'0x{type_tag:02x}') if type_tag else 'ERROR'
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
    if len(sys.argv) < 3:
        print('Usage: python snmpwalk.py <ip> <oid> [options]')
        print()
        print('Options:')
        print('  --community <str>  SNMP community (default: public)')
        print('  --version <1|2>    SNMP version (default: 1)')
        print('  --timeout <sec>    Timeout per OID (default: 5)')
        print('  --retries <n>      Retry count (default: 2)')
        print('  --max <n>          Max OIDs to walk (default: 500)')
        print()
        print('Examples:')
        print('  python snmpwalk.py 192.168.40.249 1.3.6.1.4.1.10642.3.1')
        print('  python snmpwalk.py 192.168.40.249 1.3.6.1.4.1.10642 --timeout 10 --max 200')
        print('  python snmpwalk.py 192.168.40.249 1.3.6.1.2.1.43 --version 2')
        sys.exit(1)

    ip = sys.argv[1]
    oid = sys.argv[2]
    community = 'public'
    version = VERSION_1
    timeout = 5
    retries = 2
    max_oids = 500

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
        elif sys.argv[i] == '--max' and i + 1 < len(sys.argv):
            max_oids = int(sys.argv[i + 1])
            i += 2
        else:
            print(f'Unknown option: {sys.argv[i]}')
            sys.exit(1)

    print(f'Walking {oid} on {ip} (v{"1" if version == VERSION_1 else "2c"}, max={max_oids})...')
    print()

    results = walk(ip, oid, community, version, max_oids, timeout, retries)

    for oid_str, value, type_tag in results:
        print(f'{oid_str} = {format_value(value, type_tag)}')

    print(f'\n{len(results)} OIDs found')


if __name__ == '__main__':
    main()
