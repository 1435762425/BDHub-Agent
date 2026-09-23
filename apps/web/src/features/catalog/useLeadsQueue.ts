"use client";
import {useCallback,useEffect,useState} from "react";
import type {LeadsQueueConfig,LeadsQueueState} from "../../server/leads-queue/bridge";

/** One owner for the queue fetch, so the funnel bar and the queue panel never poll it twice. */
export type LeadsQueueController={data:LeadsQueueState|null;draft:LeadsQueueConfig|null;setDraft:(value:LeadsQueueConfig)=>void;busy:boolean;message:string|null;reload:()=>Promise<void>;save:()=>Promise<void>;startRun:()=>Promise<void>;loaded:boolean};

export function useLeadsQueue(market:string):LeadsQueueController{
 const [data,setData]=useState<LeadsQueueState|null>(null);
 const [draft,setDraft]=useState<LeadsQueueConfig|null>(null);
 const [busy,setBusy]=useState(false);
 const [message,setMessage]=useState<string|null>(null);
 // "not read yet" is not "could not read": the first read takes a moment.
 const [loaded,setLoaded]=useState(false);
 const reload=useCallback(async()=>{
  // Same shared database as the identity stage: a single refused read must not blank the card, and
  // the operator's unsaved edits to the settings must survive a failed poll.
  for(let attempt=0;attempt<3;attempt+=1){
   const r=await fetch(`/api/leads-queue?market=${encodeURIComponent(market)}`,{cache:"no-store"}).catch(()=>null);
   if(r?.ok){const value:LeadsQueueState=await r.json();setData(value);setDraft(value.config);setLoaded(true);return;}
   await new Promise(resolve=>setTimeout(resolve,400));
  }
  throw Error('leads_queue_unavailable');
 },[market]);
 useEffect(()=>{void reload().catch(()=>{});},[reload]);
 // Only while a batch is actually running does the page need to poll.
 useEffect(()=>{if(!data?.run?.running)return;const timer=setInterval(()=>void reload().catch(()=>{}),4000);return()=>clearInterval(timer);},[data?.run?.running,reload]);
 const save=useCallback(async()=>{
  if(!draft)return;setBusy(true);setMessage(null);
  try{
   const r=await fetch(`/api/leads-queue?market=${encodeURIComponent(market)}`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action:"save",market,config:draft})});
   if(!r.ok){setMessage("保存被拒绝：刷新周期允许 1\u201390 天，批量上限 1\u20135000。");return;}
   const value:LeadsQueueState=await r.json();setData(value);setDraft(value.config);
   setMessage("队列设置已保存。刷新周期只影响以后到期的判断；批量上限只影响下次取多少条。");
  }catch{setMessage("暂时无法读取或保存队列设置。");}
  finally{setBusy(false);}
 },[draft,market]);
 const startRun=useCallback(async()=>{
  setBusy(true);setMessage(null);
  try{
   const r=await fetch(`/api/leads-queue?market=${encodeURIComponent(market)}`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action:"run",market})});
   const value=await r.json();
   if(!r.ok){setMessage(r.status===409?"已经有一批在跑，等它结束再点。":"暂时无法启动查询。");return;}
   setData(value);
   setMessage("已启动。这一批是只读查询，不发消息；额度用完会自动停下并保留断点，剩下的下次再跑。");
  }catch{setMessage("暂时无法启动查询。");}
  finally{setBusy(false);}
 },[market]);
 return {data,draft,setDraft,busy,message,reload,save,startRun,loaded};
}
