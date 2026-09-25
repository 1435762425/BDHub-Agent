import {execFile} from "node:child_process";
import {join} from "node:path";
import {projectRoot} from "../runtime/project-root.ts";

/** Counts in 达人×商品 units, plus the four layers the pool is split into. */
export type LeadPoolCounts={leads:number;merged:number;unresolved:number;queued:number;positions:number;
 creators:number;handles:number;sent:number;unsent:number;ready:number;readyCreators:number;
 cooling:number;awaitingReply:number;excluded:number;creatorsWithRelationship:number;
 aPositions:number;bPositions:number;videoUnresolved:number};
export type LeadPosition={creatorId:string;handle:string;pid:string;rank:number|null;units:number|null;
 sourceClass:"A"|"B";gmv:string|null;videoViews:number|null;videoId:string|null;videoReleasedAt:string|null;
 unlocked:boolean;sentAt:number|null;readyAt:number|null;layer:string;caseUpdatedAt?:number|null};
export type OutreachAllocation={day:string;position:number;nextPreferred:"A"|"B";arranged:{A:number;B:number};borrowed:{A:number;B:number};outcomes:Record<string,Record<string,number>>;observations:Record<string,{cardConfirmed:number;replied:number;showcased:number}>|null};
export type LeadPoolState={schema:"bdhub.lead-pool.v3";market:string;available:boolean;now?:number;counts:LeadPoolCounts;
 cooldown:{unlocked:number;locked:number};layers:Record<string,number>;pools:Record<string,LeadPosition[]>;
 business:{sendable:number;waiting:number;inactive:number;total:number};reasons:Record<string,number>;
 history:{sent:number;currentPositions:number};allocation?:OutreachAllocation|null};

const LAYERS=new Set(["ready","queued","cooling","awaiting_reply","technical_isolated","allocated_today","excluded","product_inactive","sent"]);

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

export function validateLeadPool(value:unknown,expectedMarket?:string):LeadPoolState{
 if(!value||typeof value!=="object")throw Error('invalid_lead_pool');
 const v=value as Record<string,unknown>;
 if(expectedMarket!==undefined&&v.market!==expectedMarket)throw Error('invalid_lead_pool');
 const market=typeof v.market==='string'?v.market:expectedMarket??'';
 if(v.available!==true)return {schema:"bdhub.lead-pool.v3",market,available:false,counts:{} as LeadPoolCounts,cooldown:{unlocked:0,locked:0},layers:{},pools:{},business:{sendable:0,waiting:0,inactive:0,total:0},reasons:{},history:{sent:0,currentPositions:0}};
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
 return {schema:"bdhub.lead-pool.v3",market,available:true,now:typeof v.now==="number"?v.now:undefined,counts:counts as unknown as LeadPoolCounts,
  cooldown:{unlocked:count(cooldown?.unlocked,"unlocked"),locked:count(cooldown?.locked,"locked")},
  layers:(v.layers as Record<string,number>)??{},pools,business:projected,
  reasons:countsRecord(v.reasons),history:{sent,currentPositions},allocation:validateAllocation(v.allocation)};
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

function run(market:string):Promise<LeadPoolState>{
 const root=projectRoot();
 return new Promise((resolve,reject)=>{
  execFile(join(root,".venv/bin/python"),[join(root,"scripts/lead-pool.py"),"status","--market",market],
   {cwd:root,timeout:60000,maxBuffer:4*1024*1024,env:{...process.env,PYTHONDONTWRITEBYTECODE:"1"}},(error,out)=>{
    try{resolve(validateLeadPool(JSON.parse(out),market));}
    catch{reject(Error('lead_pool_unavailable'));}
   });
 });
}

export function readLeadPool(market:string):Promise<LeadPoolState>{return run(market);}


function validateAllocation(raw:unknown):OutreachAllocation|null{
 if(raw==null)return null;
 if(typeof raw!=="object"||Array.isArray(raw))throw Error('invalid_lead_pool');
 const a=raw as Record<string,unknown>;
 if(a.policy!=="outreach-ab-4-to-1-v1"||a.scope!=="post_cutover_allocations"||typeof a.day!=="string"||!/^\d{4}-\d{2}-\d{2}$/.test(a.day))throw Error('invalid_lead_pool');
 const position=count(a.position,"position");
 if(position>4||a.nextPreferred!==(position===4?"B":"A"))throw Error('invalid_lead_pool');
 const pair=(v:unknown)=>{const r=v as Record<string,unknown>;return {A:count(r?.A,"A"),B:count(r?.B,"B")};};
 const arranged=pair(a.arranged),borrowed=pair(a.borrowed),outcomes:Record<string,Record<string,number>>={};
 const statuses=new Set(["ready","running","unknown","quarantined_unknown","confirmed","partial_delivery","rejected","failed_known","cancelled"]);
 for(const kind of ["A","B"] as const){
  const rows=(a.outcomes as Record<string,unknown>)?.[kind]??{};
  if(typeof rows!=="object"||rows===null||Array.isArray(rows))throw Error('invalid_lead_pool');
  outcomes[kind]={};let total=0;
  for(const [key,value] of Object.entries(rows)){
   if(!statuses.has(key))throw Error('invalid_lead_pool');const n=count(value,key);outcomes[kind][key]=n;total+=n;
  }
  if(total!==arranged[kind]||borrowed[kind]>arranged[kind])throw Error('invalid_lead_pool');
 }
 let observations:OutreachAllocation["observations"]=null;
 if(a.observations!=null){
  observations={};
  for(const kind of ["A","B"] as const){
   const o=(a.observations as Record<string,Record<string,unknown>>)?.[kind];
   const cardConfirmed=count(o?.cardConfirmed,"cardConfirmed"),replied=count(o?.replied,"replied"),showcased=count(o?.showcased,"showcased");
   if(cardConfirmed>arranged[kind]||replied>cardConfirmed||showcased>cardConfirmed)throw Error('invalid_lead_pool');
   observations[kind]={cardConfirmed,replied,showcased};
  }
 }
 return {day:a.day,position,nextPreferred:position===4?"B":"A",arranged,borrowed,outcomes,observations};
}
