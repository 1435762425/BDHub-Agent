"""Fixed-cohort planning and stop behavior; subprocesses are never launched."""
import contextlib
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "profile_second_batch_subject",
    Path(__file__).resolve().parents[1] / "scripts/probe-italy-second-profiles.py",
)
batch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(batch)


def cohort():
    return {"market": "it", "records": [{
        "externalId": f"kd-{index:02d}", "handle": f"creator.{index:02d}",
        "localOecHint": str(1000 + index),
    } for index in range(1, 49)]}


def observed():
    return {"market": "it", "account": "acc6", "status": "completed", "targets": [{
        "externalId": f"kd-{index:02d}", "requestedHandle": f"creator.{index:02d}",
        "status": "unresolved" if index == 2 else "completed",
    } for index in range(1, 4)]}


class ProfileSecondBatchTests(unittest.TestCase):
    def test_plan_excludes_both_completed_and_unresolved_without_retry(self):
        source = cohort()
        source["records"][3]["handle"] = " @Creator.04 "
        jobs = batch.plan(source, observed())
        self.assertEqual(len(jobs), 15)
        self.assertTrue(all(len(job) == 3 for job in jobs))
        targets = [target for job in jobs for target in job]
        self.assertEqual(len({target["externalId"] for target in targets}), 45)
        self.assertFalse({"kd-01", "kd-02", "kd-03"} & {target["externalId"] for target in targets})
        self.assertEqual(targets[0], {
            "ref": "it-second-04", "handle": "creator.04", "externalId": "kd-04",
            "historicalOecHint": "1004",
        })
        self.assertEqual(targets[-1]["ref"], "it-second-48")

    def test_wrong_market_or_cohort_size_is_rejected(self):
        for change in ("source_market", "source_size", "initial_market", "initial_status"):
            with self.subTest(change=change):
                source, initial = cohort(), observed()
                if change == "source_market":
                    source["market"] = "mx"
                elif change == "source_size":
                    source["records"].pop()
                elif change == "initial_market":
                    initial["market"] = "mx"
                else:
                    initial["status"] = "blocked"
                with self.assertRaises(ValueError):
                    batch.plan(source, initial)

    def test_duplicate_source_identity_or_normalized_handle_is_rejected(self):
        for key in ("externalId", "handle"):
            with self.subTest(key=key):
                source = cohort()
                source["records"][-1][key] = source["records"][0][key]
                if key == "handle":
                    source["records"][-1][key] = " @" + source["records"][-1][key].upper() + " "
                with self.assertRaisesRegex(ValueError, "duplicate"):
                    batch.plan(source, observed())

    def test_duplicate_initial_identity_is_not_silently_collapsed(self):
        initial = observed()
        hidden = deepcopy(initial["targets"][0])
        hidden["requestedHandle"] = "another.creator"
        initial["targets"].insert(0, hidden)
        with self.assertRaises(ValueError):
            batch.plan(cohort(), initial)

    def test_observation_outside_cohort_or_wrong_handle_or_unfinished_is_rejected(self):
        for change in ("outside", "handle", "unfinished", "empty"):
            with self.subTest(change=change):
                initial = observed()
                if change == "outside":
                    initial["targets"][0]["externalId"] = "outside-cohort"
                elif change == "handle":
                    initial["targets"][0]["requestedHandle"] = "wrong.creator"
                elif change == "unfinished":
                    initial["targets"][0]["status"] = "inflight"
                else:
                    initial["targets"] = []
                with self.assertRaises(ValueError):
                    batch.plan(cohort(), initial)

    def run_fixture(self, root, fake_run, *, initial=None):
        source_path, initial_path, output = root / "source.json", root / "initial.json", root / "run"
        source_path.write_text(json.dumps(cohort()), encoding="utf-8")
        initial_path.write_text(json.dumps(initial or observed()), encoding="utf-8")
        with patch.object(batch, "VAR", root), patch.object(batch.subprocess, "run", side_effect=fake_run) as runner, \
                patch.object(batch.sys, "argv", ["batch", "--source", str(source_path), "--initial", str(initial_path), "--output", str(output)]), \
                contextlib.redirect_stdout(io.StringIO()):
            result = batch.main()
        return result, runner, output

    def test_nonzero_child_or_blocked_report_stops_all_later_batches(self):
        for status, returncode in (("blocked", 0), ("completed", 2)):
            with self.subTest(status=status, returncode=returncode), tempfile.TemporaryDirectory() as temporary:
                calls = []

                def fake_run(command, **_kwargs):
                    calls.append(command)
                    batch_output = Path(command[command.index("--output") + 1])
                    batch_output.mkdir()
                    (batch_output / "report.private.json").write_text(json.dumps({
                        "status": "completed" if len(calls) == 1 else status,
                        "targets": [],
                    }), encoding="utf-8")
                    return SimpleNamespace(returncode=0 if len(calls) == 1 else returncode)

                result, runner, output = self.run_fixture(Path(temporary), fake_run)
                self.assertEqual(result, 2)
                self.assertEqual(runner.call_count, 2)
                self.assertFalse((output / "batch-03-targets.private.json").exists())
                self.assertTrue(all(command[command.index("--account") + 1] == "acc6" for command in calls))

    def test_missing_child_report_stops_without_retry(self):
        with tempfile.TemporaryDirectory() as temporary:
            result, runner, _ = self.run_fixture(Path(temporary), lambda *_args, **_kwargs: SimpleNamespace(returncode=0))
            self.assertEqual(result, 2)
            self.assertEqual(runner.call_count, 1)

    def test_account_change_is_rejected_before_output_or_process(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            initial = observed()
            initial["account"] = "acc7"
            with self.assertRaisesRegex(ValueError, "same account"):
                self.run_fixture(root, lambda *_args, **_kwargs: self.fail("subprocess must not run"), initial=initial)
            self.assertFalse((root / "run").exists())


if __name__ == "__main__":
    unittest.main()
