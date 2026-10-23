"use client";
import {useState} from "react";
import {Button,Card,Dialog,EmptyState,Notice,PageHeading,Pill,StatTile,Toggle} from "../bdhub/ui";
import {useFirstOutreach} from "./useFirstOutreach";
import {firstOutreachCanContinue,firstOutreachLabel} from "./presentation";

const time=(value:number|null|undefined)=>value==null?"尚未读取":new Date(value*1000).toLocaleString("zh-CN",{timeZone:"Asia/Shanghai",hour12:false});
export default function FirstOutreachWorkspace(){
 const {data,loaded,error,message,busy,reload,call}=useFirstOutreach();
 const [confirmation,setConfirmation]=useState<"start"|"continue"|"approvals"|null>(null);
 const unavailable=!data||!!error,processing=!!data&&["preparing","syncing"].includes(data.runtime.state),active=data?.control.mode!=="stopped",canContinue=data?firstOutreachCanContinue(data):false;
 const confirm=async()=>{if(!confirmation)return;const ok=await call(confirmation==="approvals"?"set-approvals":confirmation,confirmation==="approvals"?true:undefined);if(ok)setConfirmation(null);};
 return <div className="space-y-5">
  <PageHeading title="一发 · 免费样品推荐" description="巴西 · 按主营带货类目匹配，每位达人每次推荐一个商品。" action={<Button variant="outline" disabled={!!busy} onClick={()=>void reload()}>刷新状态</Button>}/>
  {error&&<div role="alert"><Notice tone="warning">{error}{data&&<p className="mt-1 text-xs">以下是上次成功读取的状态，当前不可用于开启推送或审批。</p>}</Notice></div>}
  {message&&<div role="status" aria-live="polite"><Notice>{message}</Notice></div>}
  {!data?<Card title="运行状态"><EmptyState title={loaded?"一发状态暂不可用":"正在读取一发状态…"} description={loaded?"尚未取得真实商品、达人和申请数据，不能显示可推荐数量。":"读取本机真实状态后展示商品、匹配与审批进度。"}/></Card>:<>
   <Card title="运行状态" action={<Pill tone={data.counts.unknown>0?"warning":data.readiness.ready?"success":"neutral"}>{firstOutreachLabel(data.runtime.state)}</Pill>}>
    <div className="space-y-4 p-5">
     {(data.runtime.reason||data.readiness.blockers.length>0)&&<Notice tone="warning"><div className="space-y-1"><p className="font-medium">当前等待</p>{Array.from(new Set([...(data.runtime.reason?[data.runtime.reason]:[]),...data.readiness.blockers])).map(reason=><p key={reason}>{firstOutreachLabel(reason)}</p>)}</div></Notice>}
     <div className="grid grid-cols-2 gap-3 lg:grid-cols-5"><StatTile label="可推荐商品" value={data.counts.products} hint="有效免费样品且有额度"/><StatTile label="匹配达人" value={data.counts.matchedCreators} hint="主营类目一致 · GMV排序" brand/><StatTile label="成功推送" value={data.counts.sent} hint="已确认成功的推荐"/><StatTile label="自动审批通过" value={data.counts.approved} hint="已确认平台通过"/><StatTile label="待处理申请" value={data.counts.pendingApplications} hint={`已确认需人工 ${data.counts.needsReview.toLocaleString()} · 结果未知 ${data.counts.unknown.toLocaleString()}`}/></div>
     <div className="flex flex-wrap gap-3">
      <Button variant="outline" disabled={!!busy||unavailable||active||processing} onClick={()=>void call("prepare")}>{busy==="prepare"?"准备中…":"准备匹配"}</Button>
      <Button disabled={!!busy||unavailable||active||processing||!data.readiness.ready||data.counts.unknown>0} onClick={()=>setConfirmation("start")}>验证首条</Button>
      <Button disabled={!!busy||unavailable||processing||!canContinue||!data.readiness.ready} onClick={()=>setConfirmation("continue")}>继续持续推送</Button>
      <Button variant="danger" disabled={busy==="stop"} onClick={()=>void call("stop")}>停止持续推送</Button>
      {data.counts.unknown>0&&<Button variant="outline" disabled={!!busy||unavailable} onClick={()=>void call("reconcile")}>核验原操作</Button>}
     </div>
     <p className="text-xs leading-5 text-gray-500">首条确认后暂停，点击“继续持续推送”才会持续执行。停止立即阻止新的推送；自动审批由下方独立开关控制，已在途的结果按原操作核验。</p>
     <p className="text-xs text-gray-400">状态更新：{time(data.runtime.updatedAt)}{data.sourceUpdatedAt!==undefined&&` · 数据更新：${time(data.sourceUpdatedAt)}`} · 北京时间</p>
    </div>
   </Card>
   <Card title="样品申请" subtitle="仅自动通过本功能成功推荐的同一达人、商品与活动，申请时仍需符合规则。" action={<Button size="sm" variant="outline" disabled={!!busy||unavailable||processing} onClick={()=>void call("sync")}>{busy==="sync"?"同步中…":"同步商品与申请"}</Button>}>
    <div className="px-5 py-2"><Toggle label={`自动审批${data.control.approvalsEnabled?"已开启":"已关闭"}`} description="审批单独开启。其他申请和涉及机构付款的申请保留待处理。" checked={data.control.approvalsEnabled} disabled={!!busy||unavailable} onChange={enabled=>enabled?setConfirmation("approvals"):void call("set-approvals",false)}/></div>
    <details className="mx-5 mb-5 rounded-xl border border-gray-200 dark:border-gray-700"><summary className="cursor-pointer px-4 py-3 text-sm font-medium">申请明细{data.applications.length>0&&` · 展示 ${data.applications.length} 条`}</summary>{data.applications.length===0?<p className="px-4 pb-4 text-sm text-gray-500">当前已读取的状态中暂无申请。</p>:<div className="overflow-x-auto"><table className="w-full min-w-[640px] text-left text-sm"><thead className="bg-gray-50 text-xs text-gray-500 dark:bg-gray-800"><tr><th className="px-4 py-3">达人</th><th className="px-4 py-3">商品</th><th className="px-4 py-3">状态与原因</th></tr></thead><tbody>{data.applications.map(row=><tr key={row.id} className="border-t border-gray-100 dark:border-gray-800"><td className="px-4 py-3">{row.handle==="身份待核对"?row.handle:`@${row.handle.replace(/^@/,"")}`}</td><td className="max-w-sm px-4 py-3"><p>{row.productName}</p><p className="mt-1 text-xs text-gray-400">{row.pid}</p></td><td className="px-4 py-3"><p>{firstOutreachLabel(row.state)}</p>{row.reason&&<p className="mt-1 text-xs text-gray-500">{firstOutreachLabel(row.reason)}</p>}</td></tr>)}</tbody></table></div>}</details>
   </Card>
   <details className="rounded-2xl border border-gray-200 bg-white dark:border-gray-800 dark:bg-white/[0.025]"><summary className="cursor-pointer px-5 py-4 text-sm font-medium">候选明细与葡语邀约预览{data.candidates.length>0&&` · 展示 ${data.candidates.length} 位`}</summary><div className="space-y-3 p-5 pt-0">{data.candidates.length===0?<p className="text-sm text-gray-500">暂无可展示的匹配候选。可先准备匹配，并查看当前等待原因。</p>:data.candidates.map(row=><details key={`${row.creatorId}:${row.pid}`} className="rounded-xl border border-gray-200 px-4 py-3 dark:border-gray-700"><summary className="cursor-pointer text-sm"><span className="font-medium">@{row.handle.replace(/^@/,"")}</span><span className="ml-3 text-gray-500">{row.productName}</span></summary><div className="mt-3 space-y-2 text-xs leading-6 text-gray-500"><p>主营类目：{row.categoryName} · GMV：{row.gmv===null?"尚未读取":`R$ ${row.gmv}`} · PID：{row.pid}</p><p className="rounded-lg bg-gray-50 p-3 text-sm text-gray-700 dark:bg-gray-800 dark:text-gray-200" lang="pt-BR">{row.message}</p></div></details>)}</div></details>
   <details className="text-xs leading-6 text-gray-500"><summary className="cursor-pointer">本期匹配与审批规则</summary><p className="mt-2">使用现有巴西达人库，仅匹配主营带货类目；带货表现和GMV用于排序，不设固定GMV或粉丝数门槛。商品必须明确支持免费样品、活动有效且仍有样品额度。每位达人选择最合适的一品，与二发共用72小时冷却。申请审批重新核对推荐成功记录、活动、当前资格、样品额度和重复申请。</p></details>
  </>}
  <Dialog open={confirmation!==null} onClose={()=>{if(!busy)setConfirmation(null);}} title={confirmation==="approvals"?"开启自动审批":confirmation==="continue"?"继续持续推送":"开始首条验证"}>
   <p className="text-sm leading-6 text-gray-600 dark:text-gray-300">{confirmation==="approvals"?"开启后，仅对本功能已成功推荐且申请时仍符合规则的样品申请自动通过；其余保留待处理。":confirmation==="continue"?"首条结果已确认。继续后，将按当前规则持续向匹配达人推送免费样品商品。":"将向一位符合当前规则的达人发送商品推荐，下方为首位候选的葡语邀约预览。发送前重新核对资格，确认首条结果后暂停，等待你继续。"}</p>
   {confirmation==="start"&&data?.candidates[0]&&<div className="mt-4 rounded-xl bg-gray-50 p-4 text-sm dark:bg-gray-800"><p className="font-medium">@{data.candidates[0].handle.replace(/^@/,"")} · {data.candidates[0].productName}</p><p className="mt-3 whitespace-pre-wrap leading-6" lang="pt-BR">{data.candidates[0].message}</p></div>}
   <div className="mt-6 flex justify-end gap-3"><Button variant="outline" disabled={!!busy} onClick={()=>setConfirmation(null)}>取消</Button><Button disabled={!!busy||unavailable} onClick={()=>void confirm()}>{busy?"正在提交…":confirmation==="approvals"?"确认开启审批":confirmation==="continue"?"确认持续推送":"确认发送首条"}</Button></div>
  </Dialog>
 </div>;
}
