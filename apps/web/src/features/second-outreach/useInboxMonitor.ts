"use client";
import {useCallback,useEffect,useState} from "react";
import type {InboxConfig,InboxState} from "../../server/inbox/bridge";

/** One owner for the inbox-monitor fetch, so the card is the only thing polling it. */
export type InboxController={data:InboxState|null;draft:InboxConfig|null;
 setDraft:(value:InboxConfig)=>void;busy:boolean;message:string|null;
 range:7|14|30;setRange:(value:7|14|30)=>void;reload:()=>Promise<void>;save:()=>Promise<void>;start:()=>Promise<void>;stop:()=>Promise<void>;loaded:boolean};

export function useInboxMonitor():InboxController{
 const [data,setData]=useState<InboxState|null>(null);
 const [draft,setDraft]=useState<InboxConfig|null>(null);
 const [busy,setBusy]=useState(false);
 const [message,setMessage]=useState<string|null>(null);
 const [loaded,setLoaded]=useState(false);
 const [range,setRange]=useState<7|14|30>(14);
 const reload=useCallback(async()=>{
  // 读的是同一个共享库；一次读被拒不是这一阶段的事实，重试后就忽略，卡片保留上一次的数字。
  for(let attempt=0;attempt<3;attempt+=1){
   const r=await fetch(`/api/inbox?days=${range}`,{cache:"no-store"}).catch(()=>null);
   if(r?.ok){const value:InboxState=await r.json();setData(value);setDraft(value.config);setLoaded(true);return;}
   await new Promise(resolve=>setTimeout(resolve,400));
  }
  throw Error('inbox_unavailable');
 },[range]);
 useEffect(()=>{void reload().catch(()=>{});},[reload]);
 // 监控是常驻的，所以只要它在跑就跟着刷；没在跑就不轮询（不制造假的"正在监控"）。
 useEffect(()=>{if(!data?.run?.running)return;const timer=setInterval(()=>void reload().catch(()=>{}),10000);return()=>clearInterval(timer);},[data?.run?.running,reload]);
 const save=useCallback(async()=>{
  if(!draft)return;setBusy(true);setMessage(null);
  try{
   const r=await fetch("/api/inbox",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action:"save",config:draft})});
   if(!r.ok){setMessage("保存被拒绝：一轮 1\u201312 个会话，间隔 30\u20133600 秒。");return;}
   const value:InboxState=await r.json();setData(value);setDraft(value.config);
   setMessage("设置已保存。它只影响下一轮什么时候读，不会自己开始跑。");
  }catch{setMessage("暂时无法读取或保存监控设置。");}
  finally{setBusy(false);}
 },[draft]);
 const start=useCallback(async()=>{
  if(!draft)return;setBusy(true);setMessage(null);
  try{
   const r=await fetch("/api/inbox",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action:"start",config:draft})});
   const value=await r.json();
   if(!r.ok){setMessage(r.status===409?"已经有一个监控在跑，不用再开一个。":value?.error==="invalid_inbox_request"?"请求被拒绝：检查两个数值。":"暂时无法启动。");return;}
   setData(value);
   setMessage("已开始监控。只读已索引的会话，不发任何消息；补身份/发送跑批时它会自动退避，收到的东西不会丢。");
  }catch{setMessage("暂时无法启动。");}
  finally{setBusy(false);}
 },[draft]);
 const stop=useCallback(async()=>{
  setBusy(true);setMessage(null);
  try{
   const r=await fetch("/api/inbox",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action:"stop"})});
   const value=await r.json();
   if(!r.ok){setMessage(r.status===409?"监控没在跑。":"暂时无法停止。");return;}
   setData(value);
   setMessage("已请求停止：读完这一轮就停，已经收到的回复都在库里，下次接着读。");
  }catch{setMessage("暂时无法停止。");}
  finally{setBusy(false);}
 },[]);
 return {data,draft,setDraft,busy,message,range,setRange,reload,save,start,stop,loaded};
}
