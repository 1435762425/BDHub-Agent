import test from "node:test";
import assert from "node:assert/strict";
import {MatchingStore} from "../src/server/matching/store.ts";
import {compileDraftContext,normalizeDraftContextRequest} from "../src/server/outreach-drafts/context.ts";
const NOW=1_780_000_000_000,REGISTRY="creator_"+"a".repeat(32),OEC="7493995835639171139",PID="1729502070782139035";
const source={ref:"fixture-product-source",observedAt:NOW,windowStart:null,windowEnd:null};
const category={status:"observed",namespace:"fixture-alignment",sourceLabels:["Sports & Outdoor"],source,transformVersion:"fixture-v1",timeBasis:"field_observation",note:""};
function fixture(t){
  const store=new MatchingStore(":memory:",{seed:false,now:()=>NOW+1000,dataset:{id:"italy-profiles-fixture",mode:"imported-offline",label:"fixture",importedAt:NOW,sourceRefs:["fixture"],warnings:[]}});t.after(()=>store.close());
  const creator={id:"match-creator",market:"it",oecId:OEC,name:"@creator_test",avatar:"",categories:["sport"],categoryFact:category,formats:[],bio:"",priceMinMinor:null,priceMaxMinor:null,currency:"EUR",control:"unknown",marketingStopped:null,source,profileOrigin:{kind:"identity_registry",creatorId:REGISTRY,observationRef:"private-registry-ref",observedAt:NOW,categoryMode:"observed",metricStates:{followers:"value",unitsSold:"value",avgViews:"value",gmvValue:"value"}},profileSignals:{followers:31234,unitsSold:6789,avgViews:7890,gmvValue:"99999.99",gmvCurrency:"EUR",periodLabel:null,comparisonScope:"fixture",source}};
  const product={id:"match-product",market:"it",pid:PID,title:"Leggings yoga",image:"",categories:["sport"],categoryFact:category,formats:[],description:"",priceMinor:null,currency:"EUR",source};
  store.upsert({creators:[creator],products:[product]});const run=store.recall({direction:"creator",subjectId:creator.id,source:"first",limit:20}),packet=store.prepareReview(run.id,creator.id);
  const identity={kind:"creator",status:"verified",creatorId:REGISTRY,market:"it",oecId:OEC,currentHandle:"creator_test",currentHandleVerifiedAt:new Date(NOW).toISOString(),verifiedAt:new Date(NOW).toISOString(),lastObservedAt:new Date(NOW).toISOString(),handleConflict:false};
  const request={packetId:packet.id,style:"friendly",instructions:"不要使用表情"};return {store,creator,product,run,packet,identity,request,compile:(extra={})=>compileDraftContext(request,packet,run,identity,"f".repeat(64),extra)};
}
test("capsule is bounded and includes permitted named products and fit facts without private identity or metrics",t=>{
  const f=fixture(t),one=f.compile(),two=f.compile();assert.equal(one.fingerprint,two.fingerprint);assert.equal(one.context.binding.oecId,OEC);assert.equal(one.context.executionBlocked,true);
  const model=JSON.stringify(one.context.modelFacts);for(const forbidden of [OEC,REGISTRY,"99999.99","31234","6789","private-registry-ref",PID])assert(!model.includes(forbidden),forbidden);
  assert.equal(one.context.modelFacts.products[0].id,"p1");assert.equal(one.context.modelFacts.products[0].nameIt,"leggings da yoga");assert.deepEqual(one.context.modelFacts.commercialTerms.allowedPromises,[]);assert(Buffer.byteLength(model)<8000);
  assert.equal(one.context.modelFacts.intent,"explore_interest");assert.equal(one.context.modelFacts.recipient.handle,"creator_test");
});
test("wrong identity, conflicted handle and unsupported external-only identity cannot be compiled",t=>{
  const f=fixture(t);for(const identity of [{...f.identity,oecId:"123"},{...f.identity,creatorId:"creator_"+"b".repeat(32)},{...f.identity,handleConflict:true}])assert.throws(()=>compileDraftContext(f.request,f.packet,f.run,identity,"f".repeat(64)),e=>e.code==="identity_unverified");
  const run=structuredClone(f.run);run.candidates[0].creator.externalIdentity={namespace:"kalodata",id:"123"};assert.throws(()=>compileDraftContext(f.request,f.packet,run,f.identity,"f".repeat(64)));
});
test("changed source, missing fit, suppressed control and packet product mismatch stop before a provider call",t=>{
  const f=fixture(t);assert.throws(()=>compileDraftContext(f.request,f.packet,{...f.run,stale:true},f.identity,"f".repeat(64)),e=>e.code==="stale_context");
  for(const change of [c=>c.creator.control="paused",c=>c.creator.marketingStopped=true,c=>c.readiness="suppressed"]){const run=structuredClone(f.run);change(run.candidates[0]);assert.throws(()=>compileDraftContext(f.request,f.packet,run,f.identity,"f".repeat(64)),e=>e.code==="relationship_suppressed");}
  const packet=structuredClone(f.packet);packet.payload.candidates[0].product.pid="not-the-source-product";assert.throws(()=>compileDraftContext(f.request,packet,f.run,f.identity,"f".repeat(64)),e=>e.code==="draft_facts_missing");
});
test("current packet accessor refuses stale evidence and leaves the stored old packet unchanged",t=>{
  const f=fixture(t),raw=f.store.db.prepare('SELECT data FROM review_packets WHERE id=?').get(f.packet.id).data;
  assert.equal(f.store.getCurrentReviewPacket(f.packet.id).id,f.packet.id);
  f.store.upsert({creators:[{...f.creator,control:"paused"}]});assert.throws(()=>f.store.getCurrentReviewPacket(f.packet.id),e=>e.code==="stale_run");
  assert.equal(f.store.db.prepare('SELECT data FROM review_packets WHERE id=?').get(f.packet.id).data,raw);
});
test("style fields cannot set commercial bindings, provider or recipient and input changes alter the fingerprint",t=>{
  const f=fixture(t);assert.throws(()=>normalizeDraftContextRequest({...f.request,model:"another"}));assert.throws(()=>normalizeDraftContextRequest({...f.request,oecId:"another"}));assert.throws(()=>normalizeDraftContextRequest({...f.request,instructions:"x".repeat(501)}));
  const a=f.compile(),b=compileDraftContext({...f.request,style:"direct"},f.packet,f.run,f.identity,"f".repeat(64));assert.notEqual(a.fingerprint,b.fingerprint);
});
