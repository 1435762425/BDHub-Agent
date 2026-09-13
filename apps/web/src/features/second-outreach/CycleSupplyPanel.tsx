"use client";
import {useEffect,useState} from "react";
import type {CycleStatus} from "../../server/second-cycle/bridge";
const labels:Record<string,string>={missing_stock:"缺库存",missing_creatorPercent:"缺达人佣金",missing_publicPercent:"缺公开佣金",missing_endAt:"缺活动有效期",missing_available:"缺可用状态",stock_not_over_100:"库存未超过100",expiry_not_over_45_days:"有效期未超过45天",no_creator_commission_advantage:"无达人佣金优势",unavailable:"商品不可用"};
export default function CycleSupplyPanel(){const [data,setData]=useState<CycleStatus|null>(null),[error,setError]=useState(false);
 useEffect(()=>{const controller=new AbortController();fetch("/api/second-cycle",{signal:controller.signal,cache:"no-store"}).then(async r=>{if(!r.ok)throw new Error();return r.json();}).then(v=>{if(!controller.signal.aborted)setData(v);}).catch(()=>{if(!controller.signal.aborted)setError(true);});return()=>controller.abort();},[]);
 return <section className="mb-5 rounded-2xl border border-gray-200 bg-white p-5 dark:border-gray-800 dark:bg-gray-900" aria-label="二发供给底座">
 <h2 className="text-base font-semibold text-gray-900 dark:text-white">二发供给 · 本地准备</h2>
 <p className="mt-1 text-sm text-gray-500">货盘已接只读刷新；线索持续抓取、旧试点控制与真实发送尚未接通。</p>
 {error?<p role="status" className="mt-3 text-sm">暂时无法读取供给状态，未启动任何操作。</p>:!data?<p role="status" className="mt-3 text-sm">正在读取供给状态…</p>:data.available===false?<p className="mt-3 text-sm">尚未导入本地供给资料。</p>:<>
 <div className="mt-4 flex flex-wrap gap-x-6 gap-y-2 text-sm text-gray-700 dark:text-gray-200"><span>货盘方案 <strong>{data.offerCount??data.offers?.length??0}</strong></span><span>条件合格方案 <strong>{data.eligibleOfferCount??0}</strong></span><span>稳定关系 <strong>{data.relationships}</strong></span><span>来源线索 <strong>{data.sourceEdges}</strong></span><span>达人×商品机会 <strong>{data.opportunities}</strong></span><span>条件齐全达人 <strong>{data.eligibleUniqueCreators}</strong></span></div>
 <details className="mt-4 text-sm"><summary className="cursor-pointer text-brand-500">查看商品条件与待补信息</summary><ul className="mt-3 space-y-2 text-gray-600 dark:text-gray-300">{data.offers?.map((o,index)=><li key={`${o.offerKey||o.pid}-${index}`} className="break-words"><strong>{o.title||o.pid}</strong>（{o.catalogSource==="campaign"?"Campaign":o.catalogSource==="selected"?"已选商品":"历史源"}）：{o.assessment.eligible?"当前资料条件齐全，仍不代表可发送":o.assessment.reasons.map(r=>labels[r]||"资料需核对").join("、")}</li>)}</ul><p className="mt-3 text-xs text-gray-500">最多展示40条，优先已有线索商品。拟分配佣金不代表已建链；历史同品归属仍未核验。</p></details>
 </>}
 </section>;
}
