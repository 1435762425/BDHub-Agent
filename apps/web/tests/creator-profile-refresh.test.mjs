import test from "node:test";
import assert from "node:assert/strict";
import {existsSync} from "node:fs";
import {join} from "node:path";
import {decodeRefreshOutput,parseRefreshRequest,parseRefreshQuery,projectRoot} from "../src/server/creator-identities/refresh.ts";

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
