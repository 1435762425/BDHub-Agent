import {execFile} from "node:child_process";
import {join} from "node:path";
import {projectRoot} from "../runtime/project-root.ts";

/** The Kalodata grabber's login card, plus the product id used to test the scraping path. */
export type KalodataIdentityConfig={version:string;activationCode:string;canaryPid:string};
export type KalodataProbe={verdict:string;identityOk:boolean|null;detail:string|null;pid:string;rows:number|null;proxyConfigured:boolean;checkedAt:number;elapsedSeconds:number};
export type KalodataCookieState={present:boolean;updatedAt:number|null;bytes:number};
export type KalodataLoginState={mode:string;pid:number;startedAt:number;log:string;running:boolean}|null;
export type KalodataIdentityState={config:KalodataIdentityConfig;grabber:string;loginDir:string;pythonReady:boolean;cookie:KalodataCookieState;proxy:{configured:boolean;updatedAt:number|null};lastProbe:KalodataProbe|null;probePid:string;login:KalodataLoginState;probeEndpoint:string;probeHint:string;saved?:boolean;started?:KalodataLoginState};

const VERDICTS=new Set(["ready","quota_exhausted","auth_required","business_rejected","unreachable"]);

export function validateIdentityConfig(value:unknown):KalodataIdentityConfig{
 if(!value||typeof value!=="object")throw Error('invalid_kalodata_identity');
 const v=value as Record<string,unknown>;
 if(typeof v.version!=="string"||!v.version)throw Error('invalid_kalodata_identity');
 const code=String(v.activationCode??"").trim();
 // The card is filled into a browser form; anything with control whitespace is not one.
 if(code.length>200||/[\r\n\t]/.test(code))throw Error('invalid_kalodata_identity');
 const pid=String(v.canaryPid??"").trim();
 if(pid&&!/^\d{19}$/.test(pid))throw Error('invalid_kalodata_identity');
 return {version:v.version,activationCode:code,canaryPid:pid};
}

function finite(value:unknown,fallback=0):number{return typeof value==="number"&&Number.isFinite(value)?value:fallback;}

function validateProbe(value:unknown):KalodataProbe|null{
 if(value==null)return null;
 if(typeof value!=="object"||Array.isArray(value))throw Error('invalid_kalodata_identity');
 const v=value as Record<string,unknown>;
 if(typeof v.verdict!=="string"||!VERDICTS.has(v.verdict))throw Error('invalid_kalodata_identity');
 if(!(v.identityOk===null||typeof v.identityOk==="boolean"))throw Error('invalid_kalodata_identity');
 return {verdict:v.verdict,identityOk:v.identityOk as boolean|null,
  detail:v.detail==null?null:String(v.detail),
  pid:typeof v.pid==="string"?v.pid:"",
  rows:v.rows==null?null:finite(v.rows),
  proxyConfigured:v.proxyConfigured===true,
  checkedAt:finite(v.checkedAt),elapsedSeconds:finite(v.elapsedSeconds)};
}

function validateLogin(value:unknown):KalodataLoginState{
 if(value==null)return null;
 if(typeof value!=="object")throw Error('invalid_kalodata_identity');
 const v=value as Record<string,unknown>;
 if(typeof v.mode!=="string"||typeof v.pid!=="number"||typeof v.running!=="boolean")throw Error('invalid_kalodata_identity');
 return {mode:v.mode,pid:v.pid,startedAt:finite(v.startedAt),log:typeof v.log==="string"?v.log:"",running:v.running};
}

export function validateIdentityState(value:unknown):KalodataIdentityState{
 if(!value||typeof value!=="object")throw Error('invalid_kalodata_identity');
 const v=value as Record<string,unknown>;
 const cookie=v.cookie as Record<string,unknown>|undefined;
 const proxy=v.proxy as Record<string,unknown>|undefined;
 if(!cookie||typeof cookie!=="object"||typeof cookie.present!=="boolean")throw Error('invalid_kalodata_identity');
 if(!proxy||typeof proxy!=="object")throw Error('invalid_kalodata_identity');
 return {config:validateIdentityConfig(v.config),
  grabber:typeof v.grabber==="string"?v.grabber:"",
  loginDir:typeof v.loginDir==="string"?v.loginDir:"",
  pythonReady:v.pythonReady===true,
  cookie:{present:cookie.present,updatedAt:cookie.updatedAt==null?null:finite(cookie.updatedAt),bytes:finite(cookie.bytes)},
  proxy:{configured:proxy.configured===true,updatedAt:proxy.updatedAt==null?null:finite(proxy.updatedAt)},
  lastProbe:validateProbe(v.lastProbe),
  probePid:typeof v.probePid==="string"?v.probePid:"",
  login:validateLogin(v.login),
  probeEndpoint:typeof v.probeEndpoint==="string"?v.probeEndpoint:"",
  probeHint:typeof v.probeHint==="string"?v.probeHint:"",
  ...(typeof v.saved==="boolean"?{saved:v.saved}:{}),
  ...(v.started?{started:validateLogin(v.started)}:{})};
}

function runIdentity(args:string[],timeout:number):Promise<KalodataIdentityState>{
 const root=projectRoot();
 return new Promise((resolve,reject)=>{
  execFile(join(root,".venv/bin/python"),[join(root,"scripts/kalodata-identity.py"),...args],
   {cwd:root,timeout,maxBuffer:2*1024*1024,env:{...process.env,PYTHONDONTWRITEBYTECODE:"1"}},(error,out)=>{
    try{resolve(validateIdentityState(JSON.parse(out)));}
    catch{reject(Error('kalodata_identity_unavailable'));}
   });
 });
}

export function readIdentity():Promise<KalodataIdentityState>{return runIdentity(["status"],30000);}
export function saveIdentity(config:KalodataIdentityConfig):Promise<KalodataIdentityState>{return runIdentity(["save","--json",JSON.stringify(config)],30000);}
// A probe makes one real creator-list request, so it is slower than a status read.
export function probeIdentity():Promise<KalodataIdentityState>{return runIdentity(["probe"],120000);}
export function startIdentityLogin(mode:"activate"|"refresh"):Promise<KalodataIdentityState>{return runIdentity([mode],60000);}

export function validateIdentityRequest(value:unknown):{action:"save"|"probe"|"activate"|"refresh";config?:KalodataIdentityConfig}{
 if(!value||typeof value!=="object")throw Error('invalid_kalodata_identity_request');
 const v=value as Record<string,unknown>;
 if(v.action!=="save"&&v.action!=="probe"&&v.action!=="activate"&&v.action!=="refresh")throw Error('invalid_kalodata_identity_request');
 try{
  if(v.action==="save")return {action:v.action,config:validateIdentityConfig(v.config)};
  if(v.config!==undefined)return {action:v.action,config:validateIdentityConfig(v.config)};
  return {action:v.action};
 }catch{throw Error('invalid_kalodata_identity_request');}
}
