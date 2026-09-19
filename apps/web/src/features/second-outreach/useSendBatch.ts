"use client";
import {useCallback,useEffect,useState} from "react";
import type {SendConfig,SendState} from "./send-contracts";

/** 发送池与发送这一块只由这张卡自己读，别处不再重复请求。 */
export type SendController={data:SendState|null;draft:SendConfig|null;
 setDraft:(value:SendConfig)=>void;busy:boolean;message:string|null;
 loaded:boolean;reload:()=>Promise<void>;save:()=>Promise<void>};

export function useSendBatch():SendController{
 const [data,setData]=useState<SendState|null>(null);
 const [draft,setDraft]=useState<SendConfig|null>(null);
 const [busy,setBusy]=useState(false);
 const [message,setMessage]=useState<string|null>(null);
 const [loaded,setLoaded]=useState(false);
 const reload=useCallback(async()=>{
  // 预检要逐条复检池位（缺卡片、被关系控制都要挑出来），一次读可能被共享库挡住；
  // 重试后就认了，卡片保留上一次的数字，不假装池子是空的。
  for(let attempt=0;attempt<3;attempt+=1){
   const r=await fetch("/api/send",{cache:"no-store"}).catch(()=>null);
   if(r?.ok){const value:SendState=await r.json();setData(value);setDraft(value.config);setLoaded(true);return;}
   await new Promise(resolve=>setTimeout(resolve,500));
  }
  throw Error('send_batch_unavailable');
 },[]);
 useEffect(()=>{void reload().catch(()=>{});},[reload]);
 const save=useCallback(async()=>{
  if(!draft)return;setBusy(true);setMessage(null);
  try{
   const r=await fetch("/api/send",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({action:"save",config:draft})});
   if(!r.ok){setMessage("设置被拒绝：一批 1\u20132000 条，窗口形如 09:00\u201324:00。");return;}
   const value:SendState=await r.json();setData(value);setDraft(value.config);
   setMessage("设置已保存。它只决定下一批的规模和窗口，不会自己开始发。");
  }catch{setMessage("暂时无法读取或保存发送设置。");}
  finally{setBusy(false);}
 },[draft]);
 return {data,draft,setDraft,busy,message,loaded,reload,save};
}
