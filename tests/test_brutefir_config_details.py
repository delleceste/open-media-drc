#!/usr/bin/env python3
"""The live BruteFIR configuration page reports exact runtime headroom."""

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "omdrc_config_app", ROOT / "omdrc-ctrl/src/app.py")
APP = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(APP)


class BrutefirConfigDetailsTest(unittest.TestCase):
    def test_attenuation_endpoint_has_no_hard_coded_fallback(self):
        with mock.patch.object(APP, "_BRUTEFIR_ATTENUATION_FILE", "/missing/state"), \
             mock.patch.object(APP, "_selected_brutefir_conf", return_value=None):
            response = APP.app.test_client().get("/drc/attenuation")

        data = response.get_json()
        self.assertFalse(data["ok"])
        self.assertEqual(response.status_code, 503)
        self.assertIn("selected BruteFIR configuration", data["error"])

    def test_attenuation_endpoint_uses_active_configuration_on_first_run(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "brutefir-192000@multipos.fdw6.conf"
            config.write_text(
                'coeff "c-l" { filename: "left.raw"; attenuation: 8.0; };\n'
                'coeff "c-r" { filename: "right.raw"; attenuation: 8.0; };\n',
                encoding="utf-8")
            process = {"config": str(config)}
            with mock.patch.object(APP, "_BRUTEFIR_ATTENUATION_FILE", "/missing/state"), \
                 mock.patch.object(APP, "_active_brutefir_process", return_value=process):
                response = APP.app.test_client().get("/drc/attenuation")

        data = response.get_json()
        self.assertEqual(data["db"], 8.0)
        self.assertEqual(data["source"], "configuration")

    def test_attenuation_endpoint_uses_selected_configuration_while_stopped(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "brutefir-192000@multipos.fdw6.conf"
            config.write_text(
                'coeff "c-l" { filename: "left.raw"; attenuation: 6.4; };\n'
                'coeff "c-r" { filename: "right.raw"; attenuation: 6.4; };\n',
                encoding="utf-8")
            with mock.patch.object(APP, "_BRUTEFIR_ATTENUATION_FILE", "/missing/state"), \
                 mock.patch.object(APP, "_active_brutefir_process", return_value=None), \
                 mock.patch.object(APP, "_selected_brutefir_conf", return_value=str(config)):
                response = APP.app.test_client().get("/drc/attenuation")

        data = response.get_json()
        self.assertEqual(data["db"], 6.4)
        self.assertEqual(data["source"], "configuration")
        self.assertFalse(data["running"])

    def test_slider_saves_total_and_sends_only_delta_from_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "brutefir-192000.conf"
            config.write_text(
                'coeff "c-l" { filename: "left.raw"; attenuation: 8.0; };\n'
                'coeff "c-r" { filename: "right.raw"; attenuation: 8.0; };\n',
                encoding="utf-8")
            state = root / "attenuation"
            process = {"config": str(config)}
            with mock.patch.object(APP, "_BRUTEFIR_ATTENUATION_FILE", str(state)), \
                 mock.patch.object(APP, "_active_brutefir_process", return_value=process), \
                 mock.patch.object(APP, "_brutefir_cli") as cli:
                response = APP.app.test_client().post(
                    "/drc/attenuation", json={"db": 4.0})

            self.assertEqual(state.read_text(encoding="utf-8"), "4.0\n")
        self.assertTrue(response.get_json()["ok"])
        cli.assert_called_once_with(
            "cfoa drc_l left_out -4.0; cfoa drc_r right_out -4.0")

    def test_headroom_is_calculated_from_the_current_raw_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "L.raw"
            np.asarray([2.0, 0.0], dtype="<f8").tofile(path)
            result = APP._raw_filter_headroom(str(path), "FLOAT64_LE")
        self.assertEqual(result["taps"], 2)
        self.assertAlmostEqual(result["peak_gain_db"], 6.0206, places=4)
        self.assertEqual(result["safety_margin_db"], 1.0)
        self.assertEqual(result["safe_attenuation_db"], 7.1)

    def test_endpoint_follows_the_running_command_config_and_filters(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_dir = root / "configs/120.blue"
            filter_dir = root / "filters/120.blue/48000/test-design"
            config_dir.mkdir(parents=True)
            filter_dir.mkdir(parents=True)
            left = filter_dir / "L.raw"
            right = filter_dir / "R.raw"
            np.asarray([2.0, 0.0], dtype="<f8").tofile(left)
            np.asarray([1.0, 0.0], dtype="<f8").tofile(right)
            config = config_dir / "brutefir-48000@test-design.conf"
            config.write_text(
                f'''sampling_rate: 48000;
coeff "c-l" {{ filename: "{left}"; format: "FLOAT64_LE"; attenuation: 7.1; }};
coeff "c-r" {{ filename: "{right}"; format: "FLOAT64_LE"; attenuation: 7.1; }};
''', encoding="utf-8")
            command = f"/usr/local/bin/brutefir {config} -daemon"
            process = {
                "command_line": command,
                "argv": ["/usr/local/bin/brutefir", str(config), "-daemon"],
                "config": str(config),
            }
            with mock.patch.object(APP, "_BRUTEFIR_ATTENUATION_FILE", "/missing/state"), \
                 mock.patch.object(APP, "_active_brutefir_process", return_value=process):
                response = APP.app.test_client().get("/drc/brutefir-config")

        data = response.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["command_line"], command)
        self.assertEqual(data["config_path"], str(config))
        self.assertEqual(data["geometry"], "120.blue")
        self.assertEqual(data["design_id"], "test-design")
        self.assertEqual(data["configured_attenuation_db"], 7.1)
        self.assertEqual(data["effective_attenuation_db"], 7.1)
        self.assertEqual(data["effective_attenuation_source"], "configuration")
        self.assertEqual(data["safe_attenuation_db"], 7.1)
        self.assertTrue(data["headroom_safe"])
        self.assertEqual([item["filename"] for item in data["filters"]],
                         [str(left), str(right)])
        self.assertTrue(all(item["is_raw"] for item in data["filters"]))

    def test_config_page_separates_saved_effective_and_file_attenuation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "filter.raw"
            np.asarray([2.0, 0.0], dtype="<f8").tofile(raw)
            config = root / "brutefir-48000.conf"
            config.write_text(
                f'coeff "c-l" {{ filename: "{raw}"; attenuation: 8.0; }};\n',
                encoding="utf-8")
            state = root / "attenuation"
            state.write_text("4.0\n", encoding="utf-8")
            process = {"command_line": f"brutefir {config}", "config": str(config)}
            with mock.patch.object(APP, "_BRUTEFIR_ATTENUATION_FILE", str(state)), \
                 mock.patch.object(APP, "_active_brutefir_process", return_value=process):
                data = APP.app.test_client().get("/drc/brutefir-config").get_json()

        self.assertEqual(data["effective_attenuation_db"], 4.0)
        self.assertEqual(data["effective_attenuation_source"], "saved")
        self.assertEqual(data["configured_attenuation_db"], 8.0)
        self.assertFalse(data["headroom_safe"])

    def test_process_match_ignores_commands_that_only_mention_brutefir(self):
        lines = [
            "vim /tmp/brutefir-48000.conf",
            "grep brutefir /tmp/processes",
            "brutefir /active/brutefir-48000@design.conf -daemon",
        ]
        with mock.patch.object(APP, "_ps_arg_lines", return_value=lines):
            process = APP._active_brutefir_process()
        self.assertEqual(process["config"], "/active/brutefir-48000@design.conf")
        self.assertEqual(process["command_line"], lines[-1])

    def test_control_button_opens_a_new_configuration_tab(self):
        page = (ROOT / "omdrc-ctrl/src/templates/index.html").read_text(encoding="utf-8")
        details = (ROOT / "omdrc-ctrl/src/templates/brutefir_config.html").read_text(
            encoding="utf-8")
        self.assertIn('href="/brutefir-config" target="_blank"', page)
        self.assertIn("Geometry: ${data.geometry} · Filter design: ${design}", details)
        self.assertIn("Attenuation set in BruteFIR", details)
        self.assertIn("Attenuation in BruteFIR configuration files", details)
        self.assertIn("Safe attenuation, calculated now", details)
        self.assertIn("Live peak, reported by BruteFIR", details)


class BrutefirPeakStatusTest(unittest.TestCase):
    """BruteFIR only writes a "peak: ..." line when its held per-output peak
    sets a new session-high (see bfrun.c's print_overflows); between records
    `show_progress: true` fills the same file with one "rti: ..." line per
    second, so the parser has to see past those to find the last real one."""

    def test_no_log_means_no_record(self):
        with tempfile.TemporaryDirectory() as directory:
            status = APP._brutefir_peak_status(str(Path(directory) / "missing.out"))
        self.assertEqual(status, {"available": False})

    def test_last_peak_line_wins_over_older_ones_and_filler(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "brutefir.out"
            path.write_text(
                "peak: 0/0/-12.30 1/0/-11.90 \n" +
                "rti: 0.412\n" * 50 +
                "peak: 0/0/-1.50 1/1/+0.80 \n" +
                "rti: 0.418\n" * 20,
                encoding="utf-8")
            status = APP._brutefir_peak_status(str(path))
        self.assertTrue(status["available"])
        self.assertAlmostEqual(status["peak_db"], 0.80)
        self.assertTrue(status["clipped"])
        self.assertEqual(
            status["channels"],
            [{"channel": 0, "overflow_count": 0, "peak_db": -1.50},
             {"channel": 1, "overflow_count": 1, "peak_db": 0.80}])

    def test_minus_inf_before_any_sample_is_not_a_number(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "brutefir.out"
            path.write_text("peak: 0/0/-Inf 1/0/-Inf \n", encoding="utf-8")
            status = APP._brutefir_peak_status(str(path))
        self.assertTrue(status["available"])
        self.assertIsNone(status["peak_db"])
        self.assertFalse(status["clipped"])

    def test_endpoint_serves_the_parsed_peak(self):
        parsed = {"available": True, "peak_db": -6.0, "clipped": False, "channels": []}
        with mock.patch.object(APP, "_brutefir_peak_status", return_value=parsed):
            response = APP.app.test_client().get("/drc/brutefir-peak")
        data = response.get_json()
        self.assertTrue(data["available"])
        self.assertEqual(data["peak_db"], -6.0)


class BrutefirRtiStatusTest(unittest.TestCase):
    """The DSP-headroom gauge reads BruteFIR's own "rti: ..." line -- a
    once-a-second scheduling figure (`show_progress: true`), distinct from
    the audio-level peak-hold above."""

    def test_no_log_means_no_record(self):
        with tempfile.TemporaryDirectory() as directory:
            status = APP._brutefir_rti_status(str(Path(directory) / "missing.out"))
        self.assertEqual(status, {"available": False})

    def test_last_line_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "brutefir.out"
            path.write_text("rti: 0.310\nrti: 0.322\nrti: 0.298\n", encoding="utf-8")
            status = APP._brutefir_rti_status(str(path))
        self.assertEqual(status, {"available": True, "rti": 0.298, "full_processing": True})

    def test_not_full_processing_reports_no_number(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "brutefir.out"
            path.write_text(
                "rti: 0.310\nrti: not full processing - no rti update\n", encoding="utf-8")
            status = APP._brutefir_rti_status(str(path))
        self.assertEqual(status,
                         {"available": True, "rti": None, "full_processing": False})

    def test_endpoint_serves_the_parsed_rti(self):
        parsed = {"available": True, "rti": 0.42, "full_processing": True}
        with mock.patch.object(APP, "_brutefir_rti_status", return_value=parsed):
            response = APP.app.test_client().get("/drc/brutefir-rti")
        data = response.get_json()
        self.assertTrue(data["available"])
        self.assertEqual(data["rti"], 0.42)


if __name__ == "__main__":
    unittest.main()
