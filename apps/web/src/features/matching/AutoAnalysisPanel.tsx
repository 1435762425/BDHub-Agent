"use client";

import { Icon, Pill } from "../bdhub/ui";
import type { CandidateAnalysis, MatchCandidate, MatchRun, ProfileSignals } from "./contracts";
import type {ProfileMetricState} from "./profile-sync-contracts";

const tierLabels: Record<CandidateAnalysis["tier"], string> = {
  explicit_demand: "已有明确需求",
  same_product: "已有同品证据",
  category_aligned: "类目适配",
  exploration: "探索线索",
};

function count(value: number | null | undefined, unit = "") {
  return value === null || value === undefined ? "未记录" : `${value.toLocaleString("zh-CN")}${unit}`;
}
function gmv(signals: ProfileSignals | null) {
  if (signals?.gmvValue === null || signals?.gmvValue === undefined) return "未记录";
  if (!signals.gmvCurrency) return `${signals.gmvValue}（币种未记录）`;
  return new Intl.NumberFormat("zh-CN", {
    style: "currency", currency: signals.gmvCurrency, maximumFractionDigits: 2,
  }).format(Number(signals.gmvValue));
}

function observationDate(value:number) {return new Intl.DateTimeFormat("zh-CN",{month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false,timeZone:"Asia/Shanghai"}).format(value);}
function missingState(value:ProfileMetricState|undefined) {return value==="no_value"?"平台未提供值":value==="unauthorized"?"平台未授权":value==="error"?"本次未取得":value==="absent"?"本次未返回":"未记录";}

export function AutoAnalysisBadge({ analysis }: { analysis: CandidateAnalysis }) {
  return <Pill tone={analysis.tier === "exploration" ? "neutral" : "brand"}>{tierLabels[analysis.tier]}</Pill>;
}

export function AutoAnalysisDetails({ candidate }: { candidate: MatchCandidate }) {
  const { analysis } = candidate;
  const signals = analysis.profileSummary.signals;
  const origin = candidate.creator.profileOrigin;
  const metric=(value:number|null|undefined,key:"followers"|"unitsSold"|"avgViews",unit="")=>value===null||value===undefined?missingState(origin?.metricStates[key]):count(value,unit);
  return <div className="mt-4 space-y-3">
    <div className="rounded-xl bg-brand-50/70 px-4 py-3 dark:bg-brand-500/10">
      <p className="text-sm font-medium leading-6 text-gray-800 dark:text-gray-200">{analysis.summary}</p>
      {analysis.positiveEvidence.length > 0 && <ul className="mt-2 space-y-1.5 text-xs leading-5 text-gray-600 dark:text-gray-400">
        {analysis.positiveEvidence.map((evidence, index) => <li key={`${index}-${evidence}`} className="flex items-start gap-2"><Icon name="check" className="mt-0.5 size-3.5 shrink-0 text-brand-500" /><span>{evidence}</span></li>)}
      </ul>}
    </div>
    <dl className="grid grid-cols-2 gap-3 rounded-xl border border-gray-100 p-3 sm:grid-cols-4 dark:border-gray-800">
      {[
        ["画像销量", metric(signals?.unitsSold, "unitsSold", " 件")],
        ["平均播放", metric(signals?.avgViews, "avgViews")],
        ["GMV", signals?.gmvValue===null||signals?.gmvValue===undefined?missingState(origin?.metricStates.gmvValue):gmv(signals)],
        ["粉丝", metric(signals?.followers, "followers")],
      ].map(([label, value]) => <div key={label} className="min-w-0"><dt className="text-xs text-gray-400">{label}</dt><dd className="mt-1 break-words text-sm font-semibold text-gray-800 dark:text-gray-200">{value}</dd></div>)}
    </dl>
    {origin&&<p className="text-xs leading-5 text-gray-500">经营画像观测于 {observationDate(origin.observedAt)}（北京时间）。{origin.categoryMode==="historical_fallback"&&`类目沿用 ${candidate.creator.categoryFact?observationDate(candidate.creator.categoryFact.source.observedAt):"原"} 的历史资料。`}{origin.categoryMode==="unavailable"&&"本次类目未提供。"}{origin.categoryMode==="conflict_preserved"&&"原类目冲突保留，暂不用于类目适配。"}</p>}
    <p className="text-xs leading-5 text-gray-400">{signals?.periodLabel || "来源未记录统计周期"}。销量与播放仅在同一来源口径内辅助排序；GMV与粉丝用于理解经营规模。</p>
    {analysis.limitations.length > 0 && <details className="text-xs text-gray-500"><summary className="cursor-pointer py-1">查看结论边界</summary><ul className="mt-2 list-disc space-y-1 pl-4 leading-5">{analysis.limitations.map((limitation, index) => <li key={`${index}-${limitation}`}>{limitation}</li>)}</ul></details>}
  </div>;
}

export function AutoAnalysisSummary({ run }: { run: MatchRun }) {
  const categoryMatches = run.candidates.filter(candidate => candidate.features.categoryOverlap > 0).length;
  const observed = run.candidates.filter(candidate => candidate.analysis.profileSummary.signalStatus === "observed").length;
  return <section aria-label="当前候选自动分析统计" className="rounded-xl border border-brand-100 bg-brand-50/30 p-4 dark:border-brand-900 dark:bg-brand-500/5">
    <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="text-sm font-medium text-gray-800 dark:text-gray-200">{run.stale ? "上次画像分析" : "画像分析已完成"}</h3><Pill tone="neutral">{run.stale?"生成时的分析口径":"按当前分析口径"}</Pill></div>
    <div className="mt-3 grid grid-cols-3 gap-3">
      {[["类目有交集", categoryMatches], ["有经营数据", observed], ["模型调用", run.analysisPolicy.modelCalls]].map(([label, value]) => <div key={label}><p className="text-xs text-gray-400">{label}</p><p className="mt-1 text-xl font-semibold text-gray-800 dark:text-gray-200">{value}<span className="ml-1 text-xs font-normal text-gray-400">{label === "模型调用" ? "次" : "对"}</span></p></div>)}
    </div>
    <p className="mt-3 text-xs leading-6 text-gray-500">已自动整理本次 {run.candidates.length} 个候选的依据。按类目交集优先，经营数据补充顺序；结论来自结构化分析，不是合作成功率。</p>
    <details className="mt-1 text-xs text-gray-500"><summary className="cursor-pointer py-1">查看分析与排序规则</summary><ol className="mt-2 list-decimal space-y-1 pl-4 leading-5">{run.analysisPolicy.ranking.map((rule, index) => <li key={`${index}-${rule}`}>{rule}</li>)}</ol><p className="mt-2 leading-5">画像时间不作分析门槛；价格带、内容形式仅作参考。人工判断保留在下方，可按需补充。</p></details>
  </section>;
}
