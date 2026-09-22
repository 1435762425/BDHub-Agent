import test from "node:test";
import assert from "node:assert/strict";
import {parseCreatorIdentityQuery} from "../src/server/creator-identities/validation.ts";
import {GET} from "../src/app/api/creator-identities/route.ts";
const parse=q=>parseCreatorIdentityQuery(`http://127.0.0.1:5198/api/creator-identities?${q}`);

test("identity GET validation supports exact views and bounded pagination",()=>{
  assert.deepEqual(parse("view=list&market=it&q=%40old_name&status=verified&limit=30&offset=20"),{view:"list",market:"it",q:"old_name",status:"verified",limit:30,offset:20});
  assert.deepEqual(parse("view=source&market=it&oecId=123&externalId=source-1"),{view:"source",market:"it",oecId:"123",externalId:"source-1"});
  assert.deepEqual(parse("view=list&market=br"),{view:"list",market:"br",q:"",status:"verified",limit:20,offset:0});
  assert.deepEqual(parse("view=list&market=my"),{view:"list",market:"my",q:"",status:"verified",limit:20,offset:0});
  assert.deepEqual(parse("view=list&market=uk"),{view:"list",market:"uk",q:"",status:"verified",limit:20,offset:0});
  assert.deepEqual(parse(""),{view:"overview",market:"it"});
});
test("unsupported, duplicate, excessive and handle routing queries are rejected",()=>{
  for(const q of ["view=source&handle=alice","view=list&market=us","view=list&market=mx","view=detail&creatorId=../other","view=list&limit=101","view=list&offset=-1","view=source&oecId=1e5","view=source&oecId=","view=source&externalId=","view=list&q="+"a".repeat(81),"view=list&market=it&market=mx","view=list&status=resolved","view=source&externalId=%0a", "view=source&externalId="+"a".repeat(257)])assert.throws(()=>parse(q),error=>error.code==="invalid_request");
});
test("GET rejects foreign host and cross-origin requests before reading database",async()=>{
  for(const headers of [{host:"evil.invalid"},{host:"127.0.0.1:5198",origin:"https://evil.invalid"},{host:"127.0.0.1:5198","sec-fetch-site":"cross-site"}]){
    const response=await GET(new Request("http://127.0.0.1:5198/api/creator-identities",{headers}));assert.equal(response.status,403);assert.equal((await response.json()).error.code,"local_origin_required");
  }
  const invalid=await GET(new Request("http://127.0.0.1:5198/api/creator-identities?view=source&handle=alice",{headers:{host:"127.0.0.1:5198"}}));assert.equal(invalid.status,400);
});
