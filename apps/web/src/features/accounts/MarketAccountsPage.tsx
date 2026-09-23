"use client";

import Link from "next/link";
import {useCallback,useEffect,useRef,useState} from "react";
import {Button,Card,Notice,PageHeading,Pill,Toggle,type Tone} from "../bdhub/ui";
import type {AccountRow,AccountStatus} from "../../server/market-accounts/bridge";

const responsibilityLabels:Record<string,string>={
  inbox_read:"收信监控",message_send:"IM 发送",agent_reply:"Agent 回复",
  oecid_find:"OECID 获取",creator_profile:"达人画像",
  catalog_read:"货盘读取",campaign:"Campaign",product_select:"商品选入",taplink:"TapLink 创建与核验",
};
const capabilityLabels:Record<string,string>={
  browser_session:"浏览器身份",partner_http:"HTTP 身份",institution_market:"机构与市场",
  im_identity:"IM 身份",im_card_search:"商品卡读取",catalog_read:"货盘读取",
  inbox_read:"收信监控",message_send:"IM 发送",agent_reply:"Agent 回复",
  oecid_find:"OECID 获取",creator_profile:"达人画像",campaign:"Campaign",
  product_select:"商品选入",taplink:"TapLink",link_create:"TapLink 创建",im_token:"IM Token",
  full_managed_catalog:"全托货盘读取",campaign_join:"Campaign 加入",
};
const stateLabels:Record<string,string>={
  queued:"已排队",draining:"等待当前任务结束",running:"维护中",completed:"已发布",
  needs_human:"需人工处理",failed_known:"维护失败",cancelled:"已取消",
};
const stageLabels:Record<string,string>={
  draining:"正在收齐当前任务",refreshing_identity:"正在静默刷新身份",
  browser_login:"浏览器已打开，正在自动登录",validating_capabilities:"正在验证浏览器、HTTP 与 IM",
  publishing_generation:"正在发布新身份代次",completed:"新身份已发布",
  needs_human:"等待人工处理",failed_known:"维护失败",
};
const errorLabels:Record<string,string>={
  project_identity_authority_required:"旧版本未接通项目身份，请重新点击“立即重登”",
  account_login_timeout:"登录窗口超时，请重新重登并在窗口内完成验证码",
  account_manual_verification_required:"需要在登录窗口完成验证码或二次验证",
  saved_credentials_missing:"没有可用于自动填写的已保存账号密码",
  account_browser_login_failed:"浏览器登录没有完成",
  account_refresh_failed:"静默刷新失败",
  account_maintenance_worker_exited:"维护进程已退出，请重新点击",
};
const date=(value:string|number|null|undefined)=>value?new Date(typeof value==="number"?value*1000:value).toLocaleString("zh-CN",{timeZone:"Asia/Shanghai",hour12:false}):"未取得";
const activeStates=new Set(["queued","draining","running"]);

function maintenancePresentation(row:AccountRow):{label:string;tone:Tone}{
  const state=row.maintenanceIntent?.state;
  if(state&&activeStates.has(state))return {label:stateLabels[state]??state,tone:"brand"};
  if(state==="needs_human")return {label:"需人工处理",tone:"warning"};
  if(state==="failed_known")return {label:"维护失败",tone:"error"};
  if(state==="completed")return {label:"代次已发布",tone:"success"};
  return row.identityGeneration?.state==="published"?{label:"代次已发布",tone:"success"}:{label:"尚未建立",tone:"neutral"};
}

function MaintenanceNotice({row}:{row:AccountRow}){
  const intent=row.maintenanceIntent;if(!intent)return null;
  const stage=intent.checkpoint?.stage;
  const message=stageLabels[stage??""]??stateLabels[intent.state]??intent.state;
  const error=intent.errorCode?(errorLabels[intent.errorCode]??intent.errorCode):null;
  return <Notice tone={intent.state==="completed"?"success":"warning"}>
    {intent.operation==="relogin"?"重登":"身份刷新"}：{message}{error?` · ${error}`:""}
  </Notice>;
}

export default function MarketAccountsPage({market,embedded=false}:{market:string;embedded?:boolean}){
  const [data,setData]=useState<AccountStatus|null>(null),[message,setMessage]=useState(""),[busy,setBusy]=useState<string|null>(null);
  const ids=useRef(new Map<string,string>());
  const load=useCallback(async()=>{const response=await fetch(`/api/market-accounts?market=${encodeURIComponent(market)}`,{cache:"no-store"});if(!response.ok)throw Error();setData(await response.json());},[market]);
  useEffect(()=>{let timer:ReturnType<typeof setTimeout>|undefined,inFlight=false,stopped=false;const poll=async()=>{if(stopped)return;if(document.visibilityState!=="visible"){timer=setTimeout(poll,60000);return;}if(inFlight){timer=setTimeout(poll,5000);return;}inFlight=true;try{await load();}catch{setMessage("暂时无法读取账号状态。");}finally{inFlight=false;if(!stopped)timer=setTimeout(poll,5000);}};const visible=()=>{if(document.visibilityState==="visible"&&!inFlight){if(timer)clearTimeout(timer);void poll();}};document.addEventListener("visibilitychange",visible);void poll();return()=>{stopped=true;if(timer)clearTimeout(timer);document.removeEventListener("visibilitychange",visible);};},[load]);
  const mutate=async(row:AccountRow,action:"set_enabled"|"refresh"|"relogin",enabled?:boolean)=>{
    const key=`${row.account}:${action}`,requestId=ids.current.get(key)??`account-${crypto.randomUUID()}`;ids.current.set(key,requestId);setBusy(key);
    try{
      const response=await fetch(`/api/market-accounts?market=${encodeURIComponent(market)}`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action,market,account:row.account,requestId,...(action==="set_enabled"?{enabled,expectedRevision:row.localRevision??0}:{})})});
      if(!response.ok)throw Error();setData(await response.json());ids.current.delete(key);
      setMessage(action==="set_enabled"?"账号启用状态已保存。":action==="relogin"?"重登已启动；浏览器会自动打开并填写已保存的账号密码。":"身份刷新已启动；如静默刷新失败，会自动打开登录浏览器。");
    }catch{setMessage("账号操作未完成；最后一套已发布身份没有被覆盖。");}finally{setBusy(null);}
  };
  return <div className="space-y-5">
    {!embedded&&(
      <PageHeading title="机构账号设置" description="两个账号固定分工；职责与身份实测分开显示，重登只发布到本项目身份目录。" action={<Link href={`/${market}/ops/jobs`} className="text-sm font-medium text-brand-500">作业与定时 →</Link>}/>
    )}
    {message&&<Notice tone={message.includes("已")||message.includes("启动")?"success":"warning"}>{message}</Notice>}
    {!data?<p className="text-sm text-gray-500">正在读取账号代次…</p>:data.markets.length===0?<Notice tone="warning">当前市场尚未登记通信/货盘账号；页面位置保留，刷新、重登和平台动作均不可用。</Notice>:data.markets.map(market=><div key={market.market} className="grid gap-5 xl:grid-cols-2">
      {market.accounts.map(row=>{const presentation=maintenancePresentation(row);const capabilities=row.identityGeneration?.capabilities??row.evidence?.capabilities??{};return <Card key={row.account} title={`${row.account.toUpperCase()} · ${row.role==="communications"?"通信账号":"货盘账号"}`} action={<Pill tone={presentation.tone}>{presentation.label}</Pill>}>
        <div className="space-y-4 p-5">
          <Toggle label="启用账号" description="控制新系统是否给该账号领取固定职责内的任务。" checked={row.localEnabled??true} disabled={busy!==null} onChange={value=>void mutate(row,"set_enabled",value)}/>
          <div>
            <p className="mb-2 text-xs font-medium text-gray-500">固定职责</p>
            <div className="flex flex-wrap gap-2">{row.responsibilities.map(name=><Pill key={name} tone="success">{responsibilityLabels[name]??name}</Pill>)}</div>
          </div>
          <div className="grid grid-cols-2 gap-3 text-xs"><div className="rounded-xl bg-gray-50 p-3 dark:bg-gray-800"><p className="text-gray-400">当前任务</p><p className="mt-1 font-medium">{row.legacyLeaseBusy?row.legacyOperation??"账号正在使用":"空闲"}</p></div><div className="rounded-xl bg-gray-50 p-3 dark:bg-gray-800"><p className="text-gray-400">下次维护</p><p className="mt-1 font-medium">{date(row.plannedLoginMaintenance)}</p></div></div>
          <div className="space-y-3 rounded-xl border border-gray-200 p-4 text-xs dark:border-gray-800">
            <div><p className="font-medium">身份实测</p><p className="mt-1 text-gray-400">绿色表示只读验证通过；灰色表示该写能力尚未实测，不代表账号职责。</p></div>
            <div className="flex flex-wrap gap-2">{Object.entries(capabilities).map(([name,value])=>{const state=typeof value==="string"?value:value.state;return <Pill key={name} tone={state==="verified"?"success":state==="blocked"||state==="failed"?"warning":"neutral"}>{capabilityLabels[name]??name} · {state==="verified"?"已验证":state==="not_tested"?"未实测":"不可用"}</Pill>;})}</div>
            <details className="text-[11px] leading-5 text-gray-400"><summary className="cursor-pointer">身份代次技术详情</summary><div className="mt-2 break-all"><p>{row.identityGeneration?.generationId??"尚未建立"}</p><p>浏览器：{row.identityGeneration?.browserRef??"旧只读引用"}</p><p>HTTP：{row.identityGeneration?.httpRef??"旧只读引用"}</p><p>IM：{row.identityGeneration?.imRef??"旧只读引用"}</p></div></details>
          </div>
          <MaintenanceNotice row={row}/>
          <div className="flex flex-wrap gap-2"><Button size="sm" variant="outline" disabled={busy!==null||activeStates.has(row.maintenanceIntent?.state??"")} onClick={()=>void mutate(row,"refresh")}>立即刷新身份</Button><Button size="sm" variant="outline" disabled={busy!==null||activeStates.has(row.maintenanceIntent?.state??"")} onClick={()=>void mutate(row,"relogin")}>立即重登</Button></div>
          <p className="text-[11px] leading-5 text-gray-400">立即重登会打开可见浏览器并自动填写只读读取的已保存账号密码；新 profile、HTTP 与 IM 身份只写入 BDHub-Agent 的 var 目录，全部验证通过后才原子切换。</p>
        </div>
      </Card>;})}
    </div>)}
  </div>;
}
