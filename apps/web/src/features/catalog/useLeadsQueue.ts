"use client";
import {useCallback,useEffect,useRef,useState} from "react";
import type {LeadsQueueConfig,LeadsQueueState} from "../../server/leads-queue/bridge";

/** One owner for the queue fetch, so the funnel bar and the queue panel never poll it twice. */
// failed/lastSuccessfulAt: a refresh that failed keeps the last good data, marked stale (I03).
export type LeadsQueueController={data:LeadsQueueState|null;draft:LeadsQueueConfig|null;setDraft:(value:LeadsQueueConfig)=>void;busy:boolean;message:string|null;reload:()=>Promise<void>;save:()=>Promise<void>;startRun:()=>Promise<void>;loaded:boolean;failed:boolean;lastSuccessfulAt:number|null};

export function useLeadsQueue(market:string):LeadsQueueController{
 const [data,setData]=useState<LeadsQueueState|null>(null);
 const [draft,setDraft]=useState<LeadsQueueConfig|null>(null);
 const [busy,setBusy]=useState(false);
 const [message,setMessage]=useState<string|null>(null);
 // "not read yet" is not "could not read": the first read takes a moment.
 const [loaded,setLoaded]=useState(false);
 const [failed,setFailed]=useState(false),[lastSuccessfulAt,setLastSuccessfulAt]=useState<number|null>(null);
 // The settings last read from the server: a background refresh only replaces a draft nobody edited.
 const savedConfig=useRef<string|null>(null);
 const adopt=useCallback((value:LeadsQueueState,force=false)=>{
  setData(value);setFailed(false);setLoaded(true);setLastSuccessfulAt(Date.now()/1000);
  const incoming=JSON.stringify(value.config);
  setDraft(current=>force||current==null||JSON.stringify(current)===savedConfig.current?value.config:current);
  savedConfig.current=incoming;
 },[]);
 const reload=useCallback(async()=>{
  // Same shared database as the identity stage: a single refused read must not blank the card, and
  // the operator's unsaved edits to the settings must survive a failed poll.
  for(let attempt=0;attempt<3;attempt+=1){
   const r=await fetch(`/api/leads-queue?market=${encodeURIComponent(market)}`,{cache:"no-store"}).catch(()=>null);
   if(r?.ok){adopt(await r.json() as LeadsQueueState);return;}
   await new Promise(resolve=>setTimeout(resolve,400));
  }
  // A read that failed ends "loading" and is shown as such; the last good data stays, marked stale.
  setFailed(true);setLoaded(true);
  throw Error('leads_queue_unavailable');
 },[market,adopt]);
 // One bounded poll while the page is visible: queued or running stages every 5 s, otherwise every
 // minute; nothing while hidden, and an immediate read when the page becomes visible again.
 const active=Boolean(data?.run?.running||data?.run?.queued);
 useEffect(()=>{
  let stopped=false,timer:ReturnType<typeof setTimeout>|undefined;
  const poll=async()=>{if(stopped)return;if(document.visibilityState==="visible")await reload().catch(()=>{});if(!stopped)timer=setTimeout(poll,active?5000:60000);};
  const visible=()=>{if(document.visibilityState==="visible"){if(timer)clearTimeout(timer);void poll();}};
  document.addEventListener("visibilitychange",visible);void poll();
  return()=>{stopped=true;if(timer)clearTimeout(timer);document.removeEventListener("visibilitychange",visible);};
 },[reload,active]);
 const save=useCallback(async()=>{
  if(!draft)return;setBusy(true);setMessage(null);
  try{
   const r=await fetch(`/api/leads-queue?market=${encodeURIComponent(market)}`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action:"save",market,config:draft})});
   if(!r.ok){setMessage("保存被拒绝：刷新周期允许 1\u201390 天，批量上限 1\u20135000。");return;}
   adopt(await r.json() as LeadsQueueState,true);
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
   adopt(value as LeadsQueueState);
   setMessage("已提交本次读取与线索衔接流程，不发消息。额度不足保留原断点；开启自动运营的市场会按状态续跑。");
  }catch{setMessage("暂时无法启动查询。");}
  finally{setBusy(false);}
 },[market]);
 return {data,draft,setDraft,busy,message,reload,save,startRun,loaded,failed,lastSuccessfulAt};
}
