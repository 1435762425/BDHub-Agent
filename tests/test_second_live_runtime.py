"""Runtime wiring tests: fake auth/session/SQL, isolated gate/identity fixtures."""
from contextlib import closing, contextmanager, ExitStack
from dataclasses import replace
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from lib import second_live_runtime as M
from lib.italy_im_auth import ItalyImAuthContext, ImProbeDeadline
from lib.italy_cards import PID, LIST_ID, CAMPAIGN_ID, combine_card_facts
from lib.second_card_binding import card_from_facts


def auth():
    return ItalyImAuthContext("acc6", "101", {"token": "PRIVATE_TOKEN"},
        {"market_region": "8", "market_id": "202", "partner_id": "303", "partner": "PRIVATE_RAW_PARTNER"},
        {"User-Agent": "fixture"})


def card():
    facts = combine_card_facts([{"product_list_id": LIST_ID, "product_list_name": "fixture", "campaign_id": "0",
        "campaign_products": [{"product_id": PID, "stock": 1}]}],
        [{"product_id": PID, "campaign_id": CAMPAIGN_ID, "stock": 1}],
        im_observed_at="2026-09-12T16:00:00Z", members_observed_at="2026-09-12T16:00:01Z")
    return card_from_facts(facts)


EMPTY_STATE = {"task_enabled": False, "outbox_pending": False, "delivery_pending": False,
               "component_pending": False, "attempt_pending": False}


class FakeConnection:
    def __init__(self, result): self.result = result; self.sql = []
    def exec_driver_sql(self, sql):
        self.sql.append(sql)
        return SimpleNamespace(mappings=lambda: SimpleNamespace(one=lambda: self.result))


class SecondLiveRuntimeTests(unittest.TestCase):
    def assert_code(self, code, callback):
        with self.assertRaises(M.SecondLiveRuntimeError) as caught: callback()
        self.assertEqual(caught.exception.code, code)
        self.assertNotIn("PRIVATE", str(caught.exception))

    @contextmanager
    def fixture(self):
        with TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory); var = root / "var"; var.mkdir()
            identity_file = root / "identity.fixture"; identity_file.write_text("PRIVATE_IDENTITY")
            state = {"guarded": False, "closed": False, "marks": 0, "gateCalls": [], "authCalls": 0}
            account = SimpleNamespace(name="acc6", headers_json=identity_file)
            identity = SimpleNamespace(market="it"); identity.require_product_search = Mock(return_value=identity)
            secret = auth(); stopped = Mock(return_value=False); maintenance = Mock(return_value=False)
            @contextmanager
            def guard(account, *, wait_seconds=0):
                self.assertEqual(wait_seconds,15)
                self.assertFalse(state["guarded"]); state["guarded"] = True
                try: yield
                finally: state["guarded"] = False
            def authenticate(account, identity, headers, report, **options):
                self.assertTrue(state["guarded"]); state["authCalls"] += 1
                self.assertEqual(headers, {"Cookie": "PRIVATE_COOKIE"})
                self.assertTrue(options["use_environment_proxy"])
                report.update(authReads=[{"stage": "token", "status": "returned"}])
                return secret
            class Reads:
                def __init__(self, auth_value, report, **options):
                    state["readOptions"] = options; self.auth = auth_value
                    state["readCreated"] = True
                def __enter__(self): return self
                def __exit__(self, *_): state["closed"] = True
            @contextmanager
            def gate(sender, **options):
                self.assertTrue(state["guarded"])
                state["gateCalls"].append((sender, options))
                def mark(): state["marks"] += 1
                yield mark
            loaded = Mock(return_value=(account, identity,
                lambda path: SimpleNamespace(headers={"Cookie": "PRIVATE_COOKIE"}), guard, maintenance, "canary"))
            stack.enter_context(patch("lib.im_session_owner.enabled", return_value=False))
            stack.enter_context(patch.object(M, "VAR", var))
            stack.enter_context(patch.object(M, "_load_runtime", loaded))
            stack.enter_context(patch.object(M, "authenticate_it", authenticate))
            stack.enter_context(patch.object(M, "ItalyImReadSession", Reads))
            adapter = stack.enter_context(patch.object(M, "ItalyImDeliveryAdapter", return_value=object()))
            stack.enter_context(patch.object(M, "_resolve_policy", return_value=SimpleNamespace(im_send_pool=True, im_send_interval_seconds=7.5)))
            stack.enter_context(patch.object(M, "_shared_gate", gate))
            legacy_gate = stack.enter_context(patch.object(M, "check_legacy_gate", return_value={"state": "idle", "active": False}))
            legacy_sql = stack.enter_context(patch.object(M, "check_legacy_send_state", return_value=dict(EMPTY_STATE)))
            stack.enter_context(patch("socket.socket", side_effect=AssertionError("no real network")))
            yield SimpleNamespace(root=root, var=var, identity_file=identity_file, account=account, identity=identity,
                state=state, auth=secret, stopped=stopped, maintenance=maintenance, loaded=loaded, adapter=adapter,
                legacy_gate=legacy_gate, legacy_sql=legacy_sql)

    def test_readable_im_does_not_open_adapter_when_send_capability_is_disabled(self):
        with self.fixture() as fixture:
            fixture.loaded.return_value=(*fixture.loaded.return_value[:-1], "unsupported")
            with self.assertRaises(M.SecondLiveRuntimeError) as error:
                with M.live_runtime(M.sender_binding_sha256(fixture.auth), {}, var_dir=fixture.var):
                    self.fail("Unavailable market must not expose write adapter")
            self.assertEqual(error.exception.code, "live_market_send_unavailable")
            fixture.adapter.assert_not_called()

    def test_read_only_reconciliation_works_without_send_capability_or_write_gate(self):
        with self.fixture() as fixture:
            fixture.loaded.return_value=(*fixture.loaded.return_value[:-1], "unsupported")
            with M.live_runtime(M.sender_binding_sha256(fixture.auth), {}, var_dir=fixture.var,
                                read_only=True) as runtime:
                self.assert_code("live_read_only", lambda: runtime['write_gate']().__enter__())
                fixture.adapter.assert_called_once()
            fixture.legacy_gate.assert_not_called()
            fixture.legacy_sql.assert_not_called()
            self.assertEqual(fixture.state['marks'],0)

    def test_borrowed_runtime_reuses_outer_guard_and_auth_but_closes_each_session(self):
        with self.fixture() as f:
            report={}
            with M._authenticated(report,stopped=f.stopped) as context:
                for _ in range(2):
                    with M.live_runtime(M.sender_binding_sha256(f.auth),dict(report),var_dir=f.var,
                                        authenticated_context=context,send_interval=.75):
                        self.assertTrue(f.state['guarded'])
                self.assertEqual(f.state['authCalls'],1)
                self.assertTrue(f.state['guarded'])
            self.assertFalse(f.state['guarded'])

    def test_read_only_auth_releases_guard_before_inbox_scan_and_rejects_rotation(self):
        with self.fixture() as f:
            with M._authenticated({},stopped=f.stopped,read_only=True) as context:
                self.assertEqual(f.state['authCalls'],1)
                self.assertFalse(f.state['guarded'])
                context[-1]()
                f.identity_file.write_text('rotated')
                self.assert_code('live_identity_changed',context[-1])
            self.assertFalse(f.state['guarded'])

    def test_sender_reuses_short_lived_auth_only_for_same_identity_file(self):
        with self.fixture() as f:
            with M._authenticated({},stopped=f.stopped): pass
            report={}
            with M._authenticated(report,stopped=f.stopped):
                self.assertTrue(report['authCacheHit'])
            self.assertEqual(f.state['authCalls'],1)
            f.identity_file.write_text('rotated')
            with M._authenticated(report,stopped=f.stopped):
                self.assertFalse(report['authCacheHit'])
            self.assertEqual(f.state['authCalls'],2)

    def test_sender_hash_is_canonical_identity_only_and_token_independent(self):
        source = auth(); expected = {"account": "acc6", "market": "it", "im_id": "101", "market_id": "202", "partner_id": "303"}
        result = M.sender_binding_sha256(source)
        self.assertEqual(result, hashlib.sha256(json.dumps(expected, sort_keys=True, ensure_ascii=False,
            separators=(",", ":"), allow_nan=False).encode()).hexdigest())
        self.assertEqual(result, M.sender_binding_sha256(replace(source, token={"token": "OTHER_PRIVATE_TOKEN"})))
        for key in ("market_id", "partner_id"):
            changed = replace(source, native_context={**source.native_context, key: "999"})
            self.assertNotEqual(result, M.sender_binding_sha256(changed))
        self.assertNotEqual(result, M.sender_binding_sha256(replace(source, im_id="404")))
        self.assert_code("live_sender_invalid", lambda: M.sender_binding_sha256(replace(source, account_name="acc7")))

    def test_missing_or_wrong_sender_binding_never_exposes_a_write_adapter(self):
        with self.fixture() as f:
            for expected in (None, "", "not-a-hash", "A" * 64):
                self.assert_code("live_sender_binding_required", lambda: M.live_runtime(expected, {}, var_dir=f.var).__enter__())
            f.loaded.assert_not_called(); f.adapter.assert_not_called()
            self.assert_code("live_sender_binding_mismatch", lambda: M.live_runtime("0" * 64, {}, var_dir=f.var).__enter__())
            self.assertEqual(f.state["authCalls"], 1); f.adapter.assert_not_called()
            self.assertFalse(f.state["guarded"]); self.assertNotIn("readCreated", f.state)

    def test_read_sender_binding_only_authenticates_without_session_gate_or_adapter(self):
        with self.fixture() as f:
            report = {}; before = f.identity_file.read_bytes()
            self.assertEqual(M.read_sender_binding(report), M.sender_binding_sha256(f.auth))
            f.adapter.assert_not_called(); f.legacy_gate.assert_not_called(); f.legacy_sql.assert_not_called()
            self.assertEqual(f.state["gateCalls"], []); self.assertNotIn("readCreated", f.state)
            self.assertFalse(f.state["guarded"]); self.assertTrue(report["identityFileUnchanged"])
            self.assertEqual(f.identity_file.read_bytes(), before); self.assertNotIn("PRIVATE", json.dumps(report))

    def test_guard_sender_fifo_and_last_moment_conflicts_are_wired_without_new_pacing(self):
        with self.fixture() as f:
            report = {}; before = f.identity_file.read_bytes()
            with M.live_runtime(M.sender_binding_sha256(f.auth), report, var_dir=f.var, stopped=f.stopped) as runtime:
                self.assertTrue(f.state["guarded"])
                self.assertEqual(set(runtime), {"adapter", "reads", "write_gate", "validate_card", "senderBindingHash"})
                self.assertTrue(f.state["readOptions"]["use_environment_proxy"])
                self.assertIs(f.state["readOptions"]["maintenance_due"], f.maintenance)
                with runtime["write_gate"]() as mark:
                    self.assertEqual(f.state["marks"], 0); mark()
                    self.assert_code("live_mark_reused", mark)
                sender, options = f.state["gateCalls"][0]
                self.assertEqual(sender, f.auth.im_id); self.assertEqual(options["interval"], 7.5)
                self.assertEqual(options["directory"], (f.var / "im-http-write-gates").resolve())
                self.assertEqual(f.legacy_sql.call_count, 4); self.assertEqual(f.legacy_gate.call_count, 4)
            self.assertEqual(f.state["marks"], 1); self.assertTrue(f.state["closed"]); self.assertFalse(f.state["guarded"])
            self.assertFalse(report["crossProjectAtomicCoordination"])
            self.assertEqual(report["writeGateScope"], "new_system_sender_only")
            self.assertEqual(f.identity_file.read_bytes(), before); self.assertNotIn("PRIVATE", json.dumps(report))
            self.assert_code("live_runtime_closed", lambda: runtime["write_gate"]().__enter__())

    def test_conflict_at_entry_or_immediately_before_mark_does_not_dispatch(self):
        with self.fixture() as f:
            f.legacy_sql.side_effect = M.SecondLiveRuntimeError("legacy_send_conflict")
            self.assert_code("legacy_send_conflict", lambda: M.live_runtime(M.sender_binding_sha256(f.auth), {}, var_dir=f.var).__enter__())
            f.adapter.assert_not_called(); self.assertEqual(f.state["marks"], 0)
        with self.fixture() as f:
            with M.live_runtime(M.sender_binding_sha256(f.auth), {}, var_dir=f.var) as runtime:
                with runtime["write_gate"]() as mark:
                    f.legacy_gate.side_effect = M.SecondLiveRuntimeError("legacy_gate_unknown")
                    self.assert_code("legacy_gate_unknown", mark)
            self.assertEqual(f.state["marks"], 0)

    def test_stopped_maintenance_identity_change_or_changed_sender_prevents_mark(self):
        alterations = [("live_stopped", lambda f: setattr(f.stopped, "return_value", True)),
                       ("live_maintenance_due", lambda f: setattr(f.maintenance, "return_value", True)),
                       ("live_identity_changed", lambda f: f.identity_file.write_text("changed")),
                       ("live_sender_binding_mismatch", lambda f: f.auth.native_context.update(partner_id="999"))]
        for code, alter in alterations:
            with self.subTest(code=code), self.fixture() as f:
                with M.live_runtime(M.sender_binding_sha256(f.auth), {}, var_dir=f.var, stopped=f.stopped) as runtime:
                    with runtime["write_gate"]() as mark:
                        alter(f); self.assert_code(code, mark)
                self.assertEqual(f.state["marks"], 0); self.assertFalse(f.state["guarded"])

    def test_validate_card_refreshes_under_guard_and_keeps_card_transport_options_separate(self):
        with self.fixture() as f:
            before = card(); current = replace(before, verified_at="2026-09-13T00:00:00Z", evidence_sha256="b" * 64)
            def refresh(previous, account, identity, headers, report, **options):
                self.assertTrue(f.state["guarded"]); self.assertIs(previous, before)
                self.assertIs(account, f.account); self.assertIs(identity, f.identity)
                self.assertEqual(headers, {"Cookie": "PRIVATE_COOKIE"})
                self.assertNotIn("use_environment_proxy", options)
                self.assertIs(options["maintenance_due"], f.maintenance)
                report["requests"] = [{"endpointPath": "/fixture", "status": "returned"}]
                return current, {"publicCommission": None}, {"discarded": "PRIVATE_RAW_FACTS"}
            with patch.object(M, "refresh_card_binding", side_effect=refresh) as reader:
                report = {}
                with M.live_runtime(M.sender_binding_sha256(f.auth), report, var_dir=f.var) as runtime:
                    self.assertIs(runtime["validate_card"](before), current)
                reader.assert_called_once(); f.identity.require_product_search.assert_called_once()
            self.assertEqual(report["cardRefreshes"][0]["evidenceSha256"], "b" * 64)
            self.assertNotIn("PRIVATE", json.dumps(report)); self.assertEqual(f.state["marks"], 0)

    def test_guard_busy_and_deadline_release_without_exposing_private_exception_text(self):
        with self.fixture() as f:
            @contextmanager
            def busy(_,**kwargs):
                self.assertEqual(kwargs.get('wait_seconds'),15)
                raise BlockingIOError("PRIVATE_PATH")
                yield
            runtime = list(f.loaded.return_value); runtime[3] = busy; f.loaded.return_value = tuple(runtime)
            self.assert_code("live_guard_busy", lambda: M.read_sender_binding({}))
            self.assertEqual(f.state["authCalls"], 0)
        with self.fixture() as f, patch.object(M, "authenticate_it", side_effect=ImProbeDeadline()):
            with self.assertRaises(ImProbeDeadline): M.read_sender_binding({})
            self.assertFalse(f.state["guarded"])

    def test_legacy_gate_read_only_checks_live_dead_unknown_and_cooldown(self):
        with TemporaryDirectory() as directory:
            root = Path(directory); path = root / (hashlib.sha256(b"101").hexdigest() + ".lock")
            read = lambda **kwargs: M.check_legacy_gate("101", interval=5, directory=root, wall_time=lambda: 100, **kwargs)
            self.assertEqual(read()["state"], "absent"); self.assertFalse(path.exists())
            cases = [([], 1, None), ([{"token": "fixture", "pid": 123, "active": False}], 1, "legacy_gate_busy"),
                     ([{"token": "fixture", "pid": 123, "active": True}], 1, "legacy_gate_busy"),
                     ([], 99, "legacy_recent_dispatch")]
            for queue, last, code in cases:
                path.write_text(json.dumps({"queue": queue, "last_dispatch": last})); original = path.read_bytes()
                if code: self.assert_code(code, lambda: read(pid_alive=lambda _: True))
                else: self.assertEqual(read(pid_alive=lambda _: True)["state"], "idle")
                self.assertEqual(path.read_bytes(), original)
            path.write_text(json.dumps({"queue": [{"token": "fixture", "pid": 123, "active": True}], "last_dispatch": 1}))
            self.assert_code("legacy_gate_unknown", lambda: read(pid_alive=lambda _: False))
            path.write_text(json.dumps({"queue": [{"token": "fixture", "pid": 123, "active": False}], "last_dispatch": 1}))
            before = path.read_bytes(); self.assertEqual(read(pid_alive=lambda _: False)["deadInactiveWaiters"], 1)
            self.assertEqual(path.read_bytes(), before)
            with path.open("rb") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.assert_code("legacy_gate_busy", read)
            path.write_text("PRIVATE_INVALID_JSON")
            self.assert_code("legacy_gate_invalid", read)

    def test_legacy_sql_only_returns_booleans_and_any_unavailable_state_blocks(self):
        @contextmanager
        def factory(result): yield FakeConnection(result)
        for flag in EMPTY_STATE:
            report = {}; result = {**EMPTY_STATE, flag: True}
            self.assert_code("legacy_send_conflict", lambda: M.check_legacy_send_state(report, connection_factory=lambda: factory(result)))
            self.assertEqual(report["legacyConflictChecks"], [result])
        connection = FakeConnection(dict(EMPTY_STATE))
        @contextmanager
        def connection_factory(): yield connection
        self.assertEqual(M.check_legacy_send_state({}, connection_factory=connection_factory), EMPTY_STATE)
        self.assertEqual(len(connection.sql), 1); self.assertTrue(connection.sql[0].lstrip().startswith("SELECT"))
        self.assertIn("send_component_attempt", connection.sql[0]); self.assertIn("manual_resolution,resolution", connection.sql[0])
        self.assertNotIn("UPDATE", connection.sql[0]); self.assertNotIn("DELETE", connection.sql[0])
        for result in ({}, {**EMPTY_STATE, "outbox_pending": 0}):
            self.assert_code("legacy_state_unavailable", lambda: M.check_legacy_send_state({}, connection_factory=lambda: factory(result)))
        self.assert_code("legacy_state_unavailable", lambda: M.check_legacy_send_state({}, connection_factory=Mock(side_effect=RuntimeError("PRIVATE_DB_URL"))))

    def test_legacy_sql_covers_paused_unknown_and_ignores_manually_resolved_attempts(self):
        # SQLite fixture changes only PostgreSQL's JSON path operator; the actual
        # joins, market predicates and state clauses run against synthetic rows.
        with closing(sqlite3.connect(":memory:")) as db:
            db.row_factory = sqlite3.Row
            db.executescript("""
                CREATE TABLE send_task(task_id TEXT, bd_market TEXT, status TEXT);
                CREATE TABLE send_task_lead(lead_id TEXT, task_id TEXT);
                CREATE TABLE send_task_component(component_id TEXT, lead_id TEXT, status TEXT);
                CREATE TABLE send_component_attempt(component_id TEXT, status TEXT, transport_snapshot TEXT);
                CREATE TABLE reply_outbox(bd_market TEXT, status TEXT);
                CREATE TABLE im_delivery_intent(bd_market TEXT, status TEXT);
                INSERT INTO send_task VALUES ('task', 'it', 'paused');
                INSERT INTO send_task_lead VALUES ('lead', 'task');
                INSERT INTO send_task_component VALUES ('component', 'lead', 'sent');
                INSERT INTO send_component_attempt VALUES ('component', 'sent', '{}');
            """)
            sql = M.LEGACY_CONFLICT_SQL.replace("a.transport_snapshot #>> '{manual_resolution,resolution}'",
                "json_extract(a.transport_snapshot, '$.manual_resolution.resolution')")
            run = lambda: {key: bool(value) for key, value in dict(db.execute(sql).fetchone()).items()}
            self.assertEqual(run(), EMPTY_STATE)
            for status in ("draft", "ready", "preflighting", "paused", "completed", "cancelled", "running"):
                db.execute("UPDATE send_task SET status=?", (status,))
                self.assertEqual(run()["task_enabled"], status == "running")
            db.execute("UPDATE send_task SET status='paused'")
            for status in ("sent", "sending", "unknown", "failed_retryable"):
                db.execute("UPDATE send_task_component SET status=?", (status,))
                self.assertEqual(run()["component_pending"], status in {"sending", "unknown"})
            db.execute("UPDATE send_task_component SET status='sent'")
            db.execute("UPDATE send_component_attempt SET status='unknown'")
            self.assertTrue(run()["attempt_pending"])
            for resolution in ("confirmed_sent", "confirmed_not_sent", "not_verified"):
                db.execute("UPDATE send_component_attempt SET transport_snapshot=?", (json.dumps({"manual_resolution": {"resolution": resolution}}),))
                self.assertEqual(run()["attempt_pending"], resolution == "not_verified")
            db.execute("UPDATE send_component_attempt SET status='sending'")
            self.assertTrue(run()["attempt_pending"])
            db.execute("UPDATE send_task SET bd_market='mx'")
            self.assertEqual(run(), EMPTY_STATE)
            for table, blocked in (("reply_outbox", {"queued", "unknown"}), ("im_delivery_intent", {"sending", "unknown"})):
                flag = "outbox_pending" if table == "reply_outbox" else "delivery_pending"
                for status in ("queued", "sending", "unknown", "sent", "failed", "cancelled"):
                    db.execute(f"DELETE FROM {table}")
                    db.execute(f"INSERT INTO {table} VALUES ('it', ?)", (status,))
                    self.assertEqual(run()[flag], status in blocked)
                db.execute(f"DELETE FROM {table}")

    def test_default_database_connection_is_read_only_bounded_and_disposed(self):
        connection = FakeConnection({})
        @contextmanager
        def connect(): yield connection
        engine = SimpleNamespace(connect=connect, dispose=Mock())
        url = SimpleNamespace(get_backend_name=lambda: "postgresql", host="127.0.0.1")
        create = Mock(return_value=engine)
        modules = {"bdhub": SimpleNamespace(config=SimpleNamespace(load=lambda: SimpleNamespace(db_url="PRIVATE_DB_URL"))),
                   "sqlalchemy": SimpleNamespace(create_engine=create),
                   "sqlalchemy.engine": SimpleNamespace(make_url=lambda value: url),
                   "sqlalchemy.pool": SimpleNamespace(NullPool=object())}
        with patch.dict(sys.modules, modules), patch.object(M, "_legacy_imports"):
            with M._legacy_connection() as actual: self.assertIs(actual, connection)
        self.assertEqual(connection.sql, ["SET TRANSACTION READ ONLY"])
        self.assertIn("default_transaction_read_only=on", create.call_args.kwargs["connect_args"]["options"])
        self.assertIn("statement_timeout=5000", create.call_args.kwargs["connect_args"]["options"])
        self.assertEqual(create.call_args.kwargs["connect_args"]["connect_timeout"], 5)
        engine.dispose.assert_called_once()

    @unittest.skipUnless(importlib.util.find_spec("yaml"), "legacy Python dependencies unavailable")
    def test_default_gate_reuses_legacy_sender_fifo_in_only_a_temporary_directory(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with M._shared_gate("101", interval=0.01, directory=root) as mark:
                path = root / (hashlib.sha256(b"101").hexdigest() + ".lock")
                before = json.loads(path.read_text())
                self.assertTrue(before["queue"][0]["active"]); self.assertEqual(before["last_dispatch"], 0)
                mark()
            after = json.loads(path.read_text())
            self.assertEqual(after["queue"], []); self.assertGreater(after["last_dispatch"], 0)

    def test_gate_cannot_be_redirected_to_old_project_or_outside_new_var(self):
        with self.fixture() as f:
            self.assert_code("live_var_invalid", lambda: M.live_runtime(M.sender_binding_sha256(f.auth), {}, var_dir=f.root / "outside").__enter__())
            (f.var / "im-http-write-gates").symlink_to(f.root)
            self.assert_code("live_var_invalid", lambda: M.live_runtime(M.sender_binding_sha256(f.auth), {}, var_dir=f.var).__enter__())
            f.loaded.assert_not_called()


if __name__ == "__main__": unittest.main()
