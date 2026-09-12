"use client";
import {useCallback,useEffect,useRef,useState} from "react";
import {Button,Icon,Pill} from "../bdhub/ui";
import type {ProfileRefreshJob,ProfileRefreshJobs,ProfileRefreshRequest} from "./refresh-contracts";

const API="/api/creator-profile-refresh",PENDING="bdhub-profile-refresh-pending-v1";
const labels={queued:"等待抓取",running:"正在抓取",completed:"刷新完成",blocked:"本次未完成"};
const knownErrors:Record<string,string>={account_not_startable:"采集账号暂不可用",maintenance_due:"账号维护中",shared_backoff:"平台请求正在退避",verification_required:"平台验证尚未完成",remote_error:"平台暂未返回有效结果",whole_probe_deadline:"本次抓取超时",worker_interrupted:"上次抓取中断，原档案已保留",probe_incomplete:"未取得完整的身份回执",identity_mismatch:"本次身份回执不一致"};
function job(value:unknown):value is ProfileRefreshJob{return Boolean(value&&typeof value==="object"&&typeof (value as ProfileRefreshJob).id==="string"&&["queued","running","completed","blocked"].includes((value as ProfileRefreshJob).status));}

export default function ProfileRefreshPanel({creatorId,market,onComplete}:{creatorId:string;market:string;onComplete:()=>void}) {
  const [data,setData]=useState<ProfileRefreshJobs|null>(null),[error,setError]=useState(""),[readError,setReadError]=useState(""),[busy,setBusy]=useState(false),[pending,setPending]=useState<ProfileRefreshRequest|null>(null),[restored,setRestored]=useState(false);
  const submitting=useRef(false),generation=useRef(0),completed=useRef(new Set<string>()),callback=useRef(onComplete);callback.current=onComplete;
  const currentId=useRef(creatorId);currentId.current=creatorId;
  useEffect(()=>{try{const raw=sessionStorage.getItem(PENDING);if(raw){const value=JSON.parse(raw);if(typeof value.creatorId!=="string"||typeof value.requestId!=="string")throw new Error();setPending(value);}}catch{setError("无法恢复本次刷新请求，请检查浏览器会话存储。");}setRestored(true);},[]);
  const load=useCallback(async(signal?:AbortSignal)=>{
    const id=creatorId,sequence=generation.current;
    const response=await fetch(`${API}?creatorId=${encodeURIComponent(id)}`,{cache:"no-store",signal});const value=await response.json();
    if(!response.ok)throw new Error(value?.error?.message||"无法读取抓取状态。");
    if(!Array.isArray(value.jobs)||!value.jobs.every(job)||typeof value.workerOnline!=="boolean")throw new Error("抓取状态响应不完整。");
    if(currentId.current!==id||sequence!==generation.current)return;
    setData(value);setReadError("");
    let changed=false;for(const item of value.jobs as ProfileRefreshJob[])if(item.status==="completed"&&!completed.current.has(item.id)){completed.current.add(item.id);changed=true;}if(changed)callback.current();
  },[creatorId]);
  useEffect(()=>{
    generation.current++;setData(null);setReadError("");const controller=new AbortController();let reading=false;
    const read=async()=>{if(reading||document.visibilityState!=="visible")return;reading=true;try{await load(controller.signal);}catch(e){if(!controller.signal.aborted)setReadError(e instanceof TypeError?"暂时无法连接本机采集服务，正在等待恢复。":e instanceof Error?e.message:"无法读取抓取状态。");}finally{reading=false;}};
    void read();const timer=window.setInterval(()=>void read(),2500);return()=>{controller.abort();window.clearInterval(timer);};
  },[load]);
  const submit=async(request:ProfileRefreshRequest)=>{
    if(submitting.current)return;
    try{sessionStorage.setItem(PENDING,JSON.stringify(request));}catch{setError("未能保存请求编号，本次刷新没有提交。");return;}
    submitting.current=true;setBusy(true);setPending(request);setError("");
    try{
      const response=await fetch(API,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(request)});const body=await response.json();
      if(!response.ok){if(response.status>=400&&response.status<500){sessionStorage.removeItem(PENDING);setPending(null);}throw new Error(body?.error?.message||"刷新请求尚未确认。");}
      if(!job(body))throw new Error("刷新请求尚未确认，请恢复原请求。");
      sessionStorage.removeItem(PENDING);setPending(null);await load();
    }catch(e){setError(e instanceof Error?e.message:"刷新请求尚未确认。");}
    finally{submitting.current=false;setBusy(false);}
  };
  const latest=data?.jobs[0],active=data?.jobs.some(j=>j.status==="queued"||j.status==="running");
  return <div className="space-y-3 rounded-xl border border-gray-200 p-4 dark:border-gray-700">
    <div className="flex flex-wrap items-center justify-between gap-3"><div><p className="text-sm font-medium text-gray-800 dark:text-gray-200">更新平台画像</p><p className="mt-1 text-xs leading-5 text-gray-500">按 OECID 刷新，名字变化后仍保留同一档案。</p></div><Button size="sm" disabled={!restored||!data||busy||Boolean(pending)||active||market!=="it"} onClick={()=>void submit({creatorId,requestId:crypto.randomUUID()})}><Icon name="arrow" className="size-4"/>{busy?"提交中…":active?"刷新进行中":"刷新画像"}</Button></div>
    {market!=="it"&&<p className="text-xs text-gray-500">此市场的页面刷新尚未接通。</p>}
    {latest&&<div className="flex flex-wrap items-center gap-2 text-xs text-gray-500" aria-live="polite"><Pill tone={latest.status==="completed"?"success":latest.status==="blocked"?"warning":"brand"}>{labels[latest.status]}</Pill><span>{latest.status==="completed"?"已保存新观察，名字和来源记录可继续追溯。":latest.status==="blocked"?knownErrors[latest.errorCode||""]||"未取得可用的新观察，原档案已保留。":latest.status==="running"?"正在核对平台身份与画像。":"任务已保存，关闭页面后仍可继续。"}</span></div>}
    {data&&!data.workerOnline&&<p className="text-xs leading-5 text-gray-500">采集服务暂未在线，已提交的任务会保留在本机。</p>}
    {(error||readError)&&<p role="alert" className="text-xs leading-5 text-error-500">{error||readError}</p>}
    {pending&&<div className="flex flex-wrap items-center justify-between gap-2 text-xs text-gray-500"><span>上次请求结果待确认，恢复时沿用原编号。</span><Button size="sm" variant="outline" disabled={busy} onClick={()=>void submit(pending)}>恢复原请求</Button></div>}
    {latest&&<details className="text-xs text-gray-400"><summary className="cursor-pointer">抓取记录</summary><p className="mt-2 break-all leading-5">任务 {latest.id} · 业务请求 {latest.requestCount===null?"待记录":`${latest.requestCount} 次`}</p><p className="leading-5">本次只读取画像，不发送消息。</p></details>}
  </div>;
}
