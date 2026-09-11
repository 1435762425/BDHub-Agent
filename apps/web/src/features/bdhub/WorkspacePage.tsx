"use client";

import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useDemo } from "./store";
import {
  MARKETS, STATUS, creatorFor, productFor, relationFor, money,
  type CaseStatus, type CooperationCase, type Market, type Relation, type Scenario,
} from "./model";
import {
  Avatar, Button, Dialog, EmptyState, Field, Icon, Input, MarketPill,
  Notice, PageHeading, Pill, Select, StatusPill, TextArea, type IconName,
} from "./ui";

type CaseFilter = "all" | CaseStatus | "unknown";
type WorkspaceDialog = { kind: "note" | "decision"; caseId: string } | null;
type DecisionChoice = "keep" | "pause" | "manual";

const controlLabels: Record<Relation["control"], string> = {
  ai: "Agent 负责", human: "人工接管中", paused: "自动外联已暂停",
};

const scenarios: { value: Scenario; title: string; description: string; icon: IconName; group: "creator" | "platform" }[] = [
  { value: "received_item_request", title: "已有实物，索取商品卡", description: "更新实物状态，取消不适用的寄样引导。", icon: "box", group: "creator" },
  { value: "sample_requested", title: "已申请样品，询问进度", description: "将下一步转为核对申请与等待业务变化。", icon: "file", group: "creator" },
  { value: "ask_later", title: "约定下周再联系", description: "记录等待安排，使原来的待发草稿失效。", icon: "time", group: "creator" },
  { value: "opt_out", title: "明确停止营销联系", description: "暂停这位达人的自动外联，结束相关营销事项。", icon: "lock", group: "creator" },
  { value: "adopted", title: "确认已采用合作方案", description: "记录方案采用结果；收益仍需独立证据。", icon: "check", group: "creator" },
  { value: "send_unknown", title: "商品卡提交结果未知", description: "模拟传输超时，保留原意图并进入核验。", icon: "info", group: "platform" },
  { value: "verify_accepted", title: "核验原意图已被接收", description: "补入平台核验证据，不重新发送任何消息。", icon: "eye", group: "platform" },
];

function Drawer({ open, title, description, onClose, children }: {
  open: boolean; title: string; description?: string; onClose: () => void; children: ReactNode;
}) {
  const headingId = useId();
  const panelRef = useRef<HTMLElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    const previousOverflow = document.body.style.overflow;
    const shell = document.getElementById("bdhub-shell");
    const hadInert = shell?.hasAttribute("inert");
    document.body.style.overflow = "hidden";
    shell?.setAttribute("inert", "");
    const timer = window.setTimeout(() => panelRef.current?.querySelector<HTMLElement>("button,select,input,textarea")?.focus(), 20);
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); closeRef.current(); }
      if (event.key !== "Tab") return;
      const focusable = Array.from(panelRef.current?.querySelectorAll<HTMLElement>("button:not([disabled]),select:not([disabled]),input:not([disabled]),textarea:not([disabled]),a[href],[tabindex='0']") || []).filter((element) => element.offsetParent !== null);
      const first = focusable[0], last = focusable[focusable.length - 1];
      if (!first) { event.preventDefault(); return; }
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      window.clearTimeout(timer);
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previousOverflow;
      if (!hadInert) shell?.removeAttribute("inert");
      previous?.focus();
    };
  }, [open]);

  if (!open) return null;
  return createPortal(
    <div className="fixed inset-0 z-[99999] flex justify-end bg-gray-900/40 backdrop-blur-[2px]" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <aside ref={panelRef} role="dialog" aria-modal="true" aria-labelledby={headingId} className="flex h-dvh w-full max-w-md flex-col bg-white shadow-theme-xl dark:bg-gray-900">
        <div className="flex shrink-0 items-start justify-between gap-4 border-b border-gray-200 px-6 py-5 dark:border-gray-800">
          <div><h2 id={headingId} className="text-lg font-semibold text-gray-800 dark:text-white/90">{title}</h2>{description && <p className="mt-1.5 text-sm leading-6 text-gray-500">{description}</p>}</div>
          <Button variant="ghost" size="sm" className="!p-2" aria-label="关闭抽屉" onClick={onClose}><Icon name="close" /></Button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">{children}</div>
      </aside>
    </div>, document.body,
  );
}

export default function WorkspacePage({ initialCaseId }: { initialCaseId?: string }) {
  const { state, dispatch, notify, go, openCase, hydrated } = useDemo();
  const [selectedId, setSelectedId] = useState(initialCaseId || "");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<CaseFilter>("all");
  const [mobileDetail, setMobileDetail] = useState(Boolean(initialCaseId));
  const [showTranslation, setShowTranslation] = useState(true);
  const [draft, setDraft] = useState("");
  const [dialog, setDialog] = useState<WorkspaceDialog>(null);
  const [note, setNote] = useState("");
  const [decisionChoice, setDecisionChoice] = useState<DecisionChoice>("keep");
  const [formError, setFormError] = useState("");
  const [drawer, setDrawer] = useState<"events" | "details" | null>(null);
  const [eventCaseId, setEventCaseId] = useState("");
  const [selectedEvent, setSelectedEvent] = useState<Scenario>("received_item_request");
  const handledRoute = useRef<string | undefined>(undefined);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!hydrated || !initialCaseId || handledRoute.current === initialCaseId) return;
    const item = state.cases.find((entry) => entry.id === initialCaseId);
    if (!item) return;
    handledRoute.current = initialCaseId;
    setSelectedId(item.id);
    setFilter("all");
    setQuery("");
    setMobileDetail(true);
    const creator = creatorFor(state, item.creatorId);
    if (state.marketFilter !== "all" && creator.market !== state.marketFilter) dispatch({ type: "market", market: creator.market });
  }, [hydrated, initialCaseId, state, dispatch]);

  const marketCases = useMemo(() => state.cases.filter((item) => {
    const creator = creatorFor(state, item.creatorId);
    return creator && (state.marketFilter === "all" || creator.market === state.marketFilter);
  }), [state]);

  const filteredCases = useMemo(() => {
    const term = query.trim().toLocaleLowerCase();
    return marketCases.filter((item) => {
      if (filter === "unknown" ? item.sendState !== "unknown" : filter !== "all" && item.status !== filter) return false;
      if (!term) return true;
      const creator = creatorFor(state, item.creatorId), product = productFor(state, item.productId);
      return [creator.name, creator.handle, product.name, item.title, item.nextStep, item.id].join(" ").toLocaleLowerCase().includes(term);
    });
  }, [marketCases, filter, query, state]);

  const currentCase = filteredCases.find((item) => item.id === selectedId) || filteredCases[0];
  const creator = currentCase ? creatorFor(state, currentCase.creatorId) : undefined;
  const product = currentCase ? productFor(state, currentCase.productId) : undefined;
  const relation = creator ? relationFor(state, creator.id) : undefined;
  const relatedCases = creator ? state.cases.filter((item) => item.creatorId === creator.id) : [];
  const goal = currentCase ? state.goals.find((item) => item.id === currentCase.goalId) : undefined;
  const unknown = currentCase?.sendState === "unknown";
  const commercialDecision = Boolean(currentCase?.status === "needs_operator" && currentCase.nextStep !== "由运营继续处理" && (currentCase.reason.includes("商业条件") || currentCase.title.includes("佣金")));
  const goalAllowsAdvance = Boolean(goal && creator && goal.markets.includes(creator.market) && (goal.status === "active" || (goal.status === "paused" && goal.pauseScope === "new_acquisition" && currentCase?.serviceRequested)));
  const canAdvance = Boolean(currentCase && relation?.control === "ai" && currentCase.status === "processing" && currentCase.draftValid && !currentCase.delivered && !unknown && product?.ready && goalAllowsAdvance && (!relation.marketingStopped || currentCase.serviceRequested));
  const canSend = Boolean(currentCase && relation?.control === "human" && !unknown && currentCase.status !== "closed");
  const modalCase = dialog ? state.cases.find((item) => item.id === dialog.caseId) : undefined;
  const modalCreator = modalCase ? creatorFor(state, modalCase.creatorId) : undefined;
  const modalProduct = modalCase ? productFor(state, modalCase.productId) : undefined;
  const eventCase = state.cases.find((item) => item.id === eventCaseId);
  const eventCreator = eventCase ? creatorFor(state, eventCase.creatorId) : undefined;
  const needsDecisionCount = marketCases.filter((item) => item.status === "needs_operator").length;
  const unknownCount = marketCases.filter((item) => item.sendState === "unknown").length;

  useEffect(() => { setDraft(""); }, [currentCase?.id]);
  useEffect(() => {
    if (!scrollRef.current) return;
    scrollRef.current.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "auto" });
  }, [currentCase?.id, currentCase?.messages.length, showTranslation]);

  function selectCase(id: string) {
    setSelectedId(id);
    setMobileDetail(true);
    setDraft("");
    openCase(id);
  }

  function selectRelatedCase(id: string) {
    setFilter("all"); setQuery(""); setDrawer(null); selectCase(id);
  }

  function changeControl(mode: Relation["control"]) {
    if (!creator || !relation || relation.control === mode) return;
    dispatch({ type: "control", creatorId: creator.id, mode });
    setDraft("");
    notify(`${creator.name} 的 ${relatedCases.length} 项合作已${mode === "human" ? "统一交由运营处理" : mode === "ai" ? "交还 Agent 重新判断" : "暂停自动外联"}。`);
  }

  function openDialog(kind: "note" | "decision") {
    if (!currentCase) return;
    setNote(""); setFormError(""); setDecisionChoice("keep");
    setDialog({ kind, caseId: currentCase.id });
  }

  function submitDialog() {
    if (!modalCase || !dialog) return;
    if (!note.trim()) { setFormError(dialog.kind === "note" ? "请填写需要补充的信息。" : "请简要记录本次决定的依据。"); return; }
    if (note.trim().length > 1000) { setFormError("请将内容控制在 1,000 字以内。"); return; }
    if (dialog.kind === "note") {
      dispatch({ type: "add_note", caseId: modalCase.id, text: note.trim() });
      notify("内部补充已记录，不会作为消息发给达人。");
    } else {
      if (modalCase.status !== "needs_operator") { setFormError("该事项状态已经变化，请关闭并查看最新进展。"); return; }
      dispatch({ type: "resolve_decision", caseId: modalCase.id, choice: decisionChoice, note: note.trim() });
      notify("决定已记录，相关合作将按最新条件接续。");
    }
    setDialog(null); setNote(""); setFormError("");
  }

  function sendMessage() {
    if (!currentCase || !canSend || !draft.trim() || draft.trim().length > 2000) return;
    dispatch({ type: "human_message", caseId: currentCase.id, text: draft.trim() });
    setDraft(""); notify("人工消息已记录。");
  }

  function openEvents(event?: Scenario) {
    setEventCaseId(currentCase?.id || state.cases[0]?.id || "");
    setSelectedEvent(event || "received_item_request"); setDrawer("events");
  }

  function scenarioBlocked(event: Scenario, item?: CooperationCase) {
    if (!item) return "先选择一项合作";
    if (event === "verify_accepted" && item.sendState !== "unknown") return "仅用于结果未知的发送意图";
    if (event === "send_unknown" && (item.sendState !== "not_sent" || item.status === "closed")) return "先选择一项尚未提交的合作";
    if (event === "adopted" && (!item.delivered || item.sendState !== "accepted" || item.adopted)) return item.adopted ? "该方案已经核实采用" : "先交付方案或核验原发送结果";
    if (event === "opt_out" && relationFor(state, item.creatorId).marketingStopped) return "已记录停止营销联系";
    return "";
  }

  function applyEvent() {
    if (!eventCase || scenarioBlocked(selectedEvent, eventCase)) return;
    dispatch({ type: "simulate_event", caseId: eventCase.id, event: selectedEvent });
    notify(`已应用演示事件：${scenarios.find((entry) => entry.value === selectedEvent)?.title}`);
  }

  function renderRelationDetails() {
    if (!currentCase || !creator || !product || !relation) return <EmptyState title="先选择一项合作" />;
    const opportunities = state.opportunities.filter((item) => item.creatorId === creator.id && item.productId === product.id);
    const stoppedMarketing = relation.marketingStopped;
    const canReturnForService = !stoppedMarketing || relatedCases.some((item) => item.serviceRequested && item.status !== "closed");
    return <div className="divide-y divide-gray-100 dark:divide-gray-800">
      <section className="p-5">
        <div className="mb-4 flex items-center justify-between"><h3 className="text-sm font-semibold text-gray-800 dark:text-gray-200">关系控制</h3><span className="text-xs text-gray-400">v{relation.revision}</span></div>
        <div className={`rounded-xl p-3.5 ${relation.control === "ai" ? "bg-brand-50 dark:bg-brand-500/10" : "bg-gray-50 dark:bg-gray-800"}`}>
          <div className="flex items-center gap-2 text-sm font-medium text-gray-800 dark:text-gray-200"><Icon name={relation.control === "ai" ? "agent" : relation.control === "human" ? "users" : "lock"} className={`size-4 ${relation.control === "ai" ? "text-brand-500" : "text-gray-500"}`} />{controlLabels[relation.control]}</div>
          <p className="mt-2 text-xs leading-5 text-gray-500">{relation.control === "ai" ? "统一协调该达人的全部合作与下一步。" : relation.control === "human" ? "AI 未发草稿已作废，运营可以继续回复。" : "自动外联已停止，已有记录与关系仍保留。"}</p>
        </div>
        <div className="mt-3 grid grid-cols-2 gap-2">
          {relation.control !== "human" ? <Button variant="outline" size="sm" className="!px-2" onClick={() => changeControl("human")}><Icon name="users" className="size-4" />接管关系</Button> : <Button size="sm" className="!px-2" onClick={() => changeControl("ai")} disabled={!canReturnForService}><Icon name="agent" className="size-4" />交还 Agent</Button>}
          {relation.control === "paused" ? <Button variant="outline" size="sm" className="!px-2" onClick={() => changeControl("ai")} disabled={!canReturnForService}>{stoppedMarketing ? "接续主动服务" : "恢复 Agent"}</Button> : <Button variant="ghost" size="sm" className="!px-2" onClick={() => changeControl("paused")}><Icon name="lock" className="size-4" />暂停外联</Button>}
        </div>
        <p className="mt-2.5 text-[11px] leading-5 text-gray-400">{stoppedMarketing ? "营销拒联持续生效；交还 Agent 只接续明确的主动服务。" : `控制变更同步作用于这位达人的 ${relatedCases.length} 项合作。`}</p>
      </section>

      <section className="p-5">
        <div className="mb-3 flex items-center justify-between"><h3 className="text-sm font-semibold text-gray-800 dark:text-gray-200">这位达人的合作</h3><Pill>{relatedCases.length}</Pill></div>
        <div className="space-y-2">
          {relatedCases.map((item) => <button key={item.id} type="button" onClick={() => selectRelatedCase(item.id)} className={`w-full rounded-xl border p-3 text-left transition focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-brand-300 ${item.id === currentCase.id ? "border-brand-200 bg-brand-50/60 dark:border-brand-800 dark:bg-brand-500/10" : "border-gray-200 hover:bg-gray-50 dark:border-gray-800 dark:hover:bg-gray-800"}`}>
            <div className="flex items-start gap-2"><span className={`mt-1.5 size-1.5 shrink-0 rounded-full ${item.status === "closed" ? "bg-gray-300" : item.sendState === "unknown" || item.status === "needs_operator" ? "bg-warning-500" : "bg-brand-500"}`} /><span className="min-w-0 flex-1 text-xs font-medium leading-5 text-gray-700 dark:text-gray-200">{item.title}</span>{item.id === currentCase.id && <Icon name="check" className="mt-0.5 size-4 shrink-0 text-brand-500" />}</div>
            <p className="ml-3.5 mt-1 text-[11px] text-gray-500">{productFor(state, item.productId).name} · {item.sendState === "unknown" ? "待核验" : STATUS[item.status]}</p>
          </button>)}
        </div>
        <Button variant="ghost" size="sm" className="mt-2 !px-0 text-xs" onClick={() => go("opportunities")}>查看其他商品机会<Icon name="arrow" className="size-3.5" /></Button>
      </section>

      <section className="p-5">
        <h3 className="mb-3 text-sm font-semibold text-gray-800 dark:text-gray-200">本次判断依据</h3>
        <p className="text-xs leading-6 text-gray-600 dark:text-gray-400">{currentCase.reason}</p>
        {opportunities.length > 0 && <div className="mt-3 space-y-3">{opportunities.map((item) => <div key={item.id} className="border-l-2 border-gray-200 pl-3 dark:border-gray-700"><span className="text-[11px] font-medium text-gray-500">{item.source === "first" ? "一发匹配依据" : "二发发现依据"}</span><p className="mt-1 text-xs leading-5 text-gray-600 dark:text-gray-400">{item.evidence}</p></div>)}</div>}
        <div className="mt-4 flex items-center justify-between rounded-lg bg-gray-50 px-3 py-2.5 text-xs dark:bg-gray-800"><span className="text-gray-500">画像资料</span><span className={creator.profileFresh ? "text-success-600 dark:text-success-400" : "text-warning-600 dark:text-warning-400"}>{creator.profileFresh ? "关键字段已准备" : "历史资料，待复核"}</span></div>
      </section>

      <section className="p-5">
        <h3 className="mb-3 text-sm font-semibold text-gray-800 dark:text-gray-200">当前商品与条件</h3>
        <div className="flex items-center gap-3"><img src={product.image} alt={product.name} width={48} height={48} className="size-12 rounded-xl border border-gray-100 bg-gray-50 object-cover dark:border-gray-700" /><div className="min-w-0"><p className="truncate text-xs font-medium text-gray-800 dark:text-gray-200">{product.name}</p><p className="mt-1 text-xs text-gray-500">{money(product)}</p></div></div>
        <dl className="mt-4 space-y-3 text-xs">
          <div className="flex justify-between gap-2"><dt className="text-gray-500">当前佣金</dt><dd className="font-medium text-gray-700 dark:text-gray-200">{product.commission}%</dd></div>
          <div className="flex justify-between gap-2"><dt className="text-gray-500">样品路径</dt><dd className="text-gray-700 dark:text-gray-200">{product.sample ? "可查询" : "暂无可用样品"}</dd></div>
          <div className="flex justify-between gap-2"><dt className="text-gray-500">方案状态</dt><dd className={product.ready ? "text-success-600" : "text-warning-600"}>{product.ready ? "已准备" : "等待准备"}</dd></div>
          <div className="flex justify-between gap-2"><dt className="text-gray-500">发送结果</dt><dd className={unknown ? "text-warning-600" : "text-gray-700 dark:text-gray-200"}>{unknown ? "待核验" : currentCase.sendState === "accepted" ? "平台已接收" : "尚未提交"}</dd></div>
          <div className="flex justify-between gap-2"><dt className="text-gray-500">方案采用</dt><dd className={currentCase.adopted ? "text-success-600" : "text-gray-700 dark:text-gray-200"}>{currentCase.adopted ? "已核实" : "待确认"}</dd></div>
        </dl>
      </section>

      <section className="p-5">
        <h3 className="mb-3 text-sm font-semibold text-gray-800 dark:text-gray-200">偏好与经营目标</h3>
        <div className="mb-3"><MarketPill market={creator.market} /></div>
        <div className="space-y-2">{relation.preferences.map((entry, index) => <p key={`${entry}-${index}`} className="flex gap-2 text-xs leading-5 text-gray-500"><Icon name="check" className="mt-0.5 size-3.5 shrink-0 text-gray-400" />{entry}</p>)}</div>
        {goal && <button type="button" className="mt-4 flex w-full items-center justify-between gap-2 rounded-xl bg-gray-50 p-3 text-left text-xs text-gray-600 hover:bg-gray-100 dark:bg-gray-800 dark:text-gray-400 dark:hover:bg-gray-700" onClick={() => go("goals")}><span className="min-w-0"><span className="block truncate font-medium text-gray-700 dark:text-gray-200">{goal.name}</span><span className="mt-1 block">策略 v{goal.version}{goal.status === "paused" ? goal.pauseScope === "new_acquisition" ? " · 暂停新获客，服务继续" : " · 暂停全部自动外发" : goal.status === "draft" ? " · 草稿" : ""}</span></span><Icon name="arrow" className="size-4 shrink-0" /></button>}
      </section>
    </div>;
  }

  return <>
    <PageHeading title="合作工作台" description="每段对话，都连着一个清楚的下一步。" action={<div className="flex flex-wrap gap-3"><Button onClick={() => go("workspace", {mode:"local"})}><Icon name="agent" className="size-4" />进入本地运行</Button><Button variant="outline" onClick={() => openEvents()}><Icon name="bolt" className="size-4" />演示事件</Button></div>} />

    <div className="mb-4 flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-gray-500">
      <span className="flex items-center gap-2"><span className="size-1.5 rounded-full bg-brand-500" />{marketCases.filter((item) => item.status !== "closed").length} 项正在合作</span>
      <button type="button" className={`flex items-center gap-1.5 hover:text-warning-700 ${filter === "needs_operator" ? "font-medium text-warning-600" : ""}`} onClick={() => { setFilter(filter === "needs_operator" ? "all" : "needs_operator"); setMobileDetail(false); }}><Icon name="users" className="size-3.5" />{needsDecisionCount} 项需要决定</button>
      <button type="button" className={`flex items-center gap-1.5 hover:text-warning-700 ${filter === "unknown" ? "font-medium text-warning-600" : ""}`} onClick={() => { setFilter(filter === "unknown" ? "all" : "unknown"); setMobileDetail(false); }}><Icon name="eye" className="size-3.5" />{unknownCount} 项等待核验</button>
    </div>

    {hydrated && initialCaseId && !state.cases.some((item) => item.id === initialCaseId) && <div className="mb-4"><Notice>链接中的合作事项暂不可用。你可以从当前列表选择，或在机会货盘中建立合作。</Notice></div>}

    <div className="grid h-[calc(100dvh-344px)] min-h-[360px] grid-cols-1 overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-theme-xs lg:h-[calc(100dvh-280px)] lg:min-h-[420px] lg:grid-cols-[264px_minmax(0,1fr)] xl:grid-cols-[264px_minmax(0,1fr)_280px] 2xl:grid-cols-[288px_minmax(0,1fr)_300px] dark:border-gray-800 dark:bg-gray-900">
      <aside className={`${mobileDetail ? "hidden lg:flex" : "flex"} min-h-0 flex-col border-r border-gray-200 dark:border-gray-800`} aria-label="合作事项列表">
        <div className="shrink-0 border-b border-gray-100 p-4 dark:border-gray-800">
          <div className="mb-4 flex items-center justify-between"><h2 className="text-sm font-semibold text-gray-800 dark:text-gray-200">全部合作</h2><Pill>{filteredCases.length}</Pill></div>
          <div className="relative"><Icon name="search" className="pointer-events-none absolute left-3 top-3.5 size-4 text-gray-400" /><Input aria-label="搜索达人、商品或事项" placeholder="搜索达人、商品…" value={query} onChange={(event) => setQuery(event.target.value)} className="!h-10 !pl-9 !text-xs" /></div>
          <div className="mt-2 grid grid-cols-2 gap-2">
            <Select aria-label="按市场筛选合作" value={state.marketFilter} onChange={(event) => dispatch({ type: "market", market: event.target.value as "all" | Market })} className="!h-9 !px-2 !text-xs"><option value="all">全部市场</option>{Object.entries(MARKETS).map(([value, entry]) => <option key={value} value={value}>{entry.name}</option>)}</Select>
            <Select aria-label="按状态筛选合作" value={filter} onChange={(event) => setFilter(event.target.value as CaseFilter)} className="!h-9 !px-2 !text-xs"><option value="all">全部状态</option>{Object.entries(STATUS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}<option value="unknown">结果待核验</option></Select>
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
          {filteredCases.length === 0 ? <EmptyState title="没有匹配的合作" description={state.cases.length ? "试试其他市场或状态，刚建立的合作会出现在这里。" : "从机会货盘选中一项机会，即可开始合作。"} action={<Button size="sm" variant="outline" onClick={() => { if (!state.cases.length) go("opportunities"); else { setQuery(""); setFilter("all"); dispatch({ type: "market", market: "all" }); } }}>{state.cases.length ? "清除筛选" : "前往机会货盘"}</Button>} /> : filteredCases.map((item) => {
            const person = creatorFor(state, item.creatorId), itemRelation = relationFor(state, item.creatorId);
            const lastMessage = [...item.messages].reverse().find((entry) => entry.direction !== "system");
            const active = currentCase?.id === item.id;
            return <button key={item.id} type="button" aria-current={active ? "true" : undefined} onClick={() => selectCase(item.id)} className={`relative flex w-full gap-3 border-b border-gray-100 px-4 py-4 text-left transition focus-visible:z-10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-400 dark:border-gray-800 ${active ? "bg-brand-50/75 dark:bg-brand-500/10" : "hover:bg-gray-50 dark:hover:bg-gray-800/60"}`}>
              {active && <span className="absolute inset-y-4 left-0 w-0.5 rounded-r bg-brand-500" />}
              <div className="relative self-start"><Avatar src={person.avatar} name={person.name} size={40} /><span className={`absolute -bottom-0.5 -right-0.5 size-3 rounded-full border-2 border-white dark:border-gray-900 ${itemRelation.control === "ai" ? "bg-success-500" : itemRelation.control === "human" ? "bg-brand-500" : "bg-gray-400"}`} /></div>
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline justify-between gap-2"><span className="truncate text-sm font-semibold text-gray-800 dark:text-gray-200">{person.name}</span><span className="shrink-0 text-[10px] font-medium text-gray-400">{person.market.toUpperCase()}</span></div>
                <p className="mt-1 truncate text-xs font-medium text-gray-600 dark:text-gray-300">{item.title}</p>
                <p className="mt-1.5 line-clamp-2 text-[11px] leading-5 text-gray-500">{lastMessage?.translation || lastMessage?.text || item.nextStep}</p>
                <div className="mt-2 flex items-center justify-between gap-2"><span className={`truncate text-[11px] ${item.sendState === "unknown" || item.status === "needs_operator" ? "text-warning-600 dark:text-warning-400" : item.adopted ? "text-success-600" : "text-gray-400"}`}>{item.sendState === "unknown" ? "结果待核验" : item.adopted ? "方案已采用" : STATUS[item.status]}</span><span className="shrink-0 text-[10px] text-gray-400">{item.messages.at(-1)?.at || "新建"}</span></div>
              </div>
            </button>;
          })}
        </div>
        <div className="shrink-0 border-t border-gray-100 px-4 py-3 text-[11px] text-gray-400 dark:border-gray-800">一发、二发和历史需求，共用同一达人关系</div>
      </aside>

      <section className={`${mobileDetail ? "flex" : "hidden lg:flex"} min-h-0 min-w-0 flex-col`} aria-label="当前合作对话">
        {!currentCase || !creator || !product || !relation ? <div className="flex flex-1 items-center justify-center"><EmptyState title="选择一项合作，接续下一步" description="在左侧查看达人需求、商品方案与当前进展。" action={<Button variant="outline" className="lg:hidden" onClick={() => setMobileDetail(false)}>返回合作列表</Button>} /></div> : <>
          <header className="flex shrink-0 items-center gap-3 border-b border-gray-100 px-4 py-4 sm:px-5 dark:border-gray-800">
            <Button variant="ghost" size="sm" className="!p-1.5 lg:hidden" onClick={() => setMobileDetail(false)} aria-label="返回合作列表"><Icon name="arrow" className="size-5 rotate-180" /></Button>
            <Avatar src={creator.avatar} name={creator.name} size={42} />
            <div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-x-2 gap-y-1"><h2 className="truncate text-sm font-semibold text-gray-800 sm:text-base dark:text-white/90">{creator.name}</h2><span className="text-[10px] font-medium text-gray-400">{MARKETS[creator.market].locale}</span></div><p className="mt-1 truncate text-xs text-gray-500">@{creator.handle} <span className="mx-1.5 text-gray-300">·</span> {creator.format}</p></div>
            <Button variant="ghost" size="sm" className={`!px-2 ${showTranslation ? "!text-brand-500" : ""}`} aria-pressed={showTranslation} title={showTranslation ? "收起中文释义" : "显示中文释义"} onClick={() => setShowTranslation(!showTranslation)}><Icon name="file" className="size-4" /><span className="hidden sm:inline">释义</span></Button>
            <Button variant="ghost" size="sm" className="!p-2 xl:hidden" onClick={() => setDrawer("details")} aria-label="查看关系与证据"><Icon name="info" /></Button>
          </header>

          <div className="shrink-0 border-b border-gray-100 px-4 py-3 sm:px-5 dark:border-gray-800">
            <div className="flex flex-wrap items-center justify-between gap-2"><p className="text-xs font-medium text-gray-700 dark:text-gray-300">{currentCase.title}</p><StatusPill item={currentCase} /></div>
            <button type="button" onClick={() => setDrawer("details")} className="mt-3 flex w-full items-center gap-3 rounded-xl bg-gray-50 p-2.5 text-left transition hover:bg-gray-100 dark:bg-gray-800/70 dark:hover:bg-gray-800">
              <img src={product.image} alt={product.name} width={38} height={38} className="size-10 rounded-lg border border-white object-cover dark:border-gray-700" />
              <span className="min-w-0 flex-1"><span className="block truncate text-xs font-medium text-gray-700 dark:text-gray-200">{product.name}</span><span className="mt-1 block truncate text-[11px] text-gray-500">{money(product)} <span className="mx-1.5">·</span> 当前佣金 {product.commission}%</span></span><Icon name="arrow" className="size-4 shrink-0 text-gray-400" />
            </button>
          </div>

          <div ref={scrollRef} className="min-h-0 flex-1 space-y-5 overflow-y-auto overscroll-contain bg-gray-50/30 px-4 py-5 sm:px-5 dark:bg-gray-950/20" role="log" aria-label={`${creator.name} 的合作对话`} aria-live="polite">
            <div className="flex items-center gap-3 text-[10px] text-gray-400"><span className="h-px flex-1 bg-gray-100 dark:bg-gray-800" /><span>当前合作记录</span><span className="h-px flex-1 bg-gray-100 dark:bg-gray-800" /></div>
            {currentCase.messages.length === 0 && <div className="mx-auto my-6 max-w-xs rounded-2xl border border-dashed border-gray-200 px-5 py-6 text-center dark:border-gray-700"><Icon name="chat" className="mx-auto mb-3 size-7 text-gray-300" /><h3 className="text-sm font-medium text-gray-700 dark:text-gray-200">合作已建立，尚未开始对话</h3><p className="mt-2 text-xs leading-6 text-gray-500">{currentCase.reason}</p><p className="mt-3 text-xs text-brand-500">准备好后，可模拟 Agent 推进下一步。</p></div>}
            {currentCase.messages.map((message) => {
              if (message.direction === "system") return <div key={message.id} className="mx-auto flex max-w-[94%] items-start gap-2 rounded-lg bg-gray-100/80 px-3 py-2.5 text-[11px] leading-5 text-gray-500 dark:bg-gray-800/80"><Icon name={message.text.includes("未知") ? "info" : "time"} className="mt-0.5 size-3.5 shrink-0 text-gray-400" /><span>{message.text}<span className="ml-2 whitespace-nowrap text-[10px] text-gray-400">{message.at}</span></span></div>;
              const fromTeam = message.direction === "team";
              return <div key={message.id} className={`flex items-start gap-2.5 ${fromTeam ? "flex-row-reverse" : ""}`}>
                {fromTeam ? <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-brand-50 text-brand-500 dark:bg-brand-500/10"><Icon name={message.by === "human" ? "users" : "agent"} className="size-4" /></span> : <Avatar src={creator.avatar} name={creator.name} size={28} />}
                <div className="min-w-0 max-w-[calc(100%-46px)] sm:max-w-[84%]">
                  <div className={`mb-1.5 flex items-center gap-2 text-[10px] text-gray-400 ${fromTeam ? "justify-end" : ""}`}><span>{fromTeam ? message.by === "human" ? "BJN · 运营" : "关系 Agent" : creator.name}</span></div>
                  <div className={`rounded-2xl px-4 py-3 ${fromTeam ? "rounded-tr-sm bg-brand-500 text-white" : "rounded-tl-sm border border-gray-100 bg-white text-gray-700 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200"}`}>
                    <p className="whitespace-pre-wrap break-words text-sm leading-6">{message.text}</p>
                    {showTranslation && message.translation && <div className={`mt-3 border-t pt-2.5 ${fromTeam ? "border-white/20" : "border-gray-100 dark:border-gray-700"}`}><p className={`mb-1 text-[10px] ${fromTeam ? "text-white/60" : "text-gray-400"}`}>中文释义</p><p className={`text-xs leading-5 ${fromTeam ? "text-white/85" : "text-gray-500 dark:text-gray-400"}`}>{message.translation}</p></div>}
                  </div>
                  <div className={`mt-1.5 flex items-center gap-2 text-[10px] text-gray-400 ${fromTeam ? "justify-end" : ""}`}><span>{message.at}</span>{fromTeam && message.receipt && <span className={`inline-flex items-center gap-1 ${message.receipt === "unknown" ? "text-warning-600" : ""}`}><Icon name={message.receipt === "unknown" ? "time" : "check"} className="size-3" />{message.receipt === "unknown" ? "结果待核验" : "平台已接收"}</span>}</div>
                  {fromTeam && message.by === "ai" && message.translation === "这是对应商品的合作方案，请查看当前条件。" && <div className="mt-2 overflow-hidden rounded-xl border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-800"><div className="flex items-center gap-3 p-3"><img src={product.image} alt={product.name} width={50} height={50} className="size-12 rounded-lg object-cover" /><div className="min-w-0"><p className="text-xs font-medium text-gray-800 dark:text-gray-200">{product.name}</p><p className="mt-1 text-xs text-gray-500">{money(product)} · {product.commission}% 佣金</p></div></div><div className="border-t border-gray-100 px-3 py-2 text-[10px] text-gray-400 dark:border-gray-700">关联商品方案 · 浏览器内演示</div></div>}
                </div>
              </div>;
            })}
            {currentCase.adopted && <div className="rounded-xl border border-success-100 bg-success-50 px-4 py-3 dark:border-success-900 dark:bg-success-500/10"><div className="flex items-center gap-2 text-sm font-medium text-success-700 dark:text-success-400"><Icon name="check" className="size-4" />具体方案已核实采用</div><p className="mt-1.5 pl-6 text-xs leading-5 text-success-600">本事项已结束。内容发布与收益各自等待独立证据。</p></div>}
          </div>

          <div className="shrink-0 border-t border-gray-100 bg-white p-4 sm:px-5 dark:border-gray-800 dark:bg-gray-900">
            {unknown ? <div className="rounded-xl border border-warning-200 bg-warning-50 px-4 py-3 dark:border-warning-900 dark:bg-warning-500/10"><div className="flex items-start gap-2.5"><Icon name="info" className="mt-0.5 size-4 shrink-0 text-warning-600" /><div className="min-w-0 flex-1"><p className="text-xs font-semibold text-warning-700 dark:text-warning-400">原发送结果未知，先核验</p><p className="mt-1 text-xs leading-5 text-warning-600">{currentCase.nextStep}</p></div></div><Button size="sm" variant="outline" className="mt-3 !bg-white !text-xs dark:!bg-gray-900" onClick={() => openEvents("verify_accepted")}><Icon name="eye" className="size-3.5" />模拟核验原意图</Button></div> : commercialDecision && relation.control !== "human" ? <div className="rounded-xl border border-warning-200 bg-warning-50/60 p-3.5 dark:border-warning-900 dark:bg-warning-500/10"><p className="text-xs font-semibold text-gray-800 dark:text-gray-200">需要一次具体商业决定</p><p className="mt-1.5 text-xs leading-5 text-gray-500">达人希望调整条件；当前方案佣金为 {product.commission}%。选择继续、暂缓或由运营沟通。</p><Button size="sm" className="mt-3" onClick={() => openDialog("decision")}>处理这个决定<Icon name="arrow" className="size-3.5" /></Button></div> : relation.control === "human" ? <form onSubmit={(event) => { event.preventDefault(); sendMessage(); }}>
              <div className="mb-2.5 flex items-center justify-between gap-2"><span className="flex items-center gap-1.5 text-xs font-medium text-gray-600 dark:text-gray-300"><Icon name="users" className="size-3.5" />运营正在回复</span>{commercialDecision ? <button type="button" className="text-xs font-medium text-warning-600 hover:text-warning-700" onClick={() => openDialog("decision")}>记录商业决定</button> : <span className="text-[10px] text-gray-400">{MARKETS[creator.market].locale}</span>}</div>
              <TextArea aria-label="人工回复内容" rows={3} value={draft} onChange={(event) => setDraft(event.target.value)} disabled={!canSend} maxLength={2000} placeholder={currentCase.status === "closed" ? "该事项已结束，可通过新的达人事件接续需求。" : "输入回复，使用达人对应语言…"} className="!min-h-20 !resize-none !text-sm" onKeyDown={(event) => { if ((event.metaKey || event.ctrlKey) && event.key === "Enter" && !event.nativeEvent.isComposing) { event.preventDefault(); sendMessage(); } }} />
              <div className="mt-2.5 flex items-center justify-between gap-3"><span className="text-[10px] text-gray-400">{draft.length}/2000 <span className="ml-2 hidden sm:inline">⌘ / Ctrl + Enter 发送</span></span><Button type="submit" size="sm" disabled={!canSend || !draft.trim()}><Icon name="send" className="size-4" />发送消息</Button></div>
            </form> : relation.control === "paused" ? <div className="flex items-start gap-3 rounded-xl bg-gray-50 p-3.5 dark:bg-gray-800"><span className="rounded-lg bg-white p-2 text-gray-400 dark:bg-gray-700"><Icon name="lock" className="size-4" /></span><div><p className="text-xs font-medium text-gray-700 dark:text-gray-200">自动外联已暂停</p><p className="mt-1 text-xs leading-5 text-gray-500">新收到的主动需求仍会保留，可以接管后处理。</p><Button variant="ghost" size="sm" className="mt-1 !px-0 !text-xs" onClick={() => changeControl("human")}>由我接续处理<Icon name="arrow" className="size-3.5" /></Button></div></div> : <div>
              <div className="flex items-start gap-2.5"><span className="rounded-lg bg-brand-50 p-2 text-brand-500 dark:bg-brand-500/10"><Icon name="agent" className="size-4" /></span><div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><p className="text-xs font-semibold text-gray-700 dark:text-gray-200">{currentCase.status === "closed" ? "本事项已结束" : "Agent 正在负责"}</p>{currentCase.draftValid && canAdvance && <span className="text-[10px] text-success-600">下一步已准备</span>}</div><p className="mt-1 text-xs leading-5 text-gray-500">{goal?.status === "paused" && !goalAllowsAdvance ? goal.pauseScope === "new_acquisition" ? "新获客已暂停，明确的主动服务请求仍可接续。" : "所属目标暂停全部自动外发，等待目标恢复。" : currentCase.nextStep}</p>{relation.marketingStopped && <p className="mt-1 text-[10px] leading-5 text-gray-400">营销拒联保留，仅处理明确的主动服务请求。</p>}</div></div>
              <div className="mt-3 flex flex-wrap items-center justify-between gap-2"><Button variant="ghost" size="sm" className="!px-0 !text-xs" onClick={() => openDialog("note")}><Icon name="plus" className="size-3.5" />补充信息</Button><Button size="sm" disabled={!canAdvance} onClick={() => { dispatch({ type: "advance_case", caseId: currentCase.id }); notify("已模拟推进，查看对话与最新状态。"); }}><Icon name="bolt" className="size-3.5" />{currentCase.delivered ? "正在等待后续事件" : "模拟推进下一步"}</Button></div>
            </div>}
          </div>
        </>}
      </section>

      <aside className="hidden min-h-0 overflow-y-auto overscroll-contain border-l border-gray-200 xl:block dark:border-gray-800" aria-label="关系控制与业务证据">{renderRelationDetails()}</aside>
    </div>

    <Drawer open={drawer === "details"} onClose={() => setDrawer(null)} title="关系与业务证据" description={creator ? `${creator.name} · 同一关系下的全部合作` : undefined}>{renderRelationDetails()}</Drawer>

    <Drawer open={drawer === "events"} onClose={() => setDrawer(null)} title="演示事件" description="选择一条事件，观察关系、对话和下一步如何变化。">
      <div className="p-5">
        <Field label="应用到哪项合作"><Select value={eventCaseId} onChange={(event) => setEventCaseId(event.target.value)} disabled={!state.cases.length}><option value="" disabled>选择合作事项</option>{state.cases.map((item) => <option key={item.id} value={item.id}>{creatorFor(state, item.creatorId).name} · {item.title}</option>)}</Select></Field>
        {eventCase && eventCreator && <div className="mt-4 flex items-center gap-3 rounded-xl bg-gray-50 p-3 dark:bg-gray-800"><Avatar src={eventCreator.avatar} name={eventCreator.name} size={36} /><div className="min-w-0 flex-1"><p className="truncate text-xs font-semibold text-gray-700 dark:text-gray-200">{eventCreator.name}</p><p className="mt-1 truncate text-[11px] text-gray-500">{eventCase.nextStep}</p></div><StatusPill item={eventCase} /></div>}
        {!state.cases.length ? <EmptyState title="还没有可演示的合作" description="先从机会货盘建立一项合作，再体验事件推进。" action={<Button onClick={() => { setDrawer(null); go("opportunities"); }}>前往机会货盘</Button>} /> : (["creator", "platform"] as const).map((group) => <div key={group} className="mt-6"><h3 className="mb-3 text-xs font-semibold text-gray-500">{group === "creator" ? "达人带来新消息" : "平台与结果变化"}</h3><div className="space-y-2">{scenarios.filter((entry) => entry.group === group).map((entry) => {
          const blocked = scenarioBlocked(entry.value, eventCase), active = selectedEvent === entry.value;
          return <button key={entry.value} type="button" aria-pressed={active} disabled={Boolean(blocked)} title={blocked || entry.description} onClick={() => setSelectedEvent(entry.value)} className={`flex w-full items-start gap-3 rounded-xl border p-3.5 text-left transition focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-brand-300 disabled:cursor-not-allowed disabled:opacity-45 ${active && !blocked ? "border-brand-300 bg-brand-50 dark:border-brand-700 dark:bg-brand-500/10" : "border-gray-200 hover:border-gray-300 dark:border-gray-800 dark:hover:border-gray-700"}`}><span className={`mt-0.5 rounded-lg p-2 ${active && !blocked ? "bg-white text-brand-500 dark:bg-gray-800" : "bg-gray-50 text-gray-400 dark:bg-gray-800"}`}><Icon name={entry.icon} className="size-4" /></span><span className="min-w-0 flex-1"><span className="block text-xs font-semibold text-gray-700 dark:text-gray-200">{entry.title}</span><span className="mt-1 block text-[11px] leading-5 text-gray-500">{blocked || entry.description}</span></span>{active && !blocked && <Icon name="check" className="mt-1 size-4 shrink-0 text-brand-500" />}</button>;
        })}</div></div>)}
      </div>
      <div className="sticky bottom-0 border-t border-gray-100 bg-white/95 px-5 py-4 backdrop-blur dark:border-gray-800 dark:bg-gray-900/95"><Button className="w-full" disabled={Boolean(scenarioBlocked(selectedEvent, eventCase))} onClick={applyEvent}><Icon name="bolt" className="size-4" />模拟这条事件</Button><p className="mt-2 text-center text-[10px] leading-5 text-gray-400">仅更新浏览器演示状态，不调用平台或模型。</p></div>
    </Drawer>

    <Dialog open={Boolean(dialog)} onClose={() => setDialog(null)} title={dialog?.kind === "decision" ? "处理合作条件决定" : "给 Agent 补充信息"} description={modalCreator ? `${modalCreator.name} · ${modalCase?.title}` : undefined}>
      {!modalCase || !modalCreator || !modalProduct ? <EmptyState title="该事项暂不可用" description="关闭窗口后查看最新合作列表。" /> : <div className="space-y-5">
        {dialog?.kind === "decision" ? <>
          <div className="rounded-xl border border-gray-200 bg-gray-50 p-4 dark:border-gray-700 dark:bg-gray-800"><div className="flex items-center gap-3"><img src={modalProduct.image} alt={modalProduct.name} width={44} height={44} className="size-11 rounded-lg object-cover" /><div><p className="text-sm font-medium text-gray-800 dark:text-gray-200">{modalProduct.name}</p><p className="mt-1 text-xs text-gray-500">当前方案佣金 {modalProduct.commission}% · {money(modalProduct)}</p></div></div><p className="mt-3 text-xs leading-6 text-gray-600 dark:text-gray-400">{modalCase.reason}</p></div>
          <fieldset><legend className="mb-3 text-sm font-medium text-gray-700 dark:text-gray-200">这次怎么继续？</legend><div className="space-y-2">{([{ value: "keep", label: "沿用当前条件", description: "不增加佣金，交还 Agent 按当前方案继续。" }, { value: "pause", label: "暂缓本次合作", description: "结束本轮事项，保留达人关系与其他合作。" }, { value: "manual", label: "由我接管沟通", description: "统一接管该达人的全部合作，记录后继续回复。" }] as const).map((entry) => <label key={entry.value} className={`flex cursor-pointer items-start gap-3 rounded-xl border p-3.5 ${decisionChoice === entry.value ? "border-brand-300 bg-brand-50/60 dark:border-brand-700 dark:bg-brand-500/10" : "border-gray-200 dark:border-gray-700"}`}><input type="radio" name="commercial-decision" value={entry.value} checked={decisionChoice === entry.value} onChange={() => setDecisionChoice(entry.value)} className="mt-1 accent-brand-500" /><span><span className="block text-sm font-medium text-gray-700 dark:text-gray-200">{entry.label}</span><span className="mt-1 block text-xs leading-5 text-gray-500">{entry.description}</span></span></label>)}</div></fieldset>
        </> : <Notice>补充会保存为内部记录，不发送给达人。商品条件与执行结果仍以当前业务证据为准。</Notice>}
        <Field label={dialog?.kind === "decision" ? "决定依据" : "补充内容"} hint="最多 1,000 字。请写清具体事实或接续安排。"><TextArea autoFocus value={note} maxLength={1000} onChange={(event) => { setNote(event.target.value); setFormError(""); }} placeholder={dialog?.kind === "decision" ? "例如：本次沿用已有方案；若达人不接受，则保留关系等待其他机会。" : "例如：达人已明确表示手里仍有同款，请先核对型号再继续。"} aria-invalid={Boolean(formError)} /></Field>
        {formError && <p role="alert" className="text-xs text-error-600">{formError}</p>}
        <div className="flex justify-end gap-3 border-t border-gray-100 pt-4 dark:border-gray-800"><Button variant="outline" onClick={() => setDialog(null)}>取消</Button><Button onClick={submitDialog}>{dialog?.kind === "decision" ? "记录决定并接续" : "保存内部补充"}</Button></div>
      </div>}
    </Dialog>
  </>;
}
