"use client";
import {Card,MetricTable,Progress} from "../bdhub/ui";
import type {LeadPoolState} from "../../server/lead-pool/bridge";

/** The sending pool: what could go out, in what order, and what is holding each layer back. */
export default function LeadPool({data,loaded}:{data:LeadPoolState|null;loaded?:boolean}){
 if(!data?.available)return <Card title="发送池"><div className="p-5"><p className="text-sm text-gray-500">{loaded?"暂时无法读取发送池。":"读取中…"}</p></div></Card>;
 const c=data.counts;
 return <Card title="发送池">
  <div className="space-y-4 p-5">
  <MetricTable rows={[
   {label:"可发送",value:data.business.sendable.toLocaleString(),detail:"每位达人只保留当前 A/B 排序最优的一条",accent:true},
   {label:"等待中",value:data.business.waiting.toLocaleString(),detail:"等轮次、冷却或达人问题处理"},
   {label:"暂不参与",value:data.business.inactive.toLocaleString(),detail:"商品当前不合格或达人明确排除"},
   {label:"线索位置（达人×商品）",value:c.positions.toLocaleString(),detail:`A 类 ${c.aPositions.toLocaleString()} · B 类 ${c.bPositions.toLocaleString()}`},
   {label:"当前池内",value:data.business.total.toLocaleString(),detail:"不包含已发送历史"},
   {label:"已发送历史",value:data.history.sent.toLocaleString(),detail:"历史结果，不是池状态"},
   {label:"身份覆盖",value:c.readyCreators.toLocaleString(),detail:"当前可发送达人"},
  ]}/>
  <Progress done={data.history.sent} total={data.history.sent+data.business.total} label="累计发送 / 当前剩余机会"/>
  <details className="rounded-xl border border-gray-200 p-4 text-xs text-gray-500 dark:border-gray-700"><summary className="cursor-pointer font-medium text-gray-700 dark:text-gray-300">查看等待和排除原因</summary><div className="mt-3 flex flex-wrap gap-x-5 gap-y-2"><span>同达人其他商品 {data.reasons.queued??0}</span><span>冷却中 {data.reasons.cooling??0}</span><span>达人问题未结 {data.reasons.awaiting_reply??0}</span><span>商品当前不合格 {data.reasons.product_inactive??0}</span><span>技术隔离 {data.reasons.technical_isolated??0}</span><span>明确排除 {data.reasons.excluded??0}</span></div></details>
  <p className="text-xs text-gray-500">A 类销售线索整体优先，按同市场数值 GMV、销量、sourceRank、PID 排序；B 类按代表视频播放量、发布时间、PID 排序。同一达人只占一个发送槽位。</p>
  <p className="text-xs text-gray-500">池里只有<b>已经拿到 OECID</b>的达人：handle 解析不到身份的线索根本不会成为一条位置，所以这里不显示「缺身份」这一层。身份进度在上一张卡。</p>
  </div>
 </Card>;
}
