import {execFile} from "node:child_process";
import {join} from "node:path";
import {projectRoot} from "../creator-identities/refresh.ts";

/** The two catalogue actions that actually do platform work, and whether each is running. */
export type JobConfig={limit?:number;readLimit?:number;creates?:number;lanes?:number;qps?:number;maxRequests?:number;passes?:number};
// A running batch publishes the step it is on. ``phase`` says which stage it is in; ``pass`` is
// only meaningful for the read stage, which repeats the same bounded pass many times.
export type JobProgress={phase:string;step:number;pass:number|null;passes:number;created:number;updatedAt:number;startedAt:number;total:number;states:Record<string,number>;
 // 采集作业发布的是另一种形状：读到第几轮、花了多少请求、拿了多少 offer。
 status?:string;round?:number;rounds?:number;requests?:number;offers?:number;error?:string|null};
export type JobRun={name:string;label:string;pid:number;startedAt:number;log:string;config:JobConfig;platformWrites:boolean;running:boolean;progress:JobProgress|null};
export type CatalogJobsState={selection:{label:string;config:JobConfig;run:JobRun|null};links:{label:string;config:JobConfig;run:JobRun|null};linksCampaign:{label:string;config:JobConfig;run:JobRun|null};campaignCollect:{label:string;config:JobConfig;run:JobRun|null}};

const NAMES=new Set(["selection","links","linksCampaign","campaignCollect"]);
const JOB_NAMES=["selection","links","linksCampaign","campaignCollect"] as const;
// 非全托链接作业没有 route 配置项：渠道由作业名钉死，所以两条渠道不可能彼此带错渠道。
type JobName=(typeof JOB_NAMES)[number];
// Lanes and rate are a closed set on the reader side, so they are checked as a set here too:
// a value inside the numeric range but outside the set would otherwise reach the driver and fail
// there as an unavailable service instead of a bad request.
const CHOICES:Record<string,Record<string,number[]>>={links:{lanes:[1,3,6,9],qps:[3,5,8,12]},
 linksCampaign:{lanes:[1,3,6,9],qps:[3,5,8,12]}};
const BOUNDS:Record<string,Record<string,[number,number]>>={
 selection:{limit:[1,600]},
 // Same-account HTTP lanes and the request rate they share. Creation ran on a single lane
 // until the driver was told otherwise, which is what kept it far below its measured rate.
 links:{readLimit:[1,200],creates:[0,200],lanes:[1,9],qps:[3,12]},
 linksCampaign:{readLimit:[1,200],creates:[0,200],lanes:[1,9],qps:[3,12]},
 // 非全托采集：只读平台，每轮最多 150 个请求（脚本自己的上限）。
 campaignCollect:{maxRequests:[1,150],passes:[1,200]},
};

export function validateJobConfig(name:string,value:unknown):JobConfig{
 if(!NAMES.has(name))throw Error('invalid_catalog_jobs');
 if(!value||typeof value!=="object"||Array.isArray(value))throw Error('invalid_catalog_jobs');
 const out:JobConfig={};
 for(const [key,raw] of Object.entries(value as Record<string,unknown>)){
  const bounds=BOUNDS[name][key];
  if(!bounds)throw Error('invalid_catalog_jobs');
  if(typeof raw!=="number"||!Number.isSafeInteger(raw)||raw<bounds[0]||raw>bounds[1])throw Error('invalid_catalog_jobs');
  const choices=CHOICES[name]?.[key];
  if(choices&&!choices.includes(raw))throw Error('invalid_catalog_jobs');
  (out as Record<string,number>)[key]=raw;
 }
 return out;
}

function validateProgress(value:unknown):JobProgress|null{
 // Absent progress is normal: a job that has not published its first step yet has none. A present
 // one is checked strictly, because a progress bar drawn from a half-written file would lie.
 if(value==null)return null;
 if(typeof value!=="object"||Array.isArray(value))throw Error('invalid_catalog_jobs');
 const v=value as Record<string,unknown>;
 const whole=(raw:unknown)=>{if(typeof raw!=="number"||!Number.isSafeInteger(raw)||raw<0)throw Error('invalid_catalog_jobs');return raw;};
 const clock=(raw:unknown)=>{if(typeof raw!=="number"||!Number.isFinite(raw)||raw<0)throw Error('invalid_catalog_jobs');return raw;};
 const states=v.states;
 // 采集作业发布的是另一种形状（status/round/requests/offers），没有 phase/step/states。
 // 按**形状**分派而不是按作业名：进度文件写坏时两种形状都必须被拒，不能被硬套成其中一种。
 if(typeof v.status==="string"){
  return {phase:"collect",step:0,pass:null,passes:whole(v.rounds),created:0,
   updatedAt:clock(v.updatedAt),startedAt:clock(v.startedAt),total:0,states:{},
   status:v.status,round:whole(v.round),rounds:whole(v.rounds),
   requests:whole(v.requests),offers:whole(v.offers)};
 }
 if(!states||typeof states!=="object"||Array.isArray(states))throw Error('invalid_catalog_jobs');
 const counts:Record<string,number>={};
 for(const [key,raw] of Object.entries(states as Record<string,unknown>))counts[key]=whole(raw);
 return {phase:typeof v.phase==="string"?v.phase:"",step:whole(v.step),pass:v.pass==null?null:whole(v.pass),
  passes:whole(v.passes),created:whole(v.created),updatedAt:clock(v.updatedAt),startedAt:clock(v.startedAt),
  total:whole(v.total),states:counts};
}

function validateRun(value:unknown,fallback:JobConfig,name:string):JobRun|null{
 if(value==null)return null;
 if(typeof value!=="object")throw Error('invalid_catalog_jobs');
 const v=value as Record<string,unknown>;
 if(typeof v.name!=="string"||!NAMES.has(v.name)||typeof v.running!=="boolean")throw Error('invalid_catalog_jobs');
 if(typeof v.pid!=="number"||typeof v.label!=="string")throw Error('invalid_catalog_jobs');
 // A run record is history: a config that no longer validates must not blank the job panel.
 let runConfig:JobConfig;try{runConfig=validateJobConfig(name,v.config);}catch{runConfig=fallback;}
 return {name:v.name,label:v.label,pid:v.pid,
  startedAt:typeof v.startedAt==="number"?v.startedAt:0,
  log:typeof v.log==="string"?v.log:"",
  config:runConfig,platformWrites:v.platformWrites===true,running:v.running,
  progress:validateProgress(v.progress)};
}

export function validateCatalogJobs(value:unknown):CatalogJobsState{
 if(!value||typeof value!=="object")throw Error('invalid_catalog_jobs');
 const v=value as Record<string,unknown>;
 const out:Partial<CatalogJobsState>={};
 for(const name of JOB_NAMES){
  const entry=v[name];
  if(!entry||typeof entry!=="object")throw Error('invalid_catalog_jobs');
  const row=entry as Record<string,unknown>;
  const config=validateJobConfig(name,row.config);
  out[name]={label:typeof row.label==="string"?row.label:name,
   config,run:validateRun(row.run,config,name)};
 }
 return out as CatalogJobsState;
}

function runJobs(args:string[]):Promise<CatalogJobsState>{
 const root=projectRoot();
 return new Promise((resolve,reject)=>{
  execFile(join(root,".venv/bin/python"),[join(root,"scripts/job-run.py"),...args],
   {cwd:root,timeout:60000,maxBuffer:1024*1024,env:{...process.env,PYTHONDONTWRITEBYTECODE:"1"}},(error,out)=>{
    let parsed:unknown;
    try{parsed=JSON.parse(out);}
    catch{reject(Error('catalog_jobs_unavailable'));return;}
    // A refusal the operator can act on (for example "already running") keeps its own code.
    if(parsed&&typeof parsed==="object"&&Object.keys(parsed).length===1&&typeof (parsed as {error?:unknown}).error==="string"&&(parsed as {error:string}).error!==""){
     reject(Error((parsed as {error:string}).error));return;
    }
    try{resolve(validateCatalogJobs(parsed));}
    catch{reject(Error('catalog_jobs_unavailable'));}
   });
 });
}

export function readCatalogJobs():Promise<CatalogJobsState>{return runJobs(["status"]);}
export function saveCatalogJob(name:JobName,config:JobConfig):Promise<CatalogJobsState>{
 return runJobs(["save","--name",name,"--json",JSON.stringify(config)]);
}
export function stopCatalogJob(name:JobName):Promise<CatalogJobsState>{
 return runJobs(["stop","--name",name]);
}
export function startCatalogJob(name:JobName,config?:JobConfig):Promise<CatalogJobsState>{
 return runJobs(["start","--name",name,...(config?["--json",JSON.stringify(config)]:[])],);
}

export function validateCatalogJobsRequest(value:unknown):
 {action:"save";name:JobName;config:JobConfig}|{action:"start";name:JobName;config?:JobConfig}|{action:"stop";name:JobName}{
 if(!value||typeof value!=="object")throw Error('invalid_catalog_jobs_request');
 const v=value as Record<string,unknown>;
 const name=v.name;
 if(typeof name!=="string"||!JOB_NAMES.includes(name as JobName))throw Error('invalid_catalog_jobs_request');
 const job=name as JobName;
 if(v.action!=="start"&&v.action!=="save"&&v.action!=="stop")throw Error('invalid_catalog_jobs_request');
 // A bad config inside a request is a bad request, not an unavailable service.
 try{
  if(v.action==="start")return {action:"start",name:job,...(v.config===undefined?{}:{config:validateJobConfig(job,v.config)})};
  // 停止只写一个停止请求文件，驱动器在两次推进之间安全退出。它**不接受**任何其它字段：
  // 多带一个（比如顺手带上的配置）就是坏请求，静默忽略正是这个代码库一直在防的事。
  if(v.action==="stop"){
   if(Object.keys(v).some(key=>key!=="action"&&key!=="name"))throw Error('invalid_catalog_jobs_request');
   return {action:"stop",name:job};
  }
  return {action:"save",name:job,config:validateJobConfig(job,v.config)};
 }catch{throw Error('invalid_catalog_jobs_request');}
}
