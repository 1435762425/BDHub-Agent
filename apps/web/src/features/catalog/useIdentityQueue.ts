"use client";
import {useCallback,useEffect,useState} from "react";
import type {IdentityConfig,IdentityQueueState} from "../../server/identity-queue/bridge";

/** One owner for the identity stage fetch, so the card is the only thing polling it. */
export type IdentityController={data:IdentityQueueState|null;draft:IdentityConfig|null;
 setDraft:(value:IdentityConfig)=>void;busy:boolean;message:string|null;
 reload:()=>Promise<void>;save:()=>Promise<void>;start:()=>Promise<void>;stop:()=>Promise<void>;loaded:boolean};

export function useIdentityQueue():IdentityController{
 const [data,setData]=useState<IdentityQueueState|null>(null);
 const [draft,setDraft]=useState<IdentityConfig|null>(null);
 const [busy,setBusy]=useState(false);
 const [message,setMessage]=useState<string|null>(null);
 // A first read that has not landed yet is "loading", not "unavailable": the numbers take about a
 // second to compute, and calling that a failure blamed the stage for being slow.
 const [loaded,setLoaded]=useState(false);
 const reload=useCallback(async()=>{
  // Same shared database as the other stages. A refused read is retried and then ignored: the card
  // keeps its last numbers, because "unavailable" for one poll is not a fact about the stage.
  for(let attempt=0;attempt<3;attempt+=1){
   const r=await fetch("/api/identity-queue",{cache:"no-store"}).catch(()=>null);
   if(r?.ok){const value:IdentityQueueState=await r.json();setData(value);setDraft(value.config);setLoaded(true);return;}
   await new Promise(resolve=>setTimeout(resolve,400));
  }
  throw Error('identity_queue_unavailable');
 },[]);
 useEffect(()=>{void reload().catch(()=>{});},[reload]);
 // Only while a backfill is actually running does the page need to poll.
 useEffect(()=>{if(!data?.run?.running)return;const timer=setInterval(()=>void reload().catch(()=>{}),4000);return()=>clearInterval(timer);},[data?.run?.running,reload]);
 const save=useCallback(async()=>{
  if(!draft)return;setBusy(true);setMessage(null);
  try{
   const r=await fetch("/api/identity-queue",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action:"save",config:draft})});
   if(!r.ok){setMessage("保存被拒绝：一次上限允许 1\u2013200000，每轮只能是 10 / 20 / 50 个 handle。");return;}
   const value:IdentityQueueState=await r.json();setData(value);setDraft(value.config);
   setMessage("设置已保存。上限只影响下次补多少，不会自己开始跑。");
  }catch{setMessage("暂时无法读取或保存身份设置。");}
  finally{setBusy(false);}
 },[draft]);
 const start=useCallback(async()=>{
  if(!draft)return;setBusy(true);setMessage(null);
  try{
   const r=await fetch("/api/identity-queue",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action:"start",config:draft})});
   const value=await r.json();
   if(!r.ok){setMessage(r.status===409?"已经有一批在补，等它结束再点。":value?.error==="invalid_identity_queue_request"?"请求被拒绝：检查两个数值。":"暂时无法启动。");return;}
   setData(value);
   setMessage("已开始补充身份。搜索不到的达人会单独记账。");
  }catch{setMessage("暂时无法启动。");}
  finally{setBusy(false);}
 },[draft]);
 const stop=useCallback(async()=>{
  setBusy(true);setMessage(null);
  try{
   const r=await fetch("/api/identity-queue",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action:"stop"})});
   const value=await r.json();
   if(!r.ok){setMessage(r.status===409?"这一批已经结束了。":"暂时无法停止。");return;}
   setData(value);
   setMessage("已请求停止：跑完当前这一轮就停，已经补到的身份都保留，下次接着跑。");
  }catch{setMessage("暂时无法停止。");}
  finally{setBusy(false);}
 },[]);
 return {data,draft,setDraft,busy,message,reload,save,start,stop,loaded};
}
