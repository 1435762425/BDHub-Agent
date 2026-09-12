"""Provider contracts use fake transport/key/clock only; zero paid API requests."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import signal
import subprocess
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[1] / "scripts/lib/draft_provider.py"
SPEC = importlib.util.spec_from_file_location("draft_provider_tested", PATH)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
MESSAGES = [{"role": "system", "content": "只依据事实生成草稿。"}, {"role": "user", "content": "fixture facts"}]
SECRET = "FAKE_SECRET_NEVER_OUTPUT"
USAGE = {"prompt_tokens": 3000, "prompt_cache_hit_tokens": 1000, "prompt_cache_miss_tokens": 2000,
         "completion_tokens": 300, "completion_tokens_details": {"reasoning_tokens": 200}, "total_tokens": 3300,
         "prompt_tokens_details": {"cached_tokens": 1000}}


def clock(*values):
    times = [datetime.fromisoformat(value.replace("Z", "+00:00")) for value in values] or [datetime(2026, 9, 12, 1, tzinfo=timezone.utc)]
    def current():
        return times.pop(0) if len(times) > 1 else times[0]
    return current


def payload():
    return {"id": "completion-fixture-1", "model": M.MODEL, "usage": deepcopy(USAGE),
            "choices": [{"finish_reason": "stop", "message": {"content": '{"text":"Ciao"}',
                                                                  "reasoning_content": "PRIVATE_REASONING_NEVER_SAVE"}}]}


class Response:
    def __init__(self, value=None, *, status=200, error=None):
        self.value = payload() if value is None else value
        self.status_code = status
        self.error = error
        self.content = json.dumps(self.value).encode()

    def json(self):
        if self.error:
            raise self.error
        return deepcopy(self.value)


class HTTP:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response or Response(), error, []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return self.response


def call(http=None, **kwargs):
    return M.call_model(MESSAGES, http=http or HTTP(), clock=kwargs.pop("clock", clock()),
                        key_resolver=kwargs.pop("key_resolver", lambda: SECRET), **kwargs)


class DraftProviderTests(unittest.TestCase):
    def test_real_alarm_interrupts_blocked_fake_transport_without_network_or_sleep(self):
        script = r'''
import importlib.util,json,os,sys,time
spec=importlib.util.spec_from_file_location("provider",sys.argv[1]);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
reader,writer=os.pipe()
class HTTP:
    def post(self,*args,**kwargs):
        os.read(reader,1)
started=time.monotonic()
try:
    m.call_model([{"role":"user","content":"fixture"}],http=HTTP(),key_resolver=lambda:"fixture",timeout=0.05)
except m.DraftProviderError as error:
    print(json.dumps({"code":error.code,"outcome":error.outcome,"elapsed":time.monotonic()-started}))
finally:
    os.close(reader);os.close(writer)
'''
        result = subprocess.run([sys.executable, "-c", script, str(PATH)], capture_output=True, text=True, timeout=2)
        self.assertEqual(result.returncode, 0)
        actual = json.loads(result.stdout)
        self.assertEqual(actual["code"], "provider_timeout")
        self.assertEqual(actual["outcome"], "outcome_unknown")
        self.assertLess(actual["elapsed"], 1)

    def test_wall_deadline_escapes_http_exception_handlers_and_restores_signal(self):
        previous = signal.getsignal(signal.SIGALRM)
        class InterruptedHTTP(HTTP):
            def post(self, url, **kwargs):
                self.calls.append((url, kwargs))
                try:
                    signal.getsignal(signal.SIGALRM)(signal.SIGALRM, None)
                except Exception:
                    raise AssertionError("deadline must not be swallowed as a normal HTTP error")
        http = InterruptedHTTP()
        with self.assertRaises(M.DraftProviderError) as found:
            call(http)
        self.assertEqual(found.exception.code, "provider_timeout")
        self.assertEqual(found.exception.outcome, "outcome_unknown")
        self.assertEqual(len(http.calls), 1)
        self.assertEqual(signal.getsignal(signal.SIGALRM), previous)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))

    def test_deadline_during_content_validation_keeps_already_received_usage(self):
        def interrupted(_):
            signal.getsignal(signal.SIGALRM)(signal.SIGALRM, None)
        with patch.object(M, "_json_object", interrupted), self.assertRaises(M.DraftProviderError) as found:
            call()
        self.assertEqual(found.exception.code, "provider_timeout")
        self.assertEqual(found.exception.receipt["usage"]["completionTokens"], 300)
        self.assertTrue(found.exception.receipt["cost"]["complete"])
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))

    def test_nonmain_thread_and_occupied_alarm_fail_before_send(self):
        http, errors = HTTP(), []
        def work():
            try:
                call(http)
            except M.DraftProviderError as error:
                errors.append(error)
        thread = threading.Thread(target=work)
        thread.start()
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors[0].code, "provider_deadline_unavailable")
        self.assertEqual(errors[0].outcome, "request_not_sent")
        self.assertEqual(http.calls, [])
        prior_handler, prior_timer = signal.getsignal(signal.SIGALRM), signal.getitimer(signal.ITIMER_REAL)
        handler = lambda *_: None
        try:
            signal.signal(signal.SIGALRM, handler)
            signal.setitimer(signal.ITIMER_REAL, 20)
            with self.assertRaises(M.DraftProviderError) as found:
                call(http)
            self.assertEqual(found.exception.outcome, "request_not_sent")
            self.assertEqual(signal.getsignal(signal.SIGALRM), handler)
            self.assertTrue(19 < signal.getitimer(signal.ITIMER_REAL)[0] <= 20)
            self.assertEqual(http.calls, [])
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, prior_handler)
            signal.setitimer(signal.ITIMER_REAL, *prior_timer)

    def test_success_restores_an_existing_inactive_handler(self):
        previous = signal.getsignal(signal.SIGALRM)
        handler = lambda *_: None
        try:
            signal.signal(signal.SIGALRM, handler)
            call()
            self.assertEqual(signal.getsignal(signal.SIGALRM), handler)
            self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))
        finally:
            signal.signal(signal.SIGALRM, previous)

    def test_default_session_has_zero_retries_and_closes_without_a_real_transport(self):
        class Session(HTTP):
            def __init__(self):
                super().__init__()
                self.mounted, self.closed = [], False
            def mount(self, scheme, adapter):
                self.mounted.append((scheme, adapter))
            def close(self):
                self.closed = True
        session = Session()
        module = SimpleNamespace(Session=lambda: session, adapters=SimpleNamespace(HTTPAdapter=lambda **kwargs: kwargs))
        with patch.dict("sys.modules", {"requests": module}):
            result = M.call_model(MESSAGES, key_resolver=lambda: SECRET, clock=clock())
        self.assertEqual(session.mounted, [("https://", {"max_retries": 0})])
        self.assertTrue(callable(session.auth))
        self.assertEqual(len(session.calls), 1)
        self.assertTrue(session.closed)
        self.assertEqual(result["model"], M.MODEL)

    def test_fixed_official_endpoint_json_disabled_thinking_and_single_request(self):
        http = HTTP()
        result = call(http)
        self.assertEqual(len(http.calls), 1)
        url, request = http.calls[0]
        self.assertEqual(url, "https://api.deepseek.com/chat/completions")
        self.assertEqual(request["json"]["model"], "deepseek-flash")
        self.assertEqual(request["json"]["thinking"], {"type": "disabled"})
        self.assertEqual(request["json"]["response_format"], {"type": "json_object"})
        self.assertFalse(request["allow_redirects"])
        self.assertFalse(request["json"]["stream"])
        self.assertEqual(request["json"]["max_tokens"], 1200)
        self.assertIn("JSON", request["json"]["messages"][0]["content"])
        self.assertNotIn("JSON", MESSAGES[0]["content"])
        self.assertEqual(json.loads(result["content"]), {"text": "Ciao"})
        encoded = json.dumps(result)
        self.assertNotIn(SECRET, encoded)
        self.assertNotIn("PRIVATE_REASONING", encoded)

    def test_credential_priority_and_status_are_local_and_secret_free(self):
        def forbidden():
            raise AssertionError("legacy config should not be read")
        value = M.resolve_credential(environ={"DEEPSEEK_API_KEY": SECRET}, config_loader=forbidden)
        self.assertEqual(value, SECRET)
        status = M.provider_status(key_resolver=lambda: value)
        self.assertTrue(status["credentialReady"])
        self.assertTrue(status["ready"])
        self.assertFalse(status["availabilityVerified"])
        self.assertNotIn(SECRET, json.dumps(status))
        missing = M.provider_status(key_resolver=lambda: "")
        self.assertFalse(missing["ready"])
        self.assertEqual(missing["errorCode"], "provider_not_configured")

    def test_legacy_key_only_accepts_fixed_official_https_provider(self):
        for url in ("https://api.deepseek.com", "https://api.deepseek.com/v1", "https://api.deepseek.com:443/v1/"):
            self.assertEqual(M.resolve_credential(environ={}, config_loader=lambda: {"reply": {"api_key": SECRET, "base_url": url, "model": "deepseek-chat"}}), SECRET)
        for url in ("http://api.deepseek.com", "https://proxy.invalid/v1", "https://api.deepseek.com.evil.invalid", "https://user:secret@api.deepseek.com", "https://api.deepseek.com:8443", "https://api.deepseek.com?api_key=secret", "https://api.deepseek.com/other"):
            with self.subTest(url=url), self.assertRaises(M.DraftProviderError) as found:
                M.resolve_credential(environ={}, config_loader=lambda: {"reply": {"api_key": SECRET, "base_url": url}})
            self.assertEqual(found.exception.code, "provider_config_invalid")
            self.assertNotIn("secret", str(found.exception).lower())

    def test_input_limit_and_invalid_configuration_stop_before_http(self):
        http = HTTP()
        with self.assertRaises(M.DraftProviderError) as found:
            call(http, key_resolver=lambda: "")
        self.assertEqual(found.exception.outcome, "request_not_sent")
        self.assertTrue(all(value is None for value in found.exception.receipt["usage"].values()))
        self.assertEqual(found.exception.receipt["cost"]["estimatedCny"], "0")
        for messages in ([{"role": "user", "content": "汉" * 8000}], [{"role": "tool", "content": "tool"}], [{"role": [], "content": "bad"}], [{"role": "user", "content": "x", "tools": []}]):
            with self.assertRaises(M.DraftProviderError) as caught:
                M.call_model(messages, http=http, clock=clock(), key_resolver=lambda: SECRET)
            self.assertEqual(caught.exception.outcome, "request_not_sent")
        for limit in (0, 1201, True):
            with self.assertRaises(M.DraftProviderError):
                call(http, max_output_tokens=limit)
        self.assertEqual(http.calls, [])

    def test_prompt_hit_miss_and_reasoning_are_not_double_billed(self):
        result = call()
        self.assertEqual(result["usage"], {"promptTokens": 3000, "cacheHitTokens": 1000, "cacheMissTokens": 2000,
                                           "completionTokens": 300, "reasoningTokens": 200, "totalTokens": 3300})
        self.assertEqual(Decimal(result["cost"]["estimatedCny"]), Decimal("0.00322"))
        self.assertEqual(Decimal(result["cost"]["upperBoundCny"]), Decimal("0.00644"))
        self.assertTrue(result["cost"]["complete"])
        self.assertEqual(result["cost"]["window"], "off_peak")

    def test_weekday_peak_and_cross_window_keep_peak_budget_upper_bound(self):
        peak = call(clock=clock("2026-09-14T01:10:00Z", "2026-09-14T01:10:01Z"))
        self.assertEqual(peak["cost"]["window"], "peak")
        self.assertEqual(Decimal(peak["cost"]["estimatedCny"]), Decimal("0.00644"))
        crossed = call(clock=clock("2026-09-14T00:59:59Z", "2026-09-14T01:00:01Z"))
        self.assertEqual(crossed["cost"]["window"], "cross_window")
        self.assertIsNone(crossed["cost"]["estimatedCny"])
        self.assertEqual(Decimal(crossed["cost"]["upperBoundCny"]), Decimal("0.00644"))
        self.assertFalse(crossed["cost"]["complete"])
        long = call(clock=clock("2026-09-14T00:00:00Z", "2026-09-14T11:00:00Z"))
        self.assertEqual(long["cost"]["window"], "cross_window")

    def test_missing_usage_stays_null_with_unknown_cost(self):
        value = payload()
        value.pop("usage")
        result = call(HTTP(Response(value)))
        self.assertTrue(all(token is None for token in result["usage"].values()))
        self.assertIsNone(result["cost"]["estimatedCny"])
        self.assertIsNone(result["cost"]["upperBoundCny"])
        self.assertFalse(result["cost"]["complete"])
        value["usage"] = {"prompt_tokens": 3000, "completion_tokens": 300}
        partial = call(HTTP(Response(value)))
        self.assertEqual(partial["usage"]["promptTokens"], 3000)
        self.assertIsNone(partial["usage"]["cacheHitTokens"])
        self.assertIsNone(partial["cost"]["upperBoundCny"])

    def test_official_cached_token_alias_is_accepted_without_inventing_other_missing_fields(self):
        raw = deepcopy(USAGE)
        raw.pop("prompt_cache_hit_tokens")
        usage, valid = M.normalize_usage(raw)
        self.assertTrue(valid)
        self.assertEqual(usage["cacheHitTokens"], 1000)
        raw.pop("prompt_tokens")
        usage, valid = M.normalize_usage(raw)
        self.assertTrue(valid)
        self.assertIsNone(usage["promptTokens"])

    def test_inconsistent_usage_preserves_readable_counts_but_cannot_claim_cost(self):
        for change in ({"prompt_tokens": 2999}, {"prompt_tokens_details": {"cached_tokens": 999}},
                       {"completion_tokens_details": {"reasoning_tokens": 301}}, {"total_tokens": 5000},
                       {"prompt_cache_miss_tokens": True}):
            value = payload()
            value["usage"].update(change)
            http = HTTP(Response(value))
            with self.assertRaises(M.DraftProviderError) as found:
                call(http)
            error = found.exception
            self.assertEqual(error.code, "provider_usage_invalid")
            self.assertEqual(error.outcome, "outcome_unknown")
            self.assertEqual(error.receipt["usage"]["completionTokens"], 300)
            self.assertIsNone(error.receipt["cost"]["estimatedCny"])
            self.assertEqual(len(http.calls), 1)

    def test_bad_content_truncation_and_http_failure_keep_usage_receipts(self):
        for content in ("not JSON SECRET", "[]", '{"a":1,"a":2}', '{"a":NaN}'):
            value = payload()
            value["choices"][0]["message"]["content"] = content
            with self.assertRaises(M.DraftProviderError) as found:
                call(HTTP(Response(value)))
            error = found.exception
            self.assertEqual(error.code, "provider_output_invalid")
            self.assertEqual(error.receipt["usage"]["completionTokens"], 300)
            self.assertTrue(error.receipt["cost"]["complete"])
            self.assertNotIn("content", error.receipt)
            self.assertNotIn("SECRET", json.dumps(error.receipt))
        value = payload()
        value["choices"][0]["finish_reason"] = "length"
        with self.assertRaises(M.DraftProviderError) as found:
            call(HTTP(Response(value)))
        self.assertEqual(found.exception.code, "provider_output_truncated")
        with self.assertRaises(M.DraftProviderError) as found:
            call(HTTP(Response(payload(), status=500)))
        self.assertEqual(found.exception.code, "provider_http_error")
        self.assertEqual(found.exception.receipt["usage"]["cacheHitTokens"], 1000)

    def test_timeout_network_nonjson_and_redirect_never_retry_or_leak_raw_error(self):
        for http, expected in ((HTTP(error=TimeoutError(SECRET)), "provider_timeout"),
                               (HTTP(error=OSError(SECRET)), "provider_network_error"),
                               (HTTP(Response(error=ValueError(SECRET))), "provider_response_invalid"),
                               (HTTP(Response(status=302, error=ValueError(SECRET))), "provider_redirect_rejected")):
            with self.assertRaises(M.DraftProviderError) as found:
                call(http)
            error = found.exception
            self.assertEqual(error.code, expected)
            self.assertEqual(error.outcome, "outcome_unknown")
            self.assertEqual(len(http.calls), 1)
            self.assertNotIn(SECRET, str(error) + json.dumps(error.receipt))
            self.assertIsNone(error.receipt["cost"]["upperBoundCny"])

    def test_unexpected_model_preserves_usage_without_applying_flash_pricing(self):
        value = payload()
        value["model"] = "other-provider-model"
        with self.assertRaises(M.DraftProviderError) as found:
            call(HTTP(Response(value)))
        self.assertEqual(found.exception.code, "provider_model_mismatch")
        self.assertEqual(found.exception.receipt["usage"]["completionTokens"], 300)
        self.assertIsNone(found.exception.receipt["cost"]["upperBoundCny"])
        self.assertNotIn("other-provider-model", json.dumps(found.exception.receipt))


if __name__ == "__main__":
    unittest.main()
