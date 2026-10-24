import type {OperationsHome,OperationsStage} from "../../server/operations-home/bridge.ts";

export type OperatorQueue={human:number;technical:number}|null|undefined;
export function operatorQueueSummary(queue:OperatorQueue):{value:number|null;text:string}{
 if(queue==null)return {value:null,text:"人工队列尚未核实，不能确认待办数量"};
 return {value:queue.human,text:queue.human?`${queue.human.toLocaleString()} 位达人需要你判断合作事项`:"暂无人工会话待办"};
}

const minute=(text:string,end=false)=>{
 if(end&&text==="24:00")return 1440;
 const match=/^(\d{2}):(\d{2})$/.exec(text);if(!match||+match[1]>23||+match[2]>59)return null;
 return +match[1]*60 + +match[2];
};
export function nextWindowText(window:unknown,now:number):string|null{
 if(!Array.isArray(window)||window.length!==2||window.some(value=>typeof value!=="string")||!Number.isFinite(now))return null;
 const start=minute(window[0]),end=minute(window[1],true);if(start===null||end===null||start>=end)return null;
 const parts=Object.fromEntries(new Intl.DateTimeFormat("en-GB",{timeZone:"Asia/Shanghai",hour:"2-digit",minute:"2-digit",hourCycle:"h23"}).formatToParts(new Date(now)).map(row=>[row.type,row.value]));
 const current=+parts.hour*60 + +parts.minute;
 if(current>=start&&current<end)return "窗口已到，等待下一次调度检查";
 return `${current<start?"今天":"明天"} ${window[0]}（北京时间）`;
}

export type ActivityRow={id:string;label:string;state:string;detail:string;attention:boolean;href:string};
const STATE:Record<string,string>={completed:"本轮已完成",running:"运行中",queued:"排队中",waiting_upstream:"等待上游",sending:"发送中",waiting_window:"等待发送窗口",outside_reply_window:"回复窗口外",waiting_account:"等待账号",waiting_capacity:"等待额度",waiting_pool:"等待候选",waiting_transport:"连接恢复中",waiting_dispatch:"等待原请求收口",waiting_send_window:"等待二发窗口结束",waiting_reply_check:"等待原回复核验",reply_check_deferred:"原回复核验暂缓",waiting_reconciliation:"等待原结果核验",failed:"运行失败",needs_human:"需要人工核对",attention:"已停止，需检查",unverified:"状态待核实",plan_paused:"计划已暂停",paused:"已暂停",stopped:"已停止",disabled:"未启用",off:"未开启",idle:"暂无待办",first_send_requires_page_start:"等待页面首次启动",pilot_complete_waiting_resume:"首条已确认，等待页面继续",pilot_waiting_confirmation:"首条结果待核验"};
const NORMAL_WAIT:Record<string,string>={waiting_account:"等待原账号空闲",waiting_capacity:"等待平台额度恢复",waiting_pool:"等待符合规则的候选",waiting_transport:"等待连接恢复或下次重试",waiting_dispatch:"正在核验原在途请求",waiting_send_window:"等待原二发窗口结束",waiting_reply_check:"按原回复记录核验，不会重发",reply_check_deferred:"原回复暂缓核验，保留原记录",waiting_upstream:"等待原上游结果",queued:"等待调度领取"};
const record=(raw:unknown)=>raw&&typeof raw==="object"&&!Array.isArray(raw)?raw as Record<string,unknown>:null;
function sendWindow(raw:unknown):unknown{return record(record(raw)?.control)?.window??null;}
function sendEnabled(raw:unknown,setting:boolean):boolean|null{
 const control=record(record(raw)?.control);
 if(!control||typeof control.automaticEnabled!=="boolean"||typeof control.runRequested!=="boolean"||typeof control.stopRequested!=="boolean")return null;
 return !control.stopRequested&&(setting||control.automaticEnabled||control.runRequested);
}
function activity(stage:OperationsStage|undefined,label:string,enabled:boolean|null,window:unknown,href:string,now:number):ActivityRow{
 const rawState=stage?.runtime?.fresh===false?"unverified":stage?.state??"unverified",state=STATE[rawState]??"状态待核实";
 const automatic=enabled===true&&stage?.runtime?.enabled!==false;
 let detail="请查看原运行记录，当前状态不能确认为正常",attention=["failed","needs_human","attention","unverified","waiting_reconciliation","waiting_reply_check","reply_check_deferred","pilot_waiting_confirmation","first_send_requires_page_start","pilot_complete_waiting_resume"].includes(rawState)||!STATE[rawState];
 if(rawState==="plan_paused")detail="原计划已暂停，仅继续核验已提交操作，不开启新处理";
 else if(rawState==="waiting_window"||rawState==="outside_reply_window"){
  const next=nextWindowText(window,now);
  detail=automatic?next?`${next}；进入窗口后按原授权自动继续`:"窗口时间尚未核实；保留原授权等待检查":enabled===null?"当前继续授权尚未核实，不安排自动恢复时间":"当前继续授权未开启，不安排自动恢复时间";
 }else if(NORMAL_WAIT[rawState])detail=`${NORMAL_WAIT[rawState]}${automatic?"，条件满足后按原授权继续":enabled===null?"；当前继续授权尚未核实":"；保留现有控制状态"}`;
 else if(rawState==="completed")detail="本轮已完成；后续按已保存的作业设置推进";
 else if(rawState==="running"||rawState==="sending")detail="正在按现有规则与授权处理";
 else if(rawState==="idle")detail=automatic?"当前暂无待办，保持自动检查":enabled===null?"当前继续授权尚未核实":"保留当前关闭状态";
 else if(["disabled","off","stopped","paused"].includes(rawState))detail="保留原控制与暂停状态，可查看设置或停止原因";
 else if(["first_send_requires_page_start","pilot_complete_waiting_resume"].includes(rawState))detail="需要你在既有页面确认后继续，不会自行新增授权";
 if(stage?.nextAt!=null&&NORMAL_WAIT[rawState])detail+=`；下次检查记录：${new Date(stage.nextAt*1000).toLocaleString("zh-CN",{timeZone:"Asia/Shanghai",hour12:false})}`;
 return {id:stage?.id??label,label,state,detail,attention,href};
}

export function operationsActivity(data:OperationsHome,now=Date.now()):ActivityRow[]{
 const base=`/${data.market}`,workflow=["running","queued"].includes(data.workflow.state)?data.stages.find(row=>!["continuous_send","agent_reply"].includes(row.id)&&["running","queued","waiting_upstream"].includes(row.state)):undefined;
 const main=workflow??{id:"workflow",label:"经营主链",state:data.workflow.state,nextAt:null} as OperationsStage;
 return [activity(main,workflow?`经营主链 · ${workflow.label}`:"经营主链",data.setting.automaticOperationsEnabled,null,`${base}/ops/jobs`,now),
  activity(data.stages.find(row=>row.id==="continuous_send"),"持续二发",sendEnabled(data.continuousSend,data.setting.continuousSendEnabled),sendWindow(data.continuousSend),`${base}/workspace/send`,now),
  activity(data.stages.find(row=>row.id==="agent_reply"),"Agent 回复",data.agent.enabled,data.agent.replyWindow,`${base}/conversations/agent`,now)];
}
