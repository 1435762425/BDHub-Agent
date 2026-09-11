import test from "node:test";
import assert from "node:assert/strict";
import {mkdtempSync,rmSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {SecondPilotStore} from "../src/server/second-pilot/store.ts";

function sample(id="it-case-1",extra={}) {
  const pid="1729779362302171335",source={ref:"research:exact-pid-1",observedAt:1_750_000_000_000,windowStart:1_740_000_000_000,windowEnd:1_741_000_000_000};
  return {id,sourceFingerprint:"frozen-source-1",creatorId:`creator-${id}`,creatorRef:{namespace:"kalodata",id:`kd-${id}`},handle:`creator.${id}`,market:"it",products:[{productId:"product-1",pid,title:"Product name",italianName:"Prodotto",units:3,source}],draft:{text:"Ciao! Ti contatto per valutare una possibile collaborazione su questo prodotto.",language:"it",method:"deterministic",skillVersion:"1.0.0",claimRefs:[source.ref]},liveBlockers:["OEC 未验证","当前商业条件未知"],...extra};
}
function fixture(t,Engine=SecondPilotStore){
  const dir=mkdtempSync(join(tmpdir(),"bdhub-second-pilot-")),path=join(dir,"pilot.sqlite"),clock={at:1_900_000_000_000},opened=[];
  const open=(Ctor=Engine)=>{const store=new Ctor(path,{now:()=>clock.at,leaseMs:1000});opened.push(store);return store;};
  t.after(()=>{for(const s of opened)try{s.close();}catch{}rmSync(dir,{recursive:true,force:true});});return {path,clock,open,store:open()};
}
let sequence=0;const request=()=>`request-${++sequence}`;
function queue(store,input=sample(),scenario="accepted"){
  store.importCases([input]);const snapshot=store.freeze(input.id,store.get(input.id).revision,request());return store.queue(snapshot.id,scenario,request());
}

test("empty isolated store has no seeds, no implicit work and no real execution",async t=>{
  const {store}=fixture(t);assert.deepEqual(store.list(),{items:[],total:0,offset:0,limit:50});assert.equal(await store.tick("worker"),false);
  const stats=store.overview();assert.equal(stats.cases,0);assert.equal(stats.realSends,0);assert.equal(stats.modelCalls,0);assert.equal(stats.worker.online,true);
});

test("real sources import without fabrication, duplicate imports remain idle, and multi-product cases stay merged",async t=>{
  const {store}=fixture(t),one=sample(),two={...one.products[0],productId:"product-2",pid:"1729480019490150432",units:7};
  one.products.push(two);assert.deepEqual(store.importCases([one]),{inserted:1,updated:0,unchanged:0});assert.deepEqual(store.importCases([one]),{inserted:0,updated:0,unchanged:1});
  const current=store.get(one.id);assert.equal(current.products.length,2);assert.equal(current.realIdentityStatus,"unverified");assert.equal(current.realControl,"unknown");assert.equal(current.realMarketingStopped,null);assert.equal(current.actions.length,0);
  assert.equal(store.overview().edges,2);assert.equal(store.overview().cases,1);assert.equal(await store.tick("worker"),false);
});

test("invalid sources and identity changes reject atomically",t=>{
  const {store}=fixture(t);assert.throws(()=>store.importCases([sample(),sample("it-case-2",{market:"br"})]),e=>e.code==="invalid_input");assert.equal(store.overview().cases,0);
  assert.throws(()=>store.importCases([sample("bad",{products:[{...sample().products[0],units:0}]})]),e=>e.code==="invalid_input");
  assert.throws(()=>store.importCases([{...sample(),mode:"live"}]),e=>e.code==="invalid_input");
  store.importCases([sample()]);assert.throws(()=>store.importCases([sample(undefined,{creatorRef:{namespace:"kalodata",id:"another"}})]),e=>e.code==="identity_conflict");assert.equal(store.get("it-case-1").creatorRef.id,"kd-it-case-1");
});

test("freezing is immutable and idempotent; conflicting request bodies cannot overwrite it",t=>{
  const {store,open}=fixture(t);store.importCases([sample()]);const first=store.freeze("it-case-1",1,"freeze-one"),second=store.freeze("it-case-1",1,"freeze-two");assert.equal(first.id,second.id);assert.equal(first.liveExecutable,false);assert.equal(first.transport,"local-simulator");assert.equal(first.input.draft.text,sample().draft.text);
  first.input.draft.text="client mutation";assert.equal(store.get("it-case-1").snapshots[0].input.draft.text,sample().draft.text);
  assert.throws(()=>store.freeze("it-case-1",2,"freeze-one"),e=>e.code==="request_conflict");
  store.close();const reopened=open();assert.equal(reopened.freeze("it-case-1",1,"freeze-one").id,second.id);assert.equal(reopened.overview().frozen,1);
});

test("queued one-text action survives restart and is accepted once with a simulated receipt",async t=>{
  const {store,open}=fixture(t),action=queue(store);assert.equal(action.componentKind,"text");assert.equal(action.attempts,0);store.close();const reopened=open();await reopened.tick("new-worker");
  const result=reopened.get("it-case-1").actions[0];assert.equal(result.status,"simulated_accepted");assert.equal(result.attempts,1);assert.match(result.receiptRef,/second-sim-receipt-/);assert.equal(await reopened.tick("new-worker"),false);
  assert.equal(reopened.overview().simulatedReceipts,1);assert.equal(reopened.overview().realSends,0);
  assert.equal(reopened.queue(action.snapshotId,"accepted",request()).id,action.id);assert.equal(reopened.get("it-case-1").actions.length,1);
});

test("different queue request ids dedupe to frozen intent and scenarios cannot change",async t=>{
  const {store}=fixture(t);store.importCases([sample()]);const snapshot=store.freeze("it-case-1",1,request()),first=store.queue(snapshot.id,"accepted","queue-one");
  assert.equal(store.queue(snapshot.id,"accepted","queue-two").id,first.id);assert.throws(()=>store.queue(snapshot.id,"receipt_lost","queue-one"),e=>e.code==="request_conflict");assert.throws(()=>store.queue(snapshot.id,"receipt_lost","queue-three"),e=>e.code==="scenario_conflict");assert.throws(()=>store.queue(snapshot.id,"live",request()),e=>e.code==="invalid_scenario");
  await store.tick("worker");assert.equal(store.queue(snapshot.id,"accepted","queue-one").status,"simulated_accepted");assert.equal(store.overview().attempts,1);
});

test("source or draft updates cancel queued work while preserving immutable snapshot",async t=>{
  const {store}=fixture(t),first=sample(),pending=queue(store,first);const changed=sample(undefined,{sourceFingerprint:"source-2",draft:{...first.draft,text:"Nuova proposta."}});store.importCases([changed]);
  let current=store.get(first.id);assert.equal(current.revision,2);assert.equal(current.actions[0].status,"cancelled");assert.equal(current.snapshots[0].input.draft.text,first.draft.text);assert.equal(await store.tick("worker"),false);
  assert.equal(store.queue(pending.snapshotId,"accepted",request()).status,"cancelled");
  assert.throws(()=>store.freeze(first.id,1,request()),e=>e.code==="revision_conflict");const next=store.freeze(first.id,2,request());store.queue(next.id,"accepted",request());await store.tick("worker");current=store.get(first.id);assert.equal(current.actions[1].status,"simulated_accepted");assert.equal(store.overview().attempts,1);
});

test("control changes invalidate unqueued snapshots and cancel queued work without changing real controls",async t=>{
  const {store}=fixture(t);store.importCases([sample()]);const snapshot=store.freeze("it-case-1",1,request());const paused=store.control("it-case-1",1,"paused",request());assert.equal(paused.realControl,"unknown");assert.equal(paused.realMarketingStopped,null);
  assert.throws(()=>store.freeze("it-case-1",2,request()),e=>e.code==="local_paused");store.control("it-case-1",2,"running",request());assert.throws(()=>store.queue(snapshot.id,"accepted",request()),e=>e.code==="stale_snapshot");
  const next=store.freeze("it-case-1",3,request());store.queue(next.id,"accepted",request());store.control("it-case-1",3,"paused",request());assert.equal(await store.tick("worker"),false);assert.equal(store.get("it-case-1").actions[0].status,"cancelled");assert.equal(store.overview().attempts,0);
});

test("response-loss verification reads original receipt without adding attempts; unknown blocks later source versions",async t=>{
  const {store,open}=fixture(t),first=queue(store,sample(),"receipt_lost");await store.tick("worker");assert.equal(store.get("it-case-1").actions[0].status,"result_unknown");
  store.importCases([sample(undefined,{sourceFingerprint:"changed"})]);assert.throws(()=>store.freeze("it-case-1",2,request()),e=>e.code==="unresolved_attempt");assert.equal(await store.tick("worker"),false);store.close();const reopened=open();const verified=reopened.verify(first.id,"verify-one");assert.equal(verified.status,"simulated_accepted");assert.equal(verified.attempts,1);assert.equal(reopened.overview().attempts,1);assert.equal(reopened.verify(first.id,"verify-one").receiptRef,verified.receiptRef);
  const review=reopened.freeze("it-case-1",2,request());assert.throws(()=>reopened.queue(review.id,"accepted",request()),e=>e.code==="case_completed");
});

test("crash before simulator submission recovers as unknown and absence of receipt never authorizes retry",async t=>{
  const {store,clock,open}=fixture(t),first=queue(store,sample(),"before_submit_crash");await store.tick("crashed-worker");assert.equal(store.get("it-case-1").actions[0].status,"submitting");assert.equal(store.overview().simulatedReceipts,0);store.close();clock.at+=1001;const reopened=open();await reopened.tick("recovery-worker");
  assert.equal(reopened.get("it-case-1").actions[0].status,"result_unknown");for(let i=0;i<3;i++){assert.equal(reopened.verify(first.id,request()).status,"result_unknown");assert.equal(await reopened.tick("recovery-worker"),false);}
  assert.equal(reopened.overview().attempts,1);assert.equal(reopened.overview().simulatedReceipts,0);assert.throws(()=>reopened.freeze("it-case-1",1,request()),e=>e.code==="unresolved_attempt");
});

test("two worker connections cannot both submit the same case",async t=>{
  const {store,open}=fixture(t),other=open();queue(store);const results=await Promise.all([store.tick("first-worker"),other.tick("second-worker")]);assert.equal(results.filter(Boolean).length,1);assert.equal(store.overview().attempts,1);assert.equal(store.overview().simulatedReceipts,1);
});

test("expired original worker cannot commit a late simulator call after recovery fences it",async t=>{
  let resume;const wait=new Promise(resolve=>{resume=resolve;});
  class Delayed extends SecondPilotStore {async beforeSubmit(){await wait;}}
  const {store,open,clock}=fixture(t,Delayed),other=open(SecondPilotStore);queue(store);const old=store.tick("old-worker");await Promise.resolve();clock.at+=1001;await other.tick("replacement-worker");resume();await old;
  assert.equal(store.get("it-case-1").actions[0].status,"result_unknown");assert.equal(store.overview().attempts,1);assert.equal(store.overview().simulatedReceipts,0);
});

test("pause between persisted intent and simulator invocation prevents the local receipt",async t=>{
  let other;class Pausing extends SecondPilotStore {async beforeSubmit(){other.control("it-case-1",1,"paused",request());}}
  const {store,open}=fixture(t,Pausing);other=open(SecondPilotStore);queue(store);await store.tick("worker");assert.equal(store.get("it-case-1").actions[0].status,"cancelled");assert.equal(store.overview().simulatedReceipts,0);assert.equal(store.get("it-case-1").realControl,"unknown");
});

test("crash after simulator acceptance retains receipt and is resolved without another submit",async t=>{
  class Crashing extends SecondPilotStore {async afterSubmit(){throw new Error("process interrupted");}}
  const {store,open,clock}=fixture(t,Crashing),first=queue(store);await assert.rejects(store.tick("old-worker"),/process interrupted/);assert.equal(store.overview().simulatedReceipts,1);store.close();clock.at+=1001;
  const reopened=open(SecondPilotStore);await reopened.tick("new-worker");assert.equal(reopened.get("it-case-1").actions[0].status,"result_unknown");assert.equal(reopened.verify(first.id,request()).status,"simulated_accepted");assert.equal(reopened.overview().attempts,1);assert.equal(reopened.overview().simulatedReceipts,1);
});

test("pause after simulator receipt preserves acceptance rather than erasing external evidence",async t=>{
  let other;class Pausing extends SecondPilotStore {async afterSubmit(){other.control("it-case-1",1,"paused",request());}}
  const {store,open}=fixture(t,Pausing);other=open(SecondPilotStore);queue(store);await store.tick("worker");const current=store.get("it-case-1");assert.equal(current.localControl,"paused");assert.equal(current.actions[0].status,"simulated_accepted");assert.equal(store.overview().attempts,1);
});

test("source update after acceptance never mutates original receipt or requeues completion",async t=>{
  const {store}=fixture(t);queue(store);await store.tick("worker");const receipt=store.get("it-case-1").actions[0].receiptRef;store.importCases([sample(undefined,{sourceFingerprint:"later-evidence"})]);assert.equal(store.get("it-case-1").actions[0].receiptRef,receipt);assert.equal(await store.tick("worker"),false);assert.equal(store.overview().attempts,1);
});

test("pagination and command limits reject malformed calls",t=>{
  const {store}=fixture(t);store.importCases([sample(),sample("it-case-2")]);assert.equal(store.list({offset:1,limit:1}).items[0].id,"it-case-2");assert.throws(()=>store.list({limit:0}),e=>e.code==="invalid_input");assert.throws(()=>store.list({offset:-1}),e=>e.code==="invalid_input");assert.throws(()=>store.control("it-case-1",1,"live",request()),e=>e.code==="invalid_input");assert.throws(()=>store.verify("missing",request()),e=>e.code==="action_missing");
});

test("another case id cannot bypass creator merge or unknown-result exclusion",async t=>{
  const {store}=fixture(t),original=sample();queue(store,original,"receipt_lost");await store.tick("worker");const duplicate={...original,id:"another-case"};
  assert.throws(()=>store.importCases([duplicate]),e=>e.code==="identity_duplicate");assert.equal(store.overview().cases,1);assert.equal(store.overview().attempts,1);assert.equal(store.overview().resultUnknown,1);
});

test("duplicate creators in one import are rejected instead of creating two separate contacts",t=>{
  const {store}=fixture(t),original=sample();assert.throws(()=>store.importCases([original,{...original,id:"another-case"}]),e=>e.code==="identity_duplicate");assert.equal(store.overview().cases,0);
});
