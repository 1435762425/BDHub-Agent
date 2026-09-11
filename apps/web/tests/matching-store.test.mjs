import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync,rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { MatchingStore,MatchingError } from "../src/server/matching/store.ts";
import { makeMatchingFixture } from "../src/server/matching/fixtures.ts";

const NOW=1_900_000_000_000;
const source=(ref="fact",extra={})=>({ref:`synthetic:${ref}`,observedAt:NOW,windowStart:null,windowEnd:null,...extra});
function product(id="p1",extra={}) {return {id:`synthetic-product-${id}`,market:"mx",pid:`990000000000000000${id.replace(/\D/g,"")||"1"}`,title:`[合成] 商品 ${id}`,image:"",categories:["home"],formats:["video"],description:"合成商品事实",priceMinor:10000,currency:"MXN",source:source(`product/${id}`),...extra};}
function creator(id="c1",extra={}){return {id:`synthetic-creator-${id}`,market:"mx",oecId:`880000000000000000${id.replace(/\D/g,"")||"1"}`,name:`[合成] 达人 ${id}`,avatar:"",categories:["home"],formats:["video"],bio:"合成达人事实",priceMinMinor:5000,priceMaxMinor:15000,currency:"MXN",control:"auto",marketingStopped:false,source:source(`creator/${id}`),...extra};}
function offer(p,id="o1",extra={}){return {id:`synthetic-offer-${id}`,productId:p.id,campaignId:`synthetic-campaign-${id}`,accountRef:"synthetic-account-mx",publicCommissionBps:800,totalCommissionBps:1500,creatorCommissionBps:1200,agencyCommissionBps:300,stock:20,sampleAvailable:false,sampleQuota:0,startsAt:NOW-1000,endsAt:NOW+10000,cardStatus:"verified",source:source(`offer/${id}`),...extra};}
function evidence(p,c,id="e1",extra={}){return {id:`synthetic-evidence-${id}`,creatorId:c.id,market:c.market,pid:p.pid,units:3,format:"video",source:source(`evidence/${id}`,{windowStart:NOW-30*86400000,windowEnd:NOW}),...extra};}
function demand(p,c,id="d1",extra={}){return {id:`synthetic-demand-${id}`,creatorId:c.id,productId:p?.id??null,categories:[],active:true,source:source(`demand/${id}`),...extra};}
function fixture(t,{seed=false}={}){const clock={at:NOW};const store=new MatchingStore(":memory:",{seed,now:()=>clock.at});t.after(()=>store.close());return {store,clock};}
function recall(store,subject,direction="product",source="all",limit=50){return store.recall({direction,subjectId:subject.id,source,limit});}
function basic(t){const f=fixture(t),p=product(),c=creator(),o=offer(p);f.store.upsert({products:[p],creators:[c],offers:[o]});return {...f,p,c,o};}

test("default synthetic fixture has three markets, exact evidence, and no model usage",t=>{
  const {store}=fixture(t,{seed:true});const stats=store.stats();assert.equal(stats.products,72);assert.equal(stats.creators,360);assert.equal(stats.semanticBuilds,432);assert.equal(stats.llmCalls,0);assert.equal(stats.billedTokens,0);
  for(const market of ["mx","br","it"]){const p=store.listProducts({market}).items[0];assert.match(p.pid,/^\d{20}$/);assert(recall(store,p,"product","second").candidates.length>0);}
});
test("same normalized facts import idempotently, including multi-offer product revision",t=>{
  const {store}=fixture(t);const batch=makeMatchingFixture({products:9,creators:15,now:NOW});const first=store.upsert(batch),before=store.stats(),p=store.listProducts().items[0];const second=store.upsert(batch);
  assert(first.inserted>0);assert.equal(second.inserted,0);assert.equal(second.updated,0);assert.equal(second.semanticChanges,0);assert.equal(second.commercialChanges,0);assert.equal(store.stats().semanticBuilds,before.semanticBuilds);assert.equal(store.listProducts().items[0].commercialRevision,p.commercialRevision);
});
test("commercial updates invalidate matching without semantic rebuild; semantic updates are separate",t=>{
  const {store,p,c,o}=basic(t);const initial=store.stats().semanticBuilds;const run=recall(store,p);const next=store.upsert({products:[{...p,priceMinor:11000}],offers:[{...o,stock:0}]});assert.equal(next.semanticChanges,0);assert.equal(next.commercialChanges,2);assert.equal(store.stats().semanticBuilds,initial);assert.notEqual(recall(store,p).id,run.id);
  assert.equal(store.upsert({creators:[{...c,bio:"新的明确内容主题"}]}).semanticChanges,1);assert.equal(store.upsert({products:[{...p,description:"新的商品语义"}]}).semanticChanges,1);
});
test("long string identities and explicit unknown values survive persistence; null does not become zero",t=>{
  const dir=mkdtempSync(join(tmpdir(),"bdhub-matching-")),path=join(dir,"matching.sqlite"),p=product("p9",{priceMinor:null}),c=creator("c9",{priceMinMinor:null,priceMaxMinor:null});let store=new MatchingStore(path,{seed:false,now:()=>NOW});
  t.after(()=>{store.close();rmSync(dir,{recursive:true,force:true});});store.upsert({products:[p],creators:[c],offers:[offer(p,"o9",{stock:null,sampleAvailable:null,sampleQuota:null,creatorCommissionBps:null,agencyCommissionBps:null})]});store.close();store=new MatchingStore(path,{seed:false,now:()=>NOW});const candidate=recall(store,p).candidates[0];assert.equal(candidate.product.pid,p.pid);assert.equal(candidate.creator.oecId,c.oecId);assert.equal(candidate.features.priceOverlap,null);assert.equal(candidate.offers[0].stock,null);assert.equal(candidate.offers[0].sampleQuota,null);assert.equal(candidate.readiness,"needs_facts");assert.equal(candidate.product.source.windowStart,null);
});
test("invalid amounts, currencies, identities and windows reject atomically",t=>{
  const {store}=fixture(t);const p=product();for(const change of [{pid:9900000000000000001},{currency:"BRL"},{priceMinor:undefined},{priceMinor:-1},{priceMinor:0.5},{source:source("bad",{windowStart:20,windowEnd:10})}])assert.throws(()=>store.upsert({products:[{...p,...change}]}),e=>e instanceof MatchingError&&e.status===400);
  assert.throws(()=>store.upsert({products:[p],creators:[creator("c1",{currency:"EUR"})]}));assert.equal(store.stats().products,0);
  store.upsert({products:[p],creators:[creator()]});assert.throws(()=>store.upsert({products:[{...p,pid:"123"}]}),e=>e.code==="identity_conflict");assert.throws(()=>store.upsert({products:[{...p,id:"another-id"}]}),e=>e.code==="identity_conflict");
});
test("offers keep one complete campaign quote and validate commission arithmetic",t=>{
  const {store,p}=basic(t);assert.throws(()=>store.upsert({offers:[offer(p,"bad",{creatorCommissionBps:1300})]}),e=>e.code==="invalid_fact");store.upsert({offers:[offer(p,"o2",{totalCommissionBps:2500,creatorCommissionBps:1000,agencyCommissionBps:1500,stock:0,sampleAvailable:true,sampleQuota:20})]});const candidate=recall(store,p).candidates[0];assert.equal(candidate.offers.length,2);assert.equal(candidate.offers.find(o=>o.campaignId==="synthetic-campaign-o1").creatorCommissionBps,1200);assert.equal(candidate.offers.find(o=>o.campaignId==="synthetic-campaign-o2").stock,0);assert(candidate.gaps.some(g=>g.includes("无库存")));
});
test("second recall requires same-market exact PID positive sales, never category, zero units, or samples",t=>{
  const {store,p,c}=basic(t),zero=creator("c2"),other=creator("c3"),yes=creator("c4"),br=creator("c5",{market:"br",currency:"BRL"});store.upsert({creators:[zero,other,yes,br],evidence:[evidence(p,zero,"zero",{units:0}),evidence(p,other,"different",{pid:"1234567890123456789"}),evidence(p,yes,"positive"),evidence(p,br,"br")]});const run=recall(store,p,"product","second");assert.deepEqual(run.candidates.map(x=>x.creator.id),[yes.id]);assert.equal(run.candidates[0].offers[0].sampleAvailable,false);assert.equal(recall(store,c,"creator","second").candidates.length,0);
});
test("multi-category, explicit demand and reverse lookup merge a single pair",t=>{
  const {store,p,c}=basic(t);const creator2={...c,categories:["beauty","home"]};store.upsert({creators:[creator2],evidence:[evidence(p,c)],demands:[demand(p,c)]});const run=recall(store,p);assert.equal(run.candidates.length,1);for(const route of ["explicit_demand","exact_pid","category_price","category"])assert(run.candidates[0].sources.includes(route));const reverse=recall(store,c,"creator");assert.equal(reverse.candidates.length,1);assert.equal(reverse.candidates[0].product.id,p.id);
});
test("positive observations in overlapping windows are not summed as total sales",t=>{
  const {store,p,c}=basic(t);store.upsert({evidence:[evidence(p,c,"e1",{units:4}),evidence(p,c,"e2",{units:7})]});const candidate=recall(store,p,"product","second").candidates[0];assert.equal(candidate.features.exactUnits,7);assert(candidate.reasons.some(r=>r.includes("单一观测 7 件")&&r.includes("窗口不相加")));assert(candidate.evidenceRefs.includes("synthetic:evidence/e2"));
});
test("cold start preserves unknown categories, and explicit demand can find outside profile categories",t=>{
  const {store}=fixture(t),p=product(),c=creator("c1",{categories:[],priceMinMinor:null}),outside=creator("c2",{categories:["pets"]});store.upsert({products:[p],creators:[c,outside],demands:[demand(p,outside)]});const run=recall(store,p,"product","first");assert(run.candidates.find(x=>x.creator.id===c.id).sources.includes("cold_start"));assert(run.candidates.find(x=>x.creator.id===outside.id).sources.includes("explicit_demand"));assert.equal(run.candidates.find(x=>x.creator.id===c.id).features.priceOverlap,null);
});
test("offer price changes recall newly eligible candidates instead of updating only old output edges",t=>{
  const {store}=fixture(t),c=creator(),old=product("p1",{priceMinor:10000}),entrant=product("p2",{priceMinor:30000});store.upsert({products:[old,entrant],creators:[c]});const before=recall(store,c,"creator","first",1);assert.equal(before.candidates[0].product.id,old.id);
  store.upsert({products:[{...entrant,priceMinor:8000},{...old,priceMinor:30000}]});const after=recall(store,c,"creator","first",1);assert.notEqual(after.id,before.id);assert.equal(after.candidates[0].product.id,entrant.id);assert.throws(()=>store.prepareReview(before.id,c.id),e=>e.code==="stale_run");
});
test("new in-scope creator invalidates cached product recall even when no previous candidate existed",t=>{
  const {store}=fixture(t),p=product(),c=creator();store.upsert({products:[p]});const empty=recall(store,p);assert.equal(empty.candidates.length,0);store.upsert({creators:[c]});const next=recall(store,p);assert.notEqual(next.id,empty.id);assert.equal(next.candidates[0].creator.id,c.id);
});
test("other market and unrelated category updates retain cached run",t=>{
  const {store,p}=basic(t);const run=recall(store,p);store.upsert({creators:[creator("c20",{market:"br",currency:"BRL"}),creator("c21",{categories:["pets"]})]});const same=recall(store,p);assert.equal(same.id,run.id);assert.equal(same.cacheHit,true);
});
test("new exact evidence invalidates first-source enriched context, and new demand invalidates second",t=>{
  const {store,p,c}=basic(t);const first=recall(store,p,"product","first");store.upsert({evidence:[evidence(p,c)]});assert.notEqual(recall(store,p,"product","first").id,first.id);const second=recall(store,p,"product","second");store.upsert({demands:[demand(p,c)]});assert.notEqual(recall(store,p,"product","second").id,second.id);
});
test("suppressed relationships remain discoverable but cannot prepare an Agent packet",t=>{
  const {store,p,c}=basic(t);for(const change of [{control:"human"},{control:"paused"},{marketingStopped:true}]){store.upsert({creators:[{...c,...change}]});const run=recall(store,p);assert.equal(run.candidates[0].readiness,"suppressed");assert.throws(()=>store.prepareReview(run.id,c.id),e=>e.code==="relationship_suppressed");}
});
test("review packets isolate a single creator, cap candidates and characters, and reuse current fingerprint",t=>{
  const {store}=fixture(t),c=creator(),other=creator("c2");const products=Array.from({length:12},(_,i)=>product(`p${i+1}`));store.upsert({products,creators:[c,other],offers:products.map((p,i)=>offer(p,`o${i+1}`)),evidence:[evidence(products[0],c)]});const run=recall(store,c,"creator"),packet=store.prepareReview(run.id,c.id);assert(packet.candidates>0&&packet.candidates<=5);assert(packet.characters<=6000);assert.equal(packet.characters,JSON.stringify(packet.payload).length);assert.equal(packet.estimatedTokens,null);assert.equal(packet.modelStatus,"not_called");assert.equal(packet.executable,false);assert(!JSON.stringify(packet.payload).includes(other.id));assert.equal(store.prepareReview(run.id,c.id).id,packet.id);assert.throws(()=>store.prepareReview(run.id,other.id),e=>e.code==="candidate_missing");
});
test("commercial and control changes invalidate packets; expiration rejects old run without new write",t=>{
  const {store,p,c,o,clock}=basic(t);const run=recall(store,p),packet=store.prepareReview(run.id,c.id);store.upsert({offers:[{...o,stock:0}]});assert.throws(()=>store.prepareReview(run.id,c.id),e=>e.code==="stale_run");let current=recall(store,p);assert.notEqual(store.prepareReview(current.id,c.id).id,packet.id);clock.at=NOW+11000;assert.throws(()=>store.prepareReview(current.id,c.id),e=>e.code==="stale_run");const expired=recall(store,p);assert.notEqual(expired.id,current.id);assert(expired.candidates[0].gaps.some(g=>g.includes("已过期")));assert.throws(()=>store.prepareReview(current.id,c.id),e=>e.code==="stale_run");
});
test("demo update uses optimistic concurrency and durable request idempotency",t=>{
  const {store,p}=basic(t),saved=store.listProducts().items[0];const one=store.demoChange(p.id,saved.commercialRevision,"raise_price","request-1");assert.equal(one.product.priceMinor,20000);assert.deepEqual(store.demoChange(p.id,saved.commercialRevision,"raise_price","request-1"),one);assert.throws(()=>store.demoChange(p.id,saved.commercialRevision,"lower_price","request-1"),e=>e.code==="request_conflict");assert.throws(()=>store.demoChange(p.id,saved.commercialRevision,"raise_price","request-2"),e=>e.code==="revision_conflict");assert.equal(store.stats().llmCalls,0);
});
test("every route uses indexed scope, returns bounded rows, and does not evaluate Cartesian pairs",t=>{
  const {store}=fixture(t);store.upsert(makeMatchingFixture({products:180,creators:2400,now:NOW}));const p=store.listProducts({market:"mx"}).items[0],c=store.listCreators({market:"mx"}).items[0];for(const [subject,direction] of [[p,"product"],[c,"creator"]]){const run=recall(store,subject,direction);assert(run.candidates.length<=50);assert(run.diagnostics.rowsFetched<=1200);assert.equal(run.diagnostics.perRouteLimit,200);assert.equal(run.diagnostics.fullCartesianEvaluated,false);assert.equal(run.diagnostics.llmCalls,0);const plan=store.explainRecall(run.query);assert(plan.some(line=>line.includes("SEARCH")&&line.includes("INDEX")));assert(!plan.some(line=>/SCAN (products|creators|evidence|product_categories|creator_categories)/.test(line)));}
});
test("catalog pagination, escaped search and import unknown-field validation",t=>{
  const {store}=fixture(t,{seed:true});const page=store.listProducts({market:"mx",limit:5,offset:5});assert.equal(page.items.length,5);assert.equal(page.total,24);assert.equal(store.listProducts({q:"%"}).total,0);assert.throws(()=>store.listProducts({limit:1000}));assert.throws(()=>store.upsert({secrets:[]}));
});

test("many overlapping observations cannot consume a whole exact route for a single creator",t=>{
  const {store,p,c}=basic(t),second=creator("c2");store.upsert({creators:[second],evidence:[...Array.from({length:250},(_,i)=>evidence(p,c,`many${i}`,{units:10})),evidence(p,second,"other",{units:1})]});const run=recall(store,p,"product","second");assert.deepEqual(new Set(run.candidates.map(x=>x.creator.id)),new Set([c.id,second.id]));assert.equal(run.diagnostics.rowsFetched,2);
});
test("review prioritizes a complete currently usable Offer rather than splicing activity maxima",t=>{
  const {store,p,c,o}=basic(t);store.upsert({offers:[{...o,stock:0},offer(p,"o2",{cardStatus:"unknown"}),offer(p,"o3",{totalCommissionBps:1400,creatorCommissionBps:1000,agencyCommissionBps:400})]});const run=recall(store,p),packet=store.prepareReview(run.id,c.id);assert.equal(packet.payload.candidates[0].offers[0].id,"synthetic-offer-o3");assert.equal(packet.payload.candidates[0].offers[0].creatorCommissionBps,1000);assert.equal(packet.payload.candidates[0].offers[0].agencyCommissionBps,400);
});

test("out-of-order observations cannot roll back current facts or invalidate valid cached results",t=>{
  const {store,p,c,o}=basic(t);const e=evidence(p,c),d=demand(p,c);store.upsert({evidence:[e],demands:[d]});
  const run=recall(store,p),builds=store.stats().semanticBuilds;
  for(const [key,record] of [["products",p],["creators",c],["offers",o],["evidence",e],["demands",d]]){
    assert.throws(()=>store.upsert({[key]:[{...record,source:{...record.source,observedAt:NOW-1}}]}),error=>error instanceof MatchingError&&error.code==="older_observation");
    assert.equal(recall(store,p).id,run.id);
  }
  assert.throws(()=>store.upsert({products:[{...p,title:"must roll back",source:{...p.source,observedAt:NOW+1}}],offers:[{...o,source:{...o.source,observedAt:NOW-1}}]}));
  assert.equal(store.listProducts().items[0].title,p.title);assert.equal(store.stats().semanticBuilds,builds);
});

test("creator price-band updates refresh recall without rebuilding semantic features",t=>{
  const {store,p,c}=basic(t);const before=recall(store,p),builds=store.stats().semanticBuilds;
  store.upsert({creators:[{...c,priceMinMinor:20000,priceMaxMinor:30000,source:{...c.source,observedAt:NOW+1}}]});
  const after=recall(store,p);assert.notEqual(after.id,before.id);assert.equal(after.candidates[0].features.priceOverlap,false);
  assert.equal(store.stats().semanticBuilds,builds);assert.throws(()=>store.prepareReview(before.id,c.id),error=>error.code==="stale_run");
});

test("retrying an old successful change returns current product facts without replaying the mutation",t=>{
  const {store,p}=basic(t);const original=store.listProducts().items[0];
  const first=store.demoChange(p.id,original.commercialRevision,"lower_price","old-request");
  const newer=store.demoChange(p.id,first.product.commercialRevision,"raise_price","new-request");
  const replay=store.demoChange(p.id,original.commercialRevision,"lower_price","old-request");
  assert.equal(replay.product.priceMinor,newer.product.priceMinor);assert.equal(replay.product.commercialRevision,newer.product.commercialRevision);
  assert.equal(store.listProducts().items[0].commercialRevision,newer.product.commercialRevision);
});
