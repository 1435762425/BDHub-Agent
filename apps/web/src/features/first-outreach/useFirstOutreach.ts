"use client";
import {useCallback,useEffect,useRef,useState} from "react";
import type {FirstOutreachAction,FirstOutreachRequest,FirstOutreachState} from "./contracts";
import {firstOutreachError} from "./presentation";

const endpoint="/api/first-outreach?market=br";
export function useFirstOutreach(){
 const [data,setData]=useState<FirstOutreachState|null>(null),[loaded,setLoaded]=useState(false),[error,setError]=useState<string|null>(null),[message,setMessage]=useState<string|null>(null),[busy,setBusy]=useState<FirstOutreachAction|null>(null);
 const current=useRef<FirstOutreachState|null>(null),pending=useRef(new Map<string,FirstOutreachRequest>()),active=useRef(new Set<FirstOutreachAction>()),alive=useRef(true);
 const receive=useCallback((value:FirstOutreachState)=>{if(!alive.current)return;const prior=current.current;if(prior&&value.control.revision<prior.control.revision)return;current.current=value;setData(value);setError(null);setLoaded(true);},[]);
 const reload=useCallback(async()=>{
  try{const response=await fetch(endpoint,{cache:"no-store"});if(!response.ok)throw Error();receive(await response.json() as FirstOutreachState);}
  catch{if(alive.current){setLoaded(true);setError("状态读取失败，当前数据可能已变化。请刷新后再启动或修改审批设置。");}}
 },[receive]);
 useEffect(()=>{
  alive.current=true;let timer:ReturnType<typeof setTimeout>|undefined,stopped=false,inFlight=false;
  const poll=async()=>{if(stopped||inFlight)return;if(document.visibilityState!=="visible"){timer=setTimeout(poll,15000);return;}inFlight=true;try{await reload();}finally{inFlight=false;if(!stopped)timer=setTimeout(poll,5000);}};
  const visible=()=>{if(document.visibilityState==="visible"&&!inFlight){if(timer)clearTimeout(timer);void poll();}};
  document.addEventListener("visibilitychange",visible);void poll();
  return()=>{stopped=true;alive.current=false;if(timer)clearTimeout(timer);document.removeEventListener("visibilitychange",visible);};
 },[reload]);
 const call=useCallback(async(action:FirstOutreachAction,approvalsEnabled?:boolean)=>{
  if(!current.current||active.current.has(action)||action!=="stop"&&active.current.size>0)return false;
  active.current.add(action);setBusy(action);setMessage(null);
  const key=JSON.stringify({action,approvalsEnabled});
  let request=pending.current.get(key)??{action,market:"br" as const,requestId:`first-${crypto.randomUUID()}`,expectedRevision:current.current.control.revision,...(approvalsEnabled===undefined?{}:{approvalsEnabled})};
  pending.current.set(key,request);
  try{
   const send=(body:FirstOutreachRequest)=>fetch(endpoint,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
   let response=await send(request),value=await response.json();
   // A concurrent prepare/start may finish just before stop. Its known revision conflict
   // has no side effect, so apply the same stop intent once against the refreshed state.
   if(action==="stop"&&response.status===409&&typeof value.error==="string"&&value.error.includes("conflict")){
    await reload();request={...request,requestId:`first-${crypto.randomUUID()}`,expectedRevision:current.current!.control.revision};pending.current.set(key,request);response=await send(request);value=await response.json();
   }
   if(!response.ok){if(response.status===409){pending.current.delete(key);await reload();}throw Error(typeof value.error==="string"?value.error:"first_outreach_unavailable");}
   receive(value as FirstOutreachState);pending.current.delete(key);
   if(alive.current)setMessage(action==="start"?"已启动首条验证；确认首条结果后会停止，等待你继续。":action==="continue"?"已请求持续推送，实际进度会自动刷新。":action==="stop"?"已停止新的推送；自动审批由独立开关控制，在途结果按原操作核验。":action==="set-approvals"?(approvalsEnabled?"已开启自动审批，仅处理本功能成功推荐且仍合格的申请。":"已关闭自动审批。"):action==="prepare"?"已请求准备匹配，进度会自动刷新。":action==="sync"?"已请求同步商品与样品申请，进度会自动刷新。":"已请求核验原操作结果，进度会自动刷新。");
   return true;
  }catch(error){if(alive.current)setMessage(firstOutreachError(error instanceof Error?error.message:"first_outreach_unavailable"));return false;}
  finally{active.current.delete(action);if(alive.current)setBusy([...active.current].at(-1)??null);}
 },[receive,reload]);
 return {data,loaded,error,message,busy,reload,call};
}
