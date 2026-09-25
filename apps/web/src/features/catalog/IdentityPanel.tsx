"use client";
import {Button,Card,Field,Input,MetricTable,Notice,Pill,Progress} from "../bdhub/ui";
import type {IdentityProgress} from "../../server/identity-queue/bridge";
import type {IdentityController} from "./useIdentityQueue";

const STOP:Record<string,string>={
 backlog_clear:"待补充身份已处理完。",
 nothing_pending:"没有待补充身份。",
 limit_reached:"已达到本次上限。",
 stopped_by_operator:"已按要求停止。",
 retry_wait:"暂时没有到期的可查达人，系统将自动续查。",
 account_wait:"身份账号通道等待恢复，其他已解析达人继续。",
 queue_stalled:"当前没有可继续查询的达人。",
 round_limit:"已达到本次轮次上限。",
 round_timeout:"本轮超时，断点已保留。",
 round_output_invalid:"本轮没有返回可读结果。",
 account_not_startable:"采集账号暂不可用。",
 maintenance_due:"采集账号维护中。",
 shared_backoff:"平台正在退避。",
 verification_required:"平台验证尚未完成。",
 identity_policy_unreadable:"暂时读不到已发布的身份通道配置。",
 published_identity_policy_invalid:"身份通道配置与验收证据不一致。",
 internal_error:"查询进程异常停止。",
};

function failureDetail(progress:IdentityProgress){
 const last=progress.errors?.at(-1);
 if(!last?.detail)return null;
 return {round:last.round,text:last.detail.split("\n").map(line=>line.trimEnd()).filter(Boolean).slice(-3).join("\n")};
}

export default function IdentityPanel({controller}:{controller:IdentityController}){
 const {data,draft,setDraft,busy,message,save,start,stop,loaded}=controller;
 if(!data?.available)return <Card title="达人身份（OECID）"><div className="p-5"><p className="text-sm text-gray-500">{!loaded?"读取中…":data?"当前没有身份任务。":"暂时无法读取身份进度。"}</p></div></Card>;
 const progress=data.run?.progress??null;
 const running=Boolean(data.run?.running);
 const stopping=Boolean(data.run?.stopping);
 const by=data.byCreator;
 const batchTarget=progress?Math.min(progress.limit,progress.pendingAtStart):0;
 const elapsedSeconds=progress?Math.max(0,(running?Date.now()/1000:progress.updatedAt)-progress.startedAt):0;
 const perMinute=progress&&elapsedSeconds>0?progress.claimed/elapsedSeconds*60:0;
 const remaining=Math.max(0,batchTarget-(progress?.claimed??0));
 const etaMinutes=perMinute>0?Math.ceil(remaining/perMinute):null;
 const done=progress?.claimed??0;
 const stateLine=running
  ?progress?.retry?`通道配置读取重试 ${progress.retry} 次。`
   :progress?.roundRunning?`第 ${progress.roundRunning} 轮查询中。`:"正在准备第一轮…"
  :progress?.stopReason?(STOP[progress.stopReason]??`已停止：${progress.stopReason}`):null;
 const detail=progress?failureDetail(progress):null;

 return <Card title="达人身份（OECID）">
  <div className="space-y-4 p-5">
   <MetricTable rows={[
    {label:"已就位",value:(by?.resolved??data.resolvedCreators).toLocaleString(),detail:"可进入发送池"},
    {label:"待补充身份",value:(by?by.blocked+by.unknown:data.pendingCreators).toLocaleString(),detail:"等待查询 OECID",accent:true},
    {label:"技术隔离",value:(by?.isolated??0).toLocaleString(),detail:"停止重试，无需逐个处理"},
    {label:"搜索不到",value:(by?.unresolved??data.unresolvedCreators).toLocaleString(),detail:"平台没有返回精确达人"},
   ]}/>

   <div className="flex flex-wrap items-center gap-2 text-xs text-gray-500">
    {data.policy.published
     ?<Pill tone="success">{data.policy.lanes} lanes · {data.policy.qps} QPS · 验证重放已接通</Pill>
     :<Pill tone="warning">身份通道尚未发布</Pill>}
    {running&&(stopping?<Pill tone="warning">正在停止</Pill>:<Pill tone="brand">查询中</Pill>)}
   </div>

   {(running||stopping)&&batchTarget>0&&<div className="space-y-3">
    <Progress done={done} total={batchTarget} label={`当前进度 ${done.toLocaleString()} / ${batchTarget.toLocaleString()}`}/>
    <div className="grid gap-3 sm:grid-cols-3">
     {[
      ["速度",`${perMinute>0?perMinute.toFixed(1):"0"} 人/分`],
      ["预计剩余",etaMinutes==null?"—":etaMinutes<1?"不到 1 分钟":`约 ${etaMinutes} 分钟`],
      ["本次结果",`已就位 ${progress?.found??0} · 搜索不到 ${progress?.notFound??0}`],
     ].map(([label,value])=><div key={label} className="rounded-xl bg-brand-50 p-4 dark:bg-brand-500/10">
      <p className="text-xs text-gray-500">{label}</p>
      <p className="mt-2 text-lg font-semibold tabular-nums">{value}</p>
     </div>)}
    </div>
    {stateLine&&<p className="text-xs text-gray-500">{stateLine}</p>}
   </div>}
   {running&&!progress&&<Progress done={0} total={1} label="正在准备第一轮…"/>}
   {!running&&detail&&<details className="rounded-xl bg-gray-50 p-3 text-xs dark:bg-white/[0.03]">
    <summary className="cursor-pointer text-gray-500">第 {detail.round} 轮错误详情</summary>
    <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-all text-gray-600 dark:text-gray-300">{detail.text}</pre>
   </details>}

   <div className="grid gap-4 lg:grid-cols-2">
    <Field label="本次最多处理" hint="待补不足时自动提前结束"><Input type="number" min={1} max={200000} value={draft?.batchSize??data.config.batchSize} onChange={e=>draft&&setDraft({...draft,batchSize:Number(e.target.value)})}/></Field>
    <Field label="每轮处理" hint="推荐 50；共享 12 QPS"><Input type="number" min={10} max={50} value={draft?.cohortSize??data.config.cohortSize} onChange={e=>draft&&setDraft({...draft,cohortSize:Number(e.target.value)})}/></Field>
   </div>
   <div className="flex flex-wrap items-center gap-2">
    <Button size="sm" variant="outline" disabled={busy} onClick={()=>void save()}>{busy?"保存中…":"保存设置"}</Button>
    <Button size="sm" disabled={busy||running||data.pendingCreators===0} onClick={()=>void start()}>{running?"补充中…":"开始补充身份"}</Button>
    {running&&<Button size="sm" variant="outline" disabled={busy||stopping} onClick={()=>void stop()}>{stopping?"正在停止…":"停止"}</Button>}
    {!running&&(data.pendingCreators===0?<Pill tone="success">已完成</Pill>:<Pill tone="neutral">就绪</Pill>)}
   </div>
   {message&&<Notice tone="info">{message}</Notice>}
  </div>
 </Card>;
}
