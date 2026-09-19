"use client";
import {Card,Pill,Progress} from "../bdhub/ui";
import type {LeadPoolState} from "../../server/lead-pool/bridge";

const LAYERS=[
 {key:"ready",label:"可发",tone:"success" as const,hint:"冷却已过、未被阻断"},
 {key:"queued",label:"池中等待",tone:"neutral" as const,hint:"同一达人的其余位置，轮到再上"},
 {key:"cooling",label:"冷却中",tone:"brand" as const,hint:"距上次发送未满冷却"},
 {key:"awaiting_reply",label:"等达人回复",tone:"warning" as const,hint:"有未解决案件或人工接管"},
 {key:"excluded",label:"已排除",tone:"neutral" as const,hint:"明确拒联"},
 {key:"sent",label:"已发送",tone:"success" as const,hint:"已成功发出"},
];

/** The sending pool: what could go out, in what order, and what is holding each layer back. */
export default function LeadPool({data,loaded}:{data:LeadPoolState|null;loaded?:boolean}){
 if(!data?.available)return <Card title="发送池"><div className="p-5"><p className="text-sm text-gray-500">{loaded?"暂时无法读取发送池。":"读取中…"}</p></div></Card>;
 const c=data.counts;
 return <Card title="发送池">
  <div className="space-y-4 p-5">
  <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
   <div className="rounded-xl bg-gray-50 p-4 dark:bg-gray-800"><p className="text-xs text-gray-500">线索位置（达人×商品）</p><p className="mt-2 text-2xl font-semibold tabular-nums">{c.positions.toLocaleString()}</p><p className="mt-1 text-xs leading-5 text-gray-400">{c.creators.toLocaleString()} 个达人 · 搜索不到 {c.unresolved.toLocaleString()}</p></div>
   <div className="rounded-xl bg-gray-50 p-4 dark:bg-gray-800"><p className="text-xs text-gray-500">已发送（累计）</p><p className="mt-2 text-2xl font-semibold tabular-nums">{c.sent.toLocaleString()}</p></div>
   <div className="rounded-xl bg-gray-50 p-4 dark:bg-gray-800"><p className="text-xs text-gray-500">未发送</p><p className="mt-2 text-2xl font-semibold tabular-nums">{c.unsent.toLocaleString()}</p></div>
   <div className="rounded-xl bg-brand-50 p-4 dark:bg-brand-500/10"><p className="text-xs text-gray-500">本次可发</p><p className="mt-2 text-2xl font-semibold tabular-nums">{c.ready.toLocaleString()}</p><p className="mt-1 text-xs leading-5 text-gray-400">{c.readyCreators.toLocaleString()} 个达人各一个位置</p></div>
  </div>
  <Progress done={c.sent} total={c.positions} label="发送进度"/>
  <div className="grid grid-cols-2 gap-3 lg:grid-cols-6">{LAYERS.map(layer=><div key={layer.key} className="rounded-xl border border-gray-200 p-3 dark:border-gray-700"><div className="flex items-center justify-between"><p className="text-xs text-gray-500">{layer.label}</p><Pill tone={layer.tone}>{(data.layers[layer.key]??0).toLocaleString()}</Pill></div><p className="mt-1 text-xs leading-5 text-gray-400">{layer.hint}</p></div>)}</div>
  <p className="text-xs text-gray-500">冷却：已解锁 {data.cooldown.unlocked/3600} 小时 · 未解锁 {data.cooldown.locked/3600} 小时。排序：先按能不能发分层，可发的按 Kalodata GMV 排名，冷却的按解冻时间，同强度最久没联系的优先。「等达人回复」和「冷却中」分开——前者要人处理，后者只要等时间。</p>
  <p className="text-xs text-gray-500">池里只有<b>已经拿到 OECID</b>的达人：handle 解析不到身份的线索根本不会成为一条位置，所以这里不显示「缺身份」这一层。身份进度在上一张卡。</p>
  </div>
 </Card>;
}
