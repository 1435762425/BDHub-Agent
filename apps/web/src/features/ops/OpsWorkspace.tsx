"use client";
import {useEffect,useState} from "react";
import {Button,Card,Field,Input,Notice,PageHeading} from "../bdhub/ui";
import type {LinkNamingConfig,LinkNamingState} from "../../server/link-naming/bridge";
import JobsPanel from "./JobsPanel";
import KalodataIdentityPanel from "./KalodataIdentityPanel";

/**
 * Cross-business configuration and operations. These are deliberately not on the 货盘 page: they do
 * not belong to one pipeline stage, they change rarely, and keeping them here leaves the catalogue
 * page reading as a single flow.
 */
export default function OpsWorkspace(){
 const [naming,setNaming]=useState<LinkNamingState|null>(null),[namingDraft,setNamingDraft]=useState<LinkNamingConfig|null>(null);
 const [namingBusy,setNamingBusy]=useState(false),[namingMessage,setNamingMessage]=useState<string|null>(null);
 useEffect(()=>{const controller=new AbortController();void(async()=>{try{const r=await fetch("/api/link-naming",{signal:controller.signal,cache:"no-store"});if(!r.ok)throw Error();const v:LinkNamingState=await r.json();if(!controller.signal.aborted){setNaming(v);setNamingDraft(v.config);}}catch{if(!controller.signal.aborted)setNaming(null);}})();return()=>controller.abort();},[]);
 async function namingAction(action:"preview"|"save"){if(!namingDraft)return;setNamingBusy(true);setNamingMessage(null);try{const r=await fetch("/api/link-naming",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action,config:namingDraft})});const v=await r.json();if(!r.ok){setNamingMessage(action==="save"?"保存被拒绝：模板未通过校验（检查占位符与数值范围）。":"预览失败，请检查模板占位符。");return;}setNaming(v);setNamingDraft(v.config);setNamingMessage(action==="save"?"命名模板已保存。之后新建的链接使用新模板；已冻结的建链意图与已有链接保持原名。":"预览已按当前输入更新。");}catch{setNamingMessage("暂时无法读取或保存命名设置。");}finally{setNamingBusy(false);}}
 return <div className="space-y-5">
  <PageHeading title="系统与运维" description="跨业务的账号身份、作业调度与命名配置。这里不放业务流水，也不授予发送权限。"/>
  <Notice>这一页的东西会同时影响多个业务：抓取身份决定达人线索能否查询，作业面板决定各步骤是手动还是定时，命名模板决定之后新建链接的卡名。货盘页只保留业务流水。</Notice>
  <KalodataIdentityPanel/>
  <JobsPanel/>
  <Card title="新建链接命名" subtitle="只影响之后新建的 TapLink。名字不参与建链去重，改模板不会重复建链；已冻结的建链意图与已有链接保持原名。"><div className="space-y-4 p-5">
  {!namingDraft&&<p className="text-sm text-gray-500">暂时无法读取命名设置，已有链接不受影响。</p>}
  {namingDraft&&<>
  <div className="grid gap-4 lg:grid-cols-2">
  <Field label="命名模板" hint="平台卡名上限 50 字；超长时按词逐级裁短短名。"><Input value={namingDraft.template} maxLength={120} onChange={e=>setNamingDraft({...namingDraft,template:e.target.value})}/></Field>
  <div className="grid grid-cols-3 gap-3">
  <Field label="指纹长度"><Input type="number" min={4} max={12} value={namingDraft.tailLength} onChange={e=>setNamingDraft({...namingDraft,tailLength:Number(e.target.value)})}/></Field>
  <Field label="总长上限"><Input type="number" min={10} max={50} value={namingDraft.maxLength} onChange={e=>setNamingDraft({...namingDraft,maxLength:Number(e.target.value)})}/></Field>
  <Field label="短名上限"><Input type="number" min={1} max={40} value={namingDraft.shortNameMaxLength} onChange={e=>setNamingDraft({...namingDraft,shortNameMaxLength:Number(e.target.value)})}/></Field>
  </div></div>
  <p className="text-xs text-gray-500">可用占位符：{naming?.placeholders.join("、")??"—"}。指纹由 PID、活动与达人佣金确定性生成，用于防重复与核对。</p>
  <div className="flex gap-2"><Button size="sm" variant="outline" disabled={namingBusy} onClick={()=>void namingAction("preview")}>预览</Button><Button size="sm" disabled={namingBusy} onClick={()=>void namingAction("save")}>{namingBusy?"处理中…":"保存模板"}</Button></div>
  {namingMessage&&<Notice tone="info">{namingMessage}</Notice>}
  {naming?.preview&&naming.preview.length>0&&<div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead className="border-b border-gray-200 text-xs text-gray-500 dark:border-gray-700"><tr>{["商品短名","达人佣金","渲染后的卡名","字数"].map(label=><th key={label} className="whitespace-nowrap px-3 py-3 font-medium">{label}</th>)}</tr></thead><tbody>{naming.preview.map(row=><tr key={row.pid} className="border-b border-gray-100 dark:border-gray-800"><td className="px-3 py-3">{row.shortName??"—"}</td><td className="whitespace-nowrap px-3 py-3">{row.creatorPercent}%</td><td className="px-3 py-3 font-mono text-xs">{row.name??<span className="text-warning-600">{row.error}</span>}</td><td className={`whitespace-nowrap px-3 py-3 ${row.length>row.limit?"text-warning-600":""}`}>{row.length}/{row.limit}</td></tr>)}</tbody></table></div>}
  <p className="text-xs text-gray-400">预览取自当前真正在等待建链的商品，不是示例数据。</p>
  </>}
  </div></Card>
 </div>;
}
