import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
import unittest.mock
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "scripts"))

from lib import state_retention  # noqa: E402
from lib.second_cycle import digest  # noqa: E402

NOW = time.time()
DAY = 86400


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project"
        self.var = self.root / "var"
        self.var.mkdir(parents=True)
        self.archive_root = Path(self.temp.name) / "archives"
        self.open_run = "workflow-open"
        self.open_source = "it-global-20260923-" + digest([self.open_run, "selected"])[:12]
        self._second_cycle()
        self._global_source()
        self._files()
        self._identities()

    def tearDown(self):
        self.temp.cleanup()

    def _second_cycle(self):
        with closing(sqlite3.connect(self.var / "second-cycle.sqlite")) as db, db:
            db.executescript("""
                CREATE TABLE catalog(id TEXT PRIMARY KEY,plan_id TEXT,source TEXT,observed REAL,state TEXT,payload TEXT);
                CREATE TABLE catalog_head(plan_id TEXT,source TEXT,snapshot_id TEXT);
                CREATE TABLE workflow_run(run_id TEXT PRIMARY KEY,state TEXT);
                CREATE TABLE workflow_stage_run(stage_run_id TEXT PRIMARY KEY,run_id TEXT,checkpoint_json TEXT,counts_json TEXT);
                CREATE TABLE account_identity_generation(generation_id TEXT PRIMARY KEY,account TEXT,browser_ref TEXT,
                                                         state TEXT,published_at REAL);""")
            for number in range(1, 6):
                db.execute("INSERT INTO catalog VALUES(?,?,?,?,?,?)",
                           (f"snap-{number}", "plan-uk", "live-uk-campaign", float(number), "complete",
                            json.dumps({"n": number})))
            db.execute("INSERT INTO catalog_head VALUES('plan-uk','live-uk-campaign','snap-5')")
            db.execute("INSERT INTO workflow_run VALUES(?,?)", (self.open_run, "needs_human"))
            db.execute("INSERT INTO workflow_run VALUES('workflow-done','completed')")
            db.execute("INSERT INTO workflow_stage_run VALUES('stage-open',?,?,NULL)",
                       (self.open_run, json.dumps({"report": "var/campaign-collect-referenced.json"})))
            for index, (generation, published) in enumerate((("gen-old", 1), ("gen-prev", 2), ("gen-cur", 3))):
                db.execute("INSERT INTO account_identity_generation VALUES(?,?,?,?,?)",
                           (f"row-{index}", "acc4", f"project-browser:{generation}", "published", float(published)))

    def _global_source(self):
        runs = [("old-a", "completed", 1), ("old-b", "completed", 2), ("stopped-c", "stopped", 3),
                ("base", "accepted_partial", 5), ("collecting-d", "collecting", 7), (self.open_source, "stopped", 8),
                ("refresh", "completed", 9), ("coverage-head", "completed", 10)]
        overlay = {"coverageOverlay": {"baselineRunId": "base", "refreshRunId": "refresh"}}
        with closing(sqlite3.connect(self.var / "global-source.sqlite")) as db, db:
            db.executescript("""
                CREATE TABLE global_source_run(id TEXT PRIMARY KEY,scope TEXT,state TEXT,created REAL);
                CREATE TABLE global_source_head(scope_hash TEXT PRIMARY KEY,run_id TEXT);
                CREATE TABLE global_source_product(run_id TEXT,pid TEXT,payload TEXT,PRIMARY KEY(run_id,pid));
                CREATE TABLE global_source_page(run_id TEXT,page INTEGER,PRIMARY KEY(run_id,page));
                CREATE TABLE global_source_screen_run(run_id TEXT PRIMARY KEY,source_run TEXT);
                CREATE TABLE global_source_screen(run_id TEXT,pid TEXT,PRIMARY KEY(run_id,pid));
                CREATE TABLE global_source_operator_acceptance(run_id TEXT PRIMARY KEY,action TEXT);""")
            for run_id, state, created in runs:
                scope = overlay if run_id == "coverage-head" else {}
                db.execute("INSERT INTO global_source_run VALUES(?,?,?,?)", (run_id, json.dumps(scope), state, float(created)))
                for pid in ("1", "2"):
                    db.execute("INSERT INTO global_source_product VALUES(?,?,?)", (run_id, pid, "{}"))
                db.execute("INSERT INTO global_source_page VALUES(?,1)", (run_id,))
                db.execute("INSERT INTO global_source_screen_run VALUES(?,?)", (f"screen-{run_id}", run_id))
                db.execute("INSERT INTO global_source_screen VALUES(?,'1')", (f"screen-{run_id}",))
            db.execute("INSERT INTO global_source_head VALUES('scope','coverage-head')")

    def _touch(self, path, *, age_days, text="{}"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        stamp = NOW - age_days * DAY
        os.utime(path, (stamp, stamp))

    def _files(self):
        for name, age in (("cycle-catalog-uk-campaign-20260921-111122.json", 3),
                          ("cycle-catalog-uk-campaign-20260922-085755.json", 2),
                          ("cycle-catalog-uk-campaign-20260924-070056.json", 0)):
            self._touch(self.var / name, age_days=age)
        self._touch(self.var / "catalog-link-read-00-1789.json", age_days=20)
        self._touch(self.var / "catalog-link-read-00-1790.json", age_days=1)
        self._touch(self.var / "job-links.json", age_days=30)
        self._touch(self.var / "it-conversations-scan.json", age_days=30)
        self._touch(self.var / "campaign-collect-referenced.json", age_days=30)
        self._touch(self.var / "cycle-scheduler" / "catalog-old.log", age_days=20, text="old")
        self._touch(self.var / "cycle-scheduler" / "catalog-new.log", age_days=1, text="new")
        self._touch(self.var / "big.log", age_days=0, text="x" * 64)

    def _identities(self):
        base = self.var / "account-identities" / "acc4" / "generations"
        for name, age in (("gen-old", 10), ("gen-prev", 9), ("gen-cur", 8), ("gen-orphan", 7), ("gen-young", 0.5)):
            self._touch(base / name / "profile" / "Cookies", age_days=age, text=name)
            stamp = NOW - age * DAY
            os.utime(base / name, (stamp, stamp))

    def _rows(self, database, sql):
        with closing(sqlite3.connect(self.var / database)) as db:
            return [row[0] for row in db.execute(sql)]

    def test_plan_keeps_current_two_recent_versions_and_everything_referenced(self):
        with unittest.mock.patch.object(state_retention, "ROTATE_LOG_BYTES", 10):
            result = state_retention.plan(self.root, now=NOW)
        self.assertEqual([row["id"] for row in result["catalogSnapshots"]], ["snap-2", "snap-1"])
        source = result["sourceRuns"][0]
        self.assertEqual(sorted(source["candidates"]), ["old-a", "old-b", "stopped-c"])
        self.assertEqual(source["protected"]["coverage-head"], "current head")
        self.assertEqual(source["protected"]["refresh"], "referenced by head overlay (refreshRunId)")
        self.assertEqual(source["protected"][self.open_source], "source of an open workflow run")
        self.assertEqual(source["protected"]["collecting-d"], "not finished (collecting)")
        self.assertEqual(result["catalogFiles"], ["var/cycle-catalog-uk-campaign-20260922-085755.json",
                                                  "var/cycle-catalog-uk-campaign-20260921-111122.json"])
        self.assertEqual(result["reports"], ["var/catalog-link-read-00-1789.json"])
        self.assertEqual(result["stageLogs"], ["var/cycle-scheduler/catalog-old.log"])
        self.assertEqual(result["rotateLogs"], ["var/big.log"])
        self.assertEqual(result["identityGenerations"], ["var/account-identities/acc4/generations/gen-old",
                                                         "var/account-identities/acc4/generations/gen-orphan"])
        self.assertEqual(result["identityKept"], {"acc4": ["gen-cur", "gen-prev"]})

    def test_a_log_with_a_live_writer_is_left_alone_until_it_outgrows_the_hard_cap(self):
        log = self.root / "var/big.log"
        with open(log, "a", encoding="utf-8") as writer:  # this process keeps it open, like a worker
            writer.write("still writing\n"); writer.flush()
            with unittest.mock.patch.object(state_retention, "ROTATE_LOG_BYTES", 10):
                receipt = state_retention.apply(self.root, self.archive_root, now=NOW)
            manifest = json.loads((Path(receipt["archive"]) / "manifest.json").read_text())
            self.assertEqual(manifest["skippedLogs"], [{"log": "var/big.log", "reason": "writer_active"}])
            self.assertGreater(log.stat().st_size, 0)
            with unittest.mock.patch.object(state_retention, "ROTATE_LOG_BYTES", 10), \
                 unittest.mock.patch.object(state_retention, "FORCE_ROTATE_LOG_BYTES", 10):
                forced = state_retention.apply(self.root, self.archive_root, now=NOW + 1)
            rotated = next(row for row in forced["archives"] if row.get("rotatedLog") == "var/big.log")
            self.assertTrue(rotated["mayMissTail"])
            self.assertEqual(log.stat().st_size, 0)

    def test_unknown_writers_count_as_busy(self):
        with unittest.mock.patch.object(state_retention, "ROTATE_LOG_BYTES", 10), \
             unittest.mock.patch.object(state_retention, "open_writers", lambda _path: None):
            receipt = state_retention.apply(self.root, self.archive_root, now=NOW)
        manifest = json.loads((Path(receipt["archive"]) / "manifest.json").read_text())
        self.assertEqual(manifest["skippedLogs"][0]["reason"], "writers_unknown")

    def test_apply_archives_before_removing_and_restore_puts_everything_back(self):
        with unittest.mock.patch.object(state_retention, "ROTATE_LOG_BYTES", 10):
            receipt = state_retention.apply(self.root, self.archive_root, now=NOW)
        archive = Path(receipt["archive"])
        self.assertTrue((archive / "manifest.json").exists())
        self.assertEqual(self._rows("second-cycle.sqlite", "SELECT id FROM catalog ORDER BY id"),
                         ["snap-3", "snap-4", "snap-5"])
        self.assertEqual(self._rows("global-source.sqlite", "SELECT DISTINCT run_id FROM global_source_product ORDER BY run_id"),
                         sorted(["base", "collecting-d", self.open_source, "refresh", "coverage-head"]))
        self.assertEqual(len(self._rows("global-source.sqlite", "SELECT id FROM global_source_run")), 8)
        self.assertNotIn("screen-old-a", self._rows("global-source.sqlite", "SELECT run_id FROM global_source_screen"))
        self.assertFalse((self.var / "cycle-catalog-uk-campaign-20260921-111122.json").exists())
        self.assertTrue((self.var / "cycle-catalog-uk-campaign-20260924-070056.json").exists())
        self.assertTrue((self.var / "job-links.json").exists())
        self.assertTrue((self.var / "campaign-collect-referenced.json").exists())
        self.assertFalse((self.var / "account-identities/acc4/generations/gen-old").exists())
        self.assertTrue((self.var / "account-identities/acc4/generations/gen-young").exists())
        self.assertEqual((self.var / "big.log").stat().st_size, 0)

        restored = state_retention.restore(self.root, archive)
        self.assertTrue(restored["restored"])
        self.assertEqual(self._rows("second-cycle.sqlite", "SELECT id FROM catalog ORDER BY id"),
                         ["snap-1", "snap-2", "snap-3", "snap-4", "snap-5"])
        self.assertIn("old-a", self._rows("global-source.sqlite", "SELECT DISTINCT run_id FROM global_source_product"))
        self.assertIn("screen-old-a", self._rows("global-source.sqlite", "SELECT run_id FROM global_source_screen"))
        self.assertTrue((self.var / "cycle-catalog-uk-campaign-20260921-111122.json").exists())
        self.assertEqual((self.var / "account-identities/acc4/generations/gen-old/profile/Cookies").read_text(), "gen-old")

    def test_second_apply_with_nothing_left_writes_an_empty_receipt(self):
        with unittest.mock.patch.object(state_retention, "ROTATE_LOG_BYTES", 10):
            state_retention.apply(self.root, self.archive_root, now=NOW)
        with unittest.mock.patch.object(state_retention, "ROTATE_LOG_BYTES", 10**9):
            again = state_retention.apply(self.root, self.archive_root, now=NOW + 1)
        self.assertEqual(again["archives"], [])


if __name__ == "__main__":
    unittest.main()
