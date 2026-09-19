import test from "node:test";
import assert from "node:assert/strict";
import {callSecondLiveCommand,createSecondLiveHandlers,decodeSecondLiveOutput,parseSecondLiveQuery,parseSecondLiveStart,SecondLiveApiError} from "../src/server/second-live/bridge.ts";
import {liveStartBlocker,readLiveTrial,validStartRequest} from "../src/features/second-outreach/live-read.ts";
const ID="second_live_trial_"+"a".repeat(32),ITEM="second_live_item_"+"b".repeat(32),HASH="c".repeat(64),CREATOR="creator_"+"d".repeat(32),AT="2026-09-13T00:00:00.000Z",EXPIRES="2026-09-13T00:30:00.000Z";
const request={trialId:ID,snapshotHash:HASH,confirmed:true,requestId:"user-confirmed-request-1"};
function value(){return {trial:{trialId:ID,snapshotHash:HASH,market:"it",account:"acc6",campaignId:"italy-second-pilot",createdAt:AT,expiresAt:EXPIRES,approved:false,approvedAt:null,paused:false,expired:false,complete:false,blockedByUnknown:[],items:[{itemId:ITEM,opportunityId:"second-it-fixture",creatorId:CREATOR,oecId:"1234567890123456789",handle:"fixture.creator",draftId:"template-draft_"+"e".repeat(64),textIt:"Ciao! Il link BJN è qui sopra.",translationZh:"你好，BJN 链接在上方。",textSha256:"f".repeat(64),state:"pending",partialDelivery:false,requiresReconciliation:false,unknownStage:null,errorCode:null,components:[{componentId:ITEM+":card:0",componentKind:"card",position:0,state:"pending",productId:"1729779362302171335",listId:"1111111111111111111",listName:"BJN",campaignName:"Fixture",confirmed:false,errorCode:null},{componentId:ITEM+":text",componentKind:"text",position:1,state:"pending",productId:null,listId:null,listName:null,campaignName:null,confirmed:false,errorCode:null}],attempts:[]}]},launch:{state:"not_started",pid:null,requestId:null}};}
const headers={host:"127.0.0.1:5198",origin:"http://127.0.0.1:5198","content-type":"application/json"};
const get=query=>new Request(`http://127.0.0.1:5198/api/second-live${query?`?${query}`:""}`,{headers:{host:headers.host}});
const post=(body=request,extra={},url="http://127.0.0.1:5198/api/second-live")=>new Request(url,{method:"POST",headers:{...headers,...extra},body:typeof body==="string"?body:JSON.stringify(body)});

test("GET requires exactly one explicit trial ID with no sample or latest fallback",()=>{
  assert.deepEqual(parseSecondLiveQuery(get(`trialId=${ID}`).url),{trialId:ID});
  for(const query of ["",`trialId=${ID}&trialId=${ID}`,`trialId=${ID}&view=start`,"trialId=../../scope",`trialId=trial_fake`,`account=acc6`])assert.throws(()=>parseSecondLiveQuery(get(query).url));
});
test("start request requires confirmed true and immutable snapshot hash only",()=>{
  assert.deepEqual(parseSecondLiveStart(request),request);
  for(const confirmed of [false,1,"true",null,undefined])assert.throws(()=>parseSecondLiveStart({...request,confirmed}));
  for(const key of ["account","market","model","textIt","oecId","cards","scope","args","policy","script"]){assert.throws(()=>parseSecondLiveStart({...request,[key]:"FORGED"}));}
  for(const snapshotHash of ["",HASH.toUpperCase(),"x".repeat(64),HASH+"0"])assert.throws(()=>parseSecondLiveStart({...request,snapshotHash}));
});
test("local host/origin JSON and body size reject before any launcher call",async()=>{
  let called=0;const api=createSecondLiveHandlers(async()=>{called++;return value();});
  for(const [input,expected] of [[post(request,{host:"outside.invalid"}),403],[post(request,{origin:"http://outside.invalid"}),403],[post(request,{"sec-fetch-site":"cross-site"}),403],[post(request,{"content-type":"text/plain"}),415],[post("x".repeat(4097)),400],[post("{invalid"),400],[post({...request,model:"evil"}),400],[post(request,{},get(`trialId=${ID}`).url),400]])assert.equal((await api.POST(input)).status,expected);
  assert.equal((await api.GET(get(""))).status,400);assert.equal(called,0);
});
test("GET invokes only show and preserves an unapproved expired snapshot",async()=>{
  const data=value();data.trial.expired=true;const calls=[],api=createSecondLiveHandlers(async(command,input)=>{calls.push({command,input});return data;});
  const response=await api.GET(get(`trialId=${ID}`));assert.equal(response.status,200);assert.deepEqual(await response.json(),data);assert.deepEqual(calls,[{command:"show",input:{trialId:ID}}]);assert.equal(data.trial.approved,false);
});
test("POST forwards same exact request identity and does not inject content or account",async()=>{
  const calls=[],api=createSecondLiveHandlers(async(command,input)=>{calls.push({command,input});return value();});
  for(let i=0;i<2;i++)assert.equal((await api.POST(post())).status,200);
  assert.deepEqual(calls,[{command:"start",input:request},{command:"start",input:request}]);
});
test("child and scope errors have static user messages instead of credential or raw stderr",async()=>{
  for(const [code,status] of [["trial_expired",409],["scope_conflict",409],["request_conflict",409],["approval_result_unknown",503],["campaign_recipient_conflict",409],["trial_scope_not_found",404]])assert.throws(()=>decodeSecondLiveOutput(JSON.stringify({error:{code,status:200,message:"PRIVATE_COOKIE"}})),error=>error.code===code&&error.status===status&&!error.message.includes("PRIVATE"));
  const api=createSecondLiveHandlers(async()=>{throw new Error("PRIVATE_TOKEN");});const result=await api.POST(post());assert.equal(result.status,503);assert(!JSON.stringify(await result.json()).includes("PRIVATE"));
});
test("projection keeps partial/unknown state and card index while dropping private provenance",()=>{
  const data=value();data.trial.items[0].state="result_unknown";data.trial.items[0].partialDelivery=true;data.trial.items[0].unknownStage="send_card:0";data.trial.items[0].components[0].state="result_unknown";data.trial.scope={apiKey:"PRIVATE"};data.trial.items[0].receipt={cookie:"PRIVATE"};data.authReport="PRIVATE";
  const decoded=decodeSecondLiveOutput(JSON.stringify(data));assert.equal(decoded.trial.items[0].unknownStage,"send_card:0");assert.equal(decoded.trial.items[0].partialDelivery,true);assert.equal(decoded.trial.items[0].components[0].listId,"1111111111111111111");assert(!JSON.stringify(decoded).includes("PRIVATE"));
  data.trial.items[0].unknownStage="send_card:9";assert.throws(()=>decodeSecondLiveOutput(JSON.stringify(data)));
});
test("projection matches Python Unicode limits and rejects mismatched fixed account or false confirmations",()=>{
  const data=value();data.trial.items[0].components[0].listName="🚀".repeat(1000);data.trial.items[0].components[0].campaignName="名".repeat(600);data.trial.items[0].textIt="☀️".repeat(5000);assert.equal(decodeSecondLiveOutput(JSON.stringify(data)).trial.items[0].components[0].listName,data.trial.items[0].components[0].listName);
  data.trial.account="acc5";assert.throws(()=>decodeSecondLiveOutput(JSON.stringify(data)));data.trial.account="acc6";data.trial.items[0].components[0].confirmed=true;assert.throws(()=>decodeSecondLiveOutput(JSON.stringify(data)));
});
test("CLI argument vector is fixed with safe stdin and no shell for fake startup",async()=>{
  let recorded;const run=(executable,args,options,callback)=>({stdin:{on(){return this;},end(input){recorded={executable,args,options,input};queueMicrotask(()=>callback(null,JSON.stringify(value())));}}});
  await callSecondLiveCommand("start",request,{root:"/project/BDHub-Agent",run});assert.equal(recorded.executable,"/project/BDHub-Agent/.venv/bin/python");assert.deepEqual(recorded.args,["/project/BDHub-Agent/scripts/second-live-api.py","start"]);assert.deepEqual(JSON.parse(recorded.input),request);assert.equal(recorded.options.shell,false);assert.equal(recorded.options.env.PYTHONDONTWRITEBYTECODE,"1");assert(!recorded.args.join(" ").includes(HASH));
});
test("nonzero child status or another trial response is not acknowledged as a launch",async()=>{
  const runner=(output,failed=false)=>(executable,args,options,callback)=>({stdin:{on(){return this;},end(){queueMicrotask(()=>callback(failed?Error("PRIVATE"):null,JSON.stringify(output)));}}});
  await assert.rejects(callSecondLiveCommand("start",request,{root:"/project/BDHub-Agent",run:runner(value(),true)}),error=>error.code==="second_live_unavailable");const another=value();another.trial.trialId="second_live_trial_"+"1".repeat(32);await assert.rejects(callSecondLiveCommand("show",{trialId:ID},{root:"/project/BDHub-Agent",run:runner(another)}));
});
test("queryless UI read does nothing and mismatched history does not become default data",async()=>{
  let calls=0;assert.equal(await readLiveTrial("",async()=>{calls++;return value();}),null);assert.equal(calls,0);
  await assert.rejects(readLiveTrial("not-a-trial",async()=>{calls++;return value();}));assert.equal(calls,0);await assert.rejects(readLiveTrial("second_live_trial_"+"9".repeat(32),async()=>value()));
});
test("UI start eligibility respects expiry, partial delivery, unknowns, and previous process intent",()=>{
  const data=value(),now=Date.parse(AT)+1000;assert.equal(liveStartBlocker(data,now),null);
  for(const [patch,expected] of [[{expired:true},"expired"],[{paused:true},"paused"],[{complete:true},"complete"],[{blockedByUnknown:[{trialId:ID,itemId:ITEM}]},"unknown"]])assert.equal(liveStartBlocker({...data,trial:{...data.trial,...patch}},now),expected);
  assert.equal(liveStartBlocker(data,Date.parse(EXPIRES)),"expired");assert.equal(liveStartBlocker({...data,trial:{...data.trial,expired:true,paused:true,complete:true}},Date.parse(EXPIRES)),"paused");const partial=value();partial.trial.items[0].partialDelivery=true;assert.equal(liveStartBlocker(partial,now),"partial");
  for(const state of ["started","running","finished","start_unknown"])assert.equal(liveStartBlocker({...data,launch:{state,pid:null,requestId:"old"}},now),"launch_recorded");assert.equal(liveStartBlocker({...data,launch:{state:"not_started",pid:null,requestId:"old"}},now),"launch_recorded");
  const noCards=value();noCards.trial.items[0].components=noCards.trial.items[0].components.slice(1);assert.equal(liveStartBlocker(noCards,now),"components_missing");
});
test("stored confirmation must retain the same explicit trial/hash/request envelope",()=>{
  assert(validStartRequest(request));for(const bad of [{...request,confirmed:false},{...request,model:"other"},{...request,snapshotHash:"bad"},{...request,trialId:""}])assert(!validStartRequest(bad));
});
