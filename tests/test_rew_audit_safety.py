#!/usr/bin/env python3

from contextlib import nullcontext
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "rew_mdat_audit", ROOT / "scripts/rew_mdat_audit.py")
AUDIT = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(AUDIT)


class FakeApi:
    def __init__(self):
        self.loaded_path = None

    def request(self, path, value=None):
        if path == "/measurements/command":
            self.loaded_path = Path(value["parameters"][0])
            return {"message": "Completed"}
        if path in ("/application/errors", "/application/warnings"):
            return []
        if path == "/measurements":
            return {"1": {"title": "Selected trace", "uuid": "trace-uuid"}}
        return {"message": "ok"}


class RewAuditSafetyTest(unittest.TestCase):
    def test_original_project_path_is_never_given_to_rew(self):
        fake_api = FakeApi()
        fake_process = mock.Mock(pid=321)
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "source.mdat"
            project.write_bytes(b"original-project")
            before = project.stat()
            with (mock.patch.object(AUDIT, "exclusive_rew_audit", return_value=nullcontext()),
                  mock.patch.object(AUDIT, "start_rew",
                                    return_value=(fake_process, fake_api, "test", {4321})),
                  mock.patch.object(AUDIT, "stop_rew"),
                  mock.patch.object(AUDIT, "rew_java_pids", return_value={4321})):
                report = AUDIT.audit_project(
                    project, {"measurement_left": "Selected trace"}, {}, {}, "rew")
            self.assertNotEqual(fake_api.loaded_path, project)
            self.assertEqual(fake_api.loaded_path.name, "project-copy.mdat")
            self.assertFalse(fake_api.loaded_path.exists())
            self.assertEqual(project.read_bytes(), b"original-project")
            self.assertEqual(project.stat().st_mtime_ns, before.st_mtime_ns)
            self.assertTrue(report["project"]["loaded_from_private_copy"])

    def test_existing_rew_process_refuses_audit_before_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / "audit.lock"
            with (mock.patch.object(AUDIT, "LOCK_PATH", lock),
                  mock.patch.object(AUDIT, "rew_java_pids", return_value={9876})):
                with self.assertRaisesRegex(AUDIT.AuditError, "already running"):
                    with AUDIT.exclusive_rew_audit():
                        self.fail("unsafe audit body was entered")

    def test_second_auditor_cannot_take_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / "audit.lock"
            with (mock.patch.object(AUDIT, "LOCK_PATH", lock),
                  mock.patch.object(AUDIT, "rew_java_pids", return_value=set())):
                with AUDIT.exclusive_rew_audit():
                    with self.assertRaisesRegex(AUDIT.AuditError, "concurrent audits"):
                        with AUDIT.exclusive_rew_audit():
                            self.fail("second auditor entered")


if __name__ == "__main__":
    unittest.main()
