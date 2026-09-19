import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {callDiscoveryCommand,createDiscoveryHandlers,decodeDiscoveryOutput,parseDiscoveryPost,parseDiscoveryQuery} from "../src/server/creator-identities/discovery.ts";

const BATCH="discovery_"+"a".repeat(32),HASH="b".repeat(64),TIME="2026-09-12T03:00:00.000000Z";
const initial={command:"preview",market:"it",sourceLabel:"  意大利名单  ",text:"@alice\nhttps://www.tiktok.com/@bob"};
const batch=()=>({id:BATCH,market:"it",sourceLabel:"意大利名单",status:"queued",createdAt:TIME,startedAt:null,finishedAt:null,errorCode:null,counts:{total:2,queued:2,running:0,completed:0,unresolved:0,blocked:0,invalid:0,duplicate:0,created:0,existing:0,identityOnly:0},workerOnline:false});
const preview=()=>({market:"it",sourceLabel:"意大利名单",previewHash:HASH,counts:{total:3,valid:1,duplicate:1,invalid:1},items:[{index:1,raw:"@alice",handle:"alice",status:"valid",reason:null,duplicateOf:null},{index:2,raw:"alice",handle:"alice",status:"duplicate",reason:"duplicate_handle",duplicateOf:1},{index:3,raw:"https://wrong.invalid/alice",handle:null,status:"invalid",reason:"unsupported_url",duplicateOf:null}],canSubmit:true});
const headers={host:"127.0.0.1:5198",origin:"http://127.0.0.1:5198","content-type":"application/json"};
const request=(body,extra={})=>new Request("http://127.0.0.1:5198/api/creator-discovery",{method:"POST",headers:{...headers,...extra},body:typeof body==="string"?body:JSON.stringify(body)});

test("preview and submit preserve text while normalizing source and constraining command payload",()=>{
  assert.deepEqual(parseDiscoveryPost(initial),{command:"preview",input:{market:"it",sourceLabel:"意大利名单",text:initial.text}});
  assert.equal(parseDiscoveryPost({...initial,command:"submit",previewHash:HASH,requestId:"request-1"}).input.previewHash,HASH);
  assert.deepEqual(parseDiscoveryPost({command:"control",batchId:BATCH,action:"resume",requestId:"request-2"}),{command:"control",input:{batchId:BATCH,action:"resume",requestId:"request-2"}});
});
test("unknown account, OEC, executable and control arguments are rejected before invocation",async()=>{
  let called=0;const api=createDiscoveryHandlers(async()=>{called++;return preview();});
  for(const key of ["account","oecId","script","args","worker","proxy"]){const response=await api.POST(request({...initial,[key]:"DO_NOT_EXECUTE"}));assert.equal(response.status,400,key);}
  const control=await api.POST(request({command:"control",batchId:BATCH,action:"delete",requestId:"one"}));assert.equal(control.status,400);assert.equal(called,0);
});
test("text is bounded by UTF8 bytes and nonempty line count, not only JS characters",()=>{
  assert.throws(()=>parseDiscoveryPost({...initial,text:"汉".repeat(21846)}),error=>error.code==="input_limit_exceeded");
  assert.throws(()=>parseDiscoveryPost({...initial,text:Array(501).fill("alice").join("\n")}),error=>error.code==="input_limit_exceeded");
  assert.equal(parseDiscoveryPost({...initial,text:Array(500).fill("alice").join("\n")}).command,"preview");
  assert.throws(()=>parseDiscoveryPost({...initial,command:"submit",text:"\n ",previewHash:HASH,requestId:"one"}),error=>error.code==="empty_batch");
  assert.throws(()=>parseDiscoveryPost({...initial,sourceLabel:"source\naccount"}),error=>error.code==="invalid_request");
});
test("GET accepts list and exact batch detail only",()=>{
  const parse=q=>parseDiscoveryQuery(`http://127.0.0.1:5198/api/creator-discovery?${q}`);
  assert.deepEqual(parse("view=list"),{command:"list",input:{}});assert.deepEqual(parse(`view=detail&batchId=${BATCH}`),{command:"detail",input:{batchId:BATCH}});
  for(const q of ["view=list&account=acc6","view=detail&batchId=../script","view=list&view=detail","view=create","script=evil"])assert.throws(()=>parse(q),error=>error.code==="invalid_request");
});
test("local origin, JSON format and body cap are enforced before CLI side effects",async()=>{
  let called=0;const api=createDiscoveryHandlers(async()=>{called++;return preview();});
  assert.equal((await api.POST(request(initial,{host:"evil.invalid"}))).status,403);
  assert.equal((await api.POST(request(initial,{origin:"http://evil.invalid"}))).status,403);
  assert.equal((await api.POST(request(initial,{"sec-fetch-site":"cross-site"}))).status,403);
  assert.equal((await api.POST(request(initial,{"content-type":"text/plain"}))).status,415);
  assert.equal((await api.POST(request("x".repeat(128*1024+1)))).status,413);
  assert.equal((await api.POST(request("{broken json"))).status,400);
  assert.equal(called,0);
});
test("API forwards normalized input and preserves same request ID for recovery",async()=>{
  const calls=[],api=createDiscoveryHandlers(async(command,input)=>{calls.push({command,input});return batch();});
  const body={...initial,command:"submit",previewHash:HASH,requestId:"stable-recovery-request"};
  for(let i=0;i<2;i++)assert.equal((await api.POST(request(body))).status,200);
  assert.deepEqual(calls[0],calls[1]);assert.equal(calls[0].input.requestId,body.requestId);assert.equal(calls[0].input.sourceLabel,"意大利名单");
});
test("CLI errors use static status and messages instead of source message, status or stack",()=>{
  for(const code of ["preview_mismatch","idempotency_conflict","batch_not_resumable"]){assert.throws(()=>decodeDiscoveryOutput("submit",JSON.stringify({error:{code,message:"SECRET_COOKIE",status:200,stack:"/private/path"}})),error=>error.code===code&&error.status===409&&!error.message.includes("SECRET"));}
  for(const output of ["SECRET_STDERR",JSON.stringify({error:{code:"SECRET",message:"SECRET"}}),"[]","null"]){assert.throws(()=>decodeDiscoveryOutput("list",output),error=>error.code==="discovery_unavailable"&&!error.message.includes("SECRET"));}
});
test("preview projection retains row reasons and removes unrelated payload fields",()=>{
  const value=preview();value.cookie="SECRET_COOKIE";value.items[0].script="SECRET_SCRIPT";
  const result=decodeDiscoveryOutput("preview",JSON.stringify(value));assert.equal(result.items[1].duplicateOf,1);assert.equal(result.items[2].reason,"unsupported_url");assert(!JSON.stringify(result).includes("SECRET"));
  value.items[0].handle=null;assert.throws(()=>decodeDiscoveryOutput("preview",JSON.stringify(value)),error=>error.code==="discovery_unavailable");
});
test("missing or incompatible data never creates demo batches and unknown failure detail is masked",async()=>{
  assert.deepEqual(decodeDiscoveryOutput("list",'{"batches":[],"workerOnline":false}'),{batches:[],workerOnline:false});
  assert.throws(()=>decodeDiscoveryOutput("list",'{"error":{"code":"schema_mismatch","message":"PRIVATE_TABLE"}}'),error=>error.status===503&&!error.message.includes("PRIVATE"));
  assert.throws(()=>decodeDiscoveryOutput("list",'{"batches":[{"demo":true}],"workerOnline":false}'),error=>error.code==="discovery_unavailable");
  const api=createDiscoveryHandlers(async()=>{throw new Error("PRIVATE_COOKIE_FROM_CHILD");}),response=await api.POST(request(initial));assert.equal(response.status,503);assert(!JSON.stringify(await response.json()).includes("PRIVATE"));
});
test("child execution is a fixed local script with stdin and no shell or user text in argv",async()=>{
  let captured;const run=(executable,args,options,callback)=>({stdin:{on(){return this;},end(data){captured={executable,args,options,data};queueMicrotask(()=>callback(null,JSON.stringify(preview())));}}});
  const input={market:"it",sourceLabel:"意大利名单",text:"@alice\n$(touch /tmp/never)"};
  await callDiscoveryCommand("preview",input,{root:"/project/BDHub-Agent",run});
  assert.equal(captured.executable,"/project/BDHub-Agent/.venv/bin/python");assert.deepEqual(captured.args,["/project/BDHub-Agent/scripts/creator-discovery.py","preview"]);assert.equal(captured.options.shell,false);assert.deepEqual(JSON.parse(captured.data),input);assert(!captured.args.join(" ").includes("touch"));
});
test("nonzero child success-looking output is not acknowledged and known error is still recoverable",async()=>{
  const runner=output=>(executable,args,options,callback)=>({stdin:{on(){return this;},end(){queueMicrotask(()=>callback(new Error("PRIVATE_EXEC_ERROR"),output));}}});
  await assert.rejects(callDiscoveryCommand("list",{},{root:"/project/BDHub-Agent",run:runner('{"batches":[],"workerOnline":false}')}),error=>error.code==="discovery_unavailable");
  await assert.rejects(callDiscoveryCommand("submit",{market:"it",sourceLabel:"source",text:"alice",previewHash:HASH,requestId:"same"},{root:"/project/BDHub-Agent",run:runner('{"error":{"code":"idempotency_conflict","message":"PRIVATE"}}')}),error=>error.code==="idempotency_conflict"&&error.status===409);
});
test("detail preserves identity-only outcome without calling it a complete profile",()=>{
  const one=batch();one.counts={...one.counts,total:1,queued:0,blocked:1,identityOnly:1};one.status="blocked";
  const result=decodeDiscoveryOutput("detail",JSON.stringify({batch:one,items:[{id:"discovery_item_"+"c".repeat(32),index:1,handle:"alice",status:"blocked",reason:"PRIVATE_NETWORK_RESPONSE",duplicateOf:null,creatorId:"creator_"+"d".repeat(32),oecId:"123",startedAt:TIME,finishedAt:TIME,requestCount:2,outcome:"identity_only",cookie:"SECRET"}]}));
  assert.equal(result.items[0].outcome,"identity_only");assert.equal(result.items[0].reason,"unknown_error");assert.equal(result.batch.counts.identityOnly,1);assert(!JSON.stringify(result).includes("SECRET"));
});
test("all bounded Python worker error codes remain diagnosable without exposing remote messages",()=>{
  const source=readFileSync(new URL("../../../scripts/lib/profile_refresh.py",import.meta.url),"utf8");
  const block=source.match(/SAFE_ERRORS = frozenset\(\{([\s\S]*?)\}\)/)[1];
  const codes=[...block.matchAll(/"([a-z0-9_]+)"/g)].map(match=>match[1]);assert(codes.length>20);
  for(const code of codes){const value=batch();value.errorCode=code;assert.equal(decodeDiscoveryOutput("submit",JSON.stringify(value)).errorCode,code);}
  const value=preview();value.items[2].reason="looks_like_id";value.items[2].raw="1234567890123456789";
  assert.equal(decodeDiscoveryOutput("preview",JSON.stringify(value)).items[2].reason,"looks_like_id");
});
