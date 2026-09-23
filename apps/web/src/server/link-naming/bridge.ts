import {execFile} from "node:child_process";
import {join} from "node:path";
import {projectRoot} from "../creator-identities/refresh.ts";
import {enabledMarket} from "../markets/registry.ts";

export type LinkNamingConfig={version:string;template:string;tailLength:number;maxLength:number;shortNameMaxLength:number;market?:string;language?:string;locale?:string};
export type LinkNamingPreviewRow={pid:string;campaignId:string;title:string;creatorPercent:string;publicPercent:string|null;totalPercent:string|null;shortName:string|null;name:string|null;tail:string;length:number;limit:number;error:string|null};
export type LinkNamingState={config:LinkNamingConfig;fingerprint:string;placeholders:string[];defaults?:LinkNamingConfig;preview:LinkNamingPreviewRow[];saved?:boolean;error?:string};

const PLACEHOLDERS=new Set(["short_name","creator_percent","public_percent","total_percent","tail","pid_last6","campaign_last6","market"]);

function validateConfig(value:unknown,expectedMarket?:string):LinkNamingConfig{
 if(!value||typeof value!=="object")throw Error('invalid_link_naming');
 const v=value as Record<string,unknown>;
 if(typeof v.version!=="string"||!v.version)throw Error('invalid_link_naming');
 if(typeof v.template!=="string"||!v.template.trim()||v.template.length>120)throw Error('invalid_link_naming');
 if(v.template.split("{").length!==v.template.split("}").length)throw Error('invalid_link_naming');
 const found=[...v.template.matchAll(/\{([a-z_0-9]+)\}/g)].map(m=>m[1]);
 if(found.some(name=>!PLACEHOLDERS.has(name)))throw Error('invalid_link_naming');
 if(!found.includes("short_name"))throw Error('invalid_link_naming');
 const bounded=(raw:unknown,low:number,high:number)=>{if(!Number.isSafeInteger(raw)||(raw as number)<low||(raw as number)>high)throw Error('invalid_link_naming');return raw as number;};
 const rawMarket=v.market===undefined?"it":v.market;
 if(typeof rawMarket!=="string")throw Error('invalid_link_naming');
 const market=expectedMarket??rawMarket,registered=enabledMarket(market);
 if(!registered||!registered.contentReady||!registered.templateLanguage||!registered.locale||rawMarket!==market)throw Error('invalid_link_naming');
 const hasMetadata=v.market!==undefined||v.language!==undefined||v.locale!==undefined;
 if((market!=="it"||hasMetadata)&&(v.market!==market||v.language!==registered.templateLanguage||v.locale!==registered.locale))throw Error('invalid_link_naming');
 const config:LinkNamingConfig={version:v.version,template:v.template,
  tailLength:bounded(v.tailLength,4,12),maxLength:bounded(v.maxLength,10,50),shortNameMaxLength:bounded(v.shortNameMaxLength,1,40)};
 if(market!=="it"||hasMetadata){config.market=market;config.language=registered.templateLanguage;config.locale=registered.locale;}
 return config;
}

export function validateNaming(value:unknown,expectedMarket?:string):LinkNamingState{
 if(!value||typeof value!=="object")throw Error('invalid_link_naming');
 const v=value as Record<string,unknown>;
 const config=validateConfig(v.config,expectedMarket);
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

function runNaming(args:string[],market="it"):Promise<LinkNamingState>{
 const root=projectRoot();
 return new Promise((resolve,reject)=>{
  execFile(join(root,".venv/bin/python"),[join(root,"scripts/link-naming.py"),...args,"--market",market],
   {cwd:root,timeout:30000,maxBuffer:2*1024*1024,env:{...process.env,PYTHONDONTWRITEBYTECODE:"1"}},(error,out)=>{
    try{const parsed=validateNaming(JSON.parse(out),market);if(error&&!parsed.error)throw error;resolve(parsed);}
    catch{reject(Error('link_naming_unavailable'));}
   });
 });
}

export function readNaming(market="it"):Promise<LinkNamingState>{return runNaming(["show"],market);}
export function previewNaming(config:unknown,market="it"):Promise<LinkNamingState>{return runNaming(["preview","--json",JSON.stringify(validateConfig(config,market))],market);}
export function saveNaming(config:unknown,market="it"):Promise<LinkNamingState>{return runNaming(["save","--json",JSON.stringify(validateConfig(config,market))],market);}
export function validateNamingRequest(value:unknown):{action:"preview"|"save";market:string;config:LinkNamingConfig}{
 if(!value||typeof value!=="object")throw Error('invalid_link_naming_request');
 const v=value as Record<string,unknown>;
 if(v.action!=="preview"&&v.action!=="save")throw Error('invalid_link_naming_request');
 const keys=Object.keys(v).sort().join(",");
 if(keys!=="action,config,market")throw Error('invalid_link_naming_request');
 const market=v.market;
 if(typeof market!=="string"||!enabledMarket(market))throw Error('invalid_link_naming_request');
 // A bad template is a bad request, not an unavailable service.
 try{return {action:v.action,market,config:validateConfig(v.config,market)};}
 catch{throw Error('invalid_link_naming_request');}
}
