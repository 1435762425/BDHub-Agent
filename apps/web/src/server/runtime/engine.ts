import { DatabaseSync } from "node:sqlite";
import { createHash, randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import type {
  RuntimeAction, RuntimeAudit, RuntimeCommand, RuntimeCommandResult, RuntimeComponent,
  RuntimeContext, RuntimeEventKind, RuntimeJob, RuntimeMessage, RuntimeRelationship, RuntimeSnapshot,
} from "../../features/runtime/contracts.ts";
import { CARD_SKILL, planCard } from "./skills.ts";

export class RuntimeError extends Error {
  status: number;
  code: string;
  constructor(status: number, code: string, message: string) {
    super(message); this.name = "RuntimeError"; this.status = status; this.code = code;
  }
}

type Relation = Omit<RuntimeRelationship, "messages" | "jobs">;
type JobPayload = { eventId?: string; actionId?: string; componentId?: string; inboxRevision?: number };
type JobRow = {
  id: string; relationship_id: string; kind: RuntimeJob["kind"]; status: RuntimeJob["status"];
  due_at: number; lease_until: number | null; lease_owner: string | null;
  fence: number; attempts: number; payload: string; note: string | null;
};
type EventRow = {
  id: string; relationship_id: string; source_id: string; kind: RuntimeEventKind;
  mode: "live" | "history"; text: string; occurred_at: number; observed_at: number; payload_hash: string;
};
type ReceiptRow = { component_id: string; receipt_id: string; accepted_at: number; payload_hash: string };

function stable(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stable).join(",")}]`;
  if (value && typeof value === "object") return `{${Object.entries(value).filter(([, v]) => v !== undefined).sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => `${JSON.stringify(k)}:${stable(v)}`).join(",")}}`;
  return JSON.stringify(value);
}
function hash(value: unknown) { return createHash("sha256").update(stable(value)).digest("hex"); }
function id(prefix: string) { return `${prefix}-${randomUUID()}`; }
function message(row: EventRow): RuntimeMessage {
  return { id: row.id, sourceMessageId: row.source_id, kind: row.kind, mode: row.mode, text: row.text, occurredAt: row.occurred_at, observedAt: row.observed_at };
}

export class LocalRuntime {
  private db: DatabaseSync;
  private now: () => number;
  private leaseMs: number;

  constructor(dbPath: string, options: { now?: () => number; leaseMs?: number } = {}) {
    const path = resolve(dbPath);
    mkdirSync(dirname(path), { recursive: true });
    this.now = options.now ?? Date.now;
    this.leaseMs = options.leaseMs ?? 15_000;
    this.db = new DatabaseSync(path);
    this.db.exec("PRAGMA busy_timeout=5000; PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;");
    this.transaction(() => {
      this.db.exec(`
        CREATE TABLE IF NOT EXISTS runtime_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS relationships (id TEXT PRIMARY KEY, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events (
          id TEXT PRIMARY KEY, relationship_id TEXT NOT NULL REFERENCES relationships(id),
          source_id TEXT NOT NULL, kind TEXT NOT NULL, mode TEXT NOT NULL, text TEXT NOT NULL,
          occurred_at INTEGER NOT NULL, observed_at INTEGER NOT NULL, payload_hash TEXT NOT NULL,
          UNIQUE(relationship_id, source_id)
        );
        CREATE TABLE IF NOT EXISTS command_requests (id TEXT PRIMARY KEY, payload_hash TEXT NOT NULL, message TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY, relationship_id TEXT NOT NULL REFERENCES relationships(id),
          kind TEXT NOT NULL, status TEXT NOT NULL, due_at INTEGER NOT NULL,
          lease_until INTEGER, lease_owner TEXT, fence INTEGER NOT NULL DEFAULT 0,
          attempts INTEGER NOT NULL DEFAULT 0, payload TEXT NOT NULL, note TEXT
        );
        CREATE INDEX IF NOT EXISTS jobs_due ON jobs(status, due_at, lease_until);
        CREATE TABLE IF NOT EXISTS contexts (id TEXT PRIMARY KEY, relationship_id TEXT NOT NULL, body TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS component_attempts (
          id TEXT PRIMARY KEY, component_id TEXT NOT NULL, action_id TEXT NOT NULL,
          started_at INTEGER NOT NULL, status TEXT NOT NULL, receipt_ref TEXT,
          UNIQUE(component_id)
        );
        CREATE TABLE IF NOT EXISTS platform_receipts (
          component_id TEXT PRIMARY KEY, receipt_id TEXT NOT NULL UNIQUE,
          accepted_at INTEGER NOT NULL, payload_hash TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS worker_heartbeats (id TEXT PRIMARY KEY, seen_at INTEGER NOT NULL);
      `);
      const version = this.db.prepare("SELECT value FROM runtime_meta WHERE key='schema_version'").get() as {value: string} | undefined;
      if (version && version.value !== "1") throw new RuntimeError(409, "schema_version", `本地数据库版本 ${version.value} 不兼容当前运行底座。`);
      this.db.prepare("INSERT OR IGNORE INTO runtime_meta(key,value) VALUES('schema_version','1')").run();
      this.seed();
    });
  }

  close() { this.db.close(); }

  private transaction<T>(fn: () => T): T {
    this.db.exec("BEGIN IMMEDIATE");
    try { const result = fn(); this.db.exec("COMMIT"); return result; }
    catch (error) { this.db.exec("ROLLBACK"); throw error; }
  }

  private seed() {
    const seeds = [
      {id:"rt-sofia-mx",market:"mx" as const,name:"Sofía Luna",handle:"sofia.demo",avatar:"/images/user/user-17.jpg", productId:"watch-mx",pid:"DEMO-MX-WATCH",title:"运动智能手表",image:"/images/product/product-02.jpg",priceMinor:249900,currency:"MXN" as const,commissionBps:1200},
      {id:"rt-pedro-br",market:"br" as const,name:"Pedro Lima",handle:"pedro.demo",avatar:"/images/user/user-22.jpg", productId:"watch-br",pid:"DEMO-BR-WATCH",title:"运动智能手表",image:"/images/product/product-02.jpg",priceMinor:59900,currency:"BRL" as const,commissionBps:1100},
      {id:"rt-luca-it",market:"it" as const,name:"Luca Moretti",handle:"luca.demo",avatar:"/images/user/user-18.jpg", productId:"tablet-it",pid:"DEMO-IT-TABLET",title:"轻便平板电脑",image:"/images/product/product-04.jpg",priceMinor:29900,currency:"EUR" as const,commissionBps:800},
    ];
    for (const seed of seeds) {
      const now = this.now();
      const rel: Relation = {
        id: seed.id,market:seed.market,name:seed.name,handle:seed.handle,avatar:seed.avatar,
        product:{id:seed.productId,pid:seed.pid,title:seed.title,image:seed.image,offerVersion:1,commissionBps:seed.commissionBps,priceMinor:seed.priceMinor,currency:seed.currency,validUntil:now+86_400_000},
        control:"auto",revision:1,inboxRevision:0,policyRevision:1,marketingStopped:false,
        status:"idle",nextStep:"等待新的达人请求；历史导入不会触发发送。",
        evidence:[],actions:[],context:null,audit:[],
      };
      this.offerEvidence(rel);
      this.db.prepare("INSERT OR IGNORE INTO relationships(id,body) VALUES(?,?)").run(rel.id, JSON.stringify(rel));
    }
  }

  private relation(relationshipId: string): Relation {
    const row = this.db.prepare("SELECT body FROM relationships WHERE id=?").get(relationshipId) as {body:string} | undefined;
    if (!row) throw new RuntimeError(404,"relationship_missing","未找到该示例达人关系。");
    return JSON.parse(row.body) as Relation;
  }
  private save(rel: Relation) { this.db.prepare("UPDATE relationships SET body=? WHERE id=?").run(JSON.stringify(rel),rel.id); }
  private events(relationshipId: string): EventRow[] {
    return this.db.prepare("SELECT * FROM events WHERE relationship_id=? ORDER BY rowid").all(relationshipId) as EventRow[];
  }
  private audit(rel: Relation, title: string, detail: string) {
    rel.audit.push({id:id("audit"),at:this.now(),title,detail} satisfies RuntimeAudit);
  }
  private offerEvidence(rel: Relation) {
    const product = rel.product;
    const existing = rel.evidence.find((e) => e.kind === "offer" && e.sourceRef === `${product.id}@${product.offerVersion}`);
    if (existing) { existing.validUntil = product.validUntil; existing.status = product.validUntil > this.now() ? "valid" : "stale"; return; }
    rel.evidence.push({id:id("evidence"),kind:"offer",sourceRef:`${product.id}@${product.offerVersion}`,observedAt:this.now(),validUntil:product.validUntil,status:product.validUntil>this.now()?"valid":"stale",summary:`模拟商品条件：${product.pid}；佣金 ${product.commissionBps / 100}%；版本 ${product.offerVersion}`});
  }

  snapshot(): RuntimeSnapshot {
    // A read transaction prevents a multi-query snapshot from mixing revisions.
    this.db.exec("BEGIN");
    try {
      const now = this.now();
      const rows = this.db.prepare("SELECT body FROM relationships ORDER BY rowid").all() as {body:string}[];
      const relationships = rows.map(({body}) => {
        const rel = JSON.parse(body) as Relation;
        rel.evidence = rel.evidence.map(e => e.validUntil !== null && e.validUntil <= now ? {...e,status:"stale" as const}:e);
        const jobs = (this.db.prepare("SELECT * FROM jobs WHERE relationship_id=? ORDER BY rowid").all(rel.id) as JobRow[]).map(j => ({id:j.id,kind:j.kind,status:j.status,dueAt:j.due_at,leaseUntil:j.lease_until,fence:j.fence,attempts:j.attempts,note:j.note}));
        return {...rel,messages:this.events(rel.id).map(message),jobs};
      });
      const heartbeat = this.db.prepare("SELECT MAX(seen_at) AS seen FROM worker_heartbeats").get() as {seen:number|null};
      const snapshot: RuntimeSnapshot = {schemaVersion:1,mode:"local-simulator",at:now,worker:{online:heartbeat.seen!==null && now-heartbeat.seen<Math.max(20_000,this.leaseMs*2),lastSeenAt:heartbeat.seen},relationships};
      this.db.exec("COMMIT"); return snapshot;
    } catch (error) { this.db.exec("ROLLBACK"); throw error; }
  }

  command(command: RuntimeCommand, requestId: string): RuntimeCommandResult {
    if (!requestId || requestId.length>160) throw new RuntimeError(400,"request_id","需要长度不超过 160 的请求幂等标识。");
    const payloadHash = hash(command);
    const result = this.transaction(() => {
      const previous = this.db.prepare("SELECT payload_hash,message FROM command_requests WHERE id=?").get(requestId) as {payload_hash:string;message:string}|undefined;
      if (previous) {
        if (previous.payload_hash!==payloadHash) throw new RuntimeError(409,"request_conflict","同一请求标识已用于不同内容。");
        return {message:previous.message,duplicate:true};
      }
      const rel = this.relation(command.relationshipId);
      let result: {message:string;duplicate:boolean};
      switch (command.type) {
        case "ingest": result = this.ingest(rel,command); break;
        case "control": {
          this.checkRevision(rel,command.expectedRevision);
          if (!["auto","human","paused"].includes(command.mode)) throw new RuntimeError(400,"control_mode","无效的控制模式。");
          rel.control=command.mode; rel.revision++;
          this.invalidate(rel,"控制版本变化，旧的未提交动作失效。");
          this.audit(rel,"关系控制已更新",`当前由 ${command.mode} 控制；营销抑制状态保持 ${rel.marketingStopped ? "开启":"关闭"}。`);
          if(command.mode==="auto") this.replanLatest(rel);
          else {rel.status=command.mode;rel.nextStep=command.mode==="human"?"人工接管中；已提交组件保留原结果。":"关系已暂停；已提交组件仍可核验。";}
          this.save(rel);result={message:"关系控制已持久保存。",duplicate:false}; break;
        }
        case "verify": {
          const {action,component}=this.component(rel,command.actionId,command.componentId);
          if(component.status!=="unknown") throw new RuntimeError(409,"verify_state","只有结果未知的组件需要核验。");
          this.enqueue(rel.id,"verify",{actionId:action.id,componentId:component.id},this.now());
          this.audit(rel,"已登记核验",`只读取原组件 ${component.id} 的平台记录，不创建新的发送尝试。`);
          this.save(rel);result={message:"核验任务已保存，等待 Worker 读取原回执。",duplicate:false};break;
        }
        case "refresh_offer": case "expire_offer": {
          this.checkRevision(rel,command.expectedRevision);
          rel.revision++;
          if(command.type==="refresh_offer") {
            rel.evidence=rel.evidence.map(e=>e.kind==="offer"?{...e,status:"stale" as const}:e);
            rel.product.offerVersion++;rel.product.validUntil=this.now()+86_400_000;
          } else rel.product.validUntil=this.now()-1;
          this.offerEvidence(rel);
          this.invalidate(rel,"商品条件发生变化，旧的未提交动作失效。");
          this.audit(rel,command.type==="refresh_offer"?"商品条件已刷新":"商品条件已过期",`当前商品版本 ${rel.product.offerVersion}，有效至 ${new Date(rel.product.validUntil).toISOString()}。`);
          if(command.type==="refresh_offer") this.replanLatest(rel);
          else {rel.status=rel.control==="auto"?"needs_facts":rel.control;rel.nextStep="等待刷新有效商品条件；不会发送过期卡片。";}
          this.save(rel);result={message:command.type==="refresh_offer"?"新商品版本已保存。":"商品条件已标记过期。",duplicate:false};break;
        }
        default: throw new RuntimeError(400,"command_type","不支持的运行指令。");
      }
      this.db.prepare("INSERT INTO command_requests(id,payload_hash,message) VALUES(?,?,?)").run(requestId,payloadHash,result.message);
      return result;
    });
    return {...result,snapshot:this.snapshot()};
  }

  private checkRevision(rel: Relation, revision: number) {
    if(rel.revision!==revision) throw new RuntimeError(409,"revision_conflict","关系已被另一个操作更新，请刷新后再操作。");
  }
  private ingest(rel: Relation, command: Extract<RuntimeCommand,{type:"ingest"}>) {
    if(!command.sourceMessageId || command.sourceMessageId.length>160 || !command.text?.trim() || command.text.length>4000 || !["live","history"].includes(command.mode) || !["request_card","sample_question","ask_later","opt_out","adopted"].includes(command.kind)) throw new RuntimeError(400,"ingest_input","入站示例的标识、正文、模式或类型无效。");
    if(command.deferSeconds!==undefined && (!Number.isInteger(command.deferSeconds)||command.deferSeconds<1||command.deferSeconds>86400)) throw new RuntimeError(400,"defer_seconds","延后时间必须在 1 至 86400 秒之间。");
    const payloadHash=hash({kind:command.kind,text:command.text,mode:command.mode,deferSeconds:command.deferSeconds});
    const existing=this.db.prepare("SELECT payload_hash FROM events WHERE relationship_id=? AND source_id=?").get(rel.id,command.sourceMessageId) as {payload_hash:string}|undefined;
    if(existing) {
      if(existing.payload_hash!==payloadHash) throw new RuntimeError(409,"event_conflict","同一来源消息标识对应不同内容，未覆盖原消息。");
      return {message:"重复来源消息已合并，未创建新的动作。",duplicate:true};
    }
    const eventId=id("event");const now=this.now();
    this.db.prepare("INSERT INTO events(id,relationship_id,source_id,kind,mode,text,occurred_at,observed_at,payload_hash) VALUES(?,?,?,?,?,?,?,?,?)").run(eventId,rel.id,command.sourceMessageId,command.kind,command.mode,command.text,now,now,payloadHash);
    rel.evidence.push({id:id("evidence"),kind:"creator_statement",sourceRef:eventId,observedAt:now,validUntil:null,status:command.mode==="history"?"stale":"valid",summary:`${command.mode==="history"?"历史":"新入站"}达人陈述：${command.text.slice(0,240)}`});
    if(command.mode==="history") {
      this.audit(rel,"历史消息已保存","保留原始消息，仅作为历史资料；没有改变当前收件版本或生成外发任务。");this.save(rel);
      return {message:"历史消息已入库，没有触发外发。",duplicate:false};
    }
    const previousLive=this.events(rel.id).filter(e=>e.mode==="live"&&e.id!==eventId).at(-1);
    rel.inboxRevision++;
    this.invalidate(rel,"收到新的入站消息，旧的未提交动作失效。");
    switch(command.kind) {
      case "request_card":
        rel.status="processing";rel.nextStep="明确的商品卡服务请求已保存，等待生成受限计划。";
        if(rel.control==="auto") this.enqueue(rel.id,"plan",{eventId,inboxRevision:rel.inboxRevision},now);
        break;
      case "sample_question":
        rel.status="needs_facts";rel.nextStep="达人称已申请样品；尚无平台申请记录，需补充证据。";
        break;
      case "ask_later":
        rel.status="waiting_until";rel.nextStep="已登记延后复查；到期只检查最新事实，不自动发送。";
        this.enqueue(rel.id,"wake",{eventId,inboxRevision:rel.inboxRevision},now+(command.deferSeconds??20)*1000);
        break;
      case "opt_out":
        rel.marketingStopped=true;rel.policyRevision++;
        rel.status="idle";rel.nextStep="已停止新的营销联系；仅处理达人后来明确提出的服务请求。";
        break;
      case "adopted": {
        const action=previousLive?.kind==="request_card"?rel.actions.filter(a=>a.eventId===previousLive.id).at(-1):undefined;
        if(action && action.components.length===2 && action.components.every(c=>c.status==="accepted") && action.status==="accepted") {
          action.adoptedAt=now;action.adoptionEventId=eventId;
          rel.status="adopted";rel.nextStep="达人已确认采用最近方案；收益仍需独立证据。";
          this.audit(rel,"采用证据已关联",`达人陈述 ${eventId} 关联最近商品卡请求 ${previousLive!.id} 和动作 ${action.id}；两组件均已接收。`);
        } else {rel.status="needs_facts";rel.nextStep="已保存采用陈述，尚不能关联到最近完整接收的方案；不计入已采用。";}
        break;
      }
    }
    if(rel.control!=="auto") {rel.status=rel.control;rel.nextStep=rel.control==="human"?"人工接管中；新消息已保存，等待人工处理或交还 Agent。":"关系暂停中；新消息已保存，等待恢复。";}
    this.audit(rel,"新入站已保存",`来源 ${command.sourceMessageId}；收件版本 ${rel.inboxRevision}。`);
    this.save(rel);return {message:"新消息已持久保存。",duplicate:false};
  }

  private enqueue(relationshipId: string, kind: RuntimeJob["kind"], payload: JobPayload, dueAt: number) {
    const encoded=stable(payload);
    const existing=this.db.prepare("SELECT id FROM jobs WHERE relationship_id=? AND kind=? AND payload=? AND status IN ('ready','leased')").get(relationshipId,kind,encoded) as {id:string}|undefined;
    if(existing) return existing.id;
    if(kind==="plan" || kind==="verify") {
      const blocked=this.db.prepare("SELECT id FROM jobs WHERE relationship_id=? AND kind=? AND payload=? AND status='blocked' ORDER BY rowid DESC LIMIT 1").get(relationshipId,kind,encoded) as {id:string}|undefined;
      if(blocked) {
        this.db.prepare("UPDATE jobs SET status='ready',due_at=?,lease_until=NULL,lease_owner=NULL,fence=fence+1,note=NULL WHERE id=?").run(dueAt,blocked.id);
        return blocked.id;
      }
    }
    const jobId=id("job");
    this.db.prepare("INSERT INTO jobs(id,relationship_id,kind,status,due_at,payload) VALUES(?,?,?,'ready',?,?)").run(jobId,relationshipId,kind,dueAt,encoded);
    return jobId;
  }

  private invalidate(rel: Relation, reason: string) {
    for(const action of rel.actions) {
      for(const component of action.components) if(component.status==="prepared") component.status="cancelled";
      this.actionStatus(action);
    }
    // Submitted operations and their verification are immutable facts. Their leased
    // execute job remains claimable so recovery can turn an abandoned submission unknown.
    const jobs=this.db.prepare("SELECT * FROM jobs WHERE relationship_id=? AND status IN ('ready','leased') AND kind!='verify'").all(rel.id) as JobRow[];
    for(const job of jobs) {
      const p=JSON.parse(job.payload) as JobPayload;
      const component=rel.actions.find(a=>a.id===p.actionId)?.components.find(c=>c.id===p.componentId);
      if(job.kind==="execute"&&component?.status==="submitting") continue;
      this.db.prepare("UPDATE jobs SET status='cancelled',lease_until=NULL,lease_owner=NULL,fence=fence+1,note=? WHERE id=?").run(reason,job.id);
    }
  }

  private replanLatest(rel: Relation) {
    if(rel.control!=="auto") return;
    const latest=this.events(rel.id).filter(e=>e.mode==="live").at(-1);
    if(!latest) {rel.status="idle";rel.nextStep="等待新的达人请求。";return;}
    if(rel.actions.some(a=>a.components.some(c=>c.status==="unknown"||c.status==="submitting"))) {rel.status="waiting_verification";rel.nextStep="存在已提交但未确认的组件，先核验原意图。";return;}
    if(latest.kind==="request_card") {
      const actions=rel.actions.filter(a=>a.eventId===latest.id);
      if(actions.some(a=>a.components.every(c=>c.status==="accepted"))) {rel.status="waiting_creator";rel.nextStep="最近请求已交付，等待达人回应；不会重复发送。";return;}
      if(actions.some(a=>a.components.some(c=>c.status==="accepted"))) {rel.status="needs_facts";rel.nextStep="最近方案已部分交付；其余旧组件失效，等待新的明确请求，避免重发已交付内容。";return;}
      if(rel.product.validUntil<=this.now()) {rel.status="needs_facts";rel.nextStep="商品条件已过期，等待刷新。";return;}
      this.enqueue(rel.id,"plan",{eventId:latest.id,inboxRevision:rel.inboxRevision},this.now());rel.status="processing";rel.nextStep="按最新控制、收件和商品条件重新规划。";
    } else if(latest.kind==="sample_question") {rel.status="needs_facts";rel.nextStep="样品进度缺少平台证据，等待补充事实。";}
    else if(latest.kind==="ask_later") {rel.status="needs_facts";rel.nextStep="延后请求仍保留；等待最新事实，不会自动外发。";}
    else if(latest.kind==="opt_out") {rel.status="idle";rel.nextStep="营销联系保持停止，等待新的明确服务请求。";}
    else {
      const adopted=rel.actions.find(a=>a.adoptionEventId===latest.id);
      rel.status=adopted?"adopted":"needs_facts";
      rel.nextStep=adopted?"最近方案的采用证据已保存；收益仍需独立证据。":"采用陈述需关联到最近完整交付的方案。";
    }
  }

  private actionStatus(action: RuntimeAction) {
    const statuses=action.components.map(c=>c.status);
    action.status=statuses.includes("unknown")?"unknown":statuses.includes("submitting")?"processing":statuses.every(s=>s==="accepted")?"accepted":statuses.includes("cancelled")?"cancelled":statuses.some(s=>s==="accepted")?"processing":"prepared";
  }
  private component(rel: Relation, actionId?: string, componentId?: string) {
    const action=rel.actions.find(a=>a.id===actionId);const component=action?.components.find(c=>c.id===componentId);
    if(!action||!component) throw new RuntimeError(404,"component_missing","未找到对应动作组件。");
    return {action,component};
  }

  private isOwned(job: JobRow, workerId: string) {
    const row=this.db.prepare("SELECT status,fence,lease_owner,lease_until FROM jobs WHERE id=?").get(job.id) as JobRow|undefined;
    return !!row && row.status==="leased" && row.fence===job.fence && row.lease_owner===workerId && row.lease_until!==null && row.lease_until>this.now();
  }
  private finish(job: JobRow,status: RuntimeJob["status"]="done",note: string|null=null) {
    this.db.prepare("UPDATE jobs SET status=?,lease_until=NULL,lease_owner=NULL,note=? WHERE id=? AND fence=?").run(status,note,job.id,job.fence);
  }

  private claim(workerId: string): {job:JobRow;recovered:boolean}|null {
    return this.transaction(()=>{
      const now=this.now();
      this.db.prepare("INSERT INTO worker_heartbeats(id,seen_at) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET seen_at=excluded.seen_at").run(workerId,now);
      const job=this.db.prepare("SELECT * FROM jobs WHERE (status='ready' AND due_at<=?) OR (status='leased' AND lease_until<=?) ORDER BY due_at,rowid LIMIT 1").get(now,now) as JobRow|undefined;
      if(!job) return null;
      const expired=job.status==="leased";
      job.fence++;job.attempts++;job.status="leased";job.lease_owner=workerId;job.lease_until=now+this.leaseMs;
      this.db.prepare("UPDATE jobs SET status='leased',lease_owner=?,lease_until=?,fence=?,attempts=? WHERE id=?").run(workerId,job.lease_until,job.fence,job.attempts,job.id);
      if(expired && job.kind==="execute") {
        const rel=this.relation(job.relationship_id);const p=JSON.parse(job.payload) as JobPayload;
        const {action,component}=this.component(rel,p.actionId,p.componentId);
        if(component.status==="submitting") {
          component.status="unknown";this.actionStatus(action);
          this.db.prepare("UPDATE component_attempts SET status='unknown' WHERE component_id=?").run(component.id);
          if(rel.control==="auto") {rel.status="waiting_verification";rel.nextStep="执行租约到期，提交结果未知；核验原意图后再推进。";}
          this.audit(rel,"提交结果待核验",`组件 ${component.id} 的 Worker 租约到期；未重放平台写入。`);
          this.save(rel);this.finish(job,"blocked","恢复提交中的动作：结果未知，等待只读核验。");
          return {job,recovered:true};
        }
      }
      return {job,recovered:false};
    });
  }

  async tick(workerId: string): Promise<boolean> {
    if(!workerId || workerId.length>160) throw new RuntimeError(400,"worker_id","Worker 标识无效。");
    const claim=this.claim(workerId);if(!claim) return false;if(claim.recovered) return true;
    const {job}=claim;
    if(job.kind==="execute") await this.execute(job,workerId);
    else {
      // No transaction spans this asynchronous boundary. Tests use this seam to
      // prove an expired worker cannot commit a plan after another worker owns it.
      await this.beforeJobCommit(job.kind);
      this.transaction(()=>{
        if(!this.isOwned(job,workerId)) return;
        if(job.kind==="plan") this.plan(job);
        else if(job.kind==="verify") this.verify(job);
        else this.wake(job);
      });
    }
    return true;
  }

  protected async beforeJobCommit(_kind: RuntimeJob["kind"]): Promise<void> {}
  protected async beforePlatformSubmit(_componentId: string): Promise<void> {}
  protected async afterPlatformSubmit(_componentId: string): Promise<void> {}

  private plan(job: JobRow) {
    const rel=this.relation(job.relationship_id);const payload=JSON.parse(job.payload) as JobPayload;
    const events=this.events(rel.id);const source=events.find(e=>e.id===payload.eventId);
    const live=events.filter(e=>e.mode==="live").at(-1);
    if(rel.control!=="auto"||payload.inboxRevision!==rel.inboxRevision||!source||source.kind!=="request_card"||source.mode!=="live"||live?.id!==source.id) {this.finish(job,"cancelled","计划依据已经变化。");return;}
    if(rel.product.validUntil<=this.now()) {rel.status="needs_facts";rel.nextStep="商品条件过期；补充有效条件后才能生成可发送计划。";this.save(rel);this.finish(job,"blocked","商品条件过期。");return;}
    if(rel.actions.some(a=>a.components.some(c=>c.status==="unknown"||c.status==="submitting"))) {rel.status="waiting_verification";rel.nextStep="存在原提交结果未知的组件，需先核验。";this.save(rel);this.finish(job,"blocked","存在未核验提交。");return;}
    const sourceActions=rel.actions.filter(a=>a.eventId===source.id);
    if(sourceActions.some(a=>a.status!=="cancelled"||a.components.some(c=>c.status==="accepted"))) {this.finish(job,"done","同一入站请求已经有动作，未重复生成。");return;}
    const context=this.buildContext(rel,events,source.id);
    const action:RuntimeAction={id:id("action"),eventId:source.id,contextId:context.id,createdAt:this.now(),status:"prepared",controlRevision:rel.revision,inboxRevision:rel.inboxRevision,policyRevision:rel.policyRevision,offerVersion:rel.product.offerVersion,components:planCard(rel.market,rel.product).map((part,ordinal)=>({id:id("component"),...part,ordinal,status:"prepared",attempts:0,receiptRef:null,submittedAt:null}))};
    rel.actions.push(action);rel.context=context;rel.status="processing";rel.nextStep="计划已冻结：先交付说明文本，再交付对应版本商品卡。";
    this.enqueue(rel.id,"execute",{actionId:action.id,componentId:action.components[0].id},this.now());
    this.audit(rel,"受限计划已生成",`上下文 ${context.id}；${context.messageIds.length} 条消息，${context.characterCount} 字符；技能 ${context.skillVersion}。`);
    this.save(rel);this.finish(job);
  }

  private buildContext(rel: Relation, events: EventRow[], sourceId: string):RuntimeContext {
    // The actual live request owns a reserved slot. A subsequent historical
    // import must never push the action's source fact out of its bounded context.
    const source=events.find(e=>e.id===sourceId);
    if(!source) throw new RuntimeError(409,"context_source","动作来源消息不存在。");
    const selectedIdsForContext=new Set([...events.filter(e=>e.id!==sourceId).slice(-5).map(e=>e.id),sourceId]);
    const selected=events.filter(e=>selectedIdsForContext.has(e.id));
    // Each message remains explicitly typed as live/history. Plans only act on
    // the live sourceId; imported text cannot grant action authority.
    const selectedIds=new Set(selected.map(e=>e.id));
    const evidence=rel.evidence.filter(e=>selectedIds.has(e.sourceRef)||(e.kind==="offer"&&e.sourceRef===`${rel.product.id}@${rel.product.offerVersion}`));
    const payload={relationshipId:rel.id,sourceEventId:sourceId,control:rel.control,controlRevision:rel.revision,inboxRevision:rel.inboxRevision,policyRevision:rel.policyRevision,marketingStopped:rel.marketingStopped,offer:{...rel.product},skill:{id:CARD_SKILL.id,version:CARD_SKILL.version,hash:CARD_SKILL.hash},messages:selected.map(e=>({id:e.id,mode:e.mode,kind:e.kind,text:e.text.slice(0,450)})),evidence:evidence.map(e=>({id:e.id,sourceRef:e.sourceRef,kind:e.kind,status:e.status,validUntil:e.validUntil}))};
    let encoded=JSON.stringify(payload);
    // Keep all six message references while reducing raw text if metadata grows.
    while(encoded.length>6000&&payload.messages.some(m=>m.text.length>0)) {
      for(const entry of payload.messages) entry.text=entry.text.slice(0,Math.max(0,entry.text.length-50));
      encoded=JSON.stringify(payload);
    }
    if(encoded.length>6000) throw new RuntimeError(500,"context_budget","上下文元数据超出 6000 字符预算。");
    const context:RuntimeContext={id:id("context"),createdAt:this.now(),relationshipId:rel.id,controlRevision:rel.revision,inboxRevision:rel.inboxRevision,policyRevision:rel.policyRevision,offerVersion:rel.product.offerVersion,evidenceRefs:evidence.map(e=>e.id),messageIds:selected.map(e=>e.id),skillId:CARD_SKILL.id,skillVersion:`${CARD_SKILL.version}+${CARD_SKILL.hash.slice(0,12)}`,characterCount:encoded.length,omittedMessages:events.length-selected.length,summary:`载入当前来源消息及本关系最近 ${selected.length-1} 条其他消息；动作来源 ${sourceId}；${rel.product.pid} / offer v${rel.product.offerVersion}；历史消息不提供发送授权。`};
    this.db.prepare("INSERT INTO contexts(id,relationship_id,body,payload) VALUES(?,?,?,?)").run(context.id,rel.id,JSON.stringify(context),encoded);
    return context;
  }

  private canSubmit(rel: Relation, action: RuntimeAction, component: RuntimeComponent) {
    const ctx=this.db.prepare("SELECT body FROM contexts WHERE id=? AND relationship_id=?").get(action.contextId,rel.id) as {body:string}|undefined;
    const context=ctx?JSON.parse(ctx.body) as RuntimeContext:null;
    return rel.control==="auto"&&action.controlRevision===rel.revision&&action.inboxRevision===rel.inboxRevision&&action.offerVersion===rel.product.offerVersion&&action.policyRevision===rel.policyRevision&&context?.policyRevision===action.policyRevision&&context.controlRevision===action.controlRevision&&context.inboxRevision===action.inboxRevision&&context.offerVersion===action.offerVersion&&context.messageIds.includes(action.eventId)&&rel.product.validUntil>this.now()&&component.status==="prepared"&&action.components.filter(c=>c.ordinal<component.ordinal).every(c=>c.status==="accepted");
  }

  private async execute(job: JobRow, workerId: string) {
    let submission:{componentId:string;content:string;relationshipId:string;dropReceipt:boolean}|null=null;
    this.transaction(()=>{
      if(!this.isOwned(job,workerId)) return;
      const rel=this.relation(job.relationship_id);const p=JSON.parse(job.payload) as JobPayload;
      const {action,component}=this.component(rel,p.actionId,p.componentId);
      if(!this.canSubmit(rel,action,component)) {
        if(component.status==="prepared") component.status="cancelled";
        this.actionStatus(action);
        if(rel.control==="auto") {rel.status=rel.product.validUntil<=this.now()?"needs_facts":"idle";rel.nextStep="提交前校验未通过；旧组件已失效，需依据最新事实重新规划。";}
        this.audit(rel,"提交前校验阻止了外发","控制、收件、政策、商品版本或前置组件发生变化。");
        this.save(rel);this.finish(job,"cancelled","提交前版本校验失败。");return;
      }
      // Durable boundary before the external call. Recovery MUST NOT call send again.
      component.status="submitting";component.attempts++;component.submittedAt=this.now();this.actionStatus(action);
      this.db.prepare("INSERT INTO component_attempts(id,component_id,action_id,started_at,status) VALUES(?,?,?,?,'submitting')").run(id("attempt"),component.id,action.id,this.now());
      this.save(rel);
      // Pedro's first card is accepted by the simulator, with its response lost.
      // A durable marker makes this failure deterministic across process restarts.
      const marker=this.db.prepare("SELECT value FROM runtime_meta WHERE key='pedro_card_receipt_lost'").get();
      submission={componentId:component.id,content:component.content,relationshipId:rel.id,dropReceipt:rel.id==="rt-pedro-br"&&component.kind==="product_card"&&!marker};
    });
    if(!submission) return;
    const send=submission as {componentId:string;content:string;relationshipId:string;dropReceipt:boolean};
    await this.beforePlatformSubmit(send.componentId);
    let receipt:ReceiptRow|null=null;
    this.transaction(()=>{
      if(!this.isOwned(job,workerId)) return;
      const rel=this.relation(job.relationship_id);const p=JSON.parse(job.payload) as JobPayload;const {action,component}=this.component(rel,p.actionId,p.componentId);
      // A user may take over between durable submission intent and the simulator call.
      // No platform receipt exists yet: mark this known-not-submitted attempt cancelled.
      if(rel.control!=="auto"||action.controlRevision!==rel.revision||action.inboxRevision!==rel.inboxRevision||action.policyRevision!==rel.policyRevision||action.offerVersion!==rel.product.offerVersion||rel.product.validUntil<=this.now()) {
        component.status="cancelled";this.actionStatus(action);this.db.prepare("UPDATE component_attempts SET status='cancelled' WHERE component_id=?").run(component.id);
        this.audit(rel,"平台提交前停止","提交意图已保存，但控制或业务条件在模拟平台调用前变化；平台未收到该组件。");this.save(rel);this.finish(job,"cancelled","平台调用前条件变化。");return;
      }
      const existing=this.db.prepare("SELECT * FROM platform_receipts WHERE component_id=?").get(send.componentId) as ReceiptRow|undefined;
      if(existing&&existing.payload_hash!==hash(send.content)) throw new RuntimeError(409,"platform_payload","冻结组件内容冲突。");
      const receiptId=existing?.receipt_id??id("sim-receipt");
      this.db.prepare("INSERT OR IGNORE INTO platform_receipts(component_id,receipt_id,accepted_at,payload_hash) VALUES(?,?,?,?)").run(send.componentId,receiptId,this.now(),hash(send.content));
      if(send.dropReceipt)this.db.prepare("INSERT OR IGNORE INTO runtime_meta(key,value) VALUES('pedro_card_receipt_lost','1')").run();
      receipt=this.db.prepare("SELECT * FROM platform_receipts WHERE component_id=?").get(send.componentId) as ReceiptRow;
    });
    if(!receipt) return;
    await this.afterPlatformSubmit(send.componentId);
    const platformReceipt=receipt as ReceiptRow;
    this.transaction(()=>{
      if(!this.isOwned(job,workerId)) return;
      const rel=this.relation(job.relationship_id);const p=JSON.parse(job.payload) as JobPayload;const {action,component}=this.component(rel,p.actionId,p.componentId);
      if(send.dropReceipt) {
        component.status="unknown";this.actionStatus(action);
        this.db.prepare("UPDATE component_attempts SET status='unknown' WHERE component_id=?").run(component.id);
        if(rel.control==="auto") {rel.status="waiting_verification";rel.nextStep="商品卡回执丢失，平台可能已接收；核验原组件，不重复发送。";}
        this.audit(rel,"组件回执丢失",`${component.kind} 的原尝试结果未知；前一文本组件的已接收事实保持。`);
        this.save(rel);this.finish(job,"blocked","平台回执丢失，等待只读核验。");
      } else {this.accept(rel,action,component,platformReceipt);this.save(rel);this.finish(job);}
    });
  }

  private accept(rel:Relation,action:RuntimeAction,component:RuntimeComponent,receipt:ReceiptRow) {
    component.status="accepted";component.receiptRef=receipt.receipt_id;this.actionStatus(action);
    this.db.prepare("UPDATE component_attempts SET status='accepted',receipt_ref=? WHERE component_id=?").run(receipt.receipt_id,component.id);
    // Verification closes the original blocked submission; it is no longer pending work.
    this.db.prepare("UPDATE jobs SET status='done',note=? WHERE relationship_id=? AND kind='execute' AND status='blocked' AND json_extract(payload,'$.componentId')=?").run("原提交已按核验回执收口，没有再次发送。",rel.id,component.id);
    if(!rel.evidence.some(e=>e.sourceRef===receipt.receipt_id))rel.evidence.push({id:id("evidence"),kind:"platform_receipt",sourceRef:receipt.receipt_id,observedAt:this.now(),validUntil:null,status:"valid",summary:`模拟平台确认已接收 ${component.kind}；组件 ${component.id}。不代表达人已采用。`});
    const next=action.components.find(c=>c.status==="prepared");
    if(next&&rel.control==="auto"&&action.inboxRevision===rel.inboxRevision&&action.controlRevision===rel.revision) this.enqueue(rel.id,"execute",{actionId:action.id,componentId:next.id},this.now());
    if(rel.control==="auto"&&action.inboxRevision===rel.inboxRevision) {
      rel.status=action.status==="accepted"?"waiting_creator":action.status==="cancelled"?"needs_facts":"processing";
      rel.nextStep=action.status==="accepted"?"两项组件均被模拟平台接收；等待达人明确回应。":action.status==="cancelled"?"原方案部分交付，其余组件已失效；等待新的明确请求。":"说明文本已接收，商品卡等待下一次执行。";
    }
    this.audit(rel,"组件接收证据已保存",`${component.kind} → ${receipt.receipt_id}；原组件共 ${component.attempts} 次提交尝试。`);
  }

  private verify(job:JobRow) {
    const rel=this.relation(job.relationship_id);const p=JSON.parse(job.payload) as JobPayload;const {action,component}=this.component(rel,p.actionId,p.componentId);
    if(component.status!=="unknown") {this.finish(job,"done","组件已不处于结果未知状态。");return;}
    const receipt=this.db.prepare("SELECT * FROM platform_receipts WHERE component_id=?").get(component.id) as ReceiptRow|undefined;
    if(!receipt) {this.audit(rel,"核验尚无结论","模拟平台未查到原组件回执；保持结果未知，不增加发送尝试。" );this.save(rel);this.finish(job,"blocked","尚无原组件的接收证据；禁止重发。");return;}
    this.accept(rel,action,component,receipt);this.save(rel);this.finish(job,"done","已通过原组件标识读取平台回执，没有调用发送。");
    // A newer service request may have waited behind this unknown operation.
    if(action.inboxRevision!==rel.inboxRevision) {this.replanLatest(rel);this.save(rel);}
  }

  private wake(job:JobRow) {
    const rel=this.relation(job.relationship_id);const p=JSON.parse(job.payload) as JobPayload;
    if(p.inboxRevision!==rel.inboxRevision) {this.finish(job,"cancelled","已有更新消息，旧延后任务不再适用。");return;}
    this.audit(rel,"延后任务已复查","到期检查当前关系、控制和商品条件；本步骤没有生成外发动作。");
    if(rel.control==="auto") {rel.status="needs_facts";rel.nextStep="延后复查已完成；等待新的明确请求或人工补充事实，不自动发送。";}
    this.save(rel);this.finish(job,"done","只复查，无外发。");
  }
}
