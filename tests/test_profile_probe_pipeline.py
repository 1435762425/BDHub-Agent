"""Offline integration tests for the Italy probe orchestration.

All legacy modules, account coordination and HTTP clients are replaced with
in-memory fakes. Only isolated temporary inputs/reports touch the filesystem.
"""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "profile_probe_pipeline_subject",
    Path(__file__).resolve().parents[1] / "scripts/probe-italy-profile.py",
)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def profile(**changes):
    result = {
        "handle": {"value": "italian.creator"},
        "creator_oecuid": {"value": "123456789"},
        "selection_region": {"value": "IT"},
        "follower_cnt": {"value": 100},
        "units_sold": {"value": 0},
    }
    result.update(changes)
    return result


def field_value(row, key):
    value = row.get(key)
    if isinstance(value, dict):
        value = value.get("value")
    return str(value or "").strip()


class ProbeHarness:
    """Fake account/runtime, deliberately failing if one-shot HTTP is used."""

    def __init__(self, root, *, replies=None, startable=True, busy=False):
        self.root = root
        self.events = []
        self.calls = []
        self.identity_uses = []
        self.startable = startable
        self.busy = busy
        self.replies = list(replies) if replies is not None else [
            {"code": 0, "exact": profile()},
            {"code": 0, "creator_profile": profile(
                follower_cnt={"is_authorized": True},
                video_publish_cnt_30d={"value": 17},
            )},
            {"code": 0, "creator_profile": profile(
                follower_cnt={"is_authorized": True},
                video_publish_cnt_30d={"is_authorized": True},
            )},
        ]
        self.identity = {
            "name": "acc6", "partner_id": "1234", "signer_region": "eu",
            "api_host": "https://api-partner-eu.tiktokshop.com",
            "page_url": "https://partner.eu.tiktokshop.com/affiliate-cmp/creator?market=8",
            "aid": "360019", "user_language": "zh-CN",
        }
        identity_file = root / "identity.json"
        identity_file.write_text("{}", encoding="utf-8")
        self.account = SimpleNamespace(name="acc6", headers_json=identity_file, profile_dir=root)
        self.selected = SimpleNamespace(account=self.account, identity=self.identity)
        self.targets = root / "targets.json"
        self.targets.write_text(json.dumps({"targets": [{
            "ref": "fixture-1", "handle": "italian.creator", "externalId": "kalodata-1",
            "historicalOecHint": "123456789",
        }]}), encoding="utf-8")
        self.output = root / "report"
        self.client = None
        harness = self

        class FakeClient:
            def __init__(self, config, identity, scratch):
                harness.events.append("client")
                harness.identity_uses.append(identity)
                harness.client = self
                self.config = config
                self.business_retries = int(config.get("business_retries") or 3)
                self.captcha_attempts = int(config.get("captcha_attempts") or 3)
                self.request_count = 0
                self.challenge_count = 0
                self.captcha_success_count = 0
                self.captcha_replay_response_count = 0
                self.captcha_replay_code0_count = 0
                self.code10000_missing_header_count = 0
                self.captcha_by_stage = {}
                self.inside_post = False
                self.challenge_returned = False
                self.session = SimpleNamespace(close=lambda: harness.events.append("session_close"))

            def _signed_post_once(self, stage, _body):
                if not self.inside_post:
                    raise AssertionError("orchestration must use complete HTTP post")
                self.request_count += 1
                if stage == "find" and not self.challenge_returned:
                    self.challenge_returned = True
                    return SimpleNamespace(status_code=200, headers={"bdturing-verify": "private-verification"}), {"code": 0}
                reply = harness.replies.pop(0)
                if isinstance(reply, BaseException):
                    # The real SDK can emit diagnostics; the caller must
                    # suppress them and must not serialize exception text.
                    print("cookie=private-cookie token=private-token https://private.invalid/?secret=1")
                    raise reply
                return SimpleNamespace(status_code=200, headers={}), reply

            def _solve_captcha(self, _verify_data, _attempt):
                self.challenge_count += 1
                self.captcha_success_count += 1
                return {"code": 200, "success": True}

            def post(self, stage, body):
                # A tiny fake of the already-owned MX transport contract.
                # Calling only the low-level method cannot pass this test.
                harness.calls.append((stage, body))
                self.inside_post = True
                try:
                    response, payload = self._signed_post_once(stage, body)
                    if response.headers.get("bdturing-verify"):
                        self._solve_captcha({"opaque": "private-verification"}, 1)
                        response, payload = self._signed_post_once(stage, body)
                        self.captcha_replay_response_count += 1
                        self.captcha_replay_code0_count += 1
                        self.captcha_by_stage[stage] = {
                            "challenges": 1, "verify_successes": 1,
                            "replay_responses": 1, "replay_code0": 1,
                        }
                    return response, payload
                finally:
                    self.inside_post = False

        self.runtime = SimpleNamespace(
            PureHttpPartnerClient=FakeClient,
            find_exact=lambda payload, _handle: payload.get("exact"),
            signer_impl=SimpleNamespace(reset_signer=lambda: self.events.append("signer_reset")),
        )

        def configure(name):
            def apply(_runtime_or_client, identity, *_args):
                self.events.append(name)
                self.identity_uses.append(identity)
            return apply

        self.child = ModuleType("bdhub.enrich.pure_http_worker_child")
        self.child._load_runtime = lambda _: self.runtime
        self.child._configure_market_signer_runtime = configure("signer")
        self.child._configure_market_captcha_runtime = configure("captcha")
        self.child._configure_market_transport = configure("transport")
        self.child._oec = lambda row: field_value(row, "creator_oecuid")
        self.child._handle = lambda row: field_value(row, "handle").lower()
        self.child._missing_required_fields = lambda *_args: frozenset()

        self.worker = ModuleType("bdhub.enrich.pure_http_worker")
        self.worker.prepare_collection_accounts = lambda *_args, **_kwargs: ([self.selected], {})
        self.worker.validate_runtime = lambda _: root
        self.worker._DEFAULT_RUNTIME = root
        self.worker.scheduled_relogin_svc = SimpleNamespace(maintenance_due=lambda *_args, **_kwargs: False)
        self.config = ModuleType("bdhub.config")
        self.config.load = lambda: {}
        self.config.load_accounts = lambda _: [self.account]
        self.backoff = ModuleType("bdhub.enrich.shared_backoff")
        self.backoff.snapshot = lambda *_args: {"open": False}
        self.creator_profile = ModuleType("bdhub.enrich.creator_profile")
        self.creator_profile.merge_profiles = lambda rows: rows[0]
        self.enrich = ModuleType("bdhub.enrich")
        self.enrich.pure_http_worker = self.worker
        self.enrich.pure_http_worker_child = self.child
        self.bdhub = ModuleType("bdhub")
        self.bdhub.config = self.config
        self.bdhub.enrich = self.enrich
        self.modules = {module.__name__: module for module in (
            self.bdhub, self.config, self.enrich, self.worker, self.child,
            self.backoff, self.creator_profile,
        )}

    @contextlib.contextmanager
    def guard(self, account):
        if account is not self.account:
            raise AssertionError("different account used for guard")
        if self.busy:
            raise RuntimeError("account_in_use")
        self.events.append("guard_enter")
        try:
            yield
        finally:
            self.events.append("guard_exit")

    def run(self):
        # network_child intentionally changes child-global process state;
        # restore it here because unit tests share one process.
        saved_path = sys.path[:]
        saved_bytecode = sys.dont_write_bytecode
        saved_tempdir = tempfile.tempdir
        stdout = io.StringIO()
        stderr = io.StringIO()
        try:
            with patch.dict(sys.modules, self.modules), patch.dict(os.environ), \
                    patch.object(probe, "configure_vendored_bdhub", return_value=self.config), \
                    patch.object(probe, "get_readiness", return_value={"accounts": [{
                        "name": "acc6", "startable": self.startable,
                    }]}), patch.object(probe, "readonly_guard", self.guard), \
                    patch.object(probe.os, "umask"), patch.object(probe.signal, "signal"), \
                    patch.object(probe.signal, "setitimer") as timers, \
                    patch.object(probe.urllib.request, "build_opener", side_effect=AssertionError("network forbidden")), \
                    contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                result = probe.network_child("acc6", self.targets, self.output)
            self.timer_calls = timers.call_args_list
        finally:
            sys.path[:] = saved_path
            sys.dont_write_bytecode = saved_bytecode
            tempfile.tempdir = saved_tempdir
        self.stdout = stdout.getvalue()
        self.stderr = stderr.getvalue()
        return result, json.loads((self.output / "report.private.json").read_text())


class ProfileProbePipelineTests(unittest.TestCase):
    def test_remote_code_text_cannot_leak_sensitive_diagnostics(self):
        for remote_code in (
            "cookie=private-cookie token=private-token https://private.invalid/?secret=1",
            True, "１２３", "1" * 17,
        ):
            with self.subTest(code_type=type(remote_code).__name__):
                result = probe.classify_response(200, {}, {"code": remote_code})
                self.assertEqual(result["code"], "invalid")
                self.assertFalse(result["allowed"])
                self.assertNotIn("private", json.dumps(result))

    def test_counter_diagnostics_allow_only_nonnegative_integers_and_known_stages(self):
        counters = probe.collect_counters(SimpleNamespace(
            request_count=7, challenge_count=True, captcha_success_count="private-token",
            captcha_replay_response_count=-1, captcha_replay_code0_count=2.5,
            captcha_by_stage={
                "find": {"challenges": 2, "verify_successes": False, "token": "private-token"},
                "https://private.invalid/?secret=1": {"challenges": 99},
            },
        ))
        self.assertEqual(counters["request_count"], 7)
        for key in ("challenge_count", "captcha_success_count", "captcha_replay_response_count", "captcha_replay_code0_count"):
            self.assertEqual(counters[key], 0)
        self.assertEqual(set(counters["byStage"]), {"find", "profile"})
        self.assertEqual(counters["byStage"]["find"]["challenges"], 2)
        self.assertEqual(counters["byStage"]["find"]["verify_successes"], 0)
        self.assertNotIn("private", json.dumps(counters))

    def test_initialization_uses_same_identity_in_market_specific_order(self):
        with tempfile.TemporaryDirectory() as temporary:
            h = ProbeHarness(Path(temporary))
            client = probe.initialize_client(h.runtime, h.child, h.identity, h.root / "scratch")
            self.assertEqual(h.events, ["signer", "captcha", "client", "transport"])
            self.assertTrue(all(identity is h.identity for identity in h.identity_uses))
            self.assertEqual(client.business_retries, 2)
            self.assertEqual(client.captcha_attempts, 3)
            self.assertEqual(client.config["qps"], 1)

    def test_identity_only_stops_after_exact_it_find(self):
        with tempfile.TemporaryDirectory() as temporary:
            h=ProbeHarness(Path(temporary));data=json.loads(h.targets.read_text());data['identityOnly']=True;h.targets.write_text(json.dumps(data))
            code,report=h.run();self.assertEqual(code,0);self.assertEqual([stage for stage,_ in h.calls],['find'])
            self.assertEqual(report['targets'][0]['status'],'identity_verified');self.assertEqual(report['targets'][0]['profileCollection'],'not_requested')
            self.assertTrue(report['identityFileUnchanged'])
    def test_identity_only_falls_back_to_profiles_when_find_market_missing(self):
        with tempfile.TemporaryDirectory() as temporary:
            h=ProbeHarness(Path(temporary));h.replies[0]['exact'].pop('selection_region');data=json.loads(h.targets.read_text());data['identityOnly']=True;h.targets.write_text(json.dumps(data))
            code,report=h.run();self.assertEqual(code,0);self.assertEqual([stage for stage,_ in h.calls],['find','profile','profile'])
            self.assertEqual(report['targets'][0]['status'],'completed')

    def test_full_post_after_verification_continues_same_oec_profiles_and_merge(self):
        with tempfile.TemporaryDirectory() as temporary:
            h = ProbeHarness(Path(temporary))
            result, report = h.run()
            self.assertEqual(result, 0)
            self.assertEqual(report["status"], "completed")
            self.assertEqual([stage for stage, _ in h.calls], ["find", "profile", "profile"])
            self.assertEqual([body["profile_types"] for stage, body in h.calls if stage == "profile"], [[1, 2, 6], [2]])
            self.assertTrue(all(body["creator_oec_id"] == "123456789" for stage, body in h.calls if stage == "profile"))
            target = report["targets"][0]
            self.assertEqual(target["status"], "completed")
            self.assertTrue(target["currentPlatformIdentityVerified"])
            self.assertFalse(target["historicalCrossSourceIdentityProven"])
            fields = target["merged"]["fields"]
            self.assertEqual(fields["follower_cnt"]["value"], 100)
            self.assertEqual(fields["video_publish_cnt_30d"]["value"], 17)
            self.assertEqual(fields["units_sold"]["status"], "zero")
            self.assertTrue(report["identityFileUnchanged"])
            self.assertEqual(h.client.challenge_count, 1)
            self.assertEqual(h.client.captcha_success_count, 1)
            self.assertEqual(report["counters"]["captcha_success_count"], 1)
            self.assertEqual(report["counters"]["request_count"], 4)
            self.assertTrue(report["requests"][0]["attempts"][0]["verificationRequired"])
            self.assertTrue(report["requests"][0]["attempts"][1]["allowed"])
            self.assertEqual(report["requests"][0]["verificationAttempts"][0]["status"], "returned")
            self.assertNotIn("private-verification", json.dumps(report))
            self.assertEqual(h.events[-3:], ["session_close", "signer_reset", "guard_exit"])
            self.assertEqual(h.timer_calls[0].args, (signal.ITIMER_REAL, 175))
            self.assertEqual(h.timer_calls[-1].args, (signal.ITIMER_REAL, 0))

    def test_minimal_profile_canary_uses_only_allowlisted_type_two(self):
        with tempfile.TemporaryDirectory() as temporary:
            h=ProbeHarness(Path(temporary),replies=[
                {"code":0,"exact":profile()},
                {"code":0,"creator_profile":profile()},
            ])
            data=json.loads(h.targets.read_text());data['profileTypeSets']=[[2]]
            h.targets.write_text(json.dumps(data))
            code,report=h.run()
            self.assertEqual(code,0)
            self.assertEqual([stage for stage,_ in h.calls],['find','profile'])
            self.assertEqual([body['profile_types'] for stage,body in h.calls if stage=='profile'],[[2]])
            self.assertEqual(report['profileTypeSets'],[[2]])
            self.assertEqual(report['targets'][0]['status'],'completed')

    def test_profile_type_set_override_rejects_unapproved_shapes(self):
        with tempfile.TemporaryDirectory() as temporary:
            h=ProbeHarness(Path(temporary));data=json.loads(h.targets.read_text());data['profileTypeSets']=[[1,2]]
            h.targets.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError,'invalid_profile_type_sets'):h.run()

    def test_non_exact_handle_never_requests_profile_even_if_helper_returns_row(self):
        with tempfile.TemporaryDirectory() as temporary:
            h = ProbeHarness(Path(temporary), replies=[{
                "code": 0, "exact": profile(handle={"value": "italian.creator.similar"}),
            }])
            _, report = h.run()
            self.assertEqual(len(h.calls), 1)
            self.assertEqual(report["targets"][0]["reason"], "find_identity_invalid")
            self.assertNotIn("merged", report["targets"][0])

    def test_wrong_oec_or_market_profile_is_rejected_before_merge(self):
        for overrides, reason in (
            ({"creator_oecuid": {"value": "999999"}}, "profile_identity_mismatch"),
            ({"selection_region": {"value": "MX"}}, "profile_market_mismatch"),
        ):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as temporary:
                h = ProbeHarness(Path(temporary), replies=[
                    {"code": 0, "exact": profile()},
                    {"code": 0, "creator_profile": profile(**overrides)},
                ])
                result, report = h.run()
                self.assertEqual(result, 2)
                self.assertEqual(report["reason"], reason)
                self.assertEqual(len(h.calls), 2)
                self.assertNotIn("merged", report["targets"][0])
                self.assertEqual(h.events[-3:], ["session_close", "signer_reset", "guard_exit"])

    def test_not_ready_or_guard_busy_never_initializes_client(self):
        for kwargs in ({"startable": False}, {"busy": True}):
            with self.subTest(**kwargs), tempfile.TemporaryDirectory() as temporary:
                h = ProbeHarness(Path(temporary), **kwargs)
                result, report = h.run()
                self.assertEqual(result, 2)
                self.assertEqual(report["status"], "blocked")
                self.assertIsNone(h.client)
                self.assertEqual(h.calls, [])

    def test_sensitive_runtime_exception_is_not_logged_and_sdk_cleans_under_guard(self):
        with tempfile.TemporaryDirectory() as temporary:
            sensitive = "cookie=private-cookie token=private-token https://private.invalid/?secret=1"
            h = ProbeHarness(Path(temporary), replies=[RuntimeError(sensitive)])
            result, report = h.run()
            self.assertEqual(result, 2)
            serialized = json.dumps(report) + h.stdout + h.stderr
            for secret in ("private-cookie", "private-token", "private.invalid", "secret=1"):
                self.assertNotIn(secret, serialized)
            self.assertEqual(report["requests"][0]["errorType"], "RuntimeError")
            self.assertEqual(h.events[-3:], ["session_close", "signer_reset", "guard_exit"])

    def test_session_close_failure_still_resets_signer_before_releasing_guard(self):
        with tempfile.TemporaryDirectory() as temporary:
            h = ProbeHarness(Path(temporary))
            configure = h.child._configure_market_transport

            def fail_close():
                h.events.append("session_close")
                raise RuntimeError("cookie=private-close-cookie")

            def configure_with_close_failure(client, identity):
                configure(client, identity)
                client.session.close = fail_close

            h.child._configure_market_transport = configure_with_close_failure
            result, report = h.run()
            self.assertEqual(result, 2)
            self.assertEqual(h.events[-3:], ["session_close", "signer_reset", "guard_exit"])
            self.assertNotIn("private-close-cookie", json.dumps(report) + h.stdout + h.stderr)

    def test_identity_file_changed_before_guard_is_detected_without_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            h = ProbeHarness(Path(temporary))
            original_guard = h.guard

            @contextlib.contextmanager
            def changed_guard(account):
                with original_guard(account):
                    h.account.headers_json.write_text('{"version":2}', encoding="utf-8")
                    yield

            h.guard = changed_guard
            result, report = h.run()
            self.assertEqual(result, 2)
            self.assertEqual(report["reason"], "identity_changed_before_guard")
            self.assertEqual(h.calls, [])
            self.assertIsNone(h.client)
            self.assertFalse(report["identityFileUnchanged"])

    def test_deadline_is_not_swallowed_by_sdk_generic_exception_and_cleans(self):
        self.assertFalse(issubclass(probe.ProbeDeadline, Exception))
        with tempfile.TemporaryDirectory() as temporary:
            h = ProbeHarness(Path(temporary), replies=[probe.ProbeDeadline("whole_probe_deadline")])
            result, report = h.run()
            self.assertEqual(result, 2)
            self.assertEqual(report["status"], "bounded_timeout")
            self.assertEqual(report["reason"], "whole_probe_deadline")
            self.assertEqual(h.events[-3:], ["session_close", "signer_reset", "guard_exit"])


if __name__ == "__main__":
    unittest.main()
