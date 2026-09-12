import test from "node:test";
import assert from "node:assert/strict";
import {createSecondOutreachHandlers,parseSecondOutreachCommand,parseSecondOutreachQuery} from "../src/server/second-outreach/bridge.ts";
const BASE="http://127.0.0.1:5198",headers={host:"127.0.0.1:5198",origin:BASE,"content-type":"application/json"};
test("second outreach accepts only local preparation/control commands, never transport or arbitrary facts",()=>{
  assert.deepEqual(parseSecondOutreachCommand({requestId:"one",command:{type:"refresh"}}),{requestId:"one",command:{type:"refresh"}});
  for(const command of [{type:"send"},{type:"approve"},{type:"prepare",opportunityId:"one",expectedRevision:1,oecId:"123"},{type:"control",opportunityId:"one",expectedRevision:1,control:"send"}])assert.throws(()=>parseSecondOutreachCommand({requestId:"one",command}));
});
test("query range and source identity cannot be injected into a second outreach read",()=>{
  assert.deepEqual(parseSecondOutreachQuery(`${BASE}/api/second-outreach?view=list&filter=identified`),{command:"list",input:{offset:0,limit:20,q:"",filter:"identified"}});
  for(const q of ["view=send","view=list&limit=1000","view=list&offset=-1","view=list&filter=identified&filter=all","view=opportunity&id=../../old-db","view=status&account=acc1"])assert.throws(()=>parseSecondOutreachQuery(`${BASE}/api/second-outreach?${q}`));
});
test("nonlocal requests and unsupported commands never reach the backend",async()=>{
  let calls=0;const api=createSecondOutreachHandlers(async()=>{calls++;return {};});
  const post=(body,extra={})=>new Request(`${BASE}/api/second-outreach`,{method:"POST",headers:{...headers,...extra},body:JSON.stringify(body)});
  assert.equal((await api.POST(post({requestId:"one",command:{type:"refresh"}},{origin:"https://external.invalid"}))).status,403);
  assert.equal((await api.POST(post({requestId:"one",command:{type:"send"}}))).status,400);
  assert.equal(calls,0);
});
test("local prepare keeps original request/version and private errors are not exposed",async()=>{
  const body={requestId:"one",command:{type:"prepare",opportunityId:"second-one",expectedRevision:1}},calls=[];
  const api=createSecondOutreachHandlers(async(command,input)=>{calls.push({command,input});return {packetId:"second-packet-test",executionBlocked:true};});
  const r=await api.POST(new Request(`${BASE}/api/second-outreach`,{method:"POST",headers,body:JSON.stringify(body)}));
  assert.equal(r.status,200);assert.deepEqual(calls,[{command:"command",input:body}]);
  const failure=createSecondOutreachHandlers(async()=>{throw Error("PRIVATE_KEY");});
  const output=await failure.GET(new Request(`${BASE}/api/second-outreach`,{headers:{host:headers.host}}));assert.equal(output.status,503);assert(!(await output.text()).includes("PRIVATE_KEY"));
});
