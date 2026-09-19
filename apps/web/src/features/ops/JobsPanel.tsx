"use client";
import {useEffect,useState} from "react";
import {Button,Card,Input,Notice,Pill,Toggle} from "../bdhub/ui";
import type {JobsState,WorkbenchJob} from "../../server/jobs/bridge";

const stamp=(value:number|null)=>value?new Date(value*1000).toLocaleString("zh-CN",{timeZone:"Asia/Shanghai",hour12:false}):"尚未运行";

/** Manual entry points that are actually wired to a verified endpoint. */
async function trigger(job:WorkbenchJob):Promise<void>{
 if(job.manual==="global-source-sync"){
  const r=await fetch("/api/global-source",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action:"sync",requestId:crypto.randomUUID()})});
  if(!r.ok)throw Error();
  return;
 }
 if(job.manual==="catalog-screen-run"){
  const r=await fetch("/api/catalog-screen",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action:"run"})});
  if(!r.ok)throw Error();
  return;
 }
 throw Error("unwired");
}

export default function JobsPanel(){
 const [data,setData]=useState<JobsState|null>(null);
 const [busy,setBusy]=useState<string|null>(null);
 const [message,setMessage]=useState<string|null>(null);
 useEffect(()=>{const controller=new AbortController();void(async()=>{try{const r=await fetch("/api/jobs",{signal:controller.signal,cache:"no-store"});if(!r.ok)throw Error();const v:JobsState=await r.json();if(!controller.signal.aborted)setData(v);}catch{if(!controller.signal.aborted)setData(null);}})();return()=>controller.abort();},[]);
 async function saveJob(id:string,patch:{enabled?:boolean;at?:string|null}){
  setBusy(id);setMessage(null);
  try{
   const r=await fetch("/api/jobs",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action:"save",jobs:{[id]:patch}})});
   if(!r.ok)throw Error();
   setData(await r.json());
   setMessage("已记录定时意向。定时器尚未启用，所以现在还不会自动运行——开关打开后需要由 G2 的调度接管才会真正生效。");
  }catch{setMessage("暂时无法保存定时意向。");}
  finally{setBusy(null);}
 }
 async function runJob(job:WorkbenchJob){
  setBusy(job.id);setMessage(null);
  try{await trigger(job);setMessage(job.manual==="catalog-screen-run"?`已按当前门槛重新筛分。`:`已提交「${job.name}」。采集逐页保存，可回到上方卡片看进度，也可以再点一次继续。`);}
  catch{setMessage(job.manual==="unwired"?`「${job.name}」还没有接上手动入口，这一项先记在面板里。`:`暂时无法提交「${job.name}」。`);}
  finally{setBusy(null);}
 }
 return <Card title="作业与定时" subtitle="每个作业都能单独手动触发；定时是可选开关，默认关闭。定时器尚未启用，开关只记录你的意向。"><div className="space-y-4 p-5">
  {!data&&<p className="text-sm text-gray-500">暂时无法读取作业列表。</p>}
  {data&&<>
  <div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead className="border-b border-gray-200 text-xs text-gray-500 dark:border-gray-700"><tr>{["作业","上次运行 · 北京时间","手动触发","定时"].map(label=><th key={label} className="whitespace-nowrap px-3 py-3 font-medium">{label}</th>)}</tr></thead><tbody>{data.jobs.map(job=><tr key={job.id} className="border-b border-gray-100 align-top dark:border-gray-800">
   <td className="min-w-56 max-w-md px-3 py-4"><p className="font-medium">{job.name}</p><p className="mt-1 text-xs leading-5 text-gray-500">{job.description}</p><Pill tone="neutral">{job.group}</Pill></td>
   <td className="whitespace-nowrap px-3 py-4 text-xs text-gray-500">{stamp(job.lastRunAt)}</td>
   <td className="whitespace-nowrap px-3 py-4">{job.manual==="unwired"?<Pill tone="neutral">尚未接入</Pill>:<Button size="sm" variant="outline" disabled={busy===job.id} onClick={()=>void runJob(job)}>{busy===job.id?"提交中…":"立即运行"}</Button>}</td>
   <td className="min-w-52 px-3 py-4"><Toggle checked={job.enabled} disabled={busy===job.id||!data.schedulerReady} label={job.enabled?"已开启（意向）":"未启用"} onChange={value=>void saveJob(job.id,{enabled:value})}/><div className="flex items-center gap-2 text-xs text-gray-500"><span>每天</span><Input type="time" className="h-9 w-28" value={job.at??""} disabled={busy===job.id||!data.schedulerReady} onChange={e=>void saveJob(job.id,{at:e.target.value||null})}/><span>北京时间</span></div></td>
   </tr>)}</tbody></table></div>
  {!data.schedulerReady&&<Notice tone="warning">定时器尚未启用（属于下一步的调度工作）。下面每个开关都只记录你的意向，不会自动运行任何作业；现在请用“立即运行”手动触发。</Notice>}
  {message&&<Notice tone="info">{message}</Notice>}
  </>}
 </div></Card>;
}
