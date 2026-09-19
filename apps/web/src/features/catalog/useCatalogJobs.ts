"use client";
import {useCallback,useEffect,useState} from "react";
import type {CatalogJobsState,JobConfig} from "../../server/catalog-jobs/bridge";

/** Owns the two catalogue actions so the page and its buttons read the same running state. */
export type CatalogJobName="selection"|"links"|"linksCampaign"|"campaignCollect";
export type CatalogJobsController={data:CatalogJobsState|null;draft:Record<string,JobConfig>;setDraft:(name:string,config:JobConfig)=>void;busy:string|null;message:string|null;reload:()=>Promise<void>;start:(name:CatalogJobName)=>Promise<void>;stop:(name:CatalogJobName)=>Promise<void>};

export function useCatalogJobs():CatalogJobsController{
 const [data,setData]=useState<CatalogJobsState|null>(null);
 const [draft,setDrafts]=useState<Record<string,JobConfig>>({});
 const [busy,setBusy]=useState<string|null>(null);
 const [message,setMessage]=useState<string|null>(null);
 const reload=useCallback(async()=>{
  const r=await fetch("/api/catalog-jobs",{cache:"no-store"});
  if(!r.ok)throw Error();
  const value:CatalogJobsState=await r.json();
  setData(value);
  setDrafts({selection:value.selection.config,links:value.links.config,linksCampaign:value.linksCampaign.config,campaignCollect:value.campaignCollect.config});
 },[]);
 useEffect(()=>{void reload().catch(()=>setData(null));},[reload]);
 const running=Boolean(data?.selection.run?.running||data?.links.run?.running||data?.linksCampaign.run?.running||data?.campaignCollect.run?.running);
 // Only poll while a job is actually running; these are real platform calls, not a demo.
 useEffect(()=>{if(!running)return;const timer=setInterval(()=>void reload().catch(()=>{}),5000);return()=>clearInterval(timer);},[running,reload]);
 const setDraft=useCallback((name:string,config:JobConfig)=>setDrafts(current=>({...current,[name]:config})),[]);
 const start=useCallback(async(name:CatalogJobName)=>{
  setBusy(name);setMessage(null);
  try{
   const r=await fetch("/api/catalog-jobs",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({action:"start",name,config:draft[name]})});
   const value=await r.json();
   if(!r.ok){setMessage(r.status===409?"已经有一批在跑，等它结束再点。":"暂时无法启动。");return;}
   setData(value);
   setMessage(name==="selection"
    ?"已开始选入。这是平台写入：每个商品先冻结意图再提交，提交后逐条回查；结果未知的按原意图核验，不会重复选入。"
    :name==="campaignCollect"
     ?"已开始采集非全托的活动与商品（只读平台）。读完会自动按当前门槛重新筛分入池，所以刷新完池子就是新的。"
    :name==="linksCampaign"
     ?((draft[name]?.creates??0)>0
       ?"已开始非全托准备链接。先查已有链接并复用，再按上限新建缺的那部分；每一步都有回执，可以安全停下再继续。"
       :"已开始非全托准备链接（只查不建）。按活动逐个读卡，摸清哪些商品已有可复用链接、哪些确实缺链，不会新建任何链接。")
     :(draft[name]?.creates??0)>0
      ?"已开始准备链接。先查已有链接并复用，再按上限新建缺的那部分；每一步都有回执，可以安全停下再继续。"
      :"已开始准备链接（只查不建）。先摸清哪些商品已有可复用链接、哪些确实缺链，不会新建任何链接。");
  }catch{setMessage("暂时无法启动。");}
  finally{setBusy(null);}
 },[draft]);
 const stop=useCallback(async(name:CatalogJobName)=>{
  setBusy(name);setMessage(null);
  try{
   const r=await fetch("/api/catalog-jobs",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({action:"stop",name})});
   const value=await r.json();
   if(!r.ok){setMessage("暂时停不下来，稍后再试。");return;}
   setData(value);setMessage("已请求停止：作业会在下一个安全点结束，已经写出去的不会丢。");
  }catch{setMessage("暂时停不下来，稍后再试。");}
  finally{setBusy(null);}
 },[]);
 return {data,draft,setDraft,busy,message,reload,start,stop};
}
