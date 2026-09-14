import {execFile} from "node:child_process";
import {join} from "node:path";
import {projectRoot} from "../creator-identities/refresh.ts";

export type LinkNamingConfig={version:string;template:string;tailLength:number;maxLength:number;shortNameMaxLength:number};
export type LinkNamingPreviewRow={pid:string;campaignId:string;title:string;creatorPercent:string;publicPercent:string|null;totalPercent:string|null;shortName:string|null;name:string|null;tail:string;length:number;limit:number;error:string|null};
export type LinkNamingState={config:LinkNamingConfig;fingerprint:string;placeholders:string[];defaults?:LinkNamingConfig;preview:LinkNamingPreviewRow[];saved?:boolean;error?:string};

const PLACEHOLDERS=new Set(["short_name","creator_percent","public_percent","total_percent","tail","pid_last6","campaign_last6","market"]);

function validateConfig(value:unknown):LinkNamingConfig{
 if(!value||typeof value!=="object")throw Error('invalid_link_naming');
 const v=value as Record<string,unknown>;
 if(typeof v.version!=="string"||!v.version)throw Error('invalid_link_naming');
 if(typeof v.template!=="string"||!v.template.trim()||v.template.length>120)throw Error('invalid_link_naming');
 if(v.template.split("{").length!==v.template.split("}").length)throw Error('invalid_link_naming');
 const found=[...v.template.matchAll(/\{([a-z_0-9]+)\}/g)].map(m=>m[1]);
 if(found.some(name=>!PLACEHOLDERS.has(name)))throw Error('invalid_link_naming');
 if(!found.includes("short_name"))throw Error('invalid_link_naming');
 const bounded=(raw:unknown,low:number,high:number)=>{if(!Number.isSafeInteger(raw)||(raw as number)<low||(raw as number)>high)throw Error('invalid_link_naming');return raw as number;};
 return {version:v.version,template:v.template,
  tailLength:bounded(v.tailLength,4,12),maxLength:bounded(v.maxLength,10,50),shortNameMaxLength:bounded(v.shortNameMaxLength,1,40)};
}

export function validateNaming(value:unknown):LinkNamingState{
 if(!value||typeof value!=="object")throw Error('invalid_link_naming');
 const v=value as Record<string,unknown>;
 const config=validateConfig(v.config);
 if(typeof v.fingerprint!=="string"||!/^[0-9a-f]{16,128}$/.test(v.fingerprint))throw Error('invalid_link_naming');
 if(!Array.isArray(v.placeholders)||v.placeholders.length>16||!v.placeholders.every(p=>typeof p==="string"&&PLACEHOLDERS.has(p)))throw Error('invalid_link_naming');
 if(!Array.isArray(v.preview)||v.preview.length>20)throw Error('invalid_link_naming');
 for(const row of v.preview as LinkNamingPreviewRow[]){
  if(!row||typeof row!=="object"||!/^\d{19}$/.test(row.pid)||typeof row.title!=="string"||typeof row.creatorPercent!=="string")throw Error('invalid_link_naming');
  if(row.name!==null&&typeof row.name!=="string")throw Error('invalid_link_naming');
  if(!Number.isSafeInteger(row.length)||row.length<0||!Number.isSafeInteger(row.limit))throw Error('invalid_link_naming');
 }
 return {config,fingerprint:v.fingerprint,placeholders:v.placeholders as string[],preview:v.preview as LinkNamingPreviewRow[],
  ...(typeof v.saved==="boolean"?{saved:v.saved}:{}),...(typeof v.error==="string"?{error:v.error}:{})};
}

function runNaming(args:string[]):Promise<LinkNamingState>{
 const root=projectRoot();
 return new Promise((resolve,reject)=>{
  execFile(join(root,"../01-BDSystem-V2/.venv/bin/python"),[join(root,"scripts/link-naming.py"),...args],
   {cwd:root,timeout:30000,maxBuffer:2*1024*1024,env:{...process.env,PYTHONDONTWRITEBYTECODE:"1"}},(error,out)=>{
    try{const parsed=validateNaming(JSON.parse(out));if(error&&!parsed.error)throw error;resolve(parsed);}
    catch{reject(Error('link_naming_unavailable'));}
   });
 });
}

export function readNaming():Promise<LinkNamingState>{return runNaming(["show"]);}
export function previewNaming(config:unknown):Promise<LinkNamingState>{return runNaming(["preview","--json",JSON.stringify(validateConfig(config))]);}
export function saveNaming(config:unknown):Promise<LinkNamingState>{return runNaming(["save","--json",JSON.stringify(validateConfig(config))]);}
export function validateNamingRequest(value:unknown):{action:"preview"|"save";config:LinkNamingConfig}{
 if(!value||typeof value!=="object")throw Error('invalid_link_naming_request');
 const v=value as Record<string,unknown>;
 if(v.action!=="preview"&&v.action!=="save")throw Error('invalid_link_naming_request');
 // A bad template is a bad request, not an unavailable service.
 try{return {action:v.action,config:validateConfig(v.config)};}
 catch{throw Error('invalid_link_naming_request');}
}
