#!/usr/bin/env python3
"""The CD input card: what the daemon's log is reduced to, and the two buttons.

`omdrc-cdin` is watched rather than driven, so almost everything the card shows
is a reading of its log — and the readings that matter are the ones nothing
else would ever mention again: an underrun is an audible dropout that already
happened, and a `[stats]` line scrolls away.  The tests below pin the reduction
(stats line → chips + sentences), the rule that a stopped daemon is idle rather
than broken, and the start/stop route's refusal to believe the rc exit status
over the process itself.
"""

import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "omdrc_cdin_app", ROOT / "omdrc-ctrl/src/app.py")
APP = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(APP)

# A healthy run: the daemon came up, found both ends, and is playing a disc.
PLAYING = """\
2026-08-21 10:00:00.001 [INF] omdrc-cdin 0.1 starting: in=/dev/dsp.capture out=/dev/dsp.play 44100 Hz
2026-08-21 10:00:05.100 [INF] playback /dev/dsp.play: available, 32-bit
2026-08-21 10:00:05.200 [INF] capture /dev/dsp.capture: available
2026-08-21 10:00:05.300 [INF] state idle: capture open, waiting for audio
2026-08-21 10:01:05.300 [INF] state playing: audio on the wire
2026-08-21 10:01:05.400 [INF] playback /dev/dsp.play: acquired
2026-08-21 10:02:05.400 [INF] [stats] lead 1962 ms (min 1955, max 1972)  \
drift +1.2 ppm (+/-3.0), ring fills in 46 h  in 44100.206 Hz  out 44100.152 Hz  \
frames 4054016/3980288  drops 0 B  starves 0  silence 0%  up 125 s
"""

# A CD player switched off at the wall.  The transport drops the S/PDIF
# carrier, the ESI's clock goes with it, and the capture dribbles a few hundred
# non-zero frames a second instead of 44100 — the failure that used to hold the
# output device for hours because every other signal looked healthy.
STOPPED_TRANSPORT = """\
2026-08-21 10:00:00.001 [INF] omdrc-cdin 0.1 starting: in=/dev/dsp.capture out=/dev/dsp.play 44100 Hz
2026-08-21 10:00:05.100 [INF] playback /dev/dsp.play: available, 32-bit
2026-08-21 10:00:05.200 [INF] capture /dev/dsp.capture: available
2026-08-21 10:00:05.300 [INF] state idle: capture open, waiting for audio
2026-08-21 10:00:07.350 [WRN] state no-carrier: 500 frames/s against 44100 nominal — the transport is stopped or the cable is out
"""

# The same daemon after the output device went missing for a while.
FAILED_OUTPUT = """\
2026-08-21 09:00:00.001 [INF] omdrc-cdin 0.1 starting: in=/dev/dsp.capture out=/dev/dsp.play 44100 Hz
2026-08-21 09:00:00.100 [ERR] playback /dev/dsp.play: unavailable — No such file or directory (retrying every 2 s)
2026-08-21 09:10:00.100 [INF] playback /dev/dsp.play: available, 32-bit
2026-08-21 09:10:00.200 [INF] capture /dev/dsp.capture: available
2026-08-21 09:10:00.300 [INF] state idle: capture open, waiting for audio
"""


def _status(text: str, running: bool = True, ack: str | None = None) -> dict:
    """One reading of the card.

    The ack watermark lives in the real state directory, which on the audio box
    is a live file — so it is redirected into the temp dir here whether or not
    a test cares about it.  Otherwise every assertion about the red line would
    quietly depend on whether somebody had clicked "dismiss" on that machine.
    """
    with tempfile.TemporaryDirectory() as directory:
        log = Path(directory) / "omdrc-cdin.log"
        log.write_text(text, encoding="utf-8")
        ack_file = Path(directory) / "cdin_error_ack"
        if ack is not None:
            ack_file.write_text(ack + "\n", encoding="utf-8")
        with mock.patch.object(APP, "CDIN_LOG_FILE", str(log)), \
             mock.patch.object(APP, "_CDIN_ACK_FILE", str(ack_file)), \
             mock.patch.object(APP, "_process_running", return_value=running):
            return APP._cdin_status()


def _chip(status: dict, key: str) -> dict | None:
    return next((m for m in status["metrics"] if m["key"] == key), None)


class StatsLine(unittest.TestCase):
    """The `[stats]` line is 200 characters of which two decide anything."""

    def test_fields_are_read_off_the_line(self):
        f = _status(PLAYING)["stats_fields"]
        self.assertEqual(f["lead_ms"], 1962)
        self.assertEqual(f["lead_min_ms"], 1955)
        self.assertEqual(f["drift_ppm"], 1.2)
        self.assertEqual(f["horizon_h"], 46)
        self.assertEqual(f["starves"], 0)
        self.assertEqual(f["drops_bytes"], 0)
        self.assertEqual(f["silence"], "0%")
        self.assertEqual(f["up_s"], 125)

    def test_absent_numbers_stay_absent(self):
        """A capture-only run has no lead at all, and `lead 0` would read as a
        fault rather than as "the question does not apply"."""
        line = ("2026-08-21 10:00:00.000 [INF] [stats] capture-only  "
                "in 44100.010 Hz  frames 441000  silence n/a  up 10 s\n")
        f = _status(line)["stats_fields"]
        self.assertTrue(f["measure_only"])
        self.assertNotIn("lead_ms", f)
        self.assertNotIn("starves", f)

    def test_a_settling_drift_is_not_a_number(self):
        line = ("2026-08-21 10:00:00.000 [INF] [stats] lead 1900 ms  "
                "drift settling (12 s of 60)  drops 0 B  starves 0  "
                "silence 0%  up 12 s\n")
        f = _status(line)["stats_fields"]
        self.assertTrue(f["drift_settling"])
        self.assertNotIn("drift_ppm", f)


class Readout(unittest.TestCase):
    """Chips are measurements; problems are measurements that already cost
    something audible."""

    def test_a_healthy_run_has_chips_and_no_problems(self):
        status = _status(PLAYING)
        self.assertEqual(_chip(status, "lead")["level"], "ok")
        self.assertEqual(_chip(status, "starves")["value"], "0")
        self.assertEqual(_chip(status, "starves")["level"], "ok")
        self.assertEqual(status["problems"], [])
        self.assertTrue(status["active"])

    def test_underruns_are_both_a_chip_and_a_sentence(self):
        text = PLAYING.replace("starves 0", "starves 3")
        status = _status(text)
        self.assertEqual(_chip(status, "starves")["level"], "error")
        self.assertEqual(
            [p["level"] for p in status["problems"]], ["error"])
        self.assertIn("3 underruns", status["problems"][0]["text"])

    def test_a_ring_that_filled_up_is_reported_as_discarded_audio(self):
        text = PLAYING.replace("drops 0 B", "drops 8192 B")
        status = _status(text)
        self.assertEqual(_chip(status, "drops")["level"], "error")
        self.assertIn("8192 B", status["problems"][0]["text"])

    def test_a_lead_with_nothing_left_to_absorb_a_seek_warns(self):
        text = PLAYING.replace("lead 1962 ms", "lead 120 ms")
        status = _status(text)
        self.assertEqual(_chip(status, "lead")["level"], "warn")
        self.assertEqual(status["problems"][0]["level"], "warn")

    def test_a_scripted_dropout_is_not_a_fault(self):
        text = PLAYING.rstrip("\n") + "  dropouts 1\n"
        status = _status(text)
        self.assertEqual([p["level"] for p in status["problems"]], ["info"])

    def test_an_unopenable_end_is_a_problem_in_its_own_right(self):
        """Even with no stats line at all: the LED says a disc cannot play, and
        the sentence says which end and why."""
        status = _status(FAILED_OUTPUT.splitlines(keepends=True)[1])
        self.assertEqual(status["led"], "red")
        self.assertIn("cannot be opened", status["problems"][0]["text"])


class LastError(unittest.TestCase):
    """One red line, kept after the condition clears."""

    def test_the_newest_failure_survives_recovery(self):
        status = _status(FAILED_OUTPUT)
        self.assertEqual(status["led"], "green")       # it recovered
        self.assertIsNotNone(status["last_error"])     # and it is still said
        self.assertIn("unavailable", status["last_error"]["text"])
        self.assertEqual(status["last_error"]["at"], "2026-08-21 09:00:00")

    def test_no_failure_means_no_line(self):
        self.assertIsNone(_status(PLAYING)["last_error"])

    def test_a_switched_off_cd_player_is_not_a_fault(self):
        """no-carrier is the daemon working: the player is off, so the output
        was released and MPD has the chain.  Red here would send the user
        hunting a fault that is really an empty disc tray."""
        status = _status(STOPPED_TRANSPORT)
        self.assertEqual(status["led"], "idle")
        self.assertIn("no carrier", status["summary"])
        self.assertFalse(status["active"])

    def test_the_stopped_transport_summary_says_how_dead_the_input_is(self):
        """The rate is the whole diagnosis — it is what separates 'the player
        is off' from 'the interface is broken'."""
        self.assertIn("500 frames/s", _status(STOPPED_TRANSPORT)["summary"])

    def test_a_missing_device_is_still_red(self):
        """The other carrier loss: nothing to read from, which IS a fault and
        must not be softened by the rule above."""
        status = _status(FAILED_OUTPUT.splitlines(keepends=True)[1])
        self.assertEqual(status["led"], "red")

    def test_a_stopped_daemon_is_idle_not_broken(self):
        """Its log outlives it, and nothing is unavailable when nothing is
        trying to open it."""
        status = _status(FAILED_OUTPUT, running=False)
        self.assertEqual(status["led"], "idle")
        self.assertEqual(status["summary"], "not running")
        self.assertFalse(status["active"])


class DismissingAnError(unittest.TestCase):
    """Taking the standing red line down.

    Keeping failures after the condition clears is deliberate — the log scrolls
    away and "the DAC was busy for a minute this evening" is worth knowing.  But
    the line outlives its usefulness, and the only thing that can know when is
    the reader.  So it is dismissed by hand, with a watermark rather than a
    flag: dismissing says "I have seen up to here", never "stop telling me".
    """

    ERROR_AT = "2026-08-21 09:00:00"

    def test_the_line_is_there_until_it_is_dismissed(self):
        self.assertIsNotNone(_status(FAILED_OUTPUT)["last_error"])

    def test_dismissing_takes_it_down(self):
        status = _status(FAILED_OUTPUT, ack=self.ERROR_AT)
        self.assertIsNone(status["last_error"])

    def test_dismissing_deletes_nothing(self):
        """The card reads the daemon's state out of the same lines, so the
        failure stays in the history — only the standing line goes."""
        status = _status(FAILED_OUTPUT, ack=self.ERROR_AT)
        self.assertTrue(any("unavailable" in e["text"] for e in status["events"]))
        self.assertEqual(status["state"], "idle")
        self.assertEqual(status["led"], "green")

    def test_a_newer_failure_still_gets_through(self):
        """The whole point of a watermark: dismissing today must not blind the
        card to what happens next."""
        text = FAILED_OUTPUT + (
            "2026-08-21 11:00:00.100 [ERR] playback /dev/dsp.dac: "
            "unavailable — Device busy (retrying every 2 s)\n")
        status = _status(text, ack=self.ERROR_AT)
        self.assertIsNotNone(status["last_error"])
        self.assertEqual(status["last_error"]["at"], "2026-08-21 11:00:00")

    def test_dismissing_the_newest_covers_the_older_ones_too(self):
        text = FAILED_OUTPUT + (
            "2026-08-21 11:00:00.100 [ERR] playback /dev/dsp.dac: "
            "unavailable — Device busy (retrying every 2 s)\n")
        self.assertIsNone(
            _status(text, ack="2026-08-21 11:00:00")["last_error"])

    def test_a_stale_watermark_does_not_hide_a_fresh_log(self):
        """A daemon restarted after the watermark was written writes newer
        timestamps, so its failures show."""
        self.assertIsNotNone(
            _status(FAILED_OUTPUT, ack="2020-01-01 00:00:00")["last_error"])


class DismissRoute(unittest.TestCase):
    """The route behind the button, which writes the watermark."""

    def _post(self, body, text=FAILED_OUTPUT):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "omdrc-cdin.log"
            log.write_text(text, encoding="utf-8")
            ack = Path(directory) / "cdin_error_ack"
            with mock.patch.object(APP, "CDIN_LOG_FILE", str(log)), \
                 mock.patch.object(APP, "_CDIN_ACK_FILE", str(ack)), \
                 mock.patch.object(APP, "_STATE_DIR", directory), \
                 mock.patch.object(APP, "_process_running", return_value=True):
                response = APP.app.test_client().post("/cdin/dismiss", json=body)
                return response, (ack.read_text().strip()
                                  if ack.exists() else None)

    def test_it_writes_the_watermark_and_answers_with_the_new_reading(self):
        response, written = self._post({"at": "2026-08-21 09:00:00"})
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(written, "2026-08-21 09:00:00")
        # The answer is a full status, so the card repaints from it directly
        # rather than firing a second request to see what it just did.
        self.assertIsNone(body["last_error"])

    def test_junk_is_refused_rather_than_written(self):
        """The watermark is compared against log timestamps; anything else
        would either never match or hide everything forever."""
        response, written = self._post({"at": "yesterday-ish"})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()["ok"])
        self.assertIsNone(written)

    def test_no_timestamp_falls_back_to_the_newest_failure(self):
        response, written = self._post({})
        self.assertTrue(response.get_json()["ok"])
        self.assertEqual(written, "2026-08-21 09:00:00")

    def test_dismissing_with_nothing_to_dismiss_is_not_an_error(self):
        response, written = self._post({}, text=PLAYING)
        self.assertTrue(response.get_json()["ok"])
        self.assertIsNone(written)


class Control(unittest.TestCase):
    """Start and stop, and why the rc exit status is not the answer."""

    def _post(self, action, rc=0, output="", settles=True, control=True,
              gate_ok=True):
        done = subprocess.CompletedProcess([], rc, stdout=output, stderr="")
        running = settles == (action == "start")
        with mock.patch.object(APP, "CDIN_CONTROL", control), \
             mock.patch.object(APP, "CDIN_SETTLE_SECONDS", 0.0), \
             mock.patch.object(APP, "_cdin_disable_mpd_outputs",
                               return_value=(gate_ok, "gate failed")) as gate, \
             mock.patch.object(APP, "_cdin_restore_mpd_output",
                               return_value=(True, "")) as restore, \
             mock.patch.object(APP, "_service_action", return_value=done) as svc, \
             mock.patch.object(APP, "_process_running", return_value=running):
            response = APP.app.test_client().post(
                "/cdin/control", json={"action": action})
        self.last_gate = gate
        self.last_restore = restore
        return response, svc

    def test_start_runs_the_rc_verb_and_reports_the_process(self):
        response, svc = self._post("start")
        self.assertTrue(response.get_json()["ok"])
        self.last_gate.assert_called_once_with()
        self.last_restore.assert_not_called()
        svc.assert_called_once_with(APP.CDIN_SERVICE, "onestart")

    def test_stop_runs_the_rc_verb(self):
        response, svc = self._post("stop")
        self.assertTrue(response.get_json()["ok"])
        self.last_gate.assert_not_called()
        self.last_restore.assert_called_once_with()
        svc.assert_called_once_with(APP.CDIN_SERVICE, "onestop")

    def test_start_refuses_to_run_when_mpd_cannot_be_gated(self):
        response, svc = self._post("start", gate_ok=False)
        self.assertEqual(response.status_code, 502)
        self.assertFalse(response.get_json()["ok"])
        svc.assert_not_called()

    def test_a_zero_exit_that_did_not_start_anything_is_a_failure(self):
        """`service ... onestart` forks a daemon(8) and answers immediately, so
        it can succeed while the daemon dies a moment later on a device that is
        not there.  The process is the evidence, not the exit status."""
        response, _ = self._post("start", rc=0, settles=False)
        body = response.get_json()
        self.assertFalse(body["ok"])
        self.assertIn("did not start", body["error"])
        self.last_restore.assert_called_once_with()

    def test_a_missing_sudoers_grant_says_so(self):
        response, _ = self._post(
            "stop", rc=1, output="sudo: a password is required", settles=False)
        body = response.get_json()
        self.assertFalse(body["ok"])
        self.assertIn("NOPASSWD", body["error"])

    def test_an_unknown_action_is_refused(self):
        response, svc = self._post("release")
        self.assertEqual(response.status_code, 400)
        svc.assert_not_called()

    def test_control_can_be_turned_off(self):
        response, svc = self._post("stop", control=False)
        self.assertEqual(response.status_code, 403)
        svc.assert_not_called()

    def test_the_answer_carries_a_fresh_status(self):
        """So the card repaints from the truth after the process settled rather
        than from the poll that was already in flight."""
        response, _ = self._post("start")
        self.assertIn("status", response.get_json())


class MpdOutputGate(unittest.TestCase):
    """The CD card owns only audible MPD outputs, never the Spectrum FIFO."""

    def test_start_remembers_the_enabled_output_and_disables_all_audible_ones(self):
        outputs = [
            {"id": "1", "name": "OKTO-DAC", "enabled": False},
            {"id": "2", "name": "DRC-native", "enabled": True},
            {"id": "3", "name": "DRC-resamp", "enabled": False},
            {"id": "4", "name": "OMDRC Spectrum", "enabled": True},
        ]
        with mock.patch.object(APP, "_mpd_outputs", return_value=outputs), \
             mock.patch.object(APP, "_mpd_output_action",
                               return_value=(True, "")) as action, \
             mock.patch.object(APP, "_write_state_str") as write:
            ok, error = APP._cdin_disable_mpd_outputs()
        self.assertTrue(ok, error)
        self.assertEqual(
            [call.args for call in action.call_args_list],
            [("OKTO-DAC", "disable"), ("DRC-native", "disable"),
             ("DRC-resamp", "disable")])
        write.assert_called_once_with(APP._CDIN_MPD_OUTPUT_FILE, "DRC-native")

    def test_stop_restores_exactly_the_remembered_output(self):
        with mock.patch.object(APP, "_read_state_str", return_value="DRC-resamp"), \
             mock.patch.object(APP, "_mpd_output_action",
                               return_value=(True, "")) as action:
            ok, error = APP._cdin_restore_mpd_output()
        self.assertTrue(ok, error)
        self.assertEqual(
            [call.args for call in action.call_args_list],
            [("OKTO-DAC", "disable"), ("DRC-native", "disable"),
             ("DRC-resamp", "enable")])


if __name__ == "__main__":
    unittest.main()
