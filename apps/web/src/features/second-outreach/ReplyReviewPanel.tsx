"use client";
import {useCallback,useEffect,useState} from "react";
import {Button,Card,Notice,Pill,StatTile} from "../bdhub/ui";
import {REPLY_ACTIONS} from "./reply-review-contracts";
import type {ReplyAction,ReplyReviewItem,ReplyReviewStatus} from "./reply-review-contracts";

const labels:Record<ReplyAction,string>={no_reply:"无需回复",sample_self_service:"样品/物流自助模板",
 collaboration_ack:"合作确认模板",link_usage:"唯一商品链接用法",human:"人工接管"};

function ReviewItem({item,onChanged}:{item:ReplyReviewItem;onChanged:()=>void}){
 const [busy,setBusy]=useState(false),[message,setMessage]=useState("");
 const [correctAction,setCorrectAction]=useState<ReplyAction>("human"),[note,setNote]=useState("");
 const post=async(body:Record<string,unknown>)=>{setBusy(true);setMessage("");try{const response=await fetch("/api/reply-review",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});if(!response.ok){const problem=await response.json().catch(()=>({})) as {error?:string};throw Error(problem.error);}onChanged();}catch(error){setMessage(error instanceof Error&&error.message==="jev_not_configured"?"Jev 尚未配置；当前只能用 DeepSeek 做影子分类。":"操作没有落账，请刷新后重试。");}finally{setBusy(false);}};
 const classify=()=>post({action:"classify",turnId:item.turnId,requestId:`web-${crypto.randomUUID()}`,provider:"deepseek"});
 const submit=(verdict:"correct"|"incorrect")=>post({action:"review",classificationId:item.classificationId,
  expectedRevision:item.review?.revision??0,verdict,correctAction:verdict==="correct"?null:correctAction,note});
 const decision=item.decision;
 return <article className="space-y-3 rounded-xl border border-gray-200 p-4 dark:border-gray-800">
  <div className="flex flex-wrap items-center gap-2"><Pill tone={item.historical?"neutral":"brand"}>{item.historical?"历史样本":"当前消息"}</Pill>
   <span className="text-xs text-gray-500">消息 {item.messageId} · 关联 PID {item.episodes.map(row=>row.pid).join(" / ")||"未确定"}</span></div>
  <p className="whitespace-pre-wrap text-sm leading-6 text-gray-800 dark:text-gray-200">{item.format==="text"?item.text:"[图片或附件：必须人工处理]"}</p>
  {!decision?<Button size="sm" variant="outline" disabled={busy} onClick={()=>void classify()}>{busy?"分类中…":"DeepSeek 影子分类"}</Button>:<div className="space-y-2 rounded-lg bg-gray-50 p-3 dark:bg-white/[0.04]">
   <div className="flex flex-wrap items-center gap-2"><Pill tone={decision.action==="human"?"warning":"success"}>{labels[decision.action]}</Pill><span className="text-xs text-gray-500">置信度 {(decision.confidence*100).toFixed(0)}% · {decision.intentCode}</span></div>
   <p className="text-xs leading-5 text-gray-600 dark:text-gray-300">中文理解：{decision.meaningZh}</p>
   {decision.templateText&&<p className="rounded-md border border-gray-200 bg-white p-2 text-xs leading-5 text-gray-700 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200">候选固定回复：{decision.templateText}</p>}
   {decision.humanReason&&<p className="text-xs text-warning-600">人工原因：{decision.humanReason}</p>}
   {item.review?<Notice tone="info">已审核：{item.review.verdict==="correct"?"判断正确":`应改为「${labels[item.review.correct_action??"human"]}」`}{item.review.note?` · ${item.review.note}`:""}</Notice>:<div className="space-y-2">
    <textarea value={note} maxLength={2000} onChange={event=>setNote(event.target.value)} placeholder="可选：写下判断依据或参考答案" className="w-full rounded-lg border border-gray-300 bg-transparent p-2 text-xs dark:border-gray-700" rows={2}/>
    <div className="flex flex-wrap gap-2"><Button size="sm" disabled={busy} onClick={()=>void submit("correct")}>判断正确</Button>
     <select value={correctAction} onChange={event=>setCorrectAction(event.target.value as ReplyAction)} className="rounded-lg border border-gray-300 bg-transparent px-2 text-xs dark:border-gray-700">{REPLY_ACTIONS.map(action=><option key={action} value={action}>{labels[action]}</option>)}</select>
     <Button size="sm" variant="outline" disabled={busy} onClick={()=>void submit("incorrect")}>判断不对，按所选动作</Button></div>
   </div>}
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
    <p className="text-xs leading-5 text-gray-500">每 {data.processingIntervalSeconds/3600} 小时集中处理一次；DeepSeek 目前只做影子分类，Jev 尚未配置。固定模板也只是候选，<strong>自动回复始终关闭</strong>。</p>
    <div className="space-y-3">{data.items.map(item=><ReviewItem key={`${item.turnId}-${item.classificationId}-${item.review?.revision??0}`} item={item} onChanged={()=>setRevision(value=>value+1)}/>)}</div>
   </>}
  </div>
 </Card>;
}
