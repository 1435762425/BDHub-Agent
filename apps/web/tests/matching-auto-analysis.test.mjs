import test from "node:test";
import assert from "node:assert/strict";
import {mkdtempSync,rmSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {MatchingStore} from "../src/server/matching/store.ts";

const NOW=1_900_000_000_000;
const source=(ref="facts",observedAt=NOW)=>({ref:`offline:${ref}`,observedAt,windowStart:null,windowEnd:null});
const dataset={id:"offline-analysis-test",mode:"imported-offline",label:"离线自动分析",importedAt:NOW,sourceRefs:["offline:source"],warnings:["旧来源提示：画像过期，价格带不全，等待人工评审"]};
const product=(id=1,extra={})=>({id:`p${id}`,market:"it",pid:`99000000000000${id}`,title:`商品 ${id}`,image:"",categories:["家纺布艺"],formats:[],description:"既有商品",priceMinor:2500,currency:"EUR",source:source(`product/${id}`),...extra});
const signals=(unitsSold=10,extra={})=>({followers:200,unitsSold,avgViews:50,gmvValue:"42.50",gmvCurrency:"EUR",periodLabel:"来源截止日；起始日未提供",comparisonScope:"it:same-source-batch",source:source("profile"),...extra});
const creator=(id=1,extra={})=>({id:`c${String(id).padStart(4,"0")}`,market:"it",oecId:`88000000000000${id}`,name:`达人 ${id}`,avatar:"",categories:["家纺布艺"],formats:[],bio:"历史画像",priceMinMinor:null,priceMaxMinor:null,currency:"EUR",control:"unknown",marketingStopped:null,source:source(`creator/${id}`),...extra});
function fixture(t){const store=new MatchingStore(":memory:",{seed:false,now:()=>NOW,dataset});t.after(()=>store.close());return store;}
const recall=(store,p,extra={})=>store.recall({direction:"product",subjectId:p.id,source:"first",limit:50,...extra});

test("automatically analyzes category facts without profile refresh, price bands, formats or human labels",t=>{
  const store=fixture(t),p=product(),c=creator();store.upsert({products:[p],creators:[c]});
  const run=recall(store,p),candidate=run.candidates[0];
  assert.equal(candidate.analysis.tier,"category_aligned");assert.equal(candidate.analysis.profileSummary.signalStatus,"profile_only");assert.equal(candidate.analysis.profileSummary.signals,null);
  assert(candidate.analysis.positiveEvidence.some(text=>text.includes("家纺布艺")));assert(candidate.analysis.limitations.some(text=>text.includes("不按零销量或低价值")));
  assert(!candidate.analysis.limitations.some(text=>/画像过期|价格带|内容形式/.test(text)));
  assert.equal(candidate.readiness,"needs_facts");assert.equal(run.analysisPolicy.priceBand,"context_only");assert.equal(run.analysisPolicy.contentFormat,"context_only");assert.equal(run.analysisPolicy.profileAge,"ignore_for_analysis");
  assert(!run.warnings.some(text=>text.includes("等待人工")));assert.equal(store.assessments(run.id).summary.reviewed,0);assert.equal(store.stats().llmCalls,0);
});

test("indexed SQL ranks a strong creator beyond ID position 200 before route truncation",t=>{
  const store=fixture(t),p=product(),creators=Array.from({length:260},(_,i)=>creator(i+1,{profileSignals:signals(i===259?999999:i)}));
  store.upsert({products:[p],creators});const run=recall(store,p);
  assert.equal(run.candidates[0].creator.id,creators[259].id);assert.equal(run.candidates.length,50);assert(run.diagnostics.truncated);assert(run.diagnostics.rowsFetched<=200);
  assert(store.explainRecall(run.query).some(line=>line.includes("creator_profile_category_rank")&&line.includes("SEARCH")));assert.equal(run.diagnostics.fullCartesianEvaluated,false);
});

test("category overlap precedes same-scope performance, and views break equal sales ties",t=>{
  const store=fixture(t),p=product(1,{categories:["家纺布艺","居家日用"]});
  const top=creator(1,{categories:p.categories,profileSignals:signals(1,{avgViews:100})}),same=creator(2,{categories:p.categories,profileSignals:signals(1,{avgViews:10})}),other=creator(3,{profileSignals:signals(999999)});
  store.upsert({products:[p],creators:[other,same,top]});assert.deepEqual(recall(store,p).candidates.map(c=>c.creator.id),[top.id,same.id,other.id]);
});

test("explicit demand and exact-product facts produce stronger evidence tiers than profile performance",t=>{
  const store=fixture(t),p=product(),requested=creator(1,{categories:["其他类目"]}),sameProduct=creator(2,{categories:["其他类目"]}),highSales=creator(3,{profileSignals:signals(999999)});
  store.upsert({products:[p],creators:[requested,sameProduct,highSales],demands:[{id:"d1",creatorId:requested.id,productId:p.id,categories:[],active:true,source:source("explicit-request")}],evidence:[{id:"e1",creatorId:sameProduct.id,market:"it",pid:p.pid,units:2,format:null,source:source("exact-product")} ]});
  const run=recall(store,p,{source:"all"});assert.deepEqual(run.candidates.map(c=>c.analysis.tier),["explicit_demand","same_product","category_aligned"]);assert.deepEqual(run.candidates.map(c=>c.creator.id),[requested.id,sameProduct.id,highSales.id]);assert.equal(store.assessments(run.id).summary.reviewed,0);
});

test("price, content format and elapsed profile age do not change imported analysis rank or tier",t=>{
  const store=fixture(t),p=product(),cs=[creator(1,{profileSignals:signals(10)}),creator(2,{profileSignals:signals(20)})];
  store.upsert({products:[p],creators:cs});const before=recall(store,p);
  store.upsert({products:[{...p,priceMinor:1,formats:["live"],source:source("new-price",NOW+1)}],creators:cs.map((c,i)=>({...c,priceMinMinor:i?99999:0,priceMaxMinor:100000,formats:i?["video"]:["live"],source:{...c.source,observedAt:NOW+86400000}}))});
  const after=recall(store,p);assert.deepEqual(after.candidates.map(c=>[c.creator.id,c.analysis.tier]),before.candidates.map(c=>[c.creator.id,c.analysis.tier]));
  assert(after.candidates.every(c=>!c.sources.includes("category_price")&&!c.analysis.limitations.some(text=>/价格带|内容形式|画像过期/.test(text))));
});

test("unknown metrics are an independent lane, preserve null, and do not become zero or an unsuitable verdict",t=>{
  const store=fixture(t),p=product(),known=Array.from({length:220},(_,i)=>creator(i+1,{profileSignals:signals(Math.max(0,200-i))}));
  const unknown=creator(999,{profileSignals:signals(null,{avgViews:null})});store.upsert({products:[p],creators:[...known,unknown]});
  const run=recall(store,p),found=run.candidates.find(c=>c.creator.id===unknown.id);assert(found);assert.equal(found.analysis.tier,"category_aligned");assert.equal(found.creator.profileSignals.unitsSold,null);assert.equal(found.analysis.profileSummary.signalStatus,"profile_only");
  const zero=creator(1000,{profileSignals:signals(0)});store.upsert({creators:[zero]});const reverse=recall(store,zero,{direction:"creator",subjectId:zero.id});assert.equal(reverse.candidates[0].creator.profileSignals.unitsSold,0);assert.equal(reverse.candidates[0].analysis.profileSummary.signalStatus,"observed");
});

test("different source scopes interleave their own ranks rather than compare absolute sales",t=>{
  const store=fixture(t),p=product();const a1=creator(1,{profileSignals:signals(2,{comparisonScope:"a"})}),a2=creator(2,{profileSignals:signals(1,{comparisonScope:"a"})}),b1=creator(3,{profileSignals:signals(9999,{comparisonScope:"b"})}),b2=creator(4,{profileSignals:signals(9998,{comparisonScope:"b"})});
  store.upsert({products:[p],creators:[b2,a2,b1,a1]});assert.deepEqual(recall(store,p).candidates.map(c=>c.creator.id),[a1.id,b1.id,a2.id,b2.id]);
});

test("profile signal updates invalidate scoped caches and reject older metric provenance",t=>{
  const store=fixture(t),p=product(),a=creator(1,{profileSignals:signals(2)}),b=creator(2,{profileSignals:signals(3)});store.upsert({products:[p],creators:[a,b]});const before=recall(store,p);
  const updated={...a,profileSignals:signals(99,{source:source("new-profile",NOW+1)})};assert.equal(store.upsert({creators:[updated]}).semanticChanges,1);const after=recall(store,p);assert.notEqual(after.id,before.id);assert.equal(after.candidates[0].creator.id,a.id);
  assert.throws(()=>store.upsert({creators:[a]}),error=>error.code==="older_observation");assert.equal(recall(store,p).id,after.id);
});

test("profile input requires explicit source, exact decimal GMV and same-market currency",t=>{
  const store=fixture(t);
  for(const patch of [{source:null},{source:undefined},{gmvValue:42.5},{gmvValue:"NaN"},{gmvCurrency:"MXN"},{unitsSold:undefined},{avgViews:-1},{comparisonScope:""}])assert.throws(()=>store.upsert({creators:[creator(1,{profileSignals:signals(10,patch)})]}),error=>error.code==="invalid_fact");
  assert.equal(store.stats().creators,0);
});

test("refused or paused creators can have research conclusions without gaining execution permission",t=>{
  const store=fixture(t),p=product(),c=creator(1,{control:"paused",marketingStopped:true,profileSignals:signals(10)});store.upsert({products:[p],creators:[c]});const run=recall(store,p);
  assert.equal(run.candidates[0].analysis.tier,"category_aligned");assert.equal(run.candidates[0].readiness,"suppressed");assert.throws(()=>store.prepareReview(run.id,c.id),error=>error.code==="relationship_suppressed");
});

test("bounded relationship packet includes one creator signal record, active policy and whole candidate analyses",t=>{
  const store=fixture(t),products=Array.from({length:4},(_,i)=>product(i+1)),c=creator(1,{profileSignals:signals(100)});store.upsert({products,creators:[c]});const run=recall(store,c,{direction:"creator",subjectId:c.id}),packet=store.prepareReview(run.id,c.id);
  assert(packet.candidates>0&&packet.candidates<=4);assert.equal(packet.characters,JSON.stringify(packet.payload).length);assert(packet.characters<=6000);assert.equal(packet.payload.creator.profileSignals.unitsSold,100);assert.equal(packet.payload.analysisPolicy.version,run.analysisPolicy.version);
  assert(packet.payload.candidates.every(item=>item.analysis.tier==="category_aligned"&&!Object.hasOwn(item.analysis,"profileSummary")));assert.equal(packet.payload.omittedCandidates,4-packet.candidates);assert.equal(packet.executionBlocked,true);assert.equal(packet.modelStatus,"not_called");assert.equal(store.assessments(run.id).summary.reviewed,0);
  assert(packet.payload.sharedExecutionGaps.includes("拒联状态未知，不能将没有记录当作允许营销联系"));
  for(const item of packet.payload.candidates) {
    const candidate=run.candidates.find(c=>c.product.id===item.product.id);
    assert.deepEqual(new Set([...packet.payload.sharedExecutionGaps,...item.gaps]),new Set(candidate.gaps));
    assert.deepEqual(new Set([...packet.payload.sharedAnalysisLimitations,...item.analysis.limitations]),new Set(candidate.analysis.limitations));
    assert(!item.analysis.positiveEvidence.some(text=>text.startsWith("画像来源记录销量 ")||text.startsWith("来源平均观看 ")));
  }
});

test("profile rank index and automatic analysis survive reopen without changing stored source facts",t=>{
  const directory=mkdtempSync(join(tmpdir(),"bdhub-analysis-")),path=join(directory,"test.sqlite"),p=product(),c=creator(1,{profileSignals:signals(42)});let store=new MatchingStore(path,{seed:false,dataset});t.after(()=>{store.close();rmSync(directory,{recursive:true,force:true});});store.upsert({products:[p],creators:[c]});const original=recall(store,p);store.close();store=new MatchingStore(path,{seed:false,dataset});const after=recall(store,p);assert.equal(after.id,original.id);assert.equal(after.candidates[0].creator.profileSignals.unitsSold,42);assert.equal(store.upsert({creators:[c]}).unchanged,1);
});

test("profile packets compact long category notes while preserving source metadata, three whole products and stored facts",t=>{
  const store=fixture(t),fact={status:"historical",namespace:"source-labels-v1",sourceLabels:["家纺布艺"],source:source("category"),transformVersion:"translation-v1",timeBasis:"field_observation",note:"保留原始来源的详细说明。".repeat(35)},c=creator(1,{categoryFact:fact,profileSignals:signals(100)}),products=Array.from({length:3},(_,i)=>product(i+1,{categoryFact:{...fact,source:source(`product-category/${i+1}`)}}));
  store.upsert({products,creators:[c]});const run=recall(store,c,{direction:"creator",subjectId:c.id}),packet=store.prepareReview(run.id,c.id);
  assert.equal(packet.candidates,3);assert.equal(packet.payload.omittedCandidates,0);assert(packet.characters<=6000);assert.deepEqual(packet.payload.datasetWarnings,[]);assert.equal(packet.payload.omittedDatasetWarnings,dataset.warnings.length);assert.equal(packet.payload.sourceNotesStoredLocally,true);
  const {note,...provenance}=fact;assert.deepEqual(packet.payload.creator.categoryFact,{...provenance,omittedNoteCharacters:note.length});
  for(const item of packet.payload.candidates) {
    const original=products.find(product=>product.id===item.product.id).categoryFact;const {note,...provenance}=original;
    assert.deepEqual(item.product.categoryFact,{...provenance,omittedNoteCharacters:note.length});
  }
  assert.equal(store.listCreators().items[0].categoryFact.note,fact.note);assert(store.listProducts().items.every(product=>product.categoryFact.note===fact.note));
});
