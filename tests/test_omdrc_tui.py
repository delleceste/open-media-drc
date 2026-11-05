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
    def test_discover_genres_can_select_multiple_and_cancel(self):
        app = tui.App('http://localhost:9090')
        app.tab = 1
        genres = [{'id': '1', 'name': 'Jazz'}, {'id': '2', 'name': 'Classical'}]
        class Screen:
            def __init__(self, keys): self.keys = iter(keys)
            def getmaxyx(self): return (20, 80)
            def timeout(self, value): pass
            def move(self, *args): pass
            def clrtoeol(self): pass
            def refresh(self): pass
            def insstr(self, *args): pass
            def getch(self): return next(self.keys, -1)  # Esc delay expires
        with patch.object(app.api, 'request', return_value={'genres': genres}) as request, \
             patch.object(app, 'draw'), patch.object(app, 'colored'), \
             patch.object(app, 'save'), patch.object(app, 'fetch_list') as fetch:
            app.choose_genres(Screen([ord(' '), tui.curses.KEY_DOWN, ord(' '), 10]))
            self.assertEqual(app.filters['genre'], '1,2')
            fetch.assert_called_once()
            app.choose_genres(Screen([ord('c'), 27]))
            self.assertEqual(app.filters['genre'], '1,2')
            request.assert_called_once_with('/qobuz/genres')

    def test_discover_genres_persist_across_restarts_and_can_be_cleared(self):
        class Screen:
            def __init__(self, keys): self.keys = iter(keys)
            def getmaxyx(self): return (20, 80)
            def timeout(self, value): pass
            def move(self, *args): pass
            def clrtoeol(self): pass
            def refresh(self): pass
            def insstr(self, *args): pass
            def getch(self): return next(self.keys)
        with tempfile.TemporaryDirectory() as directory, patch.object(tui, 'CONFIG_PATH', Path(directory) / 'config.json'):
            app = tui.App('http://localhost:9090')
            app.genres = [{'id': '1', 'name': 'Jazz'}]
            with patch.object(app, 'draw'), patch.object(app, 'colored'), patch.object(app, 'fetch_list'):
                app.choose_genres(Screen([ord(' '), 10]))
            self.assertEqual(tui.App('http://localhost:9090').filters['genre'], '1')
            with patch.object(app, 'draw'), patch.object(app, 'colored'), patch.object(app, 'fetch_list'):
                app.choose_genres(Screen([ord('c'), 10]))
            self.assertEqual(tui.App('http://localhost:9090').filters['genre'], '')

    def test_label_picker_can_cancel_or_add_known_and_custom_labels(self):
        class Screen:
            def __init__(self, keys): self.keys = iter(keys)
            def getmaxyx(self): return (24, 80)
            def timeout(self, value): pass
            def move(self, *args): pass
            def clrtoeol(self): pass
            def refresh(self): pass
            def insstr(self, *args): pass
            def getch(self): return next(self.keys, -1)  # Esc delay expires
        with tempfile.TemporaryDirectory() as directory, patch.object(tui, 'CONFIG_PATH', Path(directory) / 'config.json'):
            app = tui.App('http://localhost:9090')
            app.filters['label'] = ['Decca']
            known = {'labels': [{'name': 'Decca'}, {'name': 'BIS'}]}
            with patch.object(app.api, 'request', return_value=known) as request, \
                 patch.object(app, 'draw'), patch.object(app, 'colored'), \
                 patch.object(app, 'fetch_list') as fetch, patch.object(app, 'prompt', return_value='My Label'):
                app.choose_labels(Screen([ord('c'), 27]))
                self.assertEqual(app.filters['label'], ['Decca'])
                fetch.assert_not_called()
                app.choose_labels(Screen([ord('c'), tui.curses.KEY_DOWN, ord(' '), ord('a'), 10]))
                self.assertEqual(app.filters['label'], ['BIS', 'My Label'])
                fetch.assert_called_once()
                self.assertEqual(request.call_args.args[0], '/qobuz/labels')
            self.assertEqual(tui.App('http://localhost:9090').config['custom_labels'], ['My Label'])

    def test_search_escape_returns_to_sidebar(self):
        app = tui.App('http://localhost:9090')
        app.query = 'Bach'
        app.search_draft = 'Bach changed'
        app.search_editing = True
        app.library_sidebar_focus = False
        with patch.object(tui.curses, 'curs_set'):
            app.search_edit_key(27)
        self.assertFalse(app.search_editing)
        self.assertTrue(app.library_sidebar_focus)
        self.assertEqual(app.library_sidebar_index, 0)
        self.assertEqual(app.search_draft, 'Bach')

    def test_space_toggles_playback_across_views(self):
        app = tui.App('http://localhost:9090')
        with patch.object(app, 'transport') as transport:
            for page in ('Library', 'Album', 'DRC', 'Research', 'Config'):
                app.page = page
                app.player['state'] = 'play'
                app.action(ord(' '), None)
                transport.assert_called_with('pause')
                app.player['state'] = 'pause'
                app.action(ord(' '), None)
                transport.assert_called_with('play')
            self.assertEqual(transport.call_count, 10)
            app.page = 'Library'
            app.begin_search_edit()
            app.action(ord(' '), None)
            self.assertEqual(app.search_draft, ' ')
            self.assertEqual(transport.call_count, 10)

    def test_search_escape_redraws_highlighted_sidebar_immediately(self):
        app = tui.App('http://localhost:9090')
        app.page = 'Library'
        app.begin_search_edit()
        screen = object()
        with patch.object(tui.curses, 'curs_set'), patch.object(app, 'draw') as draw:
            app.action(27, screen)
        draw.assert_called_once_with(screen)
        self.assertTrue(app.library_sidebar_focus)
        painted = []
        class Screen:
            def getmaxyx(self): return (24, 80)
        with patch.object(app, 'colored', side_effect=lambda *args: painted.append(args)):
            app.draw_library(Screen(), 20)
        search = next(args for args in painted if args[1:3] == (2, 1))
        self.assertTrue(search[3].startswith('▸ 1 Search'))
        self.assertEqual(len(search[3]), 25)
        self.assertEqual(search[5], tui.curses.A_REVERSE | tui.curses.A_BOLD)

    def test_search_query_is_submitted_before_choosing_labels(self):
        app = tui.App('http://localhost:9090')
        app.page = 'Library'
        with patch.object(tui.curses, 'curs_set'), patch.object(app, 'submit') as submit:
            app.begin_search_edit()
            for char in 'Mahler': app.action(ord(char), None)
            app.action(9, None)
            self.assertEqual(app.query, 'Mahler')
            self.assertEqual(app.library_right_index, 1)
            app.filters['label'] = ['Pentatone', 'Deutsche Grammophon']
            app.fetch_list()
            work = submit.call_args.args[1]
        with patch.object(app.api, 'request', return_value={'results': []}) as request:
            work()
        self.assertEqual(request.call_args.args[0], '/qobuz/search')
        self.assertEqual(request.call_args.args[1]['q'], 'Mahler')
        self.assertEqual(request.call_args.args[1]['label'], ['Pentatone', 'Deutsche Grammophon'])

    def test_search_and_discover_labels_are_saved_separately(self):
        class Screen:
            def __init__(self, keys): self.keys = iter(keys)
            def getmaxyx(self): return (24, 80)
            def timeout(self, value): pass
            def move(self, *args): pass
            def clrtoeol(self): pass
            def refresh(self): pass
            def insstr(self, *args): pass
            def getch(self): return next(self.keys)
        known = {'labels': [{'name': 'Pentatone'}, {'name': 'Deutsche Grammophon'}]}
        with tempfile.TemporaryDirectory() as directory, patch.object(tui, 'CONFIG_PATH', Path(directory) / 'config.json'):
            app = tui.App('http://localhost:9090')
            with patch.object(app.api, 'request', return_value=known), \
                 patch.object(app, 'draw'), patch.object(app, 'colored'), patch.object(app, 'fetch_list'):
                app.choose_labels(Screen([ord(' '), 10]))
                app.tab = 1
                app.choose_labels(Screen([tui.curses.KEY_DOWN, ord(' '), 10]))
            fresh = tui.App('http://localhost:9090')
            self.assertEqual(fresh.filters['label'], ['Pentatone'])
            self.assertEqual(fresh.filters['discover_label'], ['Deutsche Grammophon'])
            self.assertEqual(fresh.config['search_labels'], ['Pentatone'])
            self.assertEqual(fresh.config['discover_labels'], ['Deutsche Grammophon'])

    def test_search_displays_backend_failure_and_allows_long_fetch(self):
        app = tui.App('http://localhost:9090')
        app.query = 'Mahler'
        app.filters['label'] = ['Pentatone', 'Alpha']
        with patch.object(app, 'submit') as submit:
            app.fetch_list()
        work, apply = submit.call_args.args[1:]
        with patch.object(app.api, 'request', return_value={'ok': True, 'results': []}) as request:
            work()
        self.assertEqual(request.call_args.kwargs['timeout'], 60)
        apply({'results': [], 'qobuz_error': 'Qobuz unavailable'})
        self.assertEqual(app.list_error, 'Qobuz unavailable')
        painted = []
        class Screen:
            def getmaxyx(self): return (24, 80)
        with patch.object(app, 'colored', side_effect=lambda *args: painted.append(args)):
            app.draw_library(Screen(), 20)
        self.assertTrue(any(args[1] == 7 and 'Qobuz unavailable' in args[3] and args[4] == 'red'
                            for args in painted))
        self.assertTrue(any(args[1] == 8 and 'Search unavailable' in args[3] for args in painted))

    def test_missing_list_cover_has_visible_placeholder(self):
        app = tui.App('http://localhost:9090')
        app.config['cover_mode'] = 'sixel'
        painted = []
        class Screen: pass
        with patch.object(app, 'submit'), patch.object(app, 'colored', side_effect=lambda *args: painted.append(args)):
            app.draw_list_cover(Screen(), 'https://example.test/missing.jpg', 3, 4, 12, 4)
        self.assertTrue(any(args[1:5] == (6, 3, 'No cover', 'muted') for args in painted))

    def test_failed_local_cover_uses_boxed_local_marker(self):
        app = tui.App('http://localhost:9090')
        app.config['cover_mode'] = 'sixel'
        painted = []
        class Screen: pass
        with patch.object(app, 'submit'), patch.object(app, 'colored', side_effect=lambda *args: painted.append(args)):
            app.draw_list_cover(Screen(), '/qobuz/local/art?file=missing.flac', 3, 4, 10, 3, local=True)
        marker = [(args[1], args[3]) for args in painted]
        self.assertEqual(marker, [(4, '┌────────┐'), (5, '│ local  │'), (6, '└────────┘')])

    def test_result_dr_badges_follow_web_log_kind_and_use_own_color(self):
        self.assertEqual(tui.album_dr({'dr': 10}), ('DR=10', 'green', tui.curses.A_BOLD))
        self.assertEqual(tui.album_dr({'dr_log': {'dr': 8, 'kind': 'estimate'}}),
                         ('DR ~8', 'yellow', tui.curses.A_DIM))
        app = tui.App('http://localhost:9090')
        app.rows = [
            {'title': 'Exact', 'artist': 'Orchestra', 'year': 2024, 'bits': 24, 'rate': 96,
             'dr_log': {'dr': 10, 'kind': 'exact'}},
            {'title': 'Estimated', 'artist': 'Orchestra', 'year': 2025, 'bits': 24, 'rate': 96,
             'dr_log': {'dr': 8, 'kind': 'estimate'}},
        ]
        painted = []
        class Screen:
            def getmaxyx(self): return (24, 80)
        with patch.object(app, 'colored', side_effect=lambda *args: painted.append(args)):
            app.draw_library(Screen(), 20)
        exact = next(args for args in painted if args[3] == 'DR=10')
        estimate = next(args for args in painted if args[3] == 'DR ~8')
        self.assertEqual((exact[1], exact[4], exact[5]), (11, 'green', tui.curses.A_BOLD))
        self.assertEqual((estimate[1], estimate[4], estimate[5]), (15, 'yellow', tui.curses.A_DIM))
        self.assertTrue(any(args[1] == 11 and '24/96' in args[3] and args[4] == 'fmt_hires'
                            and args[2] < exact[2] for args in painted))

    def test_search_shows_loading_and_one_line_between_result_boxes(self):
        app = tui.App('http://localhost:9090')
        app.rows = [{'title': 'First'}, {'title': 'Second'}]
        app.pending.add('list-0')
        painted = []
        class Screen:
            def getmaxyx(self): return (24, 80)
        with patch.object(app, 'colored', side_effect=lambda *args: painted.append(args)), \
             patch.object(tui.time, 'monotonic', return_value=100):
            app.draw_library(Screen(), 20)
        self.assertTrue(any(args[1] == 7 and 'Searching Qobuz' in args[3] and '█' in args[3]
                            and args[4] == 'green' for args in painted))
        titles = [(args[1], args[3]) for args in painted if args[3] in ('First', 'Second')]
        self.assertEqual(titles, [(9, 'First'), (13, 'Second')])
        self.assertFalse(any(args[1] == 12 and args[2] >= 29 for args in painted))
        app.tab = 1
        painted.clear()
        with patch.object(app, 'colored', side_effect=lambda *args: painted.append(args)):
            app.draw_library(Screen(), 20)
        self.assertTrue(any(args[1] == 5 and 'Loading results' in args[3] and args[4] == 'green'
                            for args in painted))

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
        self.assertEqual(app.footer_status(40), '■ Stopped')
        self.assertEqual(app.qstatus['art'], '/qconnect/art?v=1')

    def test_cover_remains_after_album_finishes_until_another_cover_arrives(self):
        app = tui.App('http://localhost:9090')
        app.qstatus = {'art': '/cover/first'}
        self.assertEqual(app.current_art(), '/cover/first')
        app.qstatus = {}
        app.now_state = {'music': {}}
        self.assertEqual(app.current_art(), '/cover/first')
        app.now_state = {'music': {'art': '/cover/second'}}
        self.assertEqual(app.current_art(), '/cover/second')

    def test_now_cover_uses_large_qobuz_image_and_retains_it_at_stop(self):
        app = tui.App('http://localhost:9090')
        with patch.object(app, 'submit', side_effect=lambda key, work, callback: callback({
                'album': {'image_large': 'https://static.qobuz.com/large.jpg'}})):
            app.update_qstatus({'art': '/qconnect/art?v=1', 'qobuz_album': '123'})
        self.assertEqual(app.current_art(), 'https://static.qobuz.com/large.jpg')
        app.update_qstatus({})
        self.assertEqual(app.current_art(), 'https://static.qobuz.com/large.jpg')

    def test_footer_shows_playback_and_expires_transient_messages(self):
        app = tui.App('http://localhost:9090')
        self.assertEqual(app.footer_status(40), 'Connecting…')
        app.connected = True
        app.player = {'state': 'play', 'album': 'A long album title', 'title': 'A long track title'}
        self.assertEqual(app.footer_status(80), '▶ A long album title / A long track title')
        self.assertTrue(app.footer_status(14).endswith('…'))
        app.player['state'] = 'pause'
        self.assertTrue(app.footer_status(80).startswith('⏸ '))
        app.message = 'levels on · dr on · balance on'
        self.assertEqual(app.footer_status(80), app.message)
        self.assertTrue(app.footer_status(80, app._message_at + 4).startswith('⏸ '))
        app.player['state'] = 'stop'
        self.assertEqual(app.footer_status(80, app._message_at + 4), '■ Stopped')

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
        cover.assert_called_once_with(unittest.mock.ANY, '/cover', 24, 12, 2, 3)
        drc_row = next(args[1] for args in painted if str(args[3]).startswith('DRC OFF'))
        self.assertEqual(drc_row, 15)
        self.assertLess(max(args[1] for args in painted if 'Album details' in str(args[3])), drc_row)

    def test_now_time_ticks_each_second_from_the_player_response(self):
        app = tui.App('http://localhost:9090')
        app.last_player = 98  # request began before the player response arrived
        with patch.object(tui.time, 'monotonic', return_value=100):
            app.update_player({'elapsed': 10, 'duration': 60, 'state': 'play'})
        painted = []
        class Screen:
            def getmaxyx(self): return (24, 80)
        with patch.object(app, 'colored'), patch.object(app, 'text', side_effect=lambda *args: painted.append(args)):
            for now, expected in ((100.4, '0:10 / 1:00'), (101.4, '0:11 / 1:00'),
                                  (102.4, '0:12 / 1:00')):
                with patch.object(tui.time, 'monotonic', return_value=now):
                    app.draw_now(Screen(), 17)
                self.assertEqual(next(args[3] for args in painted if args[1] == 6), expected)
                painted.clear()
            app.config.update(levels=False, balance=False, dr=False)
            app.last_player = 100
            app.last_measure = 103.4
            with patch.object(app, 'submit'), patch.object(tui.time, 'monotonic', return_value=103.4):
                app.poll()  # a new request starts, but the old sample must keep ticking
            with patch.object(tui.time, 'monotonic', return_value=104.4):
                app.draw_now(Screen(), 17)
            self.assertEqual(next(args[3] for args in painted if args[1] == 6), '0:14 / 1:00')

    def test_seek_bar_sits_below_play_without_label(self):
        app = tui.App('http://localhost:9090')
        app.player = {'elapsed': 30, 'duration': 60, 'state': 'pause'}
        painted = []
        class Screen:
            def getmaxyx(self): return (24, 80)
        with patch.object(app, 'colored', side_effect=lambda *args: painted.append(args)), \
             patch.object(app, 'text'):
            app.draw_now(Screen(), 17)
        seek = [args for args in painted if args[1] == tui.NOW_MENU_TOP + 1]
        self.assertFalse(any('Seek' in str(args[3]) for args in seek))
        self.assertTrue(any('━' in str(args[3]) and args[4] == 'fmt_48' for args in seek))
        self.assertTrue(any('─' in str(args[3]) and args[4] == 'muted' for args in seek))
        self.assertTrue(any('← →' in str(args[3]) for args in seek))

    def test_list_sixel_repaints_curses_after_cover_change_and_resize(self):
        app = tui.App('http://localhost:9090')
        app.page = 'Library'
        app.config['cover_mode'] = 'sixel'
        class Screen:
            def __init__(self): self.forced = 0
            def erase(self): pass
            def refresh(self): pass
            def insstr(self, *args): pass
            def clearok(self, enabled): self.forced += enabled
            def getmaxyx(self): return (24, 80)
        screen = Screen()
        images = [(20, 8, b'first')]
        def draw_library(*_): app.list_sixel_targets.extend(images)
        with patch.object(app, 'colored'), patch.object(app, 'draw_library', side_effect=draw_library), \
             patch.object(app, 'draw_live'), patch.object(app, 'draw_nav'), patch.object(tui.os, 'write') as write:
            app.draw(screen)
            images.append((20, 13, b'second'))
            app.draw(screen)
            self.assertEqual(screen.forced, 1)
            self.assertTrue(any(call.args[1] == b'\x1b[H\x1b[2J' for call in write.call_args_list))
            app.action(tui.curses.KEY_RESIZE, screen)
            app.draw(screen)
            self.assertEqual(screen.forced, 2)

    def test_rows_end_with_distinct_blanks_so_curses_never_shifts_them(self):
        app = tui.App('http://localhost:9090')
        app.page = 'Library'
        marks = {}
        class Screen:
            def erase(self): pass
            def refresh(self): marks['refreshed'] = dict(marks)
            def insstr(self, y, x, value): marks[y] = (x, value)
            def getmaxyx(self): return (40, 120)
            def clearok(self, enabled): pass
        with patch.object(app, 'colored'), patch.object(app, 'draw_library'), \
             patch.object(app, 'draw_live'), patch.object(app, 'draw_nav'), patch.object(tui.os, 'write'):
            app.draw(Screen())
        rows = marks.pop('refreshed')
        self.assertEqual(sorted(rows), list(range(40)))
        self.assertTrue(all(x == 119 and not value.strip() for x, value in rows.values()))
        # No row repeats a row up to 12 lines away (list slots are 4-8 rows).
        self.assertTrue(all(rows[y][1] != rows[y + d][1] for d in range(1, 13) for y in range(40 - d)))

    def test_list_sixel_repaints_cover_when_selection_moves(self):
        app = tui.App('http://localhost:9090')
        app.page = 'Library'
        app.tab = 1
        class Screen:
            def erase(self): pass
            def refresh(self): pass
            def insstr(self, *args): pass
            def getmaxyx(self): return (24, 80)
            def clearok(self, enabled): raise AssertionError('selection must not clear the screen')
        screen = Screen()
        with patch.object(app, 'colored'), \
             patch.object(app, 'draw_library', side_effect=lambda *_: app.list_sixel_targets.append((31, 7, b'cover'))), \
             patch.object(app, 'draw_live'), patch.object(app, 'draw_nav'), \
             patch.object(tui.os, 'write') as write:
            app.draw(screen)
            write.reset_mock()
            app.row = 1
            app.draw(screen)
        self.assertEqual(write.call_count, 1)
        self.assertIn(b'cover', write.call_args.args[1])

    def test_switch_from_discover_clears_sixel_before_favourites_redraw(self):
        app = tui.App('http://localhost:9090')
        app.page = 'Library'
        app.tab = 1
        app.list_sixel_displayed = ((29, 6, b'old cover'),)
        app.query = 'Mahler'
        class Screen:
            def __init__(self): self.events = []
            def clear(self): self.events.append('clear')
            def clearok(self, enabled): self.events.append('clearok')
            def erase(self): self.events.append('erase')
            def refresh(self): self.events.append('refresh')
            def insstr(self, *args): pass
            def getmaxyx(self): return (24, 80)
        screen = Screen()
        with patch.object(app, 'fetch_list'), patch.object(app, 'colored'), \
             patch.object(app, 'draw_library', side_effect=lambda *_: screen.events.append('library')), \
             patch.object(app, 'draw_live'), patch.object(app, 'draw_nav'), \
             patch.object(tui.os, 'write') as write:
            app.select_library_tab(2, immediate=True)
            self.assertEqual(app.query, '')
            app.draw(screen)
        self.assertEqual(screen.events[:4], ['clear', 'clearok', 'erase', 'library'])
        self.assertEqual(write.call_args.args[1], b'\x1b[H\x1b[2J')
        self.assertFalse(app.list_sixel_clear_pending)

    def test_sixel_is_trimmed_to_whole_bands_inside_its_rows(self):
        bands = b"-".join(b"#0~~" for _ in range(14))
        payload = b'\x1bP0;1;0q"1;1;80;80' + bands + b"\x1b\\"
        trimmed = tui.fit_sixel_rows(payload, 4, cell_height=20)
        self.assertTrue(trimmed.startswith(b'\x1bP0;1;0q"1;1;80;78#0~~'))
        self.assertEqual(trimmed.count(b"-"), 12)
        self.assertTrue(trimmed.endswith(b"#0~~\x1b\\"))
        self.assertEqual(tui.fit_sixel_rows(payload, 5, cell_height=20), payload)
        self.assertEqual(tui.fit_sixel_rows(b"not sixel", 4, cell_height=20), b"not sixel")

    def test_discover_to_search_repaints_blank_cells_under_old_covers(self):
        app = tui.App('http://localhost:9090')
        app.page = 'Library'
        app.tab = 1
        app.config['cover_mode'] = 'sixel'
        app.list_sixel_displayed = ((5, 8, b'old cover'),)
        app.list_sixel_displayed_areas = ((5, 8, 3, 2),)
        class Screen:
            def clear(self): pass
            def clearok(self, enabled): pass
            def erase(self): pass
            def refresh(self): pass
            def insstr(self, *args): pass
            def getmaxyx(self): return (24, 80)
            def inch(self, y, x): return ord('x') if (y, x) == (8, 6) else 32
        writes = []
        with patch.object(app, 'colored'), patch.object(app, 'draw_library'), \
             patch.object(app, 'draw_live'), patch.object(app, 'draw_nav'), \
             patch.object(tui.os, 'write', side_effect=lambda fd, data: writes.append(data)):
            app.select_library_tab(0, immediate=True)
            app.draw(Screen())
        repaint = writes[-1]
        self.assertIn(b'\x1b[0;38;5;17m', repaint)
        for y, x in ((8, 5), (8, 7), (9, 5), (9, 6), (9, 7)):
            self.assertIn(f'\x1b[{y + 1};{x + 1}H '.encode(), repaint)
        self.assertNotIn(b'\x1b[9;7H ', repaint)  # keep text curses drew
        self.assertEqual(app.list_sixel_displayed_areas, ())

    def test_discover_to_search_clears_sixel_even_without_tracked_cover(self):
        app = tui.App('http://localhost:9090')
        app.page = 'Library'
        app.tab = 1
        app.config['cover_mode'] = 'sixel'
        events = []
        class Screen:
            def clear(self): events.append('clear')
            def clearok(self, enabled): events.append('clearok')
            def erase(self): events.append('erase')
            def refresh(self): events.append('refresh')
            def insstr(self, *args): pass
            def getmaxyx(self): return (24, 80)
        with patch.object(app, 'colored'), patch.object(app, 'draw_library', side_effect=lambda *_: events.append('search')), \
             patch.object(app, 'draw_live'), patch.object(app, 'draw_nav'), \
             patch.object(tui.os, 'write') as write:
            app.select_library_tab(0, immediate=True)
            app.draw(Screen())
        self.assertEqual(events[:4], ['clear', 'clearok', 'erase', 'search'])
        self.assertEqual(write.call_args.args[1], b'\x1b[H\x1b[2J')

    def test_favourites_query_is_separate_and_persisted(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(tui, 'CONFIG_PATH', Path(directory) / 'config.json'):
            app = tui.App('http://localhost:9090')
            app.query = 'Mahler'
            with patch.object(app, 'fetch_list'), patch.object(app, 'prompt', return_value='Bach'):
                app.select_library_tab(2, immediate=True)
                self.assertEqual(app.query, '')
                app.library_key(ord('/'), None)
                self.assertEqual(app.query, 'Bach')
                app.select_library_tab(0, immediate=True)
                self.assertEqual(app.query, 'Mahler')
                app.select_library_tab(2, immediate=True)
                self.assertEqual(app.query, 'Bach')
            self.assertEqual(tui.App('http://localhost:9090').queries[2], 'Bach')

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
            def insstr(self, *args): pass
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
        self.assertEqual(tui.NOW_ACTION_ROWS[4], tui.NOW_ACTION_ROWS[0] + 1)

    def test_config_arrows_select_and_enter_changes_value(self):
        app = tui.App('http://localhost:9090')
        app.page = 'Config'
        app.config_index = 2
        original = app.config['cover']
        app.action(tui.curses.KEY_DOWN, None)
        self.assertEqual(app.config_index, 3)
        app.action(tui.curses.KEY_UP, None)
        with patch.object(app, "save"):
            app.action(10, None)
        self.assertEqual(app.config['cover'], not original)

    def test_config_options_use_aligned_columns(self):
        app = tui.App('http://localhost:9090')
        painted = []
        class Screen:
            def getmaxyx(self): return (24, 80)
        with patch.object(app, 'colored', side_effect=lambda *args: painted.append(args)):
            app.draw_config(Screen(), 17)
        self.assertEqual([args[2] for args in painted if args[1] == 3], [2, 23])

    def test_library_sidebar_reaches_filters_and_search_prompt(self):
        app = tui.App('http://localhost:9090')
        app.page = 'Library'
        app.library_key(10, None)
        self.assertTrue(app.search_editing)
        self.assertFalse(app.library_sidebar_focus)
        with patch.object(app, "fetch_list"), patch.object(tui.curses, "curs_set"):
            for key in map(ord, "Bach"): app.action(key, None)
            app.action(10, None)
        self.assertEqual(app.query, "Bach")
        app.library_key(tui.curses.KEY_LEFT, None)
        with patch.object(app, "fetch_list"):
            for _ in range(len(tui.TABS)):
                app.library_key(tui.curses.KEY_DOWN, None)
        self.assertEqual(app.library_sidebar_index, len(tui.TABS) - 1)
        self.assertTrue(app.library_sidebar_focus)

    def test_search_tab_selection_waits_for_explicit_edit(self):
        app = tui.App('http://localhost:9090')
        app.page = 'Library'
        app.tab = app.library_sidebar_index = 1
        app.config['library_delay'] = 1
        with patch.object(app, 'fetch_list') as fetch, patch.object(tui.time, 'monotonic', return_value=100):
            app.library_key(tui.curses.KEY_UP, None)
        self.assertEqual(app.tab, 0)
        self.assertIsNone(app.library_auto_due)
        self.assertFalse(app.search_editing)
        self.assertTrue(app.library_sidebar_focus)
        fetch.assert_not_called()
        with patch.object(app, 'submit'), patch.object(app, 'fetch_list') as fetch, \
             patch.object(tui.time, 'monotonic', return_value=101):
            app.poll()
        self.assertFalse(app.search_editing)
        fetch.assert_not_called()
        app.library_key(10, None)
        self.assertTrue(app.search_editing)
        self.assertFalse(app.library_sidebar_focus)
        painted = []
        class Screen:
            def getmaxyx(self): return (20, 55)
        with patch.object(app, 'colored', side_effect=lambda *args: painted.append(args)), patch.object(tui.time, 'monotonic', return_value=101):
            app.draw_library(Screen(), 13)
        self.assertTrue(any(args[1] == 3 and str(args[3]).startswith('┌') for args in painted))
        self.assertTrue(any(args[1] == 4 and '⌕ ' in str(args[3]) for args in painted))
        self.assertTrue(any(args[1] == 5 and str(args[3]).startswith('└') for args in painted))
        self.assertTrue(any(args[1] == 4 and '▌' in str(args[3]) for args in painted))
        painted.clear()
        with patch.object(app, 'colored', side_effect=lambda *args: painted.append(args)), patch.object(tui.time, 'monotonic', return_value=101.5):
            app.draw_library(Screen(), 13)
        self.assertFalse(any(args[1] == 4 and '▌' in str(args[3]) for args in painted))
        with patch.object(app, 'fetch_list') as fetch, patch.object(tui.curses, 'curs_set'):
            for key in 'Béla'.encode(): app.action(key, None)
            app.action(10, None)
        self.assertEqual(app.query, 'Béla')
        self.assertFalse(app.search_editing)
        fetch.assert_called_once()

    def test_search_tab_click_and_library_return_do_not_focus_input(self):
        app = tui.App('http://localhost:9090')
        app.tab = app.library_sidebar_index = 1
        app.library_sidebar_focus = False
        with patch.object(app, 'fetch_list') as fetch:
            app.select_library_tab(0, immediate=True)
            app.switch('Library')
        self.assertEqual(app.tab, 0)
        self.assertTrue(app.library_sidebar_focus)
        self.assertFalse(app.search_editing)
        self.assertIsNone(app.library_auto_due)
        fetch.assert_not_called()

    def test_search_arrows_move_cursor_and_edit_at_its_position(self):
        app = tui.App('http://localhost:9090')
        app.page = 'Library'
        app.query = 'Mahler'
        app.begin_search_edit()
        for _ in range(4): app.action(tui.curses.KEY_LEFT, None)
        self.assertEqual(app.search_cursor, 2)
        painted = []
        class Screen:
            def getmaxyx(self): return (24, 80)
        with patch.object(app, 'colored', side_effect=lambda *args: painted.append(args)), \
             patch.object(tui.time, 'monotonic', return_value=100):
            app.draw_library(Screen(), 17)
        self.assertTrue(any(args[1] == 4 and '⌕ Ma▌hler' in args[3] for args in painted))
        with patch.object(tui.time, 'monotonic', return_value=101):
            app.action(ord('X'), None)
        self.assertEqual((app.search_draft, app.search_cursor), ('MaXhler', 3))
        self.assertEqual(app.search_auto_due, 103)
        app.action(tui.curses.KEY_RIGHT, None)
        self.assertEqual(app.search_cursor, 4)
        app.action(tui.curses.KEY_BACKSPACE, None)
        self.assertEqual((app.search_draft, app.search_cursor), ('MaXler', 3))
        app.action(tui.curses.KEY_DC, None)
        self.assertEqual((app.search_draft, app.search_cursor), ('MaXer', 3))

    def test_search_left_past_start_highlights_sidebar_immediately(self):
        app = tui.App('http://localhost:9090')
        app.page = 'Library'
        app.query = 'Mahler'
        app.begin_search_edit()
        app.action(ord('X'), None)
        screen = object()
        with patch.object(app, 'draw') as draw, patch.object(app, 'fetch_list') as fetch, \
             patch.object(tui.curses, 'curs_set'):
            for _ in range(len(app.search_draft)):
                app.action(tui.curses.KEY_LEFT, screen)
            self.assertTrue(app.search_editing)
            self.assertEqual(app.search_cursor, 0)
            draw.assert_not_called()
            app.action(tui.curses.KEY_LEFT, screen)
        draw.assert_called_once_with(screen)
        fetch.assert_not_called()
        self.assertFalse(app.search_editing)
        self.assertTrue(app.library_sidebar_focus)
        self.assertEqual(app.library_sidebar_index, 0)
        self.assertEqual(app.search_draft, 'Mahler')
        self.assertIsNone(app.search_auto_due)

    def test_search_cursor_stays_visible_in_long_input(self):
        app = tui.App('http://localhost:9090')
        app.query = 'abcdefghijklmnopqrstuvwxyz1234567890'
        app.begin_search_edit()
        painted = []
        class Screen:
            def getmaxyx(self): return (20, 55)
        with patch.object(app, 'colored', side_effect=lambda *args: painted.append(args)), \
             patch.object(tui.time, 'monotonic', return_value=100):
            app.draw_library(Screen(), 13)
        row = next(args[3] for args in painted if args[1] == 4 and args[2] == 29)
        self.assertIn('▌', row)
        self.assertEqual(len(row), 24)
        for _ in range(len(app.search_draft)): app.search_edit_key(tui.curses.KEY_LEFT)
        self.assertEqual(app.search_cursor, 0)
        painted.clear()
        with patch.object(app, 'colored', side_effect=lambda *args: painted.append(args)), \
             patch.object(tui.time, 'monotonic', return_value=100):
            app.draw_library(Screen(), 13)
        row = next(args[3] for args in painted if args[1] == 4 and args[2] == 29)
        self.assertTrue(row.startswith('│⌕ ▌abc'))

    def test_search_auto_starts_after_two_idle_seconds_and_resets_on_typing(self):
        app = tui.App('http://localhost:9090')
        app.page = 'Library'
        app.begin_search_edit()
        painted = []
        class Screen:
            def getmaxyx(self): return (24, 80)
        with patch.object(tui.time, 'monotonic', return_value=100):
            for char in 'Ma': app.search_edit_key(ord(char))
        self.assertIsNone(app.search_auto_due)
        with patch.object(tui.time, 'monotonic', return_value=101):
            app.search_edit_key(ord('h'))
        self.assertEqual(app.search_auto_due, 103)
        with patch.object(app, 'colored', side_effect=lambda *args: painted.append(args)), \
             patch.object(tui.time, 'monotonic', return_value=101.1):
            app.draw_library(Screen(), 17)
        self.assertTrue(any(args[1] == 2 and args[3] == 'starting search in 2s...' and
                            args[2] == 29 + 49 - len(args[3]) for args in painted))
        with patch.object(tui.time, 'monotonic', return_value=102):
            app.search_edit_key(ord('l'))
        self.assertEqual(app.search_auto_due, 104)
        painted.clear()
        with patch.object(app, 'colored', side_effect=lambda *args: painted.append(args)), \
             patch.object(tui.time, 'monotonic', return_value=103.1):
            app.draw_library(Screen(), 17)
        self.assertTrue(any(args[1] == 2 and args[3] == 'starting search in 1s...' for args in painted))
        with patch.object(app, 'fetch_list') as fetch, patch.object(app, 'submit'), \
             patch.object(tui.time, 'monotonic', return_value=103.9):
            app.poll()
        fetch.assert_not_called()
        with patch.object(app, 'fetch_list') as fetch, patch.object(app, 'submit'), \
             patch.object(tui.time, 'monotonic', return_value=104):
            app.poll()
        fetch.assert_called_once()
        self.assertEqual(app.query, 'Mahl')
        self.assertTrue(app.search_editing)
        self.assertIsNone(app.search_auto_due)
        with patch.object(tui.time, 'monotonic', return_value=105):
            app.search_edit_key(ord('e'))
        self.assertEqual(app.search_auto_due, 107)

    def test_search_auto_cancelled_by_shortening_escape_or_enter(self):
        app = tui.App('http://localhost:9090')
        app.page = 'Library'
        app.begin_search_edit()
        with patch.object(tui.time, 'monotonic', return_value=100):
            for char in 'Mah': app.search_edit_key(ord(char))
        self.assertEqual(app.search_auto_due, 102)
        app.search_edit_key(127)
        self.assertIsNone(app.search_auto_due)
        with patch.object(tui.time, 'monotonic', return_value=101):
            app.search_edit_key(ord('h'))
        self.assertEqual(app.search_auto_due, 103)
        with patch.object(tui.curses, 'curs_set'):
            app.search_edit_key(27)
        self.assertIsNone(app.search_auto_due)
        with patch.object(app, 'fetch_list') as fetch, patch.object(tui.curses, 'curs_set'):
            app.begin_search_edit()
            for char in 'Bach': app.search_edit_key(ord(char))
            app.search_edit_key(10)
        fetch.assert_called_once()
        self.assertEqual(app.query, 'Bach')
        self.assertIsNone(app.search_auto_due)
        self.assertFalse(app.search_editing)

    def test_search_inline_escape_and_tab_keep_previous_query(self):
        app = tui.App('http://localhost:9090')
        app.page = 'Library'
        app.query = 'Old'
        with patch.object(tui.curses, 'curs_set'):
            app.begin_search_edit()
            app.action(ord('x'), None)
            app.action(27, None)
            self.assertEqual(app.query, 'Old')
            app.begin_search_edit()
            app.action(9, None)
        self.assertFalse(app.search_editing)
        self.assertEqual(app.library_right_index, 1)

    def test_search_arrow_navigation_moves_between_input_filters_and_results(self):
        for entry in (9, tui.curses.KEY_RIGHT):
            app = tui.App('http://localhost:9090')
            app.page = 'Library'
            app.rows = [{'title': 'One'}, {'title': 'Two'}]
            app.action(entry, None)
            self.assertTrue(app.search_editing)
            self.assertFalse(app.library_sidebar_focus)
            self.assertEqual(app.library_right_index, 0)
            app.action(ord('A'), None)
            with patch.object(app, 'fetch_list') as initial_fetch:
                app.action(tui.curses.KEY_DOWN, None)
            initial_fetch.assert_called_once()
            self.assertFalse(app.search_editing)
            self.assertEqual(app.library_right_index, 1)
            self.assertEqual(app.search_draft, 'A')
            self.assertEqual(app.query, 'A')
            app.action(tui.curses.KEY_DOWN, None)
            self.assertEqual(app.library_right_index, 2)
            app.action(tui.curses.KEY_DOWN, None)
            self.assertEqual(app.row, 1)
            app.action(tui.curses.KEY_UP, None)
            self.assertEqual(app.row, 0)
            app.action(tui.curses.KEY_UP, None)
            self.assertEqual(app.library_right_index, 1)
            app.action(tui.curses.KEY_UP, None)
            self.assertEqual(app.library_right_index, 0)
            self.assertTrue(app.search_editing)
            self.assertEqual(app.search_draft, 'A')
            with patch.object(app, 'fetch_list') as fetch:
                app.action(10, None)
            self.assertEqual(app.query, 'A')
            fetch.assert_called_once()

    def test_discover_autoload_delay_and_enter_override(self):
        app = tui.App('http://localhost:9090')
        app.page = 'Library'
        app.config['library_delay'] = 1
        with patch.object(app, 'fetch_list') as fetch, patch.object(tui.time, 'monotonic', return_value=100):
            app.library_key(tui.curses.KEY_DOWN, None)
        self.assertEqual(app.tab, 1)
        self.assertEqual(app.library_auto_due, 101)
        fetch.assert_not_called()
        with patch.object(app, 'fetch_list') as fetch, patch.object(app, 'submit'), patch.object(tui.time, 'monotonic', return_value=100.9):
            app.poll()
            fetch.assert_not_called()
        with patch.object(app, 'fetch_list') as fetch, patch.object(app, 'submit'), patch.object(tui.time, 'monotonic', return_value=101):
            app.poll()
            fetch.assert_called_once()
        with patch.object(app, 'fetch_list') as fetch:
            app.library_key(10, None)
            fetch.assert_called_once()

    def test_discover_result_box_follows_tab_focus(self):
        app = tui.App('http://localhost:9090')
        app.tab = 1
        app.rows = [{'kind': 'folder', 'path': 'Example'}]
        painted = []
        class Screen:
            def getmaxyx(self): return (20, 55)
        with patch.object(app, 'colored', side_effect=lambda *args: painted.append(args)):
            app.draw_library(Screen(), 13)
        folder = next(args for args in painted if args[1] == 7 and 'Example' in str(args[3]))
        self.assertFalse(folder[5] & tui.curses.A_REVERSE)
        self.assertFalse(any(args[1] == 6 and str(args[3]).startswith('┌') for args in painted))
        app.library_sidebar_focus = False
        app.library_right_index = 2
        painted.clear()
        with patch.object(app, 'colored', side_effect=lambda *args: painted.append(args)):
            app.draw_library(Screen(), 13)
        folder = next(args for args in painted if args[1] == 7 and 'Example' in str(args[3]))
        self.assertFalse(folder[5] & tui.curses.A_REVERSE)
        self.assertTrue(any(args[1] == 6 and str(args[3]).startswith('┌') and args[4] == 'green'
                            for args in painted))
        self.assertTrue(any(args[1] == 11 and str(args[3]).startswith('└') and args[4] == 'green'
                            for args in painted))

    def test_favourites_selection_replaces_discover_timer(self):
        app = tui.App('http://localhost:9090')
        app.page = 'Library'
        with patch.object(app, 'fetch_list') as fetch, patch.object(tui.time, 'monotonic', side_effect=[100, 100.4]):
            app.library_key(tui.curses.KEY_DOWN, None)
            app.library_key(tui.curses.KEY_DOWN, None)
        self.assertEqual(app.tab, 2)
        self.assertEqual(app.library_auto_due, 101.4)
        with patch.object(app, 'fetch_list') as fetch, patch.object(app, 'submit'), patch.object(tui.time, 'monotonic', return_value=101):
            app.poll()
            fetch.assert_not_called()
        with patch.object(app, 'fetch_list') as fetch, patch.object(app, 'submit'), patch.object(tui.time, 'monotonic', return_value=101.4):
            app.poll()
            fetch.assert_called_once()

    def test_tab_focuses_genre_then_filters_then_results(self):
        app = tui.App('http://localhost:9090')
        app.page = 'Library'
        app.tab = app.library_sidebar_index = 1
        app.library_key(9, None)
        self.assertEqual((app.library_sidebar_focus, app.library_right_index), (False, 0))
        app.library_key(9, None)
        self.assertEqual(app.library_right_index, 1)
        app.library_key(9, None)
        self.assertEqual(app.library_right_index, 2)
        painted = []
        class Screen:
            def getmaxyx(self): return (20, 55)
        with patch.object(app, 'colored', side_effect=lambda *args: painted.append(args)):
            app.draw_library(Screen(), 13)
        self.assertEqual(sum('Genre:' in str(args[3]) for args in painted), 1)
        self.assertTrue(any(args[1] == 4 and 'Filters:' in str(args[3]) for args in painted))

    def test_config_cycles_library_delay(self):
        app = tui.App('http://localhost:9090')
        app.page = 'Config'
        app.config_index = 8
        with patch.object(app, 'save'):
            app.action(10, None)
        self.assertEqual(app.config['library_delay'], 2)

    def test_library_sidebar_keeps_all_tabs_visible_at_minimum_size(self):
        app = tui.App('http://localhost:9090')
        app.rows = [{'title': 'Example', 'artist': 'Artist'}]
        painted = []
        class Screen:
            def getmaxyx(self): return (20, 55)
        with patch.object(app, 'colored', side_effect=lambda *args: painted.append(args)):
            app.draw_library(Screen(), 13)
        tabs = [args for args in painted if args[2] == 1 and 2 <= args[1] <= 7]
        self.assertEqual(len(tabs), 6)
        self.assertTrue(any(args[1] == 10 and args[2] == 31 and 'Example' in args[3] for args in painted))

    def test_filter_summaries_count_labels_in_each_context(self):
        app = tui.App('http://localhost:9090')
        painted = []
        class Screen:
            def getmaxyx(self): return (24, 100)
        for tab, label_key, filter_y in ((0, 'label', 6), (1, 'discover_label', 4)):
            app.tab = tab
            for count in (1, 2, 3):
                app.filters[label_key] = [f'Label {i}' for i in range(count)]
                painted.clear()
                with patch.object(app, 'colored', side_effect=lambda *args: painted.append(args)):
                    app.draw_library(Screen(), 17)
                expected = f'{count} {"label" if count == 1 else "labels"}'
                self.assertTrue(any(args[1] == filter_y and expected in args[3] for args in painted))
            app.filters[label_key] = []

    def test_search_results_drop_searched_artist_and_show_quality(self):
        self.assertTrue(tui.same_name('Pink Floyd', 'pink floyd'))
        self.assertTrue(tui.same_name('Pink Floyd', 'Pink  Floyd animals'))
        self.assertFalse(tui.same_name('Pink Floyd', 'pink'))
        self.assertEqual(tui.album_quality({'bits': 24, 'rate': 96}), ('24/96', 'fmt_hires'))
        self.assertEqual(tui.album_quality({'bits': 16, 'rate': 44.1}), ('16/44.1', 'muted'))
        self.assertEqual(tui.album_quality({}), ('', None))
        app = tui.App('http://localhost:9090')
        app.filters['sort'] = 'relevance'
        app.query = 'pink floyd'
        app.rows = [{'title': 'Animals', 'artist': 'Pink Floyd', 'year': 1977, 'label': 'Pink Floyd Records',
                     'bits': 24, 'rate': 192}]
        painted = []
        class Screen:
            def getmaxyx(self): return (24, 120)
        with patch.object(app, 'colored', side_effect=lambda *args: painted.append(args)):
            app.draw_library(Screen(), 17)
        title = [args for args in painted if args[1] == 9 and args[2] >= 31]
        self.assertTrue(any(args[3] == 'Animals' for args in title))
        details = [args for args in painted if args[1] == 10 and args[2] >= 31]
        self.assertTrue(any('1977 · Pink Floyd Records · 24/192' in args[3] for args in details))
        self.assertTrue(any(args[1] == 4 and 'pink floyd' in args[3] for args in painted))
        self.assertTrue(any(args[1] == 6 and 'Sort: Qobuz' in args[3] for args in painted))

    def test_search_result_uses_green_box_without_reversing_text(self):
        app = tui.App('http://localhost:9090')
        app.rows = [{'title': 'Animals', 'artist': 'Pink Floyd'}]
        app.library_sidebar_focus = False
        app.library_right_index = 2
        painted = []
        class Screen:
            def getmaxyx(self): return (24, 80)
        with patch.object(app, 'colored', side_effect=lambda *args: painted.append(args)):
            app.draw_library(Screen(), 17)
        border = [(args[1], args[2], args[3]) for args in painted if args[4] == 'green']
        self.assertEqual(border, [(8, 29, '┌' + '─' * 47 + '┐'),
                                  (9, 29, '│'), (9, 77, '│'),
                                  (10, 29, '│'), (10, 77, '│'),
                                  (11, 29, '│'), (11, 77, '│'),
                                  (12, 29, '└' + '─' * 47 + '┘'),
                                  (10, 78, '›')])
        title = next(args for args in painted if args[3] == 'Animals')
        self.assertEqual(title[4], 'bright')
        self.assertFalse(title[5] & tui.curses.A_REVERSE)

    def test_other_library_results_use_green_box_and_one_line_gap(self):
        class Screen:
            def getmaxyx(self): return (24, 80)
        for tab in (1, 2, 3, 4, 5):
            with self.subTest(tab=tui.TABS[tab]):
                app = tui.App('http://localhost:9090')
                app.tab = tab
                app.rows = [{'title': 'First'}, {'title': 'Second'}]
                app.row = 1
                app.library_sidebar_focus = False
                app.library_right_index = 2 if tab == 1 else 1
                painted = []
                with patch.object(app, 'colored', side_effect=lambda *args: painted.append(args)):
                    app.draw_library(Screen(), 20)
                titles = [(args[1], args[3], args[5]) for args in painted
                          if args[3] in ('First', 'Second')]
                second_y = 12 if tab in (1, 4) else 11
                border_top = second_y - 1
                border_bottom = second_y + (4 if tab in (1, 4) else 3)
                self.assertEqual([(y, title) for y, title, _ in titles],
                                 [(7, 'First'), (second_y, 'Second')])
                self.assertTrue(all(not style & tui.curses.A_REVERSE for _, _, style in titles))
                self.assertTrue(any(args[1] == border_top and str(args[3]).startswith('┌') and args[4] == 'green'
                                    for args in painted))
                self.assertTrue(any(args[1] == border_bottom and str(args[3]).startswith('└') and args[4] == 'green'
                                    for args in painted))
                arrows = [(args[1], args[2]) for args in painted if args[3] == '›' and args[4] == 'green']
                self.assertEqual(arrows, [(second_y + (2 if tab in (1, 4) else 1), 78)])
                self.assertFalse(any(args[1] == border_top and args[2] >= 29 and args[4] != 'green'
                                     for args in painted))

    def test_awarded_result_shows_award_names_and_personal_rating(self):
        app = tui.App('http://localhost:9090')
        app.tab = 4
        app.rows = [{'title': 'Goldberg Variations', 'artist': 'Bach',
                     'awards': [{'name': "Diapason d'Or", 'publication': 'Diapason'},
                                {'name': 'Recording of the Month', 'publication': 'Gramophone'}],
                     'rating': 2}]
        painted = []
        class Screen:
            def getmaxyx(self): return (24, 100)
        with patch.object(app, 'colored', side_effect=lambda *args: painted.append(args)):
            app.draw_library(Screen(), 20)
        self.assertTrue(any(args[1] == 10 and args[3] ==
                            "Awards: Diapason d'Or · Recording of the Month · My rating: 2/3"
                            for args in painted))
        app.rows = [{'title': 'Unawarded favourite', 'rating': 3}]
        painted.clear()
        with patch.object(app, 'colored', side_effect=lambda *args: painted.append(args)):
            app.draw_library(Screen(), 20)
        self.assertTrue(any(args[1] == 10 and args[3] == 'My rating: 3/3' for args in painted))

    def test_awarded_search_hint_and_escape_cancel(self):
        app = tui.App('http://localhost:9090')
        app.page = 'Library'
        app.tab = app.library_sidebar_index = 4
        app.query = 'Gramophone'
        painted = []
        class Screen:
            def getmaxyx(self): return (24, 80)
        with patch.object(app, 'colored', side_effect=lambda *args: painted.append(args)):
            app.draw_library(Screen(), 20)
        self.assertTrue(any(args[1] == 3 and 'Search award, album or artist: Gramophone' in args[3]
                            for args in painted))
        app.query = ''
        painted.clear()
        with patch.object(app, 'colored', side_effect=lambda *args: painted.append(args)):
            app.draw_library(Screen(), 20)
        self.assertTrue(any(args[1] == 3 and 'Search award, album or artist  ·  Enter edit' in args[3]
                            for args in painted))
        app.query = 'Gramophone'
        with patch.object(app, 'fetch_list') as fetch, patch.object(tui.curses, 'curs_set'):
            app.action(ord('/'), None)
            self.assertTrue(app.search_editing)
            for char in ' Bach': app.action(ord(char), None)
            painted.clear()
            with patch.object(app, 'colored', side_effect=lambda *args: painted.append(args)):
                app.draw_library(Screen(), 20)
            self.assertTrue(any(args[1] == 3 and 'Esc cancel' in args[3] for args in painted))
            app.action(27, None)
            fetch.assert_not_called()
        self.assertEqual(app.query, 'Gramophone')
        self.assertFalse(app.search_editing)
        self.assertTrue(app.library_sidebar_focus)
        self.assertEqual(app.library_sidebar_index, 4)
        app.action(ord('/'), None)
        with patch.object(app, 'fetch_list') as fetch, patch.object(tui.curses, 'curs_set'):
            app.action(ord('X'), None)
            app.action(10, None)
        fetch.assert_called_once()
        self.assertEqual(app.query, 'GramophoneX')

    def test_discover_cover_uses_large_image_and_color(self):
        app = tui.App('http://localhost:9090')
        app.tab = 1
        app.config['cover_mode'] = 'symbols'
        app.rows = [{'title': 'Album', 'image': 'https://example.test/small.jpg',
                     'image_large': 'https://example.test/large.jpg'}]
        calls = []
        painted = []
        class Screen:
            def getmaxyx(self): return (24, 80)
        def submit(key, work, accept):
            calls.append((key, work))
            accept([[('▀', 196, 21)]])
        with patch.object(app, 'submit', side_effect=submit), patch.object(app, 'render_cover', return_value=[[('▀', 196, 21)]]) as render, patch.object(app, 'cover_style', return_value=42) as color, patch.object(app, 'text', side_effect=lambda *args: painted.append(args)), patch.object(app, 'colored'):
            app.draw_library(Screen(), 20)
            calls[0][1]()
            app.draw_library(Screen(), 20)
        render.assert_called_once_with('https://example.test/large.jpg', 12, 4, True, 'symbols')
        color.assert_called_with(196, 21)
        self.assertTrue(any(args[3] == '▀' and args[4] == 42 for args in painted))

    def test_failed_list_cover_is_retried_after_delay(self):
        app = tui.App('http://localhost:9090')
        app.config['cover_mode'] = 'sixel'
        with patch.object(app, 'submit') as submit, patch.object(tui.time, 'monotonic', side_effect=[100, 110, 131, 131]):
            app.draw_list_cover(None, 'https://example.test/cover.jpg', 0, 0, 10, 3)
            app.draw_list_cover(None, 'https://example.test/cover.jpg', 0, 0, 10, 3)
            app.draw_list_cover(None, 'https://example.test/cover.jpg', 0, 0, 10, 3)
        self.assertEqual(submit.call_count, 2)

    def test_discover_cover_uses_sixel_in_konsole(self):
        app = tui.App('http://localhost:9090')
        app.config['cover_mode'] = 'sixel'
        app.tab = 1
        app.rows = [{'title': 'Album', 'image_large': 'https://example.test/large.jpg'}]
        jobs = []
        class Screen:
            def getmaxyx(self): return (24, 80)
        with patch.object(app, 'submit', side_effect=lambda key, work, accept: jobs.append((work, accept))), \
             patch.object(app, 'render_cover', return_value=b'\x1bPq...\x1b\\') as render, \
             patch.object(app, 'colored'), patch.object(app, 'text'):
            app.draw_library(Screen(), 20)
            work, accept = jobs[0]
            accept(work())
            app.list_sixel_targets = []
            app.draw_library(Screen(), 20)
        render.assert_called_once_with('https://example.test/large.jpg', 12, 4, True, 'sixel')
        self.assertEqual(app.list_sixel_targets[0][2], b'\x1bPq...\x1b\\')

    def test_search_sort_control_toggles_and_is_remembered(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(tui, 'CONFIG_PATH', Path(directory) / 'config.json'):
            app = tui.App('http://localhost:9090')
            app.page = 'Library'
            app.library_sidebar_focus = False
            app.library_right_index = 1
            app.library_key(9, None)
            self.assertTrue(app.sort_focus)
            with patch.object(app, 'fetch_list') as fetch:
                app.library_key(10, None)
            fetch.assert_called_once()
            self.assertEqual(app.filters['sort'], 'date')
            self.assertEqual(tui.App('http://localhost:9090').filters['sort'], 'date')
            painted = []
            class Screen:
                def getmaxyx(self): return (24, 80)
            with patch.object(app, 'colored', side_effect=lambda *args: painted.append(args)):
                app.draw_library(Screen(), 20)
            sort = next(args for args in painted if args[1] == 6 and args[3] == 'Sort: Time')
            self.assertEqual(sort[5], tui.curses.A_REVERSE)
            app.library_key(tui.curses.KEY_LEFT, None)
            self.assertFalse(app.sort_focus)
            self.assertFalse(app.library_sidebar_focus)
            app.library_key(tui.curses.KEY_RIGHT, None)
            self.assertTrue(app.sort_focus)

    def test_sort_shortcut_is_remembered(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(tui, 'CONFIG_PATH', Path(tmp) / 'config.json'):
            app = tui.App('http://localhost:9090')
            app.page = 'Library'; app.library_sidebar_focus = False
            with patch.object(app, 'fetch_list'):
                app.library_key(ord('s'), None)
            self.assertEqual(app.filters['sort'], 'date')
            self.assertEqual(tui.App('http://localhost:9090').filters['sort'], 'date')

    def test_page_shortcuts_and_visibility_hint(self):
        app = tui.App('http://localhost:9090')
        app.config.update(levels=True, balance=True, dr=True)
        painted = []
        class Screen:
            def getmaxyx(self): return (24, 80)
        with patch.object(app, 'colored', side_effect=lambda *args: painted.append(args)):
            app.draw_nav(Screen(), 17)
        self.assertEqual([args[1] for args in painted], [16, 16, 16, 16, 21])
        self.assertEqual(painted[-1][3], "☷ Shift+Tab")
        app.page = 'Library'
        painted.clear()
        with patch.object(app, 'colored', side_effect=lambda *args: painted.append(args)):
            app.draw_nav(Screen(), 17)
        self.assertEqual(len(painted), 4)
        app.page = 'Now'
        for settings, expected_row in (
            ({"levels": True, "balance": True, "dr": False}, 20),
            ({"levels": True, "balance": False, "dr": False}, 19),
            ({"levels": False, "balance": True, "dr": False}, 18),
            ({"levels": False, "balance": False, "dr": True}, 18),
        ):
            app.config.update(settings)
            painted.clear()
            with patch.object(app, 'colored', side_effect=lambda *args: painted.append(args)):
                app.draw_nav(Screen(), 17)
            self.assertEqual(painted[-1][1], expected_row)
        app.config.update(levels=False, dr=False, balance=False)
        painted.clear()
        with patch.object(app, 'colored', side_effect=lambda *args: painted.append(args)):
            app.draw_nav(Screen(), 21)
        self.assertEqual([args[1] for args in painted], [20] * 4 + [21])
        self.assertEqual(painted[-1][2], 80 - len("☷ Shift+Tab") - 2)
        painted.clear()
        with patch.object(app, 'colored', side_effect=lambda *args: painted.append(args)):
            app.draw_live(Screen(), 21)
        self.assertFalse(any("─" in str(args[3]) for args in painted))
        app.action(tui.curses.KEY_F3, None)
        self.assertEqual(app.page, 'DRC')

    def test_page_up_down_and_ctrl_arrows_navigate_four_pages(self):
        app = tui.App('http://localhost:9090')
        app.config['dr'] = False
        with patch.object(app, 'submit'):
            app.action(tui.curses.KEY_NPAGE, None)
            self.assertEqual(app.page, 'DRC')
            app.action(tui.CTRL_RIGHT, None)
            self.assertEqual(app.page, 'Config')
            app.action(tui.curses.KEY_NPAGE, None)
            self.assertEqual(app.page, 'Config')
            app.action(tui.CTRL_LEFT, None)
            self.assertEqual(app.page, 'DRC')
            app.action(tui.curses.KEY_PPAGE, None)
            self.assertEqual(app.page, 'Now')
            app.action(tui.curses.KEY_PPAGE, None)
            self.assertEqual(app.page, 'Library')
            app.action(tui.CTRL_LEFT, None)
            self.assertEqual(app.page, 'Library')

    def test_ctrl_arrow_escape_sequences_are_decoded(self):
        app = tui.App('http://localhost:9090')
        class Screen:
            def __init__(self, keys):
                self.keys = iter(keys)
                self.timeouts = []
            def getch(self): return next(self.keys)
            def timeout(self, value): self.timeouts.append(value)
        for suffix, expected in (('D', tui.CTRL_LEFT), ('C', tui.CTRL_RIGHT)):
            screen = Screen([27, *map(ord, '[1;5' + suffix)])
            self.assertEqual(app.read_key(screen), expected)
            self.assertEqual(screen.timeouts, [25, 100])
        screen = Screen([27, -1])
        self.assertEqual(app.read_key(screen), 27)

    def test_focus_reports_toggle_dimming_without_reaching_actions(self):
        app = tui.App('http://localhost:9090')
        class Screen:
            def __init__(self, keys):
                self.keys = iter(keys)
                self.timeouts = []
                self.styles = []
            def getch(self): return next(self.keys)
            def timeout(self, value): self.timeouts.append(value)
            def getmaxyx(self): return (24, 80)
            def addnstr(self, y, x, value, count, style): self.styles.append(style)
        screen = Screen([27, ord('['), ord('O')])
        self.assertEqual(app.read_key(screen, restore_timeout=-1), -1)
        self.assertFalse(app.focused)
        self.assertEqual(screen.timeouts, [25, -1])
        app.styles['muted'] = 3 << 8
        app.text(screen, 1, 1, 'dim', tui.curses.A_BOLD | tui.curses.A_REVERSE | (1 << 8))
        self.assertEqual(screen.styles[-1], tui.curses.A_REVERSE | tui.curses.A_DIM | (3 << 8))
        app.text(screen, 1, 1, 'cover', 20 << 8)
        self.assertEqual(screen.styles[-1], 20 << 8)
        self.assertEqual(app.read_key(Screen([27, ord('['), ord('I')])), -1)
        self.assertTrue(app.focused)
        app.text(screen, 1, 1, 'bright', tui.curses.A_BOLD)
        self.assertEqual(screen.styles[-1], tui.curses.A_BOLD)

    def test_unfocused_covers_turn_dim_greyscale(self):
        payload = b'\x1bP0;1;0q"1;1;8;6#0;2;100;0;0#1;2;0;0;100#0~~$#1~~\x1b\\'
        grey = tui.grey_sixel(payload)
        self.assertIn(b'#0;2;18;18;18', grey)  # red: 0.299 * 100 * 0.6
        self.assertIn(b'#1;2;7;7;7', grey)
        self.assertTrue(grey.endswith(b'#0~~$#1~~\x1b\\'))
        self.assertEqual(tui.grey_xterm(None), None)
        self.assertEqual(tui.grey_xterm(231), 232 + 14)  # white -> 60 % grey
        self.assertEqual(tui.grey_xterm(16), 232)

    def test_now_cover_is_redrawn_grey_on_focus_loss_without_clearing(self):
        app = tui.App('http://localhost:9090')
        app.page = 'Now'
        app.sixel_target = (2, 3)
        app.cover_sixel = b'\x1bPq#0;2;100;100;100#0~\x1b\\'
        app.sixel_visible = True
        writes = []
        class Screen:
            def clear(self): raise AssertionError('focus change must not clear')
            def erase(self): pass
            def refresh(self): pass
            def insstr(self, *args): pass
            def getmaxyx(self): return (24, 80)
        with patch.object(app, 'current_art', return_value='art'), patch.object(app, 'draw_now'), \
             patch.object(app, 'colored'), patch.object(app, 'draw_live'), patch.object(app, 'draw_nav'), \
             patch.object(tui.os, 'write', side_effect=lambda fd, data: writes.append(data)):
            app.cover_key = ('art',)
            app.draw(Screen())
            self.assertEqual(writes, [])
            app.focused = False
            app.draw(Screen())
            self.assertIn(b'#0;2;60;60;60', writes[-1])
            app.focused = True
            app.draw(Screen())
            self.assertIn(b'#0;2;100;100;100', writes[-1])

    def test_list_covers_are_overdrawn_grey_without_clearing(self):
        app = tui.App('http://localhost:9090')
        app.page = 'Library'
        app.tab = 1
        cover = b'\x1bPq#0;2;100;100;100#0~\x1b\\'
        app.list_sixel_displayed = ((5, 8, cover),)
        app.list_sixel_displayed_areas = ((5, 8, 12, 4),)
        app.list_sixel_size = (24, 80)
        app.list_sixel_scroll = (1, '', 0)
        app.focused = False
        def draw_library(*_):
            app.list_sixel_targets.append((5, 8, app.cover_payload(cover)))
            app.list_sixel_areas.append((5, 8, 12, 4))
        writes = []
        class Screen:
            def clearok(self, enabled): raise AssertionError('focus change must not clear')
            def erase(self): pass
            def refresh(self): pass
            def insstr(self, *args): pass
            def getmaxyx(self): return (24, 80)
            def inch(self, y, x): return 32
        with patch.object(app, 'colored'), patch.object(app, 'draw_library', side_effect=draw_library), \
             patch.object(app, 'draw_live'), patch.object(app, 'draw_nav'), \
             patch.object(tui.os, 'write', side_effect=lambda fd, data: writes.append(data)):
            app.draw(Screen())
        self.assertFalse(any(b'\x1b[2J' in data for data in writes))
        self.assertIn(b'#0;2;60;60;60', writes[-1])

    def test_new_covers_in_same_slots_clear_moved_images(self):
        app = tui.App('http://localhost:9090')
        app.page = 'Library'
        app.tab = 0
        app.list_sixel_displayed = ((5, 8, b'old'),)
        app.list_sixel_displayed_areas = ((5, 8, 10, 3),)
        app.list_sixel_displayed_sources = (b'old',)
        app.list_sixel_size = (24, 80)
        app.list_sixel_scroll = (0, '', 0)
        def draw_library(*_):  # new search results: a different cover in the same slot
            app.list_sixel_targets.append((5, 8, b'new'))
            app.list_sixel_areas.append((5, 8, 10, 3))
            app.list_sixel_sources.append(b'new')
        writes = []
        class Screen:
            def clearok(self, enabled): pass
            def erase(self): pass
            def refresh(self): pass
            def insstr(self, *args): pass
            def getmaxyx(self): return (24, 80)
            def inch(self, y, x): return 32
        with patch.object(app, 'colored'), patch.object(app, 'draw_library', side_effect=draw_library), \
             patch.object(app, 'draw_live'), patch.object(app, 'draw_nav'), \
             patch.object(tui.os, 'write', side_effect=lambda fd, data: writes.append(data)):
            app.draw(Screen())
        self.assertIn(b'\x1b[H\x1b[2J', writes)
        self.assertTrue(writes[-1].endswith(b'new\x1b8'))
        self.assertEqual(app.list_sixel_displayed_sources, (b'new',))

    def test_list_scroll_repaints_without_terminal_scrolling(self):
        app = tui.App('http://localhost:9090')
        app.page = 'Library'
        app.tab = 0
        app.list_sixel_displayed = ((5, 8, b'old'),)
        app.list_sixel_displayed_areas = ((5, 8, 10, 3),)
        app.list_sixel_size = (24, 80)
        app.list_sixel_scroll = (0, '', 0)
        app.scroll = 1
        def draw_library(*_):
            app.list_sixel_targets.append((5, 8, b'new'))
            app.list_sixel_areas.append((5, 8, 10, 3))
        events = []
        class Screen:
            def clearok(self, enabled): events.append('clearok')
            def erase(self): pass
            def refresh(self): events.append('refresh')
            def insstr(self, *args): pass
            def getmaxyx(self): return (24, 80)
            def inch(self, y, x): return 32
        with patch.object(app, 'colored'), patch.object(app, 'draw_library', side_effect=draw_library), \
             patch.object(app, 'draw_live'), patch.object(app, 'draw_nav'), \
             patch.object(tui.os, 'write', side_effect=lambda fd, data: events.append(data)):
            app.draw(Screen())
        self.assertEqual(events[:2], ['clearok', 'refresh'])  # curses repaints instead of scrolling
        self.assertFalse(any(isinstance(event, bytes) and b'\x1b[2J' in event for event in events))
        self.assertIn(b'\x1b[0;38;5;17m', events[2])  # stale cells under old covers
        self.assertTrue(events[3].endswith(b'new\x1b8'))
        self.assertEqual(app.list_sixel_scroll, (0, '', 1))

    def test_mouse_clicks_dispatch_to_regions_drawn_this_frame(self):
        app = tui.App('http://localhost:9090')
        app.page = 'Library'
        app.tab = 0
        class Screen:
            def getmaxyx(self): return (30, 100)
            def addnstr(self, *args): pass
        with patch.object(app, 'fetch_list'), patch.object(app, 'switch') as switch:
            app.draw_library(Screen(), 24)
            app.draw_nav(Screen(), 24)
            app.mouse(None, ('click', 5, 3))  # sidebar: "2 Discover"
            self.assertEqual(app.tab, 1)
            app.mouse(None, ('click', 2, 23))  # page nav: "F1 Library"
            switch.assert_called_once_with('Library')
            app.mouse(None, ('click', 90, 1))  # nothing there
        self.assertEqual(app.tab, 1)

    def test_results_enter_plays_right_opens_and_album_left_returns(self):
        app = tui.App('http://localhost:9090')
        app.page = 'Library'
        app.tab = 2
        app.rows = [{'id': '1', 'title': 'A'}, {'id': '2', 'title': 'B'}]
        app.library_sidebar_focus = False
        app.library_right_index = 1
        app.row = 1
        with patch.object(app, 'open_album') as open_album, patch.object(app, 'album_action') as album_action:
            app.library_key(10, None)
            album_action.assert_called_once_with(app.rows[1], ord('p'), None)
            open_album.assert_not_called()
            app.library_key(tui.curses.KEY_RIGHT, None)
            open_album.assert_called_once_with(app.rows[1])
        app.page = 'Album'; app.album_source = 'Library'; app.album_action_index = 1
        with patch.object(app, 'switch') as switch:
            app.action(tui.curses.KEY_LEFT, None)
            self.assertEqual(app.album_action_index, 0)
            switch.assert_not_called()
            app.action(tui.curses.KEY_LEFT, None)
            switch.assert_called_once_with('Library')

    def test_mouse_selects_then_opens_results_and_wheel_stays_in_list(self):
        app = tui.App('http://localhost:9090')
        app.page = 'Library'
        app.tab = 2
        app.rows = [{'id': '1', 'title': 'A'}, {'id': '2', 'title': 'B'}, {'id': '3', 'title': 'C'}]
        app.library_sidebar_focus = True
        with patch.object(app, 'open_album') as open_album:
            app.click_result(1, None)
            self.assertEqual((app.row, app.library_sidebar_focus, app.library_right_index), (1, False, 1))
            open_album.assert_not_called()
            app.click_result(1, None)
            open_album.assert_called_once_with(app.rows[1])
        app.tab = 0
        app.row = 0
        app.mouse(None, ('up', 50, 12))
        self.assertEqual((app.row, app.search_editing), (0, False))
        for _ in range(5): app.mouse(None, ('down', 50, 12))
        self.assertEqual(app.row, 2)
        app.mouse(None, ('down', 5, 12))  # over the sidebar: ignored
        self.assertEqual(app.row, 2)

    def test_mouse_in_pickers_and_seek_bar(self):
        app = tui.App('http://localhost:9090')
        with patch.object(app, 'mouse_event', return_value=('click', 4, 6)):
            self.assertEqual(app.picker_mouse(tui.curses.KEY_MOUSE, 3, 10, 5, 20), (ord(' '), 8))
        with patch.object(app, 'mouse_event', return_value=('down', 4, 6)):
            self.assertEqual(app.picker_mouse(tui.curses.KEY_MOUSE, 3, 10, 5, 20), (tui.curses.KEY_DOWN, None))
        with patch.object(app, 'mouse_event', return_value=('click', 4, 40)):
            self.assertEqual(app.picker_mouse(tui.curses.KEY_MOUSE, 3, 10, 5, 20), (-1, None))
        self.assertEqual(app.picker_mouse(ord('x'), 3, 10, 5, 20), (ord('x'), None))
        app.player = {'duration': 200}
        with patch.object(app, 'transport') as transport:
            app.seek_fraction(0.25)
        transport.assert_called_once_with('seek', {'seconds': 50})

    def test_tab_cycles_all_visibility_combinations(self):
        app = tui.App('http://localhost:9090')
        app.config.update(levels=False, dr=False, balance=False)
        seen = set()
        with patch.object(app, 'save'):
            for _ in range(8):
                app.action(tui.curses.KEY_BTAB, None)
                seen.add(tuple(app.config[name] for name in ('levels', 'dr', 'balance')))
        self.assertEqual(len(seen), 8)
        self.assertEqual(tuple(app.config[name] for name in ('levels', 'dr', 'balance')),
                         (False, False, False))

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
            self.assertEqual(app.now_action, 4)
            app.now_key(tui.curses.KEY_DOWN, None)
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
