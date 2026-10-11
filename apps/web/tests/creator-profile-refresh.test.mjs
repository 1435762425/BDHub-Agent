import test from "node:test";
import assert from "node:assert/strict";
import {existsSync} from "node:fs";
import {join} from "node:path";
import {callRefreshCommand,coreProfileMarket,decodeRefreshOutput,parseRefreshRequest,parseRefreshQuery,projectRoot} from "../src/server/creator-identities/refresh.ts";

const creatorId="creator_"+"a".repeat(32);
test("refresh accepts a canonical creator only, never a replacement handle, OEC or executable path",()=>{
  assert.deepEqual(parseRefreshRequest({market:"it",creatorId,requestId:"test-1"}),{market:"it",creatorId,requestId:"test-1"});
  for(const body of [{creatorId,requestId:"t"},{market:"zz",creatorId,requestId:"t"},{market:"it",creatorId,requestId:"t",handle:"someone"},{market:"it",creatorId,requestId:"t",oecId:"123"},{market:"it",creatorId,requestId:"t",script:"evil"},{market:"it",creatorId:123,requestId:"t"},{market:"it",creatorId,requestId:"a\ncommand"},null])assert.throws(()=>parseRefreshRequest(body));
});
test("status lookups reject multiple identities and unbounded queries",()=>{
  assert.deepEqual(parseRefreshQuery(`http://localhost/api?market=it&creatorId=${creatorId}`),{command:"list",input:{market:"it",creatorId}});
  assert.equal(parseRefreshQuery("http://localhost/api?market=it&jobId=refresh_123").command,"status");
  for(const query of ["jobId=../../secret","creatorId=123",`creatorId=${creatorId}&jobId=job`,"jobId=x&jobId=y","jobId=x&path=anything"])assert.throws(()=>parseRefreshQuery(`http://localhost/api?${query}`));
});
test("CLI errors expose only fixed messages, never raw process diagnostics",()=>{
  assert.throws(()=>decodeRefreshOutput('{"error":{"code":"idempotency_conflict","message":"secret token"}}'),e=>e.status===409&&!e.message.includes("secret"));
  assert.throws(()=>decodeRefreshOutput('{"error":{"code":"cookie=secret","message":"private"}}'),e=>e.status===503&&e.code==="refresh_unavailable"&&!e.message.includes("private"));
  assert.throws(()=>decodeRefreshOutput("Traceback private token"),e=>e.status===503&&!e.message.includes("token"));
});
test("project resolution is stable from both web and repository directories",()=>{
  const root=projectRoot();assert.equal(projectRoot(root+"/apps/web"),root);assert.ok(existsSync(join(root,"scripts/lib/creator_identity.py")));assert.ok(existsSync(join(root,"apps/web/package.json")));
});
test("all seven tested markets expose the same core-profile request contract",()=>{
 for(const market of ["it","br","my","th","uk","de","fr"]){
  assert.equal(coreProfileMarket(market),true);
  assert.deepEqual(parseRefreshRequest({market,creatorId,requestId:"core-1"}),{market,creatorId,requestId:"core-1"});
  assert.equal(parseRefreshQuery(`http://localhost/api?market=${market}&creatorId=${creatorId}`).input.market,market);
 }
 assert.equal(coreProfileMarket("mx"),false);
 assert.equal(coreProfileMarket("zz"),false);
});
test("market mismatches have a fixed error without leaking another market's data",()=>{
 assert.throws(()=>decodeRefreshOutput('{"error":{"code":"market_mismatch","message":"private creator"}}'),e=>e.status===409&&!e.message.includes("private"));
});
test("refresh bridge sends an explicit fixed market argv and only canonical IDs on stdin",async()=>{
 let captured;
 const run=(executable,args,options,callback)=>({stdin:{on(){return this;},end(raw){captured={executable,args,options,raw};queueMicrotask(()=>callback(null,'{"market":"br","status":"queued"}'));}}});
 await callRefreshCommand("enqueue",{market:"br",creatorId,requestId:"frozen-br"},{root:"/project/BDHub-Agent",run});
 assert.deepEqual(captured.args,["/project/BDHub-Agent/scripts/creator-profile-refresh.py","enqueue","--market","br"]);
 assert.equal(captured.options.shell,false);
 assert.deepEqual(JSON.parse(captured.raw),{creatorId,requestId:"frozen-br"});
});
test("a failed child cannot acknowledge a success-looking refresh result",async()=>{
 const run=(executable,args,options,callback)=>({stdin:{on(){return this;},end(){queueMicrotask(()=>callback(new Error("private"),'{"status":"queued"}'));}}});
 await assert.rejects(callRefreshCommand("enqueue",{market:"br",creatorId,requestId:"same"},{root:"/project/BDHub-Agent",run}),e=>e.code==="refresh_unavailable"&&!e.message.includes("private"));
});
