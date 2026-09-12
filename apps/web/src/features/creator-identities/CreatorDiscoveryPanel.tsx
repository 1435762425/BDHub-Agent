"use client";

import Link from "next/link";
import {useCallback,useEffect,useRef,useState} from "react";
import {Button,Card,Dialog,Field,Icon,Input,Notice,Pill,Select,TextArea} from "../bdhub/ui";
import type {IdentityMarket} from "./contracts";
import type {DiscoveryBatch,DiscoveryDetail,DiscoveryList,DiscoveryPreview,CreatorDiscoveryPost} from "./discovery-contracts";

const API="/api/creator-discovery",PENDING_KEY="bdhub-creator-discovery-pending-v1";
type Mutation=Exclude<CreatorDiscoveryPost,{command:"preview"}>;
type BatchView=DiscoveryBatch&Pick<DiscoveryDetail,"items">;
type PendingError={message:string;retry:Mutation|null};
const batchLabels={queued:"等待解析",running:"正在解析",paused:"已暂停",completed:"已完成",blocked:"本次未完成"};
const itemLabels={queued:"等待解析",running:"正在解析",completed:"已入库",unresolved:"未精确匹配",blocked:"本次失败",invalid:"格式无效",duplicate:"名单重复"};
const reasonLabels:Record<string,string>={empty:"输入为空",invalid_handle:"handle 格式不正确",invalid_url:"不是有效的达人主页",unsupported_url:"请使用 TikTok 达人主页链接",duplicate:"与本名单其他行重复",duplicate_handle:"与本名单其他行重复",no_exact_handle:"未找到精确 handle",looks_like_id:"看起来是 ID，请使用 handle；数字用户名请显式加 @",request_timeout:"本次平台请求超时",find_failed:"尚未取得精确身份",profile_failed:"身份已取得，画像暂未完成",request_or_signer_error:"平台请求暂未成功",worker_interrupted_inflight:"上次请求中断，已保存资料保留",unknown_error:"本次未取得完整结果",not_found:"未找到精确 handle",find_not_exact:"未找到精确 handle",no_exact_match:"未找到精确 handle",account_not_startable:"采集账号暂不可用",account_busy:"采集账号正在使用",maintenance_due:"采集账号维护中",shared_backoff:"平台请求正在退避",verification_required:"平台验证尚未完成",remote_error:"平台暂未返回可用资料",whole_probe_deadline:"本次抓取超时",worker_interrupted:"上次解析中断，已保存结果保留",profile_incomplete:"尚未取得完整画像",identity_mismatch:"平台身份回执不一致"};
function errorMessage(value:unknown,fallback:string) {const body=value as {error?:{message?:unknown}}|null;return typeof body?.error?.message==="string"?body.error.message:fallback;}
function batch(value:unknown):value is DiscoveryBatch {const item=value as DiscoveryBatch|null;return Boolean(item&&typeof item==="object"&&typeof item.id==="string"&&item.id&&Object.hasOwn(batchLabels,item.status)&&item.counts&&typeof item.counts.total==="number");}
function mutation(value:unknown):value is Mutation {if(!value||typeof value!=="object")return false;const item=value as Mutation;if(typeof item.requestId!=="string"||!item.requestId)return false;return item.command==="submit"?item.market==="it"&&typeof item.text==="string"&&typeof item.sourceLabel==="string"&&typeof item.previewHash==="string":item.command==="control"&&typeof item.batchId==="string"&&["pause","resume"].includes(item.action);}
function date(value:string) {const time=Date.parse(value);return Number.isFinite(time)?new Intl.DateTimeFormat("zh-CN",{month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false,timeZone:"Asia/Shanghai"}).format(time):"时间未记录";}
function reason(value:string|null|undefined) {if(!value)return "";if(reasonLabels[value])return reasonLabels[value];if(value.startsWith("probe_")||value==="child_no_report")return "采集暂未取得完整结果";if(value.includes("identity")||value.includes("market_mismatch"))return "平台身份核对尚未完成";if(value.includes("verification"))return "平台验证尚未完成";if(value.includes("account")||value.includes("maintenance")||value.includes("backoff"))return "采集账号暂不可用";if(value.includes("timeout")||value==="timeout")return "本次请求超时";return "需进一步核验";}
function BatchStatus({value}:{value:DiscoveryBatch}) {return <Pill tone={value.status==="completed"?"success":value.status==="blocked"?"warning":value.status==="paused"?"neutral":"brand"}>{batchLabels[value.status]}</Pill>;}

function useDiscoveryMutation(onConfirmed:(value:DiscoveryBatch,request:Mutation)=>void) {
  const [busy,setBusy]=useState(false),[pending,setPending]=useState<Mutation|null>(null),[error,setError]=useState<PendingError|null>(null),[ready,setReady]=useState(false),[storageError,setStorageError]=useState(false);
  const writing=useRef(false),pendingRef=useRef<Mutation|null>(null),mounted=useRef(false),callback=useRef(onConfirmed),controller=useRef<AbortController|null>(null);
  callback.current=onConfirmed;
  useEffect(()=>{mounted.current=true;try{const raw=sessionStorage.getItem(PENDING_KEY);if(raw){const saved:unknown=JSON.parse(raw);if(!mutation(saved))throw new Error();pendingRef.current=saved;setPending(saved);setError({message:"上次操作的结果尚未确认，请恢复原请求。",retry:saved});}setReady(true);}catch{setStorageError(true);setError({message:"无法恢复请求编号，请检查浏览器会话存储后刷新页面。",retry:null});}return()=>{mounted.current=false;controller.current?.abort();};},[]);
  const execute=useCallback(async(request:Mutation)=>{
    if(writing.current||(pendingRef.current&&pendingRef.current.requestId!==request.requestId))return;
    try{sessionStorage.setItem(PENDING_KEY,JSON.stringify(request));}catch{setStorageError(true);setError({message:"无法保存请求编号，本次没有提交。",retry:null});return;}
    const clear=()=>{try{sessionStorage.removeItem(PENDING_KEY);}catch{}pendingRef.current=null;setPending(null);};
    writing.current=true;pendingRef.current=request;setPending(request);setBusy(true);setError(null);
    const current=new AbortController();controller.current=current;const timer=window.setTimeout(()=>current.abort(),20000);
    try{
      const response=await fetch(API,{method:"POST",credentials:"same-origin",signal:current.signal,headers:{"Content-Type":"application/json"},body:JSON.stringify(request)});const body:unknown=await response.json();
      if(!mounted.current)return;
      if(!response.ok){const definitive=[400,403,404,409,413,422].includes(response.status);if(definitive)clear();setError({message:errorMessage(body,"操作结果尚未确认，请恢复原请求。"),retry:definitive?null:request});return;}
      if(!batch(body)||body.market!=="it"||(request.command==="control"&&body.id!==request.batchId)||(request.command==="submit"&&body.sourceLabel!==request.sourceLabel.trim()))throw new Error("invalid_response");
      clear();callback.current(body,request);
    }catch{if(mounted.current)setError({message:"操作结果尚未确认。恢复时会沿用原编号，不会新建重复批次。",retry:request});}
    finally{window.clearTimeout(timer);writing.current=false;if(mounted.current)setBusy(false);}
  },[]);
  return {busy,pending,error,canDismiss:!storageError&&!pending,blocked:!ready||busy||Boolean(pending)||storageError,submit:(request:Mutation)=>{if(ready&&!storageError&&!writing.current&&!pendingRef.current)void execute(request);},retry:()=>{if(pendingRef.current)void execute(pendingRef.current);},dismiss:()=>{if(!pendingRef.current&&!storageError)setError(null);}};
}

function PreviewRows({value}:{value:DiscoveryPreview}) {
  return <div className="overflow-hidden rounded-xl border border-gray-200 dark:border-gray-700"><div className="grid grid-cols-4 gap-2 bg-gray-50 p-4 dark:bg-gray-800/40">{[["有效",value.counts.valid],["重复",value.counts.duplicate],["无效",value.counts.invalid],["输入",value.counts.total]].map(([label,count])=><div key={label}><p className="text-xs text-gray-500">{label}</p><p className="mt-1 text-xl font-semibold text-gray-800 dark:text-gray-200">{count}</p></div>)}</div><div className="max-h-64 overflow-y-auto divide-y divide-gray-100 dark:divide-gray-800">{value.items.map(item=><div key={item.index} className="grid grid-cols-[2rem_minmax(0,1fr)_auto] items-start gap-2 px-4 py-3 text-xs"><span className="pt-0.5 text-gray-400">{item.index}</span><div className="min-w-0"><p className="break-all font-medium text-gray-700 dark:text-gray-200">{item.handle?`@${item.handle}`:item.raw||"空行"}</p>{item.status!=="valid"&&<p className="mt-1 break-words leading-5 text-gray-500">{(item.status==="duplicate"&&item.duplicateOf?`与第 ${item.duplicateOf} 项重复。`:reason(item.reason))|| (item.status==="duplicate"?"本名单已包含此 handle。":"请检查此项输入格式。")}</p>}</div><Pill tone={item.status==="valid"?"success":item.status==="invalid"?"warning":"neutral"}>{item.status==="valid"?"有效":item.status==="duplicate"?"重复":"无效"}</Pill></div>)}</div></div>;
}
function BatchResults({value,onNavigate}:{value:BatchView;onNavigate:()=>void}) {
  return <div className="max-h-80 overflow-y-auto divide-y divide-gray-100 rounded-xl border border-gray-200 dark:divide-gray-800 dark:border-gray-700">{value.items.map(item=><div key={item.id} className="flex flex-wrap items-start justify-between gap-3 px-4 py-3"><div className="min-w-0 flex-1"><p className="break-all text-sm font-medium text-gray-700 dark:text-gray-200"><span className="mr-2 text-xs font-normal text-gray-400">{item.index}</span>{item.handle?`@${item.handle}`:"无效输入"}</p>{item.outcome==="identity_only"?<p className="mt-1 text-xs leading-5 text-gray-500">OEC 已保留，画像尚未补齐，可从档案按 OEC 继续刷新。</p>:item.reason&&<p className="mt-1 break-words text-xs leading-5 text-gray-500">{reason(item.reason)}</p>}{item.creatorId&&<Link href={`/creators?market=it&creatorId=${encodeURIComponent(item.creatorId)}`} onClick={onNavigate} className="mt-1.5 inline-flex text-xs font-medium text-brand-500">查看达人档案 →</Link>}</div><Pill tone={item.status==="completed"?"success":item.status==="blocked"||item.status==="unresolved"?"warning":"neutral"}>{item.outcome==="created"?"新增达人":item.outcome==="existing"?"已有达人":item.outcome==="identity_only"?"已取得 OEC":itemLabels[item.status]}</Pill></div>)}</div>;
}

export default function CreatorDiscoveryPanel({open,onClose,onOpen,market,seed,onChanged}:{open:boolean;onClose:()=>void;onOpen:()=>void;market:IdentityMarket;seed:{handle:string;key:number}|null;onChanged:()=>void}) {
  const [targetMarket,setTargetMarket]=useState<IdentityMarket>(market),[text,setText]=useState(""),[sourceLabel,setSourceLabel]=useState("手动添加"),[preview,setPreview]=useState<{key:string;value:DiscoveryPreview}|null>(null),[checking,setChecking]=useState(false),[previewError,setPreviewError]=useState("");
  const [list,setList]=useState<DiscoveryList|null>(null),[selectedId,setSelectedId]=useState(""),[detail,setDetail]=useState<BatchView|null>(null),[readError,setReadError]=useState(""),[refresh,setRefresh]=useState(0);
  const callback=useRef(onChanged),lastSeen=useRef(new Map<string,string>()),readGeneration=useRef(0),previewGeneration=useRef(0),previewController=useRef<AbortController|null>(null),checkingRef=useRef(false),previewInput=useRef("");
  callback.current=onChanged;
  const inputKey=JSON.stringify({market:targetMarket,text,sourceLabel});previewInput.current=inputKey;
  const confirmed=useCallback((value:DiscoveryBatch,request:Mutation)=>{if(request.command==="submit"){setPreview(null);setSelectedId(value.id);}setRefresh(v=>v+1);callback.current();},[]);
  const commands=useDiscoveryMutation(confirmed),pending=commands.pending;
  const lastSeed=useRef<number|null>(null);
  const hasActive=Boolean(list?.batches.some(item=>item.status==="queued"||item.status==="running"));
  useEffect(()=>{if(pending?.command==="submit"){setText(pending.text);setSourceLabel(pending.sourceLabel);setTargetMarket(pending.market);}},[pending]);
  useEffect(()=>{if(!seed||seed.key===lastSeed.current||pending)return;lastSeed.current=seed.key;setText(seed.handle);setSourceLabel("待解析线索重查");setTargetMarket("it");setPreview(null);setPreviewError("");},[seed,pending]);
  useEffect(()=>()=>{previewGeneration.current++;previewController.current?.abort();},[]);
  useEffect(()=>{
    const controller=new AbortController(),generation=++readGeneration.current;let reading=false;
    const read=async()=>{
      if(reading||document.visibilityState!=="visible")return;reading=true;
      try{
        const response=await fetch(`${API}?view=list`,{cache:"no-store",credentials:"same-origin",signal:controller.signal});const nextList:unknown=await response.json();
        if(!response.ok)throw new Error(errorMessage(nextList,"无法读取最近批次。"));
        const next=nextList as DiscoveryList;if(!Array.isArray(next.batches)||!next.batches.every(batch))throw new Error("批次列表响应不完整。");
        const id=selectedId||next.batches[0]?.id||"";
        let nextDetail:BatchView|null=null;
        if(id&&open){const detailResponse=await fetch(`${API}?view=detail&batchId=${encodeURIComponent(id)}`,{cache:"no-store",credentials:"same-origin",signal:controller.signal});const result:unknown=await detailResponse.json();if(!detailResponse.ok)throw new Error(errorMessage(result,"无法读取批次详情。"));const found=result as DiscoveryDetail;if(!batch(found.batch)||found.batch.id!==id||!Array.isArray(found.items))throw new Error("批次详情响应不完整。");nextDetail={...found.batch,items:found.items};}
        if(controller.signal.aborted||generation!==readGeneration.current)return;
        setList(next);setDetail(nextDetail);setReadError("");if(!selectedId&&id)setSelectedId(id);
        let changed=false;for(const item of next.batches){const signature=JSON.stringify(item.counts);if(lastSeen.current.has(item.id)&&lastSeen.current.get(item.id)!==signature)changed=true;lastSeen.current.set(item.id,signature);}if(changed)callback.current();
      }catch(failure){if(!controller.signal.aborted&&generation===readGeneration.current)setReadError(failure instanceof Error?failure.message:"无法读取批次状态。");}
      finally{reading=false;}
    };
    void read();const timer=open||hasActive?window.setInterval(()=>void read(),open?3000:5000):null;
    const visible=()=>{if(open||hasActive)void read();};document.addEventListener("visibilitychange",visible);
    return()=>{controller.abort();if(timer!==null)window.clearInterval(timer);document.removeEventListener("visibilitychange",visible);};
  },[open,selectedId,refresh,hasActive]);
  const check=async()=>{
    if(checkingRef.current||commands.blocked||targetMarket!=="it")return;
    checkingRef.current=true;setChecking(true);setPreviewError("");previewController.current?.abort();const controller=new AbortController();previewController.current=controller;const generation=++previewGeneration.current,key=inputKey;
    const timer=window.setTimeout(()=>controller.abort(),15000);
    try{
      const response=await fetch(API,{method:"POST",credentials:"same-origin",signal:controller.signal,headers:{"Content-Type":"application/json"},body:JSON.stringify({command:"preview",market:targetMarket,sourceLabel,text})});const body:unknown=await response.json();
      if(!response.ok)throw new Error(errorMessage(body,"无法检查名单。"));const value=body as DiscoveryPreview;if(typeof value.previewHash!=="string"||!Array.isArray(value.items)||!value.counts||typeof value.canSubmit!=="boolean")throw new Error("名单检查响应不完整。");
      if(generation===previewGeneration.current&&key===previewInput.current)setPreview({key,value});
    }catch(failure){if(generation===previewGeneration.current&&key===previewInput.current)setPreviewError(controller.signal.aborted?"本地名单检查超时，请重试。":failure instanceof Error?failure.message:"无法检查名单。");}
    finally{window.clearTimeout(timer);checkingRef.current=false;if(generation===previewGeneration.current)setChecking(false);}
  };
  const freshPreview=preview?.key===inputKey?preview.value:null;
  const current=detail?.id===selectedId?detail:null,latest=list?.batches[0];
  const formLocked=commands.blocked;
  return <>
    {pending&&!open&&<div className="mb-5 flex flex-wrap items-center justify-between gap-3 rounded-xl bg-warning-50 px-4 py-3 text-xs text-warning-700 dark:bg-warning-500/10 dark:text-warning-400"><p>有一项入池操作尚待确认，已保留原请求编号。</p><Button size="sm" variant="outline" onClick={onOpen}>恢复原操作</Button></div>}
    {latest&&<div className="mb-5 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-gray-200 bg-white px-4 py-3 dark:border-gray-800 dark:bg-white/[0.025]"><div className="flex flex-wrap items-center gap-3"><p className="text-xs text-gray-500">最近入池批次 · {latest.sourceLabel}</p><BatchStatus value={latest}/><span className="text-xs text-gray-400">新增 {latest.counts.created} · 已有 {latest.counts.existing} · 未匹配 {latest.counts.unresolved}</span></div><Button size="sm" variant="ghost" onClick={()=>{setSelectedId(latest.id);onOpen();}}>查看批次</Button></div>}
    <Dialog open={open} onClose={onClose} title="批量添加达人" description="先检查名单，再按 handle 查找平台身份；后续始终跟随 OECID。" wide>
      <div className="space-y-6">
        <div className="grid gap-4 sm:grid-cols-[180px_minmax(0,1fr)]"><Field label="市场"><Select value={targetMarket} disabled={formLocked} onChange={event=>setTargetMarket(event.target.value as IdentityMarket)}><option value="it">意大利 · IT</option><option value="mx" disabled>墨西哥 · 待接入</option><option value="br" disabled>巴西 · 待接入</option></Select></Field><Field label="名单来源" hint="例如：9 月选品名单、运营整理。"><Input value={sourceLabel} maxLength={120} disabled={formLocked} onChange={event=>setSourceLabel(event.target.value)} placeholder="为这次输入起一个来源名称"/></Field></div>
        <Field label="达人名单" hint="每批最多 500 项。每行一个，或用逗号分隔；可粘贴 @handle 或 TikTok 达人主页链接。"><TextArea value={text} maxLength={65536} rows={5} disabled={formLocked} onChange={event=>setText(event.target.value)} placeholder={"@creator.one\nhttps://www.tiktok.com/@creator.two"}/></Field>
        {targetMarket!=="it"&&<p className="text-xs text-gray-500">本次仅开放意大利解析，请选择意大利市场。</p>}
        <div className="flex flex-wrap items-center justify-between gap-3"><p className="text-xs text-gray-500">检查名单只在本机核对格式与重复，不访问平台。</p><Button variant="outline" disabled={formLocked||checking||!text.trim()||!sourceLabel.trim()||targetMarket!=="it"} onClick={()=>void check()}>{checking?"正在检查…":"检查名单"}</Button></div>
        {previewError&&<p role="alert" className="text-xs text-error-500">{previewError}</p>}
        {preview&&!freshPreview&&<p role="status" className="rounded-lg bg-gray-50 px-4 py-3 text-xs text-gray-500 dark:bg-gray-800/40">名单或来源已修改，请重新检查后再提交。</p>}
        {freshPreview&&<div className="space-y-4"><PreviewRows value={freshPreview}/><div className="flex flex-wrap items-center justify-between gap-3"><p className="max-w-md text-xs leading-5 text-gray-500">按有效且不重复的 handle 解析。已存在的 OEC 会归入原档案，未匹配的输入仍保留在线索中。</p><Button disabled={formLocked||!freshPreview.canSubmit||targetMarket!=="it"} onClick={()=>commands.submit({command:"submit",market:"it",sourceLabel,text,previewHash:freshPreview.previewHash,requestId:crypto.randomUUID()})}><Icon name="plus" className="size-4"/>{commands.busy?"正在提交…":"开始解析并入库"}</Button></div></div>}
        {commands.error&&<Notice tone="warning"><p>{commands.error.message}</p>{commands.error.retry?<Button className="mt-3" size="sm" variant="outline" disabled={commands.busy} onClick={commands.retry}>{commands.busy?"正在恢复…":"恢复原请求"}</Button>:commands.canDismiss&&<Button className="mt-2" size="sm" variant="ghost" onClick={commands.dismiss}>知道了</Button>}</Notice>}
        <section className="space-y-4 border-t border-gray-100 pt-5 dark:border-gray-800"><div className="flex flex-wrap items-center justify-between gap-3"><div><h3 className="text-sm font-semibold text-gray-800 dark:text-gray-200">最近批次</h3><p className="mt-1 text-xs text-gray-500">关闭页面后任务仍保留，再次打开可继续查看。</p></div><Button size="sm" variant="ghost" onClick={()=>setRefresh(value=>value+1)}><Icon name="arrow" className="size-3.5"/>更新状态</Button></div>
          {list&&!list.workerOnline&&<p className="text-xs leading-5 text-gray-500">采集服务暂未在线，已提交的批次会保留。</p>}
          {readError&&<p role="status" className="text-xs text-error-500">{readError}</p>}
          {!list?<p className="py-3 text-xs text-gray-400">正在读取批次…</p>:!list.batches.length?<p className="py-3 text-xs text-gray-400">尚未创建入池批次。</p>:<>
            <Field label="查看批次"><Select value={selectedId} onChange={event=>setSelectedId(event.target.value)}>{list.batches.map(item=><option key={item.id} value={item.id}>{item.sourceLabel} · {date(item.createdAt)} · {batchLabels[item.status]}</option>)}</Select></Field>
            {current?<Card><div className="space-y-4 p-4"><div className="flex flex-wrap items-center justify-between gap-3"><div className="flex items-center gap-2"><BatchStatus value={current}/><p className="text-xs text-gray-500">{current.counts.total} 项输入</p></div>{["queued","running","paused","blocked"].includes(current.status)&&<Button size="sm" variant="outline" disabled={commands.blocked||Boolean(readError)||(current.status==="paused"||current.status==="blocked")&&current.counts.queued===0} onClick={()=>commands.submit({command:"control",batchId:current.id,action:current.status==="paused"||current.status==="blocked"?"resume":"pause",requestId:crypto.randomUUID()})}>{current.status==="paused"||current.status==="blocked"?"继续未处理项":"暂停批次"}</Button>}</div><div className="flex flex-wrap gap-x-5 gap-y-2 text-xs text-gray-500"><span>新增 {current.counts.created}</span><span>已有 {current.counts.existing}</span><span>仅取得 OEC {current.counts.identityOnly}</span><span>未匹配 {current.counts.unresolved}</span><span>失败 {current.counts.blocked}</span><span>等待 {current.counts.queued}</span></div>{current.status==="running"&&<p className="text-xs text-gray-500">逐项解析中；暂停会在当前请求完成后停止领取新项。</p>}{current.status==="blocked"&&<p className="text-xs leading-5 text-gray-500">{current.errorCode&&`${reason(current.errorCode)}。`}继续只处理尚未开始的项目，不重跑已失败项。</p>}<BatchResults value={current} onNavigate={onClose}/><details className="text-xs text-gray-400"><summary className="cursor-pointer">批次记录</summary><p className="mt-2 break-all">{current.id}</p><p className="mt-1">创建于 {date(current.createdAt)}（北京时间）</p></details></div></Card>:<p className="py-3 text-xs text-gray-400">正在读取所选批次…</p>}
          </>}
        </section>
        <p className="text-xs leading-5 text-gray-400">本次只扩充身份库与画像，不发送消息。</p>
      </div>
    </Dialog>
  </>;
}
