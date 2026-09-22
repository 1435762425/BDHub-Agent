"""IT canary wire fixtures. All HTTP objects below are in-memory fakes."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from lib.italy_im_auth import ItalyImAuthContext, ImProbeDeadline
from lib.italy_im_session import ItalyImReadSession, ItalyImReadError, VerifiedConversation, IM_HOST,SG_IM_HOST, _proto
from lib.italy_im_delivery import (ItalyImDeliveryAdapter, ItalyImDeliveryError, build_create_payload,
    build_text_packet, decode_send_candidate, verify_history_body, CREATE_PATH, SEND_PATH,
    ItalyVerifiedProductCard, card_binding_sha256, build_card_packet, verify_card_history_body,
    CARD_CONTENT, CARD_TITLE_KEY, CARD_ORIGIN)

W = _proto()
REQUEST = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
TEXT = "Ciao! Ti interessa parlarne?"


def card(**changes):
    values = {"product_id": "123", "list_id": "456", "campaign_id": "789", "campaign_name": "Fixture campaign",
              "list_name": "Fixture BJN card", "market": "it", "account_name": "acc6", "verified_at": "2026-09-12T01:00:00Z",
              "origin": CARD_ORIGIN, "evidence_sha256": "a" * 64, "verified": True}
    values.update(changes)
    values.setdefault("binding_sha256", card_binding_sha256(values))
    return ItalyVerifiedProductCard(**values)


def card_message(**changes):
    value = card()
    ext = {"type": "product_list", "product_id": value.product_id, "list_id": value.list_id,
           "campaign_id": value.campaign_id, "starling_content_key": value.title_key}
    ext.update(changes.pop("ext_changes", {}))
    return message(text=changes.pop("text", CARD_CONTENT), ext_changes=ext, **changes)


def auth(**changes):
    row = {"market_region": 8, "market_id": "99", "type_list": [{"type": 1, "partner_id": "11"}, {"type": 4, "partner_id": "44"}]}
    partner = {"partner_info": {"company_name": "Fixture own company"}, "avatar_url": "",
               "partner_biz_role_info": {"company_region": 1, "market_list": [row]}}
    source = {"partner": partner, "market_row": row, "market_id": "99", "partner_id": "11", "market_region": "8"}
    values = {"account_name": "acc6", "im_id": "987", "token": {"token": "PRIVATE_TOKEN", "api_url": "https://" + IM_HOST, "app_id": 1128},
              "native_context": source, "im_headers": {"user-agent": "fixture"}, "next_request_at": 0.0}
    values.update(changes)
    return ItalyImAuthContext(**values)


def info(cid=10, oec="100", kind=2):
    extension = b"".join(W.vb(11, W.vb(1, key) + W.vb(2, value)) for key, value in {"creator_oec_id": oec, "market_region": "8", "shop_region": "IT"}.items())
    return W.vb(1, "full-" + str(cid)) + W.vi(2, cid) + W.vi(3, kind) + W.vb(4, "PRIVATE_TICKET") + W.vb(50, extension)


def envelope(command, sequence, body, status=0):
    return W.vi(1, command) + W.vi(2, sequence) + W.vi(3, status) + W.vb(6, W.vb(command, body))


def message(*, full=b"full-10", cid=10, oec="100", server_id=777, sender=987, text=TEXT, client=REQUEST, kind=1000, ext_changes=None):
    ext = {"s:client_message_id": client, "creator_oec_id": oec, "shop_region": "IT", "type": "text", "sender_im_id": "99"}
    ext.update(ext_changes or {})
    data = W.vb(1, full) + W.vi(3, server_id) + W.vi(5, cid) + W.vi(6, kind) + W.vi(7, sender) + W.vb(8, text)
    return data + b"".join(W.vb(9, W.vb(1, key) + W.vb(2, value)) for key, value in ext.items() if value is not None)


class Clock:
    def __init__(self): self.value = 0.0
    def now(self): return self.value
    def sleep(self, value): self.value += value


class HTTP:
    def __init__(self):
        self.calls = [];self.send_hook = None;self.create_hook = None;self.history = None;self.history_hook = None
    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url.endswith(CREATE_PATH):
            if self.create_hook: return self.create_hook(kwargs)
            return SimpleNamespace(status_code=200, headers={}, json=lambda: {"code": 0, "data": {"conversation_short_id": "10"}})
        fields = W.wire_fields(kwargs["data"]);command, sequence = W.one(fields, 1), W.one(fields, 2)
        if command == 100:
            body = W.wire_fields(W.one(W.wire_fields(W.one(fields, 8)), 100));client = W.one(body, 8)
            ext = {W.one(W.wire_fields(row), 1).decode(): W.one(W.wire_fields(row), 2).decode() for row in body[5]}
            self.history = W.vb(1, message(text=W.one(body, 4), client=client, ext_changes=ext))
            if self.send_hook: return self.send_hook(sequence, body)
            answer = W.vi(1, 777) + W.vi(3, 0) + W.vb(4, client) + W.vi(5, 0)
        elif command == 608:
            answer = W.vb(1, info())
        elif command == 301:
            if self.history_hook:return self.history_hook(command, sequence)
            answer = self.history if self.history is not None else b""
        else:raise AssertionError("unexpected command")
        return SimpleNamespace(status_code=200, headers={}, content=envelope(command, sequence, answer))


def fixture(**session_options):
    clock, http, report, context = Clock(), HTTP(), {}, auth()
    session = ItalyImReadSession(context, report, http=http, monotonic=clock.now, sleep=clock.sleep, sequence=100, **session_options)
    adapter = ItalyImDeliveryAdapter(context, session, wall_time=lambda: 1789174800)
    return context, session, adapter, http, clock, report


def allow(scope):
    return {**scope, "dispatchAllowed": True}


class ItalyImDeliveryTests(unittest.TestCase):
    def test_br_packet_uses_br_identity_host_and_shop_region(self):
        row={"market_region":16,"market_id":"199","type_list":[{"type":1,"partner_id":"111"},{"type":4,"partner_id":"144"}]}
        partner={"partner_info":{"company_name":"Fixture BR company"},"avatar_url":"",
                 "partner_biz_role_info":{"company_region":"BR","market_list":[row]}}
        context=ItalyImAuthContext("acc1","1987",{"token":"PRIVATE_TOKEN","api_url":"https://"+SG_IM_HOST,"app_id":1128},
          {"market":"br","account":"acc1","market_region":"16","partner_host":"https://partner.tiktokshop.com",
           "im_host":SG_IM_HOST,"partner":partner,"market_row":row,"market_id":"199","partner_id":"111"},
          {"user-agent":"fixture"},0)
        conversation=VerifiedConversation("10",b"full-10",2,b"PRIVATE_TICKET","100")
        payload=build_create_payload(context,"100",created_at_ms=123)
        self.assertEqual(payload["biz_hook_ext"]["market_region"],"16")
        self.assertEqual(payload["options"]["api_url"],"https://"+SG_IM_HOST)
        packet=build_text_packet(context,conversation,"Olá!",REQUEST,sequence=200)
        body=W.wire_fields(W.one(W.wire_fields(W.one(W.wire_fields(packet.data),8)),100))
        ext={W.one(W.wire_fields(item),1).decode():W.one(W.wire_fields(item),2).decode() for item in body[5]}
        self.assertEqual(ext["shop_region"],"BR")
        self.assertEqual((packet.market,packet.market_region),("br","16"))

    def test_unrelated_legacy_region_does_not_override_exact_it_target(self):
        conv=VerifiedConversation('10',b'full-10',2,b'ticket','100')
        old=W.vb(1,message(server_id=666,client=OTHER,ext_changes={'shop_region':'TH'}))
        target=W.vb(1,card_message())
        args=dict(sender_id='987',market_id='99',text=CARD_CONTENT,request_ref=REQUEST,message_id='777',card=card())
        self.assertEqual(verify_history_body(old+target,conv,**args),'777')
        with self.assertRaises(ItalyImDeliveryError):verify_history_body(old+W.vb(1,card_message(ext_changes={'shop_region':'TH'})),conv,**args)
        with self.assertRaises(ItalyImDeliveryError):verify_history_body(W.vb(1,message(cid=11,client=OTHER))+target,conv,**args)

    def test_card_binding_requires_exact_it_account_verified_origin_and_hash(self):
        mutations = ({"market": "mx"}, {"account_name": "acc1"}, {"verified": False}, {"verified": 1},
                     {"origin": "https://other.invalid/product_list/list"}, {"verified_at": "2026-09-12T01:00:00"},
                     {"verified_at": "not-time"}, {"evidence_sha256": "bad"}, {"binding_sha256": "0" * 64},
                     {"product_id": 123}, {"product_id": "0"}, {"list_id": "0"}, {"campaign_id": "-1"},
                     {"title_key": "wrong_title"}, {"list_name": "bad\x00name"})
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(ItalyImDeliveryError) as error:card(**mutation)
            self.assertEqual(error.exception.code, "it_delivery_card_binding_invalid")
        value = card()
        self.assertEqual(value.binding_sha256, card_binding_sha256(value))
        self.assertEqual(card(campaign_id="0").campaign_id, "0")
        with self.assertRaises(ItalyImDeliveryError):replace(value, list_id="457")
        self.assertNotIn("Fixture", repr(value))

    def test_card_packet_matches_mx_native_shape_with_it_sender_and_fixed_metadata(self):
        context, session, _, _, _, _ = fixture();conversation = session.conversation("10", "100")
        packet = build_card_packet(context, conversation, card(), REQUEST, sequence=200)
        env = W.wire_fields(packet.data);body = W.wire_fields(W.one(W.wire_fields(W.one(env, 8)), 100))
        ext = {W.one(W.wire_fields(row), 1).decode(): W.one(W.wire_fields(row), 2).decode() for row in body[5]}
        self.assertEqual(W.one(body, 4), CARD_CONTENT.encode());self.assertEqual(W.one(body, 6), 1000)
        self.assertEqual(W.one(body, 8), REQUEST.encode());self.assertEqual(W.one(env, 9), b"987")
        self.assertEqual(ext, {"PIGEON_BIZ_TYPE": "1", "sender_role": "4", "sender_im_role": "4", "sender_im_id": "99",
            "shop_region": "IT", "monitor_send_message_platform": "pc", "type": "product_list", "product_id": "123",
            "list_id": "456", "campaign_id": "789", "campaign_name": "Fixture campaign", "list_name": "Fixture BJN card",
            "starling_content_key": CARD_TITLE_KEY})
        with self.assertRaises(ItalyImDeliveryError):build_card_packet(context, conversation, {}, REQUEST, sequence=200)

    def test_card_gate_requires_component_and_exact_product_list_campaign_binding(self):
        mutations = ({"componentKind": None}, {"componentKind": "text"}, {"productId": None}, {"productId": "124"},
                     {"listId": "457"}, {"campaignId": "790"}, {"bindingSha256": "0" * 64})
        for mutation in mutations:
            _, session, adapter, http, _, _ = fixture();conversation = session.conversation("10", "100");before = len(http.calls)
            with self.subTest(mutation=mutation), self.assertRaises(ItalyImDeliveryError) as error:
                adapter.send_card_once(conversation, card(), REQUEST, before_dispatch=lambda scope: {**allow(scope), **mutation})
            self.assertEqual(error.exception.outcome, "not_submitted");self.assertEqual(len(http.calls), before)

    def test_text_also_requires_explicit_component_kind_in_callback(self):
        _, session, adapter, http, _, _ = fixture();conversation = session.conversation("10", "100");before = len(http.calls)
        with self.assertRaises(ItalyImDeliveryError):
            adapter.send_once(conversation, TEXT, REQUEST, before_dispatch=lambda scope: {"dispatchAllowed": True,
                "requestRef": scope["requestRef"], "stage": scope["stage"]})
        self.assertEqual(len(http.calls), before)

    def test_card_candidate_then_exact_readback_and_component_scope(self):
        _, session, adapter, http, _, report = fixture();conversation = session.conversation("10", "100");scopes = []
        def gate(scope):scopes.append(scope);return allow(scope)
        candidate = adapter.send_card_once(conversation, card(), REQUEST, before_dispatch=gate)
        self.assertTrue(candidate["accepted"]);self.assertNotIn("confirmed", candidate)
        result = adapter.readback_card(conversation, card(), REQUEST, message_id=candidate["messageId"])
        self.assertEqual(result["status"], "confirmed");self.assertEqual(result["messageId"], "777")
        self.assertEqual(scopes[0]["componentKind"], "card");self.assertEqual(scopes[0]["productId"], "123")
        self.assertEqual(scopes[0]["origin"], CARD_ORIGIN);self.assertEqual(scopes[0]["evidenceSha256"], "a" * 64)
        self.assertEqual(report["sendRequests"], 1)
        self.assertEqual(sum(url.endswith(SEND_PATH) for url, _ in http.calls), 1)
        self.assertFalse(any(url.endswith(CREATE_PATH) or "product_list/list" in url for url, _ in http.calls))
        with self.assertRaises(ItalyImDeliveryError):adapter.send_card_once(conversation, card(), REQUEST, before_dispatch=allow)
        with self.assertRaises(ItalyImDeliveryError):adapter.send_once(conversation, TEXT, REQUEST, before_dispatch=allow)
        self.assertEqual(sum(url.endswith(SEND_PATH) for url, _ in http.calls), 1)

    def test_card_readback_rejects_all_identity_content_type_and_card_metadata_mismatches(self):
        mutations = ({"cid": 11}, {"full": b"full-other"}, {"oec": "101"}, {"text": "wrong"}, {"client": OTHER},
                     {"sender": 988}, {"kind": 1001}, {"server_id": 778}, {"ext_changes": {"type": "text"}},
                     {"ext_changes": {"product_id": "124"}}, {"ext_changes": {"list_id": "457"}},
                     {"ext_changes": {"campaign_id": "790"}}, {"ext_changes": {"starling_content_key": "wrong"}},
                     {"ext_changes": {"s:visible": "hidden"}}, {"ext_changes": {"visibility_type": "1"}},
                     {"ext_changes": {"shop_region": "MX"}}, {"ext_changes": {"sender_im_id": "1000"}})
        mutations += tuple({"ext_changes": {key: None}} for key in ("type", "product_id", "list_id", "campaign_id", "starling_content_key"))
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                _, session, adapter, http, _, _ = fixture();conversation = session.conversation("10", "100")
                http.history = W.vb(1, card_message(**mutation))
                result = adapter.readback_card(conversation, card(), REQUEST, message_id="777")
                self.assertEqual(result["status"], "result_unknown");self.assertIsNone(result["messageId"])
                self.assertFalse(any(url.endswith(SEND_PATH) or url.endswith(CREATE_PATH) for url, _ in http.calls))

    def test_card_absent_ambiguous_or_uncorrelated_history_stays_unknown(self):
        _, session, adapter, http, _, _ = fixture();conversation = session.conversation("10", "100")
        self.assertEqual(adapter.readback_card(conversation, card(), REQUEST)["status"], "result_unknown")
        http.history = W.vb(1, card_message()) + W.vb(1, card_message(server_id=778))
        self.assertEqual(adapter.readback_card(conversation, card(), REQUEST)["reason"], "it_delivery_ambiguous")
        http.history_hook = lambda command, sequence: SimpleNamespace(status_code=200, headers={}, content=envelope(command, sequence+1, W.vb(1, card_message())))
        self.assertEqual(adapter.readback_card(conversation, card(), REQUEST)["status"], "result_unknown")

    def test_lost_card_response_recovers_by_uuid_without_post_retry(self):
        _, session, adapter, http, _, _ = fixture();conversation = session.conversation("10", "100")
        http.send_hook = lambda *_: (_ for _ in ()).throw(TimeoutError())
        with self.assertRaises(ItalyImDeliveryError) as error:adapter.send_card_once(conversation, card(), REQUEST, before_dispatch=allow)
        self.assertEqual(error.exception.outcome, "result_unknown")
        self.assertEqual(adapter.readback_card(conversation, card(), REQUEST)["status"], "confirmed")
        self.assertEqual(sum(url.endswith(SEND_PATH) for url, _ in http.calls), 1)

    def test_card_explicit_rejection_and_state_change_before_dispatch_make_no_false_success(self):
        _, session, adapter, http, _, _ = fixture();conversation = session.conversation("10", "100")
        http.send_hook = lambda sequence, body: SimpleNamespace(status_code=200, headers={},
            content=envelope(100, sequence, W.vi(3, 2) + W.vb(4, REQUEST)))
        with self.assertRaises(ItalyImDeliveryError) as error:adapter.send_card_once(conversation, card(), REQUEST, before_dispatch=allow)
        self.assertEqual(error.exception.outcome, "rejected")
        _, session, adapter, http, _, _ = fixture();conversation = session.conversation("10", "100");before = len(http.calls)
        def gate(scope):session.stopped = lambda: True;return allow(scope)
        with self.assertRaises(ItalyImDeliveryError) as error:adapter.send_card_once(conversation, card(), REQUEST, before_dispatch=gate)
        self.assertEqual(error.exception.outcome, "not_submitted");self.assertEqual(len(http.calls), before)

    def test_native_create_uses_own_roles_company_and_it_host_without_message_content(self):
        value = build_create_payload(auth(), "100", created_at_ms=123)
        self.assertEqual(value["participants"], [
            {"role": 0, "uid": "100", "extra": {"sender_role": "1", "creator_oec_id": "100"}},
            {"role": 1, "uid": "99", "extra": {"sender_role": "4", "agency_market_id": "99", "tap_id": "44", "cap_id": "11"}}])
        self.assertEqual(value["options"]["api_url"], "https://" + IM_HOST)
        self.assertEqual(value["biz_hook_ext"]["market_region"], "8")
        self.assertEqual(value["biz_hook_ext"]["agency_company_name"], "Fixture own company")
        self.assertEqual(value["biz_hook_ext"]["createConversationTime"], "123")
        self.assertNotIn("PRIVATE_TOKEN", json.dumps(value));self.assertNotIn("content", value)

    def test_invalid_account_host_or_non_own_role_is_rejected_before_http(self):
        original = auth();bad_role = deepcopy(original.native_context);bad_role["partner_id"] = "55"
        for value in (replace(original, account_name="acc1"), replace(original, token={**original.token, "api_url": "https://" + W.READ_HOST}),
                      replace(original, native_context=bad_role), replace(original, im_headers={"cookie": "PRIVATE_COOKIE"})):
            with self.subTest(account=value.account_name), self.assertRaises(ItalyImDeliveryError) as error:
                build_create_payload(value, "100", created_at_ms=1)
            self.assertEqual(error.exception.outcome, "not_submitted")
        for oec in (100, True, 1.1, "0", "bad", "9" * 100):
            with self.subTest(oec_type=type(oec).__name__), self.assertRaises(ItalyImDeliveryError):build_create_payload(original, oec, created_at_ms=1)

    def test_it_packet_has_native_cmd_text_client_id_and_sender_extensions_without_mx_override(self):
        context, session, _, _, _, _ = fixture();conversation = session.conversation("10", "100")
        packet = build_text_packet(context, conversation, TEXT, REQUEST, sequence=200)
        env = W.wire_fields(packet.data);body = W.wire_fields(W.one(W.wire_fields(W.one(env, 8)), 100))
        extensions = {W.one(W.wire_fields(row), 1).decode(): W.one(W.wire_fields(row), 2).decode() for row in body[5]}
        self.assertEqual(W.one(env, 1), 100);self.assertEqual(W.one(env, 9), b"987")
        self.assertEqual(W.one(body, 6), 1000);self.assertEqual(W.one(body, 8), REQUEST.encode());self.assertEqual(W.one(body, 4), TEXT.encode())
        self.assertEqual(extensions["shop_region"], "IT");self.assertEqual(extensions["sender_im_id"], "99");self.assertEqual(extensions["type"], "text")
        self.assertNotIn("PRIVATE", repr(packet));self.assertNotIn(TEXT, repr(packet))
        self.assertEqual(W.READ_HOST, "oec-im-tt-sg.tiktokglobalshopv.com")
        with self.assertRaises(ValueError):W.validate_packet("https://" + IM_HOST + SEND_PATH, {}, packet.data, cid="10", text=TEXT)
        with self.assertRaises(ItalyImReadError):session._read(100, b"")

    def test_create_and_send_need_exact_last_moment_acknowledgement(self):
        gates = (None, lambda _: None, lambda _: {"dispatchAllowed": False, "requestRef": REQUEST, "stage": "create_conversation"},
                 lambda _: {"dispatchAllowed": 1, "requestRef": REQUEST, "stage": "create_conversation"},
                 lambda _: {"dispatchAllowed": True, "requestRef": OTHER, "stage": "create_conversation"},
                 lambda _: {"dispatchAllowed": True, "requestRef": REQUEST, "stage": "wrong_stage"})
        for gate in gates:
            with self.subTest(gate=gate):
                _, session, adapter, http, _, _ = fixture()
                with self.assertRaises(ItalyImDeliveryError) as error:adapter.create_once("100", REQUEST, before_dispatch=gate)
                self.assertEqual(error.exception.outcome, "not_submitted");self.assertEqual(http.calls, [])
                conversation = session.conversation("10", "100");before = len(http.calls)
                with self.assertRaises(ItalyImDeliveryError) as error:adapter.send_once(conversation, TEXT, REQUEST, before_dispatch=gate)
                self.assertEqual(error.exception.outcome, "not_submitted");self.assertEqual(len(http.calls), before)

    def test_dispatch_shares_read_session_pacer_but_callback_remains_the_authority(self):
        _, session, adapter, http, clock, report = fixture();session.next_request_at = 5
        scopes = []
        def gate(scope):
            scopes.append((clock.value, scope));return allow(scope)
        created = adapter.create_once("100", REQUEST, before_dispatch=gate)
        self.assertTrue(created["candidate"]);self.assertIsNone(created["isNew"]);self.assertEqual(session.verified, {})
        conversation = session.conversation(created["conversationId"], "100")
        candidate = adapter.send_once(conversation, TEXT, OTHER, before_dispatch=gate)
        self.assertEqual([stamp for stamp, _ in scopes], [5, 7]);self.assertEqual(report["conversationCreateRequests"], 1);self.assertEqual(report["sendRequests"], 1)
        self.assertEqual(candidate["messageId"], "777");self.assertTrue(candidate["accepted"])
        self.assertEqual(set(candidate), {"conversationId", "requestRef", "messageId", "evidenceRef", "accepted"})
        self.assertEqual(scopes[-1][1]["oecId"], "100");self.assertEqual(scopes[-1][1]["account"], "acc6");self.assertEqual(scopes[-1][1]["market"], "it")
        self.assertTrue(all(url.startswith("https://" + IM_HOST + "/") for url, _ in http.calls))
        self.assertTrue(all(kwargs["allow_redirects"] is False for _, kwargs in http.calls))

    def test_wrong_or_other_session_conversation_is_not_sendable(self):
        _, session, adapter, http, _, _ = fixture();good = session.conversation("10", "100");before = len(http.calls)
        _, other, _, _, _, _ = fixture();foreign = other.conversation("10", "100")
        for value in (replace(good, oec_id="101"), replace(good, conversation_id="11"), foreign):
            with self.subTest(oec=value.oec_id), self.assertRaises(ItalyImDeliveryError) as error:adapter.send_once(value, TEXT, REQUEST, before_dispatch=allow)
            self.assertEqual(error.exception.outcome, "not_submitted")
        self.assertEqual(len(http.calls), before)

    def test_timeouts_do_not_retry_and_create_candidate_oec_mismatch_is_unknown(self):
        _, session, adapter, http, _, _ = fixture()
        http.create_hook = lambda _: (_ for _ in ()).throw(TimeoutError("PRIVATE_ERROR"))
        with self.assertRaises(ItalyImDeliveryError) as error:adapter.create_once("100", REQUEST, before_dispatch=allow)
        self.assertEqual(error.exception.outcome, "result_unknown");self.assertEqual(len(http.calls), 1);self.assertNotIn("PRIVATE", str(error.exception))
        with self.assertRaises(ItalyImDeliveryError):adapter.create_once("100", REQUEST, before_dispatch=allow)
        self.assertEqual(len(http.calls), 1)
        _, _, adapter, http, _, _ = fixture()
        http.create_hook = lambda _: SimpleNamespace(status_code=200, headers={}, json=lambda: {"code": 0, "data": {"conversation_short_id": "10", "creator_oec_id": "999"}})
        with self.assertRaises(ItalyImDeliveryError) as error:adapter.create_once("100", REQUEST, before_dispatch=allow)
        self.assertEqual(error.exception.outcome, "result_unknown");self.assertEqual(len(http.calls), 1)

    def test_native_rejection_is_distinct_from_unknown_and_wrong_client_cannot_be_a_rejection(self):
        context, session, _, _, _, _ = fixture();conversation = session.conversation("10", "100")
        packet = build_text_packet(context, conversation, TEXT, REQUEST, sequence=2)
        for client, outcome in ((REQUEST, "rejected"), (OTHER, "result_unknown")):
            wire = envelope(100, 2, W.vi(3, 2) + W.vb(4, client) + W.vi(5, 100))
            with self.subTest(client=client), self.assertRaises(ItalyImDeliveryError) as error:decode_send_candidate(wire, packet)
            self.assertEqual(error.exception.outcome, outcome)
        for wire in (envelope(100, 3, W.vi(1, 777)), envelope(301, 2, W.vi(1, 777)), envelope(100, 2, W.vi(1, 777), status=500)):
            with self.assertRaises(ItalyImDeliveryError) as error:decode_send_candidate(wire, packet)
            self.assertEqual(error.exception.outcome, "result_unknown")

    def test_accepted_candidate_is_not_confirmed_until_exact_readback(self):
        _, session, adapter, http, _, _ = fixture();conversation = session.conversation("10", "100")
        candidate = adapter.send_once(conversation, TEXT, REQUEST, before_dispatch=allow)
        self.assertNotIn("confirmed", candidate)
        result = adapter.readback(conversation, TEXT, REQUEST, message_id=candidate["messageId"])
        self.assertEqual(result["status"], "confirmed");self.assertEqual(result["messageId"], "777")
        self.assertEqual(sum(url.endswith(SEND_PATH) for url, _ in http.calls), 1)
        self.assertNotIn(TEXT, json.dumps(result));self.assertNotIn("PRIVATE", json.dumps(result))

    def test_readback_rejects_wrong_cid_oec_text_client_sender_type_or_visibility(self):
        mutations = ({"cid": 11}, {"full": b"full-other"}, {"oec": "101"}, {"text": "wrong"}, {"client": OTHER},
                     {"sender": 988}, {"kind": 1001}, {"ext_changes": {"type": "product_list"}},
                     {"ext_changes": {"s:visible": "hidden"}}, {"ext_changes": {"visibility_type": "1"}},
                     {"ext_changes": {"shop_region": "MX"}}, {"ext_changes": {"sender_im_id": "1000"}})
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                _, session, adapter, http, _, _ = fixture();conversation = session.conversation("10", "100")
                http.history = W.vb(1, message(**mutation))
                result = adapter.readback(conversation, TEXT, REQUEST, message_id="777")
                self.assertEqual(result["status"], "result_unknown");self.assertIsNone(result["messageId"])
                self.assertFalse(any(url.endswith(SEND_PATH) or url.endswith(CREATE_PATH) for url, _ in http.calls))

    def test_missing_or_ambiguous_history_stays_unknown_and_supplied_wrong_receipt_is_rejected(self):
        _, session, adapter, http, _, _ = fixture();conversation = session.conversation("10", "100")
        self.assertEqual(adapter.readback(conversation, TEXT, REQUEST)["status"], "result_unknown")
        http.history = W.vb(1, message())
        self.assertEqual(adapter.readback(conversation, TEXT, REQUEST, message_id="778")["status"], "result_unknown")
        http.history += W.vb(1, message(server_id=778))
        self.assertEqual(adapter.readback(conversation, TEXT, REQUEST)["reason"], "it_delivery_ambiguous")

    def test_lost_send_response_can_be_read_back_by_client_uuid_without_second_send(self):
        _, session, adapter, http, _, _ = fixture();conversation = session.conversation("10", "100")
        http.send_hook = lambda *_: (_ for _ in ()).throw(TimeoutError())
        with self.assertRaises(ItalyImDeliveryError) as error:adapter.send_once(conversation, TEXT, REQUEST, before_dispatch=allow)
        self.assertEqual(error.exception.outcome, "result_unknown")
        self.assertEqual(adapter.readback(conversation, TEXT, REQUEST)["status"], "confirmed")
        self.assertEqual(sum(url.endswith(SEND_PATH) for url, _ in http.calls), 1)

    def test_history_correlation_is_required_even_when_message_fields_look_right(self):
        _, session, adapter, http, _, _ = fixture();conversation = session.conversation("10", "100")
        http.history_hook = lambda command, sequence: SimpleNamespace(status_code=200, headers={}, content=envelope(command, sequence+1, W.vb(1, message())))
        self.assertEqual(adapter.readback(conversation, TEXT, REQUEST)["status"], "result_unknown")

    def test_post_deadline_is_unknown_but_preflight_pause_is_definitely_not_submitted(self):
        _, _, adapter, http, _, _ = fixture(stopped=lambda: True)
        with self.assertRaises(ItalyImDeliveryError) as error:adapter.create_once("100", REQUEST, before_dispatch=allow)
        self.assertEqual(error.exception.outcome, "not_submitted");self.assertEqual(http.calls, [])
        _, session, adapter, http, _, _ = fixture();conversation = session.conversation("10", "100")
        http.send_hook = lambda *_: (_ for _ in ()).throw(ImProbeDeadline())
        with self.assertRaises(ItalyImDeliveryError) as error:adapter.send_once(conversation, TEXT, REQUEST, before_dispatch=allow)
        self.assertEqual(error.exception.outcome, "result_unknown");self.assertEqual(error.exception.code, "it_delivery_wall_timeout")


if __name__ == "__main__":unittest.main()
