import type {OpsAlert} from "../../server/ops-alerts/bridge.ts";

export type OpsAlertKind="system"|"people"|"waiting";
const PEOPLE=/-(human|unread|needs-human|identity-overdue|agent-first-send|agent-pilot-complete|selection-isolated)$/;
const WAITING=/-(platform-rejected|send-quarantined|inbox-stopped)$|^production-paused$/;

export function opsAlertKind(alert:Pick<OpsAlert,"id"|"title">):OpsAlertKind{
 if(WAITING.test(alert.id))return "waiting";
 if(PEOPLE.test(alert.id))return "people";
 // The stage alert id is shared by technical failures and explicit needs_human results.
 if(/-stage-/.test(alert.id)&&alert.title.includes("需要人工"))return "people";
 return "system";
}
