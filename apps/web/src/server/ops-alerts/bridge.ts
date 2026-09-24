import {execFile} from "node:child_process";
import {join} from "node:path";
import {projectRoot} from "../runtime/project-root.ts";
import {isLocalRequest} from "../runtime/validation.ts";
import {singleflight} from "../runtime/singleflight.ts";

export type OpsAlertLevel="critical"|"warning"|"info";
export type OpsAlert={id:string;level:OpsAlertLevel;market:string|null;title:string;detail:string;since:number|null;href:string|null};
export type OpsAlerts={schemaVersion:"bdhub.ops-alerts.v1";checkedAt:number;alerts:OpsAlert[];readOnly:true;platformWrites:0};

const LEVELS=new Set<string>(["critical","warning","info"]);
const fail=():never=>{throw Error("invalid_ops_alerts");};
const object=(value:unknown)=>value&&typeof value==="object"&&!Array.isArray(value)?value as Record<string,unknown>:fail();
const text=(value:unknown,max:number)=>typeof value==="string"&&value.length>0&&value.length<=max?value:fail();
const stamp=(value:unknown)=>value==null?null:typeof value==="number"&&Number.isFinite(value)&&value>0?value:fail();
// Links stay inside this app: a market prefix and plain path segments, never a scheme or protocol-relative URL.
const href=(value:unknown)=>value==null?null:typeof value==="string"&&/^\/[a-z]{2}(\/[a-z-]+)*$/.test(value)?value:fail();

export function validateOpsAlerts(raw:unknown):OpsAlerts{
 const value=object(raw);
 if(value.schemaVersion!=="bdhub.ops-alerts.v1"||value.readOnly!==true||value.platformWrites!==0||!Array.isArray(value.alerts)||value.alerts.length>200)fail();
 const alerts=(value.alerts as unknown[]).map(item=>{const row=object(item);if(typeof row.level!=="string"||!LEVELS.has(row.level))fail();
  return {id:text(row.id,120),level:row.level as OpsAlertLevel,market:row.market==null?null:text(row.market,8),title:text(row.title,120),
   detail:text(row.detail,400),since:stamp(row.since),href:href(row.href)};});
 return {schemaVersion:"bdhub.ops-alerts.v1",checkedAt:stamp(value.checkedAt)??fail(),alerts,readOnly:true,platformWrites:0};
}

function run():Promise<OpsAlerts>{const root=projectRoot();return new Promise((resolve,reject)=>execFile(join(root,".venv/bin/python"),[join(root,"scripts/ops-alerts.py")],{cwd:root,timeout:30_000,maxBuffer:1024*1024,env:{...process.env,PYTHONDONTWRITEBYTECODE:"1"}},(error,stdout)=>{try{const value=JSON.parse(stdout);if(error||value?.error)throw Error(String(value?.error||"ops_alerts_unavailable"));resolve(validateOpsAlerts(value));}catch(caught){reject(caught);}}));}

const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};
export const handlers={GET:async(request:Request)=>{
 if(!isLocalRequest(request,false))return Response.json({error:"local_origin_required"},{status:403,headers});
 if(new URL(request.url).search)return Response.json({error:"invalid_query"},{status:400,headers});
 try{return Response.json(await singleflight("ops-alerts",run),{headers});}catch{return Response.json({error:"ops_alerts_unavailable"},{status:503,headers});}
}};
