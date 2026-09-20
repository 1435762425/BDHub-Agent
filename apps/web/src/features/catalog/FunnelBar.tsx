"use client";
import type {FunnelStage} from "./funnel";

/**
 * The funnel strip. The operator asked for counts rather than detail, and for the page to read as
 * 采集 → 筛出 → 入池 → 建链。这里用一张流程表集中数量、口径和定位入口。
 */
export default function FunnelBar({stages,onJump}:{stages:FunnelStage[];onJump:(key:string)=>void}){
 return <div className="overflow-x-auto rounded-xl border border-gray-200 bg-white dark:border-gray-800 dark:bg-white/[0.025]"><table className="w-full min-w-[620px] text-left text-sm"><thead className="bg-gray-50 text-xs text-gray-500 dark:bg-gray-800"><tr><th className="px-4 py-3 font-medium">流程</th><th className="w-32 px-4 py-3 text-right font-medium">数量</th><th className="px-4 py-3 font-medium">判断口径</th><th className="w-24 px-4 py-3 text-right font-medium">定位</th></tr></thead><tbody>{stages.map((stage,index)=><tr key={`${stage.key}-${index}`} className="border-t border-gray-100 dark:border-gray-800"><td className="px-4 py-3 font-medium text-gray-700 dark:text-gray-200">{index+1}. {stage.label}</td><td className="px-4 py-3 text-right text-lg font-semibold tabular-nums text-gray-800 dark:text-white/90">{stage.value==null?"—":stage.value.toLocaleString()}</td><td className="px-4 py-3 text-xs leading-5 text-gray-500">{stage.hint}</td><td className="px-4 py-3 text-right"><button type="button" onClick={()=>onJump(stage.key)} className="text-xs font-medium text-brand-500 hover:text-brand-600">查看</button></td></tr>)}</tbody></table></div>;
}
