import test from "node:test";
import assert from "node:assert/strict";
import {DatabaseSync} from "node:sqlite";
import {mkdtempSync,readFileSync,rmSync,existsSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {CreatorIdentityReadStore,PROFILE_FIELDS,sanitizeProfileField} from "../src/server/creator-identities/store.ts";

const IDS=["creator_"+"1".repeat(32),"creator_"+"2".repeat(32),"creator_"+"3".repeat(32)];
const TIMES=["2026-09-01T00:00:00.000000Z","2026-09-02T00:00:00.000000Z","2026-09-03T00:00:00.000000Z","2026-09-04T00:00:00.000000Z"];
const SCHEMA=readFileSync(new URL("../../../scripts/lib/creator_identity.py",import.meta.url),"utf8").match(/_SCHEMA = """([\s\S]*?)"""/)[1];
function fixture(t){
  const dir=mkdtempSync(join(tmpdir(),"bdhub-identity-web-")),path=join(dir,"identity.sqlite"),db=new DatabaseSync(path);db.exec(SCHEMA);
  t.after(()=>{db.close();rmSync(dir,{recursive:true,force:true});});
  const create=(id,market,oec,handle,time)=>db.prepare("INSERT INTO creator_identity VALUES(?,?,?,?,?,?,?,?,?,?)").run(id,market,oec,handle,time,TIMES.indexOf(time),0,time,TIMES.indexOf(time),time);
  const observe=(id,market,oec,handle,time,payload,kind="profile",outcome="observed")=>db.prepare("INSERT INTO identity_observation VALUES(?,?,?,?,?,?,?,?,?,?,?,?)").run(`obs_${id}_${time}_${kind}`,id,market,oec,kind,handle,outcome,time,TIMES.indexOf(time),"PRIVATE_EVIDENCE_REF", "PRIVATE_FINGERPRINT",JSON.stringify(payload));
  const lead=(id,market,handle,external,creatorId=null,externalSource="probe_source_reference")=>{
    db.prepare("INSERT INTO handle_lead VALUES(?,?,?,?,?,?,?,?,?)").run(id,market,handle,TIMES[0],"PRIVATE_LEAD_REF_"+id,"SECRET_FINGERPRINT",externalSource,external,JSON.stringify({rawMessage:"DO_NOT_EXPOSE",externalUrl:"https://secret.invalid"}));
    if(creatorId)db.prepare("INSERT INTO handle_lead_resolution VALUES(?,?,?,?,?,?)").run(id,creatorId,TIMES[1],"PRIVATE_RESOLUTION_REF","SECRET_FINGERPRINT",JSON.stringify({cookie:"DO_NOT_EXPOSE"}));
  };
  create(IDS[0],"it","111","new_name",TIMES[1]);create(IDS[1],"it","222","old_name",TIMES[2]);create(IDS[2],"mx","111","mx_name",TIMES[0]);
  const oldFields={follower_cnt:{status:"value",value:10},med_gmv_revenue:{status:"value",value:{decimal:"1000.00",format:"€1,000",cookie:"DO_NOT_EXPOSE"}},industry_groups:{status:"value",value:[{id:"1",label:"Home",weight:"0.7",externalUrl:"https://secret.invalid"}]},top_video_data:{status:"value",value:{count:1,structureKeys:["video","cookie","https://secret.invalid"],urls:["https://secret.invalid"]}}};
  observe(IDS[0],"it","111","old_name",TIMES[0],{fields:oldFields,rawMessage:"DO_NOT_EXPOSE",identity:{email:"DO_NOT_EXPOSE"}});
  observe(IDS[0],"it","111","new_name",TIMES[1],{fields:{...oldFields,follower_cnt:{status:"zero",value:0},med_gmv_revenue:{status:"no_value",value:"DO_NOT_EXPOSE"},gpm:{status:"unauthorized",value:{decimal:"DO_NOT_EXPOSE"}},partnered_brand:{status:"value",value:{brands:[],cookie:"DO_NOT_EXPOSE"}},cookie:{status:"value",value:"DO_NOT_EXPOSE"}},cookie:"DO_NOT_EXPOSE"});
  observe(IDS[0],"it","111",null,TIMES[3],{},"failure","blocked");
  observe(IDS[1],"it","222","old_name",TIMES[2],{exactFind:{raw:"DO_NOT_EXPOSE"}});observe(IDS[2],"mx","111","mx_name",TIMES[0],{fields:{follower_cnt:{status:"value",value:99}}});
  lead("lead_a","it","old_name","source-a",IDS[0]);lead("lead_pending","it","pending_name","source-pending");lead("lead_other","it","old_name","source-hidden",IDS[0],"other_provider");
  const open=(cyclePath)=>{const store=new CreatorIdentityReadStore(path,cyclePath);t.after(()=>store.close());return store;};
  return {dir,path,db,lead,create,observe,open};
}

test("read-only overview and paginated list expose explicit identities and unresolved leads only",t=>{
  const f=fixture(t),store=f.open(),bytes=readFileSync(f.path);
  const overview=store.overview("it");assert.equal(overview.datasetStatus,"ready");assert.equal(overview.verifiedIdentities,2);assert.equal(overview.pendingLeads,1);assert.equal(overview.resolvedLeads,2);
  const list=store.list({market:"it",limit:1,offset:1});assert.equal(list.total,2);assert.equal(list.items.length,1);assert.equal(list.items[0].creatorId,IDS[0]);
  const pending=store.list({market:"it",status:"pending"});assert.equal(pending.total,1);assert.equal(pending.items[0].handle,"pending_name");assert.equal(pending.items[0].historicalCrossSourceIdentityProven,false);
  for(const word of ["payload_json","rawMessage","DO_NOT_EXPOSE","PRIVATE_","source-a","https://"]){assert(!JSON.stringify([overview,list,pending]).includes(word),word);}
  assert.deepEqual(readFileSync(f.path),bytes);
});
test("overview counts replies and showcase adoption by distinct creator",t=>{
  const f=fixture(t),cyclePath=join(f.dir,"cycle.sqlite"),cycle=new DatabaseSync(cyclePath);
  cycle.exec("CREATE TABLE plan(id TEXT,market TEXT);CREATE TABLE relationship(plan_id TEXT,creator_id TEXT,oec TEXT);CREATE TABLE inbox_event(plan_id TEXT,oec TEXT,kind TEXT,historical INTEGER)");
  cycle.prepare("INSERT INTO plan VALUES('p','it')").run();cycle.prepare("INSERT INTO relationship VALUES('p',?,'111')").run(IDS[0]);cycle.prepare("INSERT INTO relationship VALUES('p',?,'222')").run(IDS[1]);
  const event=cycle.prepare("INSERT INTO inbox_event VALUES('p',?,?,?)");event.run('111','creatorReplies',0);event.run('111','creatorReplies',0);event.run('111','showcaseNotifications',0);event.run('222','showcaseNotifications',0);event.run('222','creatorReplies',1);cycle.close();
  const overview=f.open(cyclePath).overview('it');assert.equal(overview.repliedCreators,1);assert.equal(overview.showcaseCreators,2);
});
test("BR and MY identity cards and current product GMV use only their own selected lead evidence",t=>{
  const f=fixture(t),cyclePath=join(f.dir,"cycle.sqlite"),cycle=new DatabaseSync(cyclePath);
  const brId="creator_"+"4".repeat(32),myId="creator_"+"5".repeat(32);
  f.create(brId,"br","333","br_name",TIMES[1]);f.create(myId,"my","555","my_name",TIMES[1]);
  cycle.exec(`CREATE TABLE plan(id TEXT,market TEXT,institution TEXT,state TEXT);
    CREATE TABLE relationship(plan_id TEXT,creator_id TEXT,oec TEXT);
    CREATE TABLE inbox_event(plan_id TEXT,oec TEXT,kind TEXT,historical INTEGER);
    CREATE TABLE lead_query_head(plan_id TEXT,query_id TEXT);
    CREATE TABLE lead_query_selection(query_id TEXT,source_id TEXT);
    CREATE TABLE source_edge_index(plan_id TEXT,source_id TEXT,source_handle TEXT,pid TEXT,revenue_value TEXT,revenue_currency TEXT,window_start TEXT,window_end TEXT,units INTEGER);
    CREATE TABLE cycle_identity_resolution(plan_id TEXT,source_id TEXT,creator_id TEXT);
    CREATE TABLE cycle_identity_outcome(plan_id TEXT,source_id TEXT,status TEXT);`);
  for(const [plan,market,query] of [["p-br","br","q-br"],["p-my","my","q-my"]]){
    cycle.prepare("INSERT INTO plan VALUES(?,?,'bjn-local-research','active')").run(plan,market);
    cycle.prepare("INSERT INTO lead_query_head VALUES(?,?)").run(plan,query);
  }
  const add=(plan,query,source,handle,pid,gmv,currency)=>{
    cycle.prepare("INSERT INTO lead_query_selection VALUES(?,?)").run(query,source);
    cycle.prepare("INSERT INTO source_edge_index VALUES(?,?,?,?,?,?,?,?,?)").run(plan,source,handle,pid,gmv,currency,"2026-09-06","2026-09-19",2);
  };
  add("p-br","q-br","br-a","br_name","1".repeat(19),"772.34","BRL");
  add("p-br","q-br","br-b","missing","2".repeat(19),null,"BRL");
  add("p-br","q-br","br-c","br_name","3".repeat(19),null,"BRL");
  add("p-my","q-my","my-a","my_name","4".repeat(19),"1013.06","MYR");
  cycle.prepare("INSERT INTO cycle_identity_resolution VALUES('p-br','br-a',?)").run(brId);
  cycle.prepare("INSERT INTO cycle_identity_resolution VALUES('p-my','my-a',?)").run(myId);
  cycle.prepare("INSERT INTO cycle_identity_outcome VALUES('p-br','br-b','unresolved')").run();
  cycle.prepare("INSERT INTO cycle_identity_outcome VALUES('p-br','br-c','unresolved')").run();
  cycle.close();
  const store=f.open(cyclePath);
  assert.deepEqual(store.overview("br").identityStage,{resolved:1,unresolved:1});
  assert.deepEqual(store.overview("my").identityStage,{resolved:1,unresolved:0});
  assert.deepEqual(store.detail("br",brId).leadGmv,[{pid:"1".repeat(19),gmv:"772.34",currency:"BRL",windowStart:"2026-09-06",windowEnd:"2026-09-19",units:2}]);
  assert.equal(store.detail("br",brId).latestProfileObservedAt,null);
  assert.equal(store.detail("my",myId).leadGmv[0].currency,"MYR");
});
test("old handle search returns both canonical candidates after name reuse without merging",t=>{
  const store=fixture(t).open(),result=store.list({market:"it",q:"old_name"});
  assert.equal(result.total,2);assert.deepEqual(new Set(result.items.map(row=>row.oecId)),new Set(["111","222"]));
  assert.equal(result.items.find(row=>row.creatorId===IDS[0]).currentHandle,"new_name");
  assert.equal(store.list({market:"it",q:"111"}).items[0].creatorId,IDS[0]);
  assert.equal(store.list({market:"mx",q:"111"}).items[0].creatorId,IDS[2]);
  assert.equal(store.list({market:"it",q:"%"}).total,0);assert.equal(store.list({market:"it",q:"' OR 1=1--"}).total,0);
});
test("detail preserves canonical id, deduplicated alias dates, current no_value and separately dated historical metric",t=>{
  const store=fixture(t).open(),detail=store.detail("it",IDS[0]);
  assert.equal(detail.creator.creatorId,IDS[0]);assert.equal(detail.creator.oecId,"111");assert.equal(detail.creator.currentHandle,"new_name");assert.equal(detail.creator.verifiedAt,TIMES[1]);
  assert.deepEqual(detail.aliases.map(row=>row.handle),["new_name","old_name"]);assert.equal(detail.aliases[1].isCurrent,false);assert.equal(detail.aliases[0].firstObservedAt,TIMES[1]);
  const money=detail.fields.find(field=>field.name==="med_gmv_revenue");assert.equal(money.status,"no_value");assert.equal(money.observedAt,TIMES[1]);assert.equal(money.value,undefined);assert.equal(money.lastAvailable.value.decimal,"1000.00");assert.equal(money.lastAvailable.observedAt,TIMES[0]);
  assert.equal(detail.fields.find(field=>field.name==="follower_cnt").value,0);assert.equal(detail.latestObservation.outcome,"blocked");assert.equal(detail.latestObservation.observedAt,TIMES[3]);assert.equal(detail.latestProfileObservedAt,TIMES[1]);
  assert.equal(detail.fields.length,22);for(const word of ["DO_NOT_EXPOSE","cookie","PRIVATE_","https://","rawMessage"]){assert(!JSON.stringify(detail).includes(word),word);}
  assert.equal(store.detail("br",IDS[0]).creator,null);
});
test("source prefers OEC, uses only explicit provider resolutions and never guesses a source from its handle",t=>{
  const store=fixture(t).open();
  const current=store.source({market:"it",externalId:"source-a"});assert.equal(current.status,"verified");assert.equal(current.creator.oecId,"111");assert.equal(current.resolutionRelation,"current_discovery_only");assert.equal(current.historicalCrossSourceIdentityProven,false);
  assert.equal(store.source({market:"it",externalId:"source-pending"}).status,"pending");
  assert.equal(store.source({market:"it",externalId:"source-hidden"}).status,"not_found");assert.equal(store.source({market:"it",externalId:"old_name"}).status,"not_found");
  assert.equal(store.source({market:"it",oecId:"222",externalId:"source-a"}).creator.oecId,"222");assert.equal(store.source({market:"it",oecId:"999",externalId:"source-a"}).status,"not_found");
  assert.equal(store.source({market:"mx",oecId:"111"}).creator.creatorId,IDS[2]);
});
test("a reused source reference with two canonical resolutions returns ambiguous candidates",t=>{
  const f=fixture(t);f.lead("lead_reused","it","old_name","source-a",IDS[1]);
  const result=f.open().source({market:"it",externalId:"source-a"});assert.equal(result.status,"ambiguous");assert.equal(result.creator,null);assert.equal(result.candidateCount,2);assert.equal(result.candidates.length,2);assert.equal(result.historicalCrossSourceIdentityProven,false);
});
test("missing DB stays not_imported without creating a demo or filesystem entry",t=>{
  const f=fixture(t),path=join(f.dir,"missing.sqlite"),store=new CreatorIdentityReadStore(path);t.after(()=>store.close());
  assert.equal(store.overview("it").datasetStatus,"not_imported");assert.equal(store.list().total,0);assert.equal(store.detail("it",IDS[0]).creator,null);assert.equal(store.source({market:"it",oecId:"111"}).status,"not_imported");assert.equal(existsSync(path),false);
});
test("incompatible schema is refused with a fixed non-sensitive error",t=>{
  const f=fixture(t),path=join(f.dir,"bad.sqlite"),db=new DatabaseSync(path);db.exec("CREATE TABLE raw_secret(cookie TEXT)");db.close();
  assert.throws(()=>new CreatorIdentityReadStore(path),error=>error.code==="identity_schema_mismatch"&&!error.message.includes("raw_secret"));
});
test("unknown schema versions and a missing required column are rejected without migration",t=>{
  const f=fixture(t);f.db.exec("PRAGMA ignore_check_constraints=ON; UPDATE identity_store_meta SET version=2");
  assert.throws(()=>new CreatorIdentityReadStore(f.path),error=>error.code==="identity_schema_mismatch");
  f.db.exec("UPDATE identity_store_meta SET version=1; ALTER TABLE creator_identity DROP COLUMN current_handle");
  assert.throws(()=>new CreatorIdentityReadStore(f.path),error=>error.code==="identity_schema_mismatch");
  assert(!f.db.prepare("PRAGMA table_info(creator_identity)").all().some(row=>row.name==="current_handle"));
});
test("OEC-only identity without a returned handle remains usable without inventing an alias",t=>{
  const f=fixture(t),id="creator_"+"4".repeat(32);
  f.db.prepare("INSERT INTO creator_identity VALUES(?,?,?,?,?,?,?,?,?,?)").run(id,"it","444",null,null,null,0,TIMES[2],2,TIMES[2]);
  f.observe(id,"it","444",null,TIMES[2],{fields:{handle:{status:"no_value"},creator_oecuid:{status:"value",value:"444"},selection_region:{status:"value",value:"IT"}}});
  const store=f.open(),detail=store.detail("it",id);assert.equal(detail.creator.currentHandle,null);assert.equal(detail.creator.currentHandleVerifiedAt,null);assert.equal(detail.creator.status,"verified");assert.deepEqual(detail.aliases,[]);assert.equal(store.source({market:"it",oecId:"444"}).creator.creatorId,id);
});
test("repeated name observations update alias dates without duplicating it or changing canonical ID",t=>{
  const f=fixture(t);f.observe(IDS[0],"it","111","new_name",TIMES[2],{fields:{follower_cnt:{status:"value",value:11}}});
  f.db.prepare("UPDATE creator_identity SET handle_observed_at=?,handle_observed_us=2,last_observed_at=?,last_observed_us=2 WHERE creator_id=?").run(TIMES[2],TIMES[2],IDS[0]);
  const detail=f.open().detail("it",IDS[0]);assert.equal(detail.creator.creatorId,IDS[0]);assert.equal(detail.aliases.length,2);assert.equal(detail.aliases[0].firstObservedAt,TIMES[1]);assert.equal(detail.aliases[0].lastObservedAt,TIMES[2]);assert.equal(detail.creator.currentHandleVerifiedAt,TIMES[2]);
});
test("field projection keeps only allowed typed values and discards unauthorized and external URLs",()=>{
  assert.equal(PROFILE_FIELDS.length,22);
  assert.deepEqual(sanitizeProfileField("gpm",{status:"unauthorized",value:{decimal:"SECRET"}},TIMES[0]),{name:"gpm",status:"unauthorized",observedAt:TIMES[0]});
  assert.equal(sanitizeProfileField("follower_cnt",{status:"value",value:NaN},TIMES[0]).status,"error");
  assert.equal(sanitizeProfileField("product_price_range",{status:"value",value:"https://private.invalid"},TIMES[0]).status,"error");
  const value=sanitizeProfileField("top_video_data",{status:"value",value:{count:2,structureKeys:["video","cookie"],raw:"SECRET"}},TIMES[0]).value;
  assert.deepEqual(value,{count:2,structureKeys:["video"]});
});
test("same-time full Profile beats later Find for current fields and historical available values",t=>{
  const f=fixture(t),insert=f.db.prepare("INSERT INTO identity_observation VALUES(?,?,?,?,?,?,?,?,?,?,?,?)");
  const add=(event,ref,fields)=>insert.run(event,IDS[0],"it","111","profile","new_name","observed",TIMES[2],2,ref,"fingerprint",JSON.stringify({fields}));
  add("a-full-profile","probe:profile",{follower_cnt:{status:"value",value:55},med_gmv_revenue:{status:"value",value:{decimal:"100.00"}}});
  add("z-later-find","probe:find-profile",{follower_cnt:{status:"value",value:5},med_gmv_revenue:{status:"value",value:{decimal:"2.00"}}});
  const current=f.open().detail("it",IDS[0]);assert.equal(current.fields.find(field=>field.name==="follower_cnt").value,55);
});
test("historical field fallback follows full Profile quality before rowid or random event id",t=>{
  const f=fixture(t),insert=f.db.prepare("INSERT INTO identity_observation VALUES(?,?,?,?,?,?,?,?,?,?,?,?)");
  for(const [event,ref,amount] of [["a-full","probe:profile","100.00"],["z-find","probe:find-profile","2.00"]])
    insert.run(event,IDS[0],"it","111","profile","new_name","observed",TIMES[2],2,ref,"fingerprint",JSON.stringify({fields:{med_gmv_revenue:{status:"value",value:{decimal:amount}}}}));
  insert.run("latest-missing-money",IDS[0],"it","111","profile","new_name","observed",TIMES[3],3,"latest:profile","fingerprint",JSON.stringify({fields:{med_gmv_revenue:{status:"no_value"}}}));
  const detail=f.open().detail("it",IDS[0]),field=detail.fields.find(field=>field.name==="med_gmv_revenue");assert.equal(field.status,"no_value");assert.equal(field.lastAvailable.value.decimal,"100.00");
});
