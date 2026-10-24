import type {OpsAlert} from "../../server/ops-alerts/bridge.ts";

export type OpsAlertKind="system"|"people"|"backlog"|"maintenance"|"waiting";
const PEOPLE=/-(human|needs-human|identity-overdue|agent-first-send|agent-pilot-complete|selection-isolated)$/;
const WAITING=/-(platform-rejected|send-quarantined|inbox-stopped)$|^production-paused$/;

export function opsAlertKind(alert:Pick<OpsAlert,"id"|"title">):OpsAlertKind{
 if(WAITING.test(alert.id))return "waiting";
 if(/-unread$/.test(alert.id))return "backlog";
 if(/^(offsite-|restore-drill-)/.test(alert.id))return "maintenance";
 if(PEOPLE.test(alert.id))return "people";
 // The stage alert id is shared by technical failures and explicit needs_human results.
 if(/-stage-/.test(alert.id)&&alert.title.includes("需要人工"))return "people";
 return "system";
}

export function opsAlertSummary(alerts:Pick<OpsAlert,"id"|"title"|"market">[]):string{
 const scoped=(kind:OpsAlertKind,label:string)=>{
  const rows=alerts.filter(alert=>opsAlertKind(alert)===kind);if(!rows.length)return "";
  const markets=new Set(rows.flatMap(row=>row.market?[row.market]:[]));
  return `${label} ${rows.every(row=>row.market)?`${markets.size} 个市场`:`${rows.length} 组`}`;
 };
 const system=alerts.filter(alert=>opsAlertKind(alert)==="system").length;
 const maintenance=alerts.filter(alert=>opsAlertKind(alert)==="maintenance").length;
 return [system?`运行异常 ${system} 组`:"",scoped("people","人工事项"),scoped("backlog","来信积压"),maintenance?`维护 ${maintenance} 组`:""].filter(Boolean).join(" · ");
}
