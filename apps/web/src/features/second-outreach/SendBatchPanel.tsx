"use client";

import {Button,Card,Field,Input,Notice,Pill,StatTile,Toggle} from "../bdhub/ui";
import {PROBE_COUNT,SEND_COUNTS} from "./send-contracts";
import type {SendController} from "./useSendBatch";

const number=(value:number|undefined|null)=>value==null?"—":value.toLocaleString("zh-CN");
const batchLabels:Record<string,string>={prepared:"已冻结，等待明确启动",start_failed:"启动失败，可重试",starting:"正在启动",running:"发送中",stop_requested:"正在安全停止",stopped:"已停止",waiting_reconciliation:"结果未知，等待核验",completed:"已完成",completed_with_exceptions:"已完成，有逐项例外",local_capacity_reached:"本地 24 小时额度已到顶",platform_rejected:"平台账号级拒绝",material_refresh_record_failed:"材料状态落账失败"};
const blockerGroups:Record<string,string>={
 standard_link_missing:"标准 TapLink",standard_link_terms_changed:"标准 TapLink",standard_link_unreadable:"标准 TapLink",card_not_read:"标准 TapLink",card_rate_changed:"标准 TapLink",card_campaign_changed:"标准 TapLink",card_unverified:"标准 TapLink",no_card_needs_link:"标准 TapLink",no_link_in_ledger:"标准 TapLink",missing_card:"标准 TapLink",
 current_identity_missing:"OECID 身份",relationship_blocked:"达人级冻结",marketing_cooldown:"冷却",offer_not_in_current_catalog:"商品当前失效",delivery_already_exists:"已发送历史",duplicate_creator:"同达人其他 PID",missing_short_name:"材料异常",
};

function groupBlockers(skipped:Record<string,number>){
 const grouped:Record<string,number>={};
 for(const [reason,count] of Object.entries(skipped)){
  if(["beyond_requested_size","local_capacity_reached","outside_send_window"].includes(reason))continue;
  const label=blockerGroups[reason]??"其它复检异常";grouped[label]=(grouped[label]??0)+count;
 }
 return grouped;
}

export default function SendBatchPanel({controller}:{controller:SendController}){
 const {data,draft,setDraft,busy,message,loaded,save,freeze,start,stop,reconcile}=controller;
 if(!data?.available)return <Card title="发送池与发送"><div className="p-5"><p className="text-sm text-gray-500">{!loaded?"正在读取意大利发送池…":"暂时无法读取发送池；不会显示成业务 0。"}</p></div></Card>;
 const {preview,pool,batch}=data,layers=pool.layers,counts=pool.counts;
 const count=draft?.count??data.config.count,reserve=Math.ceil(Math.max(0,count)*0.1);
 const dirty=Boolean(draft&&(draft.count!==data.config.count||draft.widen!==data.config.widen||draft.windowEnabled!==data.config.windowEnabled||draft.window[0]!==data.config.window[0]||draft.window[1]!==data.config.window[1]));
 const active=Boolean(batch&&["prepared","start_failed","starting","running","stop_requested","waiting_reconciliation","local_capacity_reached","platform_rejected","material_refresh_record_failed"].includes(batch.state));
 const canStart=Boolean(batch&&["prepared","start_failed"].includes(batch.state));
 const canStop=Boolean(batch&&["starting","running","local_capacity_reached","platform_rejected","material_refresh_record_failed"].includes(batch.state));
 const blockers=groupBlockers(preview.skipped);
 const waiting=(layers.queued??0)+(layers.cooling??0)+(layers.awaiting_reply??0);
 const inactive=(layers.excluded??0)+(layers.product_inactive??0);
 return <div className="space-y-5">
  {batch?.state==="waiting_reconciliation"&&<Notice tone="warning"><strong>本批有结果未知的发送意图。</strong> 新发送和候补提升均已暂停；只允许使用原账号、原 requestRef 和原冻结材料核验，不得换号或重发。</Notice>}

  <Card title="意大利发送资格" subtitle="A 类销售线索整体优先；B 类只在没有同对 A 证据时按代表视频进入。">
   <div className="space-y-4 p-5">
    <div className="grid grid-cols-2 gap-4 lg:grid-cols-6">
     <StatTile label="A 类销售位置" value={counts.aPositions??0} hint="近 14 天正销量与数值 GMV" brand/>
     <StatTile label="B 类内容位置" value={counts.bPositions??0} hint="近 30 天最高单条视频"/>
     <StatTile label="可发送达人" value={layers.ready??preview.readyAvailable} hint="OECID、材料与关系均就绪"/>
     <StatTile label="等待中" value={waiting} hint="等轮次、冷却或人工处理"/>
     <StatTile label="暂不参与" value={inactive} hint="商品失效或明确拒联"/>
     <StatTile label="已发送历史" value={pool.counts.sent??0} hint="历史结果，不是池状态"/>
    </div>
    <p className="text-xs leading-5 text-gray-500">当前可发送槽位均已取得稳定 OECID；本次预览还会复检当前 Offer、标准 `currentListId`、达人级控制、冷却和去重。</p>
   </div>
  </Card>

  <Card title="本批设置与操作" subtitle="先保存设置并取得新预览，再冻结；冻结不会调用平台，只有“确认并开始”会启动真实 worker。" action={preview.window.enabled?<Pill tone={preview.window.open?"success":"warning"}>窗口{preview.window.open?"已打开":"等待开放"}</Pill>:<Pill tone="neutral">未设置窗口</Pill>}>
   <div className="space-y-5 p-5">
    <div className="grid gap-4 lg:grid-cols-[1fr_1fr_1.2fr]">
     <Field label="正式目标人数" hint={`允许 1–2000；候补自动为 ${number(reserve)} 人。`}><div className="space-y-2"><Input type="number" min={1} max={2000} value={count} disabled={busy||!draft||active} onChange={event=>draft&&setDraft({...draft,count:Number(event.target.value)})}/><div className="flex flex-wrap gap-2">{SEND_COUNTS.map(value=><Button key={value} size="sm" variant={count===value?"primary":"outline"} disabled={busy||!draft||active} onClick={()=>draft&&setDraft({...draft,count:value})}>{value}</Button>)}</div></div></Field>
     <Field label="发送窗口（北京时间）" hint="未启用时可先备料和冻结；启动后按窗口等待。"><div className="space-y-2"><div className="flex items-center gap-2"><Input type="time" value={draft?.window[0]??data.config.window[0]} disabled={busy||!draft||!draft.windowEnabled||active} onChange={event=>draft&&setDraft({...draft,window:[event.target.value,draft.window[1]]})}/><span className="text-sm text-gray-400">至</span><Input type="text" maxLength={5} value={draft?.window[1]??data.config.window[1]} disabled={busy||!draft||!draft.windowEnabled||active} onChange={event=>draft&&setDraft({...draft,window:[draft.window[0],event.target.value]})}/></div><Toggle label="启用发送窗口" checked={Boolean(draft?.windowEnabled)} disabled={busy||!draft||active} onChange={value=>draft&&setDraft({...draft,windowEnabled:value})}/></div></Field>
     <div className="rounded-xl border border-gray-200 p-4 dark:border-gray-800"><p className="text-sm font-medium text-gray-700 dark:text-gray-200">滚动 24 小时新联系额度</p><p className="mt-2 text-2xl font-semibold">{number(preview.capacity?.remaining)} <span className="text-sm font-normal text-gray-400">可用 / {number(preview.capacity?.limit)}</span></p><p className="mt-2 text-xs leading-5 text-gray-500">本地 500 是保护闸门，不等于平台公布额度。越界探测只在高级设置中显式开启。</p><details className="mt-3"><summary className="cursor-pointer text-xs font-medium text-brand-500">高级：平台额度探测</summary><div className="mt-2"><Toggle label={`允许越过本地闸门（快捷探测 ${PROBE_COUNT}）`} description="真实回执逐条落账；单达人限制不停止整批，账号级拒绝才停止。" checked={Boolean(draft?.widen)} disabled={busy||!draft||active} onChange={value=>draft&&setDraft({...draft,widen:value,count:value&&count<PROBE_COUNT?PROBE_COUNT:count})}/></div></details></div>
    </div>
    {!active&&<div className="flex flex-wrap items-center gap-3"><Button variant="outline" disabled={busy||!draft||!dirty} onClick={()=>void save()}>{busy?"保存中…":dirty?"保存设置并重新预览":"设置已保存"}</Button><span className="text-xs text-gray-400">预览 {preview.previewHash?`${preview.previewHash.slice(0,12)}…`:"尚不可用"}</span></div>}
    {dirty&&<Notice tone="warning">上面的候选仍对应已保存的目标 {number(preview.requested)}；保存后才会生成新的预览指纹。</Notice>}
    {message&&<Notice>{message}</Notice>}
   </div>
  </Card>

  <Card title="本次预览" subtitle="页面只展示 1–3 条正式成员样例；完整正式成员和候补只在服务端冻结。" action={<div className="flex flex-wrap gap-2"><Pill tone={preview.fullPreparation?"success":"warning"}>正式 {number(preview.sendable)}/{number(preview.requested)}</Pill><Pill tone={preview.reserveReady===preview.reserveRequested?"success":"warning"}>候补 {number(preview.reserveReady)}/{number(preview.reserveRequested)}</Pill></div>}>
   <div className="space-y-4 p-5">
    <div className="grid gap-3 md:grid-cols-3">{preview.samples.map(row=><article key={`${row.oecId}-${row.pid}`} className="rounded-xl border border-gray-200 p-4 dark:border-gray-800"><div className="flex flex-wrap items-center justify-between gap-2"><strong className="text-sm text-gray-800 dark:text-white">@{row.handle}</strong><Pill tone={row.sourceClass==="A"?"brand":"neutral"}>{row.sourceClass} 类</Pill></div><p className="mt-2 text-xs leading-5 text-gray-500">{row.sourceClass==="A"?`GMV ${row.gmv??"未解析"} EUR · 销量 ${number(row.units)} · sourceRank ${number(row.sourceRank)}`:`代表视频 ${number(row.videoViews)} 播放 · ${row.videoReleasedAt??"发布时间未记录"}`}</p><p className="mt-2 break-all text-[11px] text-gray-400">OECID …{row.oecId.slice(-8)} · PID {row.pid}<br/>currentListId …{row.currentListId.slice(-8)}</p><p lang="it" className="mt-3 rounded-lg bg-gray-50 p-3 text-sm leading-6 text-gray-700 dark:bg-gray-800 dark:text-gray-200">{row.messageIt||"材料渲染异常；此成员不能冻结。"}</p>{row.messageZh&&<p className="mt-2 text-xs leading-5 text-gray-500">中文辅助：{row.messageZh}</p>}</article>)}</div>
    {!preview.samples.length&&<p className="py-5 text-center text-sm text-gray-500">当前没有可展示的正式成员样例。</p>}
    {Object.keys(blockers).length>0&&<details className="rounded-xl border border-gray-200 p-4 dark:border-gray-800"><summary className="cursor-pointer text-sm font-medium text-gray-700 dark:text-gray-200">查看未进入本批的原因</summary><div className="mt-3 flex flex-wrap gap-3">{Object.entries(blockers).map(([label,value])=><span key={label} className="rounded-lg bg-gray-50 px-3 py-2 text-xs text-gray-600 dark:bg-gray-800 dark:text-gray-300">{label} <strong>{number(value)}</strong></span>)}</div></details>}
    {!active&&<div className="flex flex-wrap items-center gap-3 border-t border-gray-100 pt-4 dark:border-gray-800"><Button disabled={busy||dirty||!preview.fullPreparation||!preview.previewHash} onClick={()=>void freeze()}>{busy?"处理中…":dirty?"先保存设置":preview.fullPreparation?`冻结正式 ${number(preview.sendable)}＋候补 ${number(preview.reserveReady)}`:`尚缺 ${number(preview.required-preview.frozenTotal)} 位，不能冻结`}</Button><p className="text-xs leading-5 text-gray-500">冻结保存 OECID、PID、Offer、currentListId、话术、顺序和 candidate hash，不调用平台。</p></div>}
   </div>
  </Card>

  {batch&&<Card title="冻结与执行结果" subtitle={`批次 ${batch.batchId} · revision ${batch.revision}`} action={<Pill tone={batch.state==="running"?"success":batch.state==="waiting_reconciliation"?"warning":"neutral"}>{batchLabels[batch.state]??batch.state}</Pill>}>
   <div className="space-y-4 p-5"><div className="grid grid-cols-2 gap-4 lg:grid-cols-4"><StatTile label="正式目标" value={batch.target} hint="目标不会因候补改变"/><StatTile label="已进入执行" value={batch.attempted} hint="含已提升候补" brand/><StatTile label="候补已提升" value={batch.reservePromoted} hint={`剩余 ${number(batch.reserveRemaining)}`}/><StatTile label="逐项状态" value={Object.values(batch.counts).reduce((a,b)=>a+b,0)} hint={Object.entries(batch.counts).map(([key,value])=>`${key} ${value}`).join(" · ")||"尚未执行"}/></div><div className="rounded-xl bg-gray-50 p-4 text-xs leading-5 text-gray-600 dark:bg-gray-800 dark:text-gray-300"><p>发送账号 {data.account.toUpperCase()} · 窗口 {batch.authorization.sendWindow?.join("–")??"未设置"} · 预览 {batch.previewHash.slice(0,16)}…</p><p className="mt-1">unknown 不释放名额、不提升候补，只核验原发送意图。</p>{batch.runtime&&<p className="mt-1">执行断点 {batch.runtime.phase} · worker {batch.runtime.pid??"—"} · 最近心跳 {new Date(batch.runtime.seenAt*1000).toLocaleString("zh-CN")}</p>}</div>{batch.unknownDeliveries.map(item=><div key={item.deliveryId} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-warning-200 bg-warning-50 p-4 dark:border-warning-900 dark:bg-warning-900/10"><div><p className="text-sm font-medium text-warning-700 dark:text-warning-400">结果待核验 · PID {item.pid}</p><p className="mt-1 break-all text-xs text-gray-500">OECID …{item.oecId.slice(-8)} · {Object.entries(item.parts).map(([kind,state])=>`${kind} ${state}`).join(" · ")}</p></div><Button variant="outline" disabled={busy} onClick={()=>void reconcile(item.deliveryId)}>{busy?"核验中…":"核验原发送意图"}</Button></div>)}<div className="flex flex-wrap gap-2">{canStart&&<Button disabled={busy} onClick={()=>void start()}>{busy?"启动中…":"确认并开始真实发送"}</Button>}{canStop&&<Button variant="outline" disabled={busy} onClick={()=>void stop()}>{busy?"处理中…":"安全停止本批"}</Button>}</div></div>
  </Card>}
 </div>;
}
