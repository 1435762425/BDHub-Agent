"use client";

import Link from "next/link";
import { IdentityContext } from "../creator-identities/IdentityContext";
import { useCallback, useEffect, useRef, useState } from "react";
import { Button, Card, EmptyState, Field, Icon, Notice, PageHeading, Pill, Select } from "../bdhub/ui";
import type {
  SecondPilotAction, SecondPilotCase, SecondPilotOverview, SecondPilotScenario,
  SecondPilotSnapshot,
} from "./contracts";

const API = "/api/second-pilot";
const PENDING_KEY = "bdhub-second-pilot-pending-v1";
const PAGE_SIZE = 12;
type Command =
  | {type:"freeze";caseId:string;expectedRevision:number}
  | {type:"queue";snapshotId:string;scenario:SecondPilotScenario}
  | {type:"control";caseId:string;expectedRevision:number;mode:"running"|"paused"}
  | {type:"verify";actionId:string};
type Request = {requestId:string;command:Command};
type CommandResult = {kind:"snapshot";result:SecondPilotSnapshot}|{kind:"action";result:SecondPilotAction}|{kind:"case";result:SecondPilotCase};
type CasePage = {items:SecondPilotCase[];total:number;offset:number;limit:number};
type RequestError = {message:string;code:string;retry?:Request};
const statuses: Record<SecondPilotAction["status"], string> = {
  queued:"等待本地 Worker",submitting:"本地提交中",simulated_accepted:"模拟接收成功",
  result_unknown:"模拟结果未知",cancelled:"已取消预演",
};
const scenarioLabels:Record<SecondPilotScenario,string> = {
  accepted:"正常取得回执",receipt_lost:"提交后回执丢失",before_submit_crash:"提交前中断",
};
function date(value:number|null) {
  return value === null ? "未记录" : new Intl.DateTimeFormat("zh-CN",{month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false,timeZone:"Asia/Shanghai"}).format(value);
}
function sourceWindow(start:number|null,end:number|null) {
  const day=(value:number|null)=>value===null?"未记录":new Date(value).toISOString().slice(0,10);
  return `${day(start)} — ${day(end)}`;
}
function isRequest(value:unknown):value is Request {
  if(!value||typeof value!=="object")return false;
  const request=value as Request,command=request.command;
  if(typeof request.requestId!=="string"||!request.requestId||!command)return false;
  if(command.type==="freeze"||command.type==="control")return typeof command.caseId==="string"&&Number.isInteger(command.expectedRevision)&&command.expectedRevision>=0&&(command.type==="freeze"||["running","paused"].includes(command.mode));
  if(command.type==="queue")return typeof command.snapshotId==="string"&&["accepted","receipt_lost","before_submit_crash"].includes(command.scenario);
  return command.type==="verify"&&typeof command.actionId==="string";
}
function isResult(value:unknown):value is CommandResult {
  if(!value||typeof value!=="object")return false;
  const result=value as CommandResult;
  return ["snapshot","action","case"].includes(result.kind)&&typeof result.result?.id==="string";
}

function useCommands(onConfirmed:(result?:CommandResult)=>void) {
  const [ready,setReady]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState<RequestError|null>(null),[notice,setNotice]=useState("");
  const pending=useRef<Request|null>(null),writing=useRef(false),mounted=useRef(false),controller=useRef<AbortController|null>(null);
  useEffect(()=>{
    mounted.current=true;
    try {
      const raw=sessionStorage.getItem(PENDING_KEY);
      if(raw){const request:unknown=JSON.parse(raw);if(!isRequest(request))throw new Error("invalid");pending.current=request;setError({code:"pending_restored",message:"上次预演操作的结果尚未确认。恢复原请求即可继续，沿用同一个请求编号。",retry:request});}
      setReady(true);
    }catch{setError({code:"request_storage",message:"无法读取本地请求编号，请恢复浏览器会话存储后重新打开页面。"});}
    return ()=>{mounted.current=false;controller.current?.abort();};
  },[]);
  const execute=useCallback(async(request:Request)=>{
    if(!mounted.current||writing.current||(pending.current&&pending.current.requestId!==request.requestId))return;
    try {sessionStorage.setItem(PENDING_KEY,JSON.stringify(request));}
    catch{setError({code:"request_storage",message:"无法保存请求编号，本次操作没有提交。"});return;}
    pending.current=request;writing.current=true;setBusy(true);setError(null);setNotice("");
    const current=new AbortController();controller.current=current;
    const clear=()=>{pending.current=null;try{sessionStorage.removeItem(PENDING_KEY);}catch{}};
    try {
      const response=await fetch(API,{method:"POST",credentials:"same-origin",signal:current.signal,headers:{"Content-Type":"application/json"},body:JSON.stringify(request)});
      const body=await response.json().catch(()=>null);
      if(!mounted.current)return;
      if(!response.ok){
        const rejected=response.status>=400&&response.status<500;if(rejected)clear();
        setError({code:body?.error?.code||`HTTP_${response.status}`,message:body?.error?.message||"本地预演操作未完成。",retry:rejected?undefined:request});
        if(response.status===409)onConfirmed();
        return;
      }
      if(!isResult(body))throw new Error("本地服务响应不完整，暂不能确认操作结果。");
      clear();
      setNotice(body.kind==="snapshot"?"预演稿已冻结，内容和来源版本保存在本机。":body.kind==="case"?body.result.localControl==="paused"?"此关系的本地预演已暂停。":"此关系已允许继续本地预演。":body.result.note||"本地操作已确认。");
      onConfirmed(body);
    }catch(failure){if(mounted.current&&!current.signal.aborted)setError({code:"request_unconfirmed",message:failure instanceof TypeError?"与本地服务的连接中断，操作结果尚未确认。":failure instanceof Error?failure.message:"操作结果尚未确认。",retry:request});}
    finally{writing.current=false;if(mounted.current)setBusy(false);}
  },[onConfirmed]);
  return {busy,error,notice,blocked:!ready||busy||Boolean(error?.retry)||error?.code==="request_storage",submit:(command:Command)=>{if(!writing.current&&!pending.current&&ready)void execute({requestId:crypto.randomUUID(),command});},retry:()=>{if(error?.retry)void execute(error.retry);},dismiss:()=>setError(null)};
}

function ActionStatus({action}:{action:SecondPilotAction}) {
  return <Pill tone={action.status==="simulated_accepted"?"success":action.status==="result_unknown"?"warning":action.status==="queued"||action.status==="submitting"?"brand":"neutral"}>{statuses[action.status]}</Pill>;
}
function ActionHistory({actions,disabled,onVerify}:{actions:SecondPilotAction[];disabled:boolean;onVerify:(id:string)=>void}) {
  if(!actions.length)return <p className="py-3 text-sm leading-6 text-gray-500">尚无本地预演记录。冻结稿件后，可选择一个场景交给本地 Worker。</p>;
  return <div className="space-y-3">{[...actions].sort((a,b)=>b.createdAt-a.createdAt).map(action=><article key={action.id} className="rounded-xl border border-gray-200 p-4 dark:border-gray-800">
    <div className="flex flex-wrap items-center justify-between gap-2"><p className="text-sm font-medium text-gray-800 dark:text-gray-200">{scenarioLabels[action.scenario]}</p><ActionStatus action={action}/></div>
    <p className="mt-2 text-xs leading-6 text-gray-500">{action.note||"等待本地 Worker 更新预演状态。"}</p>
    {action.status==="result_unknown"&&<div className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-lg bg-warning-50 px-3 py-2 dark:bg-warning-900/10"><p className="text-xs leading-5 text-warning-700 dark:text-warning-400">先查询本地模拟器的原始回执，不重发这条内容。</p><Button size="sm" variant="outline" disabled={disabled} onClick={()=>onVerify(action.id)}>核验原回执</Button></div>}
    <details className="mt-2 text-xs text-gray-400"><summary className="cursor-pointer py-1">查看预演记录</summary><div className="mt-2 space-y-1 break-all leading-5"><p>创建于 {date(action.createdAt)}（北京时间） · 提交尝试 {action.attempts} 次</p><p>冻结稿：{action.snapshotId}</p><p>动作：{action.id}</p><p>模拟回执：{action.receiptRef||"尚未取得"}</p><p>提交于 {date(action.submittedAt)} · 结果明确于 {date(action.resolvedAt)}</p><p>仅本地模拟 · 真实发送 0</p></div></details>
  </article>)}</div>;
}
function OverviewMetrics({overview,results}:{overview:SecondPilotOverview|null;results:boolean}) {
  const metrics=results?[
    ["模拟接收成功",overview?.simulatedAccepted],["结果未知待核验",overview?.resultUnknown],["预演提交尝试",overview?.attempts],["真实发送",overview?.realSends],
  ]:[
    ["真实来源达人",overview?.cases],["同品证据组合",overview?.edges],["已冻结稿件",overview?.frozen],["真实发送",overview?.realSends],
  ];
  return <div className="mb-6 grid grid-cols-2 gap-4 xl:grid-cols-4">{metrics.map(([label,value])=><Card key={label}><div className="p-4 sm:p-5"><p className="text-xs text-gray-500">{label}</p><p className="mt-3 text-2xl font-semibold tracking-tight text-gray-800 dark:text-gray-200">{value===undefined?"—":Number(value).toLocaleString()}</p></div></Card>)}</div>;
}

export default function SecondPilotWorkspace({view="workspace"}:{view?:"workspace"|"results"}) {
  const results=view==="results";
  const [overview,setOverview]=useState<SecondPilotOverview|null>(null),[page,setPage]=useState<CasePage|null>(null),[selectedId,setSelectedId]=useState(""),[selected,setSelected]=useState<SecondPilotCase|null>(null),[offset,setOffset]=useState(0),[refresh,setRefresh]=useState(0),[loading,setLoading]=useState(true),[readError,setReadError]=useState("");
  const [scenario,setScenario]=useState<SecondPilotScenario>("accepted");
  const reload=useCallback(()=>setRefresh(value=>value+1),[]);
  const confirmed=useCallback((result?:CommandResult)=>{
    if(result)setSelectedId(result.kind==="case"?result.result.id:result.result.caseId);
    setRefresh(value=>value+1);
  },[]);
  const commands=useCommands(confirmed);
  const readSequence=useRef(0);
  useEffect(()=>{
    const controller=new AbortController(),sequence=++readSequence.current;let reading=false;
    setLoading(true);
    const read=async(url:string)=>{const response=await fetch(url,{credentials:"same-origin",cache:"no-store",signal:controller.signal});const body=await response.json();if(!response.ok)throw new Error(body?.error?.message||"无法读取本地预演数据。");return body;};
    const load=async()=>{
      if(reading||document.visibilityState!=="visible")return;reading=true;
      try {
        const [nextOverview,nextPage,nextCase]=await Promise.all([read(`${API}?view=overview`),read(`${API}?view=cases&offset=${offset}&limit=${PAGE_SIZE}`),selectedId?read(`${API}?view=case&caseId=${encodeURIComponent(selectedId)}`):Promise.resolve(null)]);
        if(controller.signal.aborted||sequence!==readSequence.current)return;
        if(nextOverview.mode!=="dry_run"||nextOverview.realSends!==0||!Array.isArray(nextPage.items)||typeof nextPage.total!=="number"||(nextCase&&nextCase.id!==selectedId))throw new Error("预演数据响应不完整，请重新读取。");
        setOverview(nextOverview);setPage(nextPage);setReadError("");
        if(nextCase)setSelected(nextCase);
        else if(!selectedId&&nextPage.items.length)setSelectedId(nextPage.items[0].id);
      }catch(failure){if(!controller.signal.aborted&&sequence===readSequence.current)setReadError(failure instanceof Error?failure.message:"无法连接本地预演服务。");}
      finally{reading=false;if(!controller.signal.aborted&&sequence===readSequence.current)setLoading(false);}
    };
    void load();const timer=window.setInterval(()=>void load(),3000);const visible=()=>{if(document.visibilityState==="visible")void load();};document.addEventListener("visibilitychange",visible);
    return ()=>{controller.abort();window.clearInterval(timer);document.removeEventListener("visibilitychange",visible);};
  },[offset,selectedId,refresh]);
  const current=selected?.id===selectedId?selected:null;
  const snapshots=current?[...current.snapshots].sort((a,b)=>b.createdAt-a.createdAt||b.revision-a.revision):[];
  const snapshot=snapshots[0]||null;
  const currentSnapshot=snapshot&&current&&snapshot.revision===current.revision&&snapshot.sourceFingerprint===current.sourceFingerprint&&snapshot.localControlRevision===current.localControlRevision&&snapshot.input.draft.text===current.draft.text?snapshot:null;
  const accepted=Boolean(current?.actions.some(action=>action.status==="simulated_accepted"));
  const unresolved=Boolean(current?.actions.some(action=>action.status==="submitting"||action.status==="result_unknown"));
  const hasAction=Boolean(currentSnapshot&&current?.actions.some(action=>action.snapshotId===currentSnapshot.id&&action.status!=="cancelled"));
  const locked=commands.blocked||Boolean(readError)||!current;

  return <div className="min-w-0">
    <PageHeading title={results?"二发预演结果":"二发预演工作台"} description={results?"查看本地队列、模拟回执和未知结果核验，不将预演结果计作真实触达。":"将同一位达人的少量同品机会合并为一条意大利语草稿，冻结后在本地模拟执行。"} action={<div className="flex flex-wrap items-center gap-2"><Link href="/creators" className="inline-flex items-center gap-2 rounded-lg border border-gray-300 bg-white px-4 py-2.5 text-sm font-medium text-gray-700 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300"><Icon name="users" className="size-4"/>达人库</Link><Link href={results?"/workspace?mode=second":"/results?mode=second"} className="inline-flex items-center gap-2 rounded-lg border border-gray-300 bg-white px-4 py-2.5 text-sm font-medium text-gray-700 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300"><Icon name={results?"chat":"chart"} className="size-4"/>{results?"回到预演工作台":"查看预演结果"}</Link></div>}/>
    <div className="mb-5 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-brand-100 bg-brand-50 px-4 py-3 dark:border-brand-900 dark:bg-brand-500/10"><p className="text-sm font-medium text-brand-600 dark:text-brand-300">本地发送预演，未触达达人</p><div className="flex flex-wrap items-center gap-3 text-xs text-gray-500"><span>意大利 · IT</span><span>模型调用 {overview?.modelCalls??"—"}</span><Pill tone={overview?.worker.online?"success":"neutral"}>{overview?.worker.online?"本地 Worker 在线":"本地 Worker 未在线"}</Pill></div></div>
    <OverviewMetrics overview={overview} results={results}/>
    {overview&&<div className="mb-5 flex flex-wrap gap-x-5 gap-y-2 text-xs text-gray-500"><span>排队 {overview.queued}</span><span>提交中 {overview.submitting}</span><span>模拟结果未知 {overview.resultUnknown}</span><span>取消 {overview.cancelled}</span><span>本地暂停 {overview.paused} 位</span><span>模拟回执 {overview.simulatedReceipts} 条</span></div>}
    {overview&&!overview.worker.online&&<div className="mb-5"><Notice>本地 Worker 暂未在线，已保存的队列会保留。页面仅展示状态，不代替 Worker 执行任务。最近心跳：{date(overview.worker.lastSeenAt)}（北京时间）。</Notice></div>}
    {readError&&<div className="mb-5"><Notice tone="warning"><p>{readError}</p><Button size="sm" variant="outline" className="mt-2" onClick={reload}>重新读取</Button></Notice></div>}
    {commands.error&&<div className="mb-5" role="alert"><Notice tone="warning"><p>{commands.error.message}</p><div className="mt-3 flex flex-wrap items-center gap-3">{commands.error.retry?<Button size="sm" variant="outline" disabled={commands.busy} onClick={commands.retry}>{commands.busy?"正在恢复…":"恢复原请求"}</Button>:commands.error.code!=="request_storage"&&<Button size="sm" variant="outline" onClick={commands.dismiss}>知道了</Button>}<details className="text-xs"><summary className="cursor-pointer">查看请求编号</summary><p className="mt-2 break-all">{commands.error.code}{commands.error.retry&&` · ${commands.error.retry.requestId}`}</p></details></div></Notice></div>}
    {commands.notice&&<p role="status" className="mb-5 rounded-xl bg-success-50 px-4 py-3 text-sm leading-6 text-success-700 dark:bg-success-500/10 dark:text-success-400">{commands.notice}</p>}
    <div className="grid min-w-0 items-start gap-6 lg:grid-cols-[270px_minmax(0,1fr)] xl:grid-cols-[320px_minmax(0,1fr)]">
      <Card title="真实来源名单" subtitle={overview?`${overview.cases} 位达人 · ${overview.products} 个商品，来自同品销量记录。`:"正在读取名单…"}>
        <div className="p-3 sm:p-4" aria-busy={loading}>
          {!page&&loading?<p className="py-8 text-center text-sm text-gray-400">正在读取名单…</p>:!page?.items.length?<p className="py-8 text-center text-sm text-gray-500">暂无已导入预演资料。</p>:<div className="space-y-2">{page.items.map(item=><button key={item.id} type="button" aria-pressed={item.id===selectedId} disabled={commands.busy} onClick={()=>{setSelectedId(item.id);setScenario("accepted");}} className={`w-full rounded-xl border p-3 text-left transition disabled:opacity-50 ${item.id===selectedId?"border-brand-200 bg-brand-50 dark:border-brand-800 dark:bg-brand-500/10":"border-transparent hover:bg-gray-50 dark:hover:bg-gray-800/40"}`}><div className="flex items-center justify-between gap-2"><span className="min-w-0 break-all text-sm font-semibold text-gray-800 dark:text-gray-200">@{item.handle.replace(/^@/,"")}</span>{item.localControl==="paused"&&<Pill tone="neutral">暂停</Pill>}</div><p className="mt-1 text-xs leading-5 text-gray-500">{item.products.length} 个商品机会 · {item.snapshots.length} 版冻结稿</p>{item.actions.length>0&&<div className="mt-2"><ActionStatus action={[...item.actions].sort((a,b)=>b.createdAt-a.createdAt)[0]}/></div>}</button>)}</div>}
          {page&&page.total>0&&<div className="mt-4 flex items-center justify-between gap-2 border-t border-gray-100 pt-3 text-xs text-gray-400 dark:border-gray-800"><span>{offset+1}–{Math.min(offset+PAGE_SIZE,page.total)} / {page.total}</span><div className="flex gap-1"><Button size="sm" variant="ghost" className="!px-2" disabled={commands.busy||offset===0} onClick={()=>setOffset(Math.max(0,offset-PAGE_SIZE))}>上页</Button><Button size="sm" variant="ghost" className="!px-2" disabled={commands.busy||offset+PAGE_SIZE>=page.total} onClick={()=>setOffset(offset+PAGE_SIZE)}>下页</Button></div></div>}
        </div>
      </Card>
      <div className="min-w-0 space-y-5">
        {!current?<Card><EmptyState title={loading?"正在读取关系资料":"选择一位达人"} description="每位达人保留一份关系资料，集中查看商品证据、稿件和本地预演记录。"/></Card>:<>
          <Card title={`@${current.handle.replace(/^@/,"")}`} subtitle={`${current.products.length} 个同品机会合并处理 · 意大利`} action={<div className="flex flex-wrap items-center gap-2"><Pill tone={current.localControl==="paused"?"neutral":"brand"}>{current.localControl==="paused"?"本地预演暂停":"允许本地预演"}</Pill><Button size="sm" variant="outline" disabled={locked} onClick={()=>commands.submit({type:"control",caseId:current.id,expectedRevision:current.revision,mode:current.localControl==="paused"?"running":"paused"})}>{current.localControl==="paused"?"恢复本地预演":"暂停本地预演"}</Button></div>}>
            <div className="space-y-4 p-4 sm:p-5">
              <div className="flex flex-wrap gap-x-6 gap-y-2 text-xs text-gray-500"><span>历史同品记录归属：尚未核验</span><span>真实关系控制：未知</span><span>真实拒联状态：未知</span></div>
              <IdentityContext market={current.market} externalId={current.creatorRef.id} sourceHandle={current.handle} />
              <details className="rounded-xl bg-gray-50 px-4 py-3 text-xs text-gray-500 dark:bg-gray-800/40"><summary className="cursor-pointer font-medium text-gray-700 dark:text-gray-300">真实执行尚缺哪些事实</summary><ul className="mt-3 list-disc space-y-1.5 pl-4 leading-6">{current.liveBlockers.map((blocker,index)=><li key={`${index}-${blocker}`}>{blocker}</li>)}</ul><p className="mt-2 leading-6">上方暂停与恢复仅控制本地预演，不修改真实达人关系、拒联记录或旧 BDHub 任务。</p></details>
              {!results&&<div className="space-y-3">{current.products.map(product=><article key={product.productId} className="rounded-xl border border-gray-200 p-4 dark:border-gray-800"><div className="flex flex-wrap items-start justify-between gap-3"><div className="min-w-0 flex-1"><h3 className="text-sm font-medium leading-6 text-gray-800 dark:text-gray-200">{product.title}</h3><p className="mt-1 text-xs leading-5 text-gray-500">{product.italianName}</p></div><Pill tone="brand">同品观测 {product.units.toLocaleString()} 件</Pill></div><details className="mt-2 text-xs text-gray-400"><summary className="cursor-pointer py-1">查看同品证据</summary><div className="mt-2 space-y-1 break-all leading-5"><p>精确 PID：{product.pid}</p><p>统计窗口：{sourceWindow(product.source.windowStart,product.source.windowEnd)}（来源日期）</p><p>{product.source.ref}</p><p>单次观测保留来源，不推断已持有实物、当前意愿或报价。</p></div></details></article>)}</div>}
            </div>
          </Card>
          {!results&&<Card title="合并后的意大利语草稿" subtitle="程序依据已有商品证据组织文本，未调用语言模型。" action={<Pill tone="neutral">未发送</Pill>}><div className="space-y-4 p-4 sm:p-5"><div lang="it" className="whitespace-pre-wrap break-words rounded-xl border border-gray-200 bg-gray-50 p-4 text-sm leading-7 text-gray-800 dark:border-gray-700 dark:bg-gray-800/40 dark:text-gray-200">{current.draft.text}</div><div className="flex flex-wrap items-center justify-between gap-3"><p className="text-xs text-gray-500">{current.products.length} 个商品 · 1 条文字稿 · 无报价或商品卡承诺</p><Button size="sm" disabled={locked||current.localControl==="paused"||accepted||unresolved} onClick={()=>commands.submit({type:"freeze",caseId:current.id,expectedRevision:current.revision})}><Icon name="lock" className="size-4"/>{commands.busy?"正在处理…":accepted?"本关系已完成模拟接收":unresolved?"原提交结果需先明确":currentSnapshot?"复用当前冻结稿":"冻结当前预演稿"}</Button></div><details className="text-xs text-gray-400"><summary className="cursor-pointer py-1">查看草稿依据与版本</summary><div className="mt-2 space-y-1 break-all leading-5"><p>生成方式：确定性程序 · {current.draft.skillVersion}</p><p>来源版本：{current.sourceFingerprint}</p>{current.draft.claimRefs.map((ref,index)=><p key={`${index}-${ref}`}>{ref}</p>)}</div></details></div></Card>}
          {!results&&snapshot&&<Card title="冻结稿与本地队列" subtitle="队列只使用已冻结的文字和来源版本。" action={<Pill tone={currentSnapshot?"brand":"neutral"}>{currentSnapshot?"当前版本已冻结":"已有冻结稿，需重新冻结"}</Pill>}><div className="space-y-4 p-4 sm:p-5"><div className="flex flex-wrap gap-x-5 gap-y-2 text-xs text-gray-500"><span>冻结于 {date(snapshot.createdAt)}（北京时间）</span><span>资料版本 v{snapshot.revision}</span><span>本地控制版本 v{snapshot.localControlRevision}</span></div><details className="text-xs text-gray-500"><summary className="cursor-pointer py-1">查看实际冻结内容与编号</summary><p lang="it" className="mt-2 whitespace-pre-wrap rounded-xl bg-gray-50 p-4 text-sm leading-7 dark:bg-gray-800/40">{snapshot.input.draft.text}</p><div className="mt-3 space-y-1 break-all leading-5"><p>冻结稿：{snapshot.id}</p><p>内容校验：{snapshot.draftHash}</p><p>来源版本：{snapshot.sourceFingerprint}</p><p>运行方式：本地模拟器 · 真实执行关闭</p></div></details><Field label="预演场景"><Select value={scenario} disabled={locked||hasAction||current.localControl==="paused"||!currentSnapshot} onChange={event=>setScenario(event.target.value as SecondPilotScenario)}><option value="accepted">正常取得回执</option><option value="receipt_lost">提交后回执丢失 → 核验原回执</option><option value="before_submit_crash">提交前中断 → Worker 恢复</option></Select></Field><p className="text-xs leading-6 text-gray-500">{scenario==="accepted"?"本地模拟器接收冻结文本，返回模拟回执。":scenario==="receipt_lost"?"本地模拟器已接收文本，但 Worker 没拿到回执。结果记为未知，随后查询原回执。":"模拟 Worker 在提交前中断；持久队列和租约负责恢复，不另建发送意图。"}</p><div className="flex justify-end"><Button size="sm" disabled={locked||current.localControl==="paused"||!currentSnapshot||hasAction} onClick={()=>{if(currentSnapshot)commands.submit({type:"queue",snapshotId:currentSnapshot.id,scenario});}}><Icon name="send" className="size-4"/>{hasAction?"该冻结稿已进入预演":"加入本地模拟队列"}</Button></div></div></Card>}
          <Card title={results?"此关系的预演结果":"本地预演记录"} subtitle="模拟接收和真实触达分别记录；结果未知先核验。"><div className="p-4 sm:p-5"><ActionHistory actions={current.actions} disabled={locked} onVerify={actionId=>commands.submit({type:"verify",actionId})}/></div></Card>
        </>}
      </div>
    </div>
  </div>;
}
