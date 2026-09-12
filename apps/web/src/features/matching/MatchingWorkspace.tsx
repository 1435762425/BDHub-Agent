"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { IdentityContext } from "../creator-identities/IdentityContext";
import { useDemo } from "../bdhub/store";
import {
  Avatar, Button, Card, EmptyState, Field, Icon, Input, MarketPill, Notice,
  PageHeading, Pill, Select, Tabs,
} from "../bdhub/ui";
import type {
  CandidateSource, MatchCandidate, MatchCreator, MatchingCommand, MatchingResponse,
  MatchingStats, MatchMarket, MatchOffer, MatchPage, MatchProduct, MatchRun,
  RecallQuery, ReviewPacket, AssessmentResponse,
} from "./contracts";
import { AssessmentPanel, AssessmentSummary, validAssessmentResponse, type AssessmentLabel } from "./AssessmentPanel";
import { AutoAnalysisBadge, AutoAnalysisDetails, AutoAnalysisSummary } from "./AutoAnalysisPanel";

const API = "/api/matching";
const PENDING_KEY = "bdhub-matching-pending-v1";
const ASSESSMENT_RECOVERY_KEY = "bdhub-matching-assessment-recovery-v1";
type Dataset = "demo" | "italy" | "italy-profiles";
const PAGE_SIZE = 12;
const sourceLabels: Record<CandidateSource, string> = {
  exact_pid: "二发 · 同款证据", explicit_demand: "明确需求", category_price: "类目与价格",
  category: "类目适配", cold_start: "探索候选",
};
const formatLabels = { video: "短视频", live: "直播" };
const categoryLabels: Record<string, string> = { beauty: "美妆护理", home: "家居", electronics: "数码", sport: "运动", fashion: "服饰配件", kitchen: "厨房", pets: "宠物", wellness: "日常护理" };
type Subject = MatchProduct | MatchCreator;
type PendingRequest = { requestId: string; command: MatchingCommand };
type CommandError = { code: string; message: string; retry?: PendingRequest };
type AssessmentRecovery = { runId: string; result: AssessmentResponse | null; busy: boolean; error: string };

function money(value: number | null, currency: string) {
  return value === null ? "价格未知" : new Intl.NumberFormat("zh-CN", { style: "currency", currency, maximumFractionDigits: 2 }).format(value / 100);
}
function percent(value: number | null) { return value === null ? "未知" : `${value / 100}%`; }
function date(value: number | null) { return value === null ? "未记录" : new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Shanghai" }).format(value); }
function observationWindow(subject: Subject) {
  const { windowStart, windowEnd } = subject.source;
  const sourceDate = (value: number | null) => value === null ? "未记录" : new Date(value).toISOString().slice(0, 10);
  return windowStart === null && windowEnd === null ? "来源未记录统计窗口" : `${sourceDate(windowStart)} – ${sourceDate(windowEnd)}（来源日期，时区未提供）`;
}
function relationshipControl(creator: MatchCreator) {
  return creator.control === "auto" ? "Agent 负责" : creator.control === "human" ? "人工接管" : creator.control === "paused" ? "已暂停" : "控制状态未核实";
}
function isProduct(subject: Subject): subject is MatchProduct { return "pid" in subject; }
function subjectName(subject: Subject) { return isProduct(subject) ? subject.title : subject.name; }
function categoryNames(subject: Subject) { const values = !isProduct(subject) && subject.profileSignals ? subject.categories : subject.categoryFact?.sourceLabels.length ? subject.categoryFact.sourceLabels : subject.categories; return values.map(category => categoryLabels[category] || category).join(" · ") || "类目待补充"; }
function formatNames(formats: Subject["formats"]) { return formats.length ? formats.map(format => formatLabels[format]).join(" / ") : "内容形式待补充"; }
function CategoryProvenance({ subject, detailed = false, profileAnalysis = false }: { subject: Subject; detailed?: boolean; profileAnalysis?: boolean }) {
  const fact = subject.categoryFact;
  if (!fact) return null;
  const status = fact.status === "historical" ? profileAnalysis ? "已有画像类目" : "历史类目 · 待复核" : fact.status === "conflict" ? "类目来源冲突 · 暂不匹配" : "类目缺失";
  return <div className={detailed ? "rounded-lg border border-gray-100 p-3 text-xs leading-6 text-gray-500 dark:border-gray-800" : "mt-2 text-xs leading-5 text-gray-500"}>
    <span className={fact.status === "conflict" ? "text-warning-600 dark:text-warning-400" : "text-gray-500"}>{detailed ? `${isProduct(subject) ? "商品" : "达人"}类目：` : ""}{status}</span>
    {detailed && <><p>来源标签：{fact.sourceLabels.join(" / ") || "未记录"}</p><p className="break-all">命名空间：{fact.namespace} · {fact.transformVersion ? `映射版本 ${fact.transformVersion}` : "归一版本未记录"}</p><p>{fact.note}</p><p className="break-all">{fact.source.ref}</p><p>{fact.timeBasis === "batch_completed" ? "批次完成于" : "字段观测于"} {date(fact.source.observedAt)}（北京时间）；不代表当前已核实。</p></>}
  </div>;
}
function validResponse(value: unknown): value is MatchingResponse {
  if (!value || typeof value !== "object") return false;
  const result = value as MatchingResponse;
  return result.kind === "run" ? typeof result.run?.id === "string" && Array.isArray(result.run.candidates)
    && Array.isArray(result.run.analysisPolicy?.ranking) && result.run.analysisPolicy.modelCalls === 0
    && result.run.candidates.every(candidate => candidate.analysis && typeof candidate.analysis.summary === "string" && Array.isArray(candidate.analysis.positiveEvidence) && Array.isArray(candidate.analysis.limitations) && candidate.analysis.profileSummary)
    : result.kind === "packet" ? typeof result.packet?.id === "string" && result.packet.modelStatus === "not_called"
      : result.kind === "assessment" ? validAssessmentResponse(result.result)
        : result.kind === "change" && typeof result.product?.id === "string" && Boolean(result.result);
}
function validPending(value: unknown): value is PendingRequest {
  if (!value || typeof value !== "object") return false;
  const request = value as PendingRequest;
  if (typeof request.requestId !== "string" || !request.requestId || !request.command) return false;
  const command = request.command;
  if (command.type === "recall") return Boolean(command.query && ["product", "creator"].includes(command.query.direction) && typeof command.query.subjectId === "string" && ["all", "first", "second"].includes(command.query.source) && Number.isInteger(command.query.limit));
  if (command.type === "prepare_review") return typeof command.runId === "string" && typeof command.creatorId === "string";
  if (command.type === "assess_candidate") return typeof command.runId === "string" && typeof command.creatorId === "string" && typeof command.productId === "string" && [null, "suitable", "unsuitable", "insufficient"].includes(command.label) && typeof command.note === "string" && command.note.length <= 1000 && Number.isInteger(command.expectedRevision) && command.expectedRevision >= 0;
  return command.type === "demo_change" && typeof command.productId === "string" && Number.isInteger(command.expectedRevision) && ["raise_price", "lower_price", "offer_unavailable", "offer_available"].includes(command.change);
}

/** Only request identity lives in the browser. Matching facts and results belong to the API. */
function useMatchingCommands(api: string, pendingKey: string) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<CommandError | null>(null);
  const [ready, setReady] = useState(false);
  const pending = useRef<PendingRequest | null>(null);
  const writing = useRef(false);
  const mounted = useRef(false);
  const controller = useRef<AbortController | null>(null);

  useEffect(() => {
    mounted.current = true;
    try {
      const currentRaw = sessionStorage.getItem(pendingKey);
      // Requests from the earlier single-dataset UI belong only to the demo API.
      const legacyRaw = !currentRaw && api === `${API}?dataset=demo` ? sessionStorage.getItem(PENDING_KEY) : null;
      const raw = currentRaw || legacyRaw;
      if (raw) {
        const saved: unknown = JSON.parse(raw);
        if (!validPending(saved)) throw new Error("invalid request");
        if (legacyRaw) { sessionStorage.setItem(pendingKey, raw); sessionStorage.removeItem(PENDING_KEY); }
        pending.current = saved;
        setError({ code: "PENDING_RESTORED", message: "上次请求的结果尚未确认。请恢复原请求，再继续其他操作。", retry: saved });
      }
      setReady(true);
    } catch {
      setError({ code: "REQUEST_STORAGE", message: "当前会话无法保存或恢复请求编号。请检查浏览器会话存储后重新打开页面。" });
    }
    return () => { mounted.current = false; controller.current?.abort(); };
  }, [api, pendingKey]);

  const execute = useCallback(async (request: PendingRequest): Promise<MatchingResponse | null> => {
    if (!mounted.current || writing.current || (pending.current && pending.current.requestId !== request.requestId)) return null;
    try { sessionStorage.setItem(pendingKey, JSON.stringify(request)); }
    catch {
      setError({ code: "REQUEST_STORAGE", message: "无法保存请求编号，本次没有提交。请恢复会话存储后重试。" });
      return null;
    }
    const confirmed = () => { pending.current = null; try { sessionStorage.removeItem(pendingKey); } catch {} };
    pending.current = request;
    writing.current = true;
    setBusy(true);
    setError(null);
    const current = new AbortController();
    controller.current = current;
    try {
      const response = await fetch(api, {
        method: "POST", credentials: "same-origin", signal: current.signal,
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(request),
      });
      const body = await response.json().catch(() => null);
      if (!mounted.current) return null;
      if (!response.ok) {
        const rejected = response.status >= 400 && response.status < 500;
        if (rejected) confirmed();
        setError({
          code: typeof body?.error?.code === "string" ? body.error.code : `HTTP_${response.status}`,
          message: typeof body?.error?.message === "string" ? body.error.message : "本次请求未能完成，请检查本地服务。",
          retry: rejected ? undefined : request,
        });
        return null;
      }
      if (!validResponse(body)) throw new Error("服务响应不完整，尚不能确认本次请求结果。");
      confirmed();
      return body;
    } catch (failure) {
      if (!mounted.current || current.signal.aborted) return null;
      setError({ code: "REQUEST_UNCONFIRMED", message: failure instanceof TypeError ? "本地服务连接中断，尚不能确认本次请求结果。" : failure instanceof Error ? failure.message : "连接中断，尚不能确认请求结果。", retry: request });
      return null;
    } finally {
      writing.current = false;
      if (mounted.current) setBusy(false);
    }
  }, [api, pendingKey]);

  const submit = useCallback((command: MatchingCommand) => {
    if (writing.current || pending.current) return Promise.resolve(null);
    return execute({ requestId: crypto.randomUUID(), command });
  }, [execute]);
  return {
    busy, error, submit, blocked: !ready || busy || Boolean(error?.retry) || error?.code === "REQUEST_STORAGE",
    retry: () => error?.retry ? execute(error.retry) : Promise.resolve(null),
    dismiss: () => { if (!pending.current) setError(null); },
  };
}

function SubjectPicture({ subject, size = 44, offline = false }: { subject: Subject; size?: number; offline?: boolean }) {
  if (offline || !(isProduct(subject) ? subject.image : subject.avatar)) return <span aria-hidden="true" className="flex shrink-0 items-center justify-center rounded-xl border border-gray-100 bg-gray-50 text-gray-400 dark:border-gray-800 dark:bg-gray-800/60" style={{ width: size, height: size }}><Icon name={isProduct(subject) ? "box" : "users"} className="size-5" /></span>;
  return isProduct(subject)
    ? <img src={subject.image} alt="" width={size} height={size} className="shrink-0 rounded-xl border border-gray-100 bg-gray-50 object-cover dark:border-gray-800" style={{ width: size, height: size }} />
    : <span className="shrink-0" aria-hidden="true"><Avatar src={subject.avatar} name={subject.name} size={size} /></span>;
}

function FactChip({ label, value }: { label: string; value: boolean | null }) {
  return <span className={`inline-flex items-center gap-1.5 text-xs ${value === true ? "text-success-600 dark:text-success-400" : value === false ? "text-gray-500" : "text-warning-600 dark:text-warning-400"}`}>
    <Icon name={value === true ? "check" : "info"} className="size-3.5" />{label}{value === true ? "符合" : value === false ? "不符合" : "待补充"}
  </span>;
}

function OfferDetails({ offer, offline }: { offer: MatchOffer; offline: boolean }) {
  const now = Date.now();
  const validNow = offer.startsAt <= now && offer.endsAt > now;
  return <div className="rounded-xl border border-gray-200 p-4 dark:border-gray-700">
    <div className="flex flex-wrap items-center justify-between gap-2"><p className="text-xs font-medium text-gray-700 dark:text-gray-200">{offline ? "历史采集方案" : "当前方案"} <span className="font-normal text-gray-400">v{offer.version}</span></p><Pill tone={!validNow || offer.stock === 0 ? "warning" : "neutral"}>{offline ? "历史条件 · 待复核" : !validNow ? "不在有效期内" : offer.stock === 0 ? "暂无库存" : "有效期内"}</Pill></div>
    <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-3 text-xs sm:grid-cols-4">
      <div><dt className="text-gray-400">达人佣金</dt><dd className="mt-1 font-medium text-gray-800 dark:text-gray-200">{percent(offer.creatorCommissionBps)}</dd></div>
      <div><dt className="text-gray-400">机构保留</dt><dd className="mt-1 text-gray-700 dark:text-gray-300">{percent(offer.agencyCommissionBps)}</dd></div>
      <div><dt className="text-gray-400">库存</dt><dd className="mt-1 text-gray-700 dark:text-gray-300">{offer.stock === null ? "待核实" : offer.stock.toLocaleString()}</dd></div>
      <div><dt className="text-gray-400">商品卡</dt><dd className="mt-1 text-gray-700 dark:text-gray-300">{offer.cardStatus === "verified" ? "已有准备证据" : offer.cardStatus === "needs_preparation" ? "需要准备" : "状态未知"}</dd></div>
      <div><dt className="text-gray-400">样品可申请性</dt><dd className="mt-1 text-gray-700 dark:text-gray-300">{offer.sampleAvailable === null ? "待核实" : offer.sampleAvailable ? "可申请" : "不可申请"}</dd></div>
      <div><dt className="text-gray-400">样品额度</dt><dd className="mt-1 text-gray-700 dark:text-gray-300">{offer.sampleQuota === null ? "待核实" : offer.sampleQuota.toLocaleString()}</dd></div>
      <div><dt className="text-gray-400">公开 / 总佣金</dt><dd className="mt-1 text-gray-700 dark:text-gray-300">{percent(offer.publicCommissionBps)} / {percent(offer.totalCommissionBps)}</dd></div>
      <div><dt className="text-gray-400">有效至</dt><dd className="mt-1 text-gray-700 dark:text-gray-300">{date(offer.endsAt)}</dd></div>
    </dl>
    <details className="mt-3 text-xs text-gray-400"><summary className="cursor-pointer py-1">查看方案来源</summary><p className="mt-1 break-all leading-5">活动 {offer.campaignId} · 账号引用 {offer.accountRef}</p><p className="break-all leading-5">{offer.source.ref} · 采集于 {date(offer.source.observedAt)}</p></details>
  </div>;
}

function CandidateCard({ candidate, direction, disabled, reviewing, packetReady, onPrepare, offline, assessment, profileAnalysis = false }: {
  candidate: MatchCandidate; direction: RecallQuery["direction"]; disabled: boolean;
  reviewing: boolean; packetReady: boolean; onPrepare: () => void; offline: boolean; assessment?: ReactNode; profileAnalysis?: boolean;
}) {
  const subject = direction === "product" ? candidate.creator : candidate.product;
  const [identityExpanded, setIdentityExpanded] = useState(false);
  const stopped = candidate.readiness === "suppressed";
  const shortGaps = [...new Set(candidate.gaps.map(gap => gap.replace(/^Offer [^:]+:\s*/, "商品方案：")))];
  return <article className="rounded-xl border border-gray-200 bg-white p-4 dark:border-gray-800 dark:bg-white/[0.015] sm:p-5">
    <div className="flex flex-wrap items-start justify-between gap-4">
      <div className="flex min-w-0 flex-1 items-start gap-3">
        <SubjectPicture subject={subject} size={48} offline={offline} />
        <div className="min-w-0"><h3 className="break-words text-sm font-semibold text-gray-800 dark:text-white/90">{subjectName(subject)}</h3><p className="mt-1 text-xs leading-5 text-gray-500">{categoryNames(subject)}{!profileAnalysis && ` · ${formatNames(subject.formats)}`}</p><div className="mt-2 flex flex-wrap items-center gap-2"><MarketPill market={subject.market} />{!profileAnalysis && <span className="text-xs text-gray-400">{money(candidate.product.priceMinor, candidate.product.currency)}</span>}</div></div>
      </div>
      {profileAnalysis ? <AutoAnalysisBadge analysis={candidate.analysis} /> : <Pill tone={stopped ? "neutral" : candidate.readiness === "needs_facts" ? "warning" : "success"}>{stopped ? "暂不进入评审" : candidate.readiness === "needs_facts" ? "有待补充事实" : "可准备评审"}</Pill>}
    </div>
    {!profileAnalysis && <div className="mt-4 flex flex-wrap gap-2">{candidate.sources.map(source => <Pill key={source} tone={source === "exact_pid" ? "brand" : "neutral"}>{sourceLabels[source]}</Pill>)}</div>}
    {!profileAnalysis && <CategoryProvenance subject={subject} />}
    {profileAnalysis && <AutoAnalysisDetails candidate={candidate} />}
    {candidate.sources.includes("explicit_demand") && <p className="mt-3 text-sm leading-6 text-gray-600 dark:text-gray-400">达人有已记录的商品或类目需求。</p>}
    {!profileAnalysis && <div className="mt-3 flex flex-wrap gap-x-4 gap-y-2">
      <FactChip label={subject.categoryFact ? "历史类目交集" : "类目"} value={candidate.creator.categories.length && candidate.product.categories.length ? candidate.features.categoryOverlap > 0 : null} />
      <FactChip label="价格带" value={candidate.features.priceOverlap} /><FactChip label="内容形式" value={candidate.features.formatOverlap} />
    </div>}
    {candidate.features.exactUnits !== null && <p className="mt-3 rounded-lg bg-brand-50 px-3 py-2 text-xs leading-5 text-brand-600 dark:bg-brand-500/10 dark:text-brand-300">精确同 PID 证据：单次观测 {candidate.features.exactUnits.toLocaleString()} 件，重叠窗口不累加。历史记录不代表当前持有实物或合作意愿。</p>}
    {!profileAnalysis && shortGaps.length > 0 && <p className="mt-3 text-xs leading-5 text-warning-600 dark:text-warning-400">待补充：{shortGaps.slice(0,2).join("；")}{shortGaps.length>2?`；另有 ${shortGaps.length-2} 项见详情`:""}</p>}
    <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-gray-100 pt-3 dark:border-gray-800">
      <p className="text-xs text-gray-400">{profileAnalysis ? "基于已有画像自动分析" : candidate.offers.length ? `${candidate.offers.length} 个${offline ? "采集" : "当前"}方案` : offline ? "暂无已核实商业方案" : "尚无当前方案"}{packetReady&&direction==="product" ? " · 资料包已准备" : ""}</p>
      {direction==="product"&&<Button size="sm" variant={packetReady ? "outline" : "primary"} onClick={onPrepare} disabled={disabled || stopped}><Icon name="agent" className="size-4" />{reviewing ? "正在准备…" : packetReady ? "查看 / 复用资料包" : profileAnalysis ? "整理关系资料包" : offline ? "准备离线证据评审包" : "准备 AI 评审包"}</Button>}
    </div>
    <details className="mt-2 text-sm" onToggle={event => setIdentityExpanded(event.currentTarget.open)}>
      <summary className="cursor-pointer py-2 text-gray-500 marker:text-gray-400">{profileAnalysis ? "查看参考条件、执行准备与来源" : "查看事实、方案与来源"}</summary>
      <div className="mt-2 space-y-3">
        {profileAnalysis && <p className="text-xs leading-6 text-gray-500">候选来源：{candidate.sources.map(source => sourceLabels[source]).join(" · ")}</p>}
        <p className="break-words text-xs leading-6 text-gray-500">{candidate.reasons.join("；")}</p>
        {candidate.gaps.length>0&&<p className="break-words text-xs leading-6 text-gray-500">{profileAnalysis ? "执行准备信息（不影响上述画像分析）：" : "完整缺项："}{candidate.gaps.join("；")}</p>}
        <div className="rounded-xl bg-gray-50 p-4 text-xs leading-6 text-gray-500 dark:bg-gray-800/50">
          <p className="font-medium text-gray-700 dark:text-gray-200">{candidate.product.title}</p><p className="break-all">PID {candidate.product.pid}</p>
          <p>{profileAnalysis ? "参考价格带：" : "达人价格带："}{candidate.creator.priceMinMinor === null || candidate.creator.priceMaxMinor === null ? "尚不完整" : `${money(candidate.creator.priceMinMinor, candidate.creator.currency)} – ${money(candidate.creator.priceMaxMinor, candidate.creator.currency)}`}</p>
          <p>商品价格：{money(candidate.product.priceMinor, candidate.product.currency)}{profileAnalysis && " · 仅作参考"}</p>
          <p>内容形式：{formatNames(candidate.creator.formats)}{profileAnalysis && " · 仅作参考"}</p>
          <p>关系控制：{relationshipControl(candidate.creator)}</p>
          {profileAnalysis && <p>执行状态：本次为离线分析，不发送消息。{stopped ? "该关系已有暂停、接管或拒联限制。" : candidate.readiness === "needs_facts" ? "联系与商业事实仍需在真实执行前核实。" : "离线分析结果不授予执行权限。"}</p>}
          <p>营销联系记录：{candidate.creator.marketingStopped === true ? "已停止营销" : candidate.creator.marketingStopped === false ? "未记录拒联" : "拒联状态未核实"}</p>
          <p className="break-all">OEC 身份：{candidate.creator.oecId || "尚未确认"}{candidate.creator.externalIdentity && ` · Kalodata ${candidate.creator.externalIdentity.id}`}</p>
        </div>
        <CategoryProvenance subject={candidate.product} detailed profileAnalysis={profileAnalysis} />
        {offline && direction === "product" && identityExpanded && <IdentityContext market={candidate.creator.market} oecId={candidate.creator.oecId} externalId={candidate.creator.externalIdentity?.id} sourceHandle={candidate.creator.name} />}
        <CategoryProvenance subject={candidate.creator} detailed profileAnalysis={profileAnalysis} />
        {candidate.offers.map(offer => <OfferDetails key={offer.id} offer={offer} offline={offline} />)}
        {!candidate.offers.length && <p className="text-xs leading-5 text-warning-600">商品可被发现，但缺少当前可核实方案，不能据此承诺佣金或发送卡片。</p>}
        <div className="break-all text-xs leading-5 text-gray-400"><p>商品来源：{candidate.product.source.ref}</p><p>采集于 {date(candidate.product.source.observedAt)} · 统计窗口：{observationWindow(candidate.product)}</p><p className="mt-2">达人来源：{candidate.creator.source.ref}</p><p>采集于 {date(candidate.creator.source.observedAt)} · 统计窗口：{observationWindow(candidate.creator)}</p><p className="mt-1">采集时间显示为北京时间；统计窗口保留来源日期。历史采集不代表当前事实。</p></div>
        {candidate.evidenceRefs.length > 0 && <p className="break-all text-xs leading-5 text-gray-400">证据引用：{candidate.evidenceRefs.join(" · ")}</p>}
      </div>
    </details>
    {assessment}
  </article>;
}

export default function MatchingWorkspace() {
  const params = useSearchParams();
  const requestedDataset = params.get("dataset");
  const dataset: Dataset = requestedDataset === "italy" || requestedDataset === "italy-profiles" ? requestedDataset : "demo";
  return <MatchingDatasetWorkspace key={dataset} dataset={dataset} />;
}

function MatchingDatasetWorkspace({ dataset }: { dataset: Dataset }) {
  const { state, dispatch, notify, go } = useDemo();
  const offline = dataset !== "demo";
  const profiles = dataset === "italy-profiles";
  const api = `${API}?dataset=${dataset}`;
  const commands = useMatchingCommands(api, `${PENDING_KEY}:${dataset}:${API}`);
  const [direction, setDirection] = useState<RecallQuery["direction"]>("product");
  const [source, setSource] = useState<RecallQuery["source"]>(profiles ? "first" : offline ? "second" : "all");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<Subject | null>(null);
  const [page, setPage] = useState<MatchPage<Subject> | null>(null);
  const [pageKey, setPageKey] = useState("");
  const [loading, setLoading] = useState(true);
  const [readError, setReadError] = useState("");
  const [stats, setStats] = useState<MatchingStats | null>(null);
  const [statsError, setStatsError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [run, setRun] = useState<MatchRun | null>(null);
  const [packet, setPacket] = useState<ReviewPacket | null>(null);
  const [reviewingId, setReviewingId] = useState("");
  const [changeMessage, setChangeMessage] = useState("");
  const [assessments, setAssessments] = useState<AssessmentResponse | null>(null);
  const [assessmentError, setAssessmentError] = useState("");
  const [assessmentRefresh, setAssessmentRefresh] = useState(0);
  const [assessmentsLoading, setAssessmentsLoading] = useState(false);
  const [savingPair, setSavingPair] = useState("");
  const [assessmentRecovery, setAssessmentRecovery] = useState<AssessmentRecovery | null>(null);
  const recoveryKey = `${ASSESSMENT_RECOVERY_KEY}:${dataset}:${API}`;
  const recoveryController = useRef<AbortController | null>(null);
  const recoveryStartupDone = useRef(false);
  const viewRevision = useRef(0);
  const readSequence = useRef(0);
  const packetRef = useRef<HTMLElement | null>(null);
  const market = offline ? "it" : state.marketFilter;
  const listKey = `${dataset}|${direction}|${market}|${query}|${offset}`;
  const previousContext = useRef(`${direction}|${market}`);
  const clearResult = useCallback(() => { ++viewRevision.current; setRun(null); setPacket(null); setChangeMessage(""); setAssessments(null); setAssessmentError(""); }, []);

  const rememberAssessmentRecovery = useCallback((runId: string, result: AssessmentResponse | null, error = "") => {
    // Keep only the confirmed run identity in session storage; labels remain in SQLite.
    try { sessionStorage.setItem(recoveryKey, JSON.stringify({ runId })); }
    catch { error = `${error}${error ? " " : ""}浏览器无法保留恢复入口，再次刷新前请先恢复上下文；已保存的判断仍在本地数据库。`; }
    setAssessmentRecovery({ runId, result, busy: false, error });
  }, [recoveryKey]);

  const recoverAssessmentContext = useCallback(async (runId: string, confirmed: AssessmentResponse | null, revision: number) => {
    recoveryController.current?.abort();
    const controller = new AbortController();
    recoveryController.current = controller;
    setAssessmentRecovery({ runId, result: confirmed, busy: true, error: "" });
    try {
      const read = async (view: "run" | "assessments") => {
        const response = await fetch(`${api}&view=${view}&runId=${encodeURIComponent(runId)}`, { credentials: "same-origin", cache: "no-store", signal: controller.signal });
        const body = await response.json();
        if (!response.ok) throw new Error(body?.error?.code === "stale_run" ? "原候选版本已过期。历史判断已保留，请重新查找候选；旧判断不会计入新版本。" : body?.error?.message || "无法读取原候选上下文。");
        return body;
      };
      const [recoveredRun, recoveredAssessments] = await Promise.all([read("run"), read("assessments")]);
      if (controller.signal.aborted) return;
      if (!validResponse({ kind: "run", run: recoveredRun }) || recoveredRun.id !== runId || recoveredRun.stale !== false || !recoveredRun.query || !["product", "creator"].includes(recoveredRun.query.direction) || recoveredRun.query.source !== "first" || recoveredRun.market !== "it" || recoveredRun.subject?.id !== recoveredRun.query.subjectId || !validAssessmentResponse(recoveredAssessments) || recoveredAssessments.runId !== runId) throw new Error("原候选或人工评审响应不完整，请重新恢复上下文。");
      if (revision !== viewRevision.current) {
        setAssessmentRecovery({ runId, result: recoveredAssessments, busy: false, error: "筛选已改变，已保存判断的上下文尚未切回。需要时可恢复原候选。" });
        return;
      }
      const restored = recoveredRun as MatchRun;
      previousContext.current = `${restored.query.direction}|${restored.market}`;
      setDirection(restored.query.direction); setSource(restored.query.source); setSelected(restored.subject);
      setSearch(""); setQuery(""); setOffset(0); setRun(restored); setPacket(null); setChangeMessage("");
      setAssessments(recoveredAssessments); setAssessmentError("");
      try { sessionStorage.removeItem(recoveryKey); } catch {}
      setAssessmentRecovery(null);
    } catch (failure) {
      if (!controller.signal.aborted) setAssessmentRecovery({ runId, result: confirmed, busy: false, error: failure instanceof Error ? failure.message : "无法连接本地服务以恢复原候选。" });
    }
  }, [api, recoveryKey]);

  useEffect(() => () => { recoveryController.current?.abort(); recoveryStartupDone.current = false; }, []);
  useEffect(() => {
    if (!profiles || recoveryStartupDone.current) return;
    recoveryStartupDone.current = true;
    try {
      const raw = sessionStorage.getItem(recoveryKey);
      if (!raw) return;
      const saved: unknown = JSON.parse(raw);
      if (!saved || typeof saved !== "object" || !("runId" in saved) || typeof saved.runId !== "string" || !saved.runId || saved.runId.length > 100) return;
      void recoverAssessmentContext(saved.runId, null, viewRevision.current);
    } catch { /* Command storage errors remain separate from confirmed, read-only context recovery. */ }
  }, [profiles, recoveryKey, recoverAssessmentContext]);

  useEffect(() => { const timer = window.setTimeout(() => setQuery(search.trim()), 250); return () => window.clearTimeout(timer); }, [search]);
  useEffect(() => {
    const next = `${direction}|${market}`;
    if (next !== previousContext.current) {
      previousContext.current = next;
      clearResult(); setSelected(null); setSearch(""); setQuery(""); setOffset(0);
    }
  }, [direction, market, clearResult]);

  useEffect(() => {
    if (commands.error?.code === "stale_run") {
      setRun(current => current ? { ...current, stale: true } : current);
      setPacket(null);
    } else if (profiles && (commands.error?.code === "assessment_conflict" || commands.error?.code === "revision_conflict")) {
      setAssessmentRefresh(value => value + 1);
    } else if (commands.error?.code === "revision_conflict") {
      clearResult(); setSelected(null); setRefresh(value => value + 1);
    }
  }, [commands.error?.code, clearResult, profiles]);

  useEffect(() => {
    const controller = new AbortController();
    const current = ++readSequence.current;
    setLoading(true); setReadError("");
    const params = new URLSearchParams({ view: direction === "product" ? "products" : "creators", q: query, offset: String(offset), limit: String(PAGE_SIZE) });
    if (market !== "all") params.set("market", market);
    void (async () => {
      try {
        const response = await fetch(`${api}&${params}`, { credentials: "same-origin", cache: "no-store", signal: controller.signal });
        const body = await response.json();
        if (!response.ok) throw new Error(body.error?.message || "无法读取匹配资料，请重试。");
        if (!Array.isArray(body.items) || typeof body.total !== "number") throw new Error("资料响应不完整，请重新读取。");
        if (controller.signal.aborted || current !== readSequence.current) return;
        setPage(body); setPageKey(listKey);
      } catch (failure) {
        if (!controller.signal.aborted && current === readSequence.current) setReadError(failure instanceof Error ? failure.message : "无法连接本地服务。");
      } finally { if (!controller.signal.aborted && current === readSequence.current) setLoading(false); }
    })();
    return () => controller.abort();
  }, [api, direction, market, query, offset, refresh, listKey]);

  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      try {
        const response = await fetch(`${api}&view=stats`, { credentials: "same-origin", cache: "no-store", signal: controller.signal });
        const body = await response.json();
        if (!response.ok) throw new Error(body.error?.message || "规模信息暂时无法读取。");
        if (body.mode !== (offline ? "imported-offline" : "synthetic-local") || typeof body.products !== "number" || typeof body.creators !== "number") throw new Error("规模信息响应不完整。");
        if (!controller.signal.aborted) { setStats(body); setStatsError(""); }
      } catch (failure) { if (!controller.signal.aborted) setStatsError(failure instanceof Error ? failure.message : "规模信息暂时无法读取。"); }
    })();
    return () => controller.abort();
  }, [api, offline, refresh]);

  const isCurrent = Boolean(run && selected && run.query.direction === direction && run.query.source === source && run.query.subjectId === selected.id && (market === "all" || run.market === market));
  const visibleRun = isCurrent ? run : null;
  const visiblePacket = packet && (packet.runId === visibleRun?.id || (!run && !selected)) ? packet : null;
  const assessmentRunId = profiles && visibleRun && !visibleRun.stale ? visibleRun.id : null;
  const visibleAssessments = assessments?.runId === assessmentRunId ? assessments : null;
  const selectablePage = pageKey === listKey ? page : null;
  const reading = loading || search.trim() !== query;

  useEffect(() => {
    setAssessments(current => current?.runId === assessmentRunId ? current : null); setAssessmentError("");
    if (!assessmentRunId) { setAssessmentsLoading(false); return; }
    const controller = new AbortController();
    setAssessmentsLoading(true);
    void (async () => {
      try {
        const response = await fetch(`${api}&view=assessments&runId=${encodeURIComponent(assessmentRunId)}`, { credentials: "same-origin", cache: "no-store", signal: controller.signal });
        const body = await response.json();
        if (!response.ok) {
          if (body?.error?.code === "stale_run" && !controller.signal.aborted) setRun(current => current?.id === assessmentRunId ? { ...current, stale: true } : current);
          throw new Error(body?.error?.message || "无法读取人工判断，请重新读取。");
        }
        if (!validAssessmentResponse(body) || body.runId !== assessmentRunId) throw new Error("人工评审响应不完整，请重新读取。");
        if (!controller.signal.aborted) setAssessments(body);
      } catch (failure) { if (!controller.signal.aborted) setAssessmentError(failure instanceof Error ? failure.message : "无法读取人工判断。"); }
      finally { if (!controller.signal.aborted) setAssessmentsLoading(false); }
    })();
    return () => controller.abort();
  }, [api, assessmentRunId, assessmentRefresh]);

  const acceptResponse = (result: MatchingResponse | null, revision: number, restore = false) => {
    if (!result) return;
    setRefresh(value => value + 1);
    if (result.kind === "assessment") {
      notify("人工判断已确认保存到本地；不改变发送权限。");
      if (profiles && revision === viewRevision.current && result.result.runId === run?.id && selected) {
        setAssessments(result.result);
      } else if (profiles) {
        rememberAssessmentRecovery(result.result.runId, result.result, revision !== viewRevision.current ? "筛选已改变，已保存判断的上下文尚未切回。" : "");
        if (revision === viewRevision.current && (restore || !run || !selected)) void recoverAssessmentContext(result.result.runId, result.result, revision);
      }
      return;
    }
    if (revision !== viewRevision.current) { notify("筛选已改变，本次结果已保存。请按当前条件重新查找候选。"); return; }
    if (result.kind === "run") {
      if (restore) {
        setDirection(result.run.query.direction); setSource(result.run.query.source); setSelected(result.run.subject);
        previousContext.current = `${result.run.query.direction}|${result.run.market}`;
        if (!offline) dispatch({ type: "market", market: result.run.market });
      }
      setRun(result.run); setPacket(null); setChangeMessage("");
      notify(profiles ? `已完成 ${result.run.candidates.length} 个候选的画像分析，未调用模型。` : `找到 ${result.run.candidates.length} 个有依据的候选，未调用模型。`);
    } else if (result.kind === "packet") {
      setPacket(result.packet);
      notify(`已准备 ${result.packet.candidates} 个商品的关系资料包，未调用模型。`);
      window.setTimeout(() => packetRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
    } else {
      if (restore) {
        setDirection("product"); setSelected(result.product);
        previousContext.current = `product|${result.product.market}`;
        if (!offline) dispatch({ type: "market", market: result.product.market });
      } else setSelected(current => current?.id === result.product.id ? result.product : current);
      setRun(current => current ? { ...current, stale: true } : current);
      setPacket(null); setChangeMessage(result.message);
      notify("示例商品条件已更新，请重新召回查看变化。");
    }
  };
  const recall = async () => {
    if (!selected || commands.blocked) return;
    const revision = viewRevision.current;
    const result = await commands.submit({ type: "recall", query: { direction, subjectId: selected.id, source, limit: 20 } });
    acceptResponse(result, revision);
  };
  const prepare = async (creatorId: string) => {
    if (!visibleRun || visibleRun.stale || commands.blocked) return;
    if (visiblePacket?.creatorId === creatorId) { packetRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }); return; }
    const revision = viewRevision.current;
    setReviewingId(creatorId);
    const result = await commands.submit({ type: "prepare_review", runId: visibleRun.id, creatorId });
    setReviewingId("");
    acceptResponse(result, revision);
  };
  const changeProduct = async (change: Extract<MatchingCommand, { type: "demo_change" }>["change"]) => {
    if (offline || !selected || !isProduct(selected) || commands.blocked) return;
    const revision = viewRevision.current;
    acceptResponse(await commands.submit({ type: "demo_change", productId: selected.id, expectedRevision: selected.commercialRevision, change }), revision);
  };
  const assess = async (candidate: MatchCandidate, label: AssessmentLabel, note: string, expectedRevision: number) => {
    if (!profiles || !visibleRun || visibleRun.stale || !visibleAssessments || assessmentsLoading || assessmentError || commands.blocked) return;
    const revision = viewRevision.current;
    setSavingPair(`${candidate.creator.id}:${candidate.product.id}`);
    const response = await commands.submit({ type: "assess_candidate", runId: visibleRun.id, creatorId: candidate.creator.id, productId: candidate.product.id, label, note, expectedRevision });
    setSavingPair("");
    acceptResponse(response, revision);
  };

  return <div className="min-w-0">
    <PageHeading title={profiles ? "画像分析" : "匹配工作台"} description={profiles ? "以已有画像中的类目与经营表现分析商品和达人，自动整理适配依据与推进顺序。" : offline ? "用意大利已有商品与达人证据，验证双向召回和有限上下文。" : "从万级资料中找到有依据的合作机会，把少量方案交给关系 Agent。"} action={<div className="flex flex-wrap items-center gap-2"><Link href="/creators" className="inline-flex items-center gap-2 rounded-lg border border-gray-300 bg-white px-3 py-2 text-xs font-medium text-gray-700 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300"><Icon name="users" className="size-4" />达人库</Link><Button variant="outline" size="sm" onClick={() => go("opportunities")}><Icon name="grid" className="size-4" />回到界面演示</Button></div>} />
    <div className="mb-5 flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
      <div className="w-full sm:w-72"><Field label="测试数据集"><Select value={dataset} disabled={commands.busy} onChange={event => { const next = new URL(window.location.href); next.searchParams.set("mode", "matching"); next.searchParams.set("dataset", event.target.value); window.location.assign(next.toString()); }}><option value="italy-profiles">意大利画像分析 · 一发</option><option value="italy">意大利真实数据 · 二发回放</option><option value="demo">合成演示数据</option></Select></Field></div>
      <p className="max-w-lg text-xs leading-5 text-gray-500">{profiles ? "以已有画像为分析基础，商品与达人双向查找；每项结论可展开查看依据。" : offline ? "仅在独立测试库中查询历史资料。真实身份、联系状态和商业条件仍需核实。" : "合成数据用于体验匹配流程与模拟条件变化。"}</p>
    </div>
    <div className="mb-6 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-brand-100 bg-brand-50 px-4 py-3 dark:border-brand-900 dark:bg-brand-500/10">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-xs"><span className="font-medium text-brand-600 dark:text-brand-300">{profiles ? "意大利画像 · 自动分析" : offline ? "意大利历史数据 · 离线测试" : "本地示例 · 仅匹配与准备"}</span><span className="text-gray-600 dark:text-gray-400">{stats ? `${stats.products.toLocaleString()} 个商品 · ${stats.creators.toLocaleString()} 位达人${profiles ? "" : ` · ${stats.evidence.toLocaleString()} 条同款证据`}` : "正在读取资料规模…"}</span><span className="font-medium text-brand-600 dark:text-brand-300">模型 Token：{stats ? stats.billedTokens : "—"}</span></div>
      <span className="text-xs text-gray-400">{stats?.matchingVersion || "结构召回"} · 模型未调用 · 不会发送消息</span>
    </div>
    {offline && stats?.dataset && <div className="mb-5 rounded-xl border border-gray-200 bg-white p-4 dark:border-gray-800 dark:bg-white/[0.015]">
      <div className="flex flex-wrap items-center justify-between gap-2"><p className="text-sm font-medium text-gray-700 dark:text-gray-200">{profiles ? "本次分析口径" : stats.dataset.label}</p><span className="text-xs text-gray-400">导入于 {date(stats.dataset.importedAt)}（北京时间）</span></div>
      <p className="mt-2 text-xs leading-6 text-gray-500">{profiles ? "按已有多类目分析适配性，用同一来源口径内的销量与播放表现补充排序。画像时间不阻塞分析，价格带与内容形式仅作参考。" : "这是已知同款关系的离线回放，可验证检索是否正确；尚不能证明 AI 选人质量或当前可联系性。"}</p>
      {!profiles && stats.dataset.warnings.length > 0 && <p className="mt-1 text-xs leading-6 text-gray-500">{stats.dataset.warnings.find(warning => warning.startsWith("历史快照：")) || stats.dataset.warnings[0]}</p>}
      <details className="mt-2 text-xs text-gray-500"><summary className="cursor-pointer py-1">查看数据范围、历史记录与来源</summary><ul className="mt-2 list-disc space-y-1.5 pl-4 leading-6">{(profiles ? stats.dataset.warnings : stats.dataset.warnings.slice(1)).map((warning, index) => <li key={index}>{warning}</li>)}</ul><div className="mt-3 space-y-1 border-t border-gray-100 pt-3 text-gray-400 dark:border-gray-800">{stats.dataset.sourceRefs.map(ref => <p key={ref} className="break-all leading-5">{ref}</p>)}</div></details>
    </div>}
    {statsError && <div className="mb-4"><Notice tone="warning"><div className="flex flex-wrap items-center gap-3">{statsError}<Button size="sm" variant="outline" onClick={() => setRefresh(value => value + 1)}>重新读取</Button></div></Notice></div>}
    {profiles && assessmentRecovery && <div className="mb-5" role="status"><Notice tone={assessmentRecovery.error ? "warning" : "info"}>
      <p className="font-medium">人工判断已经保存{assessmentRecovery.busy ? "，正在恢复原候选与评审统计…" : "，候选上下文尚未恢复。"}</p>
      {assessmentRecovery.error && <p className="mt-1">{assessmentRecovery.error}</p>}
      {assessmentRecovery.result && <p className="mt-1 text-xs">已确认结果：适合 {assessmentRecovery.result.summary.suitable} 对 · 不适合 {assessmentRecovery.result.summary.unsuitable} 对 · 资料不足 {assessmentRecovery.result.summary.insufficient} 对。</p>}
      <p className="mt-1 text-xs">恢复只读取原候选和已保存判断，不会再次提交评审。</p>
      <Button className="mt-3" size="sm" variant="outline" disabled={assessmentRecovery.busy || commands.blocked} onClick={() => void recoverAssessmentContext(assessmentRecovery.runId, assessmentRecovery.result, viewRevision.current)}>{assessmentRecovery.busy ? "正在恢复上下文…" : "恢复评审上下文"}</Button>
    </Notice></div>}
    {commands.error && <div className="mb-5" role="alert"><Notice tone="warning"><p>{commands.error.message}</p>{commands.error.retry && <p className="mt-1 text-xs">将沿用原请求编号，避免创建重复结果。</p>}<div className="mt-3 flex flex-wrap items-center gap-3">{commands.error.retry ? <Button size="sm" variant="outline" disabled={commands.busy} onClick={async () => { const revision = viewRevision.current; acceptResponse(await commands.retry(), revision, !selected); }}>{commands.busy ? "正在恢复…" : "恢复原请求"}</Button> : commands.error.code !== "REQUEST_STORAGE" && <Button size="sm" variant="outline" onClick={commands.dismiss}>知道了</Button>}<details className="text-xs"><summary className="cursor-pointer">查看诊断编号</summary><p className="mt-2 break-all font-mono">{commands.error.code}{commands.error.retry && ` · ${commands.error.retry.requestId}`}</p></details></div></Notice></div>}

    <div className="grid min-w-0 gap-6 lg:grid-cols-[280px_minmax(0,1fr)] xl:grid-cols-[340px_minmax(0,1fr)]">
      <div className="min-w-0 space-y-5">
        <Card title={profiles ? "选择分析对象" : "从哪里开始"} subtitle={profiles ? "选择商品或达人，一次生成候选与依据。" : "先选择一个商品或达人，再查找候选。"}>
          <div className="space-y-4 p-4 sm:p-5">
            <fieldset disabled={commands.blocked} aria-label="匹配方向" className={commands.blocked ? "opacity-60" : ""}><Tabs items={[{ value: "product", label: "商品找达人" }, { value: "creator", label: "达人找商品" }]} value={direction} onChange={value => { if (!commands.blocked) { clearResult(); setSelected(null); setDirection(value); } }} /></fieldset>
            <Field label="市场" hint={offline ? "此数据集固定为意大利，保留演示模式的市场偏好。" : undefined}><Select value={market} disabled={commands.blocked || offline} onChange={event => { clearResult(); setSelected(null); dispatch({ type: "market", market: event.target.value as "all" | MatchMarket }); }}><option value="all">全部市场</option><option value="mx">墨西哥 · MX</option><option value="br">巴西 · BR</option><option value="it">意大利 · IT</option></Select></Field>
            <Field label={direction === "product" ? "选择商品" : "选择达人"}><div className="relative"><Icon name="search" className="pointer-events-none absolute left-3 top-3.5 size-4 text-gray-400" /><Input type="search" maxLength={120} value={search} disabled={commands.blocked} placeholder={direction === "product" ? "搜索商品名称或 PID" : "搜索达人名称或身份"} className="!pl-9" onChange={event => { clearResult(); setSelected(null); setOffset(0); setSearch(event.target.value); }} /></div></Field>
            <div className="min-h-32" aria-busy={reading}>
              {readError ? <div className="rounded-xl bg-warning-50 p-4 text-sm leading-6 text-warning-700 dark:bg-warning-900/10 dark:text-warning-400"><p>{readError}</p><Button size="sm" variant="outline" className="mt-3" onClick={() => setRefresh(value => value + 1)}>重新读取资料</Button></div>
                : reading || !selectablePage ? <p role="status" className="py-10 text-center text-sm text-gray-400">正在读取{direction === "product" ? "商品" : "达人"}…</p>
                  : !selectablePage.items.length ? <p className="py-10 text-center text-sm leading-6 text-gray-500">没有符合搜索的资料。<br />试试其他关键词或市场。</p>
                    : <div className="max-h-[380px] space-y-2 overflow-y-auto pr-1">{selectablePage.items.map(subject => <button key={subject.id} type="button" aria-pressed={selected?.id === subject.id} disabled={commands.blocked} onClick={() => { clearResult(); setSelected(subject); }} className={`flex w-full items-start gap-3 rounded-xl border p-3 text-left transition disabled:opacity-50 ${selected?.id === subject.id ? "border-brand-300 bg-brand-50 dark:border-brand-800 dark:bg-brand-500/10" : "border-transparent hover:bg-gray-50 dark:hover:bg-white/5"}`}><SubjectPicture subject={subject} size={40} offline={offline} /><span className="min-w-0 flex-1"><span className="line-clamp-2 block text-sm font-medium leading-5 text-gray-800 dark:text-gray-200">{subjectName(subject)}</span><span className="mt-1 block text-xs leading-5 text-gray-400">{subject.market.toUpperCase()} · {profiles ? categoryNames(subject) : isProduct(subject) ? money(subject.priceMinor, subject.currency) : formatNames(subject.formats)}</span></span>{selected?.id === subject.id && <Icon name="check" className="mt-1 size-4 shrink-0 text-brand-500" />}</button>)}</div>}
            </div>
            {selectablePage && !readError && <div className="flex items-center justify-between gap-2 text-xs text-gray-400"><span>{selectablePage.total.toLocaleString()} 项{selectablePage.total > 0 && ` · ${offset + 1}–${Math.min(offset + PAGE_SIZE, selectablePage.total)}`}</span><div className="flex gap-1"><Button variant="ghost" size="sm" className="!px-2 !py-1.5" disabled={commands.blocked || reading || offset === 0} onClick={() => { clearResult(); setSelected(null); setOffset(Math.max(0, offset - PAGE_SIZE)); }}>上一页</Button><Button variant="ghost" size="sm" className="!px-2 !py-1.5" disabled={commands.blocked || reading || offset + PAGE_SIZE >= selectablePage.total} onClick={() => { clearResult(); setSelected(null); setOffset(offset + PAGE_SIZE); }}>下一页</Button></div></div>}
            <div className="border-t border-gray-100 pt-4 dark:border-gray-800"><Field label="候选来源" hint={profiles ? "一发按已有画像分析；同品销量保留在二发数据集中。" : offline && source !== "second" ? "一发画像尚不完整，探索候选需补充依据；候选来源不代表发送次数。" : "一发与二发表示机会来源，不代表发送次数。"}><Select value={source} disabled={commands.blocked || profiles} onChange={event => { clearResult(); setSource(event.target.value as RecallQuery["source"]); }}><option value="all">合并来源 · 去重后查找</option><option value="first">一发 · 新的适配机会</option><option value="second">二发 · 精确同款证据</option></Select></Field></div>
            <Button className="w-full" disabled={commands.blocked || !selected || Boolean(readError) || reading} onClick={recall}><Icon name="search" className="size-4" />{commands.busy && !reviewingId ? "正在处理…" : visibleRun?.stale ? "重新查找候选" : profiles ? "开始画像分析" : "查找候选"}</Button>
          </div>
        </Card>
        {offline && selected && !isProduct(selected) && <IdentityContext market={selected.market} oecId={selected.oecId} externalId={selected.externalIdentity?.id} sourceHandle={selected.name} />}
        {profiles && selected && <Card title="选中资料的类目" subtitle={categoryNames(selected)}><div className="p-4 sm:p-5"><CategoryProvenance subject={selected} profileAnalysis /><details className="mt-2 text-xs text-gray-500"><summary className="cursor-pointer py-1">查看类目依据与时间</summary><div className="mt-2"><CategoryProvenance subject={selected} detailed profileAnalysis /></div></details></div></Card>}
        {!offline && selected && isProduct(selected) && <Card><details className="p-4 sm:p-5"><summary className="cursor-pointer text-sm font-medium text-gray-700 dark:text-gray-200">增量更新演示</summary><p className="mt-3 text-xs leading-6 text-gray-500">只改变本地示例商品的价格或方案库存。更新后重新召回，观察新候选和旧结果失效。</p><div className="mt-3 grid grid-cols-2 gap-2"><Button size="sm" variant="outline" disabled={commands.blocked} onClick={() => changeProduct("lower_price")}>模拟降价</Button><Button size="sm" variant="outline" disabled={commands.blocked} onClick={() => changeProduct("raise_price")}>模拟涨价</Button><Button size="sm" variant="outline" disabled={commands.blocked} onClick={() => changeProduct("offer_unavailable")}>模拟缺货</Button><Button size="sm" variant="outline" disabled={commands.blocked} onClick={() => changeProduct("offer_available")}>模拟恢复库存</Button></div><p className="mt-3 text-xs text-gray-400">商业版本 v{selected.commercialRevision} · 语义版本 v{selected.semanticRevision}</p>{changeMessage && <p role="status" className="mt-3 text-xs leading-5 text-brand-600 dark:text-brand-300">{changeMessage}</p>}</details></Card>}
      </div>

      <div className="min-w-0 space-y-5">
        <Card title={visibleRun ? `${profiles ? "分析结果" : "候选机会"} · ${visibleRun.candidates.length}` : profiles ? "分析结果" : "候选机会"} subtitle={selected ? subjectName(selected) : "选择左侧资料，开始一次有依据的匹配。"} action={visibleRun && <Pill tone="neutral">模型未调用</Pill>}>
          {!visibleRun ? <EmptyState title={selected ? profiles ? "已选好，开始画像分析" : "已选好，开始查找候选" : "一次聚焦一个经营问题"} description={selected ? profiles ? "索引召回后，自动整理类目交集和已有经营表现；本步骤不消耗模型 Token。" : "结构索引先缩小范围，保留适配理由和事实缺口；此步骤不消耗模型 Token。" : "为一个商品找到适合的达人，或为一位达人找到少量值得推进的商品。"} />
            : <div className="space-y-4 p-4 sm:p-5">
              {visibleRun.stale && <Notice tone="warning">资料或匹配版本已变化，这批候选与评审已不再适用于当前版本。请重新查找候选。</Notice>}
              {profiles && <AutoAnalysisSummary run={visibleRun} />}
              {direction==="creator"&&visibleRun.candidates.length>0&&<div className="flex flex-wrap items-center justify-between gap-3 border-b border-gray-100 pb-4 dark:border-gray-800"><p className="text-xs leading-5 text-gray-500">为这位达人合并最多 5 个商品，整理一份关系资料包。</p><Button size="sm" disabled={commands.blocked||visibleRun.stale||visibleRun.candidates.every(c=>c.readiness==="suppressed")} onClick={()=>prepare(visibleRun.subject.id)}><Icon name="agent" className="size-4"/>{visiblePacket?"查看关系资料包":"整理该达人资料包"}</Button></div>}
              {!visibleRun.candidates.length && <EmptyState title="本次没有符合条件的候选" description={profiles ? "类目缺失或来源冲突时会暂停类目推荐。可以查看选中资料的来源，或更换其他商品、达人。" : "可以更换商品、达人或候选来源；没有同款证据并不代表达人从未带过该商品。"} />}
              {visibleRun.candidates.map(candidate => {
                const item = visibleAssessments?.items.find(item => item.creatorId === candidate.creator.id && item.productId === candidate.product.id);
                const pair = `${candidate.creator.id}:${candidate.product.id}`;
                return <CandidateCard key={`${visibleRun.id}:${pair}`} candidate={candidate} direction={direction} disabled={commands.blocked || visibleRun.stale} reviewing={commands.busy && reviewingId === candidate.creator.id} packetReady={visiblePacket?.creatorId === candidate.creator.id} onPrepare={() => prepare(candidate.creator.id)} offline={offline} profileAnalysis={profiles} assessment={profiles && item ? <AssessmentPanel key={`${visibleRun.id}:${pair}:${item.revision}`} item={item} disabled={commands.blocked || visibleRun.stale || assessmentsLoading || Boolean(assessmentError)} saving={commands.busy && savingPair === pair} onSave={(label, note, revision) => assess(candidate, label, note, revision)} /> : undefined} />;
              })}
              {profiles && !visibleRun.stale && <details className="rounded-xl border border-gray-200 p-4 text-xs text-gray-500 dark:border-gray-800">
                <summary className="cursor-pointer py-1 text-sm font-medium">可选人工复核与已保存判断</summary>
                <p className="mt-2 leading-6">自动分析已完成。可在候选卡片底部补充人工判断，独立保留作后续评测。</p>
                {assessmentsLoading && <p role="status" className="mt-2 text-xs text-gray-400">正在读取已保存的人工判断…</p>}
                {assessmentError && <div className="mt-2"><p>{assessmentError}</p><Button size="sm" variant="outline" className="mt-2" onClick={() => setAssessmentRefresh(value => value + 1)}>重新读取人工判断</Button></div>}
                {visibleAssessments && <div className="mt-3"><AssessmentSummary result={visibleAssessments} /></div>}
              </details>}
              <details className="rounded-lg bg-gray-50 px-3 py-2 text-xs text-gray-400 dark:bg-gray-800/40"><summary className="cursor-pointer py-1">本次召回记录与数据范围</summary><div className="mt-2 flex flex-wrap gap-x-5 gap-y-2 pb-1"><span>{visibleRun.cacheHit ? "复用有效缓存" : "新建召回结果"}</span><span>召回返回 {visibleRun.diagnostics.rowsFetched.toLocaleString()} 行</span><span>用时 {visibleRun.diagnostics.durationMs.toFixed(1)} ms</span><span>候选上限 {visibleRun.diagnostics.candidateLimit}</span><span>模型调用 {visibleRun.diagnostics.llmCalls} 次</span></div>{visibleRun.diagnostics.truncated && <p className="mt-2 leading-5">已按本次候选上限截取结果，当前列表不是全库的全部匹配。</p>}{visibleRun.warnings.map((warning, index) => <p key={`${index}-${warning}`} className="mt-2 leading-5">{warning}</p>)}<p className="mt-2 break-all leading-5">{visibleRun.matchingVersion} · {date(visibleRun.createdAt)} · {visibleRun.id}</p></details>
            </div>}
        </Card>
        {visiblePacket && <section ref={packetRef} className="scroll-mt-24"><Card title={profiles ? "关系资料包" : offline ? "离线证据评审包" : "关系评审包"} subtitle="同一位达人的少量商品集中评审，避免重复读取整库和全部关系历史。" action={<Pill tone="brand">已准备 · 未调用模型</Pill>}><div className="space-y-4 p-4 sm:p-5"><div className="grid grid-cols-3 gap-3"><div className="rounded-xl bg-gray-50 p-3 dark:bg-gray-800/50"><p className="text-xs text-gray-400">商品方案</p><p className="mt-2 text-xl font-semibold text-gray-800 dark:text-gray-200">{visiblePacket.candidates}<span className="ml-1 text-xs font-normal text-gray-400">/ 最多 5</span></p></div><div className="rounded-xl bg-gray-50 p-3 dark:bg-gray-800/50"><p className="text-xs text-gray-400">上下文字符</p><p className="mt-2 text-xl font-semibold text-gray-800 dark:text-gray-200">{visiblePacket.characters.toLocaleString()}</p></div><div className="rounded-xl bg-gray-50 p-3 dark:bg-gray-800/50"><p className="text-xs text-gray-400">计费 Token</p><p className="mt-2 text-sm font-medium text-gray-700 dark:text-gray-300">待实际调用测量</p></div></div><p className="text-sm leading-6 text-gray-600 dark:text-gray-400">资料对象：<strong className="font-medium text-gray-800 dark:text-gray-200">{visibleRun?.candidates.find(candidate => candidate.creator.id === visiblePacket.creatorId)?.creator.name || visiblePacket.creatorId}</strong>。结构摘要包含关系控制、候选理由、已记录条件与证据引用；未核实字段保持未知，不将全部商品逐对交给模型。</p><Notice>{profiles ? "画像自动分析已完成，本资料包可供后续关系 Agent 使用；模型仍未调用，未生成或发送消息。字符数不等于模型 Token。" : "本次只准备模型输入。尚未进行 AI 决策、生成话术或发送消息；字符数不等于模型 Token。"}</Notice><details><summary className="cursor-pointer py-2 text-sm text-gray-500">查看实际结构摘要</summary><pre className="mt-2 max-h-96 overflow-y-auto whitespace-pre-wrap break-words rounded-xl bg-gray-50 p-4 text-xs leading-6 text-gray-600 dark:bg-gray-800/60 dark:text-gray-400">{JSON.stringify(visiblePacket.payload, null, 2)}</pre></details></div></Card></section>}
      </div>
    </div>
  </div>;
}
