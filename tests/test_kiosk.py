#!/usr/bin/env python3
"""The small-screen kiosk UI (omdrc-ctrl/src/kiosk, served under /k/).

It is a separate package and a pure client of the panel's routes, so what needs
pinning is the seam: it is registered, it serves its shell and every script the
shell references, it never exposes a command line, and the desktop panel can
still hand it over with ?view=mini.
"""
import importlib.util
import re
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "omdrc-ctrl/src"
sys.path.insert(0, str(SRC))

SPEC = importlib.util.spec_from_file_location("omdrc_kiosk_app", SRC / "app.py")
APP = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(APP)


class KioskTests(unittest.TestCase):
    def setUp(self):
        self.client = APP.app.test_client()
        self._saved = (APP.COMMANDS, APP.CMD_MAP)
        APP.COMMANDS = [{"id": "drc_off", "what": "OFF", "group": "drc", "type": "WRITE",
                         "button": "Apply", "confirm": "yes", "cmd": "/bin/secret --flag"}]
        APP.CMD_MAP = {c["id"]: c for c in APP.COMMANDS}

    def tearDown(self):
        APP.COMMANDS, APP.CMD_MAP = self._saved

    def test_shell_is_served_and_lists_existing_scripts(self):
        page = self.client.get("/k/")
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        scripts = re.findall(r'src="(/k/static/[^"?]+)', html)
        self.assertIn("/k/static/main.js", scripts)
        for path in scripts:
            self.assertEqual(self.client.get(path).status_code, 200, path)
        self.assertEqual(self.client.get("/k/static/kiosk.css").status_code, 200)

    def test_config_exposes_commands_but_never_their_command_line(self):
        data = self.client.get("/k/api/config").get_json()
        self.assertTrue(data["ok"])
        self.assertEqual([c["id"] for c in data["commands"]], ["drc_off"])
        self.assertNotIn("cmd", data["commands"][0])
        self.assertEqual(data["commands"][0]["confirm"], "yes")
        self.assertEqual(set(data["features"]), {"drdb", "cdin"})

    def test_view_mini_redirects_the_desktop_panel_to_the_kiosk(self):
        r = self.client.get("/?view=mini")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith("/k/"))

    def test_transport_maps_three_words_to_fixed_mpc_commands(self):
        import kiosk
        seen = []
        saved = kiosk._mpc
        kiosk._mpc = lambda args: (seen.append(args) or (True, ""))
        try:
            for word in ("play", "pause", "stop"):
                r = self.client.post("/k/api/transport", json={"action": word})
                self.assertEqual((r.status_code, r.get_json()["ok"]), (200, True), word)
            self.assertEqual(seen, [["play"], ["pause"], ["stop"]])
            for bad in ("next", "play; reboot", "", None):
                r = self.client.post("/k/api/transport", json={"action": bad})
                self.assertEqual(r.status_code, 400, bad)
            self.assertEqual(len(seen), 3, "nothing but the three words reaches mpc")
            kiosk._mpc = lambda args: (False, "MPD is down")
            r = self.client.post("/k/api/transport", json={"action": "play"})
            self.assertEqual(r.get_json(), {"ok": False, "error": "MPD is down"})
        finally:
            kiosk._mpc = saved

    def test_every_page_script_registers_a_page(self):
        pages = sorted((SRC / "kiosk/static/pages").glob("*.js"))
        self.assertGreaterEqual(len(pages), 8)
        for path in pages:
            self.assertIn("K.registerPage(", path.read_text(), path.name)

    def test_a_page_stops_its_work_when_hidden(self):
        # "no computation without visible feedback": every page that starts
        # streams or pollers must give them back in hide().
        for path in (SRC / "kiosk/static/pages").glob("*.js"):
            text = path.read_text()
            if "K.Poller(" in text or "K.streams.open(" in text or "listen(" in text:
                self.assertIn(".hide =", text, f"{path.name} starts work but has no hide()")


if __name__ == "__main__":
    unittest.main()
