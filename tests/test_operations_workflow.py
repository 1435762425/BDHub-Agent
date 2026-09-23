import tempfile
import unittest
import sys
import os
import importlib.util
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib.operations_workflow import (create_run, finish_stage, resume_kalodata_preflight,
                                     resume_short_names, save_setting, setting, start_stage,
                                     status, update_checkpoint)  # noqa:E402
from lib.schema_migrations import apply_database  # noqa:E402
from lib.second_cycle import CycleError, CycleStore  # noqa:E402
from lib.template_library import review_send_template,send_template_reviews  # noqa:E402

_cli_spec=importlib.util.spec_from_file_location("operations_workflow_cli",ROOT/"scripts/operations-workflow.py")
_cli=importlib.util.module_from_spec(_cli_spec)
_cli_spec.loader.exec_module(_cli)


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
        with self.assertRaisesRegex(CycleError,'template_approval_required'):
            save_setting(self.store,"it","setting-request-send-blocked",0,{"continuousSendEnabled":True})
        for item in send_template_reviews(self.store,ROOT)['items'][:10]:
            review_send_template(self.store,ROOT,'workflow-review-'+item['templateId'],
                                 item['templateId'],'approved',item['revision'])
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

    def test_stop_rejects_foreign_market_before_changing_run(self):
        run=create_run(self.store,market="it",trigger_source="manual",scheduled_at=NOW,
                       request_id="workflow-stop-scope-0001")
        with self.assertRaisesRegex(CycleError,"workflow_market_mismatch"):
            _cli.request_stop_for_market(self.store,"br",run["runId"],"queued")
        self.assertEqual(self.store.db.execute("SELECT state FROM workflow_run WHERE run_id=?",(run["runId"],)).fetchone()[0],"queued")
        stopped=_cli.request_stop_for_market(self.store,"it",run["runId"],"queued")
        self.assertEqual(stopped["market"],"it")

    def _short_name_blocked_run(self, *, writes=0, error="catalog_short_names_incomplete"):
        run = create_run(self.store, market="it", trigger_source="manual", scheduled_at=NOW + 86400,
                         request_id="workflow-short-name-recovery-0001", sources=["campaign"])
        start_stage(self.store, run["runId"], "catalog")
        finish_stage(self.store, run["runId"], "catalog", state="completed", item_count=5,
                     scope={"campaign": 5}, platform_writes=2)
        generation = status(self.store)["current"]["stages"][1]["outputGenerationId"]
        start_stage(self.store, run["runId"], "taplink_prepare", input_generation_id=generation)
        finish_stage(self.store, run["runId"], "taplink_prepare", state="needs_human",
                     platform_writes=writes, error_code=error)
        return run["runId"]

    def test_short_name_recovery_requeues_original_stage_once_and_preserves_evidence(self):
        run_id = self._short_name_blocked_run()
        resumed = resume_short_names(self.store, "it", run_id, "resume-short-names-0001", root=self.root)
        self.assertFalse(resumed["duplicate"])
        self.assertEqual(resumed["state"], "running")
        self.assertEqual(resumed["stages"][2]["state"], "queued")
        self.assertEqual(resumed["stages"][1]["platformWrites"], 2)
        self.assertEqual(resumed["stages"][1]["state"], "completed")
        self.assertEqual(resumed["stages"][3]["state"], "waiting_upstream")
        evidence = self.store.db.execute(
            "SELECT value_json FROM workflow_checkpoint WHERE run_id=? AND stage='taplink_prepare' "
            "AND checkpoint_key='recovery:catalog_short_names_incomplete'", (run_id,),
        ).fetchone()[0]
        self.assertIn('catalog_short_names_incomplete', evidence)
        self.assertTrue(resume_short_names(self.store, "it", run_id,
                                           "resume-short-names-0001", root=self.root)["duplicate"])
        with self.assertRaisesRegex(CycleError, "recovery_already_requested"):
            resume_short_names(self.store, "it", run_id, "resume-short-names-0002", root=self.root)

    def test_short_name_recovery_rejects_unresolved_gap_and_prior_writes(self):
        from unittest.mock import patch
        run_id = self._short_name_blocked_run()
        with patch("lib.catalog_names.gap", return_value={"scope": 1, "ready": 0, "missing": 1,
                                                          "invalidSourceTitles": 0}):
            with self.assertRaisesRegex(CycleError, "catalog_short_names_incomplete"):
                resume_short_names(self.store, "it", run_id, "resume-short-names-0003", root=self.root)
        self.assertEqual(status(self.store)["current"]["state"], "needs_human")
        with self.store.tx():
            self.store.db.execute("UPDATE workflow_stage_run SET platform_writes=1 WHERE run_id=? "
                                  "AND stage='taplink_prepare'", (run_id,))
        with self.assertRaisesRegex(CycleError, "recovery_state_invalid"):
            resume_short_names(self.store, "it", run_id, "resume-short-names-0003", root=self.root)

    def test_short_name_recovery_rejects_other_error_market_and_active_claim(self):
        run_id = self._short_name_blocked_run()
        with self.assertRaisesRegex(CycleError, "market_mismatch"):
            resume_short_names(self.store, "br", run_id, "resume-short-names-0004", root=self.root)
        stage_id = status(self.store)["current"]["stages"][2]["stageRunId"]
        with self.store.tx():
            self.store.db.execute("INSERT INTO workflow_stage_claim VALUES(?,?,?,?,?,?,?)",
                                  (stage_id, "test-owner", 1, NOW + 300, NOW, 12345, NOW))
        with self.assertRaisesRegex(CycleError, "recovery_claim_active"):
            resume_short_names(self.store, "it", run_id, "resume-short-names-0004", root=self.root)
        with self.store.tx():
            self.store.db.execute("DELETE FROM workflow_stage_claim WHERE stage_run_id=?", (stage_id,))
            self.store.db.execute("UPDATE workflow_run SET error_code='result_unknown' WHERE run_id=?", (run_id,))
        with self.assertRaisesRegex(CycleError, "recovery_state_invalid"):
            resume_short_names(self.store, "it", run_id, "resume-short-names-0004", root=self.root)

    def _kalodata_preflight_failed_run(self):
        run = create_run(self.store, market="it", trigger_source="manual", scheduled_at=NOW + 86400,
                         request_id="workflow-kalodata-recovery-0001", sources=["campaign"])
        for stage in ("catalog", "taplink_prepare"):
            prior = next((row["outputGenerationId"] for row in reversed(status(self.store)["current"]["stages"])
                          if row["outputGenerationId"]), None)
            start_stage(self.store, run["runId"], stage, input_generation_id=prior)
            finish_stage(self.store, run["runId"], stage, state="completed", item_count=5)
        generation = status(self.store)["current"]["stages"][2]["outputGenerationId"]
        start_stage(self.store, run["runId"], "kalodata", input_generation_id=generation)
        finish_stage(self.store, run["runId"], "kalodata", state="failed", item_count=0,
                     platform_writes=0, error_code="kalodata-sales_failed")
        return run["runId"]

    def test_kalodata_preflight_recovery_only_requeues_original_stage(self):
        run_id = self._kalodata_preflight_failed_run()
        resumed = resume_kalodata_preflight(self.store, "it", run_id,
                                            "resume-kalodata-preflight-0001", root=self.root)
        self.assertEqual(resumed["state"], "running")
        self.assertEqual(resumed["stages"][3]["state"], "queued")
        self.assertEqual(resumed["stages"][1]["state"], "completed")
        self.assertEqual(resumed["stages"][2]["state"], "completed")
        self.assertEqual(resumed["stages"][4]["state"], "waiting_upstream")
        self.assertTrue(resume_kalodata_preflight(self.store, "it", run_id,
                                                  "resume-kalodata-preflight-0001", root=self.root)["duplicate"])
        with self.assertRaisesRegex(CycleError, "recovery_already_requested"):
            resume_kalodata_preflight(self.store, "it", run_id,
                                      "resume-kalodata-preflight-0002", root=self.root)

    def test_kalodata_preflight_recovery_rejects_possible_read_or_writes(self):
        run_id = self._kalodata_preflight_failed_run()
        status_file = self.root / "var/leads-run.json"
        status_file.write_text("{}")
        os.utime(status_file, (NOW + 1, NOW + 1))
        with self.assertRaisesRegex(CycleError, "external_read_possible"):
            resume_kalodata_preflight(self.store, "it", run_id,
                                      "resume-kalodata-preflight-0003", root=self.root)
        os.utime(status_file, (NOW - 1, NOW - 1))
        with self.store.tx():
            self.store.db.execute("UPDATE workflow_stage_run SET platform_writes=1 WHERE run_id=? "
                                  "AND stage='kalodata'", (run_id,))
        with self.assertRaisesRegex(CycleError, "recovery_state_invalid"):
            resume_kalodata_preflight(self.store, "it", run_id,
                                      "resume-kalodata-preflight-0003", root=self.root)


if __name__ == "__main__":
    unittest.main()
