"use client";
import {useEffect,useMemo,useState} from "react";
import {Button,Card,Dialog,Input,Notice,Pill,TextArea} from "../bdhub/ui";
import type {TemplateLibraryState,SendTemplate} from "../../server/template-library/bridge";
import type {SendController} from "./useSendBatch";

const DEFAULT_BODY="Ciao @{creator_handle}! Per {product_name} abbiamo una commissione del {creator_commission}% per te 👏 Ti va di creare un nuovo video o LIVE?";
const sample=(body:string)=>body.replaceAll("{creator_handle}","creator_demo").replaceAll("{product_name}","questo prodotto").replaceAll("{creator_commission}","13");

export default function SendTemplateCard({controller}:{controller:SendController}){
 const {data:send,draft,setDraft,busy}=controller;
 const active=Boolean(send?.batch&&["prepared","start_failed","starting","running","stop_requested","waiting_reconciliation","local_capacity_reached","platform_rejected","material_refresh_record_failed"].includes(send.batch.state));
 const [library,setLibrary]=useState<TemplateLibraryState|null>(null),[error,setError]=useState("");
 const [selected,setSelected]=useState(send?.config.template??"standard"),[open,setOpen]=useState(false);
 const [name,setName]=useState(""),[body,setBody]=useState(DEFAULT_BODY),[editing,setEditing]=useState<SendTemplate|null>(null),[saving,setSaving]=useState(false);
 const load=async()=>{const r=await fetch("/api/template-library",{cache:"no-store"}).catch(()=>null);if(!r?.ok){setError("暂时无法读取模板库。");return;}setLibrary(await r.json());setError("");};
 useEffect(()=>{void load();},[]);
 useEffect(()=>{if(send?.config.template)setSelected(send.config.template);},[send?.config.template]);
 const current=useMemo(()=>library?.sendTemplates.find(item=>item.id===selected)??send?.templates.find(item=>item.id===selected)??null,[library,selected,send]);
 const startCreate=()=>{setEditing(null);setName("");setBody(DEFAULT_BODY);setOpen(true);};
 const startEdit=(item:SendTemplate)=>{setEditing(item);setName(item.name);setBody(item.bodyIt);setOpen(true);};
 const save=async()=>{setSaving(true);setError("");try{const payload=editing?{action:"update_send",templateId:editing.id,expectedRevision:editing.revision,name,bodyIt:body}:{action:"create_send",requestId:`web-${crypto.randomUUID()}`,name,bodyIt:body};const r=await fetch("/api/template-library",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});if(!r.ok)throw Error();const value:TemplateLibraryState=await r.json();setLibrary(value);const created=value.sendTemplates.find(item=>item.name===name&&!item.builtIn);if(created)setSelected(created.id);setOpen(false);}catch{setError("模板没有保存：请确认商品名称和达人佣金参数都存在，且只使用三个受控参数。");}finally{setSaving(false);}};
 const archive=async(item:SendTemplate)=>{setSaving(true);try{const r=await fetch("/api/template-library",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action:"archive_send",templateId:item.id,expectedRevision:item.revision})});if(!r.ok)throw Error();setLibrary(await r.json());setSelected("standard");}catch{setError("正在使用或状态已变化的模板不能归档；请先为本批选择并保存另一套模板。");}finally{setSaving(false);}};
 const templates=library?.sendTemplates??send?.templates??[];
 return <>
  <Card title="二发话术模板" subtitle="模板正文无需保存批次即可查看；设为本批后再保存设置，才会生成新的逐达人预览。" action={<Button size="sm" onClick={startCreate}>新建自定义模板</Button>}>
   <div className="grid gap-4 p-5 lg:grid-cols-[280px_1fr]">
    <div className="max-h-96 space-y-2 overflow-y-auto pr-1">{templates.map(item=><button type="button" key={item.id} onClick={()=>setSelected(item.id)} className={`w-full rounded-lg border px-3 py-3 text-left ${selected===item.id?"border-brand-400 bg-brand-50 dark:bg-brand-500/10":"border-gray-200 dark:border-gray-700"}`}><div className="flex items-center justify-between gap-2"><strong className="text-sm">{item.name}</strong><Pill tone={item.builtIn?"neutral":"brand"}>{item.builtIn?"内置":`自定义 v${item.revision}`}</Pill></div><p className="mt-1 line-clamp-2 text-xs leading-5 text-gray-500">{item.description}</p></button>)}</div>
    <div className="space-y-3">{current?<><div className="flex flex-wrap items-center justify-between gap-2"><div><p className="text-sm font-medium text-gray-800 dark:text-white">{current.name}</p><p className="mt-1 text-xs text-gray-500">可用参数：{current.parameters.map(value=>`{${value}}`).join(" · ")}</p></div><div className="flex gap-2">{!current.builtIn&&<Button size="sm" variant="outline" onClick={()=>startEdit(current as SendTemplate)}>编辑</Button>}{!current.builtIn&&<Button size="sm" variant="ghost" disabled={saving||send?.config.template===current.id} onClick={()=>void archive(current as SendTemplate)}>归档</Button>}<Button size="sm" disabled={!draft||busy||active} onClick={()=>{if(draft)setDraft({...draft,template:current.id});}}>设为本批</Button></div></div><div className="rounded-xl bg-gray-50 p-4 text-sm leading-6 text-gray-700 dark:bg-gray-800 dark:text-gray-200"><p className="whitespace-pre-wrap">{current.bodyIt}</p><div className="my-3 border-t border-gray-200 dark:border-gray-700"/><p className="text-xs text-gray-500">参数预览</p><p className="mt-1 whitespace-pre-wrap">{sample(current.bodyIt)}</p></div>{draft?.template===current.id&&<Notice tone="success">已选为本批模板；保存批次设置后生成新 preview hash。</Notice>}</>:<p className="text-sm text-gray-500">正在读取模板…</p>}</div>
   </div>
   {error&&<div className="px-5 pb-5"><Notice tone="warning">{error}</Notice></div>}
  </Card>
  <Dialog open={open} onClose={()=>setOpen(false)} title={editing?"编辑自定义二发模板":"新建自定义二发模板"} description="只允许三个参数；商品名称和达人佣金必须存在。">
   <div className="space-y-4"><label className="block text-sm font-medium">模板名称<Input className="mt-2" value={name} maxLength={60} onChange={e=>setName(e.target.value)}/></label><label className="block text-sm font-medium">意大利语正文<TextArea className="mt-2" value={body} maxLength={600} onChange={e=>setBody(e.target.value)}/></label><div className="flex flex-wrap gap-2">{["{creator_handle}","{product_name}","{creator_commission}"].map(value=><Button key={value} size="sm" variant="outline" onClick={()=>setBody(text=>`${text}${text.endsWith(" ")||!text?"":" "}${value}`)}>{value}</Button>)}</div><div className="rounded-lg bg-gray-50 p-3 text-sm leading-6 dark:bg-gray-800">{sample(body)}</div><Button disabled={saving||!name.trim()||!body.trim()} onClick={()=>void save()}>{saving?"保存中…":"保存模板"}</Button></div>
  </Dialog>
 </>;
}
