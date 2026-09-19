import {execFile} from "node:child_process";
import {join} from "node:path";
import {projectRoot} from "../creator-identities/refresh.ts";

/** One schedulable job: what it does, whether it can be started by hand, and the operator's intent. */
export type WorkbenchJob={id:string;name:string;group:string;description:string;manual:string;manualEndpoint:string|null;lastRunAt:number|null;enabled:boolean;at:string|null;schedulable:boolean;cadence:"daily"|"weekly";weekday:number|null};
export type SchedulerState={running:boolean;stopping:boolean;pid:number|null;startedAt:number|null;phase:string|null;cycle:string|null;checkedAt:number|null;lastSuccess:Record<string,number>;lastAttempt:Record<string,number>;nextDue:Record<string,number>;error:string|null};
export type JobsState={version:string;jobs:WorkbenchJob[];schedulerReady:boolean;scheduler:SchedulerState;saved?:boolean};
export type JobsSave={jobs:Record<string,{enabled?:boolean;at?:string|null;weekday?:number}>};

const ID=/^[a-z_]{1,40}$/,TIME=/^([01]\d|2[0-3]):[0-5]\d$/;

function validateJob(value:unknown):WorkbenchJob{
 if(!value||typeof value!=="object")throw Error('invalid_jobs');
 const v=value as Record<string,unknown>;
 if(typeof v.id!=="string"||!ID.test(v.id)||typeof v.name!=="string"||!v.name)throw Error('invalid_jobs');
 if(typeof v.group!=="string"||typeof v.description!=="string"||typeof v.manual!=="string")throw Error('invalid_jobs');
 if(v.manualEndpoint!==null&&typeof v.manualEndpoint!=="string")throw Error('invalid_jobs');
 if(v.lastRunAt!==null&&!(typeof v.lastRunAt==="number"&&Number.isFinite(v.lastRunAt)))throw Error('invalid_jobs');
 if(typeof v.enabled!=="boolean")throw Error('invalid_jobs');
 if(typeof v.schedulable!=="boolean"||(v.cadence!=="daily"&&v.cadence!=="weekly"))throw Error('invalid_jobs');
 if(v.at!==null&&(typeof v.at!=="string"||!TIME.test(v.at)))throw Error('invalid_jobs');
 if(v.weekday!==null&&!(typeof v.weekday==="number"&&Number.isSafeInteger(v.weekday)&&v.weekday>=0&&v.weekday<=6))throw Error('invalid_jobs');
 return {id:v.id,name:v.name,group:v.group,description:v.description,manual:v.manual,
  manualEndpoint:v.manualEndpoint as string|null,
  lastRunAt:v.lastRunAt as number|null,enabled:v.enabled,at:v.at as string|null,
  schedulable:v.schedulable,cadence:v.cadence,weekday:v.weekday as number|null};
}

function validateScheduler(raw:unknown):SchedulerState{
 if(!raw||typeof raw!=="object"||Array.isArray(raw))throw Error('invalid_jobs');const v=raw as Record<string,unknown>;
 const nullableNumber=(value:unknown)=>value==null?null:typeof value==="number"&&Number.isFinite(value)&&value>=0?value:(()=>{throw Error('invalid_jobs')})();
 const map=(value:unknown)=>{if(!value||typeof value!=="object"||Array.isArray(value))throw Error('invalid_jobs');const out:Record<string,number>={};for(const [key,stamp] of Object.entries(value as Record<string,unknown>)){const parsed=nullableNumber(stamp);if(parsed==null)throw Error('invalid_jobs');out[key]=parsed;}return out;};
 if(typeof v.running!=="boolean"||typeof v.stopping!=="boolean")throw Error('invalid_jobs');
 const pid=v.pid==null?null:typeof v.pid==="number"&&Number.isSafeInteger(v.pid)&&v.pid>0?v.pid:(()=>{throw Error('invalid_jobs')})();
 return {running:v.running,stopping:v.stopping,pid,startedAt:nullableNumber(v.startedAt),
  phase:v.phase==null?null:String(v.phase),cycle:v.cycle==null?null:String(v.cycle),checkedAt:nullableNumber(v.checkedAt),
  lastSuccess:map(v.lastSuccess),lastAttempt:map(v.lastAttempt),nextDue:map(v.nextDue),error:v.error==null?null:String(v.error)};
}

export function validateJobs(value:unknown):JobsState{
 if(!value||typeof value!=="object")throw Error('invalid_jobs');
 const v=value as Record<string,unknown>;
 if(typeof v.version!=="string"||!v.version)throw Error('invalid_jobs');
 if(!Array.isArray(v.jobs)||v.jobs.length===0||v.jobs.length>40)throw Error('invalid_jobs');
 const jobs=(v.jobs as unknown[]).map(validateJob);
 if(new Set(jobs.map(job=>job.id)).size!==jobs.length)throw Error('invalid_jobs');
 return {version:v.version,jobs,schedulerReady:v.schedulerReady===true,scheduler:validateScheduler(v.scheduler),
  ...(typeof v.saved==="boolean"?{saved:v.saved}:{})};
}

export function validateJobsSave(value:unknown):JobsSave{
 if(!value||typeof value!=="object")throw Error('invalid_jobs_request');
 const v=value as Record<string,unknown>;
 if(v.action!=="save"||!v.jobs||typeof v.jobs!=="object"||Array.isArray(v.jobs))throw Error('invalid_jobs_request');
 const out:JobsSave={jobs:{}};
 for(const [id,raw] of Object.entries(v.jobs as Record<string,unknown>)){
  if(!ID.test(id)||!raw||typeof raw!=="object"||Array.isArray(raw))throw Error('invalid_jobs_request');
  const entry=raw as Record<string,unknown>;
  const clean:{enabled?:boolean;at?:string|null;weekday?:number}={};
  if(entry.enabled!==undefined){
   if(typeof entry.enabled!=="boolean")throw Error('invalid_jobs_request');
   clean.enabled=entry.enabled;
  }
  if(entry.at!==undefined){
   if(entry.at!==null&&(typeof entry.at!=="string"||!TIME.test(entry.at)))throw Error('invalid_jobs_request');
   clean.at=entry.at as string|null;
  }
  if(entry.weekday!==undefined){if(typeof entry.weekday!=="number"||!Number.isSafeInteger(entry.weekday)||entry.weekday<0||entry.weekday>6)throw Error('invalid_jobs_request');clean.weekday=entry.weekday;}
  if(Object.keys(entry).some(key=>!['enabled','at','weekday'].includes(key)))throw Error('invalid_jobs_request');
  if(Object.keys(clean).length===0)throw Error('invalid_jobs_request');
  out.jobs[id]=clean;
 }
 if(Object.keys(out.jobs).length===0)throw Error('invalid_jobs_request');
 return out;
}

function runJobs(args:string[]):Promise<JobsState>{
 const root=projectRoot();
 return new Promise((resolve,reject)=>{
  execFile(join(root,".venv/bin/python"),[join(root,"scripts/jobs.py"),...args],
   {cwd:root,timeout:30000,maxBuffer:1024*1024,env:{...process.env,PYTHONDONTWRITEBYTECODE:"1"}},(error,out)=>{
    try{const parsed=JSON.parse(out);if(parsed&&typeof parsed==="object"&&typeof parsed.error==="string"){reject(Error(parsed.error));return;}resolve(validateJobs(parsed));}
    catch{reject(Error('jobs_unavailable'));}
   });
 });
}

export function readJobs():Promise<JobsState>{return runJobs(["status"]);}
export function saveJobs(payload:JobsSave):Promise<JobsState>{return runJobs(["save","--json",JSON.stringify(payload)]);}
export function startJobsScheduler():Promise<JobsState>{return runJobs(["start-scheduler"]);}
export function stopJobsScheduler():Promise<JobsState>{return runJobs(["stop-scheduler"]);}

export type JobsRequest={action:"save";payload:JobsSave}|{action:"start_scheduler"}|{action:"stop_scheduler"};
export function validateJobsRequest(value:unknown):JobsRequest{
 if(!value||typeof value!=="object"||Array.isArray(value))throw Error('invalid_jobs_request');const v=value as Record<string,unknown>;
 if(v.action==="start_scheduler"||v.action==="stop_scheduler"){
  if(Object.keys(v).length!==1)throw Error('invalid_jobs_request');return {action:v.action};
 }
 return {action:"save",payload:validateJobsSave(value)};
}
