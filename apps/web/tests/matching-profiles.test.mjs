import test from "node:test";
import assert from "node:assert/strict";
import {MatchingStore} from "../src/server/matching/store.ts";
import {planItalyProfileImport} from "../src/server/matching/italy-profile-import.ts";

const NOW=Date.parse("2026-09-12T00:00:00Z"),OLD="2026-07-30T08:00:00+00:00",COLLECTED="2026-09-10T04:57:45+00:00";
const PID="1729480019490150432",CONFLICT="1729779362302171335";
function inputs(){
  const file=(ref,data)=>({ref:`legacy:${ref}`,path:`fixture/${ref}.json`,sha256:"b".repeat(64),data});
  return {
    pilot:file("pilot",{market:"it",observed_at:COLLECTED,rows:[{pid:PID,label:"枕头",price:"39.05",currency:"EUR"},{pid:CONFLICT,label:"口腔清新片",price:"11.03",currency:"EUR"}]}),
    products:file("products",{market:"it",state:"completed",query:{market:"it",mode:"pids"},created_at:"2026-09-10T04:57:35+00:00",updated_at:COLLECTED,rows:[{pid:PID,title:"Cuscino",category_ids:["家纺布艺","床上用品","枕头和背垫"],detail_checked:true,priority_score:999},{pid:CONFLICT,title:"Freegrin compresse per la bocca",category_ids:["家纺布艺","床上用品","枕头和背垫"],detail_checked:true}]}),
    creators:file("creators",{market:"it",facts:{total:3,category_missing:0,read_at:"2026-09-10T09:14:10+00:00",freshness_days:30},creators:["家纺布艺","家纺布艺","保健"].map((category,i)=>({oec_id:`799999999999999999${i}`,handle:`profile_fixture_${i}`,category,category_fresh:false,category_observed_at:OLD,captured_at:OLD,price_range:null,gmv_value:"12345",cookie:"DO_NOT_COPY"}))})
  };
}
function setup(t){const plan=planItalyProfileImport(inputs(),NOW),store=new MatchingStore(":memory:",{dataset:plan.dataset});store.upsert(plan.batch);t.after(()=>store.close());return {plan,store};}
const recall=(store,subject,direction="product")=>store.recall({direction,subjectId:subject.id,source:"first",limit:50});

test("profile import preserves historical field time, raw label order and unknown prices without same-PID evidence",t=>{
  const {plan,store}=setup(t),c=store.listCreators().items[0],p=store.listProducts().items.find(p=>p.pid===PID);
  assert.equal(plan.report.counts.sourceExpiredProfiles,3);assert.equal(c.categoryFact.source.observedAt,Date.parse(OLD));assert.equal(c.source.observedAt,Date.parse(OLD));assert.notEqual(c.source.observedAt,plan.report.profileSnapshotReadAt);
  assert.equal(c.categoryFact.transformVersion,null);assert.equal(c.control,"unknown");assert.equal(c.marketingStopped,null);assert.equal(c.priceMinMinor,null);assert.equal(c.externalIdentity,undefined);assert.match(c.oecId,/^799999999999999999/);
  assert.deepEqual(p.categoryFact.sourceLabels,["家纺布艺","床上用品","枕头和背垫"]);assert.equal(p.categoryFact.timeBasis,"batch_completed");assert.equal(p.categoryFact.source.windowStart,null);
  assert.equal(store.stats().evidence,0);assert.equal(store.stats().offers,0);assert(!JSON.stringify(plan).includes("DO_NOT_COPY"));
});
test("conflicting product category stays preserved but is excluded in both matching directions",t=>{
  const {store}=setup(t),products=store.listProducts().items,conflict=products.find(p=>p.pid===CONFLICT),p=products.find(p=>p.pid===PID);
  assert.equal(conflict.categoryFact.status,"conflict");assert.deepEqual(conflict.categories,[]);assert.deepEqual(conflict.categoryFact.sourceLabels,p.categoryFact.sourceLabels);
  const empty=recall(store,conflict);assert.equal(empty.candidates.length,0);assert(empty.warnings.some(w=>w.includes("冲突")));
  for(const c of store.listCreators().items){const run=recall(store,c,"creator");assert(run.candidates.every(x=>x.product.pid!==CONFLICT));}
  const run=recall(store,p);assert.equal(run.candidates.length,2);assert(run.candidates.every(c=>c.sources.length===1&&c.sources[0]==="category"&&c.features.exactUnits===null));
});
test("first experiment packets expose category provenance and cannot manufacture model or human outcomes",t=>{
  const {store}=setup(t),p=store.listProducts().items.find(p=>p.pid===PID),run=recall(store,p),packet=store.prepareReview(run.id,run.candidates[0].creator.id);
  assert.equal(packet.payload.creator.categoryFact.status,"historical");assert.equal(packet.payload.candidates[0].product.categoryFact.timeBasis,"batch_completed");assert.equal(packet.payload.candidates[0].exactObservation,null);
  assert.equal(packet.executionBlocked,true);assert.equal(packet.modelStatus,"not_called");assert.equal(packet.estimatedTokens,null);assert(packet.characters<=6000);
  assert.equal(store.assessments(run.id).summary.suitabilityRate,null);assert.equal(store.assessments(run.id).summary.reviewed,0);
});
test("old ranking, GMV and unrelated source fields do not affect profile matching facts",()=>{
  const s=inputs(),first=planItalyProfileImport(s,NOW);
  s.products.data.rows[0].priority_score=-99;s.creators.data.creators[0].gmv_value="9999999999";s.pilot.data.rows[0].top_creators=[{handle:"fake",sale:9999}];
  const next=planItalyProfileImport(s,NOW);assert.deepEqual(first.batch,next.batch);
});
test("invalid or duplicate source identity and currency reject before import",()=>{
  for(const change of [s=>s.creators.data.market="mx",s=>s.creators.data.creators[0].oec_id=7999999999999999990,s=>s.creators.data.creators.push(s.creators.data.creators[0]),s=>s.pilot.data.rows[0].currency="IDR",s=>s.products.data.rows[0].detail_checked=false,s=>s.creators.data.creators[0].category_observed_at="2026-07-30T08:00:00"]){const s=inputs();change(s);assert.throws(()=>planItalyProfileImport(s,NOW));}
});
test("category namespace mismatch is not a compatible pair; category provenance changes stale old assessments",t=>{
  const {plan,store}=setup(t),p=store.listProducts().items.find(p=>p.pid===PID),run=recall(store,p),c=plan.batch.creators[0];
  store.assessCandidate(run.id,c.id,p.id,"insufficient","fixture only",0,"fixture-label");
  assert.deepEqual(store.getRun(run.id).query,run.query);assert.equal(store.assessments(store.getRun(run.id).id).summary.insufficient,1);
  store.upsert({creators:[{...c,categoryFact:{...c.categoryFact,namespace:"unmapped_other_namespace"}}]});
  assert.throws(()=>store.assessments(run.id),e=>e.code==="stale_run");const next=recall(store,p);assert(!next.candidates.some(x=>x.creator.id===c.id));assert.equal(store.assessments(next.id).summary.reviewed,0);
  assert.throws(()=>store.getRun(run.id),e=>e.code==="stale_run");
});
test("a conflict cannot be inserted as an active category or used to prepare a false category packet",t=>{
  const {plan,store}=setup(t),p=plan.batch.products.find(p=>p.pid===CONFLICT);
  assert.throws(()=>store.upsert({products:[{...p,categories:["家纺布艺"]}]}),e=>e.code==="invalid_fact");
  const run=recall(store,p);assert.throws(()=>store.prepareReview(run.id,plan.batch.creators[0].id),e=>e.code==="candidate_missing");
});
