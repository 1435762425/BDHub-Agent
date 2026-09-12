import contextlib
import importlib.util
import io
import json
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest
from unittest.mock import patch, Mock

SPEC = importlib.util.spec_from_file_location("profile_probe", Path(__file__).resolve().parents[1] / "scripts/probe-italy-profile.py")
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class ProfileProbeTests(unittest.TestCase):
    def test_verification_header_overrides_success_looking_status(self):
        result = probe.classify_response(200, {"bdturing-verify": "opaque", "x-tt-system-error": "3"}, {"code": 0})
        self.assertFalse(result["allowed"])
        self.assertTrue(result["verificationRequired"])
        self.assertTrue(result["systemError3"])
        self.assertNotIn("opaque", json.dumps(result))

    def test_only_explicit_success_is_accepted(self):
        self.assertTrue(probe.classify_response(200, {}, {"code": 0})["allowed"])
        for status, headers, payload in [(200, {}, {"code": False}), (200, {}, {"code": "0"}), (200, {}, None),
                                         (429, {}, {"code": 0}), (401, {}, {"code": 0}),
                                         (200, {}, {"code": 10000}), (200, {"x-tt-system-error": "3"}, {"code": 100000})]:
            self.assertFalse(probe.classify_response(status, headers, payload)["allowed"])

    def test_parent_cancellation_cleans_up_owned_process_group(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            child = Mock(pid=999991)
            child.wait.side_effect = [KeyboardInterrupt(), 0]
            with patch.object(probe, "VAR", root), patch.object(probe.subprocess, "Popen", return_value=child), \
                    patch.object(probe.os, "killpg") as kill, patch.object(probe.sys, "argv", ["probe"]):
                with self.assertRaises(KeyboardInterrupt):
                    probe.main()
            kill.assert_called_once_with(child.pid, signal.SIGTERM)
            self.assertEqual(child.wait.call_count, 2)

    def test_hard_timeout_preserves_diagnostic_and_ends_child(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            child = Mock(pid=999992)
            child.wait.side_effect = [subprocess.TimeoutExpired("probe", 55), subprocess.TimeoutExpired("probe", 3), 0]
            with patch.object(probe, "VAR", root), patch.object(probe.subprocess, "Popen", return_value=child), \
                    patch.object(probe.os, "killpg") as kill, patch.object(probe.sys, "argv", ["probe"]), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(probe.main(), 2)
            self.assertEqual(kill.call_args_list[0].args, (child.pid, signal.SIGTERM))
            self.assertEqual(kill.call_args_list[1].args, (child.pid, signal.SIGKILL))
            report = json.loads(next(root.glob("*/summary.json")).read_text())
            self.assertEqual(report["status"], "bounded_timeout")
            self.assertEqual(report["realSends"], 0)

    def test_existing_evidence_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "existing"
            output.mkdir()
            old = output / "report.private.json"
            old.write_text('{"status":"original"}')
            with patch.object(probe, "VAR", root), patch.object(probe.sys, "argv", ["probe", "--output", str(output)]):
                with self.assertRaisesRegex(ValueError, "immutable"):
                    probe.main()
            self.assertEqual(json.loads(old.read_text())["status"], "original")


if __name__ == "__main__":
    unittest.main()
