"use client";

import Link from "next/link";
import SendBatchPanel from "./SendBatchPanel";
import StatsCalendarPanel from "./StatsCalendarPanel";
import {useInboxMonitor} from "./useInboxMonitor";
import {useSendBatch} from "./useSendBatch";
import {Card,PageHeading,Pill} from "../bdhub/ui";

export type WorkspaceSection="send"|"history";
const tabs:{section:WorkspaceSection;label:string}[]=[{section:"send",label:"发送"},{section:"history",label:"结果与历史"}];

function SendWorkspace({market}:{market:string}){const controller=useSendBatch(market);return <SendBatchPanel market={market} controller={controller}/>;}
function HistoryWorkspace({market}:{market:string}){const controller=useInboxMonitor(market);return <StatsCalendarPanel controller={controller}/>;}
function Unavailable({section}:{section:WorkspaceSection}){return <div className="grid gap-5 lg:grid-cols-2"><Card title={section==="send"?"发送":"结果与历史"}><p className="p-5 text-sm leading-6 text-gray-500">页面结构已统一；当前市场账号、语言内容或写能力尚未验收，真实动作保持关闭。</p></Card><Card title="市场隔离"><p className="p-5 text-sm leading-6 text-gray-500">此处不会读取其他市场的模板、发送池、统计或 delivery。</p></Card></div>;}

export default function SecondOutreachWorkspace({section,market,label,runtimeAvailable}:{section:WorkspaceSection;market:string;label:string;runtimeAvailable:boolean}){
 return <div className="space-y-5">
  <PageHeading title="合作工作台" description="持续消费当前发送池，并用台账核对真实结果。" action={<div className="flex flex-wrap items-center gap-2"><Pill tone="brand">{label} · {market.toUpperCase()}</Pill><Pill tone="warning">真实发送需明确开启</Pill><Pill tone="neutral">自动回复独立控制</Pill></div>}/>
  <nav aria-label="合作工作台" className="flex max-w-full gap-1 overflow-x-auto rounded-xl bg-gray-100 p-1 dark:bg-gray-800">
   {tabs.map(item=><Link key={item.section} href={`/${market}/workspace/${item.section}`} aria-current={section===item.section?"page":undefined} className={`whitespace-nowrap rounded-lg px-4 py-2.5 text-sm font-medium transition ${section===item.section?"bg-white text-gray-800 shadow-theme-xs dark:bg-gray-700 dark:text-white":"text-gray-500 hover:text-gray-800 dark:hover:text-gray-200"}`}>{item.label}</Link>)}
  </nav>
  {!runtimeAvailable?<Unavailable section={section}/>:section==="send"?<SendWorkspace market={market}/>:<HistoryWorkspace market={market}/>}
 </div>;
}
