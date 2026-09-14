#!/usr/bin/env python3

"""What `drc.sh` must still record when a device on its path misbehaves.

drc.sh runs under `set -e`, and both bugs these tests cover were the same
mistake: a failing step aborted the run before it could record — or repair —
anything.

`off` talks to MPD, sudo and a cuse device before it is done, and any of those
can fail; a wedged MPD is exactly what tearing `virtual_oss` out from under an
open output produces.  With the saved state written last, one failing `mpc`
discarded the user's choice, and the next boot's `restore` brought DRC back up:
the box came back resampling after having been switched off.

A rate run had the mirror-image problem in its warm-up: the whole retry and
rollback machinery was unreachable, so a DAC that never locked left brutefir up
with every MPD output disabled, silently.
"""

import os
from pathlib import Path
import pty
import subprocess
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]

# A failing `mpc enable` reproduces the wedged-MPD case; every other verb
# succeeds, so the run reaches the point where it used to die.
FAILING_MPC = """#!/bin/sh
case "$1" in
  enable) echo "mpc: MPD error" >&2; exit 1 ;;
esac
exit 0
"""

# A brutefir that starts and stays up, so the run reaches the warm-up.  The
# marker file lets the pgrep stub answer "is brutefir running?" truthfully
# across start/stop, which keeps the teardown loops from spinning out their
# full timeouts.
FAKE_BRUTEFIR = """#!/bin/sh
: > "$DRC_TEST_BF_MARKER"
exit 0
"""

FAKE_PKILL = """#!/bin/sh
rm -f "$DRC_TEST_BF_MARKER"
exit 0
"""

FAKE_PGREP = """#!/bin/sh
case "$*" in
  *brutefir*) [ -e "$DRC_TEST_BF_MARKER" ] && exit 0; exit 1 ;;
esac
exit 1
"""

# The DAC clock never reaches the requested rate, so every verification fails.
WRONG_RATE_SYSCTL = """#!/bin/sh
echo 48000
exit 0
"""


class DrcPowerStateTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.state = root / "state"
        self.stub = root / "stub"
        site = root / "site"
        (site / "configs/flat").mkdir(parents=True)
        self.state.mkdir()
        self.stub.mkdir()

        # Nothing of the real audio chain may be touched, so every external
        # command the teardown reaches for is a stub.  pgrep reports "not
        # running" so the teardown loops fall straight through.
        for name in ("mpc", "sudo", "killall", "pkill", "brutefir", "sysctl"):
            self._stub(name, "#!/bin/sh\nexit 0\n")
        self._stub("pgrep", "#!/bin/sh\nexit 1\n")

        config = root / "omdrc.conf"
        config.write_text(
            f"GEOMETRY=flat\nOMDRC_SITE_DIR={site}\nOMDRC_STATE_DIR={self.state}\n",
            encoding="utf-8")
        self.env = os.environ.copy()
        self.env["OMDRC_CONF"] = str(config)
        self.env["PATH"] = f"{self.stub}{os.pathsep}{self.env['PATH']}"
        dac = root / "dsp.dac"
        dac.touch()
        self.env["OMDRC_DAC_DEV_LINK"] = str(dac)

    def tearDown(self):
        self._tmp.cleanup()

    def _stub(self, name, body):
        path = self.stub / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)

    def _run(self, *args):
        return subprocess.run(
            [str(ROOT / "drc.sh"), *args], env=self.env,
            capture_output=True, text=True, timeout=60)

    def _run_interactive(self, *args, reply):
        """Run drc.sh with stdin/stdout attached to a real pty, so `[ -t 0 ]`
        and `[ -t 1 ]` see a terminal and the off-confirmation prompt fires."""
        primary, secondary = pty.openpty()
        try:
            process = subprocess.Popen(
                [str(ROOT / "drc.sh"), *args], env=self.env,
                stdin=secondary, stdout=secondary, stderr=subprocess.PIPE,
                text=True)
            os.close(secondary)
            os.write(primary, (reply + "\n").encode())
            _, stderr = process.communicate(timeout=60)
            return process.returncode, stderr
        finally:
            os.close(primary)

    def _write_state(self, last_arg="resamp", last_power="on"):
        (self.state / "last_arg").write_text(last_arg + "\n", encoding="utf-8")
        (self.state / "last_power").write_text(last_power + "\n", encoding="utf-8")

    def _source(self):
        return (self.state / "last_source").read_text(encoding="utf-8").strip()

    def _power(self):
        return (self.state / "last_power").read_text(encoding="utf-8").strip()

    def test_off_records_the_off_state(self):
        self._write_state()
        result = self._run("off")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._power(), "off")
        # The remembered rate survives, so turning DRC back on returns to it.
        self.assertEqual(
            (self.state / "last_arg").read_text(encoding="utf-8").strip(), "resamp")

    def test_off_records_the_off_state_even_when_the_teardown_fails(self):
        self._write_state()
        self._stub("mpc", FAILING_MPC)
        result = self._run("off")
        # The chain is down and the choice is recorded, so the run reports the
        # MPD problem instead of failing and discarding the choice.
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("could not switch MPD", result.stderr)
        self.assertEqual(self._power(), "off")

    def test_off_at_a_real_terminal_warns_and_waits_for_yes(self):
        self._write_state()
        returncode, stderr = self._run_interactive("off", reply="yes")
        self.assertEqual(returncode, 0, stderr)
        self.assertIn("8 dB", stderr)
        self.assertEqual(self._power(), "off")

    def test_off_at_a_real_terminal_is_aborted_by_anything_but_yes(self):
        self._write_state()
        returncode, stderr = self._run_interactive("off", reply="no")
        self.assertNotEqual(returncode, 0)
        self.assertIn("aborted", stderr)
        # The choice already recorded by a previous run must survive an
        # aborted attempt to turn DRC off again.
        self.assertEqual(self._power(), "on")

    def test_off_with_a_piped_stdin_does_not_prompt(self):
        """Non-interactive callers (the web UI, systemd, another script) have
        no one to type "yes" and must not hang waiting for one; ordinary
        `subprocess.run` without a pty, as used everywhere else in this file,
        already covers this, but the point is made explicit here."""
        self._write_state()
        result = self._run("off")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Type 'yes'", result.stderr)
        self.assertEqual(self._power(), "off")

    def test_off_assume_yes_skips_the_prompt_even_at_a_terminal(self):
        self._write_state()
        self.env["OMDRC_ASSUME_YES"] = "1"
        # No reply is ever sent; a hang here means the override did not work.
        returncode, stderr = self._run_interactive("off", reply="")
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(self._power(), "off")

    def test_stop_leaves_the_saved_power_state_alone(self):
        self._write_state()
        result = self._run("stop")
        self.assertEqual(result.returncode, 0, result.stderr)
        # `stop` is the service teardown verb: a reboot of a running box must
        # come back up, so it must not look like a user switching DRC off.
        self.assertEqual(self._power(), "on")

    def test_a_warmup_that_never_locks_is_retried_and_rolled_back(self):
        """A failed warm-up must not take the run down with it.

        `warm_until_locked; warm_rc=$?` was killed by `set -e` the moment the
        function returned non-zero — which is every outcome the branches below
        it exist to handle.  The retry, the rollback to the direct DAC and the
        `run_result` log line were all unreachable, and the box was left with
        brutefir up and every MPD output disabled: silent.

        Side effect: like a real run, this rewrites /tmp/brutefir.out.
        """
        self._write_state()
        marker = Path(self._tmp.name) / "brutefir.running"
        self.env["DRC_TEST_BF_MARKER"] = str(marker)
        self._stub("brutefir", FAKE_BRUTEFIR)
        self._stub("pkill", FAKE_PKILL)
        self._stub("pgrep", FAKE_PGREP)
        self._stub("sysctl", WRONG_RATE_SYSCTL)
        (Path(self._tmp.name) / "site/configs/flat/brutefir-192000.conf").write_text(
            "sampling_rate: 192000;\n", encoding="utf-8")
        # No warm-up window: the clock is wrong on the first poll and stays
        # wrong, which is the failure this covers — not how long it waits.
        self.env["DAC_WARMUP_SECS"] = "0"
        self.env["DAC_SETTLE_SECS"] = "0"

        result = self._run("192000")
        self.assertNotEqual(result.returncode, 0)
        log = (self.state / "drc.log").read_text(encoding="utf-8")
        # Every attempt is reported and the last word is the rollback.
        self.assertEqual(log.count("event=verify"), 3, log)
        self.assertIn("result=fail observed=48000 want=192000", log)
        self.assertIn("result=rolled_back", log)
        self.assertIn("rolling back to direct DAC", result.stderr)
        # A chain that never came up must not be recorded as the state to
        # restore: the box was left on the direct DAC, not at this rate.
        self.assertEqual(self._power(), "on")
        self.assertEqual(
            (self.state / "last_arg").read_text(encoding="utf-8").strip(), "resamp")

    def test_restore_stays_off_when_the_saved_power_state_is_off(self):
        self._write_state(last_power="off")
        result = self._run("restore")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Last power state was off", result.stdout)
        self.assertEqual(self._power(), "off")
        # And it says so in the operations log, so a boot that ignored the
        # saved state can be told from a state that was never saved.
        log = (self.state / "drc.log").read_text(encoding="utf-8")
        self.assertIn("event=restore power=off", log)

    def test_bare_rate_reuses_the_saved_design_when_reenabling(self):
        """The rate buttons must not turn an A/B choice back into default."""
        self._write_state(last_arg="resamp @Rscreen", last_power="off")
        config = Path(self._tmp.name) / "site/configs/flat/brutefir-192000@Rscreen.conf"
        config.write_text("sampling_rate: 192000;\n", encoding="utf-8")
        marker = Path(self._tmp.name) / "brutefir.running"
        self.env["DRC_TEST_BF_MARKER"] = str(marker)
        self.env["DAC_WARMUP_SECS"] = "0"
        self.env["DAC_SETTLE_SECS"] = "0"
        self._stub("brutefir", FAKE_BRUTEFIR)
        self._stub("pkill", FAKE_PKILL)
        self._stub("pgrep", FAKE_PGREP)

        result = self._run("resamp")
        # The fake Linux DAC cannot verify, but config selection happens first.
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("config not found", result.stderr)
        log = (self.state / "drc.log").read_text(encoding="utf-8")
        self.assertIn(
            "event=design_reused geometry=flat rate=192000 design=@Rscreen", log)
        self.assertIn("event=run_start mode=resamp rate=192000", log)
        self.assertIn("variant=@Rscreen", log)
        # Failed hardware activation must not destroy the saved tuple either.
        self.assertEqual(
            (self.state / "last_arg").read_text(encoding="utf-8").strip(),
            "resamp @Rscreen")

    def test_cdin_intent_is_saved_before_a_failed_chain_transition(self):
        self._write_state()
        # No 44.1-kHz config exists, so the physical transition fails.  The
        # source selection is user intent and must still survive reboot/retry.
        result = self._run("cdin")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self._source(), "cdin")
        self.assertIn("config not found", result.stderr)

    def test_a_capture_source_writes_the_bridge_its_input_before_starting_it(self):
        """The bridge is started by unit name, so the input it opens can only
        reach it through the file drc.sh writes first.

        A stale file is the failure where the chain is built for one source and
        the bridge captures the other — no error, just the wrong input at the
        wrong rate — so the write is part of the start, not of the config.
        """
        drc = (ROOT / "drc.sh").read_text(encoding="utf-8")
        block = drc.split("start_cdin_linux() {", 1)[1].split("\n}", 1)[0]
        self.assertLess(block.index('write_cdin_env "$src"'),
                        block.index("systemctl_user start"),
                        "the input must be settled before the unit is started")
        # All four values the bridge needs, including the name the panel
        # reports the engaged input by.
        env = drc.split("write_cdin_env() {", 1)[1].split("\n}", 1)[0]
        for key in ("OMDRC_CDIN_SOURCE=", "OMDRC_CDIN_RATE=",
                    "OMDRC_CDIN_CAPTURE_DEVICE=", "OMDRC_CDIN_CAPTURE_SOURCE="):
            self.assertIn(key, env)
        unit = (ROOT / "etc/systemd/user/omdrc-cdin.service.in").read_text(
            encoding="utf-8")
        self.assertIn("EnvironmentFile=-@OMDRC_STATE_DIR@/cdin.env", unit)
        # The '-' matters: a box that has never selected a capture source has
        # no such file, and the unit must still start on the bridge's defaults.
        self.assertNotIn("EnvironmentFile=@OMDRC_STATE_DIR@", unit)

    def test_music_intent_is_saved_before_a_failed_chain_transition(self):
        self._write_state()
        (self.state / "last_source").write_text("cdin\n", encoding="utf-8")
        # An ordinary rate action is also the UI action for leaving CD input.
        # Even a missing config must not leave CD as the next boot's intent.
        result = self._run("192000")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self._source(), "music")
        self.assertIn("config not found", result.stderr)

    def test_transient_stop_does_not_change_the_saved_source(self):
        self._write_state()
        (self.state / "last_source").write_text("cdin\n", encoding="utf-8")
        result = self._run("stop")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._source(), "cdin")

    def test_restore_honours_the_persistent_cdin_source(self):
        self._write_state(last_arg="resamp", last_power="on")
        (self.state / "last_source").write_text("cdin\n", encoding="utf-8")
        result = self._run("restore")
        self.assertNotEqual(result.returncode, 0)  # fixture has no 44.1k config
        self.assertIn("Restoring the CD / S-PDIF input at 44100 Hz", result.stdout)
        self.assertIn("brutefir-44100.conf", result.stderr)

    def test_restore_of_a_source_no_longer_recognised_falls_back_to_music(self):
        """A `last_source` left over from a removed source (e.g. the retired
        analog Line input) must degrade to music rather than crash or wedge
        the chain on an input drc.sh no longer knows how to open."""
        self._write_state(last_arg="resamp", last_power="on")
        (self.state / "last_source").write_text("linein\n", encoding="utf-8")
        result = self._run("restore")
        self.assertNotEqual(result.returncode, 0)  # fixture has no 192k config
        self.assertIn("Restoring last state:", result.stdout)
        self.assertNotIn("Line input", result.stdout)
        self.assertEqual(self._source(), "music")

    def test_mpd_failures_are_bounded_while_the_lock_is_held(self):
        self._write_state()
        self._stub("mpc", "#!/bin/sh\nsleep 20\n")
        self.env["OMDRC_MPC_TIMEOUT"] = "0.1"
        started = time.monotonic()
        result = self._run("off")
        elapsed = time.monotonic() - started
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(elapsed, 3.0, f"mpc was not bounded: {elapsed:.2f}s")
        self.assertEqual(self._power(), "off")


class DrcOffWebConfirmationTest(unittest.TestCase):
    """The web panel's own confirmation for `drc_off`, separate from the
    console prompt drc.sh shows for a human typing at a real terminal."""

    def test_drc_off_requires_confirmation_and_names_the_8db_jump(self):
        text = (ROOT / "omdrc-ctrl/src/commands.conf.in").read_text(encoding="utf-8")
        section = text.split("[drc_off]", 1)[1].split("\n\n", 1)[0]
        self.assertIn("confirm = yes", section)
        self.assertIn("confirm_message", section)
        self.assertIn("8 dB", section)

    def test_index_page_wires_the_per_command_confirm_message(self):
        page = (ROOT / "omdrc-ctrl/src/templates/index.html").read_text(encoding="utf-8")
        self.assertIn("data-confirm-message=", page)
        self.assertIn("btn.dataset.confirmMessage", page)


class DspHeadroomGaugeMarkupTest(unittest.TestCase):
    """The linear DSP-headroom gauge beside the active rate's Apply button."""

    def test_gauge_element_is_rendered_only_for_the_drc_group(self):
        page = (ROOT / "omdrc-ctrl/src/templates/index.html").read_text(encoding="utf-8")
        self.assertIn("class=\"dsp-gauge\"", page)
        self.assertIn("gauge-{{ cmd.id }}", page)
        self.assertIn("group_name == 'drc'", page)

    def test_placement_follows_the_active_rate_and_polls_brutefir_rti(self):
        page = (ROOT / "omdrc-ctrl/src/templates/index.html").read_text(encoding="utf-8")
        self.assertIn("function updateDspGaugePlacement(", page)
        self.assertIn("function renderDspGauge(", page)
        self.assertIn("/drc/brutefir-rti", page)
        self.assertIn("updateDspGaugePlacement(active)", page)


if __name__ == "__main__":
    unittest.main()
