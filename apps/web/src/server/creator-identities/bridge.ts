import {execFile} from "node:child_process";
import {join} from "node:path";
import type {CreatorIdentityQuery} from "../../features/creator-identities/contracts.ts";
import {projectRoot} from "../runtime/project-root.ts";
import {CreatorIdentityError} from "./errors.ts";
import {validateCreatorIdentityResult,type CreatorIdentityResponse} from "./read-contract.ts";

const ERRORS:Record<string,{status:number;message:string}>={
 invalid_request:{status:400,message:"身份查询格式不正确。"},creator_not_found:{status:404,message:"当前市场没有这位达人。"},
 identity_schema_mismatch:{status:503,message:"身份资料格式不兼容，请检查导入结果。"},identity_unavailable:{status:503,message:"身份资料暂不可用，请稍后重试。"},
};
const unavailable=()=>new CreatorIdentityError(503,"identity_unavailable",ERRORS.identity_unavailable.message);

// Query data goes only to stdin; callers cannot supply a program, file, database path or node flag.
export function creatorIdentityReadCommand(root:string){return {file:process.execPath,args:["--experimental-strip-types",join(root,"scripts/read-creator-identities.mjs")],cwd:join(root,"apps/web")};}

export function createCreatorIdentityReader(execute:typeof execFile=execFile){
 return (query:CreatorIdentityQuery,signal?:AbortSignal):Promise<CreatorIdentityResponse>=>{
  const command=creatorIdentityReadCommand(projectRoot());
  if(signal?.aborted)return Promise.reject(unavailable());
  return new Promise((resolve,reject)=>{
   const child=execute(command.file,command.args,{cwd:command.cwd,timeout:30_000,killSignal:"SIGKILL",maxBuffer:4*1024*1024,env:{...process.env}},(error,stdout)=>{
    signal?.removeEventListener("abort",abort);
    try{
     const value=JSON.parse(String(stdout));
     if(value?.ok===false){const known=ERRORS[value.error?.code];if(known&&value.error?.status===known.status)throw new CreatorIdentityError(known.status,value.error.code,known.message);throw unavailable();}
     if(error||value?.ok!==true)throw unavailable();
     const result=validateCreatorIdentityResult(value.data,query);
     if(query.view==="detail"&&"creator" in result&&result.creator===null)throw new CreatorIdentityError(404,"creator_not_found",ERRORS.creator_not_found.message);
     resolve(result);
    }catch(caught){reject(caught instanceof CreatorIdentityError?caught:unavailable());}
   });
   // execFile's AbortSignal may use SIGTERM independently of its timeout killSignal. A read-only
   // SQLite call must also stop promptly when the browser cancels while native SQL is running.
   const abort=()=>{child.kill("SIGKILL");};
   signal?.addEventListener("abort",abort,{once:true});
   if(signal?.aborted)abort();
   child.stdin?.on("error",()=>{});
   child.stdin?.end(JSON.stringify(query));
  });
 };
}
export const readCreatorIdentities=createCreatorIdentityReader();
