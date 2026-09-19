import {execFile} from "node:child_process";
import {join} from "node:path";
import {projectRoot} from "../creator-identities/refresh.ts";

/** Counts in 达人×商品 units, plus the four layers the pool is split into. */
export type LeadPoolCounts={leads:number;merged:number;unresolved:number;queued:number;positions:number;
 creators:number;handles:number;sent:number;unsent:number;ready:number;readyCreators:number;
 queuedPositions:number;cooling:number;awaitingReply:number;excluded:number;creatorsWithRelationship:number};
export type LeadPosition={creatorId:string;handle:string;pid:string;rank:number|null;units:number|null;
 unlocked:boolean;sentAt:number|null;readyAt:number|null;layer:string;caseUpdatedAt?:number|null};
export type LeadPoolState={available:boolean;now?:number;counts:LeadPoolCounts;
 cooldown:{unlocked:number;locked:number};layers:Record<string,number>;pools:Record<string,LeadPosition[]>};

const LAYERS=new Set(["ready","queued","cooling","awaiting_reply","excluded","sent"]);

function count(value:unknown,name:string):number{
 if(typeof value!=="number"||!Number.isSafeInteger(value)||value<0)throw Error('invalid_lead_pool');
 return value;
}

function validatePositions(value:unknown):LeadPosition[]{
 if(!Array.isArray(value)||value.length>200)throw Error('invalid_lead_pool');
 return value.map(raw=>{
  const row=raw as Record<string,unknown>;
  if(!row||typeof row!=="object"||typeof row.creatorId!=="string"||typeof row.pid!=="string")throw Error('invalid_lead_pool');
  if(!/^\d{19}$/.test(row.pid)||typeof row.layer!=="string"||!LAYERS.has(row.layer))throw Error('invalid_lead_pool');
  const maybe=(v:unknown)=>(v==null?null:(typeof v==="number"&&Number.isFinite(v)?v:null));
  return {creatorId:row.creatorId,handle:typeof row.handle==="string"?row.handle:"",pid:row.pid,
   rank:maybe(row.rank),units:maybe(row.units),unlocked:row.unlocked===true,
   sentAt:maybe(row.sentAt),readyAt:maybe(row.readyAt),layer:row.layer,caseUpdatedAt:maybe(row.caseUpdatedAt)};
 });
}

export function validateLeadPool(value:unknown):LeadPoolState{
 if(!value||typeof value!=="object")throw Error('invalid_lead_pool');
 const v=value as Record<string,unknown>;
 if(v.available!==true)return {available:false,counts:{} as LeadPoolCounts,cooldown:{unlocked:0,locked:0},layers:{},pools:{}};
 const raw=v.counts as Record<string,unknown>;
 const names=["leads","merged","unresolved","queued","positions","creators","handles","sent","unsent",
  "ready","readyCreators","cooling","awaitingReply","excluded","creatorsWithRelationship"];
 const counts:Record<string,number>={};
 for(const name of names)counts[name]=count(raw?.[name],name);
 // The pool is a partition: every position sits in exactly one layer.
 const summed=(v.layers as Record<string,number>|undefined)??{};
 const total=Object.values(summed).reduce((a,b)=>a+b,0);
 if(total!==counts.positions)throw Error('invalid_lead_pool');
 const pools:Record<string,LeadPosition[]>={};
 for(const [name,rows] of Object.entries((v.pools as Record<string,unknown>)??{})){
  if(!LAYERS.has(name))throw Error('invalid_lead_pool');
  pools[name]=validatePositions(rows);
 }
 const cooldown=v.cooldown as Record<string,unknown>;
 return {available:true,now:typeof v.now==="number"?v.now:undefined,counts:counts as unknown as LeadPoolCounts,
  cooldown:{unlocked:count(cooldown?.unlocked,"unlocked"),locked:count(cooldown?.locked,"locked")},
  layers:(v.layers as Record<string,number>)??{},pools};
}

function run():Promise<LeadPoolState>{
 const root=projectRoot();
 return new Promise((resolve,reject)=>{
  execFile(join(root,"../01-BDSystem-V2/.venv/bin/python"),[join(root,"scripts/lead-pool.py"),"status"],
   {cwd:root,timeout:60000,maxBuffer:4*1024*1024,env:{...process.env,PYTHONDONTWRITEBYTECODE:"1"}},(error,out)=>{
    try{resolve(validateLeadPool(JSON.parse(out)));}
    catch{reject(Error('lead_pool_unavailable'));}
   });
 });
}

export function readLeadPool():Promise<LeadPoolState>{return run();}
