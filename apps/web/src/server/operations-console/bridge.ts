import {execFile} from "node:child_process";
import {join} from "node:path";
import {projectRoot} from "../runtime/project-root.ts";
import {isLocalRequest} from "../runtime/validation.ts";
import {singleflight} from "../runtime/singleflight.ts";

/** Read-only cross-market console (H12). Every figure carries its own time; missing data stays null. */
export type Holder={market:string;stage:string;since:number|null};
// waitingKnown=false: the stage's resource needs could not be determined, which is not "nothing to wait for" (I05).
export type ConsoleStage={stage:string;state:"running"|"queued";since:number|null;heartbeatAt:number|null;waitingOn:{resource:string;heldBy:Holder[]}[];waitingKnown:boolean;waitReason:string|null};
export type FinishedStage={market:string;runId:string;stage:string;state:string;startedAt:number|null;finishedAt:number|null;errorCode:string|null;items:number|null;writeEvidence:string|null};
export type Lane={state:string|null;value:number|null;label:string|null;unit:string|null;scope:string|null;lastSuccessAt:number|null;stopReason:string|null};
export type ConsoleMarket={market:string;available:true;setting:{automaticOperationsEnabled:boolean;continuousSendEnabled:boolean;fullCatalogWeeklyEnabled:boolean};
 run:{runId:string;state:string;startedAt:number|null}|null;current:ConsoleStage|null;lastFinished:FinishedStage|null;
 needsReview:{runId:string;state:string;errorCode:string|null;at:number|null}|null;openHumanCases:number|null;
 // The conversation page's own human queue (I02); open cases are a different count.
 humanQueue:{human:number|null;technical:number|null}|null;
 lanes:{available:boolean;observedAt:number|null;continuousSend:Lane|null;agentReply:Lane|null}}|{market:string;available:false;error:string};
export type OperationsConsole={schemaVersion:"bdhub.operations-console.v1";checkedAt:number;markets:ConsoleMarket[];
 resources:(Holder&{resource:string;heartbeatAt:number|null})[];recent:FinishedStage[];scheduler:{running:boolean;checkedAt:number|null};
 stageLabels:Record<string,string>;readOnly:true;platformWrites:0};

const fail=():never=>{throw Error("invalid_operations_console");};
const object=(value:unknown)=>value&&typeof value==="object"&&!Array.isArray(value)?value as Record<string,unknown>:fail();
const text=(value:unknown,max=160)=>typeof value==="string"&&value.length>0&&value.length<=max?value:fail();
const maybeText=(value:unknown,max=160)=>value==null?null:text(value,max);
const time=(value:unknown)=>value==null?null:typeof value==="number"&&Number.isFinite(value)&&value>0?value:fail();
const count=(value:unknown)=>value==null?null:typeof value==="number"&&Number.isSafeInteger(value)&&value>=0?value:fail();
const flag=(value:unknown)=>typeof value==="boolean"?value:fail();
const list=(value:unknown,max:number)=>Array.isArray(value)&&value.length<=max?value as unknown[]:fail();
const holder=(raw:unknown):Holder=>{const v=object(raw);return {market:text(v.market,8),stage:text(v.stage,40),since:time(v.since)};};
const finished=(raw:unknown):FinishedStage=>{const v=object(raw);return {market:text(v.market,8),runId:text(v.runId),stage:text(v.stage,40),state:text(v.state,40),
 startedAt:time(v.startedAt),finishedAt:time(v.finishedAt),errorCode:maybeText(v.errorCode),items:count(v.items),writeEvidence:maybeText(v.writeEvidence,20)};};
const lane=(raw:unknown):Lane|null=>{if(raw==null)return null;const v=object(raw);
 return {state:maybeText(v.state,40),value:count(v.value),label:maybeText(v.label,40),unit:maybeText(v.unit,20),scope:maybeText(v.scope,20),lastSuccessAt:time(v.lastSuccessAt),stopReason:maybeText(v.stopReason)};};

export function validateOperationsConsole(raw:unknown):OperationsConsole{
 const v=object(raw);
 if(v.schemaVersion!=="bdhub.operations-console.v1"||v.readOnly!==true||v.platformWrites!==0)fail();
 const markets=list(v.markets,20).map((item):ConsoleMarket=>{const m=object(item);
  if(m.available===false)return {market:text(m.market,8),available:false,error:text(m.error,200)};
  if(m.available!==true)fail();
  const setting=object(m.setting),lanes=object(m.lanes);
  let current:ConsoleStage|null=null;
  if(m.current!=null){const c=object(m.current);if(c.state!=="running"&&c.state!=="queued")fail();
   current={stage:text(c.stage,40),state:c.state as "running"|"queued",since:time(c.since),heartbeatAt:time(c.heartbeatAt),
    waitingOn:list(c.waitingOn,10).map(w=>{const row=object(w);return {resource:text(row.resource,120),heldBy:list(row.heldBy,10).map(holder)};}),
    waitingKnown:c.waitingKnown!==false,waitReason:maybeText(c.waitReason,120)};}
  const run=m.run==null?null:(()=>{const r=object(m.run);return {runId:text(r.runId),state:text(r.state,40),startedAt:time(r.startedAt)};})();
  const review=m.needsReview==null?null:(()=>{const r=object(m.needsReview);return {runId:text(r.runId),state:text(r.state,40),errorCode:maybeText(r.errorCode),at:time(r.at)};})();
  return {market:text(m.market,8),available:true,
   setting:{automaticOperationsEnabled:flag(setting.automaticOperationsEnabled),continuousSendEnabled:flag(setting.continuousSendEnabled),fullCatalogWeeklyEnabled:flag(setting.fullCatalogWeeklyEnabled)},
   run,current,lastFinished:m.lastFinished==null?null:finished(m.lastFinished),needsReview:review,openHumanCases:count(m.openHumanCases),
   humanQueue:m.humanQueue==null?null:(()=>{const h=object(m.humanQueue);return {human:count(h.human),technical:count(h.technical)};})(),
   lanes:{available:flag(lanes.available),observedAt:time(lanes.observedAt),continuousSend:lane(lanes.continuousSend),agentReply:lane(lanes.agentReply)}};});
 const scheduler=object(v.scheduler),labels=object(v.stageLabels);
 return {schemaVersion:"bdhub.operations-console.v1",checkedAt:time(v.checkedAt)??fail(),markets,
  resources:list(v.resources,50).map(item=>{const r=object(item);return {resource:text(r.resource,120),...holder(r),heartbeatAt:time(r.heartbeatAt)};}),
  recent:list(v.recent,50).map(finished),scheduler:{running:flag(scheduler.running),checkedAt:time(scheduler.checkedAt)},
  stageLabels:Object.fromEntries(Object.entries(labels).map(([key,value])=>[text(key,40),text(value,40)])),readOnly:true,platformWrites:0};
}

function run():Promise<OperationsConsole>{const root=projectRoot();return new Promise((resolve,reject)=>execFile(join(root,".venv/bin/python"),[join(root,"scripts/operations-console.py")],{cwd:root,timeout:30_000,maxBuffer:2*1024*1024,env:{...process.env,PYTHONDONTWRITEBYTECODE:"1"}},(error,stdout)=>{try{const value=JSON.parse(stdout);if(error||value?.error)throw Error(String(value?.error||"operations_console_unavailable"));resolve(validateOperationsConsole(value));}catch(caught){reject(caught);}}));}

const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};
// Every open console tab shares one read while it runs; nothing is cached after it settles.
export const handlers={GET:async(request:Request)=>{
 if(!isLocalRequest(request,false))return Response.json({error:"local_origin_required"},{status:403,headers});
 if(new URL(request.url).search)return Response.json({error:"invalid_query"},{status:400,headers});
 try{return Response.json(await singleflight("operations-console",run),{headers});}catch{return Response.json({error:"operations_console_unavailable"},{status:503,headers});}
}};
