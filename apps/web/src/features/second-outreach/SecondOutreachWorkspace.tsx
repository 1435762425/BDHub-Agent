"use client";

import Link from "next/link";
import ContinuousSendPanel from "./ContinuousSendPanel";
import StatsCalendarPanel from "./StatsCalendarPanel";
import {useInboxMonitor} from "./useInboxMonitor";
import {useContinuousSend} from "./useContinuousSend";
import {Card,PageHeading} from "../bdhub/ui";

export type WorkspaceSection="send"|"history";
const tabs:{section:WorkspaceSection;label:string}[]=[{section:"send",label:"发送"},{section:"history",label:"结果与历史"}];

function SendWorkspace({market}:{market:string}){const controller=useContinuousSend(market);return <ContinuousSendPanel market={market} controller={controller}/>;}
function HistoryWorkspace({market}:{market:string}){const controller=useInboxMonitor(market);return <StatsCalendarPanel controller={controller}/>;}
function Unavailable({section}:{section:WorkspaceSection}){return <Card title={section==="send"?"发送暂未开放":"结果与历史暂未开放"}><p className="p-5 text-sm leading-6 text-gray-500">当前市场的账号、语言内容或发送能力尚未完成验收。</p></Card>;}

export default function SecondOutreachWorkspace({section,market,runtimeAvailable}:{section:WorkspaceSection;market:string;label:string;runtimeAvailable:boolean}){
 return <div className="space-y-5">
  <PageHeading title="合作工作台"/>
  <nav aria-label="合作工作台" className="flex max-w-full gap-1 overflow-x-auto rounded-xl bg-gray-100 p-1 dark:bg-gray-800">
   {tabs.map(item=><Link key={item.section} href={`/${market}/workspace/${item.section}`} aria-current={section===item.section?"page":undefined} className={`whitespace-nowrap rounded-lg px-4 py-2.5 text-sm font-medium transition ${section===item.section?"bg-white text-gray-800 shadow-theme-xs dark:bg-gray-700 dark:text-white":"text-gray-500 hover:text-gray-800 dark:hover:text-gray-200"}`}>{item.label}</Link>)}
  </nav>
  {!runtimeAvailable?<Unavailable section={section}/>:section==="send"?<SendWorkspace market={market}/>:<HistoryWorkspace market={market}/>}
 </div>;
}
