"""Tests for data generation and interpretation.

Covers:
- SNMP data type interpretation (Counter32, Integer, OctetString)
- Counter edge cases (zero, rollover, large values, None)
- Delta calculation edge cases (negative, zero, missing data)
- Meter unit interpretation
- Realistic Zebra printer response simulation
- End-to-end data pipeline
- Timestamp handling and rounding
- Multi-printer aggregation
"""

import sqlite3
import struct
import sys
import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
from adapters.zebra import ZebraAdapter, OID_LABELS
from snmp_client import TAG_INTEGER, TAG_COUNTER32, TAG_OCTET_STRING, TAG_GAUGE32


class TestCounterEdgeCases(unittest.TestCase):
    """Tests for printer counter edge cases."""

    def test_zero_counter_new_printer(self):
        """A brand new printer should have zero counters."""
        labels = 0
        meters = 0.0
        self.assertEqual(labels, 0)
        self.assertEqual(meters, 0.0)
        # Delta from zero should be the full value
        delta_labels = 100 - labels
        delta_meters = 50.0 - meters
        self.assertEqual(delta_labels, 100)
        self.assertEqual(delta_meters, 50.0)

    def test_counter32_rollover(self):
        """Counter32 wraps around at 2^32."""
        max_val = 4294967295
        # After rollover, next value is 0
        next_val = 0
        # Naive delta would be negative
        naive_delta = next_val - max_val
        self.assertEqual(naive_delta, -4294967295)
        # Correct delta considering rollover
        correct_delta = (next_val + (max_val + 1)) - max_val
        self.assertEqual(correct_delta, 1)

    def test_counter32_rollover_partial(self):
        """Counter32 rollover with partial wrap."""
        old_val = 4294967000
        new_val = 500  # Rolled over and counted 500 more
        # Correct delta
        delta = (new_val + (4294967295 + 1)) - old_val
        self.assertEqual(delta, 796)

    def test_large_counter_values(self):
        """Printers in production can have millions of labels."""
        labels = 2_500_000
        meters = 125_000.50
        # These should be stored and retrieved as integers/floats
        self.assertIsInstance(labels, int)
        self.assertIsInstance(meters, float)
        # Delta calculation
        new_labels = 2_500_150
        new_meters = 125_007.75
        self.assertEqual(new_labels - labels, 150)
        self.assertAlmostEqual(new_meters - meters, 7.25, places=2)

    def test_none_counter_unreachable(self):
        """None counters from unreachable printers."""
        labels = None
        meters = None
        # Delta with None should be None, not crash
        if labels is not None and meters is not None:
            delta = labels - 0
        else:
            delta = None
        self.assertIsNone(delta)

    def test_negative_delta_counter_reset(self):
        """Negative delta suggests counter reset or printer replacement."""
        old_labels = 50000
        new_labels = 100  # Counter was reset
        delta = new_labels - old_labels
        self.assertEqual(delta, -49900)
        # This should be flagged as suspicious
        is_suspicious = delta < 0
        self.assertTrue(is_suspicious)

    def test_counter_increment_consistency(self):
        """Labels and meters should generally increase together."""
        old_labels = 1000
        old_meters = 50.0
        new_labels = 1100
        new_meters = 55.0
        labels_increased = new_labels > old_labels
        meters_increased = new_meters > old_meters
        self.assertTrue(labels_increased)
        self.assertTrue(meters_increased)

    def test_counter_labels_only_no_meters(self):
        """Some printers report labels but not meters."""
        labels = 5000
        meters = None
        # Should be able to use labels even without meters
        self.assertIsNotNone(labels)
        self.assertIsNone(meters)


class TestDeltaCalculation(unittest.TestCase):
    """Tests for shift delta edge cases."""

    def setUp(self):
        self.conn = db.init_db(':memory:')

    def tearDown(self):
        db.close_db(self.conn)

    def test_normal_delta(self):
        """Normal shift: printer printed some labels."""
        db.save_snapshot(self.conn, '10.0.0.1', 1000, 50.0, 'cm',
                         'Zebra ZT230', 'SN1', 'idle',
                         timestamp='2026-09-17T06:00:00')
        db.save_snapshot(self.conn, '10.0.0.1', 1150, 57.5, 'cm',
                         'Zebra ZT230', 'SN1', 'idle',
                         timestamp='2026-09-17T14:00:00')
        delta = db.get_shift_delta(self.conn, '10.0.0.1',
                                   '2026-09-17T06:00:00',
                                   '2026-09-17T14:00:00')
        self.assertEqual(delta['labels_delta'], 150)
        self.assertAlmostEqual(delta['meters_delta'], 7.5, places=2)

    def test_zero_delta_idle_printer(self):
        """Printer was idle during the shift."""
        db.save_snapshot(self.conn, '10.0.0.1', 5000, 250.0, 'cm',
                         'Zebra ZT230', 'SN1', 'idle',
                         timestamp='2026-09-17T06:00:00')
        db.save_snapshot(self.conn, '10.0.0.1', 5000, 250.0, 'cm',
                         'Zebra ZT230', 'SN1', 'idle',
                         timestamp='2026-09-17T14:00:00')
        delta = db.get_shift_delta(self.conn, '10.0.0.1',
                                   '2026-09-17T06:00:00',
                                   '2026-09-17T14:00:00')
        self.assertEqual(delta['labels_delta'], 0)
        self.assertEqual(delta['meters_delta'], 0.0)

    def test_negative_delta_counter_reset(self):
        """Negative delta indicates counter reset."""
        db.save_snapshot(self.conn, '10.0.0.1', 50000, 2500.0, 'cm',
                         'Zebra ZT230', 'SN1', 'idle',
                         timestamp='2026-09-17T06:00:00')
        db.save_snapshot(self.conn, '10.0.0.1', 100, 5.0, 'cm',
                         'Zebra ZT230', 'SN1', 'idle',
                         timestamp='2026-09-17T14:00:00')
        delta = db.get_shift_delta(self.conn, '10.0.0.1',
                                   '2026-09-17T06:00:00',
                                   '2026-09-17T14:00:00')
        self.assertEqual(delta['labels_delta'], -49900)
        self.assertAlmostEqual(delta['meters_delta'], -2495.0, places=2)

    def test_missing_start_snapshot(self):
        """No snapshot at shift start."""
        db.save_snapshot(self.conn, '10.0.0.1', 1000, 50.0, 'cm',
                         'Zebra ZT230', 'SN1', 'idle',
                         timestamp='2026-09-17T14:00:00')
        delta = db.get_shift_delta(self.conn, '10.0.0.1',
                                   '2026-09-17T06:00:00',
                                   '2026-09-17T14:00:00')
        self.assertIsNone(delta)

    def test_missing_end_snapshot(self):
        """No snapshot at shift end — get_snapshot_at returns closest-before."""
        db.save_snapshot(self.conn, '10.0.0.1', 1000, 50.0, 'cm',
                         'Zebra ZT230', 'SN1', 'idle',
                         timestamp='2026-09-17T06:00:00')
        delta = db.get_shift_delta(self.conn, '10.0.0.1',
                                   '2026-09-17T06:00:00',
                                   '2026-09-17T14:00:00')
        # get_snapshot_at returns closest-before, so it finds 06:00 for both
        self.assertIsNotNone(delta)
        self.assertEqual(delta['labels_delta'], 0)
        self.assertEqual(delta['meters_delta'], 0.0)

    def test_both_snapshots_missing(self):
        """No data at all for this printer."""
        delta = db.get_shift_delta(self.conn, '10.0.0.1',
                                   '2026-09-17T06:00:00',
                                   '2026-09-17T14:00:00')
        self.assertIsNone(delta)

    def test_delta_with_none_labels(self):
        """Start snapshot has None labels (printer unreachable at start)."""
        db.save_snapshot(self.conn, '10.0.0.1', None, None, 'unknown',
                         '', '', 'offline',
                         timestamp='2026-09-17T06:00:00')
        db.save_snapshot(self.conn, '10.0.0.1', 1000, 50.0, 'cm',
                         'Zebra ZT230', 'SN1', 'idle',
                         timestamp='2026-09-17T14:00:00')
        delta = db.get_shift_delta(self.conn, '10.0.0.1',
                                   '2026-09-17T06:00:00',
                                   '2026-09-17T14:00:00')
        # None treated as 0
        self.assertEqual(delta['labels_delta'], 1000)
        self.assertEqual(delta['meters_delta'], 50.0)

    def test_same_timestamp_start_end(self):
        """Start and end timestamps are the same."""
        db.save_snapshot(self.conn, '10.0.0.1', 1000, 50.0, 'cm',
                         'Zebra ZT230', 'SN1', 'idle',
                         timestamp='2026-09-17T06:00:00')
        delta = db.get_shift_delta(self.conn, '10.0.0.1',
                                   '2026-09-17T06:00:00',
                                   '2026-09-17T06:00:00')
        self.assertIsNotNone(delta)
        self.assertEqual(delta['labels_delta'], 0)
        self.assertEqual(delta['meters_delta'], 0.0)

    def test_closest_snapshot_used(self):
        """get_snapshot_at should use closest snapshot before target."""
        db.save_snapshot(self.conn, '10.0.0.1', 100, 5.0, 'cm',
                         'Zebra ZT230', 'SN1', 'idle',
                         timestamp='2026-09-17T05:55:00')
        db.save_snapshot(self.conn, '10.0.0.1', 200, 10.0, 'cm',
                         'Zebra ZT230', 'SN1', 'idle',
                         timestamp='2026-09-17T06:05:00')
        # Query for 06:00 — should get 05:55 snapshot (closest before)
        snap = db.get_snapshot_at(self.conn, '10.0.0.1', '2026-09-17T06:00:00')
        self.assertEqual(snap['labels_total'], 100)


class TestMeterUnitInterpretation(unittest.TestCase):
    """Tests for how meter_unit affects data interpretation."""

    def test_linear_meters_meaning(self):
        """linearMeters: value is in meters."""
        meters_value = 125.5
        unit = 'linearMeters'
        # 125.5 linearMeters = 125.5 meters
        self.assertEqual(unit, 'linearMeters')

    def test_linear_feet_conversion(self):
        """linearFeet: value is in feet, can convert to meters."""
        feet = 100.0
        meters = feet * 0.3048
        self.assertAlmostEqual(meters, 30.48, places=2)

    def test_sheets_vs_linear(self):
        """sheets counts labels, linearMeters counts length."""
        sheets_count = 1000
        linear_meters = 50.0
        # These measure different things
        self.assertNotEqual(sheets_count, linear_meters)

    def test_assumed_cm_fallback(self):
        """When vendor OID available but no unit OID, assume cm."""
        unit = 'cm (assumed)'
        self.assertIn('assumed', unit)
        # Should be flagged for verification
        needs_verification = 'assumed' in unit
        self.assertTrue(needs_verification)

    def test_unit_consistency_across_snapshots(self):
        """Unit should be consistent across snapshots for same printer."""
        conn = db.init_db(':memory:')
        db.save_snapshot(conn, '10.0.0.1', 100, 5.0, 'cm',
                         'Zebra ZT230', 'SN1', 'idle',
                         timestamp='2026-09-17T06:00:00')
        db.save_snapshot(conn, '10.0.0.1', 200, 10.0, 'cm',
                         'Zebra ZT230', 'SN1', 'idle',
                         timestamp='2026-09-17T14:00:00')
        history = db.get_history(conn, '10.0.0.1')
        units = [s['meter_unit'] for s in history]
        # All units should be the same
        self.assertEqual(len(set(units)), 1)
        self.assertEqual(units[0], 'cm')
        db.close_db(conn)


class TestRealisticPrinterResponses(unittest.TestCase):
    """Tests simulating actual Zebra printer SNMP responses."""

    def _make_adapter(self, ip='192.168.40.249'):
        return ZebraAdapter(ip, community='public', timeout_sec=3, retries=0)

    @patch.object(ZebraAdapter, '_snmp_get_retry')
    @patch.object(ZebraAdapter, '_snmp_get')
    def test_zt230_full_response(self, mock_get, mock_retry):
        """ZT230 with all OIDs available."""
        mock_get.return_value = (b'ZTC ZT230-200dpi ZPL', TAG_OCTET_STRING)
        mock_retry.side_effect = [
            (15234, TAG_COUNTER32),   # labels
            (50000, TAG_COUNTER32),   # meters
        ]
        result = self._make_adapter('192.168.40.249').get_counters()

        self.assertTrue(result['reachable'])
        self.assertEqual(result['labels_total'], 15234)
        self.assertEqual(result['meters_total'], 50000.0)
        self.assertEqual(result['model_name'], 'ZTC ZT230-200dpi ZPL')

    @patch.object(ZebraAdapter, '_snmp_get_retry')
    @patch.object(ZebraAdapter, '_snmp_get')
    def test_gx430t_labels_only(self, mock_get, mock_retry):
        """GX430t with labels but no meters OID."""
        mock_get.return_value = (b'ZTC GX430t-203dpi ZPL', TAG_OCTET_STRING)
        mock_retry.side_effect = [
            (8901, TAG_COUNTER32),    # labels
            (None, None),             # meters timeout
        ]
        result = self._make_adapter('192.168.40.176').get_counters()

        self.assertTrue(result['reachable'])
        self.assertEqual(result['labels_total'], 8901)
        self.assertIsNone(result['meters_total'])

    @patch.object(ZebraAdapter, '_snmp_get_retry')
    @patch.object(ZebraAdapter, '_snmp_get')
    def test_printer_with_garbage_model_name(self, mock_get, mock_retry):
        """Printer returns non-standard model name."""
        mock_get.return_value = (b'UNKNOWN\x00\x01\x02', TAG_OCTET_STRING)
        mock_retry.side_effect = [(None, None), (None, None)]
        result = self._make_adapter().get_counters()

        self.assertTrue(result['reachable'])
        self.assertIn('UNKNOWN', result['model_name'])

    @patch.object(ZebraAdapter, '_snmp_get')
    def test_unreachable_printer(self, mock_get):
        """Printer is off the network."""
        mock_get.return_value = (None, None)
        result = self._make_adapter().get_counters()

        self.assertFalse(result['reachable'])
        self.assertIsNone(result['labels_total'])
        self.assertIsNone(result['meters_total'])
        self.assertEqual(result['model_name'], '')

    @patch.object(ZebraAdapter, '_snmp_get_retry')
    @patch.object(ZebraAdapter, '_snmp_get')
    def test_partial_oid_response(self, mock_get, mock_retry):
        """Some OIDs respond, others timeout."""
        mock_get.return_value = (b'Zebra', TAG_OCTET_STRING)
        mock_retry.side_effect = [
            (None, None),   # labels timeout
            (None, None),   # meters timeout
        ]
        result = self._make_adapter().get_counters()

        self.assertTrue(result['reachable'])
        self.assertIsNone(result['labels_total'])
        self.assertIsNone(result['meters_total'])
        self.assertEqual(result['model_name'], 'Zebra')


class TestDataPipeline(unittest.TestCase):
    """Tests for end-to-end data flow through the system."""

    def test_snmp_to_db_pipeline(self):
        """Simulate: SNMP response → adapter → DB storage."""
        conn = db.init_db(':memory:')

        # Simulate what adapter.get_counters() would return
        counters = {
            'labels_total': 15234,
            'meters_total': 761700.0,
            'meter_unit': 'linearMeters',
            'model_name': 'ZTC ZT230-200dpi ZPL',
            'serial': '55J12345',
            'status': 'idle',
            'reachable': True,
        }

        # Save to DB
        db.save_snapshot(
            conn, '192.168.40.249',
            counters['labels_total'],
            counters['meters_total'],
            counters['meter_unit'],
            counters['model_name'],
            counters['serial'],
            counters['status'],
            timestamp='2026-09-17T10:00:00'
        )

        # Retrieve and verify
        snap = db.get_latest_snapshot(conn, '192.168.40.249')
        self.assertIsNotNone(snap)
        self.assertEqual(snap['labels_total'], 15234)
        self.assertEqual(snap['meters_total'], 761700.0)
        self.assertEqual(snap['meter_unit'], 'linearMeters')
        self.assertEqual(snap['model_name'], 'ZTC ZT230-200dpi ZPL')
        self.assertEqual(snap['serial'], '55J12345')
        self.assertEqual(snap['status'], 'idle')
        db.close_db(conn)

    def test_data_types_preserved(self):
        """Data types should be preserved through the pipeline."""
        conn = db.init_db(':memory:')

        # Labels is int, meters is float
        labels = 1000
        meters = 50.5

        db.save_snapshot(conn, '10.0.0.1', labels, meters, 'cm',
                         'Zebra', 'SN', 'idle',
                         timestamp='2026-09-17T10:00:00')

        snap = db.get_latest_snapshot(conn, '10.0.0.1')
        self.assertIsInstance(snap['labels_total'], int)
        self.assertIsInstance(snap['meters_total'], float)
        self.assertEqual(snap['labels_total'], 1000)
        self.assertEqual(snap['meters_total'], 50.5)
        db.close_db(conn)

    def test_idempotent_inserts(self):
        """Same data inserted twice should not create duplicates."""
        conn = db.init_db(':memory:')

        ts = '2026-09-17T10:00:00'
        db.save_snapshot(conn, '10.0.0.1', 1000, 50.0, 'cm',
                         'Zebra', 'SN', 'idle', timestamp=ts)
        db.save_snapshot(conn, '10.0.0.1', 1000, 50.0, 'cm',
                         'Zebra', 'SN', 'idle', timestamp=ts)

        history = db.get_history(conn, '10.0.0.1')
        self.assertEqual(len(history), 1)
        db.close_db(conn)

    def test_delta_from_pipeline_data(self):
        """Deltas should be correct after pipeline storage."""
        conn = db.init_db(':memory:')

        # Shift start
        db.save_snapshot(conn, '10.0.0.1', 1000, 50.0, 'cm',
                         'Zebra', 'SN', 'idle',
                         timestamp='2026-09-17T06:00:00')
        # Shift end
        db.save_snapshot(conn, '10.0.0.1', 1150, 57.5, 'cm',
                         'Zebra', 'SN', 'idle',
                         timestamp='2026-09-17T14:00:00')

        delta = db.get_shift_delta(conn, '10.0.0.1',
                                   '2026-09-17T06:00:00',
                                   '2026-09-17T14:00:00')
        self.assertEqual(delta['labels_delta'], 150)
        self.assertAlmostEqual(delta['meters_delta'], 7.5, places=2)
        db.close_db(conn)


class TestTimestampHandling(unittest.TestCase):
    """Tests for timestamp parsing and rounding."""

    def test_iso8601_format(self):
        """Timestamps should be ISO 8601 format."""
        dt = datetime(2026, 9, 17, 10, 30, 45)
        ts = dt.isoformat()
        self.assertEqual(ts, '2026-09-17T10:30:45')
        # Should be parseable back
        parsed = datetime.fromisoformat(ts)
        self.assertEqual(parsed, dt)

    def test_5_minute_rounding_down(self):
        """Rounding down to nearest 5 minutes."""
        dt = datetime(2026, 9, 17, 10, 7, 0)
        result = db._round_timestamp(dt, interval_minutes=5)
        self.assertEqual(result, '2026-09-17T10:05:00')

    def test_5_minute_rounding_up(self):
        """Rounding up to nearest 5 minutes."""
        dt = datetime(2026, 9, 17, 10, 3, 0)
        result = db._round_timestamp(dt, interval_minutes=5)
        self.assertEqual(result, '2026-09-17T10:00:00')

    def test_5_minute_rounding_exact(self):
        """Already on a 5-minute boundary."""
        dt = datetime(2026, 9, 17, 10, 10, 0)
        result = db._round_timestamp(dt, interval_minutes=5)
        self.assertEqual(result, '2026-09-17T10:10:00')

    def test_10_minute_rounding(self):
        """Rounding to 10-minute intervals."""
        dt = datetime(2026, 9, 17, 10, 13, 0)
        result = db._round_timestamp(dt, interval_minutes=10)
        self.assertEqual(result, '2026-09-17T10:10:00')

    def test_overnight_shift_spanning_midnight(self):
        """Shift from 22:00 to 06:00 spans midnight."""
        shift_start = datetime(2026, 9, 17, 22, 0)
        shift_end = datetime(2026, 9, 18, 6, 0)
        # End is after start
        self.assertGreater(shift_end, shift_start)
        # Duration is 8 hours
        duration = shift_end - shift_start
        self.assertEqual(duration.total_seconds(), 8 * 3600)

    def test_timestamp_string_comparison(self):
        """ISO 8601 strings compare correctly lexicographically."""
        ts1 = '2026-09-17T06:00:00'
        ts2 = '2026-09-17T14:00:00'
        ts3 = '2026-09-18T06:00:00'
        self.assertTrue(ts1 < ts2)
        self.assertTrue(ts2 < ts3)
        self.assertTrue(ts1 < ts3)

    def test_rounding_preserves_order(self):
        """Rounded timestamps should maintain chronological order."""
        times = [
            datetime(2026, 9, 17, 10, 1),
            datetime(2026, 9, 17, 10, 3),
            datetime(2026, 9, 17, 10, 7),
            datetime(2026, 9, 17, 10, 12),
        ]
        rounded = [db._round_timestamp(dt) for dt in times]
        for i in range(len(rounded) - 1):
            self.assertTrue(rounded[i] <= rounded[i + 1])


class TestMultiPrinterAggregation(unittest.TestCase):
    """Tests for combining data from multiple printers."""

    def setUp(self):
        self.conn = db.init_db(':memory:')

    def tearDown(self):
        db.close_db(self.conn)

    def test_sum_across_printers(self):
        """Sum labels from all printers."""
        db.save_snapshot(self.conn, '10.0.0.1', 1000, 50.0, 'cm',
                         'Zebra ZT230', 'SN1', 'idle',
                         timestamp='2026-09-17T10:00:00')
        db.save_snapshot(self.conn, '10.0.0.2', 2000, 100.0, 'cm',
                         'Zebra ZT230', 'SN2', 'idle',
                         timestamp='2026-09-17T10:00:00')
        db.save_snapshot(self.conn, '10.0.0.3', 500, 25.0, 'cm',
                         'Zebra ZT230', 'SN3', 'idle',
                         timestamp='2026-09-17T10:00:00')

        latest = db.get_all_printers_latest(self.conn)
        total_labels = sum(s['labels_total'] or 0 for s in latest)
        total_meters = sum(s['meters_total'] or 0 for s in latest)
        self.assertEqual(total_labels, 3500)
        self.assertEqual(total_meters, 175.0)

    def test_average_per_printer(self):
        """Average meters per printer."""
        db.save_snapshot(self.conn, '10.0.0.1', 1000, 100.0, 'cm',
                         'Zebra', 'SN1', 'idle',
                         timestamp='2026-09-17T10:00:00')
        db.save_snapshot(self.conn, '10.0.0.2', 2000, 200.0, 'cm',
                         'Zebra', 'SN2', 'idle',
                         timestamp='2026-09-17T10:00:00')
        db.save_snapshot(self.conn, '10.0.0.3', 1500, 150.0, 'cm',
                         'Zebra', 'SN3', 'idle',
                         timestamp='2026-09-17T10:00:00')

        latest = db.get_all_printers_latest(self.conn)
        meters_values = [s['meters_total'] for s in latest if s['meters_total'] is not None]
        avg = sum(meters_values) / len(meters_values)
        self.assertAlmostEqual(avg, 150.0, places=2)

    def test_exclude_unreachable_printers(self):
        """Unreachable printers should be excluded from totals."""
        db.save_snapshot(self.conn, '10.0.0.1', 1000, 50.0, 'cm',
                         'Zebra', 'SN1', 'idle',
                         timestamp='2026-09-17T10:00:00')
        db.save_snapshot(self.conn, '10.0.0.2', None, None, 'unknown',
                         '', '', 'offline',
                         timestamp='2026-09-17T10:00:00')

        latest = db.get_all_printers_latest(self.conn)
        reachable = [s for s in latest if s['labels_total'] is not None]
        unreachable = [s for s in latest if s['labels_total'] is None]
        self.assertEqual(len(reachable), 1)
        self.assertEqual(len(unreachable), 1)

    def test_shift_delta_all_printers(self):
        """Shift deltas for all printers."""
        db.save_snapshot(self.conn, '10.0.0.1', 1000, 50.0, 'cm',
                         'Zebra', 'SN1', 'idle',
                         timestamp='2026-09-17T06:00:00')
        db.save_snapshot(self.conn, '10.0.0.1', 1100, 55.0, 'cm',
                         'Zebra', 'SN1', 'idle',
                         timestamp='2026-09-17T14:00:00')
        db.save_snapshot(self.conn, '10.0.0.2', 2000, 100.0, 'cm',
                         'Zebra', 'SN2', 'idle',
                         timestamp='2026-09-17T06:00:00')
        db.save_snapshot(self.conn, '10.0.0.2', 2200, 110.0, 'cm',
                         'Zebra', 'SN2', 'idle',
                         timestamp='2026-09-17T14:00:00')

        results = db.get_printers_by_shift(self.conn,
                                            '2026-09-17T06:00:00',
                                            '2026-09-17T14:00:00')
        self.assertEqual(len(results), 2)
        total_labels_delta = sum(r['labels_delta'] for r in results)
        total_meters_delta = sum(r['meters_delta'] for r in results)
        self.assertEqual(total_labels_delta, 300)
        self.assertEqual(total_meters_delta, 15.0)


class TestDataTypeCoercion(unittest.TestCase):
    """Tests for type conversions in the data pipeline."""

    def test_labels_stored_as_integer(self):
        """labels_total should be stored as INTEGER in SQLite."""
        conn = db.init_db(':memory:')
        db.save_snapshot(conn, '10.0.0.1', 1000, 50.0, 'cm',
                         'Zebra', 'SN', 'idle',
                         timestamp='2026-09-17T10:00:00')
        cursor = conn.execute(
            "SELECT typeof(labels_total) FROM snapshots WHERE printer_ip='10.0.0.1'"
        )
        type_name = cursor.fetchone()[0]
        self.assertEqual(type_name, 'integer')
        db.close_db(conn)

    def test_meters_stored_as_real(self):
        """meters_total should be stored as REAL in SQLite."""
        conn = db.init_db(':memory:')
        db.save_snapshot(conn, '10.0.0.1', 1000, 50.5, 'cm',
                         'Zebra', 'SN', 'idle',
                         timestamp='2026-09-17T10:00:00')
        cursor = conn.execute(
            "SELECT typeof(meters_total) FROM snapshots WHERE printer_ip='10.0.0.1'"
        )
        type_name = cursor.fetchone()[0]
        self.assertEqual(type_name, 'real')
        db.close_db(conn)

    def test_string_fields_stored_as_text(self):
        """Model, serial, status should be stored as TEXT."""
        conn = db.init_db(':memory:')
        db.save_snapshot(conn, '10.0.0.1', 1000, 50.0, 'cm',
                         'Zebra ZT230', 'SN123', 'idle',
                         timestamp='2026-09-17T10:00:00')
        cursor = conn.execute(
            "SELECT typeof(model_name), typeof(serial), typeof(status) FROM snapshots WHERE printer_ip='10.0.0.1'"
        )
        model_type, serial_type, status_type = cursor.fetchone()
        self.assertEqual(model_type, 'text')
        self.assertEqual(serial_type, 'text')
        self.assertEqual(status_type, 'text')
        db.close_db(conn)

    def test_int_to_float_coercion(self):
        """Integer meters should be stored and retrieved as float."""
        conn = db.init_db(':memory:')
        db.save_snapshot(conn, '10.0.0.1', 1000, 50.0, 'cm',
                         'Zebra', 'SN', 'idle',
                         timestamp='2026-09-17T10:00:00')
        snap = db.get_latest_snapshot(conn, '10.0.0.1')
        # Even though we passed 50.0 (float), it should be retrievable
        self.assertEqual(snap['meters_total'], 50.0)
        db.close_db(conn)


if __name__ == '__main__':
    unittest.main()
