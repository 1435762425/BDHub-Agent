"""Isolated fake-provider tests. No real policy, credentials, Node compiler or HTTP."""
import concurrent.futures
from copy import deepcopy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from lib.outreach_drafts import OutreachDraftStore,OutreachDraftWorker,OutreachDraftError,SKILL,validate_draft,validate_review,compile_context,safe_receipt
from lib.draft_provider import DraftProviderError,PRICING

SPEC=importlib.util.spec_from_file_location("outreach_cli_subject",ROOT/"scripts/outreach-drafts.py")
CLI=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(CLI)


def fingerprint(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()).hexdigest()
def request(packet="review-packet-test",style="friendly",instructions=""):
    return {"packetId":packet,"style":style,"instructions":instructions.strip()}

def context(req):
    return {"schema":"bdhub.outreach-draft-context.v1","binding":{"market":"it","registryCreatorId":"creator_"+"1"*32,"matchingCreatorId":"private-matching-id",
            "oecId":"1234567890123456789","runId":"private-run-id","runFingerprint":"r"*64,"packetId":req["packetId"],"packetFingerprint":"p"*64,
            "identityObservedAt":"2026-09-12T00:00:00Z","policyVersion":"it-intent-invitation@1","skillSha256":hashlib.sha256(SKILL.read_bytes()).hexdigest()},
            "modelFacts":{"intent":"explore_interest","language":"it","recipient":{"handle":"test_creator"},"style":req["style"],"instructions":req["instructions"],
                "products":[{"id":"p1","nameIt":"cuscino cervicale","sharedCategories":["家电"],"factIds":["p1-name","p1-fit"]}],
                "facts":[{"id":"recipient","kind":"recipient_handle","value":"test_creator"},{"id":"p1-name","kind":"product_name","value":"cuscino cervicale"},
                         {"id":"p1-fit","kind":"category_alignment","value":["家电"]}],"commercialTerms":{"status":"not_verified","allowedPromises":[]}},
            "productBindings":[{"id":"p1","productId":"private-product-id","pid":"9876543210987654321"}],
            "evidenceSources":[{"factId":"p1-name","sourceRef":"private-profile-source","observedAt":123}],"executionBlocked":True}

def draft():return {"textIt":"Ciao @test_creator! Ti va di valutare una collaborazione su cuscino cervicale?","translationZh":"你好！愿意聊聊颈枕的合作可能性吗？",
                   "selectedProductIds":["p1"],"evidenceRefs":["p1-name"],"rationaleZh":"使用给定商品探询兴趣，不承诺商业条件。"}
def review():return {"verdict":"pass","issues":[],"unsupportedClaims":[],"italianValid":True,"translationFaithful":True}
def receipt(output,unknown=False):return {"content":json.dumps(output,ensure_ascii=False),"model":"deepseek-flash","responseId":"response-test",
    "startedAt":"2026-09-12T01:00:00.123456Z","finishedAt":"2026-09-12T01:00:03.456789Z",
    "usage":{key:None if unknown else value for key,value in {"promptTokens":100,"cacheHitTokens":0,"cacheMissTokens":100,"completionTokens":100,"reasoningTokens":0,"totalTokens":200}.items()},
    "cost":{"estimatedCny":None if unknown else "0.001","upperBoundCny":None if unknown else "0.002","complete":not unknown,"pricingVersion":PRICING["version"],"window":"off_peak"}}


class OutreachDraftTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.var=Path(self.temporary.name);self.clock=1789174800.0;self.changed=False;self.compiles=[];self.calls=[]
        self.policy={"version":1,"id":"test-trial","enabled":True,"model":"deepseek-flash","maxDrafts":3,"maxCostCny":"1.00"}
        self.write_policy()
        self.store=OutreachDraftStore(self.var,now=lambda:self.clock,context_validator=self.compile,provider_status=lambda:{"ready":True,"model":"deepseek-flash"})
        self.addCleanup(self.store.close)

    def write_policy(self):
        (self.var/"outreach-model-policy.json").write_text(json.dumps(self.policy))

    def compile(self,req):
        self.compiles.append(deepcopy(req));value=context(req)
        if self.changed:value["binding"]["runFingerprint"]="changed"
        return {"context":value,"fingerprint":fingerprint(value),"contextRequest":req}

    def enqueue(self,request_id="request-1",req=None):
        req=req or request();value=context(req)
        return self.store.enqueue(request_id,value,fingerprint(value),req)

    def provider(self,messages,**kwargs):
        self.calls.append((deepcopy(messages),kwargs));data=json.loads(messages[-1]["content"])
        return receipt(review() if "draft" in data else draft())

    def worker(self,provider=None):return OutreachDraftWorker(self.store,provider=provider or self.provider)

    def test_disabled_default_never_enables_policy_or_calls_compiler(self):
        (self.var/"outreach-model-policy.json").unlink()
        self.assertFalse(self.store.status()["policy"]["enabled"])
        with self.assertRaises(OutreachDraftError) as error:self.enqueue()
        self.assertEqual(error.exception.code,"model_disabled");self.assertEqual(self.compiles,[])
        self.assertFalse((self.var/"outreach-model-policy.json").exists());self.assertEqual(self.calls,[])

    def test_two_stage_success_keeps_provider_input_public_and_never_becomes_sendable(self):
        queued=self.enqueue();result=self.worker().run_once();detail=self.store.detail(queued["id"])
        self.assertEqual(result["status"],"drafted");self.assertEqual(len(self.calls),2)
        self.assertEqual([a["stage"] for a in detail["attempts"]],["draft","review"])
        self.assertEqual(detail["content"],draft());self.assertEqual(detail["review"],review())
        self.assertTrue(result["executionBlocked"]);self.assertTrue(detail["executionBlocked"])
        for messages,kwargs in self.calls:
            encoded=json.dumps(messages)
            for secret in ("1234567890123456789","private-matching-id","private-profile-source","private-run-id","9876543210987654321"):
                self.assertNotIn(secret,encoded)
            self.assertEqual(kwargs,{"max_output_tokens":1200,"timeout":60});self.assertLessEqual(len(encoded.encode()),24000)
        self.assertEqual(detail["cost"],{"estimatedCny":"0.002","upperBoundCny":"0.004","complete":True})
        self.assertEqual(result["reservedCostCny"],"0.00")
        self.assertEqual(self.store.status()["budget"],{"reservedCny":"0.00","knownCostCny":"0.002","availableCny":"0.996","usedDrafts":1})
        self.assertEqual(detail["products"],[{"id":"p1","nameIt":"cuscino cervicale"}])

    def test_same_request_recovers_old_paid_result_even_when_current_context_changed(self):
        job=self.enqueue(req=request(instructions="hello"));self.worker().run_once();before=len(self.compiles);self.changed=True
        same=self.store.lookup_request("request-1",request(instructions=" hello "))
        self.assertEqual(same["id"],job["id"]);self.assertEqual(len(self.compiles),before)
        changed=context(request(instructions="hello"));changed["binding"]["runFingerprint"]="new"
        replay=self.store.enqueue("request-1",changed,"f"*64,request(instructions=" hello "))
        self.assertEqual(replay["id"],job["id"]);self.assertEqual(len(self.calls),2)
        with self.assertRaises(OutreachDraftError) as conflict:self.store.lookup_request("request-1",request(style="direct",instructions="hello"))
        self.assertEqual(conflict.exception.code,"request_conflict")

    def test_creator_history_survives_reopen_across_packets_without_policy_or_provider(self):
        first=self.enqueue("one",request(packet="review-packet-one"));self.clock+=1
        second=self.enqueue("two",request(packet="review-packet-two",style="direct"));self.clock+=1
        other_request=request(packet="review-packet-other");other=context(other_request)
        other["binding"]["registryCreatorId"]="creator_"+"2"*32
        with patch.object(self.store,"validate_context",return_value={"context":other,"fingerprint":fingerprint(other)}):
            self.store.enqueue("other",other,fingerprint(other),other_request)
        self.store.close();(self.var/"outreach-model-policy.json").unlink()
        never=Mock(side_effect=AssertionError("history cannot invoke context compilation or provider readiness"))
        with OutreachDraftStore(self.var,now=lambda:self.clock,context_validator=never,provider_status=never) as reopened:
            history=reopened.list_creator_drafts("creator_"+"1"*32)
            self.assertEqual([row["id"] for row in history["drafts"]],[second["id"],first["id"]])
            self.assertEqual({row["packetId"] for row in history["drafts"]},{"review-packet-one","review-packet-two"})
            self.assertEqual(len(reopened.list_creator_drafts("creator_"+"2"*32)["drafts"]),1)
            self.assertEqual(reopened.list_creator_drafts("creator_"+"3"*32)["drafts"],[])
            never.assert_not_called()

    def test_creator_history_requires_canonical_id_not_handle_oec_or_extra_cli_input(self):
        for invalid in ("test_creator","1234567890123456789","creator_"+"A"*32,"creator_1",None):
            with self.subTest(value=invalid),self.assertRaises(OutreachDraftError):self.store.list_creator_drafts(invalid)
        self.assertEqual(CLI.FIELDS["creator_history"],{"creatorId"})
        never=Mock(side_effect=AssertionError("invalid input cannot open storage"));output=io.StringIO()
        with patch.object(CLI.sys,"argv",["drafts","creator_history"]),patch.object(CLI.sys,"stdin",io.StringIO(json.dumps({"creatorId":"creator_"+"1"*32,"handle":"test_creator"}))),patch.object(CLI,"OutreachDraftStore",never),patch("sys.stdout",output):
            self.assertEqual(CLI.main(),1)
        self.assertEqual(json.loads(output.getvalue())["error"]["code"],"invalid_request");never.assert_not_called()

    def test_concurrent_trial_budget_is_atomic_and_is_not_a_daily_limit(self):
        def enqueue(index):
            req=request(packet="packet-"+str(index));value=context(req)
            with OutreachDraftStore(self.var,now=lambda:self.clock,context_validator=self.compile,provider_status=lambda:{"ready":True}) as store:
                try:return store.enqueue("parallel-"+str(index),value,fingerprint(value),req)["id"]
                except OutreachDraftError as error:return error.code
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:results=list(executor.map(enqueue,range(8)))
        self.assertEqual(sum(value.startswith("outreach_draft_") for value in results),3)
        self.assertEqual(results.count("trial_limit_reached"),5)
        self.assertEqual(self.store.status()["budget"]["reservedCny"],"0.75")
        self.clock+=86400
        with self.assertRaises(OutreachDraftError) as error:self.enqueue("next-day")
        self.assertEqual(error.exception.code,"trial_limit_reached")

    def test_cost_cap_can_block_before_three_slots(self):
        self.policy["maxCostCny"]="0.50";self.write_policy()
        self.enqueue("one");self.enqueue("two",request(packet="two"))
        with self.assertRaises(OutreachDraftError) as error:self.enqueue("three",request(packet="three"))
        self.assertEqual(error.exception.code,"budget_exhausted")

    def test_stale_before_first_call_releases_reservation_and_slot(self):
        job=self.enqueue();self.changed=True;result=self.worker().run_once()
        self.assertEqual(result["status"],"stale");self.assertEqual(self.calls,[])
        self.assertEqual(result["reservedCostCny"],"0.00");self.assertEqual(self.store.status()["budget"]["usedDrafts"],0)
        self.assertEqual(self.store.lookup_request("request-1",request())["id"],job["id"])

    def test_stale_before_review_retains_generator_and_does_not_call_reviewer(self):
        job=self.enqueue()
        def provider(messages,**kwargs):
            result=self.provider(messages,**kwargs);self.changed=True;return result
        result=self.worker(provider).run_once();detail=self.store.detail(job["id"])
        self.assertEqual(result["status"],"stale");self.assertEqual(len(self.calls),1)
        self.assertEqual(detail["content"],draft());self.assertIsNone(detail["review"])
        self.assertEqual(detail["facts"],context(request())["modelFacts"]["facts"])
        self.assertEqual(result["knownCostCny"],"0.001");self.assertEqual(self.store.status()["budget"]["usedDrafts"],1)

    def test_stale_before_final_save_never_marks_passed_review_current(self):
        self.enqueue()
        def provider(messages,**kwargs):
            result=self.provider(messages,**kwargs)
            if len(self.calls)==2:self.changed=True
            return result
        result=self.worker(provider).run_once();self.assertEqual(result["status"],"stale");self.assertEqual(len(self.calls),2)

    def test_semantic_reviewer_must_check_promises_even_when_schema_and_refs_are_valid(self):
        self.enqueue();count=0
        def provider(messages,**kwargs):
            nonlocal count;count+=1
            if count==1:
                content=draft();content["textIt"]+=" Il campione è gratuito.";content["translationZh"]+="样品免费。";return receipt(content)
            checked=review();checked["unsupportedClaims"]=["A free sample is not supported by the supplied facts."];return receipt(checked)
        result=self.worker(provider).run_once();self.assertEqual(result["status"],"needs_review");self.assertEqual(count,2)
        self.assertTrue(result["executionBlocked"])

    def test_invalid_draft_never_enters_review_and_cost_is_still_recorded(self):
        job=self.enqueue()
        def provider(*_args,**_kwargs):
            content=draft();content["textIt"]+=" https://unapproved.invalid";return receipt(content)
        result=self.worker(provider).run_once();detail=self.store.detail(job["id"])
        self.assertEqual(result["status"],"failed");self.assertEqual(result["errorCode"],"draft_validation_failed")
        self.assertEqual(len(detail["attempts"]),1);self.assertIsNone(detail["content"]);self.assertEqual(result["knownCostCny"],"0.001")

    def test_unknown_usage_preserves_response_and_reservation_and_stops_second_call(self):
        job=self.enqueue();provider=Mock(return_value=receipt(draft(),unknown=True))
        result=self.worker(provider).run_once();detail=self.store.detail(job["id"])
        self.assertEqual(result["status"],"result_unknown");self.assertEqual(result["errorCode"],"usage_unavailable")
        self.assertEqual(result["reservedCostCny"],"0.25");self.assertIsNone(result["knownCostCny"])
        self.assertEqual(detail["content"],draft());self.assertIsNone(detail["review"])
        self.assertIsNone(self.worker(provider).run_once());self.assertEqual(provider.call_count,1)

    def test_timeout_has_no_retry_and_never_claims_zero_cost(self):
        job=self.enqueue();provider=Mock(side_effect=DraftProviderError("provider_timeout",outcome="outcome_unknown"))
        result=self.worker(provider).run_once();detail=self.store.detail(job["id"])
        self.assertEqual(result["status"],"result_unknown");self.assertIsNone(detail["cost"]["estimatedCny"])
        self.assertEqual(detail["attempts"][0]["status"],"result_unknown");self.assertEqual(provider.call_count,1)
        self.assertEqual(result["reservedCostCny"],"0.25")

    def test_known_not_sent_failure_frees_unused_budget_and_slot(self):
        self.enqueue();provider=Mock(side_effect=DraftProviderError("provider_not_configured",outcome="request_not_sent"))
        result=self.worker(provider).run_once();self.assertEqual(result["status"],"failed")
        self.assertEqual(result["knownCostCny"],"0.00");self.assertEqual(result["reservedCostCny"],"0.00");self.assertEqual(self.store.status()["budget"]["usedDrafts"],0)

    def test_recovery_with_saved_generation_calls_only_review(self):
        job=self.enqueue();old=self.worker();claimed=self.store.claim(old.owner);self.store.begin_stage(claimed,old.owner,"draft")
        self.store.receipt(claimed,old.owner,"draft",receipt(draft()))
        self.clock+=241;result=self.worker().run_once()
        self.assertEqual(result["status"],"drafted");self.assertEqual(len(self.calls),1)
        self.assertIn("draft",json.loads(self.calls[0][0][-1]["content"]))
        self.assertEqual(self.store.detail(job["id"])["cost"]["estimatedCny"],"0.002")

    def test_provider_time_and_pricing_receipt_survive_recovery_without_ui_leakage(self):
        job=self.enqueue();old=self.worker();claimed=self.store.claim(old.owner);self.store.begin_stage(claimed,old.owner,"draft")
        self.store.receipt(claimed,old.owner,"draft",receipt(draft()))
        original=self.store.attempts(job["id"])[0]["receipt_json"]
        self.clock+=241;self.worker().run_once();restored=self.store.attempts(job["id"])[0]["receipt_json"]
        self.assertEqual(restored,original);value=json.loads(restored)
        self.assertEqual(value["startedAt"],"2026-09-12T01:00:00.123456Z")
        self.assertEqual(value["finishedAt"],"2026-09-12T01:00:03.456789Z")
        self.assertEqual(value["cost"]["pricingVersion"],PRICING["version"])
        self.assertEqual(value["cost"]["window"],"off_peak")
        public=self.store.detail(job["id"])
        self.assertEqual(set(public["attempts"][0]["cost"]),{"estimatedCny","upperBoundCny","complete"})
        self.assertNotIn("pricingVersion",json.dumps(public));self.assertNotIn("response-test",json.dumps(public))

    def test_receipt_metadata_is_allowlisted_and_malformed_private_values_are_dropped(self):
        value=receipt(draft());value.update(startedAt="https://secret.invalid/key",finishedAt="2026-09-12T00:00:00Z\nSECRET_TOKEN",apiKey="SECRET_API_KEY",headers={"Cookie":"SECRET_COOKIE"})
        value["cost"].update(pricingVersion="SECRET_PRICE_TOKEN",window={"cookie":"SECRET_WINDOW"},rawUrl="https://secret.invalid")
        cleaned=safe_receipt(value)
        self.assertIsNone(cleaned["startedAt"]);self.assertIsNone(cleaned["finishedAt"])
        self.assertIsNone(cleaned["cost"]["pricingVersion"]);self.assertEqual(cleaned["cost"]["window"],"unknown")
        self.assertNotIn("SECRET",json.dumps(cleaned));self.assertNotIn("secret.invalid",json.dumps(cleaned))
        for invalid in ("deepseek-flash-cny-2026-99-99","deepseek-flash-cny-2026-09-12?key=SECRET"):
            value["cost"]["pricingVersion"]=invalid;self.assertIsNone(safe_receipt(value)["cost"]["pricingVersion"])

    def test_cross_window_receipt_keeps_quote_version_and_upper_bound_without_inventing_fee(self):
        value=receipt(draft());value["cost"].update(window="cross_window",estimatedCny=None,complete=False)
        cleaned=safe_receipt(value)
        self.assertEqual(cleaned["cost"]["window"],"cross_window");self.assertEqual(cleaned["cost"]["pricingVersion"],PRICING["version"])
        self.assertEqual(cleaned["cost"]["upperBoundCny"],"0.002");self.assertIsNone(cleaned["cost"]["estimatedCny"])
        self.assertFalse(cleaned["cost"]["complete"])

    def test_recovery_with_both_receipts_never_calls_either_model_again(self):
        self.enqueue();old=self.worker();claimed=self.store.claim(old.owner)
        for stage,output in (("draft",draft()),("review",review())):
            self.store.begin_stage(claimed,old.owner,stage);self.store.receipt(claimed,old.owner,stage,receipt(output))
        self.clock+=241;provider=Mock(side_effect=AssertionError("durable responses must be reused"))
        result=self.worker(provider).run_once();self.assertEqual(result["status"],"drafted");provider.assert_not_called()

    def test_recovery_with_known_not_sent_receipt_keeps_failure_without_model_retry(self):
        self.enqueue();old=self.worker();claimed=self.store.claim(old.owner);self.store.begin_stage(claimed,old.owner,"draft")
        self.store.receipt(claimed,old.owner,"draft",None,error="provider_not_configured",sent=False)
        self.clock+=241;provider=Mock(side_effect=AssertionError("known failure is not a retry authorization"));result=self.worker(provider).run_once()
        self.assertEqual(result["status"],"failed");self.assertEqual(result["reservedCostCny"],"0.00");self.assertEqual(self.store.status()["budget"]["usedDrafts"],0);provider.assert_not_called()

    def test_provider_error_with_known_usage_still_preserves_unknown_outcome_reserve(self):
        self.enqueue();partial=receipt(draft());partial["content"]=None
        provider=Mock(side_effect=DraftProviderError("provider_output_truncated",outcome="outcome_unknown",receipt=partial))
        result=self.worker(provider).run_once();self.assertEqual(result["status"],"result_unknown")
        self.assertEqual(result["knownCostCny"],"0.001");self.assertEqual(result["reservedCostCny"],"0.25");self.assertEqual(provider.call_count,1)

    def test_over_reservation_stops_review_and_prevents_further_policy_spending(self):
        self.policy["maxCostCny"]="0.50";self.write_policy();self.enqueue("one");self.enqueue("two",request(packet="second-packet"))
        excessive=receipt(draft());excessive["cost"]={"estimatedCny":"0.30","upperBoundCny":"0.30","complete":True}
        provider=Mock(return_value=excessive);first=self.worker(provider).run_once()
        self.assertEqual(first["status"],"result_unknown");self.assertEqual(first["errorCode"],"reservation_exceeded");self.assertEqual(first["reservedCostCny"],"0.3")
        second=self.worker(provider).run_once();self.assertEqual(second["errorCode"],"budget_exhausted");self.assertEqual(provider.call_count,1)

    def test_only_one_running_relationship_job_is_claimed_for_the_same_creator(self):
        self.enqueue("one");self.enqueue("two",request(packet="second-packet"))
        first=self.store.claim("worker-one");self.assertIsNotNone(first);self.assertIsNone(self.store.claim("worker-two"))
        self.store.finish(first,"worker-one","failed","context_unavailable")
        second=self.store.claim("worker-two");self.assertIsNotNone(second);self.assertNotEqual(first["id"],second["id"])

    def test_untrusted_context_or_changed_name_fact_is_rejected_before_budget(self):
        value=context(request());value["modelFacts"]["products"][0]["nameIt"]="unverified item"
        with self.assertRaises(OutreachDraftError) as error:self.store.enqueue("forged",value,fingerprint(value),request())
        self.assertEqual(error.exception.code,"context_stale");self.assertEqual(self.store.status()["budget"]["usedDrafts"],0)

    def test_inflight_without_receipt_becomes_unknown_after_restart_not_a_second_request(self):
        self.enqueue();old=self.worker();claimed=self.store.claim(old.owner);self.store.begin_stage(claimed,old.owner,"draft")
        self.clock+=241;provider=Mock(side_effect=AssertionError("no duplicate model request"));result=self.worker(provider).run_once()
        self.assertEqual(result["status"],"result_unknown");self.assertEqual(result["reservedCostCny"],"0.25");provider.assert_not_called()

    def test_older_worker_cannot_write_response_after_lease_fence_changes(self):
        self.enqueue();old=self.worker();claimed=self.store.claim(old.owner);self.store.begin_stage(claimed,old.owner,"draft")
        self.clock+=241;new=self.worker();self.store.claim(new.owner)
        with self.assertRaises(OutreachDraftError) as error:self.store.receipt(claimed,old.owner,"draft",receipt(draft()))
        self.assertEqual(error.exception.code,"stale_lease")
        self.assertIsNone(self.store.attempts(claimed["id"])[0]["receipt_json"])

    def test_policy_mutation_cannot_silently_expand_an_existing_trial(self):
        self.enqueue();self.policy["maxCostCny"]="0.75";self.write_policy()
        with self.assertRaises(OutreachDraftError) as error:self.enqueue("changed-policy",request(packet="another"))
        self.assertEqual(error.exception.code,"policy_changed")
        self.assertEqual(self.worker().run_once()["errorCode"],"policy_changed");self.assertEqual(self.calls,[])

    def test_lexical_validation_keeps_product_model_digits_but_rejects_wrong_target_or_references(self):
        facts=context(request())["modelFacts"];good=draft()
        for field,replacement in (("selectedProductIds",["p2"]),("evidenceRefs",["invented"]),("textIt","Ciao @other! cuscino cervicale")):
            with self.subTest(field=field),self.assertRaises(OutreachDraftError):validate_draft({**good,field:replacement},facts)
        facts=deepcopy(facts);facts["products"][0]["nameIt"]="multimetro digitale NJTY T3"
        good["textIt"]="Ciao @test_creator! Ti interessa multimetro digitale NJTY T3?"
        self.assertEqual(validate_draft(good,facts)["selectedProductIds"],["p1"])
        checked=review();checked["translationFaithful"]=False;self.assertEqual(validate_review(checked)["verdict"],"needs_review")

    def test_fixed_context_compiler_is_bounded_and_errors_are_static(self):
        with patch("lib.outreach_drafts.subprocess.run",side_effect=RuntimeError("private filesystem and secret token")) as execute:
            with self.assertRaises(OutreachDraftError) as error:compile_context(request())
        self.assertEqual(error.exception.code,"context_unavailable");self.assertEqual(execute.call_args.kwargs["timeout"],15)
        self.assertEqual(execute.call_args.args[0][:2],["/opt/homebrew/bin/node","--experimental-strip-types"])
        self.assertNotIn("secret",str(error.exception))


if __name__=="__main__":unittest.main()
