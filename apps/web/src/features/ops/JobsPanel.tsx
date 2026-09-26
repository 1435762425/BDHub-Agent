"use client";
import Link from "next/link";
import {useCallback,useEffect,useState} from "react";
import {Button,Card,Input,Notice,Pill,Toggle} from "../bdhub/ui";
import type {JobsState,WorkbenchJob} from "../../server/jobs/bridge";
import {saveJobTime} from "./job-time-control";

const stamp=(value:number|null)=>value?new Date(value*1000).toLocaleString("zh-CN",{timeZone:"Asia/Shanghai",hour12:false}):"尚未成功";
const weekdays=["周一","周二","周三","周四","周五","周六","周日"];
const controlLabel=(job:WorkbenchJob,market:string)=>job.id==="taplink_clean"?(market==="it"?"总开关 · 扫描并删除失效卡":"总开关 · 只读核查绑定（不删除）"):job.id==="full_catalog_update"?"总开关 + 全托商品发现":job.id==="continuous_send"?"首页持续自动发送":job.id==="agent_reply"?"独立 Agent 开关":"自动运营总开关";

export default function JobsPanel({market,fullManagedCatalog}:{market:string;fullManagedCatalog:boolean|null}){
 const [data,setData]=useState<JobsState|null>(null),[times,setTimes]=useState<Record<string,string>>({}),[weekdaysDraft,setWeekdaysDraft]=useState<Record<string,number>>({}),[busy,setBusy]=useState<string|null>(null),[message,setMessage]=useState("");
 const applyState=(value:JobsState)=>{setData(value);setTimes(Object.fromEntries(value.jobs.map(job=>[job.id,job.at??""])));setWeekdaysDraft(Object.fromEntries(value.jobs.map(job=>[job.id,job.weekday??0])));};
 const load=useCallback(async()=>{const response=await fetch(`/api/jobs?market=${encodeURIComponent(market)}`,{cache:"no-store"});if(!response.ok)throw Error();applyState(await response.json());},[market]);
 useEffect(()=>{void load().catch(()=>{});},[load]);
 const saveTime=async(job:WorkbenchJob)=>{
  const at=times[job.id];if(!at)return;
  setBusy(job.id);setMessage("");
  try{
   const result=await saveJobTime(market,job,at,weekdaysDraft[job.id]??0);
   if(result.state)applyState(result.state);
   const shared=!["agent_reply","continuous_send"].includes(job.id);
   setMessage(`${job.name} 时间已保存为 ${job.cadence==="weekly"?`${weekdays[weekdaysDraft[job.id]??0]} `:""}${at}（北京时间）${shared?"，这是共享供给时间，对所有市场生效":"，仅当前市场"}。${result.state?"":"状态暂未刷新，请重新读取页面。"}`);
  }catch{setMessage(`${job.name} 时间未保存；请检查时间与 Agent/发送窗口是否重叠。`);}
  finally{setBusy(null);}
 };
 const setAgent=async(enabled:boolean)=>{if(!data)return;setBusy("agent_reply");setMessage("");try{const library=await fetch(`/api/template-library?market=${encodeURIComponent(market)}`,{cache:"no-store"}).then(response=>response.json());const {revision,...setting}=library.agentSetting;delete setting.updatedAt;setting.enabled=enabled;const synced=await fetch(`/api/template-library?market=${encodeURIComponent(market)}`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action:"save_agent",market,expectedRevision:revision,setting})});if(!synced.ok)throw Error();await load();setMessage(`Agent 回复已${enabled?"开启":"关闭"}。`);}catch{setMessage("Agent 回复开关未保存。");}finally{setBusy(null);}};
 const visibleJobs=data?.jobs;
 return <Card title="作业与定时" subtitle="标准主链跟随运营首页总开关；这里负责查看状态和调整北京时间。"><div className="space-y-4 p-5">{!data?<p className="text-sm text-gray-500">暂时无法读取作业台账。</p>:<><div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-gray-200 p-4 dark:border-gray-700"><div><div className="flex items-center gap-2"><p className="text-sm font-medium">自动运营调度器</p><Pill tone={data.scheduler.running?"success":"neutral"}>{data.scheduler.running?"运行中":"未运行"}</Pill></div><p className="mt-1 text-xs leading-5 text-gray-500">由运营首页“自动运营总开关”启动和管理，不需要在这里再启动一次。</p></div><Link href={`/${market}`} className="text-sm font-medium text-brand-500">前往运营首页 →</Link></div><div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr className="border-b border-gray-200 text-xs text-gray-500 dark:border-gray-700">{["作业","启用来源","上次成功","北京时间"].map(label=><th key={label} className="px-3 py-3 font-medium">{label}</th>)}</tr></thead><tbody>{visibleJobs?.map(job=>{// TapLink maintenance runs in every market (read-only binding check outside IT); only discovery needs full-managed.
 const fullUnavailable=job.id==="full_catalog_update"&&fullManagedCatalog!==true;const changed=times[job.id]!==job.at||(job.cadence==="weekly"&&(weekdaysDraft[job.id]??0)!==(job.weekday??0));return <tr key={job.id} className="border-b border-gray-100 align-top dark:border-gray-800"><td className="min-w-64 px-3 py-4"><p className="font-medium">{job.name}</p><p className="mt-1 text-xs leading-5 text-gray-500">{job.description}</p><details className="mt-2 text-[11px] text-gray-400"><summary>技术详情</summary><p>{job.id} · {job.manualEndpoint}</p></details></td><td className="min-w-48 px-3 py-4">{fullUnavailable?<Pill tone="neutral">{fullManagedCatalog===false?"不适用":"尚未验收"}</Pill>:job.id==="agent_reply"?<Toggle checked={job.enabled} disabled={busy!==null} label={job.enabled?"Agent 已开启":"Agent 已关闭"} onChange={value=>void setAgent(value)}/>:<Pill tone="neutral">{controlLabel(job,market)}</Pill>}</td><td className="whitespace-nowrap px-3 py-4 text-xs text-gray-500">{stamp(job.lastRunAt)}</td><td className="min-w-72 px-3 py-4">{job.cadence==="interval"?<p className="text-sm text-gray-500">上次发现完成后 {job.intervalDays} 天到期；未完成续原断点。</p>:<div className="flex items-center gap-2">{job.cadence==="weekly"&&<select value={weekdaysDraft[job.id]??0} disabled={busy!==null||fullUnavailable} onChange={event=>setWeekdaysDraft(current=>({...current,[job.id]:Number(event.target.value)}))} className="h-10 rounded-lg border border-gray-300 bg-transparent px-2 dark:border-gray-700">{weekdays.map((label,index)=><option key={label} value={index}>{label}</option>)}</select>}<Input className="h-10 w-28 shrink-0" type="time" value={times[job.id]??""} disabled={busy!==null||fullUnavailable} onChange={event=>setTimes(current=>({...current,[job.id]:event.target.value}))}/><Button size="sm" className="shrink-0 whitespace-nowrap" variant={changed?"primary":"outline"} disabled={busy!==null||fullUnavailable||!changed} onClick={()=>void saveTime(job)}>{busy===job.id?"保存中…":"保存时间"}</Button></div>}</td></tr>;})}</tbody></table></div>{message&&<Notice tone={message.includes("已")?"success":"warning"}>{message}</Notice>}</>}</div></Card>;
}
