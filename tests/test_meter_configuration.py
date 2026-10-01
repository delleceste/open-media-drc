"""Timing profiles follow the actual loaded DRC filter configuration."""
import unittest
from unittest import mock
from test_spectrum_source import APP


class MeterConfigurationTests(unittest.TestCase):
    def test_direct_output_is_separate_from_drc(self):
        with mock.patch.object(APP, '_active_brutefir_conf', return_value=None), mock.patch.object(APP, '_spectrum_resolve_source') as source:
            source.return_value.name = 'mpd'
            self.assertEqual(APP._meter_timing_configuration(), {'drc': False, 'source': 'mpd'})

    def test_rate_filter_and_partition_are_in_identity(self):
        with mock.patch.object(APP, '_active_brutefir_conf', return_value='/tmp/drc192.conf'), mock.patch.object(APP, '_parse_brutefir_conf') as parsed, mock.patch.object(APP, '_spectrum_resolve_source') as source, mock.patch.object(APP, '_read_text_quietly', return_value='filter_length: 4096;'), mock.patch.object(APP.os, 'stat') as stat:
            source.return_value.name = 'mpd'
            stat.return_value.st_mtime_ns = 123
            parsed.return_value = {'rate': 192000, 'coeffs': [{'filename': '/tmp/L.raw', 'format': 'FLOAT_LE'}]}
            first = APP._meter_timing_configuration()
            self.assertEqual(first['rate'], 192000)
            self.assertEqual(first['partition'], 4096)
            self.assertEqual(first['filters'], [['/tmp/L.raw', 'FLOAT_LE', 123]])
            parsed.return_value['rate'] = 96000
            self.assertNotEqual(first, APP._meter_timing_configuration())
            parsed.return_value['rate'] = 192000
            stat.return_value.st_mtime_ns = 456
            self.assertNotEqual(first, APP._meter_timing_configuration())
