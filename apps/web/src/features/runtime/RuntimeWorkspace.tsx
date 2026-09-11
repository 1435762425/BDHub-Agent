"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { useDemo } from "../bdhub/store";
import {
  Avatar, Button, EmptyState, Field, Icon, MarketPill, Notice,
  PageHeading, Pill, Select, Tabs, TextArea, type Tone,
} from "../bdhub/ui";
import {
  LOCAL_SCENARIOS, type RuntimeAction, type RuntimeCommand,
  type RuntimeCommandResult, type RuntimeComponent, type RuntimeEventKind,
  type RuntimeMarket, type RuntimeRelationship, type RuntimeSnapshot,
} from "./contracts";

const API = "/api/local-runtime";
const PENDING_KEY = "bdhub-local-runtime-pending-v1";
const marketNames: Record<RuntimeMarket, string> = { mx: "墨西哥", br: "巴西", it: "意大利" };
const statusLabels: Record<RuntimeRelationship["status"], string> = {
  idle: "等待新消息", processing: "处理中", waiting_creator: "等待达人回复",
  waiting_verification: "原结果待核验", needs_facts: "需要补充事实",
  human: "人工接管中", paused: "已暂停", adopted: "达人确认采用", waiting_until: "等待约定时间",
};
const componentLabels: Record<RuntimeComponent["status"], string> = {
  prepared: "待执行", submitting: "提交中", accepted: "模拟平台已接收",
  unknown: "结果未知", cancelled: "已取消",
};
const jobNames = { plan: "判断下一步", execute: "执行原意图", verify: "核验原结果", wake: "到期唤醒" };
const jobStatuses = { ready: "已排队", leased: "处理中", done: "已完成", cancelled: "已取消", blocked: "待处理" };
const eventOrder: RuntimeEventKind[] = ["request_card", "sample_question", "ask_later", "opt_out", "adopted"];

function time(value: number | null, full = false) {
  if (value === null) return "尚无记录";
  return new Intl.DateTimeFormat("zh-CN", full
    ? { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }
    : { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(value);
}

function statusTone(status: RuntimeRelationship["status"]): Tone {
  return status === "adopted" ? "success" : status === "waiting_verification" || status === "needs_facts"
    ? "warning" : status === "processing" ? "brand" : "neutral";
}

function shortId(value: string) { return value.length > 24 ? `${value.slice(0, 12)}…${value.slice(-8)}` : value; }
function isSnapshot(value: unknown): value is RuntimeSnapshot {
  if (!value || typeof value !== "object") return false;
  const snapshot = value as Partial<RuntimeSnapshot>;
  return snapshot.schemaVersion === 1 && snapshot.mode === "local-simulator"
    && typeof snapshot.at === "number" && Boolean(snapshot.worker) && Array.isArray(snapshot.relationships);
}

type Request = { requestId: string; command: RuntimeCommand };
type CommandError = { message: string; code: string; retry?: Request };

/** The API owns business state; this hook only retains the current view and request identity. */
function useLocalRuntime(notify: (message: string) => void) {
  const [snapshot, setSnapshot] = useState<RuntimeSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [readError, setReadError] = useState("");
  const [commandError, setCommandError] = useState<CommandError | null>(null);
  const [busy, setBusy] = useState(false);
  const mounted = useRef(false);
  const writing = useRef(false);
  const pending = useRef<Request | null>(null);
  const sequence = useRef(0);
  const readController = useRef<AbortController | null>(null);
  const writeController = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    if (!mounted.current || writing.current) return;
    readController.current?.abort();
    const controller = new AbortController();
    readController.current = controller;
    const currentSequence = ++sequence.current;
    setRefreshing(true);
    try {
      const response = await fetch(API, { signal: controller.signal, cache: "no-store", credentials: "same-origin" });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error?.message || "本地服务暂时无法读取数据。");
      if (!isSnapshot(body)) throw new Error("本地服务返回的数据格式不完整，请重新读取。");
      if (!mounted.current || currentSequence !== sequence.current) return;
      setSnapshot(body);
      setReadError("");
    } catch (error) {
      if (controller.signal.aborted || !mounted.current || currentSequence !== sequence.current) return;
      setReadError(error instanceof Error ? error.message : "无法连接本地服务，请检查服务后重新读取。");
    } finally {
      if (mounted.current && currentSequence === sequence.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    try {
      const raw = sessionStorage.getItem(PENDING_KEY);
      if (raw) {
        const saved = JSON.parse(raw) as Request;
        if (typeof saved.requestId !== "string" || !saved.command || typeof saved.command.type !== "string") throw new Error("invalid pending request");
        pending.current = saved;
        setCommandError({code:"PENDING_RESTORED", message:"有一项请求尚未确认结果。请用原编号核实后再提交其他操作。", retry:saved});
      }
    } catch {
      setCommandError({code:"REQUEST_STORAGE", message:"浏览器无法保存或恢复请求编号。请检查会话存储后重新打开本地运行页面。"});
    }
    void refresh();
    const interval = window.setInterval(() => {
      if (document.visibilityState === "visible") void refresh();
    }, 2000);
    const visible = () => { if (document.visibilityState === "visible") void refresh(); };
    document.addEventListener("visibilitychange", visible);
    return () => {
      mounted.current = false;
      ++sequence.current;
      readController.current?.abort();
      writeController.current?.abort();
      window.clearInterval(interval);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [refresh]);

  const run = useCallback(async (request: Request) => {
    if (writing.current || !mounted.current) return false;
    if (pending.current && pending.current.requestId !== request.requestId) return false;
    try { sessionStorage.setItem(PENDING_KEY, JSON.stringify(request)); }
    catch {
      setCommandError({code:"REQUEST_STORAGE", message:"当前浏览器无法保存请求编号，本次没有提交。请恢复会话存储后重试。"});
      return false;
    }
    const confirmed = () => { pending.current = null; try { sessionStorage.removeItem(PENDING_KEY); } catch {} };
    pending.current = request;
    writing.current = true;
    readController.current?.abort();
    const currentSequence = ++sequence.current;
    const controller = new AbortController();
    writeController.current = controller;
    setBusy(true);
    setRefreshing(false);
    setCommandError(null);
    let conflict = false;
    try {
      const response = await fetch(API, {
        method: "POST", credentials: "same-origin", signal: controller.signal,
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(request),
      });
      const body = await response.json();
      if (!mounted.current || currentSequence !== sequence.current) return false;
      if (!response.ok) {
        const message = typeof body.error?.message === "string" ? body.error.message : "本次请求未能完成。";
        const code = typeof body.error?.code === "string" ? body.error.code : `HTTP_${response.status}`;
        conflict = response.status === 409;
        const rejected = response.status >= 400 && response.status < 500;
        if (rejected) confirmed();
        setCommandError({ code, message: conflict ? `${message} 正在读取最新状态，请按新状态重新操作。` : message, retry: rejected ? undefined : request });
        return false;
      }
      const result = body as RuntimeCommandResult;
      if (!isSnapshot(result.snapshot)) throw new Error("服务响应不完整，尚不能确认本次请求的结果。");
      setSnapshot(result.snapshot);
      setReadError("");
      confirmed();
      notify(result.message || (result.duplicate ? "重复事件已识别，未创建新的执行。" : "变化已保存到本机数据库。"));
      return true;
    } catch (error) {
      if (controller.signal.aborted || !mounted.current || currentSequence !== sequence.current) return false;
      setCommandError({
        code: "REQUEST_UNCONFIRMED",
        message: error instanceof Error ? error.message : "连接中断，暂时无法确认本次请求的结果。",
        retry: request,
      });
      return false;
    } finally {
      writing.current = false;
      if (mounted.current && currentSequence === sequence.current) setBusy(false);
      if (conflict) void refresh();
    }
  }, [notify, refresh]);

  const submit = useCallback((command: RuntimeCommand) => {
    if (writing.current || pending.current) return Promise.resolve(false);
    return run({ requestId: crypto.randomUUID(), command });
  }, [run]);

  return { snapshot, loading, refreshing, readError, commandError, busy, refresh, submit,
    retry: () => commandError?.retry ? run(commandError.retry) : Promise.resolve(false),
    dismissError: () => { if (!pending.current) setCommandError(null); },
    blocked: busy || Boolean(commandError?.retry) || commandError?.code === "REQUEST_STORAGE" || Boolean(readError),
  };
}

function ActionCard({ action, disabled, verify }: {
  action: RuntimeAction; disabled: boolean; verify: (component: RuntimeComponent) => void;
}) {
  return <div className="rounded-xl border border-gray-200 dark:border-gray-800">
    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-gray-100 px-4 py-3 dark:border-gray-800">
      <div><span className="text-sm font-medium text-gray-800 dark:text-gray-200">执行意图</span><span className="ml-2 text-xs text-gray-400">{time(action.createdAt)}</span></div>
      <Pill tone={action.status === "unknown" ? "warning" : action.status === "accepted" ? "success" : "neutral"}>
        {action.status === "accepted" ? "组件已被接收" : action.status === "unknown" ? "有结果待核验" : action.status === "processing" ? "执行中" : action.status === "cancelled" ? "已取消" : "已准备"}
      </Pill>
    </div>
    <div className="divide-y divide-gray-100 dark:divide-gray-800">
      {[...action.components].sort((a, b) => a.ordinal - b.ordinal).map((component) => <div key={component.id} className="px-4 py-3">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <span className="flex items-center gap-2 text-sm font-medium text-gray-700 dark:text-gray-200"><Icon name={component.kind === "text" ? "chat" : "box"} className="size-4 text-gray-400" />{component.kind === "text" ? "文字消息" : "商品卡"}</span>
          <Pill tone={component.status === "unknown" ? "warning" : component.status === "accepted" ? "success" : "neutral"}>{componentLabels[component.status]}</Pill>
        </div>
        {component.kind === "product_card" ? <ProductCardContent content={component.content}/> : <p className="whitespace-pre-wrap break-words text-sm leading-6 text-gray-600 dark:text-gray-400">{component.content}</p>}
        <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
          <p className="text-xs text-gray-400">提交 {component.attempts} 次{component.submittedAt ? ` · ${time(component.submittedAt)}` : ""}</p>
          {component.status === "unknown" && <Button size="sm" variant="outline" disabled={disabled} onClick={() => verify(component)}><Icon name="eye" className="size-4" />核验原结果</Button>}
        </div>
        {component.status === "unknown" && <p className="mt-2 text-xs leading-5 text-warning-600 dark:text-warning-400">先查原意图的回执。核验不会增加提交次数。</p>}
        {component.receiptRef && <details className="mt-2 text-xs text-gray-400"><summary className="cursor-pointer py-1">查看模拟平台回执</summary><p className="mt-1 break-all font-mono">{component.receiptRef}</p></details>}
      </div>)}
    </div>
    {action.adoptedAt && <p className="border-t border-gray-100 px-4 py-3 text-xs text-success-600 dark:border-gray-800">达人确认采用 · {time(action.adoptedAt, true)} · 保留本次方案的独立采用记录</p>}
    <details className="border-t border-gray-100 px-4 py-2 text-xs text-gray-400 dark:border-gray-800"><summary className="cursor-pointer py-1">执行依据与标识</summary><p className="mt-1 break-all font-mono leading-6">{action.id}</p><p className="pb-1 leading-6">控制版本 {action.controlRevision} · 消息版本 {action.inboxRevision} · 商品条件 v{action.offerVersion} · 策略 v{action.policyRevision}</p><p className="break-all pb-1">上下文：{action.contextId}</p></details>
  </div>;
}

function ProductCardContent({content}: {content:string}) {
  try {
    const card = JSON.parse(content) as {title:string;pid:string;offerVersion:number;priceMinor:number;currency:string;commissionBps:number};
    if (!card.title || !Number.isFinite(card.priceMinor) || !Number.isFinite(card.commissionBps)) throw new Error("invalid card");
    return <div className="rounded-lg bg-gray-50 p-3 dark:bg-gray-800/60"><p className="text-sm font-medium text-gray-800 dark:text-gray-200">{card.title}</p><p className="mt-1 text-xs leading-6 text-gray-500">{new Intl.NumberFormat("zh-CN",{style:"currency",currency:card.currency}).format(card.priceMinor/100)} · 佣金 {card.commissionBps/100}% · 条件 v{card.offerVersion}</p><details className="mt-1 text-xs text-gray-400"><summary className="cursor-pointer">查看冻结的商品参数</summary><p className="mt-2 break-all font-mono leading-5">{content}</p></details></div>;
  } catch { return <p className="text-sm text-warning-600">商品卡内容无法展示，请查看运行记录中的原意图。</p>; }
}

export default function RuntimeWorkspace() {
  const { state, notify, go } = useDemo();
  const runtime = useLocalRuntime(notify);
  const { snapshot, blocked } = runtime;
  const [market, setMarket] = useState<"all" | RuntimeMarket>("all");
  const [selectedId, setSelectedId] = useState("rt-sofia-mx");
  const [tab, setTab] = useState<"progress" | "execution" | "audit">("progress");
  const [scenario, setScenario] = useState<RuntimeEventKind>("request_card");
  const [text, setText] = useState("");
  const [mode, setMode] = useState<"live" | "history">("live");
  const [lastIngest, setLastIngest] = useState<Record<string, Extract<RuntimeCommand, { type: "ingest" }>>>({});

  useEffect(() => {
    const selected = state.marketFilter;
    setMarket(selected === "mx" || selected === "br" || selected === "it" ? selected : "all");
  }, [state.marketFilter]);

  const relationships = snapshot?.relationships.filter((item) => market === "all" || item.market === market) || [];
  const current = relationships.find((item) => item.id === selectedId) || relationships[0];
  const currentMarket = current?.market;
  const currentId = current?.id;
  useEffect(() => {
    setText(currentMarket ? LOCAL_SCENARIOS[currentMarket][scenario].text : "");
  }, [currentId, currentMarket, scenario]);

  const actions = current ? [...current.actions].sort((a, b) => b.createdAt - a.createdAt) : [];
  const latestAction = actions[0];
  const unresolved = actions.filter((action) => action.components.some((component) => component.status === "unknown"));
  const allComponents = actions.flatMap((action) => action.components);
  const lastMessage = current?.messages.slice().sort((a, b) => b.observedAt - a.observedAt)[0];
  const previousIngest: Extract<RuntimeCommand, { type: "ingest" }> | undefined = current
    ? lastIngest[current.id] || (lastMessage ? {
      type: "ingest", relationshipId: current.id, sourceMessageId: lastMessage.sourceMessageId,
      kind: lastMessage.kind, text: lastMessage.text, mode: lastMessage.mode,
      ...(lastMessage.kind === "ask_later" ? { deferSeconds: 20 } : {}),
    } : undefined) : undefined;
  const activeJobs = current?.jobs.filter((job) => job.status === "ready" || job.status === "leased" || job.status === "blocked") || [];

  function ingest(event: FormEvent) {
    event.preventDefault();
    if (!current || blocked || !text.trim()) return;
    const command: Extract<RuntimeCommand, { type: "ingest" }> = {
      type: "ingest", relationshipId: current.id, sourceMessageId: `local-ui-${crypto.randomUUID()}`,
      kind: scenario, text: text.trim(), mode, ...(scenario === "ask_later" ? { deferSeconds: 20 } : {}),
    };
    setLastIngest((previous) => ({ ...previous, [current.id]: command }));
    void runtime.submit(command);
  }

  function verify(action: RuntimeAction, component: RuntimeComponent) {
    if (current) void runtime.submit({ type: "verify", relationshipId: current.id, actionId: action.id, componentId: component.id });
  }

  return <div className="min-w-0">
    <PageHeading title="本地合作工作台" description="关系、消息与任务保存在本机，服务重启后继续从原记录处理。" action={<Button variant="outline" onClick={() => go("workspace")}><Icon name="arrow" className="size-4 rotate-180" />回到界面演示</Button>} />

    <div className="mb-5 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-gray-200 bg-white px-4 py-3 dark:border-gray-800 dark:bg-white/[0.025]">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-xs">
        <span className="flex items-center gap-2 font-medium text-gray-700 dark:text-gray-200"><span className={`size-2 rounded-full ${snapshot && !runtime.readError ? "bg-success-500" : "bg-warning-500"}`} />{snapshot ? "本机数据库 · 已保存" : "正在连接本机数据库"}</span>
        <span className="flex items-center gap-2 text-gray-500"><span className={`size-2 rounded-full ${snapshot?.worker.online && !runtime.readError ? "bg-success-500" : "bg-gray-300 dark:bg-gray-600"}`} />{runtime.readError ? "Worker 状态待确认" : snapshot?.worker.online ? "Worker 在线" : "Worker 离线"}</span>
        {snapshot && <span className="text-gray-400">{runtime.readError ? "最后读取" : "同步于"} {time(snapshot.at)}</span>}
      </div>
      <Pill tone="neutral">本地模拟平台 · 确定性判断</Pill>
    </div>

    {runtime.readError && <div role="alert" className="mb-4"><Notice tone="warning"><p>{runtime.readError}</p><p className="mt-1 text-xs">{snapshot ? "当前保留最后读取的记录。重新连接后才能继续操作。" : "尚未读取到业务状态，不会用页面示例替代数据库。"}</p><Button className="mt-3" size="sm" variant="outline" disabled={runtime.refreshing || runtime.busy} onClick={() => void runtime.refresh()}>{runtime.refreshing ? "正在读取…" : "重新读取"}</Button></Notice></div>}

    {runtime.commandError && <div role="alert" className="mb-4"><Notice tone="warning"><p>{runtime.commandError.message}</p>{runtime.commandError.retry ? <><p className="mt-1 text-xs">本次请求可能已经保存。先使用同一请求编号核实并重试，期间暂停提交其他命令。</p><div className="mt-3 flex flex-wrap items-center gap-3"><Button size="sm" variant="outline" disabled={runtime.busy} onClick={() => void runtime.retry()}>{runtime.busy ? "正在核实…" : "用同一请求编号重试"}</Button><span className="break-all font-mono text-[11px]">{shortId(runtime.commandError.retry.requestId)}</span></div></> : <Button size="sm" variant="ghost" className="mt-2" onClick={runtime.dismissError}>知道了</Button>}<details className="mt-2 text-xs"><summary className="cursor-pointer py-1">请求详情</summary><p>{runtime.commandError.code}</p></details></Notice></div>}

    {snapshot && !snapshot.worker.online && !runtime.readError && <div className="mb-4"><Notice tone="warning"><p>Worker 当前未在线。消息仍可保存，待处理任务会在 Worker 启动后继续。</p><details className="mt-2 text-xs"><summary className="cursor-pointer py-1 font-medium">查看启动方法</summary><p className="mt-2">在新项目的 <code className="break-all">apps/web</code> 目录运行：</p><pre className="my-2 overflow-x-auto rounded-lg bg-white/60 p-3 dark:bg-gray-900"><code>npm run runtime:worker</code></pre><p>保留终端运行；重启后继续处理数据库中的任务。最近心跳：{time(snapshot.worker.lastSeenAt, true)}。</p></details></Notice></div>}

    {runtime.loading && !snapshot ? <div role="status" className="flex min-h-96 items-center justify-center rounded-2xl border border-gray-200 bg-white dark:border-gray-800 dark:bg-white/[0.025]"><div className="text-center"><span className="mx-auto mb-4 block size-7 animate-spin rounded-full border-2 border-gray-200 border-t-brand-500 motion-reduce:animate-none" /><p className="text-sm text-gray-500">正在读取本机的关系与任务…</p></div></div> : snapshot && <div className="grid min-w-0 items-start gap-5 lg:grid-cols-[225px_minmax(0,1fr)] 2xl:grid-cols-[260px_minmax(0,1fr)]">
      <aside className="min-w-0 rounded-2xl border border-gray-200 bg-white dark:border-gray-800 dark:bg-white/[0.025]" aria-label="本地达人关系">
        <div className="border-b border-gray-100 p-4 dark:border-gray-800"><div className="mb-3 flex items-center justify-between"><h2 className="text-sm font-semibold text-gray-800 dark:text-gray-200">持续经营的关系</h2><span className="text-xs text-gray-400">{relationships.length}</span></div><Select aria-label="筛选本地市场" value={market} onChange={(event) => setMarket(event.target.value as "all" | RuntimeMarket)}><option value="all">全部三个市场</option><option value="mx">墨西哥 · MX</option><option value="br">巴西 · BR</option><option value="it">意大利 · IT</option></Select></div>
        <div className="grid gap-1 p-2 sm:grid-cols-3 lg:grid-cols-1">{relationships.map((relation) => <button key={relation.id} type="button" aria-pressed={current?.id === relation.id} onClick={() => { setSelectedId(relation.id); setTab("progress"); }} className={`min-w-0 rounded-xl p-3 text-left transition ${current?.id === relation.id ? "bg-brand-50 dark:bg-brand-500/10" : "hover:bg-gray-50 dark:hover:bg-white/5"}`}>
          <div className="flex items-center gap-3"><Avatar src={relation.avatar} name={relation.name} size={36} /><div className="min-w-0 flex-1"><p className="truncate text-sm font-semibold text-gray-800 dark:text-gray-200">{relation.name}</p><p className="mt-0.5 text-[11px] text-gray-400">{marketNames[relation.market]} · {relation.market.toUpperCase()}</p></div></div>
          <div className="mt-3"><Pill tone={statusTone(relation.status)}>{statusLabels[relation.status]}</Pill></div><p className="mt-2 line-clamp-2 text-xs leading-5 text-gray-500">{relation.nextStep}</p>
        </button>)}</div>
        <div className="border-t border-gray-100 p-4 dark:border-gray-800"><p className="text-xs leading-5 text-gray-400">三位示例达人使用独立关系记录。接管、暂停和拒联状态均由数据库保留。</p></div>
      </aside>

      {current ? <section className="min-w-0 overflow-hidden rounded-2xl border border-gray-200 bg-white dark:border-gray-800 dark:bg-white/[0.025]" aria-label={`${current.name} 的本地合作`}>
        <div className="border-b border-gray-100 p-5 dark:border-gray-800">
          <div className="flex flex-wrap items-start justify-between gap-4"><div className="flex min-w-0 items-center gap-3"><Avatar src={current.avatar} name={current.name} size={44} /><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h2 className="text-lg font-semibold text-gray-800 dark:text-gray-200">{current.name}</h2><MarketPill market={current.market} /></div><p className="mt-1 break-all text-xs text-gray-400">{current.handle} · {current.control === "auto" ? "关系 Agent 负责" : current.control === "human" ? "由运营负责" : "已暂停自动处理"}</p></div></div>
            <div className="flex flex-wrap gap-2">{current.control !== "human" && <Button variant="outline" size="sm" disabled={blocked} onClick={() => void runtime.submit({ type: "control", relationshipId: current.id, expectedRevision: current.revision, mode: "human" })}><Icon name="users" className="size-4" />人工接管</Button>}{current.control !== "auto" && <Button size="sm" disabled={blocked} onClick={() => void runtime.submit({ type: "control", relationshipId: current.id, expectedRevision: current.revision, mode: "auto" })}>交还 Agent</Button>}{current.control !== "paused" && <Button variant="ghost" size="sm" disabled={blocked} onClick={() => void runtime.submit({ type: "control", relationshipId: current.id, expectedRevision: current.revision, mode: "paused" })}>暂停</Button>}</div>
          </div>
          <div className="mt-5 flex items-start gap-3 rounded-xl bg-gray-50 p-4 dark:bg-gray-900/60"><span className={`mt-0.5 rounded-lg p-2 ${current.status === "waiting_verification" || current.status === "needs_facts" ? "bg-warning-50 text-warning-600 dark:bg-warning-500/10" : "bg-brand-50 text-brand-500 dark:bg-brand-500/10"}`}><Icon name={current.status === "waiting_verification" ? "eye" : current.status === "adopted" ? "check" : "bolt"} className="size-5" /></span><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h3 className="text-sm font-semibold text-gray-800 dark:text-gray-200">{statusLabels[current.status]}</h3>{current.marketingStopped && <Pill tone="warning">已停止营销联系</Pill>}</div><p className="mt-1 text-sm leading-6 text-gray-500 dark:text-gray-400">{current.nextStep}</p>{current.marketingStopped && <p className="mt-1 text-xs leading-5 text-gray-400">交还 Agent 不会撤销拒联。达人主动提出的新服务请求单独判断。</p>}</div></div>
          <div className="mt-4 flex flex-wrap gap-x-6 gap-y-2 text-xs text-gray-400"><span><b className="mr-1.5 font-medium text-gray-700 dark:text-gray-300">{current.messages.length}</b>原消息</span><span><b className="mr-1.5 font-medium text-gray-700 dark:text-gray-300">{actions.length}</b>执行意图</span><span><b className="mr-1.5 font-medium text-gray-700 dark:text-gray-300">{allComponents.reduce((sum, item) => sum + item.attempts, 0)}</b>组件提交</span><span><b className="mr-1.5 font-medium text-gray-700 dark:text-gray-300">{activeJobs.length}</b>待处理任务</span></div>
        </div>

        <div className="border-b border-gray-100 px-5 py-3 dark:border-gray-800"><Tabs value={tab} onChange={setTab} items={[{ value: "progress", label: "关系进展" }, { value: "execution", label: "执行与证据", count: unresolved.length || undefined }, { value: "audit", label: "运行记录" }]} /></div>

        <div className="p-5">
          {tab === "progress" && <div className="space-y-5">
            {unresolved.map((action) => <ActionCard key={action.id} action={action} disabled={blocked} verify={(component) => verify(action, component)} />)}

            <div><div className="mb-3 flex items-center justify-between gap-3"><h3 className="text-sm font-semibold text-gray-800 dark:text-gray-200">收到的消息</h3><span className="text-xs text-gray-400">模拟 TikTok IM 来源</span></div>
              {current.messages.length ? <div className="max-h-64 space-y-3 overflow-y-auto overscroll-contain pr-1">{[...current.messages].sort((a, b) => b.observedAt - a.observedAt).map((message) => <div key={message.id} className="rounded-xl border border-gray-100 bg-gray-50/60 px-4 py-3 dark:border-gray-800 dark:bg-gray-900/40"><div className="flex flex-wrap items-center justify-between gap-2"><span className="text-xs font-medium text-gray-500">{current.name} · {LOCAL_SCENARIOS[current.market][message.kind].label}</span><div className="flex items-center gap-2">{message.mode === "history" && <Pill tone="neutral">历史存档</Pill>}<span className="text-[11px] text-gray-400">{time(message.occurredAt, true)}</span></div></div><p className="mt-2 whitespace-pre-wrap break-words text-sm leading-6 text-gray-800 dark:text-gray-200" lang={current.market === "mx" ? "es-MX" : current.market === "br" ? "pt-BR" : "it"}>{message.text}</p><details className="mt-2 text-[11px] text-gray-400"><summary className="cursor-pointer py-1">消息来源</summary><p className="break-all font-mono leading-5">{message.sourceMessageId}</p><p className="leading-5">本机观察时间 {time(message.observedAt, true)} · {message.mode === "history" ? "仅存档" : "实时入站"}</p></details></div>)}</div> : <div className="rounded-xl border border-dashed border-gray-200 px-5 py-6 text-sm leading-6 text-gray-400 dark:border-gray-700">还没有消息。从下方模拟一条达人回复，观察消息保存、判断与执行。</div>}
            </div>

            {latestAction && !unresolved.some((action) => action.id === latestAction.id) && <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-gray-200 px-4 py-3 dark:border-gray-800"><div><p className="text-sm font-medium text-gray-700 dark:text-gray-200">最近执行 · {latestAction.components.length} 个组件</p><p className="mt-1 text-xs leading-5 text-gray-400">{latestAction.status === "accepted" ? "模拟平台已接收，仍需达人明确确认是否采用。" : "查看每个组件的结果、提交次数和依据。"}</p></div><Button size="sm" variant="ghost" onClick={() => setTab("execution")}>查看执行<Icon name="arrow" className="size-4" /></Button></div>}

            <form onSubmit={ingest} className="rounded-xl border border-brand-100 bg-brand-50/30 p-4 dark:border-brand-900 dark:bg-brand-500/[0.03]">
              <div className="mb-4 flex flex-wrap items-center justify-between gap-2"><h3 className="flex items-center gap-2 text-sm font-semibold text-gray-800 dark:text-gray-200"><Icon name="chat" className="size-4 text-brand-500" />模拟新消息</h3><span className="text-[11px] text-gray-400">{current.market === "mx" ? "西班牙语" : current.market === "br" ? "葡萄牙语" : "意大利语"} · 文本可编辑</span></div>
              <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_170px]"><Field label="消息场景"><Select value={scenario} disabled={blocked} onChange={(event) => setScenario(event.target.value as RuntimeEventKind)}>{eventOrder.map((kind) => <option key={kind} value={kind}>{LOCAL_SCENARIOS[current.market][kind].label}</option>)}</Select></Field><Field label="处理方式"><Select value={mode} disabled={blocked} onChange={(event) => setMode(event.target.value as "live" | "history")}><option value="live">实时新消息</option><option value="history">历史消息存档</option></Select></Field></div>
              <div className="mt-3"><TextArea aria-label="模拟达人消息内容" value={text} disabled={blocked} maxLength={2000} onChange={(event) => setText(event.target.value)} className="!min-h-24 bg-white dark:bg-gray-900" /></div>
              <p className="mt-2 text-xs leading-5 text-gray-400">{mode === "history" ? "历史模式只保存原消息，不创建回复或发送任务。" : "本轮按所选场景做确定性判断，不调用模型解读编辑后的文字。"}{scenario === "ask_later" && mode === "live" ? " 到期任务将在 20 秒后重新检查条件。" : ""}</p>
              <div className="mt-4 flex flex-wrap items-center gap-2"><Button type="submit" size="sm" disabled={blocked || !text.trim()}><Icon name="plus" className="size-4" />{runtime.busy ? "正在保存…" : mode === "history" ? "存入历史消息" : "保存并交给 Worker"}</Button><Button variant="ghost" size="sm" disabled={blocked || !previousIngest} onClick={() => { if (previousIngest) void runtime.submit(previousIngest); }}>重复投递上一条</Button></div>
              {previousIngest && <p className="mt-2 text-[11px] leading-5 text-gray-400">重复投递会复用上一条消息的来源编号和原内容，用于检查去重。</p>}
            </form>

            <details className="rounded-xl border border-gray-200 px-4 py-3 dark:border-gray-800"><summary className="cursor-pointer py-1 text-xs font-medium text-gray-500">故障与恢复演示</summary><p className="mt-2 text-xs leading-5 text-gray-400">让商品条件过期，再刷新条件，观察事实检查与任务接续。原消息、意图与提交记录始终保留。</p>{current.id === "rt-pedro-br" && <p className="mt-2 text-xs leading-5 text-warning-600 dark:text-warning-400">Pedro 的首张商品卡会模拟“平台已接收但回执丢失”。先模拟索取商品卡，再核验原结果，检查提交次数没有增加。</p>}<div className="mt-3 flex flex-wrap gap-2"><Button variant="outline" size="sm" disabled={blocked || current.product.validUntil <= snapshot.at} onClick={() => void runtime.submit({ type: "expire_offer", relationshipId: current.id, expectedRevision: current.revision })}>让商品条件过期</Button><Button variant="outline" size="sm" disabled={blocked} onClick={() => void runtime.submit({ type: "refresh_offer", relationshipId: current.id, expectedRevision: current.revision })}>刷新商品条件</Button></div><p className="mt-3 text-xs leading-5 text-gray-400">重启 Worker 后，查看同一个执行意图与组件提交次数；待核验结果应继续核验原意图。</p></details>
          </div>}

          {tab === "execution" && <div className="space-y-5">
            <div className="flex items-start gap-3 rounded-xl bg-gray-50 p-4 dark:bg-gray-900/60"><img src={current.product.image} alt={current.product.title} width={52} height={52} className="size-13 shrink-0 rounded-lg border border-gray-100 bg-white object-cover dark:border-gray-700" /><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h3 className="text-sm font-medium text-gray-800 dark:text-gray-200">{current.product.title}</h3><Pill tone={current.product.validUntil > snapshot.at ? "success" : "warning"}>{current.product.validUntil > snapshot.at ? "条件有效" : "条件已过期"}</Pill></div><p className="mt-1 text-xs leading-5 text-gray-500">{new Intl.NumberFormat("zh-CN", { style: "currency", currency: current.product.currency }).format(current.product.priceMinor / 100)} · 佣金 {(current.product.commissionBps / 100).toFixed(1)}% · 条件 v{current.product.offerVersion}</p><p className="mt-1 text-[11px] text-gray-400">有效至 {time(current.product.validUntil, true)}</p></div></div>
            <div><h3 className="mb-3 text-sm font-semibold text-gray-800 dark:text-gray-200">执行意图与逐组件结果</h3><p className="mb-3 text-xs leading-5 text-gray-400">“已接收”仅指本地模拟平台回执。达人采用和经营收益需要各自证据。</p>{actions.length ? <div className="space-y-3">{actions.map((action) => <ActionCard key={action.id} action={action} disabled={blocked} verify={(component) => verify(action, component)} />)}</div> : <div className="rounded-xl border border-dashed border-gray-200 p-5 text-sm text-gray-400 dark:border-gray-700">暂无执行意图。历史存档、等待事实或人工接管不会自动产生发送。</div>}</div>
            <div><h3 className="mb-3 text-sm font-semibold text-gray-800 dark:text-gray-200">事实与证据</h3>{current.evidence.length ? <div className="divide-y divide-gray-100 rounded-xl border border-gray-200 dark:divide-gray-800 dark:border-gray-800">{current.evidence.map((evidence) => <div key={evidence.id} className="px-4 py-3"><div className="flex flex-wrap items-center justify-between gap-2"><span className="text-xs font-medium text-gray-500">{evidence.kind === "creator_statement" ? "达人原话" : evidence.kind === "offer" ? "商品条件" : "模拟平台回执"}</span><Pill tone={evidence.status === "valid" ? "success" : "warning"}>{evidence.status === "valid" ? "有效" : evidence.status === "stale" ? "已过期" : "缺少依据"}</Pill></div><p className="mt-2 break-words text-sm leading-6 text-gray-700 dark:text-gray-300">{evidence.summary}</p><details className="mt-2 text-[11px] text-gray-400"><summary className="cursor-pointer py-1">来源与时效</summary><p className="break-all font-mono leading-5">{evidence.sourceRef}</p><p className="leading-5">观察于 {time(evidence.observedAt, true)}{evidence.validUntil ? ` · 有效至 ${time(evidence.validUntil, true)}` : ""}</p></details></div>)}</div> : <p className="text-sm text-gray-400">尚无证据记录。</p>}</div>
          </div>}

          {tab === "audit" && <div className="space-y-5">
            <div><div className="mb-3 flex flex-wrap items-center justify-between gap-2"><h3 className="text-sm font-semibold text-gray-800 dark:text-gray-200">持久任务</h3><span className="text-xs text-gray-400">由 Worker 领取与恢复</span></div>{current.jobs.length ? <div className="space-y-2">{[...current.jobs].sort((a, b) => b.dueAt - a.dueAt).map((job) => <div key={job.id} className="rounded-xl border border-gray-200 px-4 py-3 dark:border-gray-800"><div className="flex flex-wrap items-center justify-between gap-2"><span className="text-sm font-medium text-gray-700 dark:text-gray-200">{jobNames[job.kind]}</span><Pill tone={job.status === "blocked" ? "warning" : job.status === "leased" ? "brand" : "neutral"}>{jobStatuses[job.status]}</Pill></div>{job.note && <p className="mt-2 text-xs leading-5 text-gray-500">{job.note}</p>}<p className="mt-2 text-[11px] leading-5 text-gray-400">计划时间 {time(job.dueAt, true)} · 已领取 {job.attempts} 次</p><details className="mt-1 text-[11px] text-gray-400"><summary className="cursor-pointer py-1">恢复信息</summary><p className="break-all font-mono leading-5">{job.id}</p><p className="leading-5">租约有效至 {time(job.leaseUntil, true)} · 执行代次 {job.fence}</p><p className="leading-5">任务领取次数与平台组件提交次数分别记录。</p></details></div>)}</div> : <p className="rounded-xl bg-gray-50 p-4 text-sm text-gray-400 dark:bg-gray-900/60">暂无任务，等待实时新消息。</p>}</div>

            <details className="rounded-xl border border-gray-200 px-4 py-3 dark:border-gray-800"><summary className="cursor-pointer py-1 text-sm font-medium text-gray-700 dark:text-gray-200">本次判断使用的有限上下文</summary>{current.context ? <div className="mt-3 space-y-3"><p className="text-xs leading-6 text-gray-500">{current.context.summary}</p><div className="grid grid-cols-2 gap-2 text-xs text-gray-400"><span>消息 {current.context.messageIds.length} 条</span><span>证据 {current.context.evidenceRefs.length} 项</span><span>上下文 {current.context.characterCount} 字符</span><span>未装入 {current.context.omittedMessages} 条消息</span></div><p className="text-xs leading-5 text-gray-400">能力：{current.context.skillId} · v{current.context.skillVersion}<br />控制 {current.context.controlRevision} · 消息 {current.context.inboxRevision} · 规则 {current.context.policyRevision} · 商品条件 {current.context.offerVersion}</p><p className="text-xs leading-5 text-gray-400">生成于 {time(current.context.createdAt, true)}。当前由确定性规则执行，未发送给真实模型。</p><div className="rounded-lg bg-gray-50 p-3 dark:bg-gray-900/60"><p className="mb-1 text-xs font-medium text-gray-500">已引用来源</p>{[...current.context.messageIds, ...current.context.evidenceRefs].map((reference) => <p key={reference} className="break-all font-mono text-[11px] leading-5 text-gray-400">{reference}</p>)}</div></div> : <p className="mt-3 text-xs leading-5 text-gray-400">尚未执行关系判断，没有生成上下文快照。</p>}</details>

            <div><h3 className="mb-3 text-sm font-semibold text-gray-800 dark:text-gray-200">运行记录</h3>{current.audit.length ? <ol className="space-y-4">{[...current.audit].sort((a, b) => b.at - a.at).map((entry) => <li key={entry.id} className="flex gap-3"><span className="mt-1.5 size-2 shrink-0 rounded-full bg-gray-300 dark:bg-gray-600" /><div className="min-w-0"><div className="flex flex-wrap items-baseline gap-x-3 gap-y-1"><h4 className="text-sm font-medium text-gray-700 dark:text-gray-200">{entry.title}</h4><span className="text-[11px] text-gray-400">{time(entry.at, true)}</span></div><p className="mt-1 break-words text-xs leading-6 text-gray-500">{entry.detail}</p></div></li>)}</ol> : <p className="text-sm text-gray-400">尚无运行记录。</p>}</div>
          </div>}
        </div>

        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-gray-100 px-5 py-3 text-[11px] text-gray-400 dark:border-gray-800"><span>本地持久化 · 控制版本 {current.revision} · 消息版本 {current.inboxRevision}</span><span>{runtime.busy ? "正在保存操作…" : lastMessage ? `最近入站 ${time(lastMessage.observedAt)}` : "等待首次入站"}</span></div>
      </section> : <div className="rounded-2xl border border-gray-200 bg-white dark:border-gray-800 dark:bg-white/[0.025]"><EmptyState title="该市场暂无本地关系" description="选择其他市场查看已建立的示例关系。" /></div>}
    </div>}
  </div>;
}
