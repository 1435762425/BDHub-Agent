import {execFile} from "node:child_process";
import {join} from "node:path";
import {projectRoot} from "../creator-identities/refresh.ts";

/** Counts in 达人×商品 units, plus the four layers the pool is split into. */
export type LeadPoolCounts={leads:number;merged:number;unresolved:number;queued:number;positions:number;
 creators:number;handles:number;sent:number;unsent:number;ready:number;readyCreators:number;
 cooling:number;awaitingReply:number;excluded:number;creatorsWithRelationship:number;
 aPositions:number;bPositions:number;videoUnresolved:number};
export type LeadPosition={creatorId:string;handle:string;pid:string;rank:number|null;units:number|null;
 sourceClass:"A"|"B";gmv:string|null;videoViews:number|null;videoId:string|null;videoReleasedAt:string|null;
 unlocked:boolean;sentAt:number|null;readyAt:number|null;layer:string;caseUpdatedAt?:number|null};
export type LeadPoolState={schema:"bdhub.lead-pool.v3";available:boolean;now?:number;counts:LeadPoolCounts;
 cooldown:{unlocked:number;locked:number};layers:Record<string,number>;pools:Record<string,LeadPosition[]>;
 business:{sendable:number;waiting:number;inactive:number;total:number};reasons:Record<string,number>;
 history:{sent:number;currentPositions:number}};

const LAYERS=new Set(["ready","queued","cooling","awaiting_reply","excluded","product_inactive","sent"]);

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
  if(row.sourceClass!=="A"&&row.sourceClass!=="B")throw Error('invalid_lead_pool');
  const maybe=(v:unknown)=>(v==null?null:(typeof v==="number"&&Number.isFinite(v)?v:null));
  const optionalText=(v:unknown,max:number)=>(v==null?null:typeof v==="string"&&v.length<=max?v:(()=>{throw Error('invalid_lead_pool')})());
  const gmv=optionalText(row.gmv,80);if(gmv!==null&&!/^\d+(?:\.\d+)?$/.test(gmv))throw Error('invalid_lead_pool');
  return {creatorId:row.creatorId,handle:typeof row.handle==="string"?row.handle:"",pid:row.pid,
   rank:maybe(row.rank),units:maybe(row.units),sourceClass:row.sourceClass,gmv,
   videoViews:maybe(row.videoViews),videoId:optionalText(row.videoId,100),videoReleasedAt:optionalText(row.videoReleasedAt,40),unlocked:row.unlocked===true,
   sentAt:maybe(row.sentAt),readyAt:maybe(row.readyAt),layer:row.layer,caseUpdatedAt:maybe(row.caseUpdatedAt)};
 });
}

export function validateLeadPool(value:unknown):LeadPoolState{
 if(!value||typeof value!=="object")throw Error('invalid_lead_pool');
 const v=value as Record<string,unknown>;
 if(v.available!==true)return {schema:"bdhub.lead-pool.v3",available:false,counts:{} as LeadPoolCounts,cooldown:{unlocked:0,locked:0},layers:{},pools:{},business:{sendable:0,waiting:0,inactive:0,total:0},reasons:{},history:{sent:0,currentPositions:0}};
 if(v.schema!=="bdhub.lead-pool.v3")throw Error('invalid_lead_pool');
 const raw=v.counts as Record<string,unknown>;
 const names=["leads","merged","unresolved","queued","positions","creators","handles","sent","unsent",
  "ready","readyCreators","cooling","awaitingReply","excluded","creatorsWithRelationship",
  "aPositions","bPositions","videoUnresolved"];
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
 const business=v.business as Record<string,unknown>,history=v.history as Record<string,unknown>;
 const projected={sendable:count(business?.sendable,"sendable"),waiting:count(business?.waiting,"waiting"),
  inactive:count(business?.inactive,"inactive"),total:count(business?.total,"total")};
 const sent=count(history?.sent,"sent"),currentPositions=count(history?.currentPositions,"currentPositions");
 if(projected.sendable+projected.waiting+projected.inactive!==projected.total||projected.total+currentPositions!==counts.positions||sent!==counts.sent||currentPositions!==(v.layers as Record<string,number>).sent)throw Error('invalid_lead_pool');
 if(counts.aPositions+counts.bPositions!==counts.positions)throw Error('invalid_lead_pool');
 return {schema:"bdhub.lead-pool.v3",available:true,now:typeof v.now==="number"?v.now:undefined,counts:counts as unknown as LeadPoolCounts,
  cooldown:{unlocked:count(cooldown?.unlocked,"unlocked"),locked:count(cooldown?.locked,"locked")},
  layers:(v.layers as Record<string,number>)??{},pools,business:projected,
  reasons:countsRecord(v.reasons),history:{sent,currentPositions}};
}

function countsRecord(raw:unknown):Record<string,number>{
 if(raw==null)return {};
 if(typeof raw!=="object"||Array.isArray(raw))throw Error('invalid_lead_pool');
 const result:Record<string,number>={};
 for(const [key,value] of Object.entries(raw as Record<string,unknown>)){
  if(!LAYERS.has(key))throw Error('invalid_lead_pool');result[key]=count(value,key);
 }
 return result;
}

function run(market="it"):Promise<LeadPoolState>{
 const root=projectRoot();
 return new Promise((resolve,reject)=>{
  execFile(join(root,".venv/bin/python"),[join(root,"scripts/lead-pool.py"),"status","--market",market],
   {cwd:root,timeout:60000,maxBuffer:4*1024*1024,env:{...process.env,PYTHONDONTWRITEBYTECODE:"1"}},(error,out)=>{
    try{resolve(validateLeadPool(JSON.parse(out)));}
    catch{reject(Error('lead_pool_unavailable'));}
   });
 });
}

export function readLeadPool(market="it"):Promise<LeadPoolState>{return run(market);}
