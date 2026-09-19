"use client";
import {Card,Pill,Progress} from "../bdhub/ui";
import type {LeadPoolState} from "../../server/lead-pool/bridge";

/** The sending pool: what could go out, in what order, and what is holding each layer back. */
export default function LeadPool({data,loaded}:{data:LeadPoolState|null;loaded?:boolean}){
 if(!data?.available)return <Card title="发送池"><div className="p-5"><p className="text-sm text-gray-500">{loaded?"暂时无法读取发送池。":"读取中…"}</p></div></Card>;
 const c=data.counts;
 return <Card title="发送池">
  <div className="space-y-4 p-5">
  <div className="grid gap-4 md:grid-cols-3">
   <div className="rounded-xl bg-success-50 p-4 dark:bg-success-900/10"><div className="flex items-center justify-between"><p className="text-xs text-gray-500">可发送</p><Pill tone="success">现在可进入批次</Pill></div><p className="mt-2 text-2xl font-semibold tabular-nums">{data.business.sendable.toLocaleString()}</p><p className="mt-1 text-xs leading-5 text-gray-400">每位达人只保留当前 sourceRank 最优的一条</p></div>
   <div className="rounded-xl bg-warning-50 p-4 dark:bg-warning-900/10"><div className="flex items-center justify-between"><p className="text-xs text-gray-500">等待中</p><Pill tone="warning">条件满足后重算</Pill></div><p className="mt-2 text-2xl font-semibold tabular-nums">{data.business.waiting.toLocaleString()}</p><p className="mt-1 text-xs leading-5 text-gray-400">等轮次、冷却或达人问题处理</p></div>
   <div className="rounded-xl bg-gray-50 p-4 dark:bg-gray-800"><div className="flex items-center justify-between"><p className="text-xs text-gray-500">暂不参与</p><Pill tone="neutral">保留事实</Pill></div><p className="mt-2 text-2xl font-semibold tabular-nums">{data.business.inactive.toLocaleString()}</p><p className="mt-1 text-xs leading-5 text-gray-400">商品当前不合格或达人明确排除</p></div>
  </div>
  <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
   <div className="rounded-xl bg-gray-50 p-4 dark:bg-gray-800"><p className="text-xs text-gray-500">线索位置（达人×商品）</p><p className="mt-2 text-2xl font-semibold tabular-nums">{c.positions.toLocaleString()}</p><p className="mt-1 text-xs leading-5 text-gray-400">{c.creators.toLocaleString()} 个达人 · 搜索不到 {c.unresolved.toLocaleString()}</p></div>
   <div className="rounded-xl bg-gray-50 p-4 dark:bg-gray-800"><p className="text-xs text-gray-500">当前池内</p><p className="mt-2 text-2xl font-semibold tabular-nums">{data.business.total.toLocaleString()}</p><p className="mt-1 text-xs leading-5 text-gray-400">不包含已发送历史</p></div>
   <div className="rounded-xl bg-gray-50 p-4 dark:bg-gray-800"><p className="text-xs text-gray-500">已发送历史</p><p className="mt-2 text-2xl font-semibold tabular-nums">{data.history.sent.toLocaleString()}</p></div>
   <div className="rounded-xl bg-brand-50 p-4 dark:bg-brand-500/10"><p className="text-xs text-gray-500">身份覆盖</p><p className="mt-2 text-2xl font-semibold tabular-nums">{c.readyCreators.toLocaleString()}</p><p className="mt-1 text-xs leading-5 text-gray-400">当前可发送达人</p></div>
  </div>
  <Progress done={c.sent} total={c.positions} label="发送进度"/>
  <details className="rounded-xl border border-gray-200 p-4 text-xs text-gray-500 dark:border-gray-700"><summary className="cursor-pointer font-medium text-gray-700 dark:text-gray-300">查看等待和排除原因</summary><div className="mt-3 flex flex-wrap gap-x-5 gap-y-2"><span>同达人其他商品 {data.reasons.queued??0}</span><span>冷却中 {data.reasons.cooling??0}</span><span>达人问题未结 {data.reasons.awaiting_reply??0}</span><span>商品当前不合格 {data.reasons.product_inactive??0}</span><span>明确排除 {data.reasons.excluded??0}</span></div></details>
  <p className="text-xs text-gray-500">冷却：已解锁 {data.cooldown.unlocked/3600} 小时 · 未解锁 {data.cooldown.locked/3600} 小时。达人之间和达人内部都按 Kalodata sourceRank 排序；并列时销量更高优先，再按 PID 固定顺序。</p>
  <p className="text-xs text-gray-500">池里只有<b>已经拿到 OECID</b>的达人：handle 解析不到身份的线索根本不会成为一条位置，所以这里不显示「缺身份」这一层。身份进度在上一张卡。</p>
  </div>
 </Card>;
}
