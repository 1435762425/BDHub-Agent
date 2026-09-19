import {execFile} from 'node:child_process';
import {join} from 'node:path';
import {projectRoot} from '../creator-identities/refresh.ts';
import {isLocalRequest} from '../runtime/validation.ts';
export type AccountRow={account:string;role:string;state:string;enabled?:boolean;legacyLeaseBusy?:boolean;legacyOperation?:string|null;lastLogin?:string|null;nextMaintenance?:string|null;plannedLoginMaintenance?:string|null;identityUpdatedAt?:number|null;evidence?:{checkedAt:number;capabilities:Record<string,string>;tokenHasExplicitExpiry:boolean}|null};
export type AccountStatus={markets:Array<{market:string;state:string;linkCanary?:{state:string;pid?:string;shortName?:string;listId?:string;creatorPercent?:string;publicPercent?:string;totalPercent?:string;agencyPercent?:string;checkedAt?:number}|null;accounts:AccountRow[];pairEvidence:{state:string;sameSender?:boolean;sharedCardCount?:number;concurrentReadSeconds?:number};maintenanceExecutor:string;autoSwitchEnabled:false}>;lifecycle:{healthPollMinutes:null;deepCheckHours:null;identityRefreshHours:number;loginMaintenanceHours:number;standbyEarlyMaintenanceHours:number;globalLoginConcurrency:number;enableNewMaintenanceWorker:false};checkedAt:number;executionEnabled:false;realSends:0};
export function readAccounts():Promise<AccountStatus>{const root=projectRoot();return new Promise((resolve,reject)=>{
 execFile(join(root,'.venv/bin/python'),[join(root,'scripts/market-account-status.py')],{cwd:root,timeout:10000,maxBuffer:131072,env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'}},(e,out)=>{
  try{if(e)throw e;const v=JSON.parse(out);if(v.executionEnabled!==false||v.realSends!==0||!Array.isArray(v.markets)||!v.markets.every((m:AccountStatus['markets'][number])=>Array.isArray(m.accounts)&&m.accounts.length===2))throw Error();resolve(v);}catch{reject(Error('account_status_unavailable'));}
 });
});}
export function createAccountsGet(read=readAccounts){return async(request:Request)=>{
 const headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'};
 if(!isLocalRequest(request,false))return Response.json({error:'local_origin_required'},{status:403,headers});
 if(request.method!=='GET')return Response.json({error:'method_not_allowed'},{status:405,headers});
 if(new URL(request.url).search)return Response.json({error:'invalid_query'},{status:400,headers});
 try{return Response.json(await read(),{headers});}catch{return Response.json({error:'account_status_unavailable'},{status:503,headers});}
};}
