import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { LocalRuntime, RuntimeError } from "../src/server/runtime/engine.ts";

const MX="rt-sofia-mx",BR="rt-pedro-br",IT="rt-luca-it";
function fixture(t,Runtime=LocalRuntime) {
  const directory=mkdtempSync(join(tmpdir(),"bdhub-runtime-test-"));
  const path=join(directory,"runtime.sqlite");
  const clock={at:1_900_000_000_000};
  const opened=[];
  const open=(Engine=Runtime)=>{const runtime=new Engine(path,{now:()=>clock.at,leaseMs:1000});opened.push(runtime);return runtime;};
  t.after(()=>{for(const r of opened)try{r.close();}catch{}rmSync(directory,{recursive:true,force:true});});
  return {path,clock,open,runtime:open()};
}
function relation(runtime,id=MX) {return runtime.snapshot().relationships.find(r=>r.id===id);}
let sequence=0;
function ingest(runtime,kind="request_card",id=MX,extra={}) {
  return runtime.command({type:"ingest",relationshipId:id,sourceMessageId:`source-${++sequence}`,kind,text:`${kind} source ${sequence}`,mode:"live",...extra},`request-${++sequence}`);
}
function control(runtime,mode,id=MX) {return runtime.command({type:"control",relationshipId:id,mode,expectedRevision:relation(runtime,id).revision},`control-${++sequence}`);}
async function drain(runtime,max=30) {for(let i=0;i<max;i++)if(!await runtime.tick("worker"))return;throw new Error("Worker did not become idle");}
function raw(path,fn) {const db=new DatabaseSync(path);try{return fn(db);}finally{db.close();}}
function counts(path) {return raw(path,db=>({attempts:db.prepare("SELECT COUNT(*) n FROM component_attempts").get().n,receipts:db.prepare("SELECT COUNT(*) n FROM platform_receipts").get().n,events:db.prepare("SELECT COUNT(*) n FROM events").get().n}));}

test("fresh local database seeds synthetic relations without messages or runnable work",async t=>{
  const {runtime,path}=fixture(t);const snapshot=runtime.snapshot();
  assert.equal(snapshot.relationships.length,3);assert.equal(snapshot.schemaVersion,1);
  assert.equal(snapshot.mode,"local-simulator");assert.equal(snapshot.worker.online,false);
  assert(snapshot.relationships.every(r=>r.actions.length===0&&r.messages.length===0&&r.jobs.length===0));
  assert.equal(await runtime.tick("worker"),false);assert.equal(runtime.snapshot().worker.online,true);
  assert.deepEqual(counts(path),{events:0,attempts:0,receipts:0});
});

test("commands and source events dedupe durably; changed payloads conflict without overwrites",t=>{
  const {runtime,path,open}=fixture(t);
  const command={type:"ingest",relationshipId:MX,sourceMessageId:"same-source",kind:"request_card",text:"source fact",mode:"live"};
  assert.equal(runtime.command(command,"same-request").duplicate,false);
  assert.equal(runtime.command(command,"same-request").duplicate,true);
  assert.equal(runtime.command(command,"second-request").duplicate,true);
  assert.throws(()=>runtime.command({...command,text:"changed"},"same-request"),e=>e instanceof RuntimeError&&e.status===409&&e.code==="request_conflict");
  assert.throws(()=>runtime.command({...command,text:"changed"},"third-request"),e=>e.code==="event_conflict");
  runtime.close();const reopened=open();assert.equal(reopened.command(command,"same-request").duplicate,true);
  assert.equal(relation(reopened).messages[0].text,"source fact");assert.equal(relation(reopened).jobs.length,1);assert.equal(counts(path).events,1);
});

test("restart preserves raw messages, control revision, and pending jobs",async t=>{
  const {runtime,open}=fixture(t);ingest(runtime);await runtime.tick("w1");
  assert.equal(relation(runtime).actions.length,1);control(runtime,"human");
  const saved=relation(runtime);runtime.close();const reopened=open();
  const current=relation(reopened);assert.equal(current.control,"human");assert.equal(current.revision,saved.revision);
  assert.equal(current.messages[0].id,saved.messages[0].id);assert.equal(current.actions[0].status,"cancelled");
  await drain(reopened);assert(current.actions[0].components.every(c=>c.attempts===0));
});

test("request freezes exact product components and delivers each in a separate durable step",async t=>{
  const {runtime,path,open}=fixture(t);ingest(runtime);
  await runtime.tick("w1");let rel=relation(runtime);assert.equal(rel.actions[0].status,"prepared");
  assert.equal(rel.context.offerVersion,1);assert.match(rel.context.skillVersion,/1\.0\.0\+/);
  const card=JSON.parse(rel.actions[0].components[1].content);assert.equal(card.pid,rel.product.pid);assert.equal(card.priceMinor,rel.product.priceMinor);
  await runtime.tick("w1");rel=relation(runtime);assert.equal(rel.actions[0].components[0].status,"accepted");assert.equal(rel.actions[0].components[1].status,"prepared");
  runtime.close();const reopened=open();await reopened.tick("w2");rel=relation(reopened);
  assert.equal(rel.status,"waiting_creator");assert.equal(rel.actions[0].status,"accepted");assert.deepEqual(counts(path),{events:1,attempts:2,receipts:2});
  control(reopened,"human");control(reopened,"auto");await drain(reopened);assert.equal(counts(path).attempts,2);
});

test("same product new explicit service request gets a new action; duplicates do not",async t=>{
  const {runtime,path}=fixture(t);ingest(runtime);await drain(runtime);ingest(runtime);await drain(runtime);
  assert.equal(relation(runtime).actions.length,2);assert.equal(counts(path).attempts,4);
});

test("human takeover invalidates old prepared work; handback plans from current revisions",async t=>{
  const {runtime,path}=fixture(t);ingest(runtime);await runtime.tick("w");
  control(runtime,"human");assert(relation(runtime).actions[0].components.every(c=>c.status==="cancelled"));await drain(runtime);assert.equal(counts(path).attempts,0);
  control(runtime,"auto");await drain(runtime);const rel=relation(runtime);
  assert.equal(rel.actions.length,2);assert.equal(rel.actions[1].controlRevision,rel.revision);assert.equal(counts(path).attempts,2);
  assert.throws(()=>runtime.command({type:"control",relationshipId:MX,expectedRevision:1,mode:"paused"},"stale"),e=>e.code==="revision_conflict");
});

test("new live input invalidates a stale draft; history input does not alter inbox or send",async t=>{
  const {runtime,path}=fixture(t);ingest(runtime,"sample_question",MX,{mode:"history",text:"old unverified sample claim"});
  assert.equal(relation(runtime).inboxRevision,0);await drain(runtime);assert.equal(counts(path).attempts,0);assert.equal(relation(runtime).status,"idle");
  ingest(runtime);await runtime.tick("w");const inbox=relation(runtime).inboxRevision;
  ingest(runtime,"request_card",MX,{mode:"history"});assert.equal(relation(runtime).inboxRevision,inbox);assert.equal(relation(runtime).actions[0].status,"prepared");
  ingest(runtime,"sample_question");await drain(runtime);assert.equal(counts(path).attempts,0);assert.equal(relation(runtime).actions[0].status,"cancelled");
});

test("sample question retains a creator statement without fabricating platform sample status",async t=>{
  const {runtime,path}=fixture(t);ingest(runtime,"sample_question");await drain(runtime);const rel=relation(runtime);
  assert.equal(rel.status,"needs_facts");assert.match(rel.nextStep,/尚无平台申请记录/);
  assert.equal(rel.actions.length,0);assert(!rel.evidence.some(e=>e.kind==="platform_receipt"));assert.equal(counts(path).attempts,0);
});

test("marketing opt-out survives handback; explicit new service request can be answered",async t=>{
  const {runtime,path}=fixture(t);ingest(runtime,"opt_out");control(runtime,"human");control(runtime,"auto");await drain(runtime);
  assert.equal(relation(runtime).marketingStopped,true);assert.equal(counts(path).attempts,0);
  ingest(runtime);await drain(runtime);assert.equal(relation(runtime).marketingStopped,true);assert.equal(relation(runtime).status,"waiting_creator");assert.equal(counts(path).attempts,2);
});

test("expired offer blocks planning and commit; refresh invalidates old version and resumes",async t=>{
  const {runtime,path,clock}=fixture(t);ingest(runtime);await runtime.tick("w");clock.at+=86_400_001;
  await drain(runtime);assert.equal(counts(path).attempts,0);assert.equal(relation(runtime).status,"needs_facts");
  runtime.command({type:"refresh_offer",relationshipId:MX,expectedRevision:relation(runtime).revision},"refresh");await drain(runtime);
  const rel=relation(runtime);assert.equal(rel.product.offerVersion,2);assert.equal(rel.actions.at(-1).offerVersion,2);assert.equal(counts(path).attempts,2);
});

test("explicit offer expiration is revision checked and survives a restart",async t=>{
  const {runtime,open}=fixture(t);ingest(runtime);await runtime.tick("w");
  runtime.command({type:"expire_offer",relationshipId:MX,expectedRevision:relation(runtime).revision},"expire");runtime.close();const reopened=open();await drain(reopened);
  const rel=relation(reopened);assert.equal(rel.actions[0].status,"cancelled");assert(rel.product.validUntil<reopened.snapshot().at);assert.equal(rel.status,"needs_facts");
});

test("text accepted plus card unknown is verified using its original receipt without another attempt",async t=>{
  const {runtime,path,open}=fixture(t);ingest(runtime,"request_card",BR);await drain(runtime);
  let rel=relation(runtime,BR);let action=rel.actions[0];assert.equal(action.components[0].status,"accepted");assert.equal(action.components[1].status,"unknown");assert.equal(rel.status,"waiting_verification");
  assert.deepEqual(counts(path),{events:1,attempts:2,receipts:2});runtime.close();const reopened=open();
  reopened.command({type:"verify",relationshipId:BR,actionId:action.id,componentId:action.components[1].id},"verify");await drain(reopened);
  rel=relation(reopened,BR);action=rel.actions[0];assert.equal(action.status,"accepted");assert.equal(rel.status,"waiting_creator");assert.equal(action.components[1].attempts,1);assert.equal(counts(path).attempts,2);
  assert(rel.jobs.every(job=>job.status==="done"||job.status==="cancelled"),"verified submission must leave no phantom pending jobs");
});

test("adoption needs the latest corresponding complete scheme and never follows unknown blindly",async t=>{
  const {runtime}=fixture(t);ingest(runtime,"adopted");assert.equal(relation(runtime).status,"needs_facts");
  ingest(runtime);await drain(runtime);ingest(runtime,"adopted");assert.equal(relation(runtime).status,"adopted");
  ingest(runtime);await runtime.tick("w");ingest(runtime,"adopted");assert.equal(relation(runtime).status,"needs_facts");
  ingest(runtime,"request_card",BR);await drain(runtime);ingest(runtime,"adopted",BR);assert.equal(relation(runtime,BR).status,"needs_facts");
});

test("deferred wake persists across restart and only rechecks facts",async t=>{
  const {runtime,path,clock,open}=fixture(t);ingest(runtime,"ask_later");assert.equal(relation(runtime).status,"waiting_until");
  assert.equal(await runtime.tick("w"),false);clock.at+=20_001;runtime.close();const reopened=open();
  assert.equal(await reopened.tick("w2"),true);const rel=relation(reopened);assert.equal(rel.status,"needs_facts");assert.equal(rel.jobs[0].status,"done");assert.equal(rel.actions.length,0);assert.equal(counts(path).attempts,0);
});

test("context is bounded to this relationship and six recent messages with evidence references",async t=>{
  const {runtime,path}=fixture(t);ingest(runtime,"sample_question",IT,{mode:"history",text:"CROSS_RELATION_SECRET"});
  for(let i=0;i<8;i++)ingest(runtime,"sample_question",MX,{mode:"history",text:`history-${i} ${"x".repeat(3500)}`});
  ingest(runtime);await runtime.tick("w");const rel=relation(runtime);const ctx=rel.context;
  assert.equal(ctx.relationshipId,MX);assert.equal(ctx.messageIds.length,6);assert.equal(ctx.omittedMessages,3);assert(ctx.characterCount<=6000);
  assert(ctx.messageIds.every(ref=>rel.messages.some(m=>m.id===ref)));assert(ctx.evidenceRefs.every(ref=>rel.evidence.some(e=>e.id===ref)));
  const payload=raw(path,db=>db.prepare("SELECT payload FROM contexts WHERE id=?").get(ctx.id).payload);
  assert(!payload.includes("CROSS_RELATION_SECRET"));assert.equal(payload.length,ctx.characterCount);assert.match(payload,/sourceEventId/);assert.match(payload,/policyRevision/);
});

test("later history imports cannot evict the current live source from bounded planning context",async t=>{
  const {runtime,path}=fixture(t);ingest(runtime,"request_card",MX,{text:"CURRENT_LIVE_SOURCE"});
  const sourceId=relation(runtime).messages[0].id;
  for(let i=0;i<9;i++)ingest(runtime,"sample_question",MX,{mode:"history",text:`later-import-${i} ${"z".repeat(3500)}`});
  await runtime.tick("w");const rel=relation(runtime);const action=rel.actions[0];
  assert(rel.context.messageIds.includes(sourceId));assert.equal(rel.context.messageIds.length,6);
  assert.equal(action.contextId,rel.context.id);assert.equal(action.policyRevision,rel.context.policyRevision);
  const payload=raw(path,db=>db.prepare("SELECT payload FROM contexts WHERE id=?").get(action.contextId).payload);
  assert.match(payload,/CURRENT_LIVE_SOURCE/);assert(payload.length<=6000);await drain(runtime);assert.equal(relation(runtime).status,"waiting_creator");
});

test("adoption remains an immutable action fact when new messages or control changes follow",async t=>{
  const {runtime,open}=fixture(t);ingest(runtime);await drain(runtime);ingest(runtime,"adopted");
  const original=relation(runtime).actions[0];assert(original.adoptedAt);assert(original.adoptionEventId);
  control(runtime,"human");control(runtime,"auto");assert.equal(relation(runtime).status,"adopted");
  ingest(runtime,"adopted");assert.equal(relation(runtime).actions[0].adoptionEventId,original.adoptionEventId);
  ingest(runtime);await runtime.tick("w");ingest(runtime,"adopted");let rel=relation(runtime);
  assert.equal(rel.status,"needs_facts");assert.equal(rel.actions[0].adoptionEventId,original.adoptionEventId);assert.equal(rel.actions[1].adoptedAt,undefined);
  runtime.close();const reopened=open();rel=relation(reopened);assert.equal(rel.actions[0].adoptionEventId,original.adoptionEventId);
});

test("two connections claiming work cannot create duplicate actions or component attempts",async t=>{
  const {runtime,path,open}=fixture(t);const other=open();ingest(runtime);
  await Promise.all([runtime.tick("worker-a"),other.tick("worker-b")]);
  await Promise.all([runtime.tick("worker-a"),other.tick("worker-b")]);
  await drain(runtime);assert.equal(relation(runtime).actions.length,1);assert.equal(counts(path).attempts,2);
});

test("expired fence rejects late planner commit after another worker has recovered the lease",async t=>{
  let resume;let entered;const paused=new Promise(resolve=>entered=resolve);
  class DelayedPlanner extends LocalRuntime {async beforeJobCommit(){entered();await new Promise(resolve=>resume=resolve);}}
  const {runtime,clock,open}=fixture(t,DelayedPlanner);const other=open(LocalRuntime);ingest(runtime);
  const oldTick=runtime.tick("old-worker");await paused;clock.at+=1001;
  await other.tick("new-worker");assert.equal(relation(other).actions.length,1);resume();await oldTick;
  assert.equal(relation(other).actions.length,1);assert.equal(relation(other).jobs[0].fence,2);
});

test("crash after platform accepts recovers submitting as unknown and does not replay writes",async t=>{
  let injected=false;
  class CrashAfterAccepted extends LocalRuntime {async afterPlatformSubmit(){if(!injected){injected=true;throw new Error("simulated process loss");}}}
  const {runtime,path,clock,open}=fixture(t,CrashAfterAccepted);ingest(runtime);await runtime.tick("old");
  await assert.rejects(runtime.tick("old"),/simulated process loss/);assert.equal(relation(runtime).actions[0].components[0].status,"submitting");
  assert.deepEqual(counts(path),{events:1,attempts:1,receipts:1});runtime.close();clock.at+=1001;const reopened=open(LocalRuntime);
  await reopened.tick("new");let rel=relation(reopened);const action=rel.actions[0];assert.equal(action.components[0].status,"unknown");assert.equal(counts(path).attempts,1);
  reopened.command({type:"verify",relationshipId:MX,actionId:action.id,componentId:action.components[0].id},"verify-crash");await reopened.tick("new");assert.equal(counts(path).attempts,1);
  await drain(reopened);rel=relation(reopened);assert.equal(rel.status,"waiting_creator");assert.equal(counts(path).attempts,2);
});

test("late executing worker cannot commit after lease expires and is fenced by recovery",async t=>{
  let resume;let entered;const paused=new Promise(resolve=>entered=resolve);
  class DelayedReceipt extends LocalRuntime {async afterPlatformSubmit(){entered();await new Promise(resolve=>resume=resolve);}}
  const {runtime,path,clock,open}=fixture(t,DelayedReceipt);const other=open(LocalRuntime);ingest(runtime);await runtime.tick("old");
  const oldTick=runtime.tick("old");await paused;clock.at+=1001;await other.tick("new");resume();await oldTick;
  const rel=relation(other);assert.equal(rel.actions[0].components[0].status,"unknown");assert.equal(rel.actions[0].components[0].receiptRef,null);assert.equal(counts(path).attempts,1);assert.equal(counts(path).receipts,1);
});

test("takeover during external call preserves submitted evidence and cancels future components",async t=>{
  let resume;let entered;const paused=new Promise(resolve=>entered=resolve);
  class DelayedReceipt extends LocalRuntime {async afterPlatformSubmit(){entered();await new Promise(resolve=>resume=resolve);}}
  const {runtime,path,open}=fixture(t,DelayedReceipt);const other=open(LocalRuntime);ingest(runtime);await runtime.tick("w");
  const pending=runtime.tick("w");await paused;control(other,"human");resume();await pending;
  const rel=relation(other);assert.equal(rel.control,"human");assert.equal(rel.actions[0].components[0].status,"accepted");assert.equal(rel.actions[0].components[1].status,"cancelled");assert.equal(counts(path).attempts,1);
});

test("lost process before actual platform call remains unknown after receipt-free verification",async t=>{
  class CrashBeforeCall extends LocalRuntime {async beforePlatformSubmit(){throw new Error("lost before call");}}
  const {runtime,path,clock,open}=fixture(t,CrashBeforeCall);ingest(runtime);await runtime.tick("old");
  await assert.rejects(runtime.tick("old"),/lost before call/);runtime.close();clock.at+=1001;
  const reopened=open(LocalRuntime);await reopened.tick("new");const action=relation(reopened).actions[0];
  reopened.command({type:"verify",relationshipId:MX,actionId:action.id,componentId:action.components[0].id},"missing-receipt");await drain(reopened);
  assert.equal(relation(reopened).actions[0].components[0].status,"unknown");assert.deepEqual(counts(path),{events:1,attempts:1,receipts:0});
});

test("new service request waits behind unknown original operation, then plans from its own context",async t=>{
  const {runtime,path}=fixture(t);ingest(runtime,"request_card",BR);await drain(runtime);
  const original=relation(runtime,BR).actions[0];ingest(runtime,"request_card",BR);await drain(runtime);
  assert.equal(relation(runtime,BR).actions.length,1);assert.equal(counts(path).attempts,2);
  runtime.command({type:"verify",relationshipId:BR,actionId:original.id,componentId:original.components[1].id},"verify-older");await drain(runtime);
  const rel=relation(runtime,BR);assert.equal(rel.actions.length,2);assert.notEqual(rel.actions[0].contextId,rel.actions[1].contextId);
  assert.equal(rel.actions[1].inboxRevision,rel.inboxRevision);assert.equal(rel.status,"waiting_creator");assert.equal(counts(path).attempts,4);
  assert(rel.jobs.every(job=>job.status==="done"||job.status==="cancelled"),"replanned work must close or reuse blocked jobs");
});
