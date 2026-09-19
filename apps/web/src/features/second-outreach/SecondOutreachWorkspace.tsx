"use client";
import {useSearchParams} from "next/navigation";
import {useState} from "react";
import BatchPreparationPanel from "./BatchPreparationPanel";
import BatchTasksPanel from "./BatchTasksPanel";
import CycleSupplyPanel from "./CycleSupplyPanel";
import InboxMonitorPanel from "./InboxMonitorPanel";
import SendBatchPanel from "./SendBatchPanel";
import SecondOutreachHistory from "./SecondOutreachHistory";
import {useInboxMonitor} from "./useInboxMonitor";
import {useSendBatch} from "./useSendBatch";
import {PageHeading,Card,EmptyState,Tabs} from "../bdhub/ui";

// 一页五件事，各占一个页签。**不堆在一页**：每块只回答一个问题，翻页签就是换问题。
// 「一发推品 · V2」不在这里——它已经挪到「匹配研究」（/opportunities）。
type Tab = "send" | "inbox" | "reply" | "calendar" | "system";

/** 历史页是另一张页面，单独一个组件：钩子不能挂在可能提前 return 的分支后面。 */
export default function SecondOutreachWorkspace(){
 const params = useSearchParams();
 if(params.get("history") === "1") return <SecondOutreachHistory/>;
 return <Workbench/>;
}

function Workbench(){
 const [tab, setTab] = useState<Tab>("send");
 // 每块的读取只有它自己一个主人，卡片之外不重复轮询。
 const inbox = useInboxMonitor();
 const send = useSendBatch();
 return <div className="space-y-5">
  <PageHeading title="合作工作台" description="从发送池发出去，盯着回复，再统计每天做了什么。"/>
  <Tabs value={tab} onChange={setTab} items={[
   {value:"send", label:"发送池与发送"},
   {value:"inbox", label:"监控与回复"},
   {value:"reply", label:"回复预演与训练"},
   {value:"calendar", label:"统计日历"},
   {value:"system", label:"系统状态与准备"},
  ]}/>

  {tab === "send" && <SendBatchPanel controller={send}/>}

  {tab === "inbox" && <InboxMonitorPanel controller={inbox}/>}

  {tab === "reply" && <Card title="回复预演与训练" subtitle="用真实回复出题：Agent 起草 → 你判 OK/不 OK → 沉淀成规则。预演阶段一条消息都不发。">
   <div className="p-5"><EmptyState title="正在接入：预演队列"
    description="现在库里已经有真实回复可以当题目。接上来之后一次给你几条，你判完就沉淀成这个 Agent 的规则和案例。"/></div>
  </Card>}

  {tab === "calendar" && <Card title="统计日历" subtitle="按北京时间分天：发了多少、触达多少达人、收到多少回复、多少加了橱窗。">
   <div className="p-5"><EmptyState title="正在接入：统计日历"
    description="按天统计的口径已经落地（触达只算回查确认，回复与橱窗排除历史补录），接下来把它画成日历。"/></div>
  </Card>}

  {tab === "system" && <div className="space-y-5">
   <CycleSupplyPanel/>
   <details className="rounded-xl border border-gray-200 p-4 dark:border-gray-700">
    <summary className="cursor-pointer text-sm font-medium">创建前检查准备容量</summary>
    <div className="mt-4"><BatchPreparationPanel/></div>
   </details>
   <BatchTasksPanel/>
   <p className="text-xs leading-5 text-gray-400">
    上一批冻结实测与早期试点记录<strong>不在工作台里</strong>（入口已删）；那批账本一条没删，要追溯时按编号查 var/second-live/。
   </p>
  </div>}
 </div>;
}
