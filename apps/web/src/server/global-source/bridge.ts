import {execFile} from "node:child_process";
import {join} from "node:path";
import {projectRoot} from "../creator-identities/refresh.ts";
import {isLocalRequest} from "../runtime/validation.ts";
export type GlobalProduct={pid:string;title:string;listedSelected:boolean|null;selectionObservation?:{state:string;observedAt:number}|null;totalCommissionRaw:string|null;publicCommissionRaw:string|null;detailsChecked:boolean;stockChecked:boolean;selectedOffers:Array<{stock:string|null;creatorPercent:string|null;endAt:string|null;assessment:{eligible:boolean;reasons:string[]}}>};
export type GlobalStatus={selectionBatch?:{performance?:{confirmedPerMinute:number;configuredQps:number;lanes:number;confirmedThisRun:number;elapsedSeconds:number};id:string;total:number;states:Record<string,number>;updatedAt:number|null}|null;available:boolean;executionAllowed:false;displayRunId?:string;displayIsComplete?:boolean;activePublished?:{id:string;products:number;updated:number}|null;id?:string;market?:string;source?:string;state?:string;products?:number;listedSelectedProducts?:number;listedUnselectedProducts?:number;pages?:number;reportedTotal?:number|null;detailProducts?:number;reason?:string|null;published?:boolean;updatedAt?:number;items:GlobalProduct[];totalMatches:number;offset:number;limit:number};
export type CatalogLinkItem={pid:string;campaignId:string;state:string;error:string|null;blocker:string|null;creatorPercent:string|null;publicPercent:string|null;totalPercent:string|null;title:string;intentId:string|null;updated:number};
export type CatalogLinkStatus={available:boolean;executionAllowed:false;reason?:string;runId?:string;scope?:Record<string,unknown>;created?:number;selection?:{intakeRun:string;confirmed:number}|null;summary?:{runId:string;total:number;states:Record<string,number>;retiredCount:number;verifiedLinkCount:number;verifiedPidCount:number;reusePidCount:number;pendingCount:number;incompleteCount:number;errors:string[];blockers:string[];retryableErrors?:string[];errorsUpdatedAt?:number|null};items?:CatalogLinkItem[]};
export function validateLinkStatus(value:unknown):CatalogLinkStatus{
 if(!value||typeof value!=="object")throw Error('invalid_link_status');const v=value as CatalogLinkStatus;
 if(v.executionAllowed!==false||typeof v.available!=="boolean")throw Error('invalid_link_status');
 if(!v.available)return v;
 const s=v.summary;if(!s||typeof s!=="object"||!Number.isSafeInteger(s.total)||s.total<0||!Number.isSafeInteger(s.verifiedLinkCount)||s.verifiedLinkCount<0||!Number.isSafeInteger(s.verifiedPidCount)||s.verifiedPidCount<0||!Number.isSafeInteger(s.pendingCount)||s.pendingCount<0)throw Error('invalid_link_status');
 if(!Array.isArray(s.errors)||!Array.isArray(s.blockers)||!Array.isArray(v.items)||v.items.length>40)throw Error('invalid_link_status');
 if(s.retryableErrors!==undefined&&(!Array.isArray(s.retryableErrors)||s.retryableErrors.length>40))throw Error('invalid_link_status');
 if(s.errorsUpdatedAt!=null&&typeof s.errorsUpdatedAt!=='number')throw Error('invalid_link_status');
 if(!v.items.every(i=>i&&/^\d{19}$/.test(i.pid)&&typeof i.campaignId==="string"&&typeof i.state==="string"&&typeof i.title==="string"))throw Error('invalid_link_status');
 return v;
}
export function readLinkStatus():Promise<CatalogLinkStatus>{const root=projectRoot();return new Promise((resolve,reject)=>{execFile(join(root,".venv/bin/python"),[join(root,"scripts/catalog-link-prepare.py"),"--status-links"],{cwd:root,timeout:20000,maxBuffer:2*1024*1024,env:{...process.env,PYTHONDONTWRITEBYTECODE:"1"}},(e,out)=>{try{if(e)throw e;resolve(validateLinkStatus(JSON.parse(out)));}catch{reject(Error('catalog_link_status_unavailable'));}});});}
export function validateGlobal(value:unknown):GlobalStatus{
 if(!value||typeof value!=="object")throw Error('invalid_global_source');const v=value as GlobalStatus;
 if(v.executionAllowed!==false||typeof v.available!=="boolean")throw Error('invalid_global_source');
 if(!v.available)return {available:false,executionAllowed:false,items:[],totalMatches:0,offset:0,limit:30};
 if(v.source!=="opportunity_global_only"||v.market!=="it"||!Number.isSafeInteger(v.products)||Number(v.products)<0||!Number.isSafeInteger(v.totalMatches)||v.totalMatches<0||!Array.isArray(v.items)||v.items.length>50||!v.items.every(p=>p&&/^\d{19}$/.test(p.pid)&&typeof p.title==='string'&&Array.isArray(p.selectedOffers)))throw Error('invalid_global_source');
 return v;
}
export function readGlobal(offset=0,query=""):Promise<GlobalStatus>{const root=projectRoot();return new Promise((resolve,reject)=>{execFile(join(root,".venv/bin/python"),[join(root,"scripts/collect-global-opportunity.py"),"--status","--offset",String(offset),`--query=${query}`],{cwd:root,timeout:15000,maxBuffer:2*1024*1024,env:{...process.env,PYTHONDONTWRITEBYTECODE:"1"}},(e,out)=>{try{if(e)throw e;resolve(validateGlobal(JSON.parse(out)));}catch{reject(Error('global_source_unavailable'));}});});}
export function createGlobalGet(read=readGlobal){return async(request:Request)=>{
 const headers={"Cache-Control":"no-store"};if(!isLocalRequest(request,false))return Response.json({error:'local_origin_required'},{status:403,headers});
 const u=new URL(request.url),offset=Number(u.searchParams.get('offset')||0),q=u.searchParams.get('q')||'';
 if([...u.searchParams.keys()].some(k=>!['offset','q'].includes(k))||!Number.isSafeInteger(offset)||offset<0||offset>1000000||q.length>100)return Response.json({error:'invalid_query'},{status:400,headers});
 try{return Response.json(validateGlobal(await read(offset,q)),{headers});}catch{return Response.json({error:'global_source_unavailable'},{status:503,headers});}
};}

export function requestGlobalSync(requestId:string):Promise<{state:string;runId:string|null;executionAllowed:false}>{const root=projectRoot();return new Promise((resolve,reject)=>{
 const child=execFile(join(root,".venv/bin/python"),[join(root,"scripts/global-source-control.py")],{cwd:root,timeout:15000,maxBuffer:65536,env:{...process.env,PYTHONDONTWRITEBYTECODE:"1"}},(e,out)=>{try{if(e)throw e;const v=JSON.parse(out);if(!['reader_started','already_running'].includes(v.state)||v.executionAllowed!==false)throw Error();resolve(v);}catch{reject(Error('source_sync_unavailable'));}});
 child.stdin?.end(JSON.stringify({action:'sync',requestId}));
});}
export function createGlobalPost(sync=requestGlobalSync){return async(request:Request)=>{
 const headers={'Cache-Control':'no-store'};if(!isLocalRequest(request,true))return Response.json({error:'local_origin_required'},{status:403,headers});
 let body:{action:string;requestId:string};try{const raw=await request.text();if(raw.length>2048)throw Error();body=JSON.parse(raw);if(!body||typeof body!=='object'||Array.isArray(body)||Object.keys(body).sort().join(',')!=='action,requestId'||body.action!=='sync'||typeof body.requestId!=='string'||!/^[A-Za-z0-9_-]{1,80}$/.test(body.requestId))throw Error();}catch{return Response.json({error:'invalid_sync_request'},{status:400,headers});}
 try{return Response.json(await sync(body.requestId),{headers});}catch{return Response.json({error:'source_sync_unavailable'},{status:503,headers});}
};}
