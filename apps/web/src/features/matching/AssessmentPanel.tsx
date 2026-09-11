"use client";

import { useState } from "react";
import { Button, Field, Pill, Select, TextArea } from "../bdhub/ui";
import type { AssessmentResponse } from "./contracts";

export type AssessmentItem = AssessmentResponse["items"][number];
export type AssessmentLabel = AssessmentItem["label"];
const labels = { suitable: "适合继续研究", unsuitable: "不适合", insufficient: "资料不足" };

export function validAssessmentResponse(value: unknown): value is AssessmentResponse {
  if (!value || typeof value !== "object") return false;
  const response = value as AssessmentResponse;
  if (typeof response.runId !== "string" || !response.runId || !Array.isArray(response.items) || !response.summary) return false;
  const pairs = new Set<string>();
  for (const item of response.items) {
    if (!item || typeof item.creatorId !== "string" || typeof item.productId !== "string" || ![null, "suitable", "unsuitable", "insufficient"].includes(item.label) || typeof item.note !== "string" || item.note.length > 1000 || !Number.isInteger(item.revision) || item.revision < 0 || !(item.reviewedAt === null || Number.isFinite(item.reviewedAt))) return false;
    const pair = `${item.creatorId}:${item.productId}`;
    if (pairs.has(pair)) return false;
    pairs.add(pair);
  }
  const summary = response.summary;
  if (![summary.total, summary.reviewed, summary.suitable, summary.unsuitable, summary.insufficient, summary.decided].every(number => Number.isInteger(number) && number >= 0)) return false;
  const suitable = response.items.filter(item => item.label === "suitable").length;
  const unsuitable = response.items.filter(item => item.label === "unsuitable").length;
  const insufficient = response.items.filter(item => item.label === "insufficient").length;
  const expectedRate = suitable + unsuitable ? suitable / (suitable + unsuitable) : null;
  const expectedCoverage = response.items.length ? (suitable + unsuitable + insufficient) / response.items.length : 0;
  return summary.total === response.items.length && summary.reviewed === summary.suitable + summary.unsuitable + summary.insufficient
    && summary.decided === summary.suitable + summary.unsuitable && summary.reviewed <= summary.total
    && summary.suitable === suitable && summary.unsuitable === unsuitable && summary.insufficient === insufficient
    && (expectedRate === null ? summary.suitabilityRate === null : typeof summary.suitabilityRate === "number" && Math.abs(summary.suitabilityRate - expectedRate) < 0.000001)
    && Number.isFinite(summary.coverage) && Math.abs(summary.coverage - expectedCoverage) < 0.000001;
}

export function AssessmentSummary({ result }: { result: AssessmentResponse }) {
  const { summary } = result;
  return <section aria-label="当前候选人工评审统计" className="rounded-xl border border-gray-200 p-4 dark:border-gray-800">
    <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="text-sm font-medium text-gray-800 dark:text-gray-200">人工评审</h3><Pill tone="neutral">仅当前候选</Pill></div>
    <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3">
      <div><p className="text-xs text-gray-400">评审覆盖</p><p className="mt-1 text-lg font-semibold text-gray-800 dark:text-gray-200">{summary.reviewed} <span className="text-xs font-normal text-gray-400">/ {summary.total}</span></p></div>
      <div><p className="text-xs text-gray-400">人工适配通过率</p><p className="mt-1 text-lg font-semibold text-gray-800 dark:text-gray-200">{summary.suitabilityRate === null ? <span className="text-sm font-medium">尚未评估</span> : `${(summary.suitabilityRate * 100).toFixed(1)}%`}</p><p className="mt-1 text-xs text-gray-400">适合 {summary.suitable} / 已判定 {summary.decided}</p></div>
      <div><p className="text-xs text-gray-400">资料不足</p><p className="mt-1 text-lg font-semibold text-gray-800 dark:text-gray-200">{summary.insufficient}<span className="ml-1 text-xs font-normal text-gray-400">对</span></p></div>
    </div>
    <p className="mt-3 text-xs leading-5 text-gray-500">不适合 {summary.unsuitable} 对 · 未评审 {summary.total - summary.reviewed} 对。通过率仅以“适合＋不适合”为分母；未评审和资料不足不算负例。这是人工判断，不代表模型准确率或发送权限。</p>
  </section>;
}

/** Parent keys this editor by run, pair and server revision; drafts never cross snapshots. */
export function AssessmentPanel({ item, disabled, saving, onSave }: {
  item: AssessmentItem; disabled: boolean; saving: boolean;
  onSave: (label: AssessmentLabel, note: string, revision: number) => Promise<void>;
}) {
  const [label, setLabel] = useState<AssessmentLabel>(item.label);
  const [note, setNote] = useState(item.note);
  const changed = label !== item.label || note.trim() !== item.note;
  return <details className="mt-3 border-t border-gray-100 pt-2 dark:border-gray-800">
    <summary className="cursor-pointer py-2 text-sm text-gray-600 dark:text-gray-300">人工判断 <span className="ml-2 text-xs text-gray-400">{item.label === null ? "尚未评审" : labels[item.label]}</span></summary>
    <div className="space-y-3 pb-1 pt-2">
      <p className="text-xs leading-5 text-gray-500">根据展示的历史资料，这个商品与达人是否值得继续研究？请自行判断并记录依据。</p>
      <Field label="适配判断"><Select value={label ?? ""} disabled={disabled} onChange={event => setLabel(event.target.value ? event.target.value as Exclude<AssessmentLabel, null> : null)}><option value="">尚未评审 / 撤销判断</option><option value="suitable">适合继续研究</option><option value="unsuitable">不适合</option><option value="insufficient">资料不足</option></Select></Field>
      <Field label="判断理由" hint={`${note.length} / 1000 字 · 建议说明适配依据或缺少的资料`}><TextArea value={note} maxLength={1000} rows={3} disabled={disabled} placeholder="填写你的判断依据" onChange={event => setNote(event.target.value)} /></Field>
      <div className="flex flex-wrap items-center justify-between gap-3"><p className="text-xs text-gray-400">{item.reviewedAt === null ? "尚未保存人工判断" : `保存于 ${new Date(item.reviewedAt).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false })}（北京时间）`}</p><Button size="sm" variant="outline" disabled={disabled || !changed || label === null && item.label === null} onClick={() => void onSave(label, note.trim(), item.revision)}>{saving ? "正在保存…" : label === null && item.label !== null ? "撤销已保存判断" : "保存判断"}</Button></div>
    </div>
  </details>;
}
