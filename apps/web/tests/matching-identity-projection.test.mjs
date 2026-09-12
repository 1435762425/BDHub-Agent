import test from "node:test";
import assert from "node:assert/strict";
import {DatabaseSync} from "node:sqlite";
import {createHash} from "node:crypto";
import {existsSync,mkdtempSync,readFileSync,rmSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {readRegistryProfileChanges,projectRegistryCreator} from "../src/server/matching/identity-profile-projection.ts";
import {PROFILE_FIELDS} from "../src/server/creator-identities/store.ts";
import {profileRankScope,orderProfileCandidates} from "../src/server/matching/profile-analysis.ts";

const SCHEMA=readFileSync(new URL("../../../scripts/lib/creator_identity.py",import.meta.url),"utf8").match(/_SCHEMA = """([\s\S]*?)"""/)[1];
const TIMES=["2026-09-01T00:00:00.000000Z","2026-09-10T00:00:00.000000Z","2026-09-11T00:00:00.000000Z","2026-09-12T00:00:00.000000Z"];
const IDS=["creator_"+"1".repeat(32),"creator_"+"2".repeat(32)];
const checkpoint=(value)=>({sourceKey:value?.sourceKey??null,cursor:value?.cursor??0,revision:0});
const value=(v)=>({status:v===0?"zero":"value",value:v});
const group=(name="Beauty & Personal Care",id="1")=>({id,label:name,weight:"0.8"});
function normalized(oec="111",overrides={}){
  const fields=Object.fromEntries(PROFILE_FIELDS.map(name=>[name,{status:"absent"}]));
  Object.assign(fields,{creator_oecuid:value(oec),selection_region:value("IT"),handle:value("current_name"),follower_cnt:value(100),units_sold:value(20),video_avg_view_cnt:value(30),
    med_gmv_revenue:value({decimal:"123.40",rawSymbol:"€"}),industry_groups:value([group()]),sales_performance_end_time:value(1788998400)},overrides);
  return {identity:{oecId:oec,market:"it",handle:fields.handle.value??null},fields,availableFields:Object.entries(fields).filter(([,f])=>f.status==="value"||f.status==="zero").map(([name])=>name)};
}
function fixture(t){
  const dir=mkdtempSync(join(tmpdir(),"identity-projection-")),path=join(dir,"identities.sqlite"),db=new DatabaseSync(path);db.exec(SCHEMA);
  t.after(()=>{db.close();rmSync(dir,{recursive:true,force:true});});
  const create=(oec="111",registryId=IDS[0],market="it",handle="current_name")=>db.prepare("INSERT INTO creator_identity VALUES(?,?,?,?,?,?,?,?,?,?)").run(registryId,market,oec,handle,TIMES[3],Date.parse(TIMES[3])*1000,0,TIMES[3],Date.parse(TIMES[3])*1000,TIMES[0]);
  let n=0;
  const observe=({oec="111",registryId=IDS[0],market="it",at=TIMES[1],ref="probe:profile",eventId=`event-${++n}`,payload=normalized(oec),kind="profile"}={})=>{
    db.prepare("INSERT INTO identity_observation VALUES(?,?,?,?,?,?,?,?,?,?,?,?)").run(eventId,registryId,market,oec,kind,kind==="profile"?"current_name":null,kind==="profile"?"observed":"unknown",at,
      Date.parse(at)*1000,ref,createHash("sha256").update(JSON.stringify(payload)).digest("hex"),JSON.stringify(payload));
  };
  create();return {dir,path,db,create,observe};
}
function existing(){
  const source={ref:"legacy:original-cohort",observedAt:Date.parse(TIMES[0]),windowStart:null,windowEnd:null};
  return {id:"matching-id-must-stay",market:"it",oecId:"111",name:"@old_name",avatar:"",categories:["家电"],formats:["video"],bio:"original",priceMinMinor:100,priceMaxMinor:300,currency:"EUR",
    control:"paused",marketingStopped:true,externalIdentity:{namespace:"kalodata",id:"777"},source,semanticRevision:8,relationRevision:3,
    categoryFact:{status:"historical",namespace:"it-history-top-label-v1",sourceLabels:["Household Appliances"],source,transformVersion:"original",timeBasis:"field_observation",note:"original category"},
    profileSignals:{followers:10,unitsSold:999,avgViews:888,gmvValue:"100.00",gmvCurrency:"EUR",periodLabel:"old",comparisonScope:"tiktok-profile:it:sales-end:1785196800",source}};
}
function projectedRecord(t,overrides={}){
  const f=fixture(t);f.observe({payload:normalized("111",overrides)});return {f,record:readRegistryProfileChanges(f.path,checkpoint()).records[0]};
}

test("reader is read-only, bounded by rowid, and late snapshots never replace a newer bundle",t=>{
  const f=fixture(t);f.observe({at:TIMES[1],payload:normalized("111",{units_sold:value(10)})});
  f.observe({at:TIMES[2],payload:normalized("111",{units_sold:value(40)})});
  f.observe({at:TIMES[0],payload:normalized("111",{units_sold:value(900)})});
  const bytes=readFileSync(f.path),first=readRegistryProfileChanges(f.path,checkpoint(),1);
  assert.equal(first.events,1);assert.equal(first.cursor,1);assert.equal(first.sourceHead,3);assert.equal(first.registryCreators,1);
  assert.equal(first.records[0].observation.fields.units_sold.value,40);
  const second=readRegistryProfileChanges(f.path,checkpoint(first),2);assert.equal(second.events,2);assert.equal(second.cursor,3);
  assert.equal(second.records[0].observation.fields.units_sold.value,40);
  assert.equal(readRegistryProfileChanges(f.path,checkpoint(second)).records.length,0);
  assert.deepEqual(readFileSync(f.path),bytes);
});
test("same-time complete Profile beats a later Find UUID and same-quality ties use rowid",t=>{
  const f=fixture(t);f.observe({eventId:"a-full",ref:"probe:profile",payload:normalized("111",{units_sold:value(40)})});
  f.observe({eventId:"z-find",ref:"probe:find-profile",payload:normalized("111",{units_sold:value(2)})});
  let read=readRegistryProfileChanges(f.path,checkpoint());assert.equal(read.records[0].observation.eventId,"a-full");
  f.observe({eventId:"0-later-full",ref:"other:profile",payload:normalized("111",{units_sold:value(70)})});
  read=readRegistryProfileChanges(f.path,checkpoint(read));assert.equal(read.records[0].observation.eventId,"0-later-full");
});
test("failure and exact-Find marker events advance cursor without overwriting normalized fields",t=>{
  const f=fixture(t);f.observe();const first=readRegistryProfileChanges(f.path,checkpoint());
  f.observe({at:TIMES[2],ref:"find-proof",payload:{exactFind:{oecId:"111"}}});
  f.observe({at:TIMES[3],ref:"failure",kind:"failure",payload:{}});
  const next=readRegistryProfileChanges(f.path,checkpoint(first));assert.equal(next.events,2);assert.equal(next.records[0].observation.eventId,first.records[0].observation.eventId);
  assert.equal(next.records[0].observation.observedAt,Date.parse(TIMES[1]));
});
test("other-market rows advance the global watermark but are not projected into Italy",t=>{
  const f=fixture(t);f.create("222",IDS[1],"mx");f.observe({market:"mx",oec:"222",registryId:IDS[1]});
  const read=readRegistryProfileChanges(f.path,checkpoint());assert.equal(read.events,1);assert.equal(read.cursor,1);assert.equal(read.registryCreators,1);assert.deepEqual(read.records,[]);
});
test("missing DB is not_imported, empty source can adopt its first event, and rewinds/source replacement fail",t=>{
  const f=fixture(t),missing=join(f.dir,"does-not-exist.sqlite");assert.equal(readRegistryProfileChanges(missing,checkpoint()).status,"not_imported");assert.equal(existsSync(missing),false);
  const empty=readRegistryProfileChanges(f.path,checkpoint());f.observe();const first=readRegistryProfileChanges(f.path,checkpoint(empty));assert.equal(first.cursor,1);
  assert.throws(()=>readRegistryProfileChanges(f.path,{...checkpoint(first),cursor:2}),e=>e.code==="registry_cursor_rewound");
  f.db.exec("DROP TRIGGER identity_observation_no_update;UPDATE identity_observation SET event_id='replacement-first-event'");
  assert.throws(()=>readRegistryProfileChanges(f.path,checkpoint(first)),e=>e.code==="registry_source_changed");
});
test("invalid source schema, checkpoint, or normalized identity cannot quietly enter matching",t=>{
  const f=fixture(t);f.observe({payload:normalized("222")});assert.throws(()=>readRegistryProfileChanges(f.path,checkpoint()),e=>e.code==="registry_profile_invalid");
  assert.throws(()=>readRegistryProfileChanges(f.path,{sourceKey:null,cursor:1,revision:0}),e=>e.code==="invalid_registry_checkpoint");
  f.db.exec("PRAGMA ignore_check_constraints=ON;UPDATE identity_store_meta SET version=2");
  assert.throws(()=>readRegistryProfileChanges(f.path,checkpoint()),e=>e.code==="registry_schema_mismatch");
});
test("existing creator keeps matching identity, relation control, source, and commercial/context fields",t=>{
  const {record}=projectedRecord(t),old=existing(),copy=structuredClone(old),result=projectRegistryCreator(record,old),next=result.creator;
  assert.deepEqual(old,copy);assert.equal(next.id,old.id);assert.equal(next.name,"@current_name");assert.equal(next.control,"paused");assert.equal(next.marketingStopped,true);
  for(const key of ["source","externalIdentity","formats","bio","priceMinMinor","priceMaxMinor","currency"])assert.deepEqual(next[key],old[key]);
  assert.equal(next.semanticRevision,undefined);assert.equal(next.relationRevision,undefined);assert.deepEqual(next.categories,["美妆个护"]);
  assert.equal(next.categoryFact.status,"observed");assert.equal(next.categoryFact.namespace,"it-history-top-label-v1");
  assert.equal(next.profileOrigin.creatorId,IDS[0]);assert.equal(next.profileOrigin.categoryMode,"observed");assert.equal(next.profileOrigin.observedAt,Date.parse(TIMES[1]));
  assert.deepEqual(result.mapping,{registryCreatorId:IDS[0],market:"it",oecId:"111",matchingCreatorId:old.id});
});
test("new exact-OEC creator has a deterministic matching ID and no invented control or contact permission",t=>{
  const {record}=projectedRecord(t),result=projectRegistryCreator(record,null);
  assert.equal(result.creator.id,"it-profile-oec-111");assert.equal(result.creator.control,"unknown");assert.equal(result.creator.marketingStopped,null);assert.equal(result.creator.avatar,"");
  assert.equal(result.creator.source.observedAt,record.observation.observedAt);assert.equal(result.creator.name,"@current_name");assert.equal(result.skipped,0);
  assert.deepEqual(result.gaps,{categoryUnavailable:0,categoryHistorical:0,metricsPartial:0,periodUnknown:0});
});
test("latest metrics stay one bundle while absent categories may use separately dated history",t=>{
  const f=fixture(t);f.observe({at:TIMES[1],payload:normalized("111",{industry_groups:value([group("Household Appliances")]),video_avg_view_cnt:value(999)})});
  f.observe({at:TIMES[2],payload:normalized("111",{industry_groups:{status:"no_value"},video_avg_view_cnt:{status:"no_value"},sales_performance_end_time:value(1789084800)})});
  const read=readRegistryProfileChanges(f.path,checkpoint()),result=projectRegistryCreator(read.records[0],existing());
  assert.deepEqual(result.creator.categories,["家电"]);assert.equal(result.creator.categoryFact.status,"historical");assert.equal(result.creator.categoryFact.source.observedAt,Date.parse(TIMES[1]));
  assert.equal(result.creator.profileSignals.avgViews,null);assert.equal(result.creator.profileSignals.unitsSold,20);assert.equal(result.creator.profileSignals.source.observedAt,Date.parse(TIMES[2]));
  assert.equal(result.creator.profileOrigin.metricStates.avgViews,"no_value");assert.equal(result.creator.profileSignals.comparisonScope,"tiktok-profile:it:sales-end:1789084800");
  assert.equal(result.gaps.categoryHistorical,1);assert.equal(result.gaps.metricsPartial,1);
});
test("baseline category fallback keeps the original source rather than stamping it current",t=>{
  const {record}=projectedRecord(t,{industry_groups:{status:"absent"}}),old=existing(),result=projectRegistryCreator(record,old);
  assert.deepEqual(result.creator.categories,old.categories);assert.deepEqual(result.creator.categoryFact.source,old.categoryFact.source);
  assert.equal(result.creator.profileOrigin.categoryMode,"historical_fallback");assert.equal(result.creator.profileOrigin.observedAt,Date.parse(TIMES[1]));
});
test("unauthorized/error categories cannot recover old values; explicit old conflicts remain conflicts",t=>{
  for(const status of ["unauthorized","error"]){
    const {record}=projectedRecord(t,{industry_groups:{status,value:[group("Household Appliances")]}}),old=existing(),result=projectRegistryCreator(record,old);
    assert.deepEqual(result.creator.categories,[]);assert.equal(result.creator.categoryFact.status,"missing");assert.equal(result.gaps.categoryHistorical,0);
  }
  const {record}=projectedRecord(t),old=existing();old.categories=[];old.categoryFact.status="conflict";old.categoryFact.note="operator conflict must remain";
  const result=projectRegistryCreator(record,old);assert.deepEqual(result.creator.categoryFact,old.categoryFact);assert.equal(result.creator.profileOrigin.categoryMode,"conflict_preserved");
});
test("Pre-Owned remains a raw source label and never becomes an invented index category",t=>{
  const {record}=projectedRecord(t,{industry_groups:value([group("Pre-Owned","2"),group(),{id:"-1",weight:"0.2"}])}),result=projectRegistryCreator(record,null);
  assert.deepEqual(result.creator.categories,["美妆个护"]);assert.deepEqual(result.creator.categoryFact.sourceLabels,["Pre-Owned","Beauty & Personal Care"]);
  assert(result.creator.categoryFact.note.includes("Pre-Owned"));assert(!result.creator.categories.includes("二手"));
});
test("known zero stays zero, unavailable values stay null, and unknown currency is not inferred",t=>{
  const {record}=projectedRecord(t,{units_sold:value(0),video_avg_view_cnt:{status:"unauthorized",value:999},med_gmv_revenue:value({decimal:"10.00"})});
  const result=projectRegistryCreator(record,null);assert.equal(result.creator.profileSignals.unitsSold,0);assert.equal(result.creator.profileSignals.avgViews,null);
  assert.equal(result.creator.profileSignals.gmvValue,null);assert.equal(result.creator.profileSignals.gmvCurrency,null);assert.equal(result.creator.profileOrigin.metricStates.unitsSold,"zero");
  assert.equal(result.creator.profileOrigin.metricStates.avgViews,"unauthorized");assert.equal(result.creator.profileOrigin.metricStates.gmvValue,"error");
});
test("different reporting endpoints and unknown periods have distinct rank scopes",t=>{
  const {record}=projectedRecord(t),a=projectRegistryCreator(record,null).creator,bRecord=structuredClone(record);
  bRecord.oecId="222";bRecord.registryCreatorId=IDS[1];bRecord.observation.fields.creator_oecuid=value("222");bRecord.historicalCategory=null;bRecord.observation.fields.sales_performance_end_time=value(1789084800);
  const b=projectRegistryCreator(bRecord,null).creator;assert.notEqual(profileRankScope(a),profileRankScope(b));
  record.observation.fields.sales_performance_end_time={status:"absent"};bRecord.observation.fields.sales_performance_end_time={status:"no_value"};
  const unknownA=projectRegistryCreator(record,null),unknownB=projectRegistryCreator(bRecord,null);assert.equal(unknownA.gaps.periodUnknown,1);
  assert.notEqual(profileRankScope(unknownA.creator),profileRankScope(unknownB.creator));assert.equal(unknownA.creator.profileSignals.periodLabel,null);
  const candidate=creator=>({creator,product:{id:"p"},sources:["category"],features:{categoryOverlap:1}});
  assert.equal(orderProfileCandidates([candidate(a),candidate(b)]).length,2);
});
test("identity-only records without normalized fields are skipped rather than overwriting a baseline",t=>{
  const f=fixture(t);f.observe({payload:{exactFind:{oecId:"111"}}});const read=readRegistryProfileChanges(f.path,checkpoint());
  assert.equal(read.records[0].observation,null);const projected=projectRegistryCreator(read.records[0],existing());assert.equal(projected.creator,null);assert.equal(projected.mapping,null);assert.equal(projected.skipped,1);
});
