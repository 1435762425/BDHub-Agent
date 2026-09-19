import {execFile,spawn} from "node:child_process";
import {openSync} from "node:fs";
import {join} from "node:path";
import {projectRoot} from "../creator-identities/refresh.ts";

/** How many TapLink card names would use a real AI short name, and how many would be truncated. */
export type NameSample={pid:string;title:string};
export type CatalogNamesState={scope:number;ready:number;missing:number;missingSample:NameSample[];
 provider:{model:string;ready:boolean;endpointHost:string;pricing?:unknown};batchLimit:number;prompt:string;promptRole:string;
 run?:{running:boolean;prepared:number;total:number;modelCalls:number;startedAt?:number;finishedAt?:number;cost?:unknown;errors?:{pids:string[];code:string}[]}|null;
 prepared?:number;requested?:number;modelCalls?:number;errors?:{pids:string[];code:string}[];stopped?:string};

function validateRun(value:unknown):CatalogNamesState["run"]{
 if(value==null)return null;
 if(typeof value!=="object"||Array.isArray(value))throw Error('invalid_catalog_names');
 const v=value as Record<string,unknown>;
 if(typeof v.running!=="boolean")throw Error('invalid_catalog_names');
 const num=(raw:unknown)=>(typeof raw==="number"&&Number.isFinite(raw)?raw:0);
 return {running:v.running,prepared:num(v.prepared),total:num(v.total),modelCalls:num(v.modelCalls),
  ...(typeof v.startedAt==="number"?{startedAt:v.startedAt}:{}),
  ...(typeof v.finishedAt==="number"?{finishedAt:v.finishedAt}:{}),
  ...(v.cost===undefined?{}:{cost:v.cost}),
  ...(Array.isArray(v.errors)?{errors:v.errors as {pids:string[];code:string}[]}:{})};
}

export function validateCatalogNames(value:unknown):CatalogNamesState{
 if(!value||typeof value!=="object")throw Error('invalid_catalog_names');
 const v=value as Record<string,unknown>;
 const count=(raw:unknown)=>{if(typeof raw!=="number"||!Number.isSafeInteger(raw)||raw<0)throw Error('invalid_catalog_names');return raw;};
 if(!Array.isArray(v.missingSample)||v.missingSample.length>20)throw Error('invalid_catalog_names');
 const sample=(v.missingSample as Record<string,unknown>[]).map(row=>{
  if(!row||typeof row!=="object"||typeof row.pid!=="string"||!/^\d{19}$/.test(row.pid))throw Error('invalid_catalog_names');
  return {pid:row.pid,title:typeof row.title==="string"?row.title:""};
 });
 const provider=v.provider as Record<string,unknown>|undefined;
 if(!provider||typeof provider!=="object")throw Error('invalid_catalog_names');
 return {scope:count(v.scope),ready:count(v.ready),missing:count(v.missing),missingSample:sample,
  provider:{model:String(provider.model??""),ready:provider.ready===true,endpointHost:String(provider.endpointHost??""),
   ...(provider.pricing===undefined?{}:{pricing:provider.pricing})},
  batchLimit:count(v.batchLimit),
  prompt:typeof v.prompt==="string"?v.prompt:"",
  promptRole:typeof v.promptRole==="string"?v.promptRole:"system",
  run:validateRun(v.run),
  ...(v.prepared===undefined?{}:{prepared:count(v.prepared)}),
  ...(v.requested===undefined?{}:{requested:count(v.requested)}),
  ...(v.modelCalls===undefined?{}:{modelCalls:count(v.modelCalls)}),
  ...(typeof v.stopped==="string"?{stopped:v.stopped}:{}),
  ...(Array.isArray(v.errors)?{errors:v.errors.length<=20?(v.errors as {pids:string[];code:string}[]):[]}:{})};
}

function run(args:string[],timeout:number):Promise<CatalogNamesState>{
 const root=projectRoot();
 return new Promise((resolve,reject)=>{
  execFile(join(root,".venv/bin/python"),[join(root,"scripts/catalog-names.py"),...args],
   {cwd:root,timeout,maxBuffer:1024*1024,env:{...process.env,PYTHONDONTWRITEBYTECODE:"1"}},(error,out)=>{
    let parsed:unknown;
    try{parsed=JSON.parse(out);}catch{reject(Error('catalog_names_unavailable'));return;}
    if(parsed&&typeof parsed==="object"&&Object.keys(parsed).length===1&&typeof (parsed as {error?:unknown}).error==="string"&&(parsed as {error:string}).error!==""){
     reject(Error((parsed as {error:string}).error));return;
    }
    try{resolve(validateCatalogNames(parsed));}catch{reject(Error('catalog_names_unavailable'));}
   });
 });
}

export function readCatalogNames():Promise<CatalogNamesState>{return run(["status"],60000);}
export function readNamesProgress():Promise<CatalogNamesState>{return run(["status","--with-progress"],60000);}
// A model call costs money and is never retried automatically, so it is always explicit and runs
// detached: the page follows the progress file instead of holding a request open for minutes.
export function startCatalogNames(limit:number,all=false):{started:boolean}{
 const root=projectRoot();
 const log=openSync(join(root,"var/catalog-names.log"),"a");
 // ``all`` covers whatever is left, so the operator never has to work out a number.
 const args=all?["prepare","--all"]:["prepare","--limit",String(limit)];
 const child=spawn(join(root,".venv/bin/python"),
  [join(root,"scripts/catalog-names.py"),...args],
  {cwd:root,detached:true,stdio:["ignore",log,log],env:{...process.env,PYTHONDONTWRITEBYTECODE:"1"}});
 child.unref();
 return {started:true};
}
