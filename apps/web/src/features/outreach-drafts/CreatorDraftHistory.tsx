"use client";
import {useEffect,useRef,useState} from "react";
import {Button,Dialog,Icon,Pill} from "../bdhub/ui";
import DraftPanel,{draftStateLabels,draftStyleLabels} from "./DraftPanel";
import type {DraftList,DraftSummary} from "./contracts";

function date(value:string){const time=Date.parse(value);return Number.isFinite(time)?new Intl.DateTimeFormat("zh-CN",{month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false,timeZone:"Asia/Shanghai"}).format(time):"时间未记录";}
function validList(value:unknown):value is DraftList{const data=value as DraftList|null;return Boolean(data&&Array.isArray(data.drafts)&&data.drafts.length<=20&&data.drafts.every(item=>typeof item.id==="string"&&typeof item.packetId==="string"&&Object.hasOwn(draftStateLabels,item.status)&&item.executionBlocked===true));}

/** Persisted history is keyed by the canonical creator, independent of open runs. */
export default function CreatorDraftHistory({creatorId}:{creatorId:string}){
  const [result,setResult]=useState<{creatorId:string;data:DraftList}|null>(null),[error,setError]=useState<{creatorId:string;message:string}|null>(null),[refresh,setRefresh]=useState(0),[selection,setSelection]=useState<{creatorId:string;draft:DraftSummary}|null>(null);
  const generation=useRef(0);
  useEffect(()=>{
    const controller=new AbortController(),version=++generation.current;let reading=false;
    const read=async()=>{if(reading||document.visibilityState!=="visible")return;reading=true;
      try{const response=await fetch(`/api/outreach-drafts?view=creator&creatorId=${encodeURIComponent(creatorId)}`,{credentials:"same-origin",cache:"no-store",signal:controller.signal});const body:unknown=await response.json();if(!response.ok)throw new Error("暂时无法读取此达人的已保存草稿。");if(!validList(body))throw new Error("草稿历史响应不完整。");if(controller.signal.aborted||version!==generation.current)return;setResult({creatorId,data:body});setError(null);}
      catch(failure){if(!controller.signal.aborted&&version===generation.current)setError({creatorId,message:failure instanceof Error?failure.message:"暂时无法读取草稿历史。"});}finally{reading=false;}
    };
    void read();const timer=window.setInterval(()=>void read(),5000),visible=()=>void read();document.addEventListener("visibilitychange",visible);return()=>{controller.abort();window.clearInterval(timer);document.removeEventListener("visibilitychange",visible);};
  },[creatorId,refresh]);
  const data=result?.creatorId===creatorId?result.data:null,readError=error?.creatorId===creatorId?error.message:"",opened=selection?.creatorId===creatorId?selection.draft:null;
  return <section aria-label="已保存邀约草稿" className="rounded-xl border border-gray-200 bg-white p-4 dark:border-gray-700 dark:bg-gray-900/40">
    <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="text-xs font-medium text-gray-700 dark:text-gray-200">已保存邀约草稿{data?` · ${data.drafts.length}`:""}</h3><Button size="sm" variant="ghost" className="!px-2 !py-1" onClick={()=>setRefresh(value=>value+1)} aria-label="更新已保存草稿"><Icon name="arrow" className="size-3.5"/></Button></div>
    <p className="mt-2 text-xs leading-5 text-gray-400">按稳定身份查回最近 20 份记录，刷新页面后仍可查看。</p>
    {readError&&<p role="status" className="mt-3 text-xs text-error-500">{readError}</p>}
    {!data&&!readError?<p role="status" className="mt-3 text-xs text-gray-400">正在读取已保存记录…</p>:data&&!data.drafts.length?<p className="mt-3 text-xs text-gray-500">此达人尚无已保存草稿。</p>:data&&<div className="mt-3 max-h-72 space-y-2 overflow-y-auto">{data.drafts.map(draft=><button key={draft.id} type="button" onClick={()=>setSelection({creatorId,draft})} className="flex w-full flex-wrap items-center justify-between gap-2 rounded-lg border border-gray-100 px-3 py-2.5 text-left transition hover:bg-gray-50 dark:border-gray-800 dark:hover:bg-gray-800/40"><span><span className="block text-xs font-medium text-gray-700 dark:text-gray-200">{draftStyleLabels[draft.style]} · {date(draft.createdAt)}</span><span className="mt-1 block text-[11px] text-gray-400">查看原文、依据与用量</span></span><Pill tone={draft.status==="needs_review"||draft.status==="result_unknown"?"warning":"neutral"}>{draftStateLabels[draft.status]}</Pill></button>)}</div>}
    <Dialog open={Boolean(opened)} onClose={()=>setSelection(null)} title="已保存的邀约草稿" description="只读查看原版本，不重新生成或发送。" wide>{opened&&<DraftPanel key={opened.id} packetId={opened.packetId} initialDraftId={opened.id} stale={false} disabled readOnly/>}</Dialog>
  </section>;
}
