import test from "node:test";
import assert from "node:assert/strict";
import {mkdtempSync,rmSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {MatchingStore} from "../src/server/matching/store.ts";

const NOW=1_900_000_000_000,REGISTRY="creator_"+"a".repeat(32),OTHER="creator_"+"b".repeat(32);
const source=(ref,at=NOW)=>({ref,observedAt:at,windowStart:null,windowEnd:null});
const dataset={id:"italy-profiles-sync-test",mode:"imported-offline",label:"同步测试",importedAt:NOW,sourceRefs:["baseline:fixture"],warnings:[]};
const category=(categories,status="historical",at=NOW)=>({status,namespace:"it-test",sourceLabels:categories,source:source("category:fixture",at),transformVersion:"it-test",timeBasis:"field_observation",note:"可复核类目"});
const product=(id="p1",categories=["home"])=>({id,market:"it",pid:id==="p1"?"111":"222",title:id,image:"",description:"",categories,categoryFact:category(categories),formats:[],priceMinor:null,currency:"EUR",source:source("product:fixture")});
const creator=(id="c1",oec="123",categories=["home"])=>({id,market:"it",oecId:oec,name:"@old_handle",avatar:"",categories,categoryFact:category(categories),formats:[],bio:"原始身份来源",priceMinMinor:null,priceMaxMinor:null,currency:"EUR",control:"unknown",marketingStopped:null,source:source("baseline:creator"),profileSignals:{followers:10,unitsSold:3,avgViews:40,gmvValue:"20.00",gmvCurrency:"EUR",periodLabel:null,comparisonScope:"same-profile-scope",source:source("profile:baseline")}});
function projected(c=creator(),registryId=REGISTRY,at=NOW+1000){return {...c,name:"@new_handle",categoryFact:category(c.categories,"observed",at),profileSignals:{...c.profileSignals,unitsSold:30,source:source("profile:updated",at)},profileOrigin:{kind:"identity_registry",creatorId:registryId,observationRef:"identity:observation",observedAt:at,categoryMode:"observed",metricStates:{followers:"value",unitsSold:"value",avgViews:"value",gmvValue:"value"}}};}
function input(store,creators=[projected()],extra={}){const checkpoint=store.profileSyncCheckpoint();return {expectedRevision:checkpoint.revision,expectedCursor:checkpoint.cursor,sourceKey:"identity-db-generation-1",cursor:checkpoint.cursor+1,registryCreators:creators.length,events:1,creators,mappings:creators.map(c=>({registryCreatorId:c.profileOrigin.creatorId,market:"it",oecId:c.oecId,matchingCreatorId:c.id})),skipped:0,gaps:{categoryUnavailable:0,categoryHistorical:0,metricsPartial:0,periodUnknown:creators.length},...extra};}
function fixture(t){const store=new MatchingStore(":memory:",{dataset,seed:false,now:()=>NOW+10000});t.after(()=>store.close());return store;}
const recall=(store,p)=>store.recall({direction:"product",subjectId:p.id,source:"first",limit:20});
const raw=(store,table,id)=>store.db.prepare(`SELECT data FROM ${table} WHERE id=?`).get(id)?.data;

test("pre-sync stats stay unchanged; observed categories and origin become durable matching facts",t=>{
  const store=fixture(t);assert.equal(store.stats().profileSync,undefined);assert.deepEqual(store.profileSyncCheckpoint(),{sourceKey:null,cursor:0,revision:0});
  assert.equal(store.db.prepare("SELECT count(*) n FROM sqlite_master WHERE name LIKE 'matching_profile_%'").get().n,0);
  const p=product();store.upsert({products:[p]});const result=store.syncProfiles(input(store));
  assert.equal(result.result.inserted,1);assert.deepEqual(result.changedCreatorIds,["c1"]);assert.equal(result.status.revision,1);assert.equal(result.status.syncedCreators,1);
  assert.equal(store.getCreatorByOec("it","123").categoryFact.status,"observed");assert.equal(store.resolveRegistryCreator(REGISTRY).id,"c1");assert.equal(store.getCreatorByOec("mx","123"),null);
  assert.equal(recall(store,p).candidates.length,1);assert.equal(store.stats().profileSync.sourceCursor,1);assert.deepEqual(store.stats().dataset,dataset);
});
test("new candidates outside previous output invalidate only dependent cached queries",t=>{
  const store=fixture(t),home=product(),pets=product("p2",["pets"]);store.upsert({products:[home,pets]});
  const empty=recall(store,home),unrelated=recall(store,pets);assert.equal(empty.candidates.length,0);
  const result=store.syncProfiles(input(store));assert.deepEqual(result.affectedRunIds,[empty.id]);assert.equal(result.status.pendingRecomputes,1);
  const drained=store.drainProfileRecomputes();assert.equal(drained.replacements.length,1);assert.equal(drained.status.pendingRecomputes,0);assert.equal(store.getRun(drained.replacements[0].newRunId).candidates[0].creator.id,"c1");
  assert.equal(store.profileRunReplacement(empty.id),drained.replacements[0].newRunId);assert.equal(store.getRun(unrelated.id).id,unrelated.id);assert.equal(recall(store,pets).id,unrelated.id);
});
test("old run, packet, annotations and request receipts remain immutable and stale operations are refused",t=>{
  const store=fixture(t),p=product(),c=creator();store.upsert({products:[p],creators:[c]});const old=recall(store,p),packet=store.prepareReview(old.id,c.id);
  store.assessCandidate(old.id,c.id,p.id,"suitable","原版本人工判断",0,"manual-before-sync");
  const beforeRun=raw(store,"recall_runs",old.id),beforePacket=raw(store,"review_packets",packet.id),beforeReceipt=raw(store,"matching_requests","manual-before-sync");
  store.syncProfiles(input(store));const replacement=store.drainProfileRecomputes().replacements[0].newRunId;
  assert.equal(raw(store,"recall_runs",old.id),beforeRun);assert.equal(raw(store,"review_packets",packet.id),beforePacket);assert.equal(raw(store,"matching_requests","manual-before-sync"),beforeReceipt);
  for(const operation of [()=>store.getRun(old.id),()=>store.assessments(old.id),()=>store.prepareReview(old.id,c.id),()=>store.assessCandidate(old.id,c.id,p.id,"suitable","原版本人工判断",0,"manual-before-sync")])assert.throws(operation,error=>error.code==="stale_run");
  assert.equal(store.assessments(replacement).summary.reviewed,0);assert.equal(store.db.prepare("SELECT label,note FROM candidate_assessments WHERE fingerprint=?").get(old.fingerprint).note,"原版本人工判断");
  const nextPacket=store.prepareReview(replacement,c.id);assert.equal(nextPacket.payload.creator.identityRegistryId,REGISTRY);assert.equal(packet.payload.creator.identityRegistryId,undefined);assert(nextPacket.characters<=6000);
});
test("sync rereads relationship controls and preserves original identity provenance and conflicts",t=>{
  const store=fixture(t),old={...creator(),categories:[],categoryFact:category(["conflicting"],"conflict"),externalIdentity:{namespace:"kalodata",id:"987"}};store.upsert({creators:[old]});
  const staleProjection=projected({...old,categories:["home"],control:"auto",marketingStopped:false});
  store.upsert({creators:[{...old,control:"paused",marketingStopped:true}]});staleProjection.source=source("must-not-replace-source",NOW+1000);delete staleProjection.externalIdentity;
  const result=store.syncProfiles(input(store,[staleProjection])),current=store.getCreatorByOec("it","123");
  assert.equal(current.id,old.id);assert.equal(current.control,"paused");assert.equal(current.marketingStopped,true);assert.deepEqual(current.source,old.source);assert.deepEqual(current.externalIdentity,old.externalIdentity);
  assert.equal(current.categoryFact.status,"conflict");assert.deepEqual(current.categories,[]);assert.equal(current.profileOrigin.categoryMode,"conflict_preserved");assert.equal(result.result.relationChanges,0);
});
test("checkpoint conflicts, source replacement and rollback cannot partly advance facts or mapping",t=>{
  const store=fixture(t);store.upsert({creators:[creator()]});const first=input(store);store.syncProfiles(first);
  assert.throws(()=>store.syncProfiles(first),error=>error.code==="profile_sync_conflict");
  assert.throws(()=>store.syncProfiles(input(store,[],{sourceKey:"another-database"})),error=>error.code==="profile_sync_source_conflict");
  assert.throws(()=>store.syncProfiles(input(store,[],{cursor:0})),error=>error.code==="profile_sync_cursor_conflict");
  const before=store.profileSyncCheckpoint(),newCreator=projected(creator("c2","456"),OTHER),older=projected(creator(),REGISTRY,NOW);
  assert.throws(()=>store.syncProfiles(input(store,[newCreator,older])),error=>error.code==="older_observation");assert.deepEqual(store.profileSyncCheckpoint(),before);assert.equal(store.getCreatorByOec("it","456"),null);assert.equal(store.resolveRegistryCreator(OTHER),null);
});
test("same OEC and registry mapping are exact and cannot be reassigned",t=>{
  const store=fixture(t);store.upsert({creators:[creator()]});
  const mismatch=input(store);mismatch.mappings[0].oecId="999";assert.throws(()=>store.syncProfiles(mismatch),error=>error.code==="profile_sync_identity_conflict");
  const changedId=projected({...creator(),id:"different-matching-id"});assert.throws(()=>store.syncProfiles(input(store,[changedId])),error=>error.code==="profile_sync_identity_conflict");
  store.syncProfiles(input(store));const changedRegistry=projected(creator(),OTHER,NOW+2000);assert.throws(()=>store.syncProfiles(input(store,[changedRegistry])),error=>error.code==="profile_sync_identity_conflict");
  assert.equal(store.resolveRegistryCreator(REGISTRY).oecId,"123");assert.equal(store.resolveRegistryCreator(OTHER),null);
});
test("same cursor no-op does not create repeated revisions, builds, or recomputes",t=>{
  const store=fixture(t),c=projected();const applied=input(store,[c]);store.syncProfiles(applied);const before=store.stats();
  const replay={...applied,...{expectedRevision:1,expectedCursor:1}};assert.equal(store.syncProfiles(replay).status.revision,1);
  assert.equal(store.syncProfiles(input(store,[],{cursor:1,events:0})).status.revision,1);assert.deepEqual(store.stats(),before);
  const conflicting={...replay,creators:[{...c,name:"different-data-at-same-cursor"}]};assert.throws(()=>store.syncProfiles(conflicting),error=>error.code==="profile_sync_cursor_conflict");
  const progressed=store.syncProfiles(input(store,[c]));assert.equal(progressed.status.revision,2);assert.equal(progressed.result.unchanged,1);assert.deepEqual(progressed.changedCreatorIds,[]);assert.deepEqual(progressed.affectedRunIds,[]);
});
test("only latest query version is scheduled and replacement chain survives successive syncs",t=>{
  const store=fixture(t),p=product(),c=creator();store.upsert({products:[p],creators:[c]});const first=recall(store,p);
  store.upsert({creators:[{...c,name:"@renamed_before_sync"}]});const latest=recall(store,p);
  const result=store.syncProfiles(input(store));assert.deepEqual(result.affectedRunIds,[latest.id]);assert(!result.affectedRunIds.includes(first.id));
  const replacement=store.drainProfileRecomputes().replacements[0].newRunId;
  store.syncProfiles(input(store,[projected(creator(),REGISTRY,NOW+2000)]));const next=store.drainProfileRecomputes().replacements[0].newRunId;
  assert.equal(store.profileRunReplacement(latest.id),next);assert.equal(store.profileRunReplacement(replacement),next);assert.equal(store.stats().profileSync.recomputedRuns,2);
});
test("pending recomputes and checkpoint persist across restart and competing store cannot overwrite revision",t=>{
  const dir=mkdtempSync(join(tmpdir(),"bdhub-profile-sync-")),path=join(dir,"matching.sqlite");let a=new MatchingStore(path,{dataset,seed:false,now:()=>NOW+10000}),b;
  t.after(()=>{a.close();b?.close();rmSync(dir,{recursive:true,force:true});});const p=product();a.upsert({products:[p]});const old=recall(a,p),stale=input(a);a.syncProfiles(stale);a.close();a=new MatchingStore(path,{seed:false,now:()=>NOW+10000});
  assert.equal(a.getProfileSyncStatus().pendingRecomputes,1);b=new MatchingStore(path,{seed:false,now:()=>NOW+10000});
  assert.throws(()=>b.syncProfiles(stale),error=>error.code==="profile_sync_conflict");const newRun=a.drainProfileRecomputes(1).replacements[0].newRunId;assert.equal(b.profileRunReplacement(old.id),newRun);assert.equal(b.getProfileSyncStatus().pendingRecomputes,0);
});
test("origin metadata is validated and actual conflict or missing labels remain outside indices",t=>{
  const store=fixture(t);for(const changed of [{creatorId:"bad"},{kind:"other"},{metricStates:{followers:"invented",unitsSold:"value",avgViews:"value",gmvValue:"value"}}]){const c=projected();c.profileOrigin={...c.profileOrigin,...changed};assert.throws(()=>store.upsert({creators:[c]}),error=>error.code==="invalid_fact");}
  for(const status of ["missing","conflict"]){const c=creator();c.categoryFact=category(c.categories,status);assert.throws(()=>store.upsert({creators:[c]}),error=>error.code==="invalid_fact");}
});
test("unconfigured datasets never create synchronization tables during stats, recall or reads",t=>{
  const store=new MatchingStore(":memory:",{seed:false,now:()=>NOW});t.after(()=>store.close());store.upsert({products:[product()],creators:[creator()]});const run=recall(store,product());
  assert.equal(store.stats().profileSync,undefined);assert.equal(store.resolveRegistryCreator(REGISTRY),null);assert.equal(store.profileRunReplacement(run.id),null);assert.deepEqual(store.drainProfileRecomputes(),{replacements:[],status:null});
  assert.throws(()=>store.syncProfiles(input(store)),error=>error.code==="profile_sync_dataset");assert.equal(store.db.prepare("SELECT count(*) n FROM sqlite_master WHERE name LIKE 'matching_profile_%' OR name='matching_latest_query'").get().n,0);
});
test("an initially empty source can bind its first event only within the same database generation",t=>{
  const store=fixture(t),base="registry:"+"a".repeat(64),first=base+":"+"b".repeat(64);
  store.syncProfiles(input(store,[],{sourceKey:base+":empty",cursor:0,events:0}));assert.equal(store.profileSyncCheckpoint().cursor,0);
  assert.throws(()=>store.syncProfiles(input(store,[],{sourceKey:"registry:"+"c".repeat(64)+":"+"b".repeat(64)})),error=>error.code==="profile_sync_source_conflict");
  const applied=store.syncProfiles(input(store,[projected()],{sourceKey:first}));assert.equal(applied.status.revision,2);assert.equal(store.profileSyncCheckpoint().sourceKey,first);
  assert.throws(()=>store.syncProfiles(input(store,[],{sourceKey:base+":"+"d".repeat(64)})),error=>error.code==="profile_sync_source_conflict");
});
test("a connection opened before another process enables sync sees the committed checkpoint",t=>{
  const dir=mkdtempSync(join(tmpdir(),"bdhub-profile-sync-race-")),path=join(dir,"matching.sqlite"),a=new MatchingStore(path,{dataset,seed:false}),b=new MatchingStore(path,{seed:false});
  t.after(()=>{a.close();b.close();rmSync(dir,{recursive:true,force:true});});const initial=input(a);assert.equal(b.profileSyncCheckpoint().revision,0);a.syncProfiles(initial);
  assert.equal(b.profileSyncCheckpoint().revision,1);assert.equal(b.getProfileSyncStatus().syncedCreators,1);assert.throws(()=>b.syncProfiles(initial),error=>error.code==="profile_sync_conflict");assert.equal(b.resolveRegistryCreator(REGISTRY).oecId,"123");
});
