"use client";
import {useCallback,useEffect,useState} from "react";
import {Button,Card,Notice,Pill,StatTile} from "../bdhub/ui";
import {initialReviewAction,REPLY_ACTIONS} from "./reply-review-contracts";
import type {ReplyAction,ReplyReviewItem,ReplyReviewStatus} from "./reply-review-contracts";

const labels:Record<ReplyAction,string>={no_reply:"无需回复",sample_self_service:"样品/物流自助模板",
 collaboration_ack:"合作确认模板",link_usage:"唯一商品链接用法",human:"人工接管"};

function ReviewItem({item,templates,onChanged}:{item:ReplyReviewItem;templates:ReplyReviewStatus["templates"];onChanged:()=>void}){
 const [busy,setBusy]=useState(false),[message,setMessage]=useState("");
 const [correctAction,setCorrectAction]=useState<ReplyAction|"">(initialReviewAction(item.review));
 const [note,setNote]=useState(item.review?.note??""),[editing,setEditing]=useState(false);
 const post=async(body:Record<string,unknown>)=>{setBusy(true);setMessage("");try{const response=await fetch("/api/reply-review",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});if(!response.ok){const problem=await response.json().catch(()=>({})) as {error?:string};throw Error(problem.error);}onChanged();}catch(error){setMessage(error instanceof Error&&error.message==="jev_not_configured"?"Jev 尚未配置；当前只能用 DeepSeek 做影子分类。":"操作没有落账，请刷新后重试。");}finally{setBusy(false);}};
 const classify=(provider:"deepseek"|"jev")=>post({action:"classify",turnId:item.turnId,requestId:`web-${crypto.randomUUID()}`,provider});
 const submit=()=>correctAction&&post({action:"review_turn",turnId:item.turnId,expectedRevision:item.review?.revision??0,
  correctAction,note});
 const applyReview=()=>item.review&&item.operational.controlRevision!=null&&item.operational.pendingRevision!=null&&post({
  action:"apply_review",turnId:item.turnId,expectedReviewRevision:item.review.revision,
  expectedControlRevision:item.operational.controlRevision,expectedPendingRevision:item.operational.pendingRevision,
  requestId:`web-apply-${crypto.randomUUID()}`});
 const decision=item.decision;
 const compared=new Set(item.comparisons.map(row=>row.provider));
 const deepseek=item.comparisons.find(row=>row.provider==="deepseek")?.decision;
 const selectedTemplate=correctAction?templates[correctAction]?.text:null;
 const editor=<div className="space-y-2">
  <p className="text-xs text-gray-500">请独立选择这条 turn 的业务标准动作。模型即使一致也不会替你预选；提交后成为两个 provider 共用的人工真值。</p>
  <textarea value={note} maxLength={2000} onChange={event=>setNote(event.target.value)} placeholder="可选：写下判断依据或参考答案" className="w-full rounded-lg border border-gray-300 bg-transparent p-2 text-xs dark:border-gray-700" rows={2}/>
  <div className="flex flex-wrap gap-2"><select value={correctAction} onChange={event=>setCorrectAction(event.target.value as ReplyAction|"")} className="rounded-lg border border-gray-300 bg-transparent px-2 text-xs dark:border-gray-700"><option value="" disabled>请选择标准动作</option>{REPLY_ACTIONS.map(action=><option key={action} value={action}>{labels[action]}</option>)}</select>
   <Button size="sm" disabled={busy||!correctAction} onClick={()=>void submit()}>{item.review?"保存新 revision":"确认标准动作"}</Button>{item.review&&<Button size="sm" variant="ghost" disabled={busy} onClick={()=>setEditing(false)}>取消修改</Button>}</div>
  {selectedTemplate&&<p className="rounded-md border border-gray-200 bg-white p-2 text-xs leading-5 text-gray-700 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200">该动作固定回复：{selectedTemplate}</p>}
 </div>;
 return <article className="space-y-3 rounded-xl border border-gray-200 p-4 dark:border-gray-800">
  <div className="flex flex-wrap items-center gap-2"><Pill tone={item.historical?"neutral":"brand"}>{item.historical?"历史样本":"当前消息"}</Pill>
   <span className="text-xs text-gray-500">消息 {item.messageId} · 关联 PID {item.episodes.map(row=>row.pid).join(" / ")||"未确定"}</span></div>
  <p className="whitespace-pre-wrap text-sm leading-6 text-gray-800 dark:text-gray-200">{item.format==="text"?item.text:"[图片或附件：必须人工处理]"}</p>
  {deepseek?.meaningZh&&<p className="text-xs leading-5 text-gray-600 dark:text-gray-300">中文理解：{deepseek.meaningZh}</p>}
  {item.comparisons.length>0&&<div className="grid gap-2 md:grid-cols-2">{item.comparisons.map(row=><div key={row.classificationId} className="rounded-lg bg-gray-50 p-3 text-xs dark:bg-white/[0.04]"><div className="flex flex-wrap items-center gap-2"><Pill tone={row.provider==="jev"?"brand":"neutral"}>{row.provider==="jev"?"Jev":"DeepSeek"}</Pill><strong>{labels[row.action]}</strong><span className="text-gray-500">{(row.confidence*100).toFixed(0)}% · {row.intentCode}</span></div>{row.decision.humanReason&&<p className="mt-1 text-warning-600">{row.decision.humanReason}</p>}<details className="mt-2"><summary className="cursor-pointer text-gray-500">查看模型依据</summary><div className="mt-2 space-y-1 leading-5 text-gray-500"><p>中文理解：{row.decision.meaningZh}</p><p>引用原文：{row.decision.evidenceQuotes.join(" / ")}</p>{row.decision.templateText&&<p>固定模板候选：{row.decision.templateText}</p>}</div></details></div>)}</div>}
  {(!compared.has("deepseek")||!compared.has("jev"))&&<div className="flex flex-wrap gap-2">{!compared.has("deepseek")&&<Button size="sm" variant="outline" disabled={busy} onClick={()=>void classify("deepseek")}>{busy?"分类中…":"DeepSeek 影子分类"}</Button>}{!compared.has("jev")&&<Button size="sm" variant="outline" disabled={busy} onClick={()=>void classify("jev")}>{busy?"分类中…":"Jev 对照分类"}</Button>}</div>}
  {decision&&<div className="space-y-2 rounded-lg border border-gray-200 p-3 dark:border-gray-800">
   {item.review&&!editing?<div className="space-y-2"><Notice tone="info">标准动作 v{item.review.revision}：{labels[item.review.correct_action]}{item.review.note?` · ${item.review.note}`:""}</Notice><Button size="sm" variant="ghost" disabled={busy} onClick={()=>setEditing(true)}>修改标准动作（追加 revision）</Button>
    {item.operational.application?<Notice tone="info">已应用到业务状态：{item.operational.application.state}（基于标准动作 v{item.operational.application.reviewRevision}）。自动回复 {item.operational.application.automaticReply?"已开启":"未开启"}，平台写入 {item.operational.application.platformWrites}。后续修订真值不会偷偷回滚已应用状态。</Notice>
     :item.operational.applicable?<Button size="sm" variant="outline" disabled={busy} onClick={()=>void applyReview()}>{item.review.correct_action==="no_reply"?"应用：安全解除本次回复冻结":item.review.correct_action==="human"?"应用：转入人工案件":"应用：生成固定模板候选（不发送）"}</Button>
     :<p className="text-xs text-gray-400">{item.operational.reason==="historical_sample"?"历史样本只用于评测，不修改业务状态。":"当前没有可应用的待处理案件；本条只保留为评测真值。"}</p>}
   </div>:editor}
  </div>}
  {message&&<Notice tone="warning">{message}</Notice>}
 </article>;
}

export default function ReplyReviewPanel(){
 const [data,setData]=useState<ReplyReviewStatus|null>(null),[error,setError]=useState(false),[revision,setRevision]=useState(0);
 const load=useCallback(async()=>{try{const response=await fetch("/api/reply-review",{cache:"no-store"});if(!response.ok)throw Error();setData(await response.json());setError(false);}catch{setError(true);}},[]);
 useEffect(()=>{void load();},[load,revision]);
 return <Card title="回复预演与训练" subtitle="真实入站 turn → 关联外发 episode → 五动作影子分类 → 你判正确/不正确。这里不会发送回复。">
  <div className="space-y-4 p-5">
   {error?<Notice tone="warning">暂时无法读取回复审核账本。</Notice>:!data?<p className="text-sm text-gray-500">正在读取真实回复…</p>:<>
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-5"><StatTile label="入站 turn" value={data.counts.turns} hint="每条消息独立保存"/><StatTile label="外发 episode" value={data.counts.episodes} hint="当时的 PID 与链接"/><StatTile label="已关联" value={data.counts.linkedTurns} hint="有候选外发上下文"/><StatTile label="已分类" value={data.counts.classified} hint="影子判断，不执行"/><StatTile label="已审核" value={data.counts.reviewed} hint="人工确认的正反例" brand/></div>
    <div className="rounded-xl bg-gray-50 p-3 text-xs leading-5 text-gray-600 dark:bg-white/[0.04] dark:text-gray-300">
     同集对照 <strong>{data.evaluation.paired}</strong> 条 · 一致 <strong>{data.evaluation.agreements}</strong> · 分歧 <strong>{data.evaluation.disagreements}</strong>
     {data.evaluation.agreementRate!=null&&<> · 一致率 <strong>{(data.evaluation.agreementRate*100).toFixed(1)}%</strong></>}
     <span className="ml-3">待你审核 <strong>{data.evaluation.pendingReview}</strong> 条。</span>
     {data.evaluation.reviewedTurns>0&&<span className="ml-3">DeepSeek 准确率 {data.evaluation.providers.deepseek.accuracy==null?"—":`${(data.evaluation.providers.deepseek.accuracy*100).toFixed(1)}%`}；Jev 准确率 {data.evaluation.providers.jev.accuracy==null?"—":`${(data.evaluation.providers.jev.accuracy*100).toFixed(1)}%`}。</span>}
    </div>
    <p className="text-xs leading-5 text-gray-500">这里保留每 {data.processingIntervalSeconds/3600} 小时一轮的影子评测口径，用于 DeepSeek/Jev 同集对照，不是 Agent 外发时间。真实 Agent 开关与 15:00–16:00 集中窗口在会话工作台单独管理。</p>
    <div className="space-y-3">{data.items.map(item=><ReviewItem key={`${item.turnId}-${item.classificationId}-${item.review?.revision??0}`} item={item} templates={data.templates} onChanged={()=>setRevision(value=>value+1)}/>)}</div>
   </>}
  </div>
 </Card>;
}
