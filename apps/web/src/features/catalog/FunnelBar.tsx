"use client";
import type {FunnelStage} from "./funnel";

/**
 * The funnel strip. The operator asked for counts rather than detail, and for the page to read as
 * 采集 → 筛出 → 入池 → 建链, so the whole pipeline is one line at the top that never folds away.
 */
export default function FunnelBar({stages,onJump}:{stages:FunnelStage[];onJump:(key:string)=>void}){
 return <div className="rounded-2xl border border-gray-200 bg-white p-5 dark:border-gray-800 dark:bg-white/[0.025]">
  <div className="flex flex-wrap items-stretch gap-2">
   {stages.map((stage,index)=><div key={stage.key} className="flex min-w-0 flex-1 items-stretch gap-2">
    {index>0&&<span aria-hidden="true" className="self-center text-lg text-gray-300 dark:text-gray-600">→</span>}
    <button type="button" onClick={()=>onJump(stage.key)} title={stage.hint}
     className="min-w-32 flex-1 rounded-xl bg-gray-50 px-4 py-3 text-left transition hover:bg-brand-50 dark:bg-gray-800 dark:hover:bg-brand-500/10">
     <span className="block text-xs text-gray-500">{stage.label}</span>
     <span className="mt-1 block text-2xl font-semibold tabular-nums">{stage.value==null?"—":stage.value.toLocaleString()}</span>
     <span className="mt-0.5 block text-xs leading-5 text-gray-400">{stage.hint}</span>
    </button>
   </div>)}
  </div>
 </div>;
}
