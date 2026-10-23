"""The fixed-rate bridge must be identified without promising source transparency."""

import importlib.util
from pathlib import Path
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "omdrc_pcm_bridge_status_app", ROOT / "omdrc-ctrl/src/app.py")
APP = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(APP)


class PcmBridgeStatus(unittest.TestCase):
    def test_process_rate_and_unprivileged_node_owner(self):
        with mock.patch.object(APP, "_ps_arg_lines", return_value=[
            "sudo -n /usr/local/sbin/omdrc-pcm-bridge --rate 192000 --user giacomo"
        ]):
            self.assertEqual(APP._pcm_bridge_rate(), 192000)

    def test_resampled_source_is_not_reported_as_bit_perfect(self):
        rate, path = APP._pcm_bridge_path_status(96000, 192000, 192000)
        self.assertEqual(rate["kind"], "resampled")
        self.assertEqual(path["kind"], "resampled")
        self.assertIn("not bit-transparent", path["detail"])

    def test_mismatched_fir_rate_is_an_error(self):
        rate, path = APP._pcm_bridge_path_status(192000, 192000, 96000)
        self.assertEqual(rate["kind"], "mismatch")
        self.assertEqual(path["kind"], "mismatch")


if __name__ == "__main__":
    unittest.main()
