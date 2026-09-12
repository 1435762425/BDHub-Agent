"""Fake HTTP coverage for Italy IM auth. No tokens, network or old writes."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from lib.italy_im_auth import authenticate_it, ItalyImAuthError, ImProbeDeadline, ENDPOINTS, PARTNER_HOST


def identity(**changes):
    return SimpleNamespace(**({"market": "it", "host": PARTNER_HOST, "aid": "360019", "im_market": "8",
                              "partner_id": "1234567890123456789", "partner_id_is_own": True} | changes))


def payloads():
    return [{"code": 0, "data": {"partner_biz_role_info": {"company_region": "MX", "market_list": [
        {"market_region": "8", "market_id": "2222222222222222222", "type_list": [{"type": 4, "partner_id": "1234567890123456789"}]}]},
        "partner_info": {"company_name": "PRIVATE_COMPANY"}}},
        {"code": 0, "data": {"im_id": "3333333333333333333"}},
        {"code": 0, "data": {"token": "PRIVATE_IM_TOKEN", "api_url": "https://observed-im.example.invalid", "app_id": 123}}]


class Clock:
    def __init__(self): self.value = 0.0
    def now(self): return self.value
    def sleep(self, seconds): self.value += seconds


class HTTP:
    def __init__(self, data=None, *, fail_at=None, status=200, verification=False):
        self.data, self.calls, self.fail_at, self.status, self.verification = data or payloads(), [], fail_at, status, verification
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.fail_at == len(self.calls):
            raise RuntimeError("PRIVATE_COOKIE_OR_RAW_RESPONSE")
        data = deepcopy(self.data[len(self.calls) - 1])
        return SimpleNamespace(status_code=self.status, headers={"bdturing-verify": "PRIVATE_VERIFY"} if self.verification else {}, json=lambda: data)


def run(http=None, **kwargs):
    http, report, timer = http or HTTP(), {}, Clock()
    result = authenticate_it(SimpleNamespace(name="acc6", enabled=True), kwargs.pop("account_identity", identity()),
        {"cookie": "sessionid=PRIVATE_COOKIE", "user-agent": "fixture UA", "Origin": "old-origin"}, report,
        http=http, monotonic=timer.now, sleep=timer.sleep, **kwargs)
    return result, report, http, timer


class ItalyImAuthTests(unittest.TestCase):
    def test_exact_eu_chain_and_private_context_do_not_leak_to_report(self):
        result, report, http, timer = run()
        self.assertEqual([url for url, _ in http.calls], list(ENDPOINTS.values()))
        self.assertEqual(http.calls[0][1]["params"]["partner_type"], 1)
        self.assertEqual(http.calls[1][1]["params"]["user_id"], "2222222222222222222")
        self.assertEqual(http.calls[1][1]["params"]["type"], 0)
        self.assertEqual(http.calls[2][1]["params"]["im_id"], "3333333333333333333")
        self.assertTrue(all(not kwargs["allow_redirects"] for _, kwargs in http.calls))
        self.assertTrue(all(kwargs["headers"]["Origin"] == PARTNER_HOST for _, kwargs in http.calls))
        self.assertEqual(timer.value, 2)
        self.assertEqual(result.token["token"], "PRIVATE_IM_TOKEN")
        self.assertEqual(result.native_context["market_region"], "8")
        self.assertFalse(any(key.lower() == "cookie" for key in result.im_headers))
        self.assertFalse(report["imHostRequested"])
        self.assertFalse(report["imHttpConversationVerified"])
        self.assertEqual(report["apiHost"], "observed-im.example.invalid")
        self.assertEqual(report["sendRequests"], 0)
        text = json.dumps(report) + repr(result)
        for value in ("PRIVATE_", "1234567890123456789", "2222222222222222222", "3333333333333333333"):
            self.assertNotIn(value, text)

    def test_wrong_identity_and_maintenance_stop_before_any_request(self):
        for change in ({"market": "mx"}, {"host": "https://evil.invalid"}, {"aid": "359713"}, {"im_market": "19"}, {"partner_id_is_own": False}):
            http = HTTP()
            with self.assertRaises(ItalyImAuthError): run(http, account_identity=identity(**change))
            self.assertEqual(http.calls, [])
        for option in ({"maintenance_due": lambda: True}, {"stopped": lambda: True}):
            http = HTTP()
            with self.assertRaises(ItalyImAuthError): run(http, **option)
            self.assertEqual(http.calls, [])

    def test_market_and_own_partner_must_match_one_row_before_id_or_token(self):
        for mutate in (
            lambda row: row.update(market_region="19"),
            lambda row: row["type_list"][0].update(partner_id="999"),
            lambda row: row.update(market_id=True),
        ):
            data = payloads();mutate(data[0]["data"]["partner_biz_role_info"]["market_list"][0]);http = HTTP(data)
            with self.assertRaises(ItalyImAuthError): run(http)
            self.assertEqual(len(http.calls), 1)
        data = payloads();rows=data[0]["data"]["partner_biz_role_info"]["market_list"];rows.append(deepcopy(rows[0]));http=HTTP(data)
        with self.assertRaises(ItalyImAuthError): run(http)
        self.assertEqual(len(http.calls), 1)

    def test_success_looking_verification_or_wrong_code_never_advances(self):
        for http, code in ((HTTP(verification=True), "verification_required"), (HTTP(status=302), "redirect_rejected")):
            with self.assertRaises(ItalyImAuthError) as found: run(http)
            self.assertEqual(found.exception.code, code)
            self.assertEqual(len(http.calls), 1)
        for code in (False, "0", 10000, "PRIVATE_REMOTE_CODE"):
            data=payloads();data[0]["code"]=code;http=HTTP(data)
            with self.assertRaises(ItalyImAuthError): run(http)
            self.assertEqual(len(http.calls), 1)

    def test_request_failure_is_sanitized_without_retry_and_token_identity_is_checked(self):
        http=HTTP(fail_at=2)
        with self.assertRaises(ItalyImAuthError) as found: run(http)
        self.assertEqual(found.exception.code,"transport_error");self.assertEqual(len(http.calls),2)
        self.assertNotIn("PRIVATE",str(found.exception))
        data=payloads();data[2]["data"]["im_id"]="444";http=HTTP(data)
        with self.assertRaises(ItalyImAuthError) as found: run(http)
        self.assertEqual(found.exception.code,"token_identity_mismatch")

    def test_im_endpoint_is_only_observed_and_unsafe_urls_are_rejected(self):
        for url in ("http://host.invalid","https://user:pass@host.invalid","https://host.invalid?token=SECRET","https://host.invalid/send","https://host.invalid:443"):
            data=payloads();data[2]["data"]["api_url"]=url;http=HTTP(data)
            with self.assertRaises(ItalyImAuthError) as found: run(http)
            self.assertEqual(found.exception.code,"im_endpoint_invalid")
            self.assertTrue(all(url.startswith(PARTNER_HOST+"/") for url,_ in http.calls))

    def test_wall_deadline_escapes_plain_http_exception_handlers(self):
        class Interrupted(HTTP):
            def get(self,*args,**kwargs):
                try: raise ImProbeDeadline()
                except Exception: raise AssertionError("must escape ordinary catch")
        with self.assertRaises(ImProbeDeadline): run(Interrupted())


if __name__ == "__main__": unittest.main()
