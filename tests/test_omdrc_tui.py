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

    def test_drc_status_aligns_with_cover_bottom(self):
        app = tui.App('http://localhost:9090')
        app.qstatus = {'art': '/cover'}
        app.now_state = {'drc': {'running': False}}
        painted = []
        class Screen:
            def getmaxyx(self): return (24, 80)
        with patch.object(app, 'colored', side_effect=lambda *args: painted.append(args)), \
             patch.object(app, 'text'), patch.object(app, 'draw_cover') as cover:
            app.draw_now(Screen(), 17)
        cover.assert_called_once_with(unittest.mock.ANY, '/cover', 26, 13, 2, 3)
        drc_row = next(args[1] for args in painted if str(args[3]).startswith('DRC OFF'))
        self.assertEqual(drc_row, 15)
        self.assertLess(max(args[1] for args in painted if 'Album details' in str(args[3])), drc_row)

    def test_stale_sixel_key_clears_without_crashing(self):
        app = tui.App('http://localhost:9090')
        app.sixel_visible = True
        app.cover_key = None
        app.qstatus = {'art': '/cover'}
        app.page = 'DRC'
        class Screen:
            cleared = False
            def clear(self): self.cleared = True
            def erase(self): pass
            def refresh(self): pass
            def getmaxyx(self): return (24, 80)
        screen = Screen()
        with patch.object(app, 'colored'), patch.object(app, 'draw_drc'), \
             patch.object(app, 'draw_live'), patch.object(tui.os, 'write'):
            app.draw(screen)
        self.assertTrue(screen.cleared)
        self.assertFalse(app.sixel_visible)

    def test_now_arrows_seek_and_track_buttons_share_row(self):
        app = tui.App('http://localhost:9090')
        app.player = {'elapsed': 50, 'duration': 100, 'state': 'pause'}
        with patch.object(app, 'transport') as transport:
            app.now_key(tui.curses.KEY_LEFT, None)
            transport.assert_called_once_with('seek', {'seconds': 40})
        with patch.object(app, 'transport') as transport:
            app.now_key(tui.curses.KEY_RIGHT, None)
            transport.assert_called_once_with('seek', {'seconds': 60})
        self.assertEqual(tui.NOW_ACTION_ROWS[1], tui.NOW_ACTION_ROWS[2])
        self.assertLess(tui.NOW_ACTION_ROWS[4], tui.NOW_ACTION_ROWS[5])

    def test_page_tabs_can_be_selected_below_cover(self):
        app = tui.App('http://localhost:9090')
        painted = []
        class Screen:
            def getmaxyx(self): return (24, 80)
        with patch.object(app, 'colored', side_effect=lambda *args: painted.append(args)):
            app.draw_nav(Screen(), 17)
        self.assertEqual([args[1] for args in painted], [16, 16, 16, 16, 17])
        self.assertEqual(painted[-1][2], 80 - len("v Visibility") - 2)
        app.action(9, None)
        self.assertTrue(app.nav_focus)
        app.action(tui.curses.KEY_RIGHT, None)
        app.action(10, None)
        self.assertEqual(app.page, 'DRC')
        self.assertFalse(app.nav_focus)

    def test_missing_ai_settings_opens_browser_and_waits_for_setup(self):
        app = tui.App('http://localhost:9090')
        app.player = {'title': 'Track', 'album': 'Album'}
        pending = {}
        with patch.object(app, 'submit', side_effect=lambda key, work, callback=None: pending.update(key=key, work=work, callback=callback)):
            app.start_research()
        self.assertEqual(pending['key'], 'research')
        with patch.object(app.api, 'request', return_value={'configured': False, 'provider': 'openai'}):
            result = pending['work']()
        with patch.object(tui.webbrowser, 'open', return_value=True) as browser:
            pending['callback'](result)
        browser.assert_called_once_with('http://localhost:9090/k/?page=config&ai_setup=1')
        self.assertTrue(app.research_setup_pending)
        self.assertIsNone(app.research_job)

    def test_research_failure_clears_busy_state(self):
        app = tui.App('http://localhost:9090')
        app.research_job = 'job'
        app.events.put(('research', None, None, 'provider failed'))
        app.drain()
        self.assertIsNone(app.research_job)
        self.assertIn('provider failed', app.message)

    def test_research_box_and_markdown_rows(self):
        rows = tui.research_rows('# Heading\n- A **bold** note\n[Source](https://example.org)', 40)
        self.assertEqual(rows[0][0], 'Heading')
        self.assertEqual(rows[1][0], '• A bold note')
        self.assertIn('https://example.org', rows[2][0])
        app = tui.App('http://localhost:9090')
        app.guide = {'overview': '# Heading\n- A **bold** note'}
        painted = []
        class Screen:
            def getmaxyx(self): return (24, 80)
        with patch.object(app, 'colored', side_effect=lambda *args: painted.append(args)):
            app.draw_research(Screen(), 17)
        self.assertTrue(any(str(args[3]).startswith('┌') for args in painted))
        self.assertTrue(any(str(args[3]).startswith('└') for args in painted))
        self.assertTrue(any(args[3] == 'Heading' for args in painted))

    def test_now_actions_select_and_activate_with_arrows(self):
        app = tui.App('http://localhost:9090')
        with patch.object(app, 'transport') as transport:
            app.now_key(tui.curses.KEY_DOWN, None)
            self.assertEqual(app.now_action, 1)
            app.now_key(10, None)
            transport.assert_called_once_with('prev')
        app.now_action = 8
        with patch.object(app, 'start_research') as research:
            app.now_key(13, None)
            research.assert_called_once()

    def test_audio_format_tiers_match_app(self):
        self.assertEqual(tui.format_color('24 bit / 192 kHz / stereo'), 'green')
        self.assertEqual(tui.format_color('24 bit / 96 kHz / stereo'), 'fmt_hires')
        self.assertEqual(tui.format_color('24 bit / 48 kHz / stereo'), 'fmt_24')
        self.assertEqual(tui.format_color('16 bit / 48 kHz / stereo'), 'fmt_48')
        self.assertEqual(tui.format_color('16 bit / 44.1 kHz / stereo'), 'muted')

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

    def test_balance_bubble_tracks_live_frame_while_average_stays_stable(self):
        app = tui.App('http://localhost:9090')
        app.config.update(levels=False, dr=False, balance=True, balance_window=15)
        app.samples.extend([(90, 1.0, .1), (100, .1, 1.0)])
        painted = []
        class Screen:
            def getmaxyx(self): return (24, 80)
        with patch.object(tui.time, 'monotonic', return_value=100), \
             patch.object(app, 'colored', side_effect=lambda *args: painted.append(args)):
            app.draw_live(Screen(), 20)
        bubble = next(args for args in painted if args[3] == '●')
        self.assertGreater(bubble[2], 2 + len('Balance L ') + 14)
        line = next(args[3] for args in painted if str(args[3]).startswith('Balance '))
        self.assertIn('15s avg +0.0 dB', line)

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
