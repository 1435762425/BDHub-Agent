"use client";
import {useSearchParams} from "next/navigation";
import Link from "next/link";
import BatchPreparationPanel from "./BatchPreparationPanel";
import BatchTasksPanel from "./BatchTasksPanel";
import CycleSupplyPanel from "./CycleSupplyPanel";
import SecondOutreachHistory from "./SecondOutreachHistory";
import {PageHeading,Card,EmptyState} from "../bdhub/ui";
export default function SecondOutreachWorkspace(){
 const params=useSearchParams();if(params.get("history")==="1")return <SecondOutreachHistory/>;
 const first=params.get("channel")==="first";
 return <div className="space-y-5"><PageHeading title="合作工作台" description={first?"一发主动推品，独立于二发批次。":"指定数量，全量准备，按时分批发送。"}/>
 <nav aria-label="一发与二发" className="flex gap-1 rounded-xl bg-gray-100 p-1 dark:bg-gray-800">{[{label:"二发批次",channel:"second"},{label:"一发推品 · V2",channel:"first"}].map(item=><Link key={item.channel} href={`/workspace?mode=second-live${item.channel==="first"?"&channel=first":""}`} aria-current={(first?"first":"second")===item.channel?"page":undefined} className={`rounded-lg px-5 py-2.5 text-sm font-medium ${(first?"first":"second")===item.channel?"bg-white text-brand-600 shadow-sm dark:bg-gray-900":"text-gray-500"}`}>{item.label}</Link>)}</nav>
 {first?<Card><EmptyState title="一发工作区将在 V2 接入" description="保留主动选品和个性化推品的独立流程，当前优先完成二发批次闭环。"/><div className="px-5 pb-5"><Link href="/opportunities?mode=matching&dataset=italy-profiles" className="text-sm text-brand-500">查看已有的一发匹配研究 →</Link></div></Card>:<><BatchTasksPanel/><details className="rounded-xl border border-gray-200 p-4 dark:border-gray-700"><summary className="cursor-pointer text-sm font-medium">创建前检查准备容量</summary><div className="mt-4"><BatchPreparationPanel/></div></details><details className="rounded-xl border border-gray-200 p-4 dark:border-gray-700" open><summary className="cursor-pointer text-sm font-medium">当前实测批次与速度</summary><div className="mt-4"><CycleSupplyPanel/></div></details><Link className="text-xs text-gray-400" href="/workspace?mode=second-live&history=1">查看早期试点历史</Link></>}
 </div>;
}
