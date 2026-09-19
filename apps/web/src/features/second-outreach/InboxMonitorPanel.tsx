"use client";
import {Button,Card,Field,Input,Notice,Pill,StatTile} from "../bdhub/ui";
import CycleServicePanel from "./CycleServicePanel";
import type {InboxController} from "./useInboxMonitor";

// 一轮没读到东西时，原因必须翻译成人话：最常见的是账号锁被别人占着——那是等待，不是坏了。
const REASONS:Record<string,string>={live_guard_busy:"采集账号正被别的作业占用（补身份或发送在跑批），它在退避重试。收到的东西不会丢，恢复后接着读。",plan_paused:"这个计划当前是暂停状态，监控读不到新东西。",account_not_startable:"采集账号暂不可用。",maintenance_due:"采集账号在维护窗口内。"};

const time=(value:number)=>value>0?new Date(value*1000).toLocaleString("zh-CN",{timeZone:"Asia/Shanghai",hour12:false}):"尚未核验";

/**
 * 监控与回复。
 *
 * 这一块不发送任何消息：它只读已索引的会话，把达人回复、橱窗通知和待人工事项摊开给人看。
 * 监控进程本身是既有的只读脚本，这里只负责启停它、并把"为什么这一轮没读到"说清楚。
 */
export default function InboxMonitorPanel({controller}:{controller:InboxController}){
 const {data,draft,setDraft,busy,message,save,start,stop,loaded}=controller;
 if(!data?.available)return <Card title="监控与回复"><div className="p-5"><p className="text-sm text-gray-500">{!loaded?"读取中…":"暂时无法读取收信监控状态。"}</p></div></Card>;
 const run=data.run,step=run?.progress??null,today=data.today;
 const running=Boolean(run?.running),stopping=Boolean(run?.stopping);
 return <Card title="监控与回复" subtitle="只读已索引的会话：达人回了什么、谁把商品加了橱窗、哪几件需要人处理。这里不发任何消息。"
  action={running?(stopping?<Pill tone="warning">已请求停止</Pill>:<Pill tone="brand">监控中</Pill>):<Pill tone="neutral">未在监控</Pill>}>
  <div className="space-y-5 p-5">
   <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
    <StatTile label="今日收到回复" value={today?.replies??0} hint={`按北京时间今天；历史补录不计入`} brand/>
    <StatTile label="今日加橱窗" value={today?.showcase??0} hint="平台侧橱窗通知，早就在抓"/>
    <StatTile label="今日触达达人" value={today?.creators??0} hint={`发出 ${today?.cards??0} 张卡；未确认 ${today?.unconfirmed??0} 条`}/>
    <StatTile label="待人工处理" value={data.openCases} hint="达人咨询里等人接话的事项"/>
   </div>
   <div className="flex flex-wrap gap-x-6 gap-y-2 text-sm text-gray-600 dark:text-gray-300">
    <span>在监控会话 <strong>{step?.conversations??0}</strong></span>
    <span>已记录事件 <strong>{step?.events??0}</strong>（历史补录 {step?.historicalEvents??0}）</span>
    <span>待取内容 <strong>{step?.pendingContent??0}</strong> 位达人</span>
    <span>最近核验 <strong>{time(step?.lastCheckedAt??0)}</strong></span>
    {Boolean(step?.gaps)&&<span className="text-warning-600">历史缺口 {step?.gaps} 个会话</span>}
   </div>
   <p className="text-xs leading-5 text-gray-500">
    自动回复现在是<strong>{step?.automaticRepliesEnabled?"开启":"关闭"}</strong>状态。新消息先冻结该达人，再进入两小时集中预演：
    Agent 只选五种动作和引用证据，回复正文只能取固定模板；你判「正确 / 不正确」。<strong>预演阶段一条消息都不发</strong>。
   </p>
   {/* 为什么这一轮没读到东西：不说清楚，一个常驻作业看起来就像坏了。 */}
   {step?.errorCode&&<p className="text-xs leading-5 text-warning-600 dark:text-warning-400">{REASONS[step.errorCode]??`这一轮停在 ${step.errorCode}。`}</p>}
   <div className="rounded-xl bg-gray-50 p-4 dark:bg-white/[0.03]">
    <p className="text-xs leading-5 text-gray-500">
     监控和补身份/发送<strong>抢同一把采集账号锁</strong>：别人跑批时它会自动退避重试（几秒一次），只是变慢，不会漏消息——
     收信是按会话断点增量读的。
    </p>
   </div>
   <div className="grid gap-4 lg:grid-cols-2">
    <Field label="一轮核验几个会话" hint="1–12。开大只会把补身份/发送挤得更久，不会更快收到回复。"><Input type="number" min={1} max={12} value={draft?.limit??data.config.limit} onChange={e=>draft&&setDraft({...draft,limit:Number(e.target.value)})}/></Field>
    <Field label="两轮之间停多久（秒）" hint="30–3600。被账号锁挡住时它会用更短的间隔重试，不受这里影响。"><Input type="number" min={30} max={3600} value={draft?.interval??data.config.interval} onChange={e=>draft&&setDraft({...draft,interval:Number(e.target.value)})}/></Field>
   </div>
   <div className="flex flex-wrap items-center gap-2">
    <Button size="sm" variant="outline" disabled={busy} onClick={()=>void save()}>{busy?"保存中…":"保存设置"}</Button>
    <Button size="sm" disabled={busy||running} onClick={()=>void start()}>{running?"监控中…":"开始监控"}</Button>
    {running&&<Button size="sm" variant="outline" disabled={busy||stopping} onClick={()=>void stop()}>{stopping?"本轮到点就停…":"停止"}</Button>}
    {data.configInvalid&&<Pill tone="warning">配置文件已过期，按默认值读</Pill>}
   </div>
   {message&&<Notice tone="info">{message}</Notice>}
   <div className="border-t border-gray-100 pt-4 dark:border-gray-800">
    <p className="mb-3 text-sm font-medium text-gray-700 dark:text-gray-300">待人工的达人咨询</p>
    <CycleServicePanel/>
   </div>
  </div>
 </Card>;
}
