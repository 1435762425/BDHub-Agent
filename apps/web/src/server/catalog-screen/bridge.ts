import {execFile} from "node:child_process";
import {join} from "node:path";
import {projectRoot} from "../runtime/project-root.ts";

/** Thresholds for admitting a collected full-managed product into the selected pool. */
export type CatalogScreenConfig={version:string;minSales:number;minRating:number;minCommissionGapPoints:number;allowUnrated:boolean};
export type CatalogScreenCollection={runId:string;state:string;products:number;reportedTotal:number|null;observedAt:number};
export type CatalogScreenRecord={runId:string;sourceRun:string;fingerprint:string;state:string;counts:Record<string,number>;reasons:Record<string,number>;updatedAt:number};
export type CatalogScreenFunnel=CatalogScreenRecord&{config:CatalogScreenConfig;collected:number;eligible:number;rejected:number;selectedEligible:number;unselectedEligible:number;unknownSelectedFlag:number;unrated:number;unratedEligible:number};
export type CatalogScreenPreview={collected:number;eligible:number;rejected:number;unselectedEligible:number;unratedEligible:number;addedVersusActive:number;removedVersusActive:number;reasons:Record<string,number>;config:CatalogScreenConfig;fingerprint:string;activeFingerprint:string};
export type CatalogScreenState={config:CatalogScreenConfig;fingerprint:string;defaults:CatalogScreenConfig;bounds:Record<string,number[]>;configPath:string;collection:CatalogScreenCollection|null;screen:CatalogScreenRecord|null;funnel:CatalogScreenFunnel|null;saved?:boolean;preview?:CatalogScreenPreview|null;previewConfig?:CatalogScreenConfig};

export const SCREEN_BOUNDS={minSales:[0,1_000_000],minRating:[0,5],minCommissionGapPoints:[0,100]} as const;

function boundedNumber(raw:unknown,low:number,high:number):number{
 if(typeof raw!=="number"||!Number.isFinite(raw)||raw<low||raw>high)throw Error('invalid_catalog_screen');
 return raw;
}

export function validateConfig(value:unknown):CatalogScreenConfig{
 if(!value||typeof value!=="object")throw Error('invalid_catalog_screen');
 const v=value as Record<string,unknown>;
 if(typeof v.version!=="string"||!v.version)throw Error('invalid_catalog_screen');
 if(typeof v.minSales!=="number"||!Number.isSafeInteger(v.minSales)||v.minSales<0||v.minSales>1_000_000)throw Error('invalid_catalog_screen');
 if(typeof v.allowUnrated!=="boolean")throw Error('invalid_catalog_screen');
 return {version:v.version,minSales:v.minSales,
  minRating:boundedNumber(v.minRating,0,5),
  minCommissionGapPoints:boundedNumber(v.minCommissionGapPoints,0,100),
  allowUnrated:v.allowUnrated};
}

function countMap(value:unknown,error:string):Record<string,number>{
 if(!value||typeof value!=="object"||Array.isArray(value))throw Error(error);
 const out:Record<string,number>={};
 for(const [key,raw] of Object.entries(value as Record<string,unknown>)){
  if(typeof key!=="string"||key.length>60||typeof raw!=="number"||!Number.isSafeInteger(raw)||raw<0)throw Error(error);
  out[key]=raw;
 }
 return out;
}

function nonNegative(value:unknown,error:string):number{
 if(typeof value!=="number"||!Number.isSafeInteger(value)||value<0)throw Error(error);
 return value;
}

function validateBounds(value:unknown):Record<string,number[]>{
 if(!value||typeof value!=="object"||Array.isArray(value))throw Error('invalid_catalog_screen');
 const out:Record<string,number[]>={};
 for(const [key,raw] of Object.entries(value as Record<string,unknown>)){
  if(typeof key!=="string"||!Array.isArray(raw)||raw.length!==2)throw Error('invalid_catalog_screen');
  if(raw.some(n=>typeof n!=="number"||!Number.isFinite(n)))throw Error('invalid_catalog_screen');
  out[key]=[raw[0] as number,raw[1] as number];
 }
 return out;
}

function validateRecord(value:unknown):CatalogScreenRecord{
 if(!value||typeof value!=="object")throw Error('invalid_catalog_screen');
 const v=value as Record<string,unknown>;
 if(typeof v.runId!=="string"||!v.runId||typeof v.sourceRun!=="string"||!v.sourceRun)throw Error('invalid_catalog_screen');
 if(typeof v.fingerprint!=="string"||!/^[0-9a-f]{16,128}$/.test(v.fingerprint))throw Error('invalid_catalog_screen');
 if(typeof v.state!=="string"||typeof v.updatedAt!=="number")throw Error('invalid_catalog_screen');
 return {runId:v.runId,sourceRun:v.sourceRun,fingerprint:v.fingerprint,state:v.state,
  counts:countMap(v.counts,'invalid_catalog_screen'),reasons:countMap(v.reasons,'invalid_catalog_screen'),updatedAt:v.updatedAt};
}

function validateFunnel(value:unknown):CatalogScreenFunnel{
 const record=validateRecord(value);
 const v=value as Record<string,unknown>;
 return {...record,config:validateConfig(v.config),
  collected:nonNegative(v.collected,'invalid_catalog_screen'),eligible:nonNegative(v.eligible,'invalid_catalog_screen'),
  rejected:nonNegative(v.rejected,'invalid_catalog_screen'),selectedEligible:nonNegative(v.selectedEligible,'invalid_catalog_screen'),
  unselectedEligible:nonNegative(v.unselectedEligible,'invalid_catalog_screen'),unknownSelectedFlag:nonNegative(v.unknownSelectedFlag,'invalid_catalog_screen'),
  unrated:nonNegative(v.unrated,'invalid_catalog_screen'),unratedEligible:nonNegative(v.unratedEligible,'invalid_catalog_screen')};
}

export function validateState(value:unknown):CatalogScreenState{
 if(!value||typeof value!=="object")throw Error('invalid_catalog_screen');
 const v=value as Record<string,unknown>;
 const config=validateConfig(v.config);
 if(typeof v.fingerprint!=="string"||!/^[0-9a-f]{16,128}$/.test(v.fingerprint))throw Error('invalid_catalog_screen');
 let collection:CatalogScreenCollection|null=null;
 if(v.collection!=null){
  const c=v.collection as Record<string,unknown>;
  if(typeof c.runId!=="string"||!c.runId||typeof c.state!=="string")throw Error('invalid_catalog_screen');
  collection={runId:c.runId,state:c.state,products:nonNegative(c.products,'invalid_catalog_screen'),
   reportedTotal:c.reportedTotal==null?null:nonNegative(c.reportedTotal,'invalid_catalog_screen'),
   observedAt:typeof c.observedAt==="number"?c.observedAt:0};
 }
 return {config,fingerprint:v.fingerprint,defaults:validateConfig(v.defaults),
  bounds:validateBounds(v.bounds),
  configPath:typeof v.configPath==="string"?v.configPath:"",
  collection,
  screen:v.screen==null?null:validateRecord(v.screen),
  funnel:v.funnel==null?null:validateFunnel(v.funnel),
  ...(typeof v.saved==="boolean"?{saved:v.saved}:{}),
  ...(v.previewConfig?{previewConfig:validateConfig(v.previewConfig)}:{}),
  ...(v.preview==null?{}:{preview:validatePreview(v.preview)})};
}

function validatePreview(value:unknown):CatalogScreenPreview{
 if(!value||typeof value!=="object")throw Error('invalid_catalog_screen');
 const v=value as Record<string,unknown>;
 return {collected:nonNegative(v.collected,'invalid_catalog_screen'),eligible:nonNegative(v.eligible,'invalid_catalog_screen'),
  rejected:nonNegative(v.rejected,'invalid_catalog_screen'),unselectedEligible:nonNegative(v.unselectedEligible,'invalid_catalog_screen'),
  unratedEligible:nonNegative(v.unratedEligible,'invalid_catalog_screen'),
  addedVersusActive:nonNegative(v.addedVersusActive,'invalid_catalog_screen'),removedVersusActive:nonNegative(v.removedVersusActive,'invalid_catalog_screen'),
  reasons:countMap(v.reasons,'invalid_catalog_screen'),config:validateConfig(v.config),
  fingerprint:String(v.fingerprint??''),activeFingerprint:String(v.activeFingerprint??'')};
}

function runScreen(args:string[]):Promise<CatalogScreenState>{
 const root=projectRoot();
 return new Promise((resolve,reject)=>{
  execFile(join(root,".venv/bin/python"),[join(root,"scripts/global-screen.py"),...args],
   {cwd:root,timeout:60000,maxBuffer:4*1024*1024,env:{...process.env,PYTHONDONTWRITEBYTECODE:"1"}},(error,out)=>{
    try{const parsed=validateState(JSON.parse(out));resolve(parsed);}
    catch{reject(Error('catalog_screen_unavailable'));}
   });
 });
}

export function readScreen():Promise<CatalogScreenState>{return runScreen(["show"]);}
export function previewScreen(config:CatalogScreenConfig):Promise<CatalogScreenState>{return runScreen(["preview","--json",JSON.stringify(config)]);}
export function saveScreen(config:CatalogScreenConfig):Promise<CatalogScreenState>{return runScreen(["save","--json",JSON.stringify(config)]);}
// Re-screen the stored collection under the thresholds in force; local arithmetic, no platform call.
export function runScreenNow():Promise<CatalogScreenState>{return runScreen(["run"]);}

export function validateScreenRequest(value:unknown):{action:"preview"|"save"|"run";config?:CatalogScreenConfig}{
 if(!value||typeof value!=="object")throw Error('invalid_catalog_screen_request');
 const v=value as Record<string,unknown>;
 if(v.action==="run")return {action:"run"};
 if(v.action!=="preview"&&v.action!=="save")throw Error('invalid_catalog_screen_request');
 // Out-of-range thresholds are a bad request, not an unavailable service.
 try{return {action:v.action,config:validateConfig(v.config)};}
 catch{throw Error('invalid_catalog_screen_request');}
}
