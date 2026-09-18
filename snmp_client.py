"""Pure-Python SNMP v1/v2c GET client. Zero external dependencies.

Uses only socket, struct, os from Python stdlib.
Supports SNMPv2c GET and GETBULK operations.
"""

import socket
import struct
import os


# --- BER tag constants ---

TAG_INTEGER = 0x02
TAG_OCTET_STRING = 0x04
TAG_NULL = 0x05
TAG_OID = 0x06
TAG_SEQUENCE = 0x30
TAG_COUNTER32 = 0x41
TAG_GAUGE32 = 0x42
TAG_TIMETICKS = 0x43
TAG_IP_ADDRESS = 0x40

# PDU tags
TAG_GET_REQUEST = 0xA0
TAG_GETNEXT_REQUEST = 0xA1
TAG_GET_RESPONSE = 0xA2
TAG_GET_BULK_REQUEST = 0xA5

# SNMP versions
VERSION_1 = 0
VERSION_2C = 1


class SnmpError(Exception):
    """Base exception for SNMP errors."""
    pass


class SnmpTimeout(SnmpError):
    """No response within timeout."""
    pass


class SnmpAuthenticationError(SnmpError):
    """Community string mismatch."""
    pass


class SnmpNoSuchObject(SnmpError):
    """Agent reported noSuchObject."""
    pass


class SnmpNoSuchInstance(SnmpError):
    """Agent reported noSuchInstance."""
    pass


class SnmpEndOfMibView(SnmpError):
    """Agent reported endOfMibView."""
    pass


class SnmpBadStatus(SnmpError):
    """Agent returned non-zero error-status."""

    def __init__(self, error_status, error_index, oid=None):
        self.error_status = error_status
        self.error_index = error_index
        self.oid = oid
        status_names = {
            1: 'tooBig',
            2: 'noSuchName',
            3: 'badValue',
            4: 'readOnly',
            5: 'genErr',
            6: 'noAccess',
            7: 'wrongType',
            8: 'wrongLength',
            9: 'wrongEncoding',
            10: 'wrongValue',
            11: 'noCreation',
            12: 'inconsistentValue',
            13: 'resourceUnavailable',
            14: 'commitFailed',
            15: 'undoFailed',
            16: 'authorizationError',
            17: 'notWritable',
            18: 'inconsistentName',
        }
        name = status_names.get(error_status, f'unknown({error_status})')
        super().__init__(f'SNMP error-status {name} at index {error_index}')


# --- BER encoding helpers ---

def _encode_length(length):
    """Encode a length value in BER format."""
    if length < 0x80:
        return bytes([length])
    if length < 0x100:
        return bytes([0x81, length])
    if length < 0x10000:
        return bytes([0x82, (length >> 8) & 0xFF, length & 0xFF])
    raise ValueError(f'Length too large for BER encoding: {length}')


def _decode_length(data, offset):
    """Decode BER length from data at offset. Returns (length, new_offset)."""
    if offset >= len(data):
        raise ValueError('Unexpected end of data while decoding length')
    first = data[offset]
    if first < 0x80:
        return first, offset + 1
    num_bytes = first & 0x7F
    if num_bytes == 0:
        raise ValueError('Indefinite length not supported')
    if offset + 1 + num_bytes > len(data):
        raise ValueError('Unexpected end of data in length bytes')
    length = 0
    for i in range(num_bytes):
        length = (length << 8) | data[offset + 1 + i]
    return length, offset + 1 + num_bytes


def _encode_integer(value):
    """Encode an integer as BER INTEGER."""
    if value == 0:
        return bytes([TAG_INTEGER, 0x01, 0x00])
    if value > 0:
        byte_length = (value.bit_length() + 8) // 8
    else:
        byte_length = (-value - 1).bit_length() // 8 + 1
    encoded = value.to_bytes(byte_length, byteorder='big', signed=True)
    if value > 0 and encoded[0] & 0x80:
        encoded = b'\x00' + encoded
    return bytes([TAG_INTEGER]) + _encode_length(len(encoded)) + encoded


def _encode_octet_string(value):
    """Encode a string as BER OCTET STRING."""
    if isinstance(value, str):
        value = value.encode('ascii')
    return bytes([TAG_OCTET_STRING]) + _encode_length(len(value)) + value


def _encode_oid(oid_str):
    """Encode an OID string like '1.3.6.1.2.1.1.1.0' as BER OID."""
    parts = [int(x) for x in oid_str.split('.')]
    if len(parts) < 2:
        raise ValueError(f'OID must have at least 2 components: {oid_str}')
    encoded = bytes([40 * parts[0] + parts[1]])
    for part in parts[2:]:
        if part < 0x80:
            encoded += bytes([part])
        else:
            temp = part
            stack = []
            stack.append(temp & 0x7F)
            temp >>= 7
            while temp > 0:
                stack.append((temp & 0x7F) | 0x80)
                temp >>= 7
            stack.reverse()
            encoded += bytes(stack)
    return bytes([TAG_OID]) + _encode_length(len(encoded)) + encoded


def _encode_sequence(contents):
    """Wrap contents in a SEQUENCE tag."""
    total = b''
    for item in contents:
        total += item
    return bytes([TAG_SEQUENCE]) + _encode_length(len(total)) + total


def _encode_null():
    """Encode NULL."""
    return bytes([TAG_NULL, 0x00])


# --- BER decoding helpers ---

def _decode_oid(data, offset, length):
    """Decode a BER OID. Returns (oid_string, new_offset)."""
    end = offset + length
    if offset >= end:
        raise ValueError('Empty OID')
    first = data[offset]
    components = [first // 40, first % 40]
    offset += 1
    while offset < end:
        component = 0
        while True:
            if offset >= end:
                raise ValueError('Unexpected end of OID data')
            byte = data[offset]
            offset += 1
            component = (component << 7) | (byte & 0x7F)
            if not (byte & 0x80):
                break
        components.append(component)
    return '.'.join(str(c) for c in components), offset


def _decode_value(data, offset):
    """Decode a single BER TLV value. Returns (tag, value, new_offset)."""
    if offset >= len(data):
        raise ValueError('Unexpected end of data')
    tag = data[offset]
    offset += 1
    length, offset = _decode_length(data, offset)

    if tag == TAG_INTEGER or tag == 0x00:
        value = int.from_bytes(data[offset:offset + length], byteorder='big', signed=True)
        return tag, value, offset + length
    elif tag == TAG_OCTET_STRING:
        value = data[offset:offset + length]
        return tag, value, offset + length
    elif tag == TAG_NULL:
        return tag, None, offset + length
    elif tag == TAG_OID:
        oid_str, new_offset = _decode_oid(data, offset, length)
        return tag, oid_str, new_offset
    elif tag == TAG_SEQUENCE:
        return tag, length, offset + length
    elif tag == TAG_COUNTER32 or tag == TAG_GAUGE32 or tag == TAG_TIMETICKS:
        value = int.from_bytes(data[offset:offset + length], byteorder='big', signed=False)
        return tag, value, offset + length
    elif tag == TAG_IP_ADDRESS:
        value = '.'.join(str(b) for b in data[offset:offset + length])
        return tag, value, offset + length
    else:
        raw = data[offset:offset + length]
        return tag, raw, offset + length


def _parse_varbind(data, offset):
    """Parse one VarBind (SEQUENCE { OID, value }). Returns (oid, value, type_tag, new_offset)."""
    if offset >= len(data) or data[offset] != TAG_SEQUENCE:
        raise ValueError(f'Expected SEQUENCE in VarBind at offset {offset}')
    offset += 1
    seq_len, offset = _decode_length(data, offset)
    content_start = offset
    seq_end = content_start + seq_len

    oid_tag, oid_val, after_oid = _decode_value(data, content_start)
    if oid_tag != TAG_OID:
        raise ValueError(f'Expected OID in VarBind, got tag 0x{oid_tag:02x}')

    val_tag, val_val, after_val = _decode_value(data, after_oid)

    return oid_val, val_val, val_tag, seq_end


def _parse_varbind_list(data, offset):
    """Parse a VarBindList (SEQUENCE of VarBinds). Returns list of (oid, value, type_tag)."""
    if offset >= len(data) or data[offset] != TAG_SEQUENCE:
        raise ValueError(f'Expected SEQUENCE in VarBindList at offset {offset}')
    offset += 1
    seq_len, offset = _decode_length(data, offset)
    content_start = offset
    seq_end = content_start + seq_len
    results = []
    pos = content_start
    while pos < seq_end:
        oid, value, vtag, pos = _parse_varbind(data, pos)
        results.append((oid, value, vtag))
    if pos != seq_end:
        raise ValueError(f'VarBindList parse ended at {pos}, expected {seq_end}')
    return results, seq_end


def _build_request(oid_str, request_id, community, version=VERSION_2C, pdu_tag=TAG_GET_REQUEST):
    """Build a complete SNMP GET request packet."""
    oid_encoded = _encode_oid(oid_str)
    varbind = _encode_sequence([oid_encoded, _encode_null()])
    varbind_list = _encode_sequence([varbind])
    pdu_content = (
        _encode_integer(request_id)
        + _encode_integer(0)  # error-status
        + _encode_integer(0)  # error-index
        + varbind_list
    )
    pdu = bytes([pdu_tag]) + _encode_length(len(pdu_content)) + pdu_content
    packet = _encode_sequence([
        _encode_integer(version),
        _encode_octet_string(community),
        pdu,
    ])
    return packet


def _build_getbulk_request(oid_str, request_id, community, max_repetitions=10):
    """Build a SNMPv2c GETBULK request packet."""
    oid_encoded = _encode_oid(oid_str)
    varbind = _encode_sequence([oid_encoded, _encode_null()])
    varbind_list = _encode_sequence([varbind])
    pdu_content = (
        _encode_integer(request_id)
        + _encode_integer(0)  # non-repeaters
        + _encode_integer(max_repetitions)
        + varbind_list
    )
    pdu = bytes([TAG_GET_BULK_REQUEST]) + _encode_length(len(pdu_content)) + pdu_content
    packet = _encode_sequence([
        _encode_integer(VERSION_2C),
        _encode_octet_string(community),
        pdu,
    ])
    return packet


def _parse_response(data):
    """Parse an SNMP GET RESPONSE packet. Returns list of (oid, value, type_tag)."""
    offset = 0
    # Outer SEQUENCE
    if offset >= len(data) or data[offset] != TAG_SEQUENCE:
        raise SnmpError('Invalid response: expected SEQUENCE')
    offset += 1
    seq_len, offset = _decode_length(data, offset)
    content_start = offset
    seq_end = content_start + seq_len
    offset = content_start

    # Version
    ver_tag, version, offset = _decode_value(data, offset)
    if ver_tag != TAG_INTEGER:
        raise SnmpError(f'Invalid response: expected version INTEGER')

    # Community
    comm_tag, community, offset = _decode_value(data, offset)
    if comm_tag != TAG_OCTET_STRING:
        raise SnmpError(f'Invalid response: expected community OCTET STRING')

    # PDU
    pdu_tag = data[offset]
    if pdu_tag not in (TAG_GET_RESPONSE,):
        raise SnmpError(f'Invalid response: expected GetResponse, got tag 0x{pdu_tag:02x}')
    offset += 1
    pdu_len, offset = _decode_length(data, offset)
    pdu_start = offset

    # request-id
    rid_tag, request_id, offset = _decode_value(data, pdu_start)
    # error-status
    es_tag, error_status, offset = _decode_value(data, offset)
    # error-index
    ei_tag, error_index, offset = _decode_value(data, offset)

    if error_status != 0:
        varbinds, _ = _parse_varbind_list(data, offset)
        oid = varbinds[0][0] if varbinds else None
        if error_status == 2:
            raise SnmpNoSuchInstance(f'noSuchName at index {error_index}, OID: {oid}')
        raise SnmpBadStatus(error_status, error_index, oid)

    # VarBindList
    varbinds, _ = _parse_varbind_list(data, offset)
    return varbinds, version


def _generate_request_id():
    """Generate a random request ID."""
    return int.from_bytes(os.urandom(4), byteorder='big', signed=False)


def get(ip, oid, community='public', timeout_sec=3, retries=2, port=161, version=VERSION_2C):
    """Perform SNMP GET for a single OID.

    Args:
        ip: Target IP address.
        oid: OID string like '1.3.6.1.2.1.1.1.0'.
        community: SNMP community string.
        timeout_sec: Socket timeout in seconds.
        retries: Number of retry attempts.
        port: UDP port (default 161).
        version: SNMP version (VERSION_1 or VERSION_2C).

    Returns:
        Tuple of (value, type_tag) where value is decoded.
        type_tag is the BER tag of the returned value.

    Raises:
        SnmpTimeout: No response after retries.
        SnmpBadStatus: Agent returned error.
        SnmpError: Other SNMP errors.
    """
    request_id = _generate_request_id()
    packet = _build_request(oid, request_id, community, version)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout_sec)
    try:
        last_error = None
        for attempt in range(retries + 1):
            try:
                sock.sendto(packet, (ip, port))
                data, addr = sock.recvfrom(65535)
                varbinds, resp_version = _parse_response(data)
                if len(varbinds) == 0:
                    raise SnmpError('Empty VarBindList in response')
                oid_str, value, type_tag = varbinds[0]
                if type_tag == 0x80:
                    raise SnmpNoSuchObject(f'noSuchObject for OID {oid}')
                if type_tag == 0x81:
                    raise SnmpNoSuchInstance(f'noSuchInstance for OID {oid}')
                if type_tag == 0x82:
                    raise SnmpEndOfMibView(f'endOfMibView for OID {oid}')
                return value, type_tag
            except socket.timeout:
                last_error = SnmpTimeout(
                    f'Timeout querying {ip} for {oid} (attempt {attempt + 1}/{retries + 1})'
                )
                continue
            except (SnmpBadStatus, SnmpNoSuchObject, SnmpNoSuchInstance, SnmpEndOfMibView):
                raise
            except SnmpError:
                raise
        raise last_error
    finally:
        sock.close()


def get_multiple(ip, oids, community='public', timeout_sec=3, retries=2, port=161):
    """Perform SNMP GET for multiple OIDs in one request.

    Args:
        ip: Target IP address.
        oids: List of OID strings.
        community: SNMP community string.
        timeout_sec: Socket timeout in seconds.
        retries: Number of retry attempts.
        port: UDP port (default 161).

    Returns:
        List of (oid, value, type_tag) tuples, same order as input.

    Raises:
        SnmpTimeout: No response after retries.
        SnmpBadStatus: Agent returned error.
    """
    request_id = _generate_request_id()

    # Build VarBindList with multiple OIDs
    varbinds_encoded = b''
    for oid_str in oids:
        oid_encoded = _encode_oid(oid_str)
        varbind = _encode_sequence([oid_encoded, _encode_null()])
        varbinds_encoded += varbind
    varbind_list = _encode_sequence([varbinds_encoded])

    pdu_content = (
        _encode_integer(request_id)
        + _encode_integer(0)  # error-status
        + _encode_integer(0)  # error-index
        + varbind_list
    )
    pdu = bytes([TAG_GET_REQUEST]) + _encode_length(len(pdu_content)) + pdu_content
    packet = _encode_sequence([
        _encode_integer(VERSION_2C),
        _encode_octet_string(community),
        pdu,
    ])

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout_sec)
    try:
        last_error = None
        for attempt in range(retries + 1):
            try:
                sock.sendto(packet, (ip, port))
                data, addr = sock.recvfrom(65535)
                varbinds, resp_version = _parse_response(data)
                results = []
                for i, (oid_str, value, type_tag) in enumerate(varbinds):
                    if type_tag == 0x80:
                        results.append((oid_str, None, type_tag))
                    elif type_tag == 0x81:
                        results.append((oid_str, None, type_tag))
                    elif type_tag == 0x82:
                        results.append((oid_str, None, type_tag))
                    else:
                        results.append((oid_str, value, type_tag))
                return results
            except socket.timeout:
                last_error = SnmpTimeout(
                    f'Timeout querying {ip} for {len(oids)} OIDs (attempt {attempt + 1}/{retries + 1})'
                )
                continue
            except (SnmpBadStatus, SnmpNoSuchObject, SnmpNoSuchInstance, SnmpEndOfMibView):
                raise
            except SnmpError:
                raise
        raise last_error
    finally:
        sock.close()


def get_bulk(ip, oid, community='public', max_repetitions=10, timeout_sec=3, retries=2, port=161):
    """Perform SNMP GETBULK for walking an OID subtree.

    Args:
        ip: Target IP address.
        oid: Starting OID string.
        community: SNMP community string.
        max_repetitions: Max number of OIDs to retrieve.
        timeout_sec: Socket timeout in seconds.
        retries: Number of retry attempts.
        port: UDP port (default 161).

    Returns:
        List of (oid, value, type_tag) tuples.
    """
    request_id = _generate_request_id()
    packet = _build_getbulk_request(oid, request_id, community, max_repetitions)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout_sec)
    try:
        last_error = None
        for attempt in range(retries + 1):
            try:
                sock.sendto(packet, (ip, port))
                data, addr = sock.recvfrom(65535)
                varbinds, resp_version = _parse_response(data)
                results = []
                for oid_str, value, type_tag in varbinds:
                    if type_tag == 0x82:  # endOfMibView
                        break
                    results.append((oid_str, value, type_tag))
                return results
            except socket.timeout:
                last_error = SnmpTimeout(
                    f'Timeout on GETBULK {ip} for {oid} (attempt {attempt + 1}/{retries + 1})'
                )
                continue
            except SnmpError:
                raise
        raise last_error
    finally:
        sock.close()
