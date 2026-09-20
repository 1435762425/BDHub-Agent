import tempfile
import unittest
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib.operations_workflow import (create_run, finish_stage, save_setting, setting, start_stage,
                                     status, update_checkpoint)  # noqa:E402
from lib.schema_migrations import apply_database  # noqa:E402
from lib.second_cycle import CycleError, CycleStore  # noqa:E402


NOW = datetime(2026, 9, 21, 7, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()  # Monday


class OperationsWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "var").mkdir()
        with CycleStore(self.root / "var/second-cycle.sqlite", lambda: NOW) as store:
            store.plan("bjn-local-research", "it")
        apply_database(self.root, "second-cycle", clock=lambda: NOW)
        self.store = CycleStore(self.root / "var/second-cycle.sqlite", lambda: NOW)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_defaults_are_all_off_and_setting_requests_are_idempotent(self):
        self.assertEqual(setting(self.store)["revision"], 0)
        self.assertFalse(setting(self.store)["automaticOperationsEnabled"])
        saved = save_setting(self.store, "it", "setting-request-0001", 0,
                             {"fullCatalogWeeklyEnabled": True})
        self.assertEqual(saved["revision"], 1)
        self.assertTrue(save_setting(self.store, "it", "setting-request-0001", 0,
                                    {"fullCatalogWeeklyEnabled": True})["duplicate"])
        with self.assertRaisesRegex(CycleError, "request_conflict"):
            save_setting(self.store, "it", "setting-request-0001", 1,
                         {"fullCatalogWeeklyEnabled": False})

    def test_monday_scope_and_complete_catalog_barrier(self):
        save_setting(self.store, "it", "setting-request-0002", 0,
                     {"fullCatalogWeeklyEnabled": True})
        run = create_run(self.store, market="it", trigger_source="manual", scheduled_at=NOW,
                         request_id="workflow-request-0001")
        self.assertEqual(run["applicableSources"], ["selected", "campaign"])
        self.assertEqual(run["stages"][0]["state"], "queued")
        start_stage(self.store, run["runId"], "taplink_clean")
        finish_stage(self.store, run["runId"], "taplink_clean", state="completed",
                     item_count=2, scope={"lists": 2}, payload={"verified": 2}, platform_writes=0)
        start_stage(self.store, run["runId"], "catalog")
        update_checkpoint(self.store, run["runId"], "catalog", "campaign-page",
                          {"page": 3}, {"items": 120})
        with self.assertRaisesRegex(CycleError, "generation_incomplete"):
            finish_stage(self.store, run["runId"], "catalog", state="completed",
                         item_count=120, complete=False)
        result = finish_stage(self.store, run["runId"], "catalog", state="completed",
                              item_count=120, complete=True,
                              scope={"sources": ["selected", "campaign"]})
        self.assertTrue(result["outputGenerationId"].startswith("generation-"))

    def test_home_continuous_switch_updates_the_execution_control_without_starting_it(self):
        saved=save_setting(self.store,"it","setting-request-send",0,{"continuousSendEnabled":True})
        self.assertTrue(saved["continuousSendEnabled"])
        row=self.store.db.execute('SELECT automatic_enabled,run_requested,stop_requested FROM continuous_send_control').fetchone()
        self.assertEqual(tuple(row),(1,0,0))
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM continuous_send_runtime').fetchone()[0],0)

    def test_kalodata_quota_exhaustion_publishes_only_completed_scope(self):
        run = create_run(self.store, market="it", trigger_source="manual", scheduled_at=NOW + 86400,
                         request_id="workflow-request-0002")
        # Tuesday skips cleaning and queues catalog.
        self.assertEqual(run["stages"][0]["state"], "skipped")
        self.assertEqual(run["stages"][1]["state"], "queued")
        for stage in ("catalog", "taplink_prepare"):
            start_stage(self.store, run["runId"], stage)
            finish_stage(self.store, run["runId"], stage, state="completed", item_count=5,
                         complete=True, scope={"pids": 5})
        start_stage(self.store, run["runId"], "kalodata")
        result = finish_stage(self.store, run["runId"], "kalodata", state="quota_exhausted",
                              item_count=4, complete=False,
                              scope={"completedPids": ["1", "2", "3", "4"]},
                              payload={"checkpoint": {"pid": "5", "page": 2}})
        self.assertIsNotNone(result["outputGenerationId"])
        self.assertEqual(status(self.store)["current"]["stages"][4]["state"], "queued")


if __name__ == "__main__":
    unittest.main()
