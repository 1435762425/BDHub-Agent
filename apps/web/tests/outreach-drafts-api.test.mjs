import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {readDraftPanelData} from "../src/features/outreach-drafts/read-data.ts";
import {callDraftCommand,createDraftHandlers,decodeDraftOutput,parseDraftQuery,parseDraftRequest} from "../src/server/outreach-drafts/bridge.ts";
const PACKET="review-packet-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",DRAFT="outreach_draft_"+"b".repeat(32),HASH="c".repeat(64),TIME="2026-09-12T04:00:00.000Z";
const input={packetId:PACKET,style:"friendly",instructions:"  简短一些  ",requestId:"one-original-request"};
const ctx={packetId:PACKET,style:"friendly",instructions:"简短一些"};
const summary=()=>({id:DRAFT,packetId:PACKET,status:"queued",style:"friendly",createdAt:TIME,startedAt:null,finishedAt:null,errorCode:null,stage:null,reservedCostCny:"0.25",knownCostCny:null,executionBlocked:true});
const status=(enabled=true)=>({provider:{ready:true,model:"deepseek-flash"},policy:{enabled,id:enabled?"trial-1":null,version:1,model:"deepseek-flash",maxDrafts:3,maxCostCny:"1.00"},budget:{reservedCny:"0.00",knownCostCny:"0.00",availableCny:"1.00",usedDrafts:0},workerOnline:false});
const cost=()=>({estimatedCny:null,upperBoundCny:null,complete:false});
const usage=()=>({promptTokens:100,cacheHitTokens:null,cacheMissTokens:null,completionTokens:20,reasoningTokens:null,totalTokens:120});
const detail=()=>({facts:[{id:"p1-fit",kind:"category_alignment",value:["居家生活"]}],products:[{id:"p1",nameIt:"lampada"}],draft:{...summary(),status:"drafted"},content:{textIt:"Ciao! Ti interessa una proposta?",translationZh:"你好，有兴趣了解一个合作想法吗？",selectedProductIds:["p1"],evidenceRefs:["p1-fit"],rationaleZh:"从类目适配出发询问意向。"},review:{verdict:"pass",issues:[],unsupportedClaims:[],italianValid:true,translationFaithful:true},attempts:[{stage:"draft",status:"completed",usage:usage(),cost:cost()}],cost:cost(),contextRequest:ctx,contextFingerprint:HASH,executionBlocked:true});
const headers={host:"127.0.0.1:5198",origin:"http://127.0.0.1:5198","content-type":"application/json"};
const post=(body=input,extra={})=>new Request("http://127.0.0.1:5198/api/outreach-drafts",{method:"POST",headers:{...headers,...extra},body:typeof body==="string"?body:JSON.stringify(body)});
const get=q=>new Request(`http://127.0.0.1:5198/api/outreach-drafts?${q}`,{headers:{host:headers.host}});

test("only packet/style/instructions/request ID accepted; free text normalized once",()=>{
  assert.deepEqual(parseDraftRequest(input),{...input,instructions:"简短一些"});
  for(const key of ["context","fingerprint","model","apiKey","authorization","budget","send"]){assert.throws(()=>parseDraftRequest({...input,[key]:"DO_NOT_USE"}),error=>error.code==="invalid_request");}
  assert.throws(()=>parseDraftRequest({...input,instructions:"字".repeat(501)}));assert.throws(()=>parseDraftRequest({...input,style:"fixed"}));assert.throws(()=>parseDraftRequest({...input,packetId:"../secret"}));
});
test("GET accepts bounded status/list/detail and rejects control or repeated query fields",()=>{
  assert.deepEqual(parseDraftQuery(get("view=status").url),{command:"status",input:{}});
  assert.deepEqual(parseDraftQuery(get(`view=list&packetId=${PACKET}`).url),{command:"list",input:{packetId:PACKET}});
  assert.deepEqual(parseDraftQuery(get(`view=detail&draftId=${DRAFT}`).url),{command:"detail",input:{draftId:DRAFT}});
  for(const q of ["view=enqueue",`view=list&packetId=${PACKET}&packetId=${PACKET}`,"view=detail&draftId=../../secret",`view=status&model=custom`])assert.throws(()=>parseDraftQuery(get(q).url));
});
test("local origin, body limits and JSON rejected before any builder or CLI call",async()=>{
  let called=0;const api=createDraftHandlers({invoke:async()=>{called++;return null;},build:()=>{called++;throw Error("DO_NOT_RUN");}});
  for(const [request,code] of [[post(input,{origin:"http://other.invalid"}),403],[post(input,{host:"other.invalid"}),403],[post(input,{"sec-fetch-site":"cross-site"}),403],[post(input,{"content-type":"text/plain"}),415],[post("x".repeat(8193)),400],[post("{invalid"),400],[post({...input,context:{secret:true}}),400]])assert.equal((await api.POST(request)).status,code);
  assert.equal(called,0);
});
test("disabled generation does not compile context or enqueue a model job",async()=>{
  const calls=[];const api=createDraftHandlers({invoke:async(command)=>{calls.push(command);return command==="lookup_request"?null:status(false);},build:()=>{throw Error("SHOULD_NOT_COMPILE");}});
  const response=await api.POST(post());assert.equal(response.status,409);assert.equal((await response.json()).error.code,"policy_disabled");assert.deepEqual(calls,["lookup_request","status"]);
});
test("replay retrieves original job before stale-context compilation or disabled-policy check",async()=>{
  let compiled=0;const calls=[],api=createDraftHandlers({invoke:async(command,value)=>{calls.push({command,value});return summary();},build:()=>{compiled++;throw Object.assign(Error("OLD_PACKET"),{code:"stale_context"});}});
  for(let i=0;i<2;i++){const response=await api.POST(post());assert.equal(response.status,200);assert.equal((await response.json()).id,DRAFT);}
  assert.equal(compiled,0);assert.deepEqual(calls[0],calls[1]);assert.equal(calls[0].command,"lookup_request");assert.deepEqual(calls[0].value,{...input,instructions:"简短一些"});
});
test("new generation uses server compiled capsule and original request ID",async()=>{
  const calls=[],compiled=[],capsule={context:{modelFacts:{safe:true},executionBlocked:true},fingerprint:HASH,contextRequest:ctx};
  const api=createDraftHandlers({invoke:async(command,value)=>{calls.push({command,value});return command==="lookup_request"?null:command==="status"?status():summary();},build:value=>{compiled.push(value);return capsule;}});
  const response=await api.POST(post());assert.equal(response.status,200);assert.deepEqual(compiled,[ctx]);assert.deepEqual(calls.at(-1),{command:"enqueue",value:{requestId:input.requestId,...capsule}});
});
test("detail freshness changes preserve original text and strip server bindings",async()=>{
  const original=detail(),api=createDraftHandlers({invoke:async()=>original,build:()=>({context:{},contextRequest:ctx,fingerprint:"d".repeat(64)})});
  const response=await api.GET(get(`view=detail&draftId=${DRAFT}`)),body=await response.json();assert.equal(body.freshness,"stale");assert.deepEqual(body.content,original.content);assert.deepEqual(body.facts,original.facts);assert.deepEqual(body.products,original.products);assert(!("contextRequest" in body));assert(!("contextFingerprint" in body));assert.equal(original.draft.status,"drafted");
});
test("stale compiler errors give stale while unknown compiler faults remain unknown without private errors",async()=>{
  for(const code of ["stale_run","identity_unverified","SECRET_INTERNAL"]){const api=createDraftHandlers({invoke:async()=>detail(),build:()=>{throw Object.assign(Error("SECRET_PROVIDER_KEY"),{code});}}),response=await api.GET(get(`view=detail&draftId=${DRAFT}`)),body=await response.json();assert.equal(body.freshness,code==="SECRET_INTERNAL"?"unknown":"stale");assert(!JSON.stringify(body).includes("SECRET"));}
});
test("decoder keeps unknown cost/cache distinct from zero and strips extra provider data",()=>{
  const original=detail();original.rawProvider={key:"SECRET"};original.facts[0].binding={oecId:"SECRET"};original.products[0].pid="SECRET";original.attempts[0].usage.secret="SECRET";original.contextRequest={...ctx};const decoded=decodeDraftOutput("detail",JSON.stringify(original));assert.equal(decoded.cost.estimatedCny,null);assert.equal(decoded.attempts[0].usage.cacheHitTokens,null);assert.equal(decoded.attempts[0].usage.totalTokens,120);assert(!JSON.stringify(decoded).includes("SECRET"));
  assert.equal(decodeDraftOutput("status",JSON.stringify(status(false))).policy.enabled,false);
  assert.equal(decodeDraftOutput("lookup_request","null"),null);
  assert.throws(()=>decodeDraftOutput("enqueue",JSON.stringify({...summary(),executionBlocked:false})),error=>error.code==="draft_service_unavailable");
});
test("fixed CLI argv and stdin keep custom content out of shell and child errors masked",async()=>{
  let captured;const runner=(executable,args,options,callback)=>({stdin:{on(){return this;},end(value){captured={executable,args,options,value};queueMicrotask(()=>callback(null,JSON.stringify(status(false))));}}});
  await callDraftCommand("status",{},{root:"/project/BDHub-Agent",run:runner});assert.equal(captured.executable,"/project/01-BDSystem-V2/.venv/bin/python");assert.deepEqual(captured.args,["/project/BDHub-Agent/scripts/outreach-drafts.py","status"]);assert.equal(captured.options.shell,false);
  const bad=(executable,args,options,callback)=>({stdin:{on(){return this;},end(){queueMicrotask(()=>callback(Error("SECRET"),JSON.stringify(summary())));}}});
  await assert.rejects(callDraftCommand("enqueue",{requestId:input.requestId,context:{text:"$(PRIVATE)"},fingerprint:HASH,contextRequest:ctx},{root:"/project/BDHub-Agent",run:bad}),error=>error.code==="draft_service_unavailable"&&!error.message.includes("SECRET"));
});
test("known context failures have safe business messages; unexpected private errors are not exposed",async()=>{
  for(const code of ["product_name_unverified","draft_facts_missing","SECRET_INTERNAL"]){const api=createDraftHandlers({invoke:async command=>command==="lookup_request"?null:status(),build:()=>{throw Object.assign(Error("SECRET_EXCEPTION"),{code});}}),response=await api.POST(post()),body=await response.json();assert.equal(response.status,code==="SECRET_INTERNAL"?503:422);assert(!JSON.stringify(body).includes("SECRET"));}
});

test("actual Python queue and provider error codes retain safe normalized meanings",()=>{
  const worker=readFileSync(new URL("../../../scripts/lib/outreach_drafts.py",import.meta.url),"utf8"),provider=readFileSync(new URL("../../../scripts/lib/draft_provider.py",import.meta.url),"utf8");
  const block=worker.match(/ERRORS = frozenset\(\{([\s\S]*?)\}\)/)[1]+provider.match(/SAFE_CODES = frozenset\(\{([\s\S]*?)\}\)/)[1];
  const codes=[...new Set([...block.matchAll(/"([a-z0-9_]+)"/g)].map(match=>match[1]))];assert(codes.length>30);
  const important={model_disabled:403,budget_exhausted:429,trial_limit_reached:429,request_conflict:409,context_stale:409};
  for(const code of codes){assert.throws(()=>decodeDraftOutput("enqueue",JSON.stringify({error:{code,message:"PRIVATE_PROVIDER_RESPONSE",status:200}})),error=>error.code===code&&error.status===(important[code]??error.status)&&!error.message.includes("PRIVATE"));const original=summary();original.errorCode=code;assert.equal(decodeDraftOutput("enqueue",JSON.stringify(original)).errorCode,code);}
});

test("creator history only accepts exact canonical identity and rejects names or alternate IDs",()=>{
  const creatorId="creator_"+"e".repeat(32);
  assert.deepEqual(parseDraftQuery(get(`view=creator&creatorId=${creatorId}`).url),{command:"creator_history",input:{creatorId}});
  for(const id of ["alice","@alice","1234567890123456789","it-profile-oec-123","creator_"+"e".repeat(31),"creator_"+"E".repeat(32),"../creator"]){assert.throws(()=>parseDraftQuery(get(`view=creator&creatorId=${encodeURIComponent(id)}`).url),error=>error.code==="invalid_request");}
  for(const q of [`view=creator&creatorId=${creatorId}&creatorId=${creatorId}`,`view=creator&creatorId=${creatorId}&handle=alice`,`view=creator&creatorId=${creatorId}&packetId=${PACKET}`,"view=creator"]){assert.throws(()=>parseDraftQuery(get(q).url),error=>error.code==="invalid_request");}
});
test("creator history reads saved cross-packet drafts with no builder, model status, or enqueue",async()=>{
  const creatorId="creator_"+"e".repeat(32),calls=[],saved=[{...summary(),status:"stale"},{...summary(),id:"outreach_draft_"+"f".repeat(32),packetId:"review-packet-11111111-1111-1111-1111-111111111111",status:"result_unknown"}];
  const make=()=>createDraftHandlers({invoke:async(command,value)=>{calls.push({command,value});assert.equal(command,"creator_history");return {drafts:saved,workerOnline:false};},build:()=>{throw Error("MUST_NOT_COMPILE_OLD_PACKET");}});
  for(let reload=0;reload<2;reload++){const response=await make().GET(get(`view=creator&creatorId=${creatorId}`));assert.equal(response.status,200);const body=await response.json();assert.deepEqual(body.drafts,saved);assert.equal(body.workerOnline,false);}
  assert.deepEqual(calls,[{command:"creator_history",value:{creatorId}},{command:"creator_history",value:{creatorId}}]);
});
test("creator lookup cannot be injected through mutation body or mixed query parameters",async()=>{
  let called=0;const api=createDraftHandlers({invoke:async()=>{called++;return null;},build:()=>{called++;throw Error("DO_NOT_RUN");}}),creatorId="creator_"+"e".repeat(32);
  assert.equal((await api.POST(post({...input,creatorId}))).status,400);
  assert.equal((await api.POST(post({command:"creator_history",creatorId}))).status,400);
  assert.equal((await api.GET(get(`view=creator&creatorId=${creatorId}&context=forged`))).status,400);
  assert.equal((await api.GET(new Request(get(`view=creator&creatorId=${creatorId}`).url,{headers:{host:headers.host,origin:"http://untrusted.invalid"}}))).status,403);
  assert.equal(called,0);
});
test("creator-history CLI is fixed, scoped and bounds saved results without leaking private bindings",async()=>{
  const creatorId="creator_"+"e".repeat(32);let calls=0,captured;
  const run=(executable,args,options,callback)=>({stdin:{on(){return this;},end(value){calls++;captured={executable,args,options,value};queueMicrotask(()=>callback(null,JSON.stringify({drafts:[{...summary(),context:{credential:"PRIVATE"},creatorId:"PRIVATE"}],workerOnline:false})));}}});
  const result=await callDraftCommand("creator_history",{creatorId},{root:"/project/BDHub-Agent",run});assert.equal(result.drafts.length,1);assert(!JSON.stringify(result).includes("PRIVATE"));assert.deepEqual(captured.args,["/project/BDHub-Agent/scripts/outreach-drafts.py","creator_history"]);assert.deepEqual(JSON.parse(captured.value),{creatorId});assert.equal(captured.options.shell,false);
  for(const bad of [{creatorId:"@alice"},{creatorId,packetId:PACKET},{creatorId,context:{}}])assert.throws(()=>callDraftCommand("creator_history",bad,{root:"/project/BDHub-Agent",run}),error=>error.code==="invalid_request");
  assert.equal(calls,1);assert.throws(()=>decodeDraftOutput("creator_history",JSON.stringify({drafts:Array(21).fill(summary()),workerOnline:false})),error=>error.code==="draft_service_unavailable");
});


test("readonly panel never requests model status and reads old content despite unavailable policy",async()=>{
  const calls=[],saved={...detail(),freshness:"stale"};
  const fakeGet=async url=>{calls.push(url);if(url.includes("view=status"))throw Error("POLICY_UNAVAILABLE");if(url.includes("view=list"))return {drafts:[summary()],workerOnline:false};return saved;};
  const read=await readDraftPanelData({packetId:PACKET,selectedId:DRAFT,readOnly:true},fakeGet);
  assert.equal(read.service,null);assert.equal(read.detail.content.textIt,saved.content.textIt);assert.equal(read.detail.freshness,"stale");assert.equal(calls.length,2);assert(calls.every(url=>!url.includes("view=status")));
});
test("normal generation panel checks model status while history tolerates unknown freshness",async()=>{
  const calls=[],fakeGet=async url=>{calls.push(url);if(url.includes("view=status"))throw Error("POLICY_UNAVAILABLE");if(url.includes("view=list"))return {drafts:[summary()],workerOnline:false};return {...detail(),freshness:"unknown"};};
  await assert.rejects(readDraftPanelData({packetId:PACKET,selectedId:DRAFT,readOnly:false},fakeGet),/POLICY_UNAVAILABLE/);assert(calls.some(url=>url.includes("view=status")));
  calls.length=0;const read=await readDraftPanelData({packetId:PACKET,selectedId:DRAFT,readOnly:true},fakeGet);assert.equal(read.detail.freshness,"unknown");assert(read.detail.content.textIt);assert(calls.every(url=>!url.includes("view=status")));
});
test("history panel opens the selected saved draft rather than latest version of the packet",async()=>{
  const older="outreach_draft_"+"1".repeat(32),calls=[],saved={...detail(),draft:{...summary(),id:older},freshness:"current"};
  const read=await readDraftPanelData({packetId:PACKET,selectedId:older,readOnly:true},async url=>{calls.push(url);return url.includes("view=list")?{drafts:[summary(),saved.draft],workerOnline:false}:saved;});
  assert.equal(read.selectedId,older);assert.equal(read.detail.draft.id,older);assert(calls.at(-1).endsWith(older));
});
test("draft panel reader rejects cross-packet or wrong selected draft responses",async()=>{
  const other="review-packet-11111111-1111-1111-1111-111111111111";
  await assert.rejects(readDraftPanelData({packetId:PACKET,selectedId:DRAFT,readOnly:true},async()=>({drafts:[{...summary(),packetId:other}],workerOnline:false})),/资料包不一致/);
  await assert.rejects(readDraftPanelData({packetId:PACKET,selectedId:DRAFT,readOnly:true},async url=>url.includes("view=list")?{drafts:[summary()],workerOnline:false}:{...detail(),draft:{...summary(),id:"outreach_draft_"+"2".repeat(32)},freshness:"current"}),/资料包不一致/);
});
