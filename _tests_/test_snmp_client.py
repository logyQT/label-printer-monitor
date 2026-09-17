"""Tests for snmp_client.py - pure Python SNMP v2c GET client.

Tests cover:
- BER encoding helpers
- BER decoding helpers
- OID encoding
- OID decoding
- SNMP packet construction
- SNMP response parsing
- Error handling classes
- get() function (with mocked socket)
- get_multiple() function
- get_bulk() function
"""

import struct
import socket
import sys
import os
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from snmp_client import (
    _encode_length,
    _decode_length,
    _encode_integer,
    _encode_octet_string,
    _encode_oid,
    _encode_sequence,
    _encode_null,
    _decode_oid,
    _decode_value,
    _parse_varbind,
    _parse_varbind_list,
    _build_request,
    _build_getbulk_request,
    _parse_response,
    _generate_request_id,
    get,
    get_multiple,
    get_bulk,
    SnmpError,
    SnmpTimeout,
    SnmpAuthenticationError,
    SnmpNoSuchObject,
    SnmpNoSuchInstance,
    SnmpEndOfMibView,
    SnmpBadStatus,
    TAG_INTEGER,
    TAG_OCTET_STRING,
    TAG_NULL,
    TAG_OID,
    TAG_SEQUENCE,
    TAG_COUNTER32,
    TAG_GAUGE32,
    TAG_TIMETICKS,
    TAG_GET_REQUEST,
    TAG_GET_RESPONSE,
    TAG_GET_BULK_REQUEST,
    VERSION_1,
    VERSION_2C,
)


class TestEncodeLength(unittest.TestCase):
    """Tests for _encode_length()."""

    def test_short_form_0(self):
        result = _encode_length(0)
        self.assertEqual(result, b'\x00')

    def test_short_form_127(self):
        result = _encode_length(127)
        self.assertEqual(result, bytes([127]))

    def test_short_form_1(self):
        result = _encode_length(1)
        self.assertEqual(result, b'\x01')

    def test_long_form_128(self):
        result = _encode_length(128)
        self.assertEqual(result, b'\x81\x80')

    def test_long_form_255(self):
        result = _encode_length(255)
        self.assertEqual(result, b'\x81\xff')

    def test_long_form_256(self):
        result = _encode_length(256)
        self.assertEqual(result, b'\x82\x01\x00')

    def test_long_form_1000(self):
        result = _encode_length(1000)
        self.assertEqual(result, b'\x82\x03\xe8')

    def test_long_form_65535(self):
        result = _encode_length(65535)
        self.assertEqual(result, b'\x82\xff\xff')

    def test_length_too_large(self):
        with self.assertRaises(ValueError):
            _encode_length(0x1000000)


class TestDecodeLength(unittest.TestCase):
    """Tests for _decode_length()."""

    def test_short_form_0(self):
        data = b'\x00'
        length, offset = _decode_length(data, 0)
        self.assertEqual(length, 0)
        self.assertEqual(offset, 1)

    def test_short_form_127(self):
        data = bytes([127])
        length, offset = _decode_length(data, 0)
        self.assertEqual(length, 127)
        self.assertEqual(offset, 1)

    def test_long_form_128(self):
        data = b'\x81\x80'
        length, offset = _decode_length(data, 0)
        self.assertEqual(length, 128)
        self.assertEqual(offset, 2)

    def test_long_form_256(self):
        data = b'\x82\x01\x00'
        length, offset = _decode_length(data, 0)
        self.assertEqual(length, 256)
        self.assertEqual(offset, 3)

    def test_offset_in_data(self):
        data = b'\xff\x82\x01\x00'
        length, offset = _decode_length(data, 1)
        self.assertEqual(length, 256)
        self.assertEqual(offset, 4)

    def test_unexpected_end(self):
        data = b'\x82\x01'
        with self.assertRaises(ValueError):
            _decode_length(data, 0)

    def test_empty_data(self):
        data = b''
        with self.assertRaises(ValueError):
            _decode_length(data, 0)


class TestEncodeInteger(unittest.TestCase):
    """Tests for _encode_integer()."""

    def test_zero(self):
        result = _encode_integer(0)
        self.assertEqual(result, b'\x02\x01\x00')

    def test_positive_small(self):
        result = _encode_integer(1)
        self.assertEqual(result, b'\x02\x01\x01')

    def test_positive_127(self):
        result = _encode_integer(127)
        self.assertEqual(result, b'\x02\x01\x7f')

    def test_positive_128_needs_padding(self):
        result = _encode_integer(128)
        self.assertEqual(result, b'\x02\x02\x00\x80')

    def test_positive_255(self):
        result = _encode_integer(255)
        self.assertEqual(result, b'\x02\x02\x00\xff')

    def test_positive_256(self):
        result = _encode_integer(256)
        self.assertEqual(result, b'\x02\x02\x01\x00')

    def test_negative_1(self):
        result = _encode_integer(-1)
        self.assertEqual(result, b'\x02\x01\xff')

    def test_negative_128(self):
        result = _encode_integer(-128)
        self.assertEqual(result, b'\x02\x01\x80')

    def test_negative_129(self):
        result = _encode_integer(-129)
        self.assertEqual(result, b'\x02\x02\xff\x7f')

    def test_large_positive(self):
        result = _encode_integer(1000000)
        expected = b'\x02\x03\x0f\x42\x40'
        self.assertEqual(result, expected)


class TestEncodeOctetString(unittest.TestCase):
    """Tests for _encode_octet_string()."""

    def test_empty_string(self):
        result = _encode_octet_string('')
        self.assertEqual(result, b'\x04\x00')

    def test_ascii_string(self):
        result = _encode_octet_string('public')
        expected = b'\x04\x06public'
        self.assertEqual(result, expected)

    def test_bytes_input(self):
        result = _encode_octet_string(b'public')
        expected = b'\x04\x06public'
        self.assertEqual(result, expected)

    def test_single_char(self):
        result = _encode_octet_string('A')
        self.assertEqual(result, b'\x04\x01A')

    def test_long_string(self):
        long_str = 'A' * 200
        result = _encode_octet_string(long_str)
        self.assertEqual(result[0], TAG_OCTET_STRING)
        self.assertEqual(result[1], 0x81)  # long form
        self.assertEqual(result[2], 200)


class TestEncodeOid(unittest.TestCase):
    """Tests for _encode_oid()."""

    def test_simple_oid(self):
        result = _encode_oid('1.3')
        self.assertEqual(result, b'\x06\x01\x2b')

    def test_sysdescr(self):
        result = _encode_oid('1.3.6.1.2.1.1.1.0')
        expected_bytes = b'\x06\x08\x2b\x06\x01\x02\x01\x01\x01\x00'
        self.assertEqual(result, expected_bytes)

    def test_enterprise_oid(self):
        result = _encode_oid('1.3.6.1.4.1.10642')
        self.assertEqual(result[0], TAG_OID)

    def test_two_components(self):
        result = _encode_oid('1.0')
        self.assertEqual(result, b'\x06\x01\x28')

    def test_too_few_components(self):
        with self.assertRaises(ValueError):
            _encode_oid('1')

    def test_zero_zero(self):
        result = _encode_oid('0.0')
        self.assertEqual(result, b'\x06\x01\x00')

    def test_large_subid(self):
        result = _encode_oid('1.3.6.1.4.1.10642')
        # 10642 = 0xD312 in BER base-128 encoding
        self.assertIn(b'\xd3\x12', result)


class TestEncodeSequence(unittest.TestCase):
    """Tests for _encode_sequence()."""

    def test_empty(self):
        result = _encode_sequence([])
        self.assertEqual(result, b'\x30\x00')

    def test_single_item(self):
        item = b'\x02\x01\x01'
        result = _encode_sequence([item])
        self.assertEqual(result, b'\x30\x03\x02\x01\x01')

    def test_multiple_items(self):
        item1 = b'\x02\x01\x01'
        item2 = b'\x04\x02ab'
        result = _encode_sequence([item1, item2])
        self.assertEqual(result, b'\x30\x07\x02\x01\x01\x04\x02ab')


class TestEncodeNull(unittest.TestCase):
    """Tests for _encode_null()."""

    def test_null(self):
        result = _encode_null()
        self.assertEqual(result, b'\x05\x00')


class TestDecodeOid(unittest.TestCase):
    """Tests for _decode_oid()."""

    def test_simple_oid(self):
        data = b'\x2b\x06\x01\x02\x01\x01\x01\x00'
        oid, offset = _decode_oid(data, 0, len(data))
        self.assertEqual(oid, '1.3.6.1.2.1.1.1.0')
        self.assertEqual(offset, 8)

    def test_two_component(self):
        data = b'\x2b'
        oid, offset = _decode_oid(data, 0, 1)
        self.assertEqual(oid, '1.3')

    def test_large_subid(self):
        # Full encoding of 1.3.6.1.4.1.10642
        # 1.3 -> 0x2B, 6 -> 0x06, 1 -> 0x01, 4 -> 0x04, 1 -> 0x01, 10642 -> 0xD3 0x12
        data = b'\x2b\x06\x01\x04\x01\xd3\x12'
        oid, offset = _decode_oid(data, 0, len(data))
        self.assertEqual(oid, '1.3.6.1.4.1.10642')

    def test_offset_in_data(self):
        data = b'\xff\x2b\x06\x01\x02\x01\x01\x01\x00'
        oid, offset = _decode_oid(data, 1, 8)
        self.assertEqual(oid, '1.3.6.1.2.1.1.1.0')

    def test_empty_oid(self):
        with self.assertRaises(ValueError):
            _decode_oid(b'', 0, 0)


class TestDecodeValue(unittest.TestCase):
    """Tests for _decode_value()."""

    def test_integer(self):
        data = b'\x02\x01\x05'
        tag, value, offset = _decode_value(data, 0)
        self.assertEqual(tag, TAG_INTEGER)
        self.assertEqual(value, 5)
        self.assertEqual(offset, 3)

    def test_octet_string(self):
        data = b'\x04\x06public'
        tag, value, offset = _decode_value(data, 0)
        self.assertEqual(tag, TAG_OCTET_STRING)
        self.assertEqual(value, b'public')
        self.assertEqual(offset, 8)

    def test_null(self):
        data = b'\x05\x00'
        tag, value, offset = _decode_value(data, 0)
        self.assertEqual(tag, TAG_NULL)
        self.assertIsNone(value)
        self.assertEqual(offset, 2)

    def test_oid(self):
        data = b'\x06\x08\x2b\x06\x01\x02\x01\x01\x01\x00'
        tag, value, offset = _decode_value(data, 0)
        self.assertEqual(tag, TAG_OID)
        self.assertEqual(value, '1.3.6.1.2.1.1.1.0')
        self.assertEqual(offset, 10)

    def test_counter32(self):
        data = b'\x41\x02\x00\x64'
        tag, value, offset = _decode_value(data, 0)
        self.assertEqual(tag, TAG_COUNTER32)
        self.assertEqual(value, 100)

    def test_gauge32(self):
        data = b'\x42\x02\x01\x00'
        tag, value, offset = _decode_value(data, 0)
        self.assertEqual(tag, TAG_GAUGE32)
        self.assertEqual(value, 256)

    def test_timeticks(self):
        data = b'\x43\x04\x00\x01\x51\x80'
        tag, value, offset = _decode_value(data, 0)
        self.assertEqual(tag, TAG_TIMETICKS)
        self.assertEqual(value, 86400)

    def test_offset_in_data(self):
        data = b'\xff\x02\x01\x05'
        tag, value, offset = _decode_value(data, 1)
        self.assertEqual(tag, TAG_INTEGER)
        self.assertEqual(value, 5)

    def test_empty_data(self):
        with self.assertRaises(ValueError):
            _decode_value(b'', 0)


class TestParseVarbind(unittest.TestCase):
    """Tests for _parse_varbind()."""

    def test_simple_varbind(self):
        # VarBind: SEQUENCE { OID, INTEGER }
        oid_data = b'\x06\x08\x2b\x06\x01\x02\x01\x01\x01\x00'
        val_data = b'\x02\x01\x05'
        varbind = _encode_sequence([oid_data, val_data])
        oid, value, vtag, end_offset = _parse_varbind(varbind, 0)
        self.assertEqual(oid, '1.3.6.1.2.1.1.1.0')
        self.assertEqual(value, 5)
        self.assertEqual(vtag, TAG_INTEGER)


class TestParseVarbindList(unittest.TestCase):
    """Tests for _parse_varbind_list()."""

    def test_single_varbind(self):
        oid_data = b'\x06\x08\x2b\x06\x01\x02\x01\x01\x01\x00'
        val_data = b'\x02\x01\x05'
        varbind = _encode_sequence([oid_data, val_data])
        varbind_list = _encode_sequence([varbind])
        results, offset = _parse_varbind_list(varbind_list, 0)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0], '1.3.6.1.2.1.1.1.0')
        self.assertEqual(results[0][1], 5)

    def test_empty_varbind_list(self):
        varbind_list = _encode_sequence([])
        results, offset = _parse_varbind_list(varbind_list, 0)
        self.assertEqual(len(results), 0)


class TestBuildRequest(unittest.TestCase):
    """Tests for _build_request()."""

    def test_build_get_request(self):
        packet = _build_request('1.3.6.1.2.1.1.1.0', 12345, 'public')
        self.assertIsInstance(packet, bytes)
        self.assertEqual(packet[0], TAG_SEQUENCE)

    def test_build_v1_request(self):
        packet = _build_request('1.3.6.1.2.1.1.1.0', 12345, 'public', version=VERSION_1)
        self.assertIsInstance(packet, bytes)

    def test_packet_contains_community(self):
        packet = _build_request('1.3.6.1.2.1.1.1.0', 12345, 'public')
        self.assertIn(b'public', packet)

    def test_packet_contains_oid(self):
        packet = _build_request('1.3.6.1.2.1.1.1.0', 12345, 'public')
        oid_encoded = _encode_oid('1.3.6.1.2.1.1.1.0')
        self.assertIn(oid_encoded, packet)


class TestBuildGetbulkRequest(unittest.TestCase):
    """Tests for _build_getbulk_request()."""

    def test_build_getbulk(self):
        packet = _build_getbulk_request('1.3.6.1.2.1.1', 12345, 'public', 10)
        self.assertIsInstance(packet, bytes)
        self.assertEqual(packet[0], TAG_SEQUENCE)

    def test_packet_contains_community(self):
        packet = _build_getbulk_request('1.3.6.1.2.1.1', 12345, 'public', 10)
        self.assertIn(b'public', packet)


class TestParseResponse(unittest.TestCase):
    """Tests for _parse_response()."""

    def _build_valid_response(self, oid_str, value, value_tag=TAG_INTEGER):
        """Helper: build a valid SNMP response packet."""
        oid_encoded = _encode_oid(oid_str)
        if value_tag == TAG_INTEGER:
            val_encoded = _encode_integer(value)
        elif value_tag == TAG_OCTET_STRING:
            val_encoded = _encode_octet_string(value)
        elif value_tag == TAG_COUNTER32:
            val_encoded = bytes([TAG_COUNTER32, 0x04]) + value.to_bytes(4, 'big')
        varbind = _encode_sequence([oid_encoded, val_encoded])
        varbind_list = _encode_sequence([varbind])
        pdu_content = (
            _encode_integer(1)  # request-id
            + _encode_integer(0)  # error-status
            + _encode_integer(0)  # error-index
            + varbind_list
        )
        pdu = bytes([TAG_GET_RESPONSE]) + _encode_length(len(pdu_content)) + pdu_content
        packet = _encode_sequence([
            _encode_integer(VERSION_2C),
            _encode_octet_string('public'),
            pdu,
        ])
        return packet

    def test_parse_integer_response(self):
        packet = self._build_valid_response('1.3.6.1.2.1.1.1.0', 42)
        varbinds, version = _parse_response(packet)
        self.assertEqual(len(varbinds), 1)
        self.assertEqual(varbinds[0][0], '1.3.6.1.2.1.1.1.0')
        self.assertEqual(varbinds[0][1], 42)
        self.assertEqual(version, VERSION_2C)

    def test_parse_string_response(self):
        packet = self._build_valid_response('1.3.6.1.2.1.1.1.0', 'test', TAG_OCTET_STRING)
        varbinds, version = _parse_response(packet)
        self.assertEqual(varbinds[0][1], b'test')

    def test_parse_error_status(self):
        # Build response with non-zero error-status
        oid_encoded = _encode_oid('1.3.6.1.2.1.1.1.0')
        varbind = _encode_sequence([oid_encoded, _encode_null()])
        varbind_list = _encode_sequence([varbind])
        pdu_content = (
            _encode_integer(1)  # request-id
            + _encode_integer(2)  # error-status = noSuchName
            + _encode_integer(1)  # error-index
            + varbind_list
        )
        pdu = bytes([TAG_GET_RESPONSE]) + _encode_length(len(pdu_content)) + pdu_content
        packet = _encode_sequence([
            _encode_integer(VERSION_2C),
            _encode_octet_string('public'),
            pdu,
        ])
        with self.assertRaises(SnmpNoSuchInstance):
            _parse_response(packet)


class TestGenerateRequestId(unittest.TestCase):
    """Tests for _generate_request_id()."""

    def test_returns_integer(self):
        rid = _generate_request_id()
        self.assertIsInstance(rid, int)

    def test_unique_values(self):
        rids = {_generate_request_id() for _ in range(100)}
        # Should have at least 90 unique values (probabilistic)
        self.assertGreater(len(rids), 90)


class TestSnmpGetFunction(unittest.TestCase):
    """Tests for get() function with mocked socket."""

    def _mock_response(self, oid_str, value, value_tag=TAG_INTEGER):
        """Create a mock SNMP response packet."""
        oid_encoded = _encode_oid(oid_str)
        if value_tag == TAG_INTEGER:
            val_encoded = _encode_integer(value)
        elif value_tag == TAG_OCTET_STRING:
            val_encoded = _encode_octet_string(value)
        varbind = _encode_sequence([oid_encoded, val_encoded])
        varbind_list = _encode_sequence([varbind])
        pdu_content = (
            _encode_integer(1)
            + _encode_integer(0)
            + _encode_integer(0)
            + varbind_list
        )
        pdu = bytes([TAG_GET_RESPONSE]) + _encode_length(len(pdu_content)) + pdu_content
        packet = _encode_sequence([
            _encode_integer(VERSION_2C),
            _encode_octet_string('public'),
            pdu,
        ])
        return packet

    @patch('snmp_client.socket.socket')
    def test_successful_get(self, mock_socket_class):
        mock_sock = MagicMock()
        mock_socket_class.return_value = mock_sock
        response = self._mock_response('1.3.6.1.2.1.1.1.0', 42)
        mock_sock.recvfrom.return_value = (response, ('10.0.0.1', 161))

        value, tag = get('10.0.0.1', '1.3.6.1.2.1.1.1.0', 'public', 3, 0)
        self.assertEqual(value, 42)
        mock_sock.sendto.assert_called_once()
        mock_sock.close.assert_called_once()

    @patch('snmp_client.socket.socket')
    def test_timeout_retries(self, mock_socket_class):
        mock_sock = MagicMock()
        mock_socket_class.return_value = mock_sock
        mock_sock.recvfrom.side_effect = socket.timeout

        with self.assertRaises(SnmpTimeout):
            get('10.0.0.1', '1.3.6.1.2.1.1.1.0', 'public', 1, 1)
        # Should have tried 2 times (initial + 1 retry)
        self.assertEqual(mock_sock.sendto.call_count, 2)

    @patch('snmp_client.socket.socket')
    def test_no_such_instance(self, mock_socket_class):
        mock_sock = MagicMock()
        mock_socket_class.return_value = mock_sock
        # Build response with noSuchInstance (tag 0x81)
        oid_encoded = _encode_oid('1.3.6.1.2.1.1.1.0')
        varbind = _encode_sequence([oid_encoded, bytes([0x81, 0x00])])
        varbind_list = _encode_sequence([varbind])
        pdu_content = (
            _encode_integer(1)
            + _encode_integer(0)
            + _encode_integer(0)
            + varbind_list
        )
        pdu = bytes([TAG_GET_RESPONSE]) + _encode_length(len(pdu_content)) + pdu_content
        packet = _encode_sequence([
            _encode_integer(VERSION_2C),
            _encode_octet_string('public'),
            pdu,
        ])
        mock_sock.recvfrom.return_value = (packet, ('10.0.0.1', 161))

        with self.assertRaises(SnmpNoSuchInstance):
            get('10.0.0.1', '1.3.6.1.2.1.1.1.0', 'public', 3, 0)


class TestSnmpGetMultipleFunction(unittest.TestCase):
    """Tests for get_multiple() with mocked socket."""

    @patch('snmp_client.socket.socket')
    def test_get_multiple(self, mock_socket_class):
        mock_sock = MagicMock()
        mock_socket_class.return_value = mock_sock

        # Build response with 2 varbinds
        oid1 = _encode_oid('1.3.6.1.2.1.1.1.0')
        val1 = _encode_integer(42)
        oid2 = _encode_oid('1.3.6.1.2.1.1.5.0')
        val2 = _encode_octet_string('test')
        vb1 = _encode_sequence([oid1, val1])
        vb2 = _encode_sequence([oid2, val2])
        vbl = _encode_sequence([vb1, vb2])
        pdu_content = (
            _encode_integer(1)
            + _encode_integer(0)
            + _encode_integer(0)
            + vbl
        )
        pdu = bytes([TAG_GET_RESPONSE]) + _encode_length(len(pdu_content)) + pdu_content
        packet = _encode_sequence([
            _encode_integer(VERSION_2C),
            _encode_octet_string('public'),
            pdu,
        ])
        mock_sock.recvfrom.return_value = (packet, ('10.0.0.1', 161))

        results = get_multiple(
            '10.0.0.1',
            ['1.3.6.1.2.1.1.1.0', '1.3.6.1.2.1.1.5.0'],
            'public', 3, 0
        )
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0][1], 42)
        self.assertEqual(results[1][1], b'test')


class TestSnmpGetBulkFunction(unittest.TestCase):
    """Tests for get_bulk() with mocked socket."""

    @patch('snmp_client.socket.socket')
    def test_get_bulk(self, mock_socket_class):
        mock_sock = MagicMock()
        mock_socket_class.return_value = mock_sock

        # Build response with bulk results
        results_list = []
        for i in range(3):
            oid = _encode_oid(f'1.3.6.1.2.1.1.{i}.0')
            val = _encode_integer(i * 10)
            results_list.append(_encode_sequence([oid, val]))
        vbl = _encode_sequence(results_list)
        pdu_content = (
            _encode_integer(1)
            + _encode_integer(0)
            + _encode_integer(0)
            + vbl
        )
        pdu = bytes([TAG_GET_RESPONSE]) + _encode_length(len(pdu_content)) + pdu_content
        packet = _encode_sequence([
            _encode_integer(VERSION_2C),
            _encode_octet_string('public'),
            pdu,
        ])
        mock_sock.recvfrom.return_value = (packet, ('10.0.0.1', 161))

        results = get_bulk('10.0.0.1', '1.3.6.1.2.1.1', 'public', 10, 3, 0)
        self.assertEqual(len(results), 3)


class TestErrorClasses(unittest.TestCase):
    """Tests for error classes."""

    def test_snmp_error(self):
        e = SnmpError('test error')
        self.assertEqual(str(e), 'test error')
        self.assertIsInstance(e, Exception)

    def test_snmp_timeout(self):
        e = SnmpTimeout('timeout')
        self.assertIsInstance(e, SnmpError)

    def test_snmp_no_such_object(self):
        e = SnmpNoSuchObject('not found')
        self.assertIsInstance(e, SnmpError)

    def test_snmp_no_such_instance(self):
        e = SnmpNoSuchInstance('not found')
        self.assertIsInstance(e, SnmpError)

    def test_snmp_end_of_mib_view(self):
        e = SnmpEndOfMibView('end')
        self.assertIsInstance(e, SnmpError)

    def test_snmp_bad_status(self):
        e = SnmpBadStatus(2, 1, '1.3.6.1')
        self.assertEqual(e.error_status, 2)
        self.assertEqual(e.error_index, 1)
        self.assertEqual(e.oid, '1.3.6.1')
        self.assertIn('noSuchName', str(e))

    def test_snmp_bad_status_unknown(self):
        e = SnmpBadStatus(99, 0)
        self.assertIn('unknown(99)', str(e))


class TestConstants(unittest.TestCase):
    """Tests for SNMP constants."""

    def test_tag_values(self):
        self.assertEqual(TAG_INTEGER, 0x02)
        self.assertEqual(TAG_OCTET_STRING, 0x04)
        self.assertEqual(TAG_NULL, 0x05)
        self.assertEqual(TAG_OID, 0x06)
        self.assertEqual(TAG_SEQUENCE, 0x30)
        self.assertEqual(TAG_COUNTER32, 0x41)
        self.assertEqual(TAG_GAUGE32, 0x42)
        self.assertEqual(TAG_TIMETICKS, 0x43)

    def test_pdu_tags(self):
        self.assertEqual(TAG_GET_REQUEST, 0xA0)
        self.assertEqual(TAG_GET_RESPONSE, 0xA2)
        self.assertEqual(TAG_GET_BULK_REQUEST, 0xA5)

    def test_versions(self):
        self.assertEqual(VERSION_1, 0)
        self.assertEqual(VERSION_2C, 1)


class TestEndToEndEncoding(unittest.TestCase):
    """End-to-end tests: encode then decode."""

    def test_integer_roundtrip(self):
        for val in [0, 1, 127, 128, 255, 256, 1000, -1, -128, -129]:
            encoded = _encode_integer(val)
            tag, decoded, offset = _decode_value(encoded, 0)
            self.assertEqual(tag, TAG_INTEGER)
            self.assertEqual(decoded, val)
            self.assertEqual(offset, len(encoded))

    def test_octet_string_roundtrip(self):
        for s in ['', 'hello', 'public', 'A' * 300]:
            encoded = _encode_octet_string(s)
            tag, decoded, offset = _decode_value(encoded, 0)
            self.assertEqual(tag, TAG_OCTET_STRING)
            if isinstance(s, str):
                s = s.encode('ascii')
            self.assertEqual(decoded, s)

    def test_oid_roundtrip(self):
        oids = ['1.3.6.1.2.1.1.1.0', '1.3.6.1.4.1.10642.20.17.2.0', '0.0']
        for oid_str in oids:
            encoded = _encode_oid(oid_str)
            tag, decoded, offset = _decode_value(encoded, 0)
            self.assertEqual(tag, TAG_OID)
            self.assertEqual(decoded, oid_str)

    def test_full_packet_roundtrip(self):
        oid_str = '1.3.6.1.2.1.1.1.0'
        request_id = 12345
        packet = _build_request(oid_str, request_id, 'public')
        # Packet should be parseable (at least structurally)
        tag, length, offset = _decode_value(packet, 0)
        self.assertEqual(tag, TAG_SEQUENCE)


if __name__ == '__main__':
    unittest.main()
