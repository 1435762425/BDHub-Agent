import {execFile} from "node:child_process";
import {join} from "node:path";
import {projectRoot} from "../creator-identities/refresh.ts";
import {isLocalRequest} from "../runtime/validation.ts";

export type CycleStatus={available?:boolean;executionAllowed:false;relationships?:number;sourceEdges?:number;opportunities?:number;eligibleUniqueCreators?:number;jobs?:Record<string,number>;offerCount?:number;eligibleOfferCount?:number;offers?:Array<{title?:string;pid:string;offerKey?:string;catalogSource?:string;commissionState?:string;assessment:{eligible:boolean;reasons:string[]}}>};
export function validateCycle(value:unknown):CycleStatus {
  if(!value||typeof value!=="object"||!("executionAllowed" in value)||value.executionAllowed!==false)throw new Error("invalid_cycle_status");
  const v=value as CycleStatus;
  if(v.available===false)return {available:false,executionAllowed:false};
  for(const key of ["relationships","sourceEdges","opportunities","eligibleUniqueCreators"] as const)if(!Number.isSafeInteger(v[key])||Number(v[key])<0)throw new Error("invalid_cycle_count");
  if(!Array.isArray(v.offers)||v.offers.length>10000||!v.offers.every(o=>o&&typeof o.pid==="string"&&(!o.title||typeof o.title==="string")&&o.assessment&&typeof o.assessment.eligible==="boolean"&&Array.isArray(o.assessment.reasons)&&o.assessment.reasons.every(r=>typeof r==="string")))throw new Error("invalid_cycle_offers");
  return v;
}
export function readCycle():Promise<CycleStatus>{const root=projectRoot();return new Promise((resolve,reject)=>{execFile(join(root,"../01-BDSystem-V2/.venv/bin/python"),[join(root,"scripts/second-cycle.py"),"status"],{cwd:root,timeout:15000,maxBuffer:2*1024*1024,env:{...process.env,PYTHONDONTWRITEBYTECODE:"1"}},(error,stdout)=>{try{if(error)throw error;resolve(validateCycle(JSON.parse(stdout)));}catch{reject(new Error("cycle_unavailable"));}});});}
export function createCycleGet(read=readCycle){return async(request:Request)=>{
 const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};
 if(!isLocalRequest(request,false))return Response.json({error:"local_origin_required"},{status:403,headers});
 if(new URL(request.url).search)return Response.json({error:"invalid_query"},{status:400,headers});
 try{return Response.json(validateCycle(await read()),{headers});}catch{return Response.json({error:"cycle_unavailable"},{status:503,headers});}
};}
