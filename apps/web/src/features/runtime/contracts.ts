// Local runtime v1: synthetic inputs, durable state, no real model or platform calls.
export type RuntimeMarket = "mx" | "br" | "it";
export type RuntimeControl = "auto" | "human" | "paused";
export type RuntimeEventKind = "request_card" | "sample_question" | "ask_later" | "opt_out" | "adopted";
export type ComponentStatus = "prepared" | "submitting" | "accepted" | "unknown" | "cancelled";

export interface RuntimeProduct {
  id: string; pid: string; title: string; image: string;
  offerVersion: number; commissionBps: number; priceMinor: number;
  currency: "MXN" | "BRL" | "EUR"; validUntil: number;
}
export interface RuntimeMessage {
  id: string; sourceMessageId: string; kind: RuntimeEventKind;
  mode: "live" | "history"; text: string; occurredAt: number; observedAt: number;
}
export interface RuntimeEvidence {
  id: string; kind: "creator_statement" | "offer" | "platform_receipt";
  sourceRef: string; observedAt: number; validUntil: number | null;
  status: "valid" | "stale" | "missing"; summary: string;
}
export interface RuntimeComponent {
  id: string; kind: "text" | "product_card"; ordinal: number;
  content: string; status: ComponentStatus; attempts: number;
  receiptRef: string | null; submittedAt: number | null;
}
export interface RuntimeAction {
  id: string; eventId: string; contextId: string; createdAt: number;
  status: "prepared" | "processing" | "accepted" | "unknown" | "cancelled";
  controlRevision: number; inboxRevision: number; policyRevision: number; offerVersion: number;
  adoptedAt?: number; adoptionEventId?: string;
  components: RuntimeComponent[];
}
export interface RuntimeJob {
  id: string; kind: "plan" | "execute" | "verify" | "wake";
  status: "ready" | "leased" | "done" | "cancelled" | "blocked";
  dueAt: number; leaseUntil: number | null; fence: number; attempts: number;
  note: string | null;
}
export interface RuntimeContext {
  id: string; createdAt: number; relationshipId: string;
  controlRevision: number; inboxRevision: number; policyRevision: number;
  offerVersion: number; evidenceRefs: string[]; messageIds: string[];
  skillId: string; skillVersion: string; characterCount: number;
  omittedMessages: number; summary: string;
}
export interface RuntimeAudit {id: string; at: number; title: string; detail: string;}
export interface RuntimeRelationship {
  id: string; market: RuntimeMarket; name: string; handle: string; avatar: string;
  product: RuntimeProduct; control: RuntimeControl; revision: number;
  inboxRevision: number; policyRevision: number; marketingStopped: boolean;
  status: "idle" | "processing" | "waiting_creator" | "waiting_verification" | "needs_facts" | "human" | "paused" | "adopted" | "waiting_until";
  nextStep: string; messages: RuntimeMessage[]; evidence: RuntimeEvidence[];
  actions: RuntimeAction[]; jobs: RuntimeJob[]; context: RuntimeContext | null;
  audit: RuntimeAudit[];
}
export interface RuntimeSnapshot {
  schemaVersion: 1; mode: "local-simulator"; at: number;
  worker: {online: boolean; lastSeenAt: number | null};
  relationships: RuntimeRelationship[];
}
export type RuntimeCommand =
  | {type: "ingest"; relationshipId: string; sourceMessageId: string; kind: RuntimeEventKind; text: string; mode: "live" | "history"; deferSeconds?: number}
  | {type: "control"; relationshipId: string; expectedRevision: number; mode: RuntimeControl}
  | {type: "verify"; relationshipId: string; actionId: string; componentId: string}
  | {type: "refresh_offer"; relationshipId: string; expectedRevision: number}
  | {type: "expire_offer"; relationshipId: string; expectedRevision: number};
export interface RuntimeCommandResult {message: string; duplicate: boolean; snapshot: RuntimeSnapshot;}

export const LOCAL_SCENARIOS: Record<RuntimeMarket, Record<RuntimeEventKind, {text: string; label: string}>> = {
  mx: {
    request_card: {label:"已有实物，索取商品卡",text:"Ya tengo este reloj. ¿Me compartes la ficha del producto?"},
    sample_question: {label:"已申请样品，询问进度",text:"Ya solicité la muestra. ¿Hay alguna novedad?"},
    ask_later: {label:"稍后再联系（20 秒演示）",text:"¿Podemos hablar más tarde?"},
    opt_out: {label:"停止新的营销联系",text:"No quiero recibir más propuestas promocionales."},
    adopted: {label:"确认已采用方案",text:"Confirmo que ya añadí el producto de esta propuesta."},
  },
  br: {
    request_card: {label:"已有实物，索取商品卡",text:"Já tenho este relógio. Pode compartilhar a ficha do produto?"},
    sample_question: {label:"已申请样品，询问进度",text:"Já solicitei a amostra. Há alguma novidade?"},
    ask_later: {label:"稍后再联系（20 秒演示）",text:"Podemos conversar mais tarde?"},
    opt_out: {label:"停止新的营销联系",text:"Não quero receber novas propostas promocionais."},
    adopted: {label:"确认已采用方案",text:"Confirmo que já adicionei o produto desta proposta."},
  },
  it: {
    request_card: {label:"已有实物，索取商品卡",text:"Ho già questo tablet. Puoi condividere la scheda del prodotto?"},
    sample_question: {label:"已申请样品，询问进度",text:"Ho già richiesto il campione. Ci sono novità?"},
    ask_later: {label:"稍后再联系（20 秒演示）",text:"Possiamo parlarne più tardi?"},
    opt_out: {label:"停止新的营销联系",text:"Non desidero ricevere nuove proposte promozionali."},
    adopted: {label:"确认已采用方案",text:"Confermo di aver aggiunto il prodotto di questa proposta."},
  },
};
