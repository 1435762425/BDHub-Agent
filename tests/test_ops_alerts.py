import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "scripts"))

from lib.cycle_inbox import Inbox  # noqa: E402
from lib.cycle_service import Service  # noqa: E402
from lib.ops_alerts import evaluate, gather  # noqa: E402
from lib.schema_migrations import apply_database  # noqa: E402
from lib.second_cycle import CycleStore  # noqa: E402

NOW = 1_790_240_000.0
HOUR = 3600


def market(key="br", **changes):
    facts = {"market": key, "active": True,
             "inbox": {"checkedAt": NOW - 60, "lastSuccessAt": NOW - 60, "errorCode": None, "failureStage": None,
                       "stopRequested": False},
             "stages": [], "unknown": {"count": 0, "oldestAt": None}, "quarantined": {"count": 0, "oldestAt": None},
             "humanCases": {"count": 0, "oldestAt": None}, "humanQueue": {"human": 0, "technical": 0},
             "selectionIsolated": 0, "unread": {"unread": 0, "oldestAt": None},
             "platformRejections": {"count": 0, "oldestAt": None},
             "agent": {"enabled": True, "rolloutStage": "pilot_running", "runtimeState": "outside_reply_window",
                       "replyWindow": ["15:00", "16:00"]},
             "accounts": [], "needsHuman": []}
    for name, value in changes.items():
        facts[name] = {**facts[name], **value} if isinstance(value, dict) and isinstance(facts.get(name), dict) else value
    return facts


def facts(*markets, running=True, stop=None, offsite=NOW - 86400):
    return {"now": NOW, "scheduler": {"running": running, "stopRequestedAt": stop, "checkedAt": NOW - 30},
            "offsite": {"createdAt": offsite} if offsite else None, "markets": list(markets)}


def levels(alerts):
    return {alert["id"]: alert["level"] for alert in alerts}


class SelectionIsolatedTests(unittest.TestCase):
    def test_counts_only_recent_isolations_and_tolerates_a_missing_ledger(self):
        import json
        import sqlite3
        from lib.ops_alerts import _selection_isolated
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "var").mkdir()
            self.assertEqual(_selection_isolated(root, "it", NOW), 0)
            with sqlite3.connect(root / "var/global-selection.sqlite") as db:
                db.execute("CREATE TABLE intake_item(run_id TEXT,pid TEXT,state TEXT,payload TEXT,updated REAL)")
                for pid, at in (("1", NOW - 3600), ("2", NOW - 8 * 86400)):
                    db.execute("INSERT INTO intake_item VALUES('r',?,'isolated_unverified',?,0)",
                               (pid, json.dumps({"isolation": {"at": at}})))
                db.execute("INSERT INTO intake_item VALUES('r','3','confirmed','{}',0)")
            self.assertEqual(_selection_isolated(root, "it", NOW), 1)
            self.assertEqual(_selection_isolated(root, "uk", NOW), 0)


class EvaluateTests(unittest.TestCase):
    def test_healthy_running_system_has_no_alerts(self):
        self.assertEqual(evaluate(facts(market("it"), market("br"))), [])

    def test_pause_keeps_real_errors_and_softens_its_consequences(self):
        paused = facts(
            market("my", inbox={"checkedAt": NOW - 2 * HOUR, "lastSuccessAt": NOW - 2 * HOUR,
                                "errorCode": "taplink_remote_read_failed", "failureStage": "auth"},
                   accounts=[{"account": "acc8", "nextMaintenanceAt": NOW - 3 * HOUR, "maintenanceState": "completed"}]),
            market("uk", inbox={"checkedAt": NOW - 2 * HOUR, "errorCode": "stopped", "failureStage": "conversation"},
                   stages=[{"stage": "kalodata", "state": "needs_human", "errorCode": "kalodata_auth_required",
                            "at": NOW - 5 * HOUR}]),
            running=False, stop=NOW - HOUR)
        alerts = evaluate(paused)
        self.assertEqual(levels(alerts), {"production-paused": "info", "my-inbox-error": "warning",
                                          "my-acc8-identity-overdue": "info", "uk-stage-kalodata": "warning"})
        by_id = {alert["id"]: alert for alert in alerts}
        self.assertEqual(by_id["my-inbox-error"]["href"], "/my/ops/accounts")
        self.assertEqual(by_id["uk-stage-kalodata"]["href"], "/uk/ops/kalodata")
        self.assertEqual([alert["level"] for alert in alerts], ["warning", "warning", "info", "info"])

    def test_running_system_escalates_stale_inbox_failures_and_overdue_identity(self):
        alerts = evaluate(facts(
            market("br", inbox={"checkedAt": NOW - 20 * 60}),
            market("my", inbox={"errorCode": "taplink_remote_read_failed", "failureStage": "auth",
                                "lastSuccessAt": NOW - HOUR}),
            market("uk", inbox={"errorCode": "live_guard_busy", "lastSuccessAt": NOW - HOUR}),
            market("it", inbox={"errorCode": "im_read_failed", "lastSuccessAt": NOW - 5 * 60},
                   accounts=[{"account": "acc6", "nextMaintenanceAt": NOW - 3 * HOUR, "maintenanceState": None},
                             {"account": "acc9", "nextMaintenanceAt": NOW - 3 * HOUR, "maintenanceState": "queued"}])))
        self.assertEqual(levels(alerts), {"br-inbox-stale": "critical", "my-inbox-error": "critical",
                                          "it-acc6-identity-overdue": "warning"})

    def test_inbox_login_alert_says_what_the_automatic_refresh_did(self):
        failing = {"errorCode": "taplink_remote_read_failed", "failureStage": "auth", "lastSuccessAt": NOW - HOUR}
        notes = {}
        for state, extra in (("running", {}), ("completed", {}), ("needs_human", {"errorCode": "account_login_timeout"}),
                             ("not_requested", {"reason": "account_maintenance_active"})):
            alert = evaluate(facts(market("br", inbox=failing | {"accountRecovery": {"state": state} | extra})))[0]
            notes[state] = alert["detail"]
        self.assertEqual(notes, {
            "running": "taplink_remote_read_failed（auth 阶段）；已自动发起账号刷新，等待结果",
            "completed": "taplink_remote_read_failed（auth 阶段）；账号已自动刷新，等下一轮收信确认",
            "needs_human": "taplink_remote_read_failed（auth 阶段）；自动刷新未成功（account_login_timeout），需要人工处理",
            "not_requested": "taplink_remote_read_failed（auth 阶段）；账号维护进行中"})

    def test_scheduler_down_without_a_stop_request_is_critical(self):
        self.assertEqual(levels(evaluate(facts(market("br"), running=False))), {"scheduler-down": "critical"})
        self.assertEqual(evaluate(facts(market("br", active=False), running=False)), [])

    def test_backlog_becomes_a_warning_after_a_missed_reply_window(self):
        recent = evaluate(facts(market("br", unread={"unread": 48, "oldestAt": NOW - 20 * HOUR})))
        self.assertEqual(levels(recent), {"br-unread": "info"})
        stale = evaluate(facts(market("br", unread={"unread": 48, "oldestAt": NOW - 27 * HOUR},
                                      agent={"enabled": False})))
        self.assertEqual(levels(stale), {"br-unread": "warning"})
        self.assertIn("AI 回复已关闭", stale[0]["detail"])

    def test_platform_rejections_say_when_new_contacts_are_held(self):
        one = evaluate(facts(market("br", platformRejections={"count": 1, "oldestAt": NOW - 600, "hold": "platform_quota"})))
        self.assertEqual(levels(one), {"br-platform-rejected": "warning"})
        self.assertIn("额度用尽，已暂停新联系到明天", one[0]["detail"])
        other = evaluate(facts(market("br", platformRejections={"count": 1, "oldestAt": NOW - 600, "hold": None})))
        self.assertIn("继续发送", other[0]["detail"])

    def test_offsite_copy_missing_or_stale_is_reported(self):
        self.assertEqual(levels(evaluate(facts(offsite=None))), {"offsite-missing": "warning"})
        self.assertEqual(levels(evaluate(facts(offsite=NOW - 8 * 86400))), {"offsite-stale": "warning"})

    def test_isolated_selections_ask_a_person_to_check_the_campaign(self):
        alerts = evaluate(facts(market("br", selectionIsolated=2)))
        self.assertEqual(levels(alerts), {"br-selection-isolated": "warning"})
        self.assertEqual(alerts[0]["title"], "BR 2 个选品因活动不符已隔离")
        self.assertEqual(alerts[0]["href"], "/br/ops/jobs")
        self.assertEqual(evaluate(facts(market("br", selectionIsolated=0))), [])
        # An unreadable ledger is reported, never read as zero isolated selections.
        self.assertEqual(levels(evaluate(facts(market("br", selectionIsolated=None)))),
                         {"br-selection-ledger-unreadable": "warning"})

    def test_human_alert_counts_the_conversation_human_queue(self):
        alerts = evaluate(facts(market("br", humanQueue={"human": 3}, humanCases={"count": 1, "oldestAt": NOW - HOUR})))
        self.assertEqual(alerts[0]["title"], "BR 3 位达人在人工队列")
        # Open cases alone are no human queue; they only stand in, named as cases, when the queue is unreadable.
        self.assertEqual(evaluate(facts(market("br", humanQueue={"human": 0}, humanCases={"count": 2, "oldestAt": NOW}))), [])
        alerts = evaluate(facts(market("br", humanQueue=None, humanCases={"count": 2, "oldestAt": NOW})))
        self.assertEqual(alerts[0]["title"], "BR 2 条未关闭人工工单")

    def test_unreadable_market_is_reported_and_others_still_checked(self):
        alerts = evaluate(facts({"market": "uk", "error": "plan_missing"},
                                market("br", humanQueue={"human": 2})))
        self.assertEqual(levels(alerts), {"uk-read-failed": "warning", "br-human": "warning"})


class GatherTests(unittest.TestCase):
    def test_empty_install_reads_every_market_without_errors(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "var").mkdir()
            shutil.copytree(ROOT / "config", root / "config")
            path = root / "var/second-cycle.sqlite"
            with CycleStore(path, lambda: NOW) as store:
                for key in ("it", "br", "my", "uk"):
                    store.plan("bjn-local-research", key)
            apply_database(root, "second-cycle", clock=lambda: NOW)
            with CycleStore(path, lambda: NOW) as store:
                Inbox(store)
                Service(store)
            with CycleStore(path, lambda: NOW, readonly=True) as store:
                gathered = gather(root, store)
            self.assertNotIn("accountError", gathered)
            self.assertEqual([entry.get("error") for entry in gathered["markets"]], [None] * len(gathered["markets"]))
            self.assertEqual(levels(evaluate(gathered)), {"offsite-missing": "warning"})


if __name__ == "__main__":
    unittest.main()


class RuntimeReleaseTests(unittest.TestCase):
    def test_workers_on_an_older_commit_are_reported_and_current_ones_are_not(self):
        base = facts()
        base["release"] = {"head": "b" * 40, "loaded": [{"role": "scheduler", "sha": "b" * 40, "dirty": True},
                                                        {"role": "agent-reply-it", "sha": "a" * 40}]}
        alerts = {alert["id"]: alert for alert in evaluate(base)}
        self.assertIn("agent-reply-it", alerts["runtime-version-mixed"]["detail"])
        self.assertNotIn("scheduler", alerts["runtime-version-mixed"]["detail"])
        base["release"]["loaded"] = base["release"]["loaded"][:1]
        self.assertNotIn("runtime-version-mixed", {alert["id"] for alert in evaluate(base)})

    def test_a_docs_only_commit_is_not_a_version_difference(self):
        base = facts()
        base["release"] = {"head": "b" * 40, "headCode": "tree-1",
                           "loaded": [{"role": "scheduler", "sha": "a" * 40, "code": "tree-1"},
                                      {"role": "agent-reply-it", "sha": "a" * 40, "code": "tree-0"}]}
        alert = next(alert for alert in evaluate(base) if alert["id"] == "runtime-version-mixed")
        self.assertIn("agent-reply-it", alert["detail"])
        self.assertNotIn("scheduler", alert["detail"])
        from lib.ops_alerts import evidence
        rows = {row["role"]: row["current"] for row in evidence(base)["processes"]}
        self.assertEqual(rows, {"scheduler": True, "agent-reply-it": False})

    def test_uncommitted_runtime_edits_are_never_reported_as_current(self):
        from lib.ops_alerts import evidence
        base = facts()
        base["release"] = {"head": "b" * 40, "headCode": "tree-1", "headDirty": "edit-1",
                           "loaded": [{"role": "scheduler", "sha": "b" * 40, "code": "tree-1", "runtimeDirty": "edit-1"},
                                      {"role": "agent-reply-it", "sha": "b" * 40, "code": "tree-1", "runtimeDirty": None},
                                      {"role": "im-session-it", "sha": "b" * 40, "code": "tree-1"}]}
        alerts = {alert["id"]: alert for alert in evaluate(base)}
        self.assertIn("runtime-code-uncommitted", alerts)
        self.assertIn("agent-reply-it", alerts["runtime-version-mixed"]["detail"])
        self.assertIn("im-session-it", alerts["runtime-version-mixed"]["detail"])
        self.assertEqual({row["role"]: row["current"] for row in evidence(base)["processes"]},
                         {"scheduler": True, "agent-reply-it": False, "im-session-it": False})
        base["release"]["headDirty"] = None  # clean tree: an older registration without the field still counts
        self.assertEqual({row["role"]: row["current"] for row in evidence(base)["processes"]},
                         {"scheduler": False, "agent-reply-it": True, "im-session-it": True})
        base["release"]["headDirty"] = "unknown"
        self.assertFalse(any(row["current"] for row in evidence(base)["processes"]))

    def test_runtime_dirty_ignores_config_and_docs(self):
        import subprocess, tempfile
        from pathlib import Path
        from lib.runtime_release import runtime_dirty
        with tempfile.TemporaryDirectory() as folder:
            run = lambda *args: subprocess.run(["git", *args], cwd=folder, check=True, capture_output=True)
            run("init", "-q"); run("config", "user.email", "t@example.com"); run("config", "user.name", "t")
            for name in ("scripts/a.py", "vendor/v.py", "config/c.json", "docs/d.md"):
                (Path(folder) / name).parent.mkdir(exist_ok=True); (Path(folder) / name).write_text("1")
            run("add", "."); run("commit", "-qm", "base")
            (Path(folder) / "config/c.json").write_text("2"); (Path(folder) / "docs/d.md").write_text("2")
            self.assertIsNone(runtime_dirty(folder))
            (Path(folder) / "scripts/new.py").write_text("x")
            untracked = runtime_dirty(folder)
            self.assertIsNotNone(untracked)
            (Path(folder) / "scripts/a.py").write_text("2")
            self.assertNotEqual(runtime_dirty(folder), untracked)

    def test_registration_records_the_loaded_commit_and_ignores_dead_processes(self):
        import json, os, tempfile
        from pathlib import Path
        from unittest.mock import patch
        from lib import runtime_release
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.object(runtime_release, "current_release", return_value={"sha": "c" * 40, "dirty": False, "contentDigest": None}):
                value = runtime_release.register(root, "scheduler")
            self.assertEqual((value["pid"], value["sha"]), (os.getpid(), "c" * 40))
            (root / "var/runtime-loaded/agent-reply-br.json").write_text(json.dumps({"role": "agent-reply-br", "pid": 999999, "sha": "d"}))
            self.assertEqual([row["role"] for row in runtime_release.loaded(root)], ["scheduler"])


class RestoreDrillAlertTests(unittest.TestCase):
    def test_only_a_failed_latest_drill_is_reported(self):
        base = facts()
        base["restoreDrill"] = {"backup": "b1", "state": "restorable", "finishedAt": NOW, "blockers": []}
        self.assertNotIn("restore-drill-blocked", {alert["id"] for alert in evaluate(base)})
        base["restoreDrill"] = {"backup": "b1", "state": "blocked", "finishedAt": NOW,
                                "blockers": ["reference:delivery_creator_identity:future_or_undated_missing"]}
        alert = next(alert for alert in evaluate(base) if alert["id"] == "restore-drill-blocked")
        self.assertIn("delivery_creator_identity", alert["detail"])
