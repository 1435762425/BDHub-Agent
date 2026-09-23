"use client";
import {useCallback,useEffect,useState} from "react";
import type {CatalogJobsState,JobConfig} from "../../server/catalog-jobs/bridge";

/** Owns the two catalogue actions so the page and its buttons read the same running state. */
export type CatalogJobName="selection"|"links"|"linksCampaign"|"campaignCollect";
export type CatalogJobsController={available:boolean;data:CatalogJobsState|null;draft:Record<string,JobConfig>;setDraft:(name:string,config:JobConfig)=>void;busy:string|null;message:string|null;reload:()=>Promise<void>;start:(name:CatalogJobName)=>Promise<void>;stop:(name:CatalogJobName)=>Promise<void>};

export function useCatalogJobs(market:string):CatalogJobsController{
 const available=market==="it";
 const [data,setData]=useState<CatalogJobsState|null>(null);
 const [draft,setDrafts]=useState<Record<string,JobConfig>>({});
 const [busy,setBusy]=useState<string|null>(null);
 const [message,setMessage]=useState<string|null>(null);
 const reload=useCallback(async()=>{
  if(!available)return;
  const r=await fetch(`/api/catalog-jobs?market=${encodeURIComponent(market)}`,{cache:"no-store"});
  if(!r.ok)throw Error();
  const value:CatalogJobsState=await r.json();
  setData(value);
  setDrafts({selection:value.selection.config,links:value.links.config,linksCampaign:value.linksCampaign.config,campaignCollect:value.campaignCollect.config});
 },[available,market]);
 useEffect(()=>{void reload().catch(()=>setData(null));},[reload]);
 const running=Boolean(data?.selection.run?.running||data?.links.run?.running||data?.linksCampaign.run?.running||data?.campaignCollect.run?.running);
 // Only poll while a job is actually running; these are real platform calls, not a demo.
 useEffect(()=>{if(!running)return;const timer=setInterval(()=>void reload().catch(()=>{}),5000);return()=>clearInterval(timer);},[running,reload]);
 const setDraft=useCallback((name:string,config:JobConfig)=>setDrafts(current=>({...current,[name]:config})),[]);
 const start=useCallback(async(name:CatalogJobName)=>{
  if(!available){setMessage("当前市场由自动运营 workflow 管理，页面不会回退启动意大利作业。");return;}
  setBusy(name);setMessage(null);
  try{
   const r=await fetch(`/api/catalog-jobs?market=${encodeURIComponent(market)}`,{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({action:"start",market,name,config:draft[name]})});
   const value=await r.json();
   if(!r.ok){setMessage(r.status===409?"已经有一批在跑，等它结束再点。":"暂时无法启动。");return;}
   setData(value);
   setMessage(name==="selection"
    ?"已开始选入。这是平台写入：每个商品先冻结意图再提交，提交后逐条回查；结果未知的按原意图核验，不会重复选入。"
    :name==="campaignCollect"
     ?"已开始采集非全托的活动与商品（只读平台）。读完会自动按当前门槛重新筛分入池，所以刷新完池子就是新的。"
    :name==="linksCampaign"
     ?((draft[name]?.creates??0)>0
       ?"已开始非全托标准链接准备。先识别完全符合当前规则的标准卡，再按上限补建缺少的部分；旧卡只保留历史。"
       :"已开始非全托标准链接检查（只查不建）。旧卡只记录为历史，不会成为新的发送材料。")
     :(draft[name]?.creates??0)>0
      ?"已开始准备当前标准链接。完全一致的标准卡直接登记，其余按上限补建；旧卡只保留历史。"
      :"已开始检查当前标准链接（只查不建）。旧卡不会被复用，也不会被删除。");
  }catch{setMessage("暂时无法启动。");}
  finally{setBusy(null);}
 },[available,draft,market]);
 const stop=useCallback(async(name:CatalogJobName)=>{
  if(!available){setMessage("当前市场没有可停止的意大利兼容作业。");return;}
  setBusy(name);setMessage(null);
  try{
   const r=await fetch(`/api/catalog-jobs?market=${encodeURIComponent(market)}`,{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({action:"stop",market,name})});
   const value=await r.json();
   if(!r.ok){setMessage("暂时停不下来，稍后再试。");return;}
   setData(value);setMessage("已请求停止：作业会在下一个安全点结束，已经写出去的不会丢。");
  }catch{setMessage("暂时停不下来，稍后再试。");}
  finally{setBusy(null);}
 },[available,market]);
 return {available,data,draft,setDraft,busy,message,reload,start,stop};
}
