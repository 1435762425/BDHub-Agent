import {execFile} from "node:child_process";
import {join} from "node:path";
import {projectRoot} from "../creator-identities/refresh.ts";
import {isLocalRequest} from "../runtime/validation.ts";
export type PreparationStatus={globalSource?:{available:boolean;state:string;products:number;reportedTotal:number|null;published:boolean;detailProducts:number;listedUnselectedProducts:number};schema:"bdhub.batch-preparation.v1";market:string;institution:string;target:number;reserve:number;required:number;identityCandidates:number;selectedCandidates:number;localMaterialsComplete:number;candidateGap:number;materialGap:number;productGroups:number;namesNeeded:number;taplinksToCheckOrCreate:number;liveEligibilityChecked:false;taskCreated:false;executionAllowed:false;products:Array<{offerKey:string;pid:string;shortName:string|null;people:number;creatorPercent:string;nameReady:boolean;cardLocated:boolean}>};
export function validatePreparation(v:unknown):PreparationStatus{
 if(!v||typeof v!=="object")throw new Error("invalid_preparation");const p=v as PreparationStatus;
 if(p.schema!=="bdhub.batch-preparation.v1"||p.executionAllowed!==false||p.taskCreated!==false||p.liveEligibilityChecked!==false)throw new Error("invalid_preparation");
 for(const k of ["target","reserve","required","identityCandidates","selectedCandidates","localMaterialsComplete","candidateGap","materialGap","productGroups","namesNeeded","taplinksToCheckOrCreate"] as const)if(!Number.isSafeInteger(p[k])||p[k]<0)throw new Error("invalid_preparation");
 if(p.target<1||p.target>100000||p.reserve!==Math.ceil(p.target/10)||p.required!==p.target+p.reserve||p.localMaterialsComplete>p.selectedCandidates||p.selectedCandidates>p.required||p.selectedCandidates>p.identityCandidates||!Array.isArray(p.products)||p.products.length>20)throw new Error("invalid_preparation");
 return p;
}
export function readPreparation(target:number):Promise<PreparationStatus>{const root=projectRoot();return new Promise((resolve,reject)=>{
 const child=execFile(join(root,".venv/bin/python"),[join(root,"scripts/inspect-batch-preparation.py")],{cwd:root,timeout:30000,maxBuffer:1024*1024,env:{...process.env,PYTHONDONTWRITEBYTECODE:"1"}},(error,stdout)=>{try{if(error)throw error;resolve(validatePreparation(JSON.parse(stdout)));}catch{reject(new Error("preparation_unavailable"));}});
 child.stdin?.end(JSON.stringify({target}));
});}
export function createPreparationPost(read=readPreparation){return async(request:Request)=>{
 const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};
 if(!isLocalRequest(request,true))return Response.json({error:"local_origin_required"},{status:403,headers});
 let body: {target:number};
 try{
  const raw=await request.text();if(raw.length>2048)throw new Error();body=JSON.parse(raw);
  if(!body||typeof body!=="object"||Array.isArray(body)||Object.keys(body).length!==1||!Number.isSafeInteger(body.target)||body.target<1||body.target>100000)throw new Error();
 }catch{return Response.json({error:"numeric_target_required"},{status:400,headers});}
 try{const result=validatePreparation(await read(body.target));if(result.target!==body.target)throw new Error();return Response.json(result,{headers});}
 catch{return Response.json({error:"preparation_unavailable"},{status:503,headers});}
};}
