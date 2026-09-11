import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { MatchingStore, MatchingError } from "../src/server/matching/store.ts";

const NOW=1_900_000_000_000;
const source={ref:"test:italy-history",observedAt:NOW,windowStart:null,windowEnd:null};
const dataset=id=>({id,mode:"imported-offline",label:"意大利离线测试",importedAt:NOW,sourceRefs:[source.ref],warnings:[]});
const product={id:"product-1",market:"it",pid:"1729779362302171335",title:"测试商品",image:"",categories:["home"],formats:["video"],description:"",priceMinor:null,currency:"EUR",source};
const creators=Array.from({length:4},(_,i)=>({id:`creator-${i+1}`,market:"it",oecId:null,externalIdentity:{namespace:"kalodata",id:`123456789123456789${i}`},name:`测试达人 ${i+1}`,avatar:"",categories:["home"],formats:["video"],bio:"",priceMinMinor:null,priceMaxMinor:null,currency:"EUR",control:"unknown",marketingStopped:null,source}));
const batch={products:[product],creators};
const query={direction:"product",subjectId:product.id,source:"first",limit:50};
const hasError=(status,code)=>error=>error instanceof MatchingError&&error.status===status&&error.code===code;
function fixture(t,{persist=false,mode="imported-offline",includeCreators=true}={}) {
  const dir=persist?mkdtempSync(join(tmpdir(),"bdhub-assessments-")):null,path=dir?join(dir,"matching.sqlite"):":memory:",clock={at:NOW};
  const store=new MatchingStore(path,{seed:false,now:()=>clock.at,...(mode==="imported-offline"?{dataset:dataset("italy-test")}:{} )});
  store.upsert(includeCreators?batch:{products:[product]});
  t.after(()=>{store.close();if(dir)rmSync(dir,{recursive:true,force:true});});
  return {store,clock,path,run:store.recall(query)};
}
const assess=(store,run,creatorIndex,label,revision=0,requestId=`request-${creatorIndex}`,note="")=>store.assessCandidate(run.id,creators[creatorIndex].id,product.id,label,note,revision,requestId);

test("candidate judgments start unreviewed and stay local without changing business facts",t=>{
  const {store,run}=fixture(t),before=store.stats(),initial=store.assessments(run.id);
  assert.deepEqual(initial.summary,{total:4,reviewed:0,suitable:0,unsuitable:0,insufficient:0,decided:0,suitabilityRate:null,coverage:0});
  assert(initial.items.every(item=>item.label===null&&item.note===""&&item.revision===0&&item.reviewedAt===null));
  const result=assess(store,run,0,"suitable",0,"local-positive","需人工查看实际内容");
  assert.equal(result.items[0].label,"suitable");assert.equal(result.items[0].revision,1);assert.equal(result.items[0].reviewedAt,NOW);
  assert.deepEqual(store.stats(),before);assert.equal(store.recall(query).id,run.id);
  const current=store.listCreators({market:"it"}).items[0];assert.equal(current.control,"unknown");assert.equal(current.marketingStopped,null);assert.equal(current.oecId,null);
  const packet=store.prepareReview(run.id,current.id);assert.equal(packet.executable,false);assert.equal(packet.executionBlocked,true);assert.equal(packet.modelStatus,"not_called");
});

test("insufficient evidence contributes coverage but never becomes a negative outcome",t=>{
  const {store,run}=fixture(t);
  const insufficient=assess(store,run,0,"insufficient");assert.equal(insufficient.summary.reviewed,1);assert.equal(insufficient.summary.decided,0);assert.equal(insufficient.summary.suitabilityRate,null);assert.equal(insufficient.summary.coverage,0.25);
  assess(store,run,1,"suitable");const result=assess(store,run,2,"unsuitable");
  assert.deepEqual(result.summary,{total:4,reviewed:3,suitable:1,unsuitable:1,insufficient:1,decided:2,suitabilityRate:0.5,coverage:0.75});
  const clear=assess(store,run,2,null,1,"clear-negative");
  assert.equal(clear.items[2].label,null);assert.equal(clear.items[2].revision,2);assert.equal(clear.items[2].reviewedAt,null);assert.equal(clear.summary.decided,1);assert.equal(clear.summary.suitabilityRate,1);assert.equal(clear.summary.coverage,0.5);
  assert.throws(()=>assess(store,run,2,"suitable",0,"stale-after-clear"),hasError(409,"revision_conflict"));
});

test("idempotent retry does not revert a newer judgment and changed request payload is rejected",t=>{
  const {store,run}=fixture(t);
  const first=assess(store,run,0,"suitable",0,"first","initial");assert.deepEqual(assess(store,run,0,"suitable",0,"first","initial"),first);
  const next=assess(store,run,0,"insufficient",1,"next","new evidence needed");
  assert.deepEqual(assess(store,run,0,"suitable",0,"first","initial"),next);
  for(const [label,note,revision] of [["unsuitable","initial",0],["suitable","changed",0],["suitable","initial",1]])assert.throws(()=>assess(store,run,0,label,revision,"first",note),hasError(409,"request_conflict"));
  assert.equal(store.assessments(run.id).items[0].revision,2);
});

test("judgments and request receipts survive reopen and competing SQLite connections use optimistic revisions",t=>{
  const {store,path,run}=fixture(t,{persist:true});
  assess(store,run,0,"suitable",0,"persisted");
  const reader=new MatchingStore(path,{seed:false,now:()=>NOW});
  try{
    const saved=reader.assessments(run.id);assert.equal(saved.items[0].label,"suitable");assert.equal(saved.items[0].revision,1);
    assert.deepEqual(assess(reader,run,0,"suitable",0,"persisted"),saved);
    assert.throws(()=>assess(reader,run,0,"unsuitable",0,"losing-write"),hasError(409,"revision_conflict"));
    const updated=assess(reader,run,0,"insufficient",1,"winning-write");assert.equal(updated.items[0].revision,2);
    assert.throws(()=>assess(store,run,0,"suitable",1,"second-loser"),hasError(409,"revision_conflict"));
    assert.equal(store.assessments(run.id).items[0].label,"insufficient");
  }finally{reader.close();}
});

test("changed facts make reads, writes and cached retries stale while historical annotations remain stored",t=>{
  const {store,path,run}=fixture(t,{persist:true});assess(store,run,0,"suitable",0,"historical");
  store.upsert({products:[{...product,priceMinor:1200,source:{...source,observedAt:NOW+1}}]});
  for(const action of [()=>store.assessments(run.id),()=>assess(store,run,0,"suitable",0,"historical"),()=>assess(store,run,1,"insufficient",0,"new-on-stale")])assert.throws(action,hasError(409,"stale_run"));
  const current=store.recall(query);assert.notEqual(current.fingerprint,run.fingerprint);assert.equal(store.assessments(current.id).summary.reviewed,0);
  const db=new DatabaseSync(path,{readOnly:true});try{const row=db.prepare("SELECT label,revision FROM candidate_assessments WHERE dataset_id=? AND fingerprint=? AND creator_id=?").get("italy-test",run.fingerprint,creators[0].id);assert.equal(row.label,"suitable");assert.equal(row.revision,1);assert.equal(db.prepare("SELECT count(*) AS n FROM matching_requests").get().n,1);}finally{db.close();}
});

test("clock expiry makes assessments stale even without any source write",t=>{
  const {store,clock}=fixture(t);store.upsert({offers:[{id:"test-offer",productId:product.id,campaignId:"test-campaign",accountRef:"test-account",publicCommissionBps:null,totalCommissionBps:null,creatorCommissionBps:null,agencyCommissionBps:null,stock:null,sampleAvailable:null,sampleQuota:null,startsAt:NOW-1,endsAt:NOW+100,cardStatus:"unknown",source}]});
  const run=store.recall(query);assess(store,run,0,"insufficient");clock.at=NOW+100;
  assert.throws(()=>store.assessments(run.id),hasError(409,"stale_run"));assert.throws(()=>assess(store,run,1,"suitable"),hasError(409,"stale_run"));
  const current=store.recall(query);assert.equal(store.assessments(current.id).summary.reviewed,0);
});

test("other run fingerprints and datasets cannot inherit judgments for the same creator-product pair",t=>{
  const {store,run}=fixture(t);assess(store,run,0,"suitable");
  const reverse=store.recall({direction:"creator",subjectId:creators[0].id,source:"first",limit:50});assert.equal(reverse.candidates.length,1);assert.equal(store.assessments(reverse.id).summary.reviewed,0);
  const other=new MatchingStore(":memory:",{seed:false,now:()=>NOW,dataset:dataset("other-italy-test")});try{other.upsert(batch);const otherRun=other.recall(query);assert.notEqual(otherRun.fingerprint,run.fingerprint);assert.equal(other.assessments(otherRun.id).summary.reviewed,0);assert.throws(()=>other.assessments(run.id),hasError(404,"run_missing"));}finally{other.close();}
});

test("invalid inputs and noncandidate pairs do not create a judgment or consume a request receipt",t=>{
  const {store,run}=fixture(t);
  for(const call of [()=>assess(store,run,0,"maybe"),()=>assess(store,run,0,"suitable",-1),()=>assess(store,run,0,"suitable",0,"valid-request","x".repeat(1001)),()=>store.assessCandidate(run.id,creators[0].id,product.id,"suitable",null,0,"valid-request")])assert.throws(call,hasError(400,"invalid_fact"));
  assert.throws(()=>store.assessCandidate(run.id,creators[0].id,"outside-product","suitable","",0,"valid-request"),hasError(404,"candidate_missing"));
  assert.throws(()=>store.assessCandidate(run.id,"outside-creator",product.id,"suitable","",0,"valid-request"),hasError(404,"candidate_missing"));
  assert.equal(store.assessments(run.id).summary.reviewed,0);assert.equal(assess(store,run,0,"insufficient",0,"valid-request","x".repeat(1000)).items[0].revision,1);
});

test("empty results preserve unknown quality and synthetic datasets also support local assessments",t=>{
  const {store,run}=fixture(t,{includeCreators:false});assert.deepEqual(store.assessments(run.id),{runId:run.id,items:[],summary:{total:0,reviewed:0,suitable:0,unsuitable:0,insufficient:0,decided:0,suitabilityRate:null,coverage:0}});
  const synthetic=fixture(t,{mode:"synthetic-local"});assert.equal(assess(synthetic.store,synthetic.run,0,"unsuitable").summary.suitabilityRate,0);
});
