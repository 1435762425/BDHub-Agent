import test from "node:test";
import assert from "node:assert/strict";
import {parseMatchingCommand,parseMatchingQuery} from "../src/server/matching/validation.ts";

test("matching query limits remain bounded and market-scoped",()=>{
  const q=parseMatchingQuery("http://127.0.0.1:5198/api/matching?view=products&market=mx&limit=20");
  assert.equal(q.options.market,"mx");assert.equal(q.options.limit,20);
  for(const query of ["market=unknown","limit=5000","limit=-1","offset=-1","q="+"a".repeat(121),"path=/tmp/secret"]){assert.throws(()=>parseMatchingQuery("http://local/?"+query));}
});
test("recall cannot request unbounded pairs, arbitrary paths or actions",()=>{
  const command={type:"recall",query:{direction:"product",subjectId:"product-mx-001",source:"second",limit:20}};
  assert.equal(parseMatchingCommand({requestId:"r1",command}).command.query.source,"second");
  for(const query of [{...command.query,limit:100000},{...command.query,subjectId:"../../db"},{...command.query,sql:"delete"}])assert.throws(()=>parseMatchingCommand({requestId:"r1",command:{...command,query}}));
  assert.throws(()=>parseMatchingCommand({requestId:"r1",command:{type:"send",creatorId:"x"}}));
});
test("review and demo updates require exact references and revisions",()=>{
  const update={type:"demo_change",productId:"p1",expectedRevision:1,change:"lower_price"};
  assert.equal(parseMatchingCommand({requestId:"r1",command:update}).command.expectedRevision,1);
  for(const invalid of [undefined,0,-1,1.5,"1"])assert.throws(()=>parseMatchingCommand({requestId:"r1",command:{...update,expectedRevision:invalid}}));
  assert.throws(()=>parseMatchingCommand({requestId:"r1",command:{type:"prepare_review",runId:"run1",creatorId:"c1",send:true}}));
});
