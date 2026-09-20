"use client";
import {useCallback,useEffect,useRef,useState} from "react";
import type {SendConfig,SendState} from "./send-contracts";

/** 发送池与发送这一块只由这张卡自己读，别处不再重复请求。 */
export type SendController={data:SendState|null;draft:SendConfig|null;
 setDraft:(value:SendConfig)=>void;busy:boolean;message:string|null;
 loaded:boolean;reload:()=>Promise<void>;save:()=>Promise<void>;
 freeze:()=>Promise<void>;start:()=>Promise<void>;stop:()=>Promise<void>;reconcile:(deliveryId:string)=>Promise<void>};

export function useSendBatch():SendController{
 const [data,setData]=useState<SendState|null>(null);
 const [draft,setDraft]=useState<SendConfig|null>(null);
 const [busy,setBusy]=useState(false);
 const [message,setMessage]=useState<string|null>(null);
 const [loaded,setLoaded]=useState(false);
 const requestId=useRef<string|null>(null);
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
   setMessage("设置已保存。人数、窗口和话术模板已进入新预览；不会自己开始发。");
  }catch{setMessage("暂时无法读取或保存发送设置。");}
  finally{setBusy(false);}
 },[draft]);
 const action=useCallback(async(body:Record<string,unknown>,success:string)=>{
  setBusy(true);setMessage(null);
  try{
   const r=await fetch("/api/send",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify(body)});
   if(!r.ok){
    const problem=await r.json().catch(()=>({})) as {error?:string};
    if(r.status===409){await reload().catch(()=>{});setMessage("预览或批次状态已经变化，已保留现有批次并刷新页面，请重新核对。");return;}
    setMessage(problem.error==="batch_empty"?"当前没有可冻结的位置。":problem.error==="full_preparation_required"?"正式目标和 10% 候补尚未备齐，不能冻结；原目标不会被缩小。":"这个批次动作被拒绝，请刷新后再核对。");return;
   }
   const value:SendState=await r.json();setData(value);setDraft(value.config);setMessage(success);
  }catch{setMessage("暂时无法完成这个批次动作。");}
  finally{setBusy(false);}
 },[reload]);
 const freeze=useCallback(async()=>{
  const hash=data?.preview.previewHash;
  if(!hash)return;
  requestId.current??=`web-${crypto.randomUUID()}`;
  await action({action:"freeze",requestId:requestId.current,expectedPreviewHash:hash},
   "本批已冻结。名单、PID、Offer、currentListId、模板、最终话术和顺序不会再变化；还没有开始发送。");
 },[action,data]);
 const start=useCallback(async()=>{
  if(!data?.batch)return;
  await action({action:"start",batchId:data.batch.batchId,expectedRevision:data.batch.revision,confirmed:true},
   "已提交开始指令。执行器只会消费本批冻结材料；遇到未知结果会整批暂停核验。");
 },[action,data]);
 const stop=useCallback(async()=>{
  if(!data?.batch)return;
  await action({action:"stop",batchId:data.batch.batchId,expectedRevision:data.batch.revision},
   "已请求停止。执行器会在安全点退出，不会把在途结果当成未发送。");
 },[action,data]);
 const reconcile=useCallback(async(deliveryId:string)=>{
  if(!data?.batch)return;
  await action({action:"reconcile",batchId:data.batch.batchId,deliveryId,expectedRevision:data.batch.revision,confirmed:true},
   "已核验原发送意图；若证据仍不足，批次会继续保持待核验，不会重发。");
 },[action,data]);
 return {data,draft,setDraft,busy,message,loaded,reload,save,freeze,start,stop,reconcile};
}
