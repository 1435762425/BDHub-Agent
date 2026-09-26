"use client";
import {Button,Card,Field,Input,Notice,Pill,StatTile,Toggle} from "../bdhub/ui";
import type {SendController} from "./useContinuousSend";
import SendTemplateManager from "./SendTemplateManager";

const labels:Record<string,string>={off:"关闭",stopped:"已停止",waiting_window:"等待窗口",waiting_pool:"等待发送池",waiting_account:"等待账号空闲",sending:"发送中",waiting_capacity:"当日额度用尽",waiting_platform_refusal:"平台拒绝该达人，继续发送",paused:"暂停",waiting_reconciliation:"待核验",first_send_requires_page_start:"等待页面启动"};
const number=(value:number|null|undefined)=>value==null?"—":value.toLocaleString("zh-CN");

export default function ContinuousSendPanel({market,controller}:{market:string;controller:SendController}){
 const {data,draft,setDraft,busy,message,loaded,save,start,stop,reconcile}=controller;
 if(!data||!draft)return <Card title="持续二发"><div className="p-5 text-sm text-gray-500">{loaded?"暂时无法读取持续发送台账。":"正在读取持续发送台账…"}</div></Card>;
 const dirty=draft.automaticEnabled!==data.control.automaticEnabled||draft.template!==data.control.template||draft.window.some((value,index)=>value!==data.control.window[index]);
 const active=["waiting_window","waiting_pool","waiting_account","sending","waiting_capacity","waiting_platform_refusal","waiting_reconciliation","paused"].includes(data.runtime.state)&&!data.control.stopRequested;
 return <div className="space-y-5">
  {data.runtime.state==="waiting_reconciliation"&&<Notice tone="warning"><strong>有发送结果未知。</strong> 系统已停止领取新达人；只能核验原 delivery、原 requestRef 和原账号，不会重发。</Notice>}
  <SendTemplateManager market={market} controller={controller}/>
  <Card title="发送设置与操作" subtitle="人工“发送”和自动发送都必须等待北京时间窗口；不再创建用户批次或候补名单。" action={<Pill tone={data.window.open?"success":"warning"}>{data.window.open?"窗口已打开":"等待窗口"}</Pill>}>
   <div className="space-y-5 p-5"><div className="grid gap-5 lg:grid-cols-3"><Toggle label="持续自动发送" description="每日到窗口后继续消费当前发送池。" checked={draft.automaticEnabled} disabled={busy} onChange={value=>setDraft({...draft,automaticEnabled:value})}/><Field label="发送开始"><Input type="time" value={draft.window[0]} disabled={busy} onChange={event=>setDraft({...draft,window:[event.target.value,draft.window[1]]})}/></Field><Field label="发送结束"><Input value={draft.window[1]} maxLength={5} disabled={busy} onChange={event=>setDraft({...draft,window:[draft.window[0],event.target.value]})}/></Field></div>
    <div className="flex flex-wrap gap-2"><Button variant="outline" disabled={busy||!dirty} onClick={()=>void save()}>{dirty?"保存设置":"设置已保存"}</Button>{!active?<Button disabled={busy} onClick={()=>void start()}>发送</Button>:<Button variant="outline" disabled={busy} onClick={()=>void stop()}>停止</Button>}{data.unknownDeliveries.length>0&&<Button variant="outline" disabled={busy} onClick={()=>void reconcile()}>核验原发送意图</Button>}</div>{message&&<Notice tone={message.includes("已")?"success":"warning"}>{message}</Notice>}
   </div>
  </Card>
  <Card title="当前发送进程" subtitle="速度、池余量和停止原因都来自台账；进程存活不等于完整触达。" action={<Pill tone={data.runtime.state==="sending"?"success":data.runtime.state==="waiting_reconciliation"?"warning":"neutral"}>{labels[data.runtime.state]??data.runtime.state}</Pill>}>
   <div className="grid grid-cols-2 gap-4 p-5 lg:grid-cols-4"><StatTile label="今日确认触达" value={data.runtime.confirmedToday} hint={data.capacity&&data.capacity.limit==null?`24 小时新联系 ${number(data.capacity.used)} · 上限由平台机构额度决定`:`本地额度 ${number(data.capacity?.limit)}`} brand/><StatTile label="最近 5 分钟速度" value={data.runtime.speedPerMinute} hint="达人/分钟"/><StatTile label="发送池剩余" value={data.poolRemaining??"—"} hint={data.poolRemaining==null?"读取不可用":"当前 ready 达人"}/><StatTile label="结果未知" value={data.runtime.unknown} hint={`明确失败 ${number(data.runtime.failedKnown)}`}/></div>
   <div className="mx-5 mb-5 rounded-xl bg-gray-50 p-4 text-xs leading-6 text-gray-500 dark:bg-gray-800"><p>当前达人 {data.runtime.currentCreatorId??"—"} · PID {data.runtime.currentPid??"—"}</p><p>最近停止原因 {data.runtime.stopReason??"无"} · 最后证据 {data.runtime.seenAt?new Date(data.runtime.seenAt*1000).toLocaleString("zh-CN",{timeZone:"Asia/Shanghai",hour12:false}):"—"}</p><p>旧 frozen-v2 批次只读保留，不再作为新发送授权。</p></div>
  </Card>
 </div>;
}
