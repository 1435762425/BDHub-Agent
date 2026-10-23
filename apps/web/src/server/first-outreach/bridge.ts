import {execFile} from "node:child_process";
import {join} from "node:path";
import {projectRoot} from "../runtime/project-root.ts";
import type {FirstOutreachAction,FirstOutreachRequest,FirstOutreachState} from "../../features/first-outreach/contracts.ts";

function object(value:unknown):Record<string,unknown>{if(!value||typeof value!=="object"||Array.isArray(value))throw Error("invalid_first_outreach");return value as Record<string,unknown>;}
function keys(value:Record<string,unknown>,required:string[],optional:string[]=[]){if(required.some(key=>!Object.hasOwn(value,key))||Object.keys(value).some(key=>!required.includes(key)&&!optional.includes(key)))throw Error("invalid_first_outreach");}
function text(value:unknown,max=160,empty=false):string{if(typeof value!=="string"||(!empty&&!value)||value.length>max)throw Error("invalid_first_outreach");return value;}
function count(value:unknown):number{if(typeof value!=="number"||!Number.isSafeInteger(value)||value<0)throw Error("invalid_first_outreach");return value;}
function timestamp(value:unknown):number{if(typeof value!=="number"||!Number.isFinite(value)||value<0)throw Error("invalid_first_outreach");return value;}
const nullableTime=(value:unknown)=>value===null?null:timestamp(value);
const nullableText=(value:unknown,max=500)=>value===null?null:text(value,max);
const actions:FirstOutreachAction[]=["prepare","sync","start","continue","stop","set-approvals","reconcile"];

export function validateFirstOutreach(raw:unknown):FirstOutreachState{
 const value=object(raw);keys(value,["schemaVersion","market","control","readiness","applicationCoverage","counts","candidates","applications","runtime","platformWrites"],["sourceUpdatedAt"]);
 if(value.schemaVersion!=="bdhub.first-outreach.v1"||value.market!=="br"||value.platformWrites!==0||value.applicationCoverage!=="unverified"&&value.applicationCoverage!=="complete")throw Error("invalid_first_outreach");
 const c=object(value.control);keys(c,["revision","mode","approvalsEnabled","updatedAt"]);
 if(!["stopped","canary","continuous"].includes(String(c.mode))||typeof c.approvalsEnabled!=="boolean")throw Error("invalid_first_outreach");
 const r=object(value.readiness);keys(r,["ready","blockers"]);
 if(typeof r.ready!=="boolean"||!Array.isArray(r.blockers)||r.blockers.length>100||r.ready&&r.blockers.length>0)throw Error("invalid_first_outreach");
 const totals=object(value.counts);keys(totals,["products","matchedCreators","sent","pendingApplications","approved","needsReview","unknown"]);
 const counts={products:count(totals.products),matchedCreators:count(totals.matchedCreators),sent:count(totals.sent),pendingApplications:count(totals.pendingApplications),approved:count(totals.approved),needsReview:count(totals.needsReview),unknown:count(totals.unknown)};
 if(!Array.isArray(value.candidates)||value.candidates.length>500||!Array.isArray(value.applications)||value.applications.length>500)throw Error("invalid_first_outreach");
 const candidates=value.candidates.map(item=>{const row=object(item);keys(row,["creatorId","handle","pid","productName","categoryName","gmv","message"]);const gmv=nullableText(row.gmv,80);if(gmv!==null&&!/^\d+(?:\.\d+)?$/.test(gmv))throw Error("invalid_first_outreach");return {creatorId:text(row.creatorId,120),handle:text(row.handle,100),pid:text(row.pid,40),productName:text(row.productName,500),categoryName:text(row.categoryName,200),gmv,message:text(row.message,4000)};});
 const applications=value.applications.map(item=>{const row=object(item);keys(row,["id","handle","pid","productName","state","reason"]);return {id:text(row.id,160),handle:text(row.handle,100),pid:text(row.pid,40),productName:text(row.productName,500),state:text(row.state,80),reason:nullableText(row.reason)};});
 const runtime=object(value.runtime);keys(runtime,["state","reason","updatedAt"]);
 return {schemaVersion:"bdhub.first-outreach.v1",market:"br",control:{revision:count(c.revision),mode:c.mode as FirstOutreachState["control"]["mode"],approvalsEnabled:c.approvalsEnabled,updatedAt:timestamp(c.updatedAt)},readiness:{ready:r.ready,blockers:r.blockers.map(item=>text(item,500))},applicationCoverage:value.applicationCoverage,counts,candidates,applications,runtime:{state:text(runtime.state,100),reason:nullableText(runtime.reason),updatedAt:nullableTime(runtime.updatedAt)},platformWrites:0,...(value.sourceUpdatedAt===undefined?{}:{sourceUpdatedAt:nullableTime(value.sourceUpdatedAt)})};
}

export function validateFirstOutreachRequest(raw:unknown):FirstOutreachRequest{
 try{
  const value=object(raw);keys(value,["action","market","requestId","expectedRevision"],value.action==="set-approvals"?["approvalsEnabled"]:[]);
  if(value.market!=="br"||!actions.includes(value.action as FirstOutreachAction))throw Error();
  const requestId=text(value.requestId,120);if(!/^[A-Za-z0-9][A-Za-z0-9._:-]{7,119}$/.test(requestId))throw Error();
  const request:FirstOutreachRequest={action:value.action as FirstOutreachAction,market:"br",requestId,expectedRevision:count(value.expectedRevision)};
  if(value.action==="set-approvals"){if(typeof value.approvalsEnabled!=="boolean")throw Error();request.approvalsEnabled=value.approvalsEnabled;}
  return request;
 }catch{throw Error("invalid_first_outreach_request");}
}

export function firstOutreachArgv(root:string,action:"status"|FirstOutreachAction,body?:FirstOutreachRequest):string[]{
 return [join(root,"scripts/first-outreach.py"),action,"--market","br",...(body?["--json",JSON.stringify(body)]:[])];
}
function run(action:"status"|FirstOutreachAction,body?:FirstOutreachRequest):Promise<FirstOutreachState>{
 const root=projectRoot();
 return new Promise((resolve,reject)=>execFile(join(root,".venv/bin/python"),firstOutreachArgv(root,action,body),{cwd:root,timeout:60_000,maxBuffer:4*1024*1024,env:{...process.env,PYTHONDONTWRITEBYTECODE:"1"}},(error,stdout)=>{
  try{const value=JSON.parse(stdout);if(error||value?.error)throw Error(typeof value?.error==="string"&&/^[a-z0-9_:-]{1,100}$/.test(value.error)?value.error:"first_outreach_unavailable");resolve(validateFirstOutreach(value));}catch(error){reject(error);}
 }));
}
export const readFirstOutreach=()=>run("status");
export const invokeFirstOutreach=(request:FirstOutreachRequest)=>run(request.action,request);
