"""Checks for the terminal client's audio math and DR subscription lifetime."""
import importlib.machinery
import importlib.util
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[1] / 'omdrc-ctrl/tools/omdrc-tui'
spec = importlib.util.spec_from_loader('omdrc_tui', importlib.machinery.SourceFileLoader('omdrc_tui', str(PATH)))
tui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tui)


class TuiTest(unittest.TestCase):
    def test_room_correction_reports_applied_state(self):
        self.assertEqual(tui.drc_summary({"running": False}), "DRC OFF · direct")
        self.assertIn("96 kHz · flat · default · −3.0 dB", tui.drc_summary({
            "running": True, "rate": 96000, "geometry": "flat", "design_id": "default",
            "effective_attenuation_db": 3.0}))

    def test_theme_detection_and_truecolor_cover_parser(self):
        self.assertTrue(tui.theme_is_light('auto', {'COLORFGBG': '0;15'}))
        self.assertFalse(tui.theme_is_light('auto', {'COLORFGBG': '15;0'}))
        rows = tui.parse_cover_ansi('\x1b[38;2;255;0;0;48;2;0;0;255m█\x1b[0m')
        self.assertEqual(rows, [[('█', 196, 21)]])

    def test_silent_dr_block_does_not_crash_history_paint(self):
        class Screen:
            def getmaxyx(self): return (24, 80)
            def addnstr(self, *args): pass
        app = tui.App('http://localhost:9090')
        app.dr_blocks = [[[0, 0], [0, 0]]]
        app.dr = {'track_age_blocks': 1}
        app.draw_live(Screen(), 17)

    def test_first_player_response_clears_connecting_status(self):
        app = tui.App('http://localhost:9090')
        app.events.put(('qstatus', lambda data: setattr(app, 'qstatus', data),
                        {'art': '/qconnect/art?v=1'}, None))
        app.drain()
        self.assertEqual(app.message, 'Connected')
        self.assertEqual(app.qstatus['art'], '/qconnect/art?v=1')

    def test_sixel_cover_uses_pixel_output_in_konsole(self):
        app = tui.App('http://localhost:9090')
        with patch.dict(tui.os.environ, {'KONSOLE_VERSION': '260802'}):
            self.assertEqual(app.cover_mode(), 'sixel')
        app.config['cover_mode'] = 'symbols'
        self.assertEqual(app.cover_mode(), 'symbols')
        app.config['cover_mode'] = 'sixel'
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, limit): return b'image'
        class Result:
            stdout = b'\x1bP0;1;0qdata\x1b\\'
        with patch.object(tui.shutil, 'which', return_value='/usr/bin/chafa'), \
             patch.object(tui.urllib.request, 'urlopen', return_value=Response()), \
             patch.object(tui.subprocess, 'run', return_value=Result()) as run:
            result = app.render_cover('/cover', 26, 13, mode='sixel')
        self.assertEqual(result, Result.stdout)
        self.assertIn('sixels', run.call_args.args[0])
        self.assertIn('on', run.call_args.args[0])

    def test_cover_zoom_key(self):
        app = tui.App('http://localhost:9090')
        app.now_key(ord('C'), None)
        self.assertTrue(app.cover_zoom)
        app.now_key(27, None)
        self.assertFalse(app.cover_zoom)

    def test_default_opens_now_on_local_service_even_with_saved_remote(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'config.json'
            config.write_text('{"server":"http://remote:9090"}')
            with patch.object(tui, 'CONFIG_PATH', config):
                app = tui.App()
        self.assertEqual(app.page, 'Now')
        self.assertEqual(app.api.server, 'http://localhost:9090')

    def test_stream_close_does_not_wait_on_reader_buffer(self):
        stream = tui.Stream.__new__(tui.Stream)
        stream.stop = threading.Event()
        class BlockingResponse:
            def close(self): raise AssertionError('must close in reader thread')
        stream.response = BlockingResponse()
        stream.close()
        self.assertTrue(stream.stop.is_set())

    def test_dr_matches_two_channel_tt_blocks_and_silence_resets(self):
        block = [[.01, .01], [.2, .2]]
        self.assertAlmostEqual(tui.dr_value([block, block]), 6.020599913, places=5)
        self.assertIsNone(tui.dr_value([block, block, [[0, 0], [0, 0]]]))

    def test_balance_averages_power_not_decibels(self):
        samples = [(1, .01, .01), (2, .01, .04)]
        self.assertAlmostEqual(tui.balance_db(samples, 2, 15), 10 * __import__('math').log10(2.5))
        self.assertIn('●', tui.balance_line(3, 11))

    def test_dr_stays_released_until_now_is_opened(self):
        class FakeStream:
            def __init__(self, *args): self.closed = False
            def close(self): self.closed = True
        app = tui.App('http://127.0.0.1:9090')
        app.config.update(levels=False, balance=False, dr=True, dr_keep_minutes=2)
        with patch.object(tui, 'Stream', FakeStream), patch.object(app, 'submit'):
            app.switch('Now')
            first = app.dr_stream
            app.switch('Library')
            with patch.object(tui.time, 'monotonic', return_value=100): app.poll()
            with patch.object(tui.time, 'monotonic', return_value=221): app.poll()
            self.assertIsNotNone(app.keep_prompt_at)
            with patch.object(tui.time, 'monotonic', return_value=282): app.poll()
            app.poll()
            self.assertTrue(first.closed)
            self.assertIsNone(app.dr_stream)
            app.switch('Now')
            self.assertIsNotNone(app.dr_stream)


if __name__ == '__main__':
    unittest.main()
