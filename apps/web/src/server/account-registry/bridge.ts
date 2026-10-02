import {execFile} from 'node:child_process';
import {join} from 'node:path';
import {projectRoot} from '../runtime/project-root.ts';
import {isLocalRequest} from '../runtime/validation.ts';

export type RegistryRow={account:string;market:string|null;partnerId:string|null;usernameSet:boolean;passwordSet:boolean;assignment:{market:string;role:string}|null};
export type Registry={accounts:RegistryRow[];secretsReturned:0};
export type RegistryMutation={action:'add';account:string;market:string;partnerId:string;username:string;password:string}|{action:'update';account:string;username?:string;password?:string};
type Runner=(action:'list'|'add'|'update',input?:Record<string,string>)=>Promise<Registry>;

const ACCOUNT=/^acc[1-9][0-9]{0,5}$/,MARKET=/^[a-z]{2}$/,PARTNER=/^[0-9]{1,30}$/;
const secret=(value:unknown)=>typeof value==='string'&&value.length>0&&value.length<=256&&value===value.trim()&&!/[\x00-\x1f\x7f]/.test(value);
const headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'};

function decode(stdout:string):Registry{
 const value=JSON.parse(stdout);
 if(value?.error)throw Error(String(value.error));
 if(value?.secretsReturned!==0||!Array.isArray(value.accounts)||!value.accounts.every((row:RegistryRow)=>ACCOUNT.test(row?.account)&&typeof row.usernameSet==='boolean'&&typeof row.passwordSet==='boolean'))throw Error('account_registry_unavailable');
 return value;
}

// Secrets travel on stdin, never in argv.
export const runRegistry:Runner=(action,input)=>{const root=projectRoot();return new Promise((resolve,reject)=>{
 const child=execFile(join(root,'.venv/bin/python'),[join(root,'scripts/account-registry.py'),action],{cwd:root,timeout:15000,maxBuffer:131072,env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'}},(error,stdout)=>{
  try{resolve(decode(stdout));}catch(decoded){reject(error&&!(decoded instanceof Error&&decoded.message.startsWith('account_'))?Error('account_registry_unavailable'):decoded);}
 });
 child.stdin?.end(input?JSON.stringify(input):'');
});};

export function validateRegistryMutation(raw:unknown):RegistryMutation{
 if(!raw||typeof raw!=='object'||Array.isArray(raw))throw Error('invalid_registry_request');
 const value=raw as Record<string,unknown>;
 if(typeof value.account!=='string'||!ACCOUNT.test(value.account))throw Error('invalid_registry_request');
 if(value.action==='add'){
  if(Object.keys(value).some(key=>!['action','account','market','partnerId','username','password'].includes(key))||typeof value.market!=='string'||!MARKET.test(value.market)||typeof value.partnerId!=='string'||!PARTNER.test(value.partnerId)||!secret(value.username)||!secret(value.password))throw Error('invalid_registry_request');
  return {action:'add',account:value.account,market:value.market,partnerId:value.partnerId,username:value.username as string,password:value.password as string};
 }
 if(value.action==='update'){
  if(Object.keys(value).some(key=>!['action','account','username','password'].includes(key))||(value.username===undefined&&value.password===undefined)||(value.username!==undefined&&!secret(value.username))||(value.password!==undefined&&!secret(value.password)))throw Error('invalid_registry_request');
  return {action:'update',account:value.account,...(value.username!==undefined?{username:value.username as string}:{}),...(value.password!==undefined?{password:value.password as string}:{})};
 }
 throw Error('invalid_registry_request');
}

export function createRegistryHandlers(run:Runner=runRegistry){
 const GET=async(request:Request)=>{
  if(!isLocalRequest(request,false))return Response.json({error:'local_origin_required'},{status:403,headers});
  if([...new URL(request.url).searchParams.keys()].length)return Response.json({error:'invalid_query'},{status:400,headers});
  try{return Response.json(await run('list'),{headers});}catch{return Response.json({error:'account_registry_unavailable'},{status:503,headers});}
 };
 const POST=async(request:Request)=>{
  if(!isLocalRequest(request,true))return Response.json({error:'local_origin_required'},{status:403,headers});
  let input:RegistryMutation;
  try{
   if([...new URL(request.url).searchParams.keys()].length)throw Error();
   if(request.headers.get('content-type')?.split(';')[0].trim()!=='application/json')return Response.json({error:'json_required'},{status:415,headers});
   const raw=await request.text();if(new TextEncoder().encode(raw).length>4096)throw Error();
   input=validateRegistryMutation(JSON.parse(raw));
  }catch{return Response.json({error:'invalid_registry_request'},{status:400,headers});}
  const {action,...body}=input;
  try{return Response.json(await run(action,body as Record<string,string>),{headers});}
  catch(error){const code=error instanceof Error&&/^account_[a-z_]+$/.test(error.message)?error.message:'account_registry_unavailable';
   return Response.json({error:code},{status:code==='account_exists'||code==='account_missing'?409:code==='account_registry_unavailable'?503:400,headers});}
 };
 return {GET,POST};
}
