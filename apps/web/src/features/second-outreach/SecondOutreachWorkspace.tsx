"use client";

import Link from "next/link";
import SendBatchPanel from "./SendBatchPanel";
import StatsCalendarPanel from "./StatsCalendarPanel";
import {useInboxMonitor} from "./useInboxMonitor";
import {useSendBatch} from "./useSendBatch";
import {PageHeading,Pill} from "../bdhub/ui";

export type WorkspaceSection="send"|"history";
const tabs:{section:WorkspaceSection;label:string}[]=[
 {section:"send",label:"发送"},
 {section:"history",label:"结果与历史"},
];

function SendWorkspace(){const controller=useSendBatch();return <SendBatchPanel controller={controller}/>;}
function HistoryWorkspace(){const controller=useInboxMonitor();return <StatsCalendarPanel controller={controller}/>;}

export default function SecondOutreachWorkspace({section}:{section:WorkspaceSection}){
 return <div className="space-y-5">
  <PageHeading title="合作工作台" description="按冻结批次触达，处理真实回复，并用台账核对结果。" action={<div className="flex flex-wrap items-center gap-2"><Pill tone="brand">意大利 · IT</Pill><Pill tone="warning">真实发送需明确启动</Pill><Pill tone="neutral">自动回复关闭</Pill></div>}/>
  <nav aria-label="合作工作台" className="flex max-w-full gap-1 overflow-x-auto rounded-xl bg-gray-100 p-1 dark:bg-gray-800">
   {tabs.map(item=><Link key={item.section} href={`/it/workspace/${item.section}`} aria-current={section===item.section?"page":undefined} className={`whitespace-nowrap rounded-lg px-4 py-2.5 text-sm font-medium transition ${section===item.section?"bg-white text-gray-800 shadow-theme-xs dark:bg-gray-700 dark:text-white":"text-gray-500 hover:text-gray-800 dark:hover:text-gray-200"}`}>{item.label}</Link>)}
  </nav>
  {section==="send"?<SendWorkspace/>:<HistoryWorkspace/>}
 </div>;
}
