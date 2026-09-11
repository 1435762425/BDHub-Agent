import type {RuntimeCommand} from "../../features/runtime/contracts.ts";

export class InputError extends Error {
  readonly status = 400;
  readonly code = "invalid_request";
}
type JsonObject = Record<string, unknown>;
function object(value: unknown): JsonObject {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new InputError("请求必须是 JSON 对象。");
  return value as JsonObject;
}
function keys(value: JsonObject, allowed: string[]) {
  if (Object.keys(value).some(key => !allowed.includes(key))) throw new InputError("请求包含不支持的字段。");
}
function id(value: unknown, name: string): string {
  if (typeof value !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$/.test(value)) throw new InputError(`${name} 格式不正确。`);
  return value;
}
function choice<T extends string>(value: unknown, allowed: readonly T[], name: string): T {
  if (typeof value !== "string" || !allowed.includes(value as T)) throw new InputError(`${name} 不受支持。`);
  return value as T;
}
function revision(value: unknown): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 1) throw new InputError("缺少有效的当前版本，请刷新后重试。");
  return value;
}

export function parseEnvelope(input: unknown): {requestId: string; command: RuntimeCommand} {
  const envelope = object(input);
  keys(envelope, ["requestId", "command"]);
  const requestId = id(envelope.requestId, "请求标识");
  const c = object(envelope.command);
  const relationshipId = id(c.relationshipId, "达人关系标识");
  let command: RuntimeCommand;
  switch (c.type) {
    case "ingest": {
      keys(c, ["type", "relationshipId", "sourceMessageId", "kind", "text", "mode", "deferSeconds"]);
      if (typeof c.text !== "string" || !c.text.trim() || c.text.length > 2000) throw new InputError("消息内容应为 1–2,000 字。");
      const kind = choice(c.kind, ["request_card", "sample_question", "ask_later", "opt_out", "adopted"] as const, "演示事件");
      if (c.deferSeconds !== undefined && (kind !== "ask_later" || typeof c.deferSeconds !== "number" || !Number.isInteger(c.deferSeconds) || c.deferSeconds < 5 || c.deferSeconds > 300)) throw new InputError("演示等待应为 5–300 秒，仅适用于稍后联系。");
      command = {type:"ingest", relationshipId, sourceMessageId:id(c.sourceMessageId, "来源消息标识"), kind, text:c.text.trim(), mode:choice(c.mode, ["live", "history"] as const, "消息模式"), ...(c.deferSeconds === undefined ? {} : {deferSeconds:c.deferSeconds as number})};
      break;
    }
    case "control":
      keys(c, ["type", "relationshipId", "expectedRevision", "mode"]);
      command = {type:"control", relationshipId, expectedRevision:revision(c.expectedRevision), mode:choice(c.mode, ["auto", "human", "paused"] as const, "关系控制")};
      break;
    case "verify":
      keys(c, ["type", "relationshipId", "actionId", "componentId"]);
      command = {type:"verify", relationshipId, actionId:id(c.actionId, "行动标识"), componentId:id(c.componentId, "组件标识")};
      break;
    case "refresh_offer":
    case "expire_offer":
      keys(c, ["type", "relationshipId", "expectedRevision"]);
      command = {type:c.type, relationshipId, expectedRevision:revision(c.expectedRevision)};
      break;
    default: throw new InputError("不支持此操作。");
  }
  return {requestId, command};
}

const LOCAL_HOSTS = new Set(["127.0.0.1:5198", "localhost:5198"]);
export function isLocalRequest(request: Request, mutation: boolean): boolean {
  const host = request.headers.get("host");
  if (!host || !LOCAL_HOSTS.has(host)) return false;
  const origin = request.headers.get("origin");
  if (mutation && origin !== `http://${host}`) return false;
  if (origin && origin !== `http://${host}`) return false;
  if (request.headers.get("sec-fetch-site") === "cross-site") return false;
  return true;
}
