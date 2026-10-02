"""Runtime wiring tests: fake auth/session, isolated gate/identity fixtures."""
from contextlib import contextmanager, ExitStack
from dataclasses import replace
import hashlib
import importlib.util
import json
import os
from pathlib import Path
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
            stack.enter_context(patch("socket.socket", side_effect=AssertionError("no real network")))
            yield SimpleNamespace(root=root, var=var, identity_file=identity_file, account=account, identity=identity,
                state=state, auth=secret, stopped=stopped, maintenance=maintenance, loaded=loaded, adapter=adapter)

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
            f.adapter.assert_not_called()
            self.assertEqual(f.state["gateCalls"], []); self.assertNotIn("readCreated", f.state)
            self.assertFalse(f.state["guarded"]); self.assertTrue(report["identityFileUnchanged"])
            self.assertEqual(f.identity_file.read_bytes(), before); self.assertNotIn("PRIVATE", json.dumps(report))

    def test_guard_and_sender_fifo_are_wired_without_new_pacing(self):
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
            self.assertEqual(f.state["marks"], 1); self.assertTrue(f.state["closed"]); self.assertFalse(f.state["guarded"])
            self.assertEqual(report["writeGateScope"], "new_system_sender_only")
            self.assertEqual(f.identity_file.read_bytes(), before); self.assertNotIn("PRIVATE", json.dumps(report))
            self.assert_code("live_runtime_closed", lambda: runtime["write_gate"]().__enter__())

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
