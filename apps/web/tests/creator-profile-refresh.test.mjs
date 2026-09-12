import test from "node:test";
import assert from "node:assert/strict";
import {decodeRefreshOutput,parseRefreshRequest,parseRefreshQuery,projectRoot} from "../src/server/creator-identities/refresh.ts";

const creatorId="creator_"+"a".repeat(32);
test("refresh accepts a canonical creator only, never a replacement handle, OEC or executable path",()=>{
  assert.deepEqual(parseRefreshRequest({creatorId,requestId:"test-1"}),{creatorId,requestId:"test-1"});
  for(const body of [{creatorId,requestId:"t",handle:"someone"},{creatorId,requestId:"t",oecId:"123"},{creatorId,requestId:"t",script:"evil"},{creatorId:123,requestId:"t"},{creatorId,requestId:"a\ncommand"},null])assert.throws(()=>parseRefreshRequest(body));
});
test("status lookups reject multiple identities and unbounded queries",()=>{
  assert.deepEqual(parseRefreshQuery(`http://localhost/api?creatorId=${creatorId}`),{command:"list",input:{creatorId}});
  assert.equal(parseRefreshQuery("http://localhost/api?jobId=refresh_123").command,"status");
  for(const query of ["jobId=../../secret","creatorId=123",`creatorId=${creatorId}&jobId=job`,"jobId=x&jobId=y","jobId=x&path=anything"])assert.throws(()=>parseRefreshQuery(`http://localhost/api?${query}`));
});
test("CLI errors expose only fixed messages, never raw process diagnostics",()=>{
  assert.throws(()=>decodeRefreshOutput('{"error":{"code":"idempotency_conflict","message":"secret token"}}'),e=>e.status===409&&!e.message.includes("secret"));
  assert.throws(()=>decodeRefreshOutput('{"error":{"code":"cookie=secret","message":"private"}}'),e=>e.status===503&&e.code==="refresh_unavailable"&&!e.message.includes("private"));
  assert.throws(()=>decodeRefreshOutput("Traceback private token"),e=>e.status===503&&!e.message.includes("token"));
});
test("project resolution is stable from both web and repository directories",()=>{
  const root=projectRoot();assert.equal(projectRoot(root+"/apps/web"),root);assert.match(root,/BDHub-Agent$/);
});
