import test from "node:test";
import assert from "node:assert/strict";
import {mkdtempSync,rmSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {buildItalySecondCases} from "../src/server/second-pilot/source.ts";
import {draftSecondIntro} from "../src/server/second-pilot/skills.ts";
import {evaluateSecondPilot} from "../src/server/second-pilot/evaluate.ts";
import {parseSecondPilotCommand,parseSecondPilotQuery} from "../src/server/second-pilot/validation.ts";
import {SecondPilotStore} from "../src/server/second-pilot/store.ts";
import {rehearsalDecision} from "../src/server/second-pilot/rehearsal-plan.ts";

const source={ref:"fixture:exact-sale",observedAt:10,windowStart:1,windowEnd:5,windowBasis:"calendar_date_unknown_timezone"};
function input(){return {products:[{id:"p1",market:"it",pid:"1729480019490150432",title:"颈枕"},{id:"p2",market:"it",pid:"1729502070782139035",title:"瑜伽裤"}],creators:[{id:"c1",market:"it",name:"@fixture_creator",externalIdentity:{namespace:"kalodata",id:"7123456789012345678"},oecId:null}],evidence:[{id:"e1",creatorId:"c1",market:"it",pid:"1729480019490150432",units:4,source},{id:"e2",creatorId:"c1",market:"it",pid:"1729502070782139035",units:8,source}]};}
test("second opportunity merges exact products into one bounded Italian exploratory draft",()=>{
  const cases=buildItalySecondCases(input(),"italy-pilot-fixture");assert.equal(cases.length,1);assert.equal(cases[0].products.length,2);assert.equal(cases[0].creatorRef.id,"7123456789012345678");assert.equal(cases[0].draft.method,"deterministic");
  assert(cases[0].draft.text.includes("cuscino cervicale"));assert(cases[0].draft.text.includes("leggings da yoga"));assert(!/%|commission|campion|gratuit|video|hai già|garanti/i.test(cases[0].draft.text));assert(cases[0].liveBlockers.length>0);
  assert.deepEqual(buildItalySecondCases(input(),"italy-pilot-fixture"),cases);assert(cases[0].draft.text.length<900);
});
test("only positive exact same-market product observations can enter the pilot",()=>{
  for(const mutate of [s=>s.evidence[0].units=0,s=>s.evidence[0].market="mx",s=>s.evidence[0].pid="111111111",s=>s.evidence[0].creatorId="missing",s=>s.evidence[0].source={...source,windowStart:null},s=>s.creators[0].externalIdentity.id=7123456789012345678,s=>s.creators[0].externalIdentity.namespace="oec"]){const s=input();mutate(s);assert.throws(()=>buildItalySecondCases(s,"italy-pilot-fixture"));}
  assert.throws(()=>buildItalySecondCases(input(),"italy-profiles-fixture"));
});
test("repeated windows stay one observed edge and are never summed",()=>{
  const s=input();s.evidence.push({...s.evidence[0],id:"older",units:999,source:{...source,observedAt:9}});
  const c=buildItalySecondCases(s,"italy-pilot-fixture")[0];assert.equal(c.products.length,2);assert.equal(c.products.find(p=>p.productId==="p1").units,4);
});
test("draft labels cannot smuggle commercial claims or arbitrary products",()=>{
  const c=buildItalySecondCases(input(),"italy-pilot-fixture")[0];assert.throws(()=>draftSecondIntro(c.handle,[{...c.products[0],italianName:"campione gratuito"}]));assert.throws(()=>draftSecondIntro("unsafe handle",c.products));
});
test("changed source facts or readiness fingerprint invalidate the case source snapshot",()=>{
  const s=input(),a=buildItalySecondCases(s,"italy-pilot-fixture","a")[0],b=buildItalySecondCases(s,"italy-pilot-fixture","b")[0];assert.equal(a.id,b.id);assert.notEqual(a.sourceFingerprint,b.sourceFingerprint);
  s.evidence[0].units=5;assert.notEqual(buildItalySecondCases(s,"italy-pilot-fixture","a")[0].sourceFingerprint,a.sourceFingerprint);
});
test("all source opportunities exercise acceptance, lost receipt and restart-before-submit without real sends",async t=>{
  const directory=mkdtempSync(join(tmpdir(),"second-source-test-"));t.after(()=>rmSync(directory,{recursive:true,force:true}));
  const r=await evaluateSecondPilot(buildItalySecondCases(input(),"italy-pilot-fixture"),directory);assert.equal(r.status,"passed");assert.equal(r.scenarios.length,3);assert.equal(r.scenarios[1].afterVerify.simulatedAccepted,1);assert.equal(r.scenarios[2].afterVerify.resultUnknown,1);assert.equal(r.scenarios[2].simulatedReceipts,0);assert(r.scenarios.every(s=>s.attempts===1&&s.duplicateSubmissions===0));assert.equal(r.realSends,0);
});
test("pilot API only accepts bounded local simulator commands",()=>{
  const request={requestId:"r1",command:{type:"queue",snapshotId:"s1",scenario:"receipt_lost"}};assert.equal(parseSecondPilotCommand(request).command.scenario,"receipt_lost");
  for(const command of [{...request.command,mode:"live"},{...request.command,transport:"tiktok-im"},{...request.command,scenario:"send_real"},{type:"send",caseId:"c1"}])assert.throws(()=>parseSecondPilotCommand({...request,command}));
  assert.equal(parseSecondPilotQuery("http://local/?view=case&caseId=c1").caseId,"c1");
  for(const q of ["view=case","limit=100000","offset=-1","file=/tmp/db","view=case&caseId=../old","view=overview&view=cases"])assert.throws(()=>parseSecondPilotQuery("http://local/?"+q));
});
test("CLI rehearsal skips paused and unresolved work but can re-freeze cancelled source revisions",t=>{
  const directory=mkdtempSync(join(tmpdir(),"second-plan-test-")),store=new SecondPilotStore(join(directory,"pilot.sqlite"));t.after(()=>{store.close();rmSync(directory,{recursive:true,force:true});});
  const c=buildItalySecondCases(input(),"italy-pilot-fixture")[0];store.importCases([c]);
  const initial=rehearsalDecision(store.get(c.id));assert.equal(initial.kind,"queue");
  store.control(c.id,1,"paused","pause");assert.deepEqual(rehearsalDecision(store.get(c.id)),{kind:"skip",reason:"paused"});
  const resumed=store.control(c.id,2,"running","resume"),decision=rehearsalDecision(resumed);assert.equal(decision.kind,"queue");assert.notEqual(decision.freezeRequestId,initial.freezeRequestId);
  const frozen=store.freeze(c.id,resumed.revision,decision.freezeRequestId);store.queue(frozen.id,"accepted","queue");assert.equal(rehearsalDecision(store.get(c.id)).reason,"already_queued");
  store.importCases([{...c,sourceFingerprint:c.sourceFingerprint+"-new"}]);const next=rehearsalDecision(store.get(c.id));assert.equal(next.kind,"queue");assert.notEqual(next.freezeRequestId,decision.freezeRequestId);assert.equal(store.get(c.id).actions[0].status,"cancelled");
});
