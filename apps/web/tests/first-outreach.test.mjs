import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {firstOutreachArgv,validateFirstOutreach,validateFirstOutreachRequest} from "../src/server/first-outreach/bridge.ts";
import {firstApplicationGroups,firstApplicationSummary,firstOutreachActions,firstOutreachCanContinue,firstOutreachCycleLabel,firstOutreachLabel,firstOutreachWaitingReasons} from "../src/features/first-outreach/presentation.ts";
import {GET,POST} from "../src/app/api/first-outreach/route.ts";

const payload={schemaVersion:"bdhub.first-outreach.v1",market:"br",applicationCoverage:"unverified",control:{revision:1,mode:"stopped",approvalsEnabled:false,updatedAt:1},readiness:{ready:false,blockers:["sample_facts_unavailable"]},counts:{products:0,matchedCreators:0,sent:0,pendingApplications:0,approved:0,needsReview:0,unknown:0},candidates:[],applications:[],runtime:{state:"blocked",reason:"sample_facts_unavailable",updatedAt:null},platformWrites:0,sourceUpdatedAt:null};
const request={action:"start",market:"br",requestId:"first-request-0001",expectedRevision:1};
const local={host:"127.0.0.1:5198",origin:"http://127.0.0.1:5198","content-type":"application/json"};
const mutate=(body,query="?market=br",headers=local)=>POST(new Request(`http://127.0.0.1:5198/api/first-outreach${query}`,{method:"POST",headers,body:JSON.stringify(body)}));

test("BR state preserves explicit data gaps and real candidate previews",()=>{
 assert.equal(validateFirstOutreach(payload).readiness.blockers[0],"sample_facts_unavailable");
 const value=validateFirstOutreach({...payload,readiness:{ready:true,blockers:[]},counts:{...payload.counts,products:1,matchedCreators:1},candidates:[{creatorId:"creator-01",handle:"alice",pid:"1729571380453480878",productName:"Creme",categoryName:"Beleza",gmv:null,message:"Olá @alice!"}]});
 assert.equal(value.candidates[0].message,"Olá @alice!");assert.equal(value.candidates[0].gmv,null);
 for(const bad of [{...payload,market:"it"},{...payload,platformWrites:1},{...payload,counts:undefined},{...payload,counts:{...payload.counts,products:-1}},{...payload,readiness:{ready:true,blockers:["missing"]}},{...payload,credentials:"private"},{...payload,control:{...payload.control,cookie:"secret"}},{...payload,applications:[{id:"1",handle:"a",pid:"1",productName:"p",state:"pending",reason:null,raw:{secret:1}}]}])assert.throws(()=>validateFirstOutreach(bad),/invalid_first_outreach/);
});

test("application coverage is explicit and old empty reads never become zero pending",()=>{
 assert.equal(validateFirstOutreach(payload).applicationCoverage,"unverified");
 assert.equal(validateFirstOutreach({...payload,applicationCoverage:"complete"}).applicationCoverage,"complete");
 for(const coverage of [undefined,null,"readonly",true])assert.throws(()=>validateFirstOutreach({...payload,applicationCoverage:coverage}),/invalid_first_outreach/);
 const ledger={pendingApplications:12,needsReview:2,approved:5,unknown:1};
 assert.deepEqual(firstApplicationSummary({applicationCoverage:"unverified",counts:ledger}),{value:"—",hint:"待审批范围尚未核实"});
 assert.deepEqual(firstApplicationSummary({counts:ledger}),{value:"—",hint:"待审批范围尚未核实"});
 assert.equal(firstApplicationSummary({applicationCoverage:"complete",counts:{...ledger,pendingApplications:0}}).value,0);
 const state=validateFirstOutreach({...payload,counts:{...payload.counts,...ledger}});
 assert.equal(state.counts.approved,5);assert.equal(state.counts.unknown,1);
});

test("waiting reasons use plain language and unknown codes never leak into the page",()=>{
 assert.equal(firstOutreachLabel("account_busy"),"等待账号空闲后继续");
 assert.equal(firstOutreachLabel("workflow_resource_busy"),"等待平台读取资源空闲后继续");
 assert.equal(firstOutreachLabel("private_technical_code:/secret/path"),"当前步骤尚未完成，请查看运行记录");
});

test("independent cycle status preserves retry times and excludes private runtime data",()=>{
 const cycle={state:"waiting",reason:"sample_verification_required",updatedAt:123,nextRunAt:183};
 const automation={supply:{...cycle,state:"running",reason:null},samples:cycle};
 assert.deepEqual(validateFirstOutreach({...payload,automation}).automation,automation);
 assert.equal(firstOutreachLabel(cycle.reason),"平台验证尚未完成，将自动重试");
 assert.equal(firstOutreachLabel("goal_observed"),"平台已进入后续阶段，原审批回执未确认");
 assert.equal(validateFirstOutreach({...payload,automation:{...automation,supply:{...cycle,state:"partial"}}}).automation.supply.state,"partial");
 assert.match(firstOutreachLabel("first_model_batches_isolated:2"),/^2 批模型准备暂未完成/);
 assert.equal(firstOutreachLabel("first_model_batches_isolated:<private>"),"当前步骤尚未完成，请查看运行记录");
 for(const bad of [null,{supply:cycle},{...automation,raw:{}},{...automation,samples:{...cycle,state:"authorized"}},{...automation,samples:{...cycle,nextRunAt:-1}},{...automation,samples:{...cycle,updatedAt:Infinity}},{...automation,samples:{...cycle,cookie:"private"}},{...automation,samples:{...cycle,reason:{raw:1}}}])assert.throws(()=>validateFirstOutreach({...payload,automation:bad}),/invalid_first_outreach/);
});

test("every mutation requires an idempotency key, revision and the exact BR action shape",()=>{
 for(const action of ["prepare","sync","start","continue","stop","reconcile"])assert.deepEqual(validateFirstOutreachRequest({...request,action}),{...request,action});
 assert.deepEqual(validateFirstOutreachRequest({...request,action:"set-approvals",approvalsEnabled:true}),{...request,action:"set-approvals",approvalsEnabled:true});
 for(const bad of [{...request,market:"it"},{...request,requestId:undefined},{...request,expectedRevision:undefined},{...request,expectedRevision:-1},{...request,expectedRevision:1.2},{...request,action:"execute"},{...request,confirmed:true},{...request,action:"reconcile",deliveryId:"different-intent"},{...request,action:"set-approvals"},{...request,approvalsEnabled:true}])assert.throws(()=>validateFirstOutreachRequest(bad),/invalid_first_outreach_request/);
});

test("bridge uses fixed CLI argv without a shell or fallback market",()=>{
 assert.deepEqual(firstOutreachArgv("/project","status"),["/project/scripts/first-outreach.py","status","--market","br"]);
 assert.deepEqual(firstOutreachArgv("/project","start",request),["/project/scripts/first-outreach.py","start","--market","br","--json",JSON.stringify(request)]);
});

test("API rejects missing, repeated and foreign markets before invoking CLI",async()=>{
 for(const query of ["","?market=it","?market=br&market=br","?market=br&other=1"]){
  const response=await GET(new Request(`http://127.0.0.1:5198/api/first-outreach${query}`,{headers:{host:local.host}}));assert.equal(response.status,400,query);
  assert.equal((await mutate(request,query)).status,400,query);
 }
 assert.equal((await mutate({...request,market:"it"})).status,409);
 assert.equal((await mutate({...request,account:"acc6"})).status,400);
 assert.equal((await mutate({...request,action:"reconcile",expectedRevision:undefined})).status,400);
});

test("API requires local origin and JSON for all mutations",async()=>{
 assert.equal((await mutate(request,"?market=br",{...local,origin:"https://foreign.test"})).status,403);
 assert.equal((await mutate(request,"?market=br",{...local,"sec-fetch-site":"cross-site"})).status,403);
 assert.equal((await mutate(request,"?market=br",{host:local.host,"content-type":"application/json"})).status,403);
 assert.equal((await mutate(request,"?market=br",{...local,"content-type":"text/plain"})).status,415);
});

test("continuous mode waits for a confirmed canary while readiness handles send unknown",()=>{
 const confirmed={control:{mode:"stopped"},runtime:{state:"canary_confirmed"},counts:{sent:1,unknown:0}};
 assert.equal(firstOutreachCanContinue(confirmed),true);assert.equal(firstOutreachCanContinue({...confirmed,counts:{sent:1,unknown:1}}),true);
 for(const row of [{...confirmed,counts:{sent:0,unknown:0}},{...confirmed,runtime:{state:"sending"}},{...confirmed,control:{mode:"continuous"}}])assert.equal(firstOutreachCanContinue(row),false);
});

test("page gates BR, presents real data gaps, separate approval and explicit first send",()=>{
 const route=readFileSync(new URL("../src/app/[market]/first-outreach/page.tsx",import.meta.url),"utf8");
 const page=readFileSync(new URL("../src/features/first-outreach/FirstOutreachWorkspace.tsx",import.meta.url),"utf8");
 const shell=readFileSync(new URL("../src/features/shell/AppShell.tsx",import.meta.url),"utf8");
 assert.match(route,/market!=="br"\)notFound\(\)/);assert.match(shell,/currentMarket.key==="br"\?\[\{href:`\$\{base\}\/first-outreach/);
 for(const text of ["可推荐商品","匹配达人","成功推送","自动审批通过","待处理申请","当前等待","葡语邀约预览","确认发送首条","继续持续推送","自动审批已关闭","停止持续推送"])assert.ok(page.includes(text)||text==="自动审批已关闭"&&page.includes('"已关闭"'),text);
 assert.match(page,/value=\{data\.counts\.products\}/);assert.match(page,/lang="pt-BR"/);assert.match(page,/setConfirmation\("start"\)/);assert.doesNotMatch(page,/sqlite|demo|mockData/i);
});


test("application display keeps unknowns and new states out of finished history",()=>{
 const rows=[{id:"history",state:"completed"},{id:"unmapped",state:"sample_application_state_unverified"},{id:"pending",state:"pending"},{id:"waiting",state:"content_pending"},{id:"unknown",state:"unknown"},{id:"new",state:"new_platform_state"},{id:"receipt",state:"goal_observed"},{id:"closed",state:"cancelled"}];
 const groups=firstApplicationGroups(rows);
 assert.deepEqual(groups.attention.map(row=>row.id),["unknown","receipt","pending","new","unmapped"]);
 assert.deepEqual(groups.waiting.map(row=>row.id),["waiting"]);
 assert.deepEqual(groups.finished.map(row=>row.id),["history","closed"]);
 assert.deepEqual(rows.map(row=>row.id),["history","unmapped","pending","waiting","unknown","new","receipt","closed"]);
 assert.equal(Object.values(groups).flat().length,rows.length);
});

test("normal waits are not repeated while distinct readiness blockers stay visible",()=>{
 assert.deepEqual(firstOutreachWaitingReasons({runtime:{state:"waiting_window",reason:"outside_send_window"},readiness:{blockers:["outside_send_window"]}}),[]);
 assert.deepEqual(firstOutreachWaitingReasons({runtime:{state:"waiting_window",reason:"outside_send_window"},readiness:{blockers:["first_outreach_materials_unavailable","first_outreach_materials_unavailable","first_outreach_result_unknown"]}}),["匹配商品的推荐链接或葡语商品名尚未就绪","有结果未知的原操作，等待核验"]);
 assert.equal(firstOutreachLabel("waiting_transport"),"连接恢复中");
});


test("first-send controls preserve explicit continuation after a confirmed canary",()=>{
 const canary={control:{mode:"canary"},runtime:{state:"canary_confirmed"},counts:{sent:1,unknown:0}};
 assert.deepEqual(firstOutreachActions(canary),["continue","stop"]);
 assert.deepEqual(firstOutreachActions({...canary,control:{mode:"continuous"},runtime:{state:"waiting_window"}}),["stop"]);
 assert.deepEqual(firstOutreachActions({...canary,control:{mode:"stopped"},counts:{sent:0,unknown:1}}),["prepare","start","reconcile"]);
});


test("optional material progress preserves old cycles and validates gap arithmetic independently from ready offers",()=>{
 const cycle={state:"complete",reason:"first_outreach_materials_waiting_evidence",updatedAt:123,nextRunAt:183};
 const materials={remaining:11,readyOffers:9,gapCounts:{commission_terms_unavailable:3,prior_intent_superseded:2,binding_not_current:3,localized_name_missing:1,material_preparation_pending:1,original_intent_pending:1},actionable:3,unchanged:14};
 const state=automation=>({...payload,automation});
 const plain=validateFirstOutreach(state({supply:cycle,samples:cycle}));
 assert.equal(plain.automation.supply.materials,undefined);
 const complete=validateFirstOutreach(state({supply:{...cycle,materials},samples:cycle}));
 assert.deepEqual(complete.automation.supply.materials,materials);
 assert.equal(complete.automation.supply.materials.readyOffers,9);
 assert.equal(firstOutreachCycleLabel(complete.automation.supply),"部分材料等待");
 assert.equal(firstOutreachCycleLabel({...cycle,materials:{...materials,remaining:0}}),"本轮已完成");
 assert.equal(firstOutreachLabel("first_outreach_materials_no_progress"),"部分材料暂无进展，按原任务退避核查");
 assert.equal(firstOutreachLabel(cycle.reason),"部分材料等待原来源更新");
 for(const bad of [null,{...materials,remaining:12},{...materials,actionable:4},{...materials,unchanged:-1},{...materials,readyOffers:true},{...materials,gapCounts:{...materials.gapCounts,unknown_reason:1}},{...materials,gapCounts:{...materials.gapCounts,localized_name_missing:undefined}}]){
  assert.throws(()=>validateFirstOutreach(state({supply:{...cycle,materials:bad},samples:cycle})),/invalid_first_outreach/);
 }
 assert.throws(()=>validateFirstOutreach(state({supply:cycle,samples:{...cycle,materials}})),/invalid_first_outreach/);
});
