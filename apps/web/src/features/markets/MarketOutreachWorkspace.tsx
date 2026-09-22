"use client";
import Link from "next/link";
import {useCallback,useEffect,useState} from "react";
import {Card,Notice,PageHeading,Pill} from "../bdhub/ui";
import LeadPool from "../catalog/LeadPool";
import {useLeadPool} from "../catalog/useLeadPool";
import MarketContentPanel from "./MarketContentPanel";

type Section="send"|"history";
type Workflow={setting?:{continuousSendEnabled?:boolean};current?:{runId?:string;state?:string;stages?:Array<{stage:string;state:string;itemCount?:number;platformWrites?:number}>}};
export default function MarketOutreachWorkspace({market,label,section}:{market:string;label:string;section:Section}){
 const leadPool=useLeadPool(market),[workflow,setWorkflow]=useState<Workflow|null>(null);
 const load=useCallback(async()=>{const response=await fetch(`/api/workflow?market=${encodeURIComponent(market)}`,{cache:"no-store"});if(response.ok)setWorkflow(await response.json());},[market]);
 useEffect(()=>{void load();const timer=setInterval(()=>void load(),15000);return()=>clearInterval(timer);},[load]);
 const enabled=workflow?.setting?.continuousSendEnabled===true,current=workflow?.current;
 return <div className="space-y-5">
  <PageHeading title="合作工作台" description={`${label}市场使用自己的发送池、语言模板和真实交付台账。`} action={<div className="flex gap-2"><Pill tone="brand">{label}</Pill><Pill tone={enabled?"success":"warning"}>{enabled?"持续发送已配置":"真实发送关闭"}</Pill></div>}/>
  <nav aria-label="合作工作台" className="flex max-w-full gap-1 overflow-x-auto rounded-xl bg-gray-100 p-1 dark:bg-gray-800">{([{id:"send",label:"发送"},{id:"history",label:"结果与历史"}] as const).map(item=><Link key={item.id} href={`/${market}/workspace/${item.id}`} className={`rounded-lg px-4 py-2.5 text-sm font-medium ${section===item.id?"bg-white shadow dark:bg-gray-700":"text-gray-500"}`}>{item.label}</Link>)}</nav>
  {section==="send"?<><Notice tone="warning">二发模板审核达到 10 条前，后端会拒绝开启和立即发送；保存页面或重启服务不会绕过该门禁。</Notice><MarketContentPanel market={market}/><LeadPool data={leadPool.data} loaded={leadPool.loaded}/><Card title="发送运行状态"><div className="grid gap-3 p-5 sm:grid-cols-3"><div><p className="text-xs text-gray-400">持续发送</p><p className="mt-2 font-semibold">{enabled?"已开启":"关闭"}</p></div><div><p className="text-xs text-gray-400">当前 workflow</p><p className="mt-2 font-semibold">{current?.state??"尚未建立"}</p></div><div><p className="text-xs text-gray-400">真实交付</p><p className="mt-2 font-semibold">卡片与正文逐条回查</p></div><Link href={`/${market}`} className="text-sm font-medium text-brand-500">前往运营首页控制开关 →</Link></div></Card></>:<><LeadPool data={leadPool.data} loaded={leadPool.loaded}/><Card title="结果口径"><div className="space-y-2 p-5 text-sm leading-6 text-gray-500"><p>“已发送历史”只计算已经写入交付台账的达人×商品位置；结果未知不会算成功，也不会自动重发。</p><p>回复与加橱窗结果进入同市场会话台账；跨市场数据不会合并。</p><Link href={`/${market}/conversations`} className="font-medium text-brand-500">查看会话与达人结果 →</Link></div></Card></>}
 </div>;
}
