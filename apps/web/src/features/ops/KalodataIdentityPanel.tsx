"use client";
import {useEffect,useState} from "react";
import {Button,Card,Field,Input,Notice,Pill} from "../bdhub/ui";
import type {KalodataIdentityConfig,KalodataIdentityState} from "../../server/kalodata-identity/bridge";

const verdicts:Record<string,{label:string;tone:"success"|"warning"|"brand"|"neutral";text:string}>={
 ready:{label:"抓取正常",tone:"success",text:"刚发出的达人列表请求拿到了数据。"},
 quota_exhausted:{label:"身份正常 · 今日额度已用完",tone:"success",text:"平台接受了这次请求并回了额度用尽，说明登录会话有效；额度按平台日重置。"},
 auth_required:{label:"登录已失效",tone:"warning",text:"平台拒绝了这个会话，需要用激活码重新获取身份，或先试刷新身份。"},
 business_rejected:{label:"平台拒绝了请求",tone:"warning",text:"会话可能有效，但平台没有接受这次查询内容；先把原始错误留下再判断。"},
 unreachable:{label:"未能完成请求",tone:"neutral",text:"请求没有走通，可能是网络或抓取器进程状态问题，不是身份结论。"},
};
const stamp=(value:number|null|undefined)=>value?new Date(value*1000).toLocaleString("zh-CN",{timeZone:"Asia/Shanghai",hour12:false}):"—";

export default function KalodataIdentityPanel(){
 const [data,setData]=useState<KalodataIdentityState|null>(null);
 const [draft,setDraft]=useState<KalodataIdentityConfig|null>(null);
 const [busy,setBusy]=useState<string|null>(null);
 const [message,setMessage]=useState<string|null>(null);
 async function refresh(signal?:AbortSignal){const r=await fetch("/api/kalodata-identity",{signal,cache:"no-store"});if(!r.ok)throw Error();const v:KalodataIdentityState=await r.json();setData(v);setDraft(v.config);}
 useEffect(()=>{const controller=new AbortController();void(async()=>{try{await refresh(controller.signal);}catch{if(!controller.signal.aborted)setData(null);}})();return()=>controller.abort();},[]);
 // Poll while the grabber's Chrome is open, so the page shows when the operator closes it.
 useEffect(()=>{if(!data?.login?.running)return;const timer=setInterval(()=>void refresh().catch(()=>{}),5000);return()=>clearInterval(timer);},[data?.login?.running]);
 async function act(action:"save"|"probe"|"activate"|"refresh"){
  setBusy(action);setMessage(null);
  try{
   const body=action==="save"||action==="activate"?{action,config:draft}:{action};
   const r=await fetch("/api/kalodata-identity",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
   const v=await r.json();
   if(!r.ok){setMessage(typeof v?.error==="string"?`请求被拒绝：${v.error}`:"请求被拒绝。");return;}
   setData(v);setDraft(v.config);
   const probe=v.lastProbe;
   setMessage(action==="save"?"激活码与测试用 PID 已保存（本机 0600 权限）。"
    :action==="probe"?(probe?.verdict==="quota_exhausted"?"身份正常：平台接受了请求并回了额度用尽，说明登录有效。":probe?.verdict==="ready"?"身份正常：已取到达人列表数据。":"测试完成，结论见下方状态。")
    :action==="activate"?"已打开专用 Chrome。请在窗口里完成扩展激活与登录，关闭窗口后回到这里点“测试身份”确认。"
    :"已打开专用 Chrome 刷新身份，关闭窗口后回到这里点“测试身份”确认。");
  }catch{setMessage("暂时无法读取或提交身份操作。");}
  finally{setBusy(null);}
 }
 const probe=data?.lastProbe,verdict=probe?verdicts[probe.verdict]:null;
 return <Card title="Kalodata 抓取身份" subtitle="达人线索按 PID 走 Kalodata 抓取。身份是否可用以真实抓取路径为准，不以抓取器自带的另一套检查器为准。"><div className="space-y-4 p-5">
  {!draft&&<p className="text-sm text-gray-500">暂时无法读取身份设置。</p>}
  {draft&&data&&<>
  <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
   <div className="rounded-xl bg-gray-50 p-4 dark:bg-gray-800"><p className="text-xs text-gray-500">身份结论</p><p className="mt-2 text-lg font-semibold">{verdict?verdict.label:"尚未测试"}</p>{probe&&<p className="mt-1 text-xs text-gray-400">{stamp(probe.checkedAt)} · {probe.elapsedSeconds} 秒</p>}</div>
   <div className="rounded-xl bg-gray-50 p-4 dark:bg-gray-800"><p className="text-xs text-gray-500">会话 Cookie</p><p className="mt-2 text-lg font-semibold">{data.cookie.present?"已存在":"缺失"}</p><p className="mt-1 text-xs text-gray-400">最近更新 {stamp(data.cookie.updatedAt)}</p></div>
   <div className="rounded-xl bg-gray-50 p-4 dark:bg-gray-800"><p className="text-xs text-gray-500">扩展代理</p><p className="mt-2 text-lg font-semibold">{data.proxy.configured?"已配置":"未配置"}</p><p className="mt-1 text-xs text-gray-400">未配置也能抓取，仅作记录</p></div>
   <div className="rounded-xl bg-gray-50 p-4 dark:bg-gray-800"><p className="text-xs text-gray-500">专用 Chrome</p><p className="mt-2 text-lg font-semibold">{data.login?.running?(data.login.mode==="activate"?"激活中":"刷新中"):"未打开"}</p><p className="mt-1 text-xs text-gray-400">{data.pythonReady?"抓取器运行时可用":"抓取器运行时缺失"}</p></div>
  </div>
  {verdict&&<Notice tone={verdict.tone==="neutral"?"warning":"info"}>{verdict.text}{probe?.detail?`（${probe.detail}）`:""}</Notice>}
  <div className="grid gap-4 lg:grid-cols-3">
   <div className="lg:col-span-2"><Field label="插件激活码（卡号）" hint="存在本机 config/kalodata-identity.json，权限 0600，不会写进日志。刷新身份时复用扩展里已保存的卡号，不用重填。"><Input type="password" autoComplete="off" value={draft.activationCode} maxLength={200} placeholder="填入扩展激活码" onChange={e=>setDraft({...draft,activationCode:e.target.value})}/></Field></div>
   <Field label="测试用 PID" hint="留空则自动取当前采集里的一条真实商品。"><Input value={draft.canaryPid} maxLength={19} placeholder={data.probePid||"自动选择"} onChange={e=>setDraft({...draft,canaryPid:e.target.value.replace(/\D/g,"")})}/></Field>
  </div>
  <div className="flex flex-wrap gap-2">
   <Button size="sm" variant="outline" disabled={busy!==null} onClick={()=>void act("save")}>{busy==="save"?"保存中…":"保存设置"}</Button>
   <Button size="sm" disabled={busy!==null} onClick={()=>void act("probe")}>{busy==="probe"?"测试中…":"测试身份"}</Button>
   <Button size="sm" variant="outline" disabled={busy!==null||!draft.activationCode||Boolean(data.login?.running)} onClick={()=>void act("activate")}>{busy==="activate"?"启动中…":"用激活码获取身份"}</Button>
   <Button size="sm" variant="outline" disabled={busy!==null||Boolean(data.login?.running)} onClick={()=>void act("refresh")}>{busy==="refresh"?"启动中…":"刷新身份"}</Button>
  </div>
  {message&&<Notice tone="info">{message}</Notice>}
  <p className="text-xs text-gray-400">“获取身份”和“刷新身份”会打开抓取器的专用 Chrome 窗口，需要你在这个窗口里完成扩展激活或登录；关掉窗口即结束。测试身份只发一次达人列表读取请求，不改平台数据。抓取器目录：<span className="font-mono">{data.loginDir}</span></p>
  </>}
 </div></Card>;
}
