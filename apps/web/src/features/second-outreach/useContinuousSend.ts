"use client";
import {useCallback,useEffect,useRef,useState} from "react";
import type {ContinuousSendState} from "./send-contracts";
import {emptySendEditor,receiveSendState,type SendDraft,type SendEditor} from "./send-editor";
export type {SendDraft} from "./send-editor";

export type SendController={data:ContinuousSendState|null;draft:SendDraft|null;setDraft:(value:SendDraft)=>void;
 busy:boolean;message:string|null;loaded:boolean;reload:()=>Promise<void>;save:()=>Promise<void>;
 start:()=>Promise<void>;stop:()=>Promise<void>;reconcile:()=>Promise<void>};

export function useContinuousSend(market:string):SendController {
 const [editor,setEditor]=useState<SendEditor>(emptySendEditor);
 const [busy,setBusy]=useState(false),[message,setMessage]=useState<string|null>(null),[loaded,setLoaded]=useState(false);
 const pending=useRef(new Map<string,{requestId:string;fingerprint:string}>());
 const scope=useRef(market);scope.current=market;
 const {data,draft}=editor;
 const setDraft=useCallback((draft:SendDraft)=>setEditor(current=>({...current,draft})),[]);
 const reload=useCallback(async()=>{
  const response=await fetch(`/api/send?market=${encodeURIComponent(market)}`,{cache:"no-store"});
  if(!response.ok){setLoaded(true);throw Error();}
  const value:ContinuousSendState=await response.json();
  if(scope.current!==market)return;
  setEditor(current=>receiveSendState(current,value));setLoaded(true);
 },[market]);
 useEffect(()=>{
  setEditor(emptySendEditor);setLoaded(false);pending.current.clear();
  let timer:ReturnType<typeof setTimeout>|undefined,inFlight=false,stopped=false;
  const poll=async()=>{
   if(stopped)return;
   if(document.visibilityState!=="visible"){timer=setTimeout(poll,60000);return;}
   if(inFlight)return;
   inFlight=true;
   try{await reload();}catch{}finally{inFlight=false;if(!stopped)timer=setTimeout(poll,10000);}
  };
  const visible=()=>{if(document.visibilityState==="visible"&&!inFlight){if(timer)clearTimeout(timer);void poll();}};
  document.addEventListener("visibilitychange",visible);void poll();
  return()=>{stopped=true;if(timer)clearTimeout(timer);document.removeEventListener("visibilitychange",visible);};
 },[reload]);
 const call=useCallback(async(action:"save"|"start"|"stop"|"reconcile",changes?:Record<string,unknown>)=>{
  if(!data||busy)return;
  setBusy(true);setMessage(null);
  const fingerprint=JSON.stringify({market,action,changes});
  const previous=pending.current.get(action);
  const requestId=previous?.fingerprint===fingerprint?previous.requestId:`continuous-${crypto.randomUUID()}`;
  pending.current.set(action,{requestId,fingerprint});
  try{
   const body=action==="reconcile"?{action,market}:{action,market,requestId,expectedRevision:data.control.revision,...(changes?{changes}:{})};
   const response=await fetch(`/api/send?market=${encodeURIComponent(market)}`,{
    method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body),
   });
   if(!response.ok){if(response.status===409)await reload();throw Error();}
   const value:ContinuousSendState=await response.json();
   if(scope.current!==market)return;
   setEditor(current=>receiveSendState(current,value,action==="save"));
   pending.current.delete(action);
   setMessage(action==="save"?"发送设置已保存；不会因保存而开始发送。":action==="start"?
    "持续发送进程已启动；窗口外会等待，不会绕过 Agent 时间。":action==="stop"?
    "已请求安全停止；在途发送仍按原意图回查。":"已启动原 unknown 意图的只读核验，不会重发。");
  }catch{setMessage("操作未完成，状态可能已在另一窗口变化；未保存的设置已保留，请核对后重试。");}
  finally{setBusy(false);}
 },[data,busy,market,reload]);
 const save=useCallback(async()=>{if(draft)await call("save",draft);},[call,draft]);
 return {data,draft,setDraft,busy,message:message??(editor.conflict?"另一窗口已更新发送设置；你的未保存修改已保留，请核对后保存。":null),
  loaded,reload,save,start:()=>call("start"),stop:()=>call("stop"),reconcile:()=>call("reconcile")};
}
