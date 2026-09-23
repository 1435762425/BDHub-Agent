"use client";
import {useCallback,useEffect,useState} from "react";
import {Button,Collapsible,Field,Input,Notice,Pill,Progress} from "../bdhub/ui";
import type {CatalogNamesState} from "../../server/catalog-names/bridge";

/**
 * The short-name step, kept to one line: how many are missing and one button to fill them.
 *
 * The model, its endpoint and the exact prompt are real facts an operator occasionally needs, so
 * they are shown — but folded away, because on a normal day none of it needs looking at.
 */
export default function ShortNames({market}:{market:string}){
 const [names,setNames]=useState<CatalogNamesState|null>(null);
 // "not read yet" is not "could not read".
 const [namesLoaded,setNamesLoaded]=useState(false);
 const reload=useCallback(async()=>{const r=await fetch(`/api/catalog-names?market=${encodeURIComponent(market)}`,{cache:"no-store"});if(!r.ok)throw Error();setNames(await r.json());setNamesLoaded(true);},[market]);
 useEffect(()=>{void reload().catch(()=>setNames(null));},[reload]);
 const [busy,setBusy]=useState(false);
 const [message,setMessage]=useState<string|null>(null);
 const [startedAt,setStartedAt]=useState<number|null>(null);
 const running=Boolean(names?.run?.running);
 // Poll briefly after a start too: the detached process has not written its first line yet.
 useEffect(()=>{const active=running||(startedAt!==null&&Date.now()-startedAt<30000);if(!active)return;const timer=setInterval(()=>void reload().catch(()=>{}),3000);return()=>clearInterval(timer);},[running,startedAt,reload]);
 const fill=useCallback(async()=>{
  setBusy(true);setMessage(null);
  try{
   const r=await fetch(`/api/catalog-names?market=${encodeURIComponent(market)}`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action:"prepare",market,all:true})});
   if(!r.ok){setMessage(r.status===409?"已经在生成中。":"暂时无法生成。");return;}
   setStartedAt(Date.now());
  }catch{setMessage("暂时无法生成。");}
  finally{setBusy(false);}
 },[market,reload]);
 if(!names)return <p className="text-sm text-gray-500">{namesLoaded?"暂时无法读取短名状态。":"读取中…"}</p>;
 return <div className="rounded-xl border border-gray-200 p-4 dark:border-gray-700">
  <div className="flex flex-wrap items-center justify-between gap-3">
   <p className="text-sm text-gray-700 dark:text-gray-200">商品短名（卡名来源）<span className="ml-2 text-xs text-gray-400">已就绪 {names.ready.toLocaleString()} / {names.scope.toLocaleString()}</span></p>
   <div className="flex items-center gap-2">
    {names.missing===0?<Pill tone="success">已备齐</Pill>:<Pill tone={names.provider.ready?"warning":"neutral"}>{names.provider.ready?`缺 ${names.missing.toLocaleString()} 个`:"模型未就绪"}</Pill>}
    <Button size="sm" disabled={busy||running||!names.provider.ready||names.missing===0} onClick={()=>void fill()}>{running?"生成中…":"一键补全"}</Button>
   </div>
  </div>
  {running&&names.run&&<div className="mt-3"><Progress done={names.run.prepared} total={names.run.total} label="生成进度"/></div>}
  {message&&<Notice tone="info">{message}</Notice>}
  <div className="mt-3">
   <Collapsible label="模型与提示词" count={undefined}>
    <dl className="space-y-2 text-xs text-gray-500">
     <div><dt className="text-gray-400">模型</dt><dd className="font-mono">{names.provider.model} @ {names.provider.endpointHost}</dd></div>
     <div><dt className="text-gray-400">凭据</dt><dd>{names.provider.ready?"已就绪":"未配置，无法调用"}</dd></div>
     <div><dt className="text-gray-400">每批数量</dt><dd>{names.batchLimit} 个商品一次请求</dd></div>
     <div><dt className="text-gray-400">提示词（{names.promptRole}）</dt><dd className="mt-1 whitespace-pre-wrap rounded-lg bg-gray-50 p-3 leading-5 dark:bg-gray-800">{names.prompt||"—"}</dd></div>
    </dl>
   </Collapsible>
  </div>
 </div>;
}
