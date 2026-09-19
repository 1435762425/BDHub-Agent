"use client";
import {useEffect,useState} from "react";
import {Button,Card,Input,Notice,Pill,Toggle} from "../bdhub/ui";
import type {JobsState,WorkbenchJob} from "../../server/jobs/bridge";

const stamp=(value:number|null)=>value?new Date(value*1000).toLocaleString("zh-CN",{timeZone:"Asia/Shanghai",hour12:false}):"尚未运行";
const weekdays=["周一","周二","周三","周四","周五","周六","周日"];

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
 useEffect(()=>{if(!data?.scheduler.running)return;const timer=setInterval(()=>void fetch("/api/jobs",{cache:"no-store"}).then(r=>r.ok?r.json():Promise.reject()).then(value=>setData(value)).catch(()=>{}),5000);return()=>clearInterval(timer);},[data?.scheduler.running]);
 async function saveJob(id:string,patch:{enabled?:boolean;at?:string|null;weekday?:number}){
  setBusy(id);setMessage(null);
  try{
   const r=await fetch("/api/jobs",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action:"save",jobs:{[id]:patch}})});
   if(!r.ok)throw Error();
   setData(await r.json());
   setMessage(data?.scheduler.running?"维护周期已保存；调度器会按新的北京时间执行。":"维护周期已保存，但调度器当前未运行；启动调度器后才会到点执行。");
  }catch{setMessage("暂时无法保存定时意向。");}
  finally{setBusy(null);}
 }
 async function scheduler(action:"start_scheduler"|"stop_scheduler"){
  setBusy("scheduler");setMessage(null);try{const r=await fetch("/api/jobs",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action})});const value=await r.json();if(!r.ok)throw Error();setData(value);setMessage(action==="start_scheduler"?"调度器已启动。只有已开启的来源周期会运行；链接核验固定 creates=0，不建链、不删卡。":"已请求停止，调度器会在当前检查点退出；已启动的底层只读作业不会被强杀。");}catch{setMessage("暂时无法改变调度器状态。");}finally{setBusy(null);}
 }
 async function runJob(job:WorkbenchJob){
  setBusy(job.id);setMessage(null);
  try{await trigger(job);setMessage(job.manual==="catalog-screen-run"?`已按当前门槛重新筛分。`:`已提交「${job.name}」。采集逐页保存，可回到上方卡片看进度，也可以再点一次继续。`);}
  catch{setMessage(job.manual==="unwired"?`「${job.name}」还没有接上手动入口，这一项先记在面板里。`:`暂时无法提交「${job.name}」。`);}
  finally{setBusy(null);}
 }
 return <Card title="作业与定时" subtitle="手动作业与来源维护分开；Campaign 可每日检查，全托 TapLink 可每周检查，默认全部关闭。"><div className="space-y-4 p-5">
  {!data&&<p className="text-sm text-gray-500">暂时无法读取作业列表。</p>}
  {data&&<>
  <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-gray-200 p-4 dark:border-gray-700"><div><div className="flex items-center gap-2"><p className="text-sm font-medium">材料维护调度器</p><Pill tone={data.scheduler.running?"success":"neutral"}>{data.scheduler.running?(data.scheduler.stopping?"正在停止":"运行中"):"未运行"}</Pill></div><p className="mt-1 text-xs leading-5 text-gray-500">只协调两个来源周期；Campaign＝来源刷新后链接只读核验，全托＝每周链接只读核验。失败保留上次成功绑定。</p>{data.scheduler.error&&<p className="mt-1 text-xs text-warning-600">最近异常：{data.scheduler.error}</p>}</div>{data.scheduler.running?<Button size="sm" variant="outline" disabled={busy==="scheduler"||data.scheduler.stopping} onClick={()=>void scheduler("stop_scheduler")}>{data.scheduler.stopping?"停止中…":"停止调度器"}</Button>:<Button size="sm" disabled={busy==="scheduler"} onClick={()=>void scheduler("start_scheduler")}>启动调度器</Button>}</div>
  <div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead className="border-b border-gray-200 text-xs text-gray-500 dark:border-gray-700"><tr>{["作业","上次运行 · 北京时间","手动触发","定时"].map(label=><th key={label} className="whitespace-nowrap px-3 py-3 font-medium">{label}</th>)}</tr></thead><tbody>{data.jobs.map(job=><tr key={job.id} className="border-b border-gray-100 align-top dark:border-gray-800">
   <td className="min-w-56 max-w-md px-3 py-4"><p className="font-medium">{job.name}</p><p className="mt-1 text-xs leading-5 text-gray-500">{job.description}</p><Pill tone="neutral">{job.group}</Pill></td>
   <td className="whitespace-nowrap px-3 py-4 text-xs text-gray-500">{stamp(job.lastRunAt)}</td>
   <td className="whitespace-nowrap px-3 py-4">{job.manual==="unwired"?<Pill tone="neutral">尚未接入</Pill>:<Button size="sm" variant="outline" disabled={busy===job.id} onClick={()=>void runJob(job)}>{busy===job.id?"提交中…":"立即运行"}</Button>}</td>
   <td className="min-w-52 px-3 py-4">{job.schedulable?<><Toggle checked={job.enabled} disabled={busy===job.id||!data.schedulerReady} label={job.enabled?"周期已开启":"未启用"} onChange={value=>void saveJob(job.id,{enabled:value})}/><div className="flex items-center gap-2 text-xs text-gray-500">{job.cadence==="weekly"&&<select aria-label={`${job.name}星期`} value={job.weekday??0} disabled={busy===job.id} onChange={e=>void saveJob(job.id,{weekday:Number(e.target.value)})} className="h-9 rounded-lg border border-gray-300 bg-transparent px-2 dark:border-gray-700">{weekdays.map((label,index)=><option key={label} value={index}>{label}</option>)}</select>}<span>{job.cadence==="daily"?"每天":""}</span><Input type="time" className="h-9 w-28" value={job.at??""} disabled={busy===job.id||!data.schedulerReady} onChange={e=>void saveJob(job.id,{at:e.target.value||null})}/><span>北京时间</span></div>{job.enabled&&data.scheduler.nextDue[job.id==="campaign_material_refresh"?"campaign":"selected"]&&<p className="mt-1 text-[11px] text-gray-400">下次：{stamp(data.scheduler.nextDue[job.id==="campaign_material_refresh"?"campaign":"selected"])}</p>}</>:<Pill tone="neutral">未接周期调度</Pill>}</td>
   </tr>)}</tbody></table></div>
  {!data.scheduler.running&&<Notice tone="warning">调度器当前未运行；即使保存了周期，也不会自动执行。启动调度器不会立刻开放任何未勾选的作业，所有周期默认关闭。</Notice>}
  {message&&<Notice tone="info">{message}</Notice>}
  </>}
 </div></Card>;
}
