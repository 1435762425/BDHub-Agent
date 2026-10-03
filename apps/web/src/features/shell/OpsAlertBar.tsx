"use client";

import Link from "next/link";
import {useEffect,useState} from "react";
import type {OpsAlert,OpsAlerts} from "@/server/ops-alerts/bridge";

const time=(value:number)=>new Date(value*1000).toLocaleString("zh-CN",{timeZone:"Asia/Shanghai",hour12:false,month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"});
const DOT={critical:"bg-error-500",warning:"bg-warning-500",info:"bg-gray-400"} as const;
// A paused or stopped scheduler explains most other alerts, so it leads the bar whatever its level.
const HEADLINES=["scheduler-down","production-paused"];
// Three kinds, derived from the alert id (H15): system faults that affect every market, work a person
// must do (the same items the overview lists under "需要我处理"), and normal waits or notices that
// the system is already handling. Only the first two make the bar orange or count as "需处理".
type Kind="system"|"people"|"waiting";
const PEOPLE=/-(human|unread|needs-human|identity-overdue|send-unknown|agent-failed|agent-first-send|agent-pilot-complete|selection-isolated)$|-stage-/;
const WAITING=/-(platform-rejected|send-quarantined|inbox-stopped)$|^production-paused$/;
const kind=(alert:OpsAlert):Kind=>WAITING.test(alert.id)?"waiting":PEOPLE.test(alert.id)?"people":"system";
const GROUPS:{kind:Kind;title:string}[]=[{kind:"system",title:"系统异常"},{kind:"people",title:"待处理事项"},{kind:"waiting",title:"等待与提示"}];

function Row({alert}:{alert:OpsAlert}){
 return <li className="flex items-start gap-3 py-2">
  <span aria-hidden className={`mt-1.5 size-2 shrink-0 rounded-full ${DOT[alert.level]}`}/>
  <div className="min-w-0 flex-1"><p className="text-sm font-medium text-gray-800 dark:text-gray-100">{alert.title}</p><p className="text-xs leading-5 text-gray-500 dark:text-gray-400">{alert.detail}{alert.since?` · 自 ${time(alert.since)}`:""}</p></div>
  {alert.href&&<Link href={alert.href} className="shrink-0 text-xs font-medium text-brand-500 hover:text-brand-600">{kind(alert)==="people"?"处理 →":"查看 →"}</Link>}
 </li>;
}

export default function OpsAlertBar(){
 const [data,setData]=useState<OpsAlerts|null>(null),[failed,setFailed]=useState(false),[open,setOpen]=useState(false);
 useEffect(()=>{
  let stopped=false,timer:ReturnType<typeof setTimeout>|undefined;
  const poll=async()=>{
   if(stopped)return;
   if(document.visibilityState==="visible"){try{const response=await fetch("/api/ops-alerts",{cache:"no-store"});if(!response.ok)throw Error();const value=await response.json() as OpsAlerts;if(!stopped){setData(value);setFailed(false);}}catch{if(!stopped)setFailed(true);}}
   if(!stopped)timer=setTimeout(poll,60_000);
  };
  const visible=()=>{if(document.visibilityState==="visible"){if(timer)clearTimeout(timer);void poll();}};
  document.addEventListener("visibilitychange",visible);void poll();
  return()=>{stopped=true;if(timer)clearTimeout(timer);document.removeEventListener("visibilitychange",visible);};
 },[]);
 // A failed read is shown whatever came before: "no alerts" from an old sample is not "no faults now".
 if(failed&&(!data||!data.alerts.length))return <div role="region" aria-label="运行告警" className="border-b border-warning-200 bg-warning-50 px-4 py-2 text-xs text-warning-700 dark:border-warning-900 dark:bg-warning-900/10 dark:text-warning-300 sm:px-6">异常巡检暂时读不到，当前运行状态无法确认{data?`（上次成功检查于 ${time(data.checkedAt)}，当时无告警）`:""}；页面其它数据不受影响。</div>;
 if(!data||!data.alerts.length)return null;
 const headline=HEADLINES.map(id=>data.alerts.find(alert=>alert.id===id)).find(Boolean);
 const rest=data.alerts.filter(alert=>alert!==headline);
 const count=(level:OpsAlert["level"])=>data.alerts.filter(alert=>alert.level===level&&kind(alert)!=="waiting").length;
 const actionable=data.alerts.filter(alert=>kind(alert)!=="waiting");
 const kinds=(k:Kind)=>rest.filter(alert=>kind(alert)===k);
 const tone=count("critical")?"border-error-200 bg-error-50 dark:border-error-900 dark:bg-error-900/10":count("warning")?"border-warning-200 bg-warning-50 dark:border-warning-900 dark:bg-warning-900/10":"border-gray-200 bg-gray-50 dark:border-gray-800 dark:bg-gray-900";
 const summary=[["系统异常",actionable.filter(alert=>kind(alert)==="system").length],["待处理",actionable.filter(alert=>kind(alert)==="people").length]].filter(([,value])=>value).map(([label,value])=>`${label} ${value}`).join(" · ");
 return <div role="region" aria-label="运行告警" className={`border-b px-4 py-2.5 sm:px-6 ${tone}`}>
  <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
   <span aria-hidden className={`size-2 shrink-0 rounded-full ${DOT[count("critical")?"critical":count("warning")?"warning":headline?.level??"info"]}`}/>
   <strong className="font-semibold text-gray-800 dark:text-gray-100">{headline?headline.title:actionable.length?`${actionable.length} 项待关注`:"运行提示"}</strong>
   {headline?.since&&<span className="text-xs text-gray-500">自 {time(headline.since)}</span>}
   {headline?rest.some(alert=>kind(alert)!=="waiting")&&<span className="text-xs font-medium text-gray-700 dark:text-gray-200">另 {rest.filter(alert=>kind(alert)!=="waiting").length} 项待关注</span>:<span className="hidden text-xs text-gray-600 dark:text-gray-300 sm:inline">{summary}</span>}
   {failed&&<span className="text-xs font-medium text-warning-700 dark:text-warning-300">巡检读取失败，以下为 {time(data.checkedAt)} 的旧结果</span>}
   <button type="button" aria-expanded={open} onClick={()=>setOpen(value=>!value)} className="ml-auto min-h-8 rounded px-2 text-xs font-medium text-brand-600 hover:bg-black/5 focus-visible:outline-2 focus-visible:outline-brand-500 dark:text-brand-300">{open?"收起":"查看详情"}</button>
  </div>
  {open&&<div className="mt-2 border-t border-black/5 pt-1 dark:border-white/10">
   {headline&&<p className="py-2 text-xs leading-5 text-gray-600 dark:text-gray-300">{headline.detail}</p>}
   {GROUPS.map(group=>{const items=kinds(group.kind);return items.length?<div key={group.kind} className="pt-2"><p className="text-[11px] font-medium text-gray-500">{group.title}</p><ul className="divide-y divide-black/5 dark:divide-white/10">{items.map(alert=><Row key={alert.id} alert={alert}/>)}</ul></div>:null;})}
   <p className="pt-2 text-xs text-gray-500">更新于 {time(data.checkedAt)}</p>
  </div>}
 </div>;
}
