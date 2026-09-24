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
             "humanCases": {"count": 0, "oldestAt": None}, "unread": {"unread": 0, "oldestAt": None},
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

    def test_offsite_copy_missing_or_stale_is_reported(self):
        self.assertEqual(levels(evaluate(facts(offsite=None))), {"offsite-missing": "warning"})
        self.assertEqual(levels(evaluate(facts(offsite=NOW - 8 * 86400))), {"offsite-stale": "warning"})

    def test_unreadable_market_is_reported_and_others_still_checked(self):
        alerts = evaluate(facts({"market": "uk", "error": "plan_missing"},
                                market("br", humanCases={"count": 2, "oldestAt": NOW - HOUR})))
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
