"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Button, Icon, Pill } from "../bdhub/ui";
import type { IdentityMarket, IdentitySourceResolution } from "./contracts";

interface IdentityContextProps {
  market:IdentityMarket;
  oecId?:string|null;
  externalId?:string;
  sourceHandle:string;
}
type ReadState = {key:string;result:IdentitySourceResolution|null;error:string};

function observedAt(value:string|null) {
  if (!value) return "未记录";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "未记录";
  return new Intl.DateTimeFormat("zh-CN", {
    year:"numeric", month:"2-digit", day:"2-digit", hour:"2-digit", minute:"2-digit",
    hour12:false, timeZone:"Asia/Shanghai",
  }).format(date);
}
function handle(value:string) { return `@${value.replace(/^@/, "")}`; }
function validResolution(value:unknown, market:IdentityMarket, oecId?:string|null):value is IdentitySourceResolution {
  if (!value || typeof value !== "object") return false;
  const body = value as IdentitySourceResolution;
  if (body.market !== market || body.queryKind !== (oecId ? "oec" : "external_source")
    || !["ready", "not_imported"].includes(body.datasetStatus)
    || !["verified", "pending", "not_found", "ambiguous", "not_imported"].includes(body.status)
    || body.resolutionRelation !== (oecId ? "canonical_oec" : "current_discovery_only")) return false;
  if (body.status !== "verified") return body.creator === null;
  const creator = body.creator;
  return Boolean(creator && typeof creator.creatorId === "string" && creator.creatorId
    && creator.market === market && typeof creator.oecId === "string"
    && (!oecId || creator.oecId === oecId)
    && (creator.currentHandle === null || typeof creator.currentHandle === "string"));
}

/** A read-only view of the identity library. Source names never become lookup keys. */
export function IdentityContext({ market, oecId, externalId, sourceHandle }:IdentityContextProps) {
  const lookupId = oecId || externalId;
  const key = `${market}:${oecId ? "oec" : "external"}:${lookupId || ""}`;
  const [state, setState] = useState<ReadState|null>(null);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    if (!lookupId) return;
    const controller = new AbortController();
    let disposed = false;
    let timedOut = false;
    const timer = window.setTimeout(() => { timedOut = true; controller.abort(); }, 12000);
    setState({ key, result:null, error:"" });
    const query = new URLSearchParams({ view:"source", market, [oecId ? "oecId" : "externalId"]:lookupId });
    void (async () => {
      try {
        const response = await fetch(`/api/creator-identities?${query}`, {
          credentials:"same-origin", cache:"no-store", signal:controller.signal,
        });
        const body:unknown = await response.json();
        if (!response.ok || !validResolution(body, market, oecId)) throw new Error("identity_read");
        if (!disposed) setState({ key, result:body, error:"" });
      } catch {
        if (!disposed) setState({ key, result:null, error:timedOut ? "身份资料读取超时。" : "暂时无法读取身份资料。" });
      } finally { window.clearTimeout(timer); }
    })();
    return () => { disposed = true; window.clearTimeout(timer); controller.abort(); };
  }, [key, lookupId, market, oecId, refresh]);

  const current = state?.key === key ? state : null;
  const result = current?.result;
  const creator = result?.creator;
  const loading = Boolean(lookupId && !result && !current?.error);
  const query = new URLSearchParams({ market });
  if (creator) query.set("creatorId", creator.creatorId);
  else if (oecId) query.set("q", oecId);
  else { query.set("status", "pending"); query.set("q", sourceHandle.replace(/^@/, "")); }
  const href = `/creators?${query}`;
  const statusText = !lookupId ? "未关联身份来源" : result?.status === "not_imported" ? "身份库尚未导入"
    : result?.status === "pending" ? "等待解析 OEC"
      : result?.status === "ambiguous" ? "存在多条身份记录"
        : result?.status === "not_found" ? oecId ? "该 OEC 尚未收录" : "尚无身份发现记录" : "";

  return <section aria-label="达人身份关联" aria-busy={loading} className="rounded-xl border border-gray-200 bg-white p-4 dark:border-gray-700 dark:bg-gray-900/40">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <p className="text-xs font-medium text-gray-700 dark:text-gray-200">达人身份</p>
      {creator && <Pill tone={creator.handleConflict ? "warning" : "brand"}>{creator.handleConflict ? "名字待核实" : "已有稳定 OEC"}</Pill>}
      {!creator && !loading && !current?.error && <Pill tone="neutral">{statusText}</Pill>}
    </div>
    <dl className="mt-3 space-y-2 text-xs leading-5">
      <div><dt className="text-gray-400">来源记录名称</dt><dd className="mt-0.5 break-all text-gray-600 dark:text-gray-300">{handle(sourceHandle)}</dd></div>
      {creator && <>
        <div><dt className="text-gray-400">{creator.handleConflict ? "最近记录的平台名称" : "最近核验的平台名称"}</dt><dd className="mt-0.5 break-all font-medium text-gray-800 dark:text-gray-200">{creator.currentHandle ? handle(creator.currentHandle) : "尚无可用名字"}</dd></div>
        <div><dt className="text-gray-400">OECID</dt><dd className="mt-0.5 break-all font-mono text-gray-700 dark:text-gray-300">{creator.oecId}</dd></div>
      </>}
    </dl>
    {loading && <p role="status" className="mt-3 text-xs text-gray-400">正在读取身份库…</p>}
    {current?.error && <div role="status" className="mt-3 flex flex-wrap items-center gap-2"><p className="text-xs text-gray-500">{current.error}</p><Button size="sm" variant="ghost" onClick={() => setRefresh(value => value + 1)}>重试</Button></div>}
    {creator && <p className="mt-3 text-xs leading-5 text-gray-400">名字核验于 {observedAt(creator.currentHandleVerifiedAt)}（北京时间）。{result?.resolutionRelation === "current_discovery_only" && "当前发现身份已记录，历史同品记录的归属仍待核验。"}</p>}
    {result?.status === "pending" && <p className="mt-3 text-xs leading-5 text-gray-500">线索已保留，取得 OEC 后继续跟进。</p>}
    {result?.status === "ambiguous" && <p className="mt-3 text-xs leading-5 text-gray-500">此来源关联多条记录，暂不指定当前达人。</p>}
    <Link href={href} className="mt-3 inline-flex items-center gap-1.5 text-xs font-medium text-brand-600 hover:text-brand-700 dark:text-brand-400"><Icon name="users" className="size-3.5"/>{creator ? "查看达人档案与改名记录" : oecId ? "在达人库查看" : "查看待解析线索"}</Link>
  </section>;
}
