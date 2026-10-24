"use client";
import {useState} from "react";
import {Button,Card,Dialog,EmptyState,Notice,PageHeading,Pill,StatTile,Toggle} from "../bdhub/ui";
import {useFirstOutreach} from "./useFirstOutreach";
import {firstApplicationGroups,firstApplicationSummary,firstOutreachActions,firstOutreachCycleLabel,firstOutreachLabel,firstOutreachWaitingReasons} from "./presentation";
import type {FirstOutreachState} from "./contracts";

const time=(value:number|null|undefined)=>value==null?"尚未读取":new Date(value*1000).toLocaleString("zh-CN",{timeZone:"Asia/Shanghai",hour12:false});
const disclosure="rounded-2xl border border-gray-200 bg-white dark:border-gray-800 dark:bg-white/[0.025]";
const summary="cursor-pointer rounded-2xl px-5 py-4 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500";

function Applications({rows}:{rows:FirstOutreachState["applications"]}){
 return <ul className="divide-y divide-gray-100 dark:divide-gray-800">{rows.map(row=>{
  const state=firstOutreachLabel(row.state),reason=row.reason?firstOutreachLabel(row.reason):null;
  return <li key={row.id} className="space-y-2 py-3 text-sm">
   <div className="flex flex-wrap items-start justify-between gap-2"><p className="font-medium">{row.handle==="身份待核对"?row.handle:`@${row.handle.replace(/^@/,"")}`}</p><Pill>{state}</Pill></div>
   <p className="line-clamp-2 break-words text-gray-600 dark:text-gray-300" title={row.productName}>{row.productName}</p>
   {reason&&reason!==state&&<p className="text-xs leading-5 text-gray-500">{reason}</p>}
   <details className="text-xs text-gray-500"><summary className="cursor-pointer">商品与申请详情</summary><p className="mt-2 break-words">{row.productName}</p><p className="mt-1 break-all">PID：{row.pid} · 申请：{row.id}</p></details>
  </li>;
 })}</ul>;
}

export default function FirstOutreachWorkspace(){
 const {data,loaded,error,message,busy,reload,call}=useFirstOutreach();
 const [confirmation,setConfirmation]=useState<"start"|"continue"|"approvals"|null>(null);
 const unavailable=!data||!!error,processing=!!data&&["preparing","syncing"].includes(data.runtime.state),actions=new Set(data?firstOutreachActions(data):[]);
 const applicationSummary=data?firstApplicationSummary(data):{value:"—",hint:"待审批范围尚未核实"};
 const groups=firstApplicationGroups(data?.applications??[]),waitingReasons=data?firstOutreachWaitingReasons(data):[];
 const attention=!!data&&(data.counts.unknown>0||["attention","failed","waiting_reconciliation"].includes(data.runtime.state));
 const confirm=async()=>{if(!confirmation)return;const ok=await call(confirmation==="approvals"?"set-approvals":confirmation,confirmation==="approvals"?true:undefined);if(ok)setConfirmation(null);};
 return <div className="space-y-5">
  <PageHeading title="一发 · 免费样品推荐" description="巴西 · 自动更新商品与匹配、持续推荐、同步样品申请与履约状态。" action={<Button variant="outline" disabled={!!busy} onClick={()=>void reload()}>刷新状态</Button>}/>
  {error&&<div role="alert"><Notice tone="warning">{error}{data&&<p className="mt-1 text-xs">以下是上次成功读取的状态，当前不可用于开启推送或审批。</p>}</Notice></div>}
  {message&&<div role="status" aria-live="polite"><Notice>{message}</Notice></div>}
  {!data?<Card title="运行状态"><EmptyState title={loaded?"一发状态暂不可用":"正在读取一发状态…"} description={loaded?"尚未取得真实商品、达人和申请数据，不能显示可推荐数量。":"读取本机真实状态后展示商品、匹配与审批进度。"}/></Card>:<>
   <Card title="推送进度" action={<div className="flex flex-wrap items-center gap-3"><Pill tone={attention?"warning":data.runtime.state==="sending"?"success":"neutral"}>{firstOutreachLabel(data.runtime.state)}</Pill>{actions.has("stop")&&<Button variant="outline" size="sm" disabled={busy==="stop"} onClick={()=>void call("stop")}>停止持续推送</Button>}</div>}>
    <div className="space-y-4 p-5">
     {waitingReasons.length>0&&(attention?<Notice tone="warning"><p className="font-medium">当前等待</p>{waitingReasons.map(reason=><p key={reason}>{reason}</p>)}</Notice>:<div className="space-y-1 text-sm leading-6 text-gray-500">{waitingReasons.map(reason=><p key={reason}>{reason}</p>)}</div>)}
     <div className="grid grid-cols-2 gap-3 lg:grid-cols-4"><StatTile label="成功推送" value={data.counts.sent} hint="已确认成功的推荐" brand/><StatTile label="自动审批通过" value={data.counts.approved} hint="已确认平台通过"/><StatTile label="可推荐商品" value={data.counts.products} hint="有效免费样品且有额度"/><StatTile label="匹配达人" value={data.counts.matchedCreators} hint="主营类目一致 · GMV排序"/></div>
     {Array.from(actions).some(action=>action!=="stop")&&<div className="flex flex-wrap gap-3">
      {actions.has("prepare")&&<Button variant="outline" disabled={!!busy||unavailable||processing} onClick={()=>void call("prepare")}>{busy==="prepare"?"准备中…":"准备匹配"}</Button>}
      {actions.has("continue")?<Button disabled={!!busy||unavailable||processing||!data.readiness.ready} onClick={()=>setConfirmation("continue")}>继续持续推送</Button>:actions.has("start")&&<Button disabled={!!busy||unavailable||processing||!data.readiness.ready} onClick={()=>setConfirmation("start")}>验证首条</Button>}
      {data.counts.unknown>0&&<Button variant="outline" disabled={!!busy||unavailable} onClick={()=>void call("reconcile")}>核验原操作</Button>}
     </div>}
    </div>
   </Card>
   <Card title="待处理申请" action={<Pill tone={data.counts.needsReview>0||data.counts.unknown>0?"warning":"neutral"}>{applicationSummary.value}{typeof applicationSummary.value==="number"?" 条":""}</Pill>}>
    <div className="space-y-3 p-5">
     <p className="text-xs text-gray-500">{applicationSummary.hint}</p>
     {data.applicationCoverage!=="complete"&&<Notice tone="warning">待审批范围尚未核实，已读取的历史记录不能代表全部待处理申请。</Notice>}
     {data.counts.unknown>0&&<Notice tone="warning">本系统有 {data.counts.unknown.toLocaleString()} 条原操作结果未知，等待核验。</Notice>}
     {groups.attention.length>0?<details open className="rounded-xl border border-gray-200 dark:border-gray-700"><summary className="cursor-pointer px-4 py-3 text-sm font-medium">需核对的已读申请 · {groups.attention.length} 条</summary><div className="max-h-[420px] overflow-y-auto px-4"><Applications rows={groups.attention}/></div></details>:<p className="text-sm text-gray-500">{data.applicationCoverage==="complete"?"已读申请中暂无需核对记录。":"等待完整同步后确认。"}</p>}
     {groups.waiting.length>0&&<details className="rounded-xl border border-gray-200 dark:border-gray-700"><summary className="cursor-pointer px-4 py-3 text-sm font-medium">等待商家与达人 · {groups.waiting.length} 条</summary><div className="max-h-[420px] overflow-y-auto px-4"><Applications rows={groups.waiting}/></div></details>}
     {groups.finished.length>0&&<details className="rounded-xl border border-gray-200 dark:border-gray-700"><summary className="cursor-pointer px-4 py-3 text-sm font-medium">已处理申请 · {groups.finished.length} 条</summary><div className="max-h-[420px] overflow-y-auto px-4"><Applications rows={groups.finished}/></div></details>}
    </div>
   </Card>
   <details className={disclosure}><summary className={summary}>自动运行与审批设置<span className="ml-3 text-xs font-normal text-gray-500">自动审批{data.control.approvalsEnabled?"已开启":"已关闭"}</span>{data.automation&&Object.entries(data.automation).filter(([,cycle])=>cycle.state==="attention").map(([key,cycle])=><span key={key} className="mt-1 block text-xs font-normal text-warning-600">{key==="supply"?"商品与匹配":"样品审批"}：{cycle.reason?firstOutreachLabel(cycle.reason):firstOutreachLabel(cycle.state)}</span>)}</summary><div className="space-y-4 border-t border-gray-100 p-5 dark:border-gray-800">
    {data.automation&&<div className="grid gap-3 sm:grid-cols-2">{([['supply','商品与匹配更新'],['samples','样品审批与履约']] as const).map(([key,title])=>{const cycle=data.automation![key];return <div key={key} className="rounded-xl border border-gray-100 p-4 dark:border-gray-800"><div className="flex items-center justify-between gap-2"><p className="text-sm font-medium">{title}</p><Pill tone={cycle.state==='attention'?'warning':'neutral'}>{firstOutreachCycleLabel(cycle)}</Pill></div>{cycle.reason&&<p className="mt-2 text-sm leading-6 text-gray-500">{firstOutreachLabel(cycle.reason)}</p>}{cycle.materials&&<p className="mt-2 text-xs leading-5 text-gray-500">推荐方案就绪 {cycle.materials.readyOffers.toLocaleString()} 个 · 待补材料 {cycle.materials.remaining.toLocaleString()} 项</p>}<p className="mt-2 text-xs leading-5 text-gray-400">{cycle.nextRunAt!==null?`下次自动检查：${time(cycle.nextRunAt)}`:`最近更新：${time(cycle.updatedAt)}`}</p></div>;})}</div>}
    <Toggle label={`自动审批${data.control.approvalsEnabled?"已开启":"已关闭"}`} description="仅审批本功能成功推荐且仍符合规则的申请；其他申请和涉及机构付款的申请保留待处理。" checked={data.control.approvalsEnabled} disabled={!!busy||unavailable} onChange={enabled=>enabled?setConfirmation("approvals"):void call("set-approvals",false)}/>
    <Button size="sm" variant="outline" disabled={!!busy||unavailable||processing} onClick={()=>void call("sync")}>{busy==="sync"?"同步中…":"同步商品与申请"}</Button>
    <p className="text-xs leading-5 text-gray-500">开启持续推送后，商品、匹配与推荐材料自动更新；失败后自动重试，中断后从原记录恢复。停止立即阻止新推送；已提交结果继续核验。</p>
    <p className="text-xs leading-5 text-gray-500">状态更新：{time(data.runtime.updatedAt)}{data.sourceUpdatedAt!==undefined&&` · 数据更新：${time(data.sourceUpdatedAt)}`} · 北京时间</p>
   </div></details>
   <details className="rounded-2xl border border-gray-200 bg-white dark:border-gray-800 dark:bg-white/[0.025]"><summary className="cursor-pointer px-5 py-4 text-sm font-medium">候选明细与葡语邀约预览{data.candidates.length>0&&` · 展示 ${data.candidates.length} 位`}</summary><div className="space-y-3 p-5 pt-0">{data.candidates.length===0?<p className="text-sm text-gray-500">暂无可展示的匹配候选。可先准备匹配，并查看当前等待原因。</p>:data.candidates.map(row=><details key={`${row.creatorId}:${row.pid}`} className="rounded-xl border border-gray-200 px-4 py-3 dark:border-gray-700"><summary className="cursor-pointer text-sm"><span className="font-medium">@{row.handle.replace(/^@/,"")}</span><span className="ml-3 text-gray-500">{row.productName}</span></summary><div className="mt-3 space-y-2 text-xs leading-6 text-gray-500"><p>主营类目：{row.categoryName} · GMV：{row.gmv===null?"尚未读取":`R$ ${row.gmv}`} · PID：{row.pid}</p><p className="rounded-lg bg-gray-50 p-3 text-sm text-gray-700 dark:bg-gray-800 dark:text-gray-200" lang="pt-BR">{row.message}</p></div></details>)}</div></details>
   <details className="text-xs leading-6 text-gray-500"><summary className="cursor-pointer">本期匹配与审批规则</summary><p className="mt-2">使用现有巴西达人库，仅匹配主营带货类目；带货表现和GMV用于排序，不设固定GMV或粉丝数门槛。商品必须明确支持免费样品、活动有效且仍有样品额度。每位达人选择最合适的一品，与二发共用72小时冷却。申请审批重新核对推荐成功记录、活动、当前资格、样品额度和重复申请。</p></details>
  </>}
  <Dialog open={confirmation!==null} onClose={()=>{if(!busy)setConfirmation(null);}} title={confirmation==="approvals"?"开启自动审批":confirmation==="continue"?"继续持续推送":"开始首条验证"}>
   <p className="text-sm leading-6 text-gray-600 dark:text-gray-300">{confirmation==="approvals"?"开启后，自动同步申请，仅对本功能已成功推荐且仍符合规则的申请审批，并回查批准与履约进度；其余保留待处理。":confirmation==="continue"?"首条结果已确认。继续后，将自动更新商品、匹配和推荐材料，按当前规则持续推送；失败后自动重试，停止后不会自动重新开启。":"将向一位符合当前规则的达人发送商品推荐，下方为首位候选的葡语邀约预览。发送前重新核对资格，确认首条结果后暂停，等待你继续。"}</p>
   {confirmation==="start"&&data?.candidates[0]&&<div className="mt-4 rounded-xl bg-gray-50 p-4 text-sm dark:bg-gray-800"><p className="font-medium">@{data.candidates[0].handle.replace(/^@/,"")} · {data.candidates[0].productName}</p><p className="mt-3 whitespace-pre-wrap leading-6" lang="pt-BR">{data.candidates[0].message}</p></div>}
   <div className="mt-6 flex justify-end gap-3"><Button variant="outline" disabled={!!busy} onClick={()=>setConfirmation(null)}>取消</Button><Button disabled={!!busy||unavailable} onClick={()=>void confirm()}>{busy?"正在提交…":confirmation==="approvals"?"确认开启审批":confirmation==="continue"?"确认持续推送":"确认发送首条"}</Button></div>
  </Dialog>
 </div>;
}
