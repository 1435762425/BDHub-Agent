import test from "node:test";
import assert from "node:assert/strict";
import { DatabaseSync } from "node:sqlite";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { MatchingStore, MatchingError } from "../src/server/matching/store.ts";

const NOW=1_900_000_000_000;
const dataset={id:"italy-offline-test",mode:"imported-offline",label:"意大利历史测试",importedAt:NOW,sourceRefs:["legacy:test/snapshot"],warnings:["窗口只覆盖有限历史样本"]};
const source={ref:"legacy:test/snapshot",observedAt:NOW-1000,windowStart:NOW-86400000,windowEnd:NOW-1000};
const product=(extra={})=>({id:"it-product-1",market:"it",pid:"1729779362302171335",title:"历史商品",image:"",categories:["home"],formats:["video"],description:"",priceMinor:1000,currency:"EUR",source,...extra});
const creator=(extra={})=>({id:"it-creator-1",market:"it",oecId:null,externalIdentity:{namespace:"kalodata",id:"99887766554433221100112233"},name:"历史达人",avatar:"",categories:["home"],formats:["video"],bio:"",priceMinMinor:500,priceMaxMinor:2000,currency:"EUR",control:"unknown",marketingStopped:null,source,...extra});
const evidence=(p,c)=>({id:"it-evidence-1",creatorId:c.id,market:"it",pid:p.pid,units:4,format:"video",source});
const offer=p=>({id:"it-offer-1",productId:p.id,campaignId:"campaign-1",accountRef:"account-1",publicCommissionBps:800,totalCommissionBps:1500,creatorCommissionBps:1200,agencyCommissionBps:300,stock:20,sampleAvailable:false,sampleQuota:0,startsAt:NOW-1000,endsAt:NOW+10000,cardStatus:"verified",source});
function fixture(t,options={}){const store=new MatchingStore(":memory:",{dataset,now:()=>NOW,...options});t.after(()=>store.close());return store;}
function recall(store,p){return store.recall({direction:"product",subjectId:p.id,source:"second",limit:50});}
function seed(store,c=creator(),p=product()){store.upsert({products:[p],creators:[c],evidence:[evidence(p,c)]});return {p,c};}

test("offline unknown identity and relationship facts persist without becoming OEC or permission",t=>{
  const dir=mkdtempSync(join(tmpdir(),"matching-offline-")),path=join(dir,"matching.sqlite");
  let store=new MatchingStore(path,{dataset,seed:true,now:()=>NOW});
  t.after(()=>{store.close();rmSync(dir,{recursive:true,force:true});});
  assert.equal(store.stats().products,0);assert.equal(store.stats().creators,0);
  const {p,c}=seed(store);const first=recall(store,p);const firstPacket=store.prepareReview(first.id,c.id);
  store.close();store=new MatchingStore(path,{now:()=>NOW});
  const actual=store.listCreators().items[0];
  assert.equal(actual.oecId,null);assert.equal(actual.externalIdentity.id,c.externalIdentity.id);
  assert.equal(actual.control,"unknown");assert.equal(actual.marketingStopped,null);
  assert.deepEqual(store.stats().dataset,dataset);assert.equal(store.stats().mode,"imported-offline");
  assert.equal(store.listCreators({q:c.externalIdentity.id}).total,1);
  const raw=new DatabaseSync(path,{readOnly:true});
  try{const row=raw.prepare("SELECT oec_id,external_namespace,external_id FROM creators").get();assert.equal(row.oec_id,null);assert.equal(row.external_namespace,"kalodata");assert.equal(row.external_id,c.externalIdentity.id);}finally{raw.close();}
  const run=recall(store,p),candidate=run.candidates[0],packet=store.prepareReview(run.id,c.id);
  assert.equal(run.id,first.id);assert.equal(packet.id,firstPacket.id);
  assert.equal(candidate.readiness,"needs_facts");
  for(const text of ["历史快照","OEC 身份未知","人工接管／暂停状态未知","拒联状态未知"])assert(candidate.gaps.some(gap=>gap.includes(text)));
  assert.equal(packet.executable,false);assert.equal(packet.executionBlocked,true);assert.equal(packet.modelStatus,"not_called");
  assert.equal(packet.payload.mode,"imported-offline");assert.equal(packet.payload.executionBlocked,true);
  assert.deepEqual(packet.payload.datasetWarnings,dataset.warnings);assert.equal(packet.payload.datasetImportedAt,NOW);
  assert.equal(packet.payload.creator.oecId,null);assert.equal(packet.payload.creator.marketingStopped,null);
  assert.equal(packet.characters,JSON.stringify(packet.payload).length);assert(packet.characters<=6000);
  assert(packet.payload.candidates[0].gaps.some(gap=>gap.includes("OEC 身份未知")));
  assert.equal(store.stats().llmCalls,0);assert.equal(store.stats().billedTokens,0);
});

test("dataset warning order preserves the dated source warning before evaluation limitations",t=>{
  const warnings=["历史快照：2026-09-10","有限样本","历史快照：2026-09-10","未知身份"];
  const store=fixture(t,{dataset:{...dataset,warnings}}),{p,c}=seed(store);
  assert.deepEqual(store.stats().dataset.warnings,[warnings[0],warnings[1],warnings[3]]);
  const packet=store.prepareReview(recall(store,p).id,c.id);
  assert.deepEqual(packet.payload.datasetWarnings,warnings.slice(0,2));assert.equal(packet.payload.omittedDatasetWarnings,1);
});

test("external identity namespace and precision are enforced and never aliased to OEC",t=>{
  const store=fixture(t),c=creator();
  for(const change of [
    {externalIdentity:undefined},{externalIdentity:null},
    {externalIdentity:{namespace:"oec",id:c.externalIdentity.id}},
    {externalIdentity:{namespace:"kalodata",id:99887766554433221100112233}},
    {externalIdentity:{namespace:"kalodata",id:"creator-123"}},
    {oecId:99887766554433221100112233},
    {avatar:"https://example.invalid/avatar.jpg"},
  ])assert.throws(()=>store.upsert({creators:[{...c,...change}]}),error=>error instanceof MatchingError&&error.code==="invalid_fact");
  store.upsert({creators:[c]});
  assert.throws(()=>store.upsert({creators:[{...c,id:"duplicate-external"}]}),error=>error.code==="identity_conflict");
  assert.throws(()=>store.upsert({creators:[{...c,externalIdentity:{namespace:"kalodata",id:"111"}}]}),error=>error.code==="identity_conflict");
  assert.throws(()=>store.upsert({creators:[{...c,oecId:"123"}]}),error=>error.code==="identity_conflict");
  // Equal digits in separate proven namespaces are not automatically the same identity.
  store.upsert({creators:[{...c,id:"known-oec",oecId:c.externalIdentity.id,externalIdentity:undefined},{...c,id:"br-external",market:"br",currency:"BRL"}]});
  assert.equal(store.stats().creators,3);assert.equal(store.listCreators({q:c.externalIdentity.id,market:"it"}).total,2);
});

test("offline dataset provenance is immutable and cannot be relabeled or seeded",t=>{
  const dir=mkdtempSync(join(tmpdir(),"matching-provenance-")),path=join(dir,"matching.sqlite");
  t.after(()=>rmSync(dir,{recursive:true,force:true}));
  let store=new MatchingStore(path,{dataset,seed:true});assert.equal(store.stats().products,0);
  const view=store.stats();view.dataset.mode="synthetic-local";view.dataset.sourceRefs.push("mutated");
  assert.deepEqual(store.stats().dataset,dataset);store.close();
  assert.throws(()=>new MatchingStore(path,{dataset:{...dataset,mode:"synthetic-local"}}),error=>error.code==="dataset_conflict");
  assert.throws(()=>new MatchingStore(path,{dataset:{...dataset,sourceRefs:["replacement"]}}),error=>error.code==="dataset_conflict");
  store=new MatchingStore(path);assert.deepEqual(store.stats().dataset,dataset);assert.equal(store.stats().products,0);store.close();
  assert.throws(()=>new MatchingStore(":memory:",{dataset:{...dataset,sourceRefs:[]}}),error=>error.code==="invalid_fact");
  assert.throws(()=>new MatchingStore(":memory:",{dataset:{...dataset,importedAt:null}}),error=>error.code==="invalid_fact");
});

test("offline mode rejects synthetic mutation even for synthetic-prefixed records",t=>{
  const store=fixture(t),p=product({id:"synthetic-product-1",source:{...source,ref:"synthetic:product/1"}});
  store.upsert({products:[p]});const before=store.listProducts().items[0];
  assert.throws(()=>store.demoChange(p.id,before.commercialRevision,"raise_price","request-1"),error=>error.status===403&&error.code==="synthetic_only");
  assert.deepEqual(store.listProducts().items[0],before);
});

test("historical offline candidates never become reviewable even with complete-looking offers",t=>{
  const store=fixture(t),{p,c}=seed(store,creator({oecId:"112233445566778899",control:"auto",marketingStopped:false}));
  store.upsert({offers:[offer(p)]});const run=recall(store,p);
  assert.equal(run.candidates[0].readiness,"needs_facts");
  assert(run.candidates[0].gaps.some(gap=>gap.includes("未核验为当前可执行事实")));
  assert.equal(store.prepareReview(run.id,c.id).executionBlocked,true);
});

test("known human, paused and refusal remain blocked in historical offline mode",t=>{
  const store=fixture(t),{p,c}=seed(store);
  for(const state of [{control:"human"},{control:"paused"},{marketingStopped:true}]){
    store.upsert({creators:[{...c,...state}]});const run=recall(store,p);
    assert.equal(run.candidates[0].readiness,"suppressed");
    assert.throws(()=>store.prepareReview(run.id,c.id),error=>error.code==="relationship_suppressed");
  }
});

test("synthetic mode cannot use unknown identity or control as permission",t=>{
  const store=fixture(t,{dataset:undefined,seed:false}),{p,c}=seed(store,creator({oecId:"111222333",control:"auto",marketingStopped:false}));
  for(const state of [{oecId:c.oecId,control:"unknown"},{oecId:c.oecId,marketingStopped:null}]){
    store.upsert({creators:[{...c,...state}]});const run=recall(store,p);
    assert.equal(run.candidates[0].readiness,"suppressed");assert.throws(()=>store.prepareReview(run.id,c.id),error=>error.code==="relationship_suppressed");
  }
});

test("schema v1 migration preserves synthetic facts, child references and stored packets",t=>{
  const dir=mkdtempSync(join(tmpdir(),"matching-v1-")),path=join(dir,"matching.sqlite");
  t.after(()=>rmSync(dir,{recursive:true,force:true}));
  const c={...creator({oecId:"987654321012345678987654321",externalIdentity:undefined,control:"auto",marketingStopped:false}),semanticRevision:7,relationRevision:3};
  const oldPacket={id:"old-packet",executable:false,payload:{mode:"synthetic-local"}};
  const old=new DatabaseSync(path);
  old.exec(`CREATE TABLE matching_meta(key TEXT PRIMARY KEY,value INTEGER NOT NULL);
    INSERT INTO matching_meta VALUES('schema_version',1),('semantic_builds',7);
    CREATE TABLE creators(id TEXT PRIMARY KEY,market TEXT NOT NULL,oec_id TEXT NOT NULL,price_min INTEGER,price_max INTEGER,name TEXT NOT NULL,semantic_hash TEXT NOT NULL,relation_hash TEXT NOT NULL,data TEXT NOT NULL,UNIQUE(market,oec_id));
    CREATE TABLE creator_categories(market TEXT NOT NULL,category TEXT NOT NULL,entity_id TEXT NOT NULL REFERENCES creators(id),price_min INTEGER,price_max INTEGER,PRIMARY KEY(market,category,entity_id));
    CREATE TABLE evidence(id TEXT PRIMARY KEY,creator_id TEXT NOT NULL REFERENCES creators(id),market TEXT NOT NULL,pid TEXT NOT NULL,units INTEGER NOT NULL,data TEXT NOT NULL);
    CREATE TABLE review_packets(id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL UNIQUE,data TEXT NOT NULL);
    CREATE TABLE recall_runs(id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL UNIQUE,data TEXT NOT NULL);`);
  old.prepare("INSERT INTO creators VALUES(?,?,?,?,?,?,?,?,?)").run(c.id,c.market,c.oecId,c.priceMinMinor,c.priceMaxMinor,c.name,"old-semantic","old-relation",JSON.stringify(c));
  old.prepare("INSERT INTO creator_categories VALUES(?,?,?,?,?)").run("it","home",c.id,500,2000);
  const e=evidence(product(),c);old.prepare("INSERT INTO evidence VALUES(?,?,?,?,?,?)").run(e.id,c.id,"it",e.pid,e.units,JSON.stringify(e));
  old.prepare("INSERT INTO review_packets VALUES(?,?,?)").run(oldPacket.id,"old-fingerprint",JSON.stringify(oldPacket));
  old.prepare("INSERT INTO recall_runs VALUES(?,?,?)").run("old-run","old-run-fingerprint",JSON.stringify({run:{id:"old-run",fingerprint:"old-run-fingerprint",matchingVersion:"structured-recall-v1",query:{direction:"creator",subjectId:c.id,source:"second",limit:50}},dependencies:[],expiresAt:null}));old.close();
  const store=new MatchingStore(path,{seed:false,now:()=>NOW});
  try{
    assert.deepEqual(store.listCreators().items[0],JSON.parse(JSON.stringify(c)));
    assert.equal(store.stats().semanticBuilds,7);assert.equal(store.stats().packets,1);assert.equal(store.stats().evidence,1);
    assert.equal(store.stats().mode,"synthetic-local");
    assert.equal(store.stats().matchingVersion,"structured-recall-v2");
    assert.throws(()=>store.prepareReview("old-run",c.id),error=>error.code==="stale_run");
    store.upsert({products:[product()]});assert.equal(recall(store,product()).candidates[0].creator.oecId,c.oecId);
    // Fresh nullable external identities fit the migrated schema without changing old OEC records.
    store.upsert({creators:[creator({id:"new-external"})]});assert.equal(store.stats().creators,2);
    const inspect=new DatabaseSync(path,{readOnly:true});
    try{assert.equal(inspect.prepare("SELECT value FROM matching_meta WHERE key='schema_version'").get().value,2);assert.deepEqual(inspect.prepare("PRAGMA foreign_key_check").all(),[]);assert.deepEqual(JSON.parse(inspect.prepare("SELECT data FROM review_packets WHERE id=?").get(oldPacket.id).data),oldPacket);}finally{inspect.close();}
  }finally{store.close();}
  assert.throws(()=>new MatchingStore(path,{dataset}),error=>error.code==="dataset_conflict");
});
