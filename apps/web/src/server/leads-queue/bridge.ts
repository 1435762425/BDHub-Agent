import {execFile} from "node:child_process";
import {join} from "node:path";
import {projectRoot} from "../runtime/project-root.ts";

/** Queue shape for PID -> creator-lead queries. Numbers only; the platform is not contacted. */
export type LeadsQueueConfig={version:string;refreshDays:number;leadsPerPid:number;windowDays:number;batchSize:number;maxAttempts:number};
// The last finished Kalodata stage as recorded by its own terminal result; null counts mean "not recorded".
export type LeadsLastFinished={runId:string;stageRunId:string;state:string;errorCode:string|null;startedAt:number|null;finishedAt:number|null;resultRecorded:boolean;
 completedQueries:number|null;aQueries:number|null;bQueries:number|null;fragments:number|null;networkRequests:number|null;sliceComplete:boolean|null;errorCount:number|null;errors:string[];
 // What this stage's publications added (H13); null when nothing was recorded in its window.
 contribution?:{publications:number;newPairs:number;refreshedPairs:number;newCreators:number;aPublications:number;bPublications:number}|null};
export type LeadsRunState={running:boolean;queued?:boolean;lastFinished?:LeadsLastFinished|null;startedAt?:number;finishedAt?:number;batchSize?:number;dueQueue?:number;targets?:number;done?:number;leads?:number;networkRequests?:number;stopped?:string|null;errors?:{pid:string;code:string}[];platformWrites?:number};
export type LeadsQueueNext={pid:string;units:number;title:string};
export type LeadsQueueDue={pid:string;queriedAt:number;dueAt:number;leads:number|null;title:string};
// ``taken`` is the batch: the top ``batchSize`` of the due queue. ``shortfall`` is how far the
// queue fell short of the ceiling -- it is reported, never filled from products that are not due.
export type RollingQueue={available:true;automaticEnabled:boolean;identityHold:boolean;types:Record<"A"|"B",{total:number;runnable:number;first:number;refresh:number;checkpoints:number;states:Record<string,number>;oldestReadyAt:number|null;staleCheckpoints:number;oldestWindowEnd:string|null}>;control:{state:string;retry_at:number;last_error:string|null}|null};
function rollingQueue(value:unknown):RollingQueue|undefined{
 if(!value||typeof value!=="object"||(value as Record<string,unknown>).available!==true)return;
 const v=value as Record<string,unknown>,types=v.types as Record<string,unknown>;
 if(!types||typeof types!=="object")throw Error("invalid_leads_queue");
 const result={} as RollingQueue["types"];
 for(const key of ["A","B"] as const){
  const row=types[key] as Record<string,unknown>,states=row?.states as Record<string,unknown>;
  if(!row||!states)throw Error("invalid_leads_queue");
  const counted=Object.fromEntries(Object.entries(states).map(([state,n])=>[state,count(n)]));
  if(Object.values(counted).reduce((a,b)=>a+b,0)!==count(row.total))throw Error("invalid_leads_queue");
  result[key]={total:count(row.total),runnable:count(row.runnable),first:count(row.first),refresh:count(row.refresh),checkpoints:count(row.checkpoints),states:counted,staleCheckpoints:count(row.staleCheckpoints??0),oldestWindowEnd:typeof row.oldestWindowEnd==="string"?row.oldestWindowEnd:null,oldestReadyAt:typeof row.oldestReadyAt==="number"?row.oldestReadyAt:null};
 }
 const control=v.control as Record<string,unknown>|null;
 return {available:true,automaticEnabled:v.automaticEnabled===true,identityHold:Boolean(v.identityHold),types:result,control:control?{state:String(control.state),retry_at:typeof control.retry_at==="number"?control.retry_at:0,last_error:typeof control.last_error==="string"?control.last_error:null}:null};
}
export type LeadsQueueState={market:string;config:LeadsQueueConfig;refreshDays:number;eligible:number;linked:number;scope:number;firstTime:number;due:number;waiting:number;unknownScope:number;nextFirstTime:LeadsQueueNext[];nextDue:LeadsQueueDue[];batchSize:number;dueQueue:number;taken:number;batchFirst:number;batchRefresh:number;shortfall:number;padded:boolean;stuck:number;run:LeadsRunState|null;saved?:boolean;
 // 队列是跨渠道的：这两个字段让页面能说清两条渠道各占多少、以及有多少商品没有销量数据。
 byChannel?:{selected:number;campaign:number};unitsUnknown?:number;rolling?:RollingQueue};

const PID=/^\d{19}$/;

export function validateQueueConfig(value:unknown):LeadsQueueConfig{
 if(!value||typeof value!=="object")throw Error('invalid_leads_queue');
 const v=value as Record<string,unknown>;
 if(typeof v.version!=="string"||!v.version)throw Error('invalid_leads_queue');
 const bounded=(raw:unknown,low:number,high:number)=>{if(typeof raw!=="number"||!Number.isSafeInteger(raw)||raw<low||raw>high)throw Error('invalid_leads_queue');return raw;};
 return {version:v.version,refreshDays:bounded(v.refreshDays,1,90),
  leadsPerPid:bounded(v.leadsPerPid,1,50),windowDays:bounded(v.windowDays,1,30),
  batchSize:bounded(v.batchSize,1,5000),
  maxAttempts:bounded(v.maxAttempts,1,20)};
}

// A batch is never padded from products that are not yet due. If the other side ever reports
// otherwise that is a contract violation, not something to quietly pass through.
function mustBeFalse(value:unknown):false{
 if(value!==false)throw Error('invalid_leads_queue');
 return false;
}

function count(value:unknown):number{
 if(typeof value!=="number"||!Number.isSafeInteger(value)||value<0)throw Error('invalid_leads_queue');
 return value;
}

function lastFinished(value:unknown):LeadsLastFinished|null{
 if(value==null)return null;
 if(typeof value!=="object"||Array.isArray(value))throw Error('invalid_leads_queue');
 const v=value as Record<string,unknown>;
 const text=(raw:unknown)=>{if(typeof raw!=="string"||!raw||raw.length>120)throw Error('invalid_leads_queue');return raw;};
 const maybe=(raw:unknown)=>raw==null?null:count(raw);
 const time=(raw:unknown)=>typeof raw==="number"&&Number.isFinite(raw)?raw:null;
 if(typeof v.resultRecorded!=="boolean"||!Array.isArray(v.errors)||v.errors.length>3)throw Error('invalid_leads_queue');
 return {runId:text(v.runId),stageRunId:text(v.stageRunId),state:text(v.state),errorCode:v.errorCode==null?null:text(v.errorCode),
  startedAt:time(v.startedAt),finishedAt:time(v.finishedAt),resultRecorded:v.resultRecorded,completedQueries:maybe(v.completedQueries),
  aQueries:maybe(v.aQueries),bQueries:maybe(v.bQueries),fragments:maybe(v.fragments),networkRequests:maybe(v.networkRequests),
  sliceComplete:typeof v.sliceComplete==="boolean"?v.sliceComplete:null,errorCount:maybe(v.errorCount),errors:(v.errors as unknown[]).map(text),
  ...("contribution" in v?{contribution:v.contribution==null?null:(()=>{const c=v.contribution as Record<string,unknown>;if(!c||typeof c!=="object")throw Error('invalid_leads_queue');
   return {publications:count(c.publications),newPairs:count(c.newPairs),refreshedPairs:count(c.refreshedPairs),newCreators:count(c.newCreators),aPublications:count(c.aPublications),bPublications:count(c.bPublications)};})()}:{})};
}

function validateRun(value:unknown):LeadsRunState|null{
 if(value==null)return null;
 if(typeof value!=="object"||Array.isArray(value))throw Error('invalid_leads_queue');
 const v=value as Record<string,unknown>;
 if(typeof v.running!=="boolean")throw Error('invalid_leads_queue');
 const optional=(raw:unknown)=>(typeof raw==="number"&&Number.isFinite(raw)?raw:undefined);
 return {running:v.running,startedAt:optional(v.startedAt),finishedAt:optional(v.finishedAt),
  batchSize:optional(v.batchSize),dueQueue:optional(v.dueQueue),targets:optional(v.targets),
  done:optional(v.done),leads:optional(v.leads),networkRequests:optional(v.networkRequests),
  stopped:v.stopped==null?null:String(v.stopped),
  ...(typeof v.queued==="boolean"?{queued:v.queued}:{}),
  ...("lastFinished" in v?{lastFinished:lastFinished(v.lastFinished)}:{}),
  ...(Array.isArray(v.errors)?{errors:(v.errors as unknown[]).slice(0,20).map(item=>{const row=(item&&typeof item==="object"?item:{}) as Record<string,unknown>;return {pid:String(row.pid??""),code:String(row.code??"")};})}:{}),
  // A read-only batch must never report a platform write. If it ever does, refuse the payload.
  platformWrites:(()=>{if(v.platformWrites!==undefined&&v.platformWrites!==0)throw Error('invalid_leads_queue');return 0;})()};
}

export function validateLeadsQueue(value:unknown,expectedMarket:string):LeadsQueueState{
 if(!value||typeof value!=="object")throw Error('invalid_leads_queue');
 const v=value as Record<string,unknown>;
 if(v.market!==expectedMarket)throw Error('invalid_leads_queue');
 if(!Array.isArray(v.nextFirstTime)||!Array.isArray(v.nextDue))throw Error('invalid_leads_queue');
 if(v.nextFirstTime.length>20||v.nextDue.length>20)throw Error('invalid_leads_queue');
 const nextFirstTime=(v.nextFirstTime as Record<string,unknown>[]).map(row=>{
  if(!row||typeof row!=="object"||typeof row.pid!=="string"||!PID.test(row.pid))throw Error('invalid_leads_queue');
  return {pid:row.pid,units:count(row.units),title:typeof row.title==="string"?row.title:""};
 });
 const nextDue=(v.nextDue as Record<string,unknown>[]).map(row=>{
  if(!row||typeof row!=="object"||typeof row.pid!=="string"||!PID.test(row.pid))throw Error('invalid_leads_queue');
  if(typeof row.queriedAt!=="number"||typeof row.dueAt!=="number")throw Error('invalid_leads_queue');
  return {pid:row.pid,queriedAt:row.queriedAt,dueAt:row.dueAt,
   leads:row.leads==null?null:count(row.leads),title:typeof row.title==="string"?row.title:""};
 });
 return {market:expectedMarket,rolling:rollingQueue(v.rolling),config:validateQueueConfig(v.config),refreshDays:count(v.refreshDays),
  eligible:count(v.eligible),linked:count(v.linked),scope:count(v.scope),
  ...(v.byChannel&&typeof v.byChannel==="object"?{byChannel:{selected:count((v.byChannel as Record<string,unknown>).selected??0),campaign:count((v.byChannel as Record<string,unknown>).campaign??0)}}:{}),
  ...(typeof v.unitsUnknown==="number"?{unitsUnknown:count(v.unitsUnknown)}:{}),
  firstTime:count(v.firstTime),due:count(v.due),waiting:count(v.waiting),
  unknownScope:count(v.unknownScope),nextFirstTime,nextDue,
  batchSize:count(v.batchSize),dueQueue:count(v.dueQueue),taken:count(v.taken),
  batchFirst:count(v.batchFirst),batchRefresh:count(v.batchRefresh),shortfall:count(v.shortfall),
  stuck:count(v.stuck),run:validateRun(v.run),
  padded:mustBeFalse(v.padded),
  ...(typeof v.saved==="boolean"?{saved:v.saved}:{})};
}

function runQueue(args:string[],market:string):Promise<LeadsQueueState>{
 const root=projectRoot();
 return new Promise((resolve,reject)=>{
  execFile(join(root,".venv/bin/python"),[join(root,"scripts/leads-queue.py"),...args],
   {cwd:root,timeout:60000,maxBuffer:2*1024*1024,env:{...process.env,PYTHONDONTWRITEBYTECODE:"1"}},(error,out)=>{
    try{resolve(validateLeadsQueue(JSON.parse(out),market));}
    catch{reject(Error('leads_queue_unavailable'));}
   });
 });
}

export function readLeadsQueue(market:string):Promise<LeadsQueueState>{return runQueue(["status","--market",market],market);}
export function saveLeadsQueue(market:string,config:LeadsQueueConfig):Promise<LeadsQueueState>{return runQueue(["save","--market",market,"--json",JSON.stringify(config)],market);}

export function validateLeadsQueueRequest(value:unknown):{action:"save";market:string;config:LeadsQueueConfig}|{action:"run";market:string}{
 if(!value||typeof value!=="object")throw Error('invalid_leads_queue_request');
 const v=value as Record<string,unknown>;
 if(typeof v.market!=="string"||!/^[a-z]{2}$/.test(v.market))throw Error('invalid_leads_queue_request');
 if(v.action==="run"){if(Object.keys(v).some(key=>key!=="action"&&key!=="market"))throw Error('invalid_leads_queue_request');return {action:"run",market:v.market};}
 if(v.action!=="save")throw Error('invalid_leads_queue_request');
 // Out-of-range ages are a bad request, not an unavailable service.
 if(Object.keys(v).some(key=>!['action','market','config'].includes(key)))throw Error('invalid_leads_queue_request');
 try{return {action:"save",market:v.market,config:validateQueueConfig(v.config)};}
 catch{throw Error('invalid_leads_queue_request');}
}

/** Start one batch detached; the page follows ``run`` in the status payload. */
export function startLeadsRun(market:string):Promise<LeadsQueueState>{
 return runQueue(["run","--market",market],market);
}
