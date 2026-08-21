#!/usr/bin/env python3
"""The audio-chain card: who the OS says is holding the sound devices.

Every other card in the panel reports what open-media-drc believes about
itself.  This one asks fstat(1)/fuser(1) instead, and the two things worth
pinning are exactly the two the belief-based cards cannot do:

  * a process that is not part of the chain — a stray PulseAudio, a leftover
    mpv — is drawn, and drawn as a fault, wherever it turns up;
  * "has the device open" and "is putting audio through it" stay separate.
    brutefir holds the DAC from the moment it starts and MPD keeps its output
    open while paused, so an LED driven by the descriptor alone would be green
    all day and would say nothing.
"""

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "omdrc_chain_app", ROOT / "omdrc-ctrl/src/app.py")
APP = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(APP)


def _device(role, path, present=True, label="", target=""):
    return {"role": role, "spec": path, "path": path, "target": target,
            "label": label, "present": present,
            "holders": [], "readers": [], "writers": []}


# The FreeBSD chain, all four roles up.
FREEBSD = {
    "capture": _device("capture", "/dev/dsp.capture", label="ESI U24XL", target="dsp1"),
    "bridge":  _device("bridge", "/dev/dsp.play"),
    "loop":    _device("loop", "/dev/dsp.loop"),
    "dac":     _device("dac", "/dev/dsp.dac", label="OKTO DAC8", target="dsp0"),
}


def _holder(pid, cmd, mode, user="giacomo"):
    return {"pid": pid, "cmd": cmd, "user": user, "mode": mode}


def _status(devices=None, holders=None, activity=None, running=(),
            services=(), privileged=True) -> dict:
    devices = {k: dict(v) for k, v in (devices or FREEBSD).items()}
    with mock.patch.object(APP, "_chain_resolve_devices", return_value=devices), \
         mock.patch.object(APP, "_device_holders",
                           return_value=(holders or {}, privileged)), \
         mock.patch.object(APP, "_chain_activity", return_value=activity or {}), \
         mock.patch.object(APP, "_process_running", lambda name: name in running), \
         mock.patch.object(APP, "_service_running", lambda name: name in services):
        return APP._chain_status()


def _node(status, node_id):
    return next((n for n in status["nodes"] if n["id"] == node_id), None)


def _edge(status, src, dst):
    return next((e for e in status["edges"]
                 if e["from"] == src and e["to"] == dst), None)


class HolderParsing(unittest.TestCase):
    """The two tools' output, read back into (pid, command, direction)."""

    def test_fstat_rows_are_matched_by_inode_not_by_name(self):
        # fstat resolves symlinks before printing, so a role link and the node
        # it points at come back under one name.  Both must still be reported.
        with tempfile.TemporaryDirectory() as directory:
            node = Path(directory) / "dsp0"
            node.write_text("")
            link = Path(directory) / "dsp.dac"
            link.symlink_to(node)
            inode = node.stat().st_ino
            out = (
                "USER     CMD          PID   FD MOUNT      INUM MODE"
                "         SZ|DV R/W NAME\n"
                f"giacomo  brutefir     722    7 /dev  {inode} crw-rw-rw-"
                f"    dsp0  w  {node}\n"
            )
            with mock.patch.object(APP, "_chain_run_tool", return_value=(out, True)):
                holders, privileged = APP._holders_fstat([str(node), str(link)])
        self.assertTrue(privileged)
        self.assertEqual(sorted(holders), sorted([str(node), str(link)]))
        self.assertEqual(holders[str(link)][0]["cmd"], "brutefir")
        self.assertEqual(holders[str(link)][0]["mode"], "w")

    def test_fstat_merges_several_descriptors_on_one_device(self):
        with tempfile.TemporaryDirectory() as directory:
            node = Path(directory) / "dsp0"
            node.write_text("")
            inode = node.stat().st_ino
            out = "\n".join([
                "USER     CMD          PID   FD MOUNT      INUM MODE         SZ|DV R/W NAME",
                f"giacomo  mpv          900    4 /dev  {inode} crw-rw-rw-    dsp0  r  {node}",
                f"giacomo  mpv          900    5 /dev  {inode} crw-rw-rw-    dsp0  w  {node}",
            ])
            with mock.patch.object(APP, "_chain_run_tool", return_value=(out, True)):
                holders, _ = APP._holders_fstat([str(node)])
        self.assertEqual(len(holders[str(node)]), 1)
        self.assertEqual(holders[str(node)][0]["mode"], "rw")

    def test_fuser_access_letters_become_directions(self):
        with tempfile.TemporaryDirectory() as directory:
            play = Path(directory) / "pcmC0D0p"
            play.write_text("")
            cap = Path(directory) / "pcmC1D1c"
            cap.write_text("")
            out = "\n".join([
                "                     USER        PID ACCESS COMMAND",
                f"{play}:              giacomo     722 F.... brutefir",
                f"{cap}:               giacomo     722 f.... brutefir",
            ])
            with mock.patch.object(APP, "_chain_run_tool", return_value=(out, True)):
                holders, _ = APP._holders_fuser([str(play), str(cap)])
        self.assertEqual(holders[str(play)][0]["mode"], "w")
        self.assertEqual(holders[str(cap)][0]["mode"], "r")


class Leds(unittest.TestCase):
    """Free / held / active are three different answers and stay that way."""

    def test_nothing_open_is_free_not_absent(self):
        status = _status()
        self.assertEqual(status["input"]["state"], "free")
        self.assertEqual(status["output"]["state"], "free")

    def test_missing_capture_card_is_absent(self):
        devices = dict(FREEBSD)
        devices["capture"] = _device("capture", "/dev/dsp.capture", present=False)
        status = _status(devices)
        self.assertEqual(status["input"]["state"], "absent")
        self.assertIn("not attached",
                      " ".join(p["text"] for p in status["problems"]))

    def test_brutefir_holding_an_idle_dac_is_held_not_active(self):
        # The case an fd-only LED gets wrong: brutefir opens the DAC when it
        # starts and never lets go, so "held" has to be distinguishable from
        # "audio is going through it" or the light means nothing.
        status = _status(
            holders={"/dev/dsp.loop": [_holder("722", "brutefir", "r")],
                     "/dev/dsp.dac":  [_holder("722", "brutefir", "w")],
                     "/dev/dsp.play": [_holder("601", "musicpd", "w")]},
            activity={"mpd": False})
        self.assertEqual(status["output"]["state"], "held")
        self.assertFalse(status["flowing"])

    def test_mpd_playing_lights_the_dac(self):
        status = _status(
            holders={"/dev/dsp.loop": [_holder("722", "brutefir", "r")],
                     "/dev/dsp.dac":  [_holder("722", "brutefir", "w")],
                     "/dev/dsp.play": [_holder("601", "musicpd", "w")]},
            activity={"mpd": True})
        self.assertEqual(status["output"]["state"], "active")
        self.assertTrue(status["flowing"])
        self.assertTrue(_edge(status, "app:601", "bridge")["active"])
        self.assertTrue(_edge(status, "app:722", "dev:dac")["active"])

    def test_cdin_reading_a_silent_disc_holds_the_input_without_lighting_it(self):
        status = _status(
            holders={"/dev/dsp.capture": [_holder("911", "omdrc-cdin", "r")]},
            activity={"omdrc-cdin": False})
        self.assertEqual(status["input"]["state"], "held")

    def test_cdin_playing_lights_the_input(self):
        status = _status(
            holders={"/dev/dsp.capture": [_holder("911", "omdrc-cdin", "r")],
                     "/dev/dsp.play":    [_holder("911", "omdrc-cdin", "w")]},
            activity={"omdrc-cdin": True})
        self.assertEqual(status["input"]["state"], "active")
        self.assertTrue(_edge(status, "dev:capture", "app:911")["active"])


class Graph(unittest.TestCase):
    """The blocks and the arcs between them."""

    def test_cd_playing_runs_capture_to_cdin_to_bridge_to_brutefir_to_dac(self):
        status = _status(
            holders={"/dev/dsp.capture": [_holder("911", "omdrc-cdin", "r")],
                     "/dev/dsp.play":    [_holder("911", "omdrc-cdin", "w")],
                     "/dev/dsp.loop":    [_holder("722", "brutefir", "r")],
                     "/dev/dsp.dac":     [_holder("722", "brutefir", "w")]},
            activity={"omdrc-cdin": True})
        for src, dst in (("dev:capture", "app:911"), ("app:911", "bridge"),
                         ("bridge", "app:722"), ("app:722", "dev:dac")):
            self.assertIsNotNone(_edge(status, src, dst), f"{src} -> {dst}")
        rows = [_node(status, i)["row"] for i in
                ("dev:capture", "app:911", "bridge", "app:722", "dev:dac")]
        self.assertEqual(rows, sorted(rows))
        self.assertEqual(len(set(rows)), len(rows))

    def test_a_player_on_the_dac_with_no_convolver_is_named_as_bypass(self):
        status = _status(
            holders={"/dev/dsp.dac": [_holder("601", "musicpd", "w")]},
            activity={"mpd": True})
        edge = _edge(status, "app:601", "dev:dac")
        self.assertIsNotNone(edge)
        self.assertIn("bypass", edge["label"])

    def test_a_running_but_silent_mpd_keeps_its_block(self):
        # MPD closes its output when playback stops.  Dropping it from the
        # diagram would make a healthy idle box look like a broken one.
        status = _status(running=("musicpd",), activity={"mpd": False})
        node = _node(status, "app:musicpd")
        self.assertIsNotNone(node)
        self.assertTrue(node["idle"])
        self.assertFalse(status["flowing"])

    def test_the_renderer_feeding_mpd_is_drawn_above_it(self):
        status = _status(running=("musicpd",), services=("upmpdcli",),
                         activity={"mpd": True})
        feeder, mpd = _node(status, "app:upmpdcli"), _node(status, "app:musicpd")
        self.assertIsNotNone(feeder)
        self.assertLess(feeder["row"], mpd["row"])
        self.assertIsNotNone(_edge(status, "app:upmpdcli", "app:musicpd"))

    def test_a_renderer_with_no_mpd_to_feed_is_not_drawn(self):
        self.assertIsNone(_node(_status(services=("upmpdcli",)), "app:upmpdcli"))


class Summary(unittest.TestCase):
    """An arrow in the summary line means "feeds"."""

    def test_two_idle_players_are_never_strung_together(self):
        # The reported bug: MPD and the CD bridge both running, neither
        # playing, no virtual_oss - rendered as "musicpd -> omdrc-cdin -> DAC",
        # a path that does not exist in any direction.
        devices = dict(FREEBSD)
        devices["bridge"] = _device("bridge", "/dev/dsp.play", present=False)
        devices["loop"] = _device("loop", "/dev/dsp.loop", present=False)
        status = _status(devices, running=("musicpd", "omdrc-cdin"),
                         activity={"mpd": False, "omdrc-cdin": False})
        self.assertNotIn("→", status["summary"])
        self.assertIn("idle", status["summary"])
        self.assertIn("free", status["summary"])

    def test_an_idle_player_with_no_bridge_gets_no_arc(self):
        devices = dict(FREEBSD)
        devices["bridge"] = _device("bridge", "/dev/dsp.play", present=False)
        devices["loop"] = _device("loop", "/dev/dsp.loop", present=False)
        status = _status(devices, running=("musicpd", "omdrc-cdin"),
                         activity={"mpd": False, "omdrc-cdin": False})
        self.assertEqual(status["edges"], [])

    def test_an_idle_player_has_no_arc_even_when_the_bridge_is_up(self):
        # An arc means an open descriptor and nothing else.  MPD holding
        # nothing has no connection to draw, whether or not virtual_oss is
        # there to receive one.
        status = _status(running=("musicpd",), activity={"mpd": False})
        self.assertIsNone(_edge(status, "app:musicpd", "bridge"))
        self.assertTrue(_node(status, "app:musicpd")["idle"])

    def test_a_held_but_silent_dac_names_who_has_it(self):
        status = _status(
            holders={"/dev/dsp.loop": [_holder("722", "brutefir", "r")],
                     "/dev/dsp.dac":  [_holder("722", "brutefir", "w")]},
            activity={"mpd": False})
        self.assertEqual(status["summary"], "idle — OKTO DAC8 held by brutefir")

    def test_playing_names_the_whole_path(self):
        status = _status(
            holders={"/dev/dsp.capture": [_holder("911", "omdrc-cdin", "r")],
                     "/dev/dsp.play":    [_holder("911", "omdrc-cdin", "w")],
                     "/dev/dsp.loop":    [_holder("722", "brutefir", "r")],
                     "/dev/dsp.dac":     [_holder("722", "brutefir", "w")]},
            activity={"omdrc-cdin": True})
        self.assertEqual(status["summary"],
                         "omdrc-cdin → brutefir → OKTO DAC8")


class Intruders(unittest.TestCase):
    """The failure the card exists for."""

    def test_a_stray_pulseaudio_on_the_dac_is_a_block_and_a_fault(self):
        status = _status(holders={"/dev/dsp.dac": [_holder("4410", "pulseaudio", "w")]})
        node = _node(status, "app:4410")
        self.assertIsNotNone(node)
        self.assertFalse(node["expected"])
        errors = [p for p in status["problems"] if p["severity"] == "error"]
        self.assertTrue(any("pulseaudio" in p["text"] for p in errors))

    def test_an_unaskable_holder_counts_as_producing(self):
        # We cannot ask mpv whether it is playing.  A squatter that turns out
        # to be silent is still the one to go and kill, so the light goes on.
        status = _status(holders={"/dev/dsp.dac": [_holder("4410", "mpv", "w")]})
        self.assertTrue(status["flowing"])
        self.assertEqual(status["output"]["state"], "active")

    def test_an_incomplete_listing_says_so(self):
        status = _status(privileged=False)
        self.assertTrue(any("only this user's processes" in p["text"]
                            for p in status["problems"]))


class Escalation(unittest.TestCase):
    """`sudo -n` around the tool, and the one thing that must not be mistaken
    for a refusal."""

    def setUp(self):
        APP._CHAIN_SUDO_OK = None
        self.addCleanup(setattr, APP, "_CHAIN_SUDO_OK", None)

    def _run(self, results):
        calls = []

        def fake(argv, **kwargs):
            calls.append(argv)
            out, code = results.pop(0)
            return mock.Mock(stdout=out, stderr="", returncode=code)

        with mock.patch.object(APP.os, "geteuid", return_value=1001), \
             mock.patch.object(APP.subprocess, "run", side_effect=fake):
            out, privileged = APP._chain_run_tool(["fuser", "-v", "/dev/x"])
        return out, privileged, calls

    def test_a_device_nobody_holds_is_not_read_as_a_refusal(self):
        # `fuser -v` exits non-zero with nothing to say when no process holds
        # the file.  Reading that as "sudo said no" would permanently downgrade
        # the card on a healthy idle box.
        out, privileged, calls = self._run([("", 1)])
        self.assertTrue(privileged)
        self.assertEqual(len(calls), 1)
        self.assertIs(APP._CHAIN_SUDO_OK, True)

    def test_a_real_refusal_falls_back_and_is_not_retried(self):
        out, privileged, calls = self._run(
            [("sudo: a password is required\n", 1), ("", 1)])
        self.assertFalse(privileged)
        self.assertEqual(len(calls), 2)                # escalated, then plain
        self.assertEqual(calls[1][0], "fuser")
        self.assertIs(APP._CHAIN_SUDO_OK, False)
        self.assertEqual(APP._chain_tool_command(["fstat"]), ["fstat"])


class SndlinkRoles(unittest.TestCase):
    """What omdrc_sndlink publishes about the role assignment it made."""

    def _with_roles(self, text):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "omdrc_sndlink.roles"
            path.write_text(text, encoding="utf-8")
            with mock.patch.object(APP, "SNDLINK_ROLES_FILE", str(path)):
                return _status()

    def test_a_guessed_dac_is_reported(self):
        # The failure that is invisible everywhere else: the chain comes up,
        # the DAC lights green, and every byte goes to the wrong card.
        status = self._with_roles(
            "dac_unit=0\ndac_desc=ESI U24XL\ndac_guessed=1\n"
            "capture_unit=\ncapture_desc=\ncapture_wanted=\n")
        warns = [p["text"] for p in status["problems"] if p["severity"] == "warn"]
        self.assertTrue(any("guessed" in w and "ESI U24XL" in w for w in warns))

    def test_a_capture_spec_that_matches_nothing_is_reported(self):
        status = self._with_roles(
            "dac_unit=1\ndac_desc=OKTO\ndac_guessed=0\n"
            "capture_unit=\ncapture_desc=\ncapture_wanted=ESI U24XL\n")
        warns = [p["text"] for p in status["problems"] if p["severity"] == "warn"]
        self.assertTrue(any("no card matches" in w for w in warns))

    def test_a_clean_assignment_says_nothing(self):
        status = self._with_roles(
            "dac_unit=1\ndac_desc=OKTO\ndac_guessed=0\n"
            "capture_unit=0\ncapture_desc=ESI U24XL\ncapture_wanted=ESI U24XL\n")
        self.assertEqual([p for p in status["problems"]
                          if p["severity"] == "warn"], [])

    def test_a_box_without_the_service_is_not_a_fault(self):
        with mock.patch.object(APP, "SNDLINK_ROLES_FILE", "/nonexistent/roles"):
            status = _status()
        self.assertEqual([p for p in status["problems"]
                          if p["severity"] == "warn"], [])


class AlsaNames(unittest.TestCase):
    def test_hw_specs_become_dev_snd_nodes(self):
        self.assertEqual(APP._alsa_node("hw:1,0", "p"), "/dev/snd/pcmC1D0p")
        self.assertEqual(APP._alsa_node("hw:1,1", "c"), "/dev/snd/pcmC1D1c")
        self.assertEqual(APP._alsa_node("plughw:0,0", "p"), "/dev/snd/pcmC0D0p")
        self.assertEqual(APP._alsa_node("hw:2", "c"), "/dev/snd/pcmC2D0c")
        self.assertEqual(APP._alsa_node("nonsense", "p"), "")


if __name__ == "__main__":
    unittest.main()
