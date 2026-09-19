import {execFile} from "node:child_process";
import {join} from "node:path";
import {projectRoot} from "../creator-identities/refresh.ts";

/** One schedulable job: what it does, whether it can be started by hand, and the operator's intent. */
export type WorkbenchJob={id:string;name:string;group:string;description:string;manual:string;manualEndpoint:string|null;lastRunAt:number|null;enabled:boolean;at:string|null};
export type JobsState={version:string;jobs:WorkbenchJob[];schedulerReady:boolean;saved?:boolean};
export type JobsSave={jobs:Record<string,{enabled?:boolean;at?:string|null}>};

const ID=/^[a-z_]{1,40}$/,TIME=/^([01]\d|2[0-3]):[0-5]\d$/;

function validateJob(value:unknown):WorkbenchJob{
 if(!value||typeof value!=="object")throw Error('invalid_jobs');
 const v=value as Record<string,unknown>;
 if(typeof v.id!=="string"||!ID.test(v.id)||typeof v.name!=="string"||!v.name)throw Error('invalid_jobs');
 if(typeof v.group!=="string"||typeof v.description!=="string"||typeof v.manual!=="string")throw Error('invalid_jobs');
 if(v.manualEndpoint!==null&&typeof v.manualEndpoint!=="string")throw Error('invalid_jobs');
 if(v.lastRunAt!==null&&!(typeof v.lastRunAt==="number"&&Number.isFinite(v.lastRunAt)))throw Error('invalid_jobs');
 if(typeof v.enabled!=="boolean")throw Error('invalid_jobs');
 if(v.at!==null&&(typeof v.at!=="string"||!TIME.test(v.at)))throw Error('invalid_jobs');
 return {id:v.id,name:v.name,group:v.group,description:v.description,manual:v.manual,
  manualEndpoint:v.manualEndpoint as string|null,
  lastRunAt:v.lastRunAt as number|null,enabled:v.enabled,at:v.at as string|null};
}

export function validateJobs(value:unknown):JobsState{
 if(!value||typeof value!=="object")throw Error('invalid_jobs');
 const v=value as Record<string,unknown>;
 if(typeof v.version!=="string"||!v.version)throw Error('invalid_jobs');
 if(!Array.isArray(v.jobs)||v.jobs.length===0||v.jobs.length>40)throw Error('invalid_jobs');
 const jobs=(v.jobs as unknown[]).map(validateJob);
 if(new Set(jobs.map(job=>job.id)).size!==jobs.length)throw Error('invalid_jobs');
 return {version:v.version,jobs,schedulerReady:v.schedulerReady===true,
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
  const clean:{enabled?:boolean;at?:string|null}={};
  if(entry.enabled!==undefined){
   if(typeof entry.enabled!=="boolean")throw Error('invalid_jobs_request');
   clean.enabled=entry.enabled;
  }
  if(entry.at!==undefined){
   if(entry.at!==null&&(typeof entry.at!=="string"||!TIME.test(entry.at)))throw Error('invalid_jobs_request');
   clean.at=entry.at as string|null;
  }
  if(Object.keys(clean).length===0)throw Error('invalid_jobs_request');
  out.jobs[id]=clean;
 }
 if(Object.keys(out.jobs).length===0)throw Error('invalid_jobs_request');
 return out;
}

function runJobs(args:string[]):Promise<JobsState>{
 const root=projectRoot();
 return new Promise((resolve,reject)=>{
  execFile(join(root,"../01-BDSystem-V2/.venv/bin/python"),[join(root,"scripts/jobs.py"),...args],
   {cwd:root,timeout:30000,maxBuffer:1024*1024,env:{...process.env,PYTHONDONTWRITEBYTECODE:"1"}},(error,out)=>{
    try{resolve(validateJobs(JSON.parse(out)));}
    catch{reject(Error('jobs_unavailable'));}
   });
 });
}

export function readJobs():Promise<JobsState>{return runJobs(["status"]);}
export function saveJobs(payload:JobsSave):Promise<JobsState>{return runJobs(["save","--json",JSON.stringify(payload)]);}
