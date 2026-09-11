import {DatabaseSync} from "node:sqlite";
import {createHash,randomUUID} from "node:crypto";
import {mkdirSync} from "node:fs";
import {dirname,resolve} from "node:path";
import type {SecondPilotAction,SecondPilotCase,SecondPilotCaseInput,SecondPilotImportResult,SecondPilotLocalControl,SecondPilotOverview,SecondPilotScenario,SecondPilotSnapshot} from "../../features/second-pilot/contracts.ts";

export class SecondPilotError extends Error {
  readonly status:number; readonly code:string;
  constructor(status:number,code:string,message:string){super(message);this.name="SecondPilotError";this.status=status;this.code=code;}
}
type CaseRow={id:string;input:string;input_hash:string;revision:number;local_control:SecondPilotLocalControl;control_revision:number;updated_at:number};
type ActionRow={id:string;case_id:string;snapshot_id:string;created_at:number;scenario:SecondPilotScenario;status:SecondPilotAction["status"];attempts:number;fence:number;lease_owner:string|null;lease_until:number|null;submitted_at:number|null;receipt_ref:string|null;resolved_at:number|null;note:string};
type Receipt={id:string;action_id:string;payload_hash:string;accepted_at:number};
const fail=(status:number,code:string,message:string):never=>{throw new SecondPilotError(status,code,message);};
function stable(value:unknown):string {
  if(Array.isArray(value))return `[${value.map(stable).join(",")}]`;
  if(value&&typeof value==="object")return `{${Object.entries(value).filter(([,v])=>v!==undefined).sort(([a],[b])=>a.localeCompare(b)).map(([k,v])=>`${JSON.stringify(k)}:${stable(v)}`).join(",")}}`;
  return JSON.stringify(value);
}
function hash(value:unknown){return createHash("sha256").update(stable(value)).digest("hex");}
function key(value:unknown,name:string):string {
  if(typeof value!=="string"||!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$/.test(value))fail(400,"invalid_input",`${name} 无效。`);
  return value as string;
}
function str(value:unknown,name:string,max:number):string {
  if(typeof value!=="string"||!value.trim()||value.length>max)fail(400,"invalid_input",`${name} 为空或过长。`);
  return value as string;
}
function inputCase(value:SecondPilotCaseInput):SecondPilotCaseInput {
  if(!value||typeof value!=="object"||Object.keys(value).some(k=>!["id","sourceFingerprint","creatorId","creatorRef","handle","market","products","draft","liveBlockers"].includes(k)))fail(400,"invalid_input","二发来源字段无效。");
  key(value.id,"case id");key(value.creatorId,"creator id");str(value.sourceFingerprint,"来源指纹",200);
  if(value.market!=="it"||value.creatorRef?.namespace!=="kalodata")fail(400,"invalid_input","此试点只接受意大利 Kalodata 来源。");
  key(value.creatorRef.id,"source identity");str(value.handle,"handle",200);
  if(!Array.isArray(value.products)||value.products.length<1||value.products.length>5)fail(400,"invalid_input","每位达人需要 1–5 项同品证据。");
  const seen=new Set<string>();
  for(const p of value.products){
    key(p.productId,"product id");if(typeof p.pid!=="string"||!/^\d{8,30}$/.test(p.pid)||seen.has(p.pid))fail(400,"invalid_input","PID 无效或重复。");seen.add(p.pid);
    str(p.title,"商品标题",1000);str(p.italianName,"意语商品名",300);
    if(!Number.isSafeInteger(p.units)||p.units<=0)fail(400,"invalid_input","二发必须保留精确商品正销量。");
    if(!p.source||!Number.isSafeInteger(p.source.observedAt)||p.source.observedAt<0)fail(400,"invalid_input","来源时间无效。");
    str(p.source.ref,"来源",1000);
    for(const n of [p.source.windowStart,p.source.windowEnd])if(n!==null&&(!Number.isSafeInteger(n)||n<0))fail(400,"invalid_input","销量窗口无效。");
    if(p.source.windowStart!==null&&p.source.windowEnd!==null&&p.source.windowEnd<p.source.windowStart)fail(400,"invalid_input","销量窗口顺序无效。");
  }
  if(!value.draft||value.draft.language!=="it"||value.draft.method!=="deterministic")fail(400,"invalid_input","只接受有版本的意语确定性草稿。");
  str(value.draft.text,"草稿",6000);str(value.draft.skillVersion,"技能版本",200);
  if(!Array.isArray(value.draft.claimRefs)||!value.draft.claimRefs.length||value.draft.claimRefs.length>20)fail(400,"invalid_input","缺少草稿事实来源。");
  for(const ref of value.draft.claimRefs)str(ref,"引用",1000);
  if(!Array.isArray(value.liveBlockers)||!value.liveBlockers.length||value.liveBlockers.length>30)fail(400,"invalid_input","真实执行缺口必须显式保留。");
  for(const blocker of value.liveBlockers)str(blocker,"执行缺口",1000);
  return structuredClone(value);
}
function action(row:ActionRow):SecondPilotAction {
  return {id:row.id,caseId:row.case_id,snapshotId:row.snapshot_id,createdAt:row.created_at,mode:"dry_run",transport:"local-simulator",componentKind:"text",scenario:row.scenario,status:row.status,attempts:row.attempts,fence:row.fence,leaseOwner:row.lease_owner,leaseUntil:row.lease_until,submittedAt:row.submitted_at,receiptRef:row.receipt_ref,resolvedAt:row.resolved_at,note:row.note};
}

// This store has no platform connector, credentials, model client or implicit seed.
// Local controls belong to the experiment; they never alter real relationship facts.
export class SecondPilotStore {
  private db:DatabaseSync; private now:()=>number; private leaseMs:number;
  constructor(path:string,options:{now?:()=>number;leaseMs?:number}={}){
    const file=resolve(path);mkdirSync(dirname(file),{recursive:true});this.now=options.now??Date.now;this.leaseMs=options.leaseMs??15_000;
    if(!Number.isSafeInteger(this.leaseMs)||this.leaseMs<1)fail(400,"invalid_input","租约时长无效。");
    this.db=new DatabaseSync(file);this.db.exec("PRAGMA busy_timeout=5000; PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;");
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS pilot_cases(id TEXT PRIMARY KEY,input TEXT NOT NULL,input_hash TEXT NOT NULL,revision INTEGER NOT NULL,local_control TEXT NOT NULL,control_revision INTEGER NOT NULL,updated_at INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS pilot_snapshots(id TEXT PRIMARY KEY,case_id TEXT NOT NULL REFERENCES pilot_cases(id),fingerprint TEXT NOT NULL UNIQUE,body TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS pilot_actions(id TEXT PRIMARY KEY,case_id TEXT NOT NULL REFERENCES pilot_cases(id),snapshot_id TEXT NOT NULL UNIQUE REFERENCES pilot_snapshots(id),created_at INTEGER NOT NULL,scenario TEXT NOT NULL,status TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,fence INTEGER NOT NULL DEFAULT 0,lease_owner TEXT,lease_until INTEGER,submitted_at INTEGER,receipt_ref TEXT,resolved_at INTEGER,note TEXT NOT NULL);
      CREATE INDEX IF NOT EXISTS pilot_actions_due ON pilot_actions(status,lease_until,created_at);
      CREATE INDEX IF NOT EXISTS pilot_actions_case ON pilot_actions(case_id,status);
      CREATE TABLE IF NOT EXISTS pilot_attempts(action_id TEXT PRIMARY KEY REFERENCES pilot_actions(id),started_at INTEGER NOT NULL,payload_hash TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS pilot_receipts(id TEXT PRIMARY KEY,action_id TEXT NOT NULL UNIQUE REFERENCES pilot_actions(id),payload_hash TEXT NOT NULL,accepted_at INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS pilot_requests(id TEXT PRIMARY KEY,payload_hash TEXT NOT NULL,result_kind TEXT NOT NULL,result_id TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS pilot_workers(id TEXT PRIMARY KEY,seen_at INTEGER NOT NULL);
    `);
  }
  close(){this.db.close();}
  private tx<T>(fn:()=>T):T {this.db.exec("BEGIN IMMEDIATE");try{const result=fn();this.db.exec("COMMIT");return result;}catch(e){this.db.exec("ROLLBACK");throw e;}}
  private row(id:string):CaseRow {const row=this.db.prepare("SELECT * FROM pilot_cases WHERE id=?").get(key(id,"case id")) as CaseRow|undefined;return row??fail(404,"case_missing","二发机会不存在。");}
  private actionRow(id:string):ActionRow {const row=this.db.prepare("SELECT * FROM pilot_actions WHERE id=?").get(key(id,"action id")) as ActionRow|undefined;return row??fail(404,"action_missing","模拟意图不存在。");}
  private snapshot(id:string):SecondPilotSnapshot {const row=this.db.prepare("SELECT body FROM pilot_snapshots WHERE id=?").get(key(id,"snapshot id")) as {body:string}|undefined;return row?JSON.parse(row.body):fail(404,"snapshot_missing","发送前快照不存在。");}
  private checkRevision(row:CaseRow,revision:number){if(!Number.isSafeInteger(revision)||row.revision!==revision)fail(409,"revision_conflict","机会版本已变化，请刷新后操作。");}
  private request<T>(requestId:string,payload:unknown,kind:"case"|"snapshot"|"action",fn:()=>T):T {
    key(requestId,"request id");const payloadHash=hash(payload),previous=this.db.prepare("SELECT * FROM pilot_requests WHERE id=?").get(requestId) as {payload_hash:string;result_kind:string;result_id:string}|undefined;
    if(previous){if(previous.payload_hash!==payloadHash||previous.result_kind!==kind)fail(409,"request_conflict","请求标识已被不同内容使用。");return (kind==="case"?this.get(previous.result_id):kind==="snapshot"?this.snapshot(previous.result_id):action(this.actionRow(previous.result_id))) as T;}
    const result=fn();this.db.prepare("INSERT INTO pilot_requests VALUES(?,?,?,?)").run(requestId,payloadHash,kind,(result as {id:string}).id);return result;
  }
  private cancelQueued(caseId:string,note:string){this.db.prepare("UPDATE pilot_actions SET status='cancelled',resolved_at=?,lease_owner=NULL,lease_until=NULL,note=? WHERE case_id=? AND status='queued'").run(this.now(),note,caseId);}
  importCases(inputs:SecondPilotCaseInput[]):SecondPilotImportResult {
    if(!Array.isArray(inputs)||inputs.length>10_000)fail(400,"invalid_input","导入规模无效。");const parsed=inputs.map(inputCase),ids=new Set<string>(),creators=new Set<string>();
    for(const c of parsed){if(ids.has(c.id))fail(400,"invalid_input","同批机会标识重复。");ids.add(c.id);const identity=`${c.market}:${c.creatorRef.namespace}:${c.creatorRef.id}`;if(creators.has(identity))fail(409,"identity_duplicate","同一来源达人必须合并为一个机会。");creators.add(identity);}
    return this.tx(()=>{const counts={inserted:0,updated:0,unchanged:0};for(const input of parsed){const encoded=stable(input),fingerprint=hash(input),old=this.db.prepare("SELECT * FROM pilot_cases WHERE id=?").get(input.id) as CaseRow|undefined;
      if(this.db.prepare("SELECT 1 FROM pilot_cases WHERE id<>? AND json_extract(input,'$.market')=? AND (json_extract(input,'$.creatorId')=? OR (json_extract(input,'$.creatorRef.namespace')=? AND json_extract(input,'$.creatorRef.id')=?))").get(input.id,input.market,input.creatorId,input.creatorRef.namespace,input.creatorRef.id))fail(409,"identity_duplicate","该达人已有机会，不能通过新标识绕过归并和未知结果。");
      if(!old){this.db.prepare("INSERT INTO pilot_cases VALUES(?,?,?,1,'running',1,?)").run(input.id,encoded,fingerprint,this.now());counts.inserted++;continue;}
      const oldInput=JSON.parse(old.input) as SecondPilotCaseInput;
      if(oldInput.creatorId!==input.creatorId||stable(oldInput.creatorRef)!==stable(input.creatorRef)||oldInput.market!==input.market)fail(409,"identity_conflict","稳定机会不能改绑到另一位达人。");
      if(old.input_hash===fingerprint){counts.unchanged++;continue;}
      this.db.prepare("UPDATE pilot_cases SET input=?,input_hash=?,revision=revision+1,updated_at=? WHERE id=?").run(encoded,fingerprint,this.now(),input.id);this.cancelQueued(input.id,"来源或草稿发生变化，尚未提交的旧意图已取消。");counts.updated++;
    }return counts;});
  }
  get(caseId:string):SecondPilotCase {
    const row=this.row(caseId);return {...JSON.parse(row.input),revision:row.revision,localControl:row.local_control,localControlRevision:row.control_revision,updatedAt:row.updated_at,realIdentityStatus:"unverified",realControl:"unknown",realMarketingStopped:null,snapshots:(this.db.prepare("SELECT body FROM pilot_snapshots WHERE case_id=? ORDER BY rowid").all(row.id) as {body:string}[]).map(r=>JSON.parse(r.body)),actions:(this.db.prepare("SELECT * FROM pilot_actions WHERE case_id=? ORDER BY rowid").all(row.id) as ActionRow[]).map(action)};
  }
  list(query:{offset?:number;limit?:number}={}):{items:SecondPilotCase[];total:number;offset:number;limit:number}{
    const offset=query.offset??0,limit=query.limit??50;if(!Number.isSafeInteger(offset)||offset<0||!Number.isSafeInteger(limit)||limit<1||limit>200)fail(400,"invalid_input","分页参数无效。");
    this.db.exec("BEGIN");try{const rows=this.db.prepare("SELECT id FROM pilot_cases ORDER BY id LIMIT ? OFFSET ?").all(limit,offset) as {id:string}[];const total=(this.db.prepare("SELECT COUNT(*) n FROM pilot_cases").get() as {n:number}).n;const result={items:rows.map(r=>this.get(r.id)),total,offset,limit};this.db.exec("COMMIT");return result;}catch(e){this.db.exec("ROLLBACK");throw e;}
  }
  overview():SecondPilotOverview {
    this.db.exec("BEGIN");try{const inputs=(this.db.prepare("SELECT input,local_control FROM pilot_cases").all() as {input:string;local_control:string}[]),states=this.db.prepare("SELECT status,COUNT(*) n FROM pilot_actions GROUP BY status").all() as {status:string;n:number}[],counts=Object.fromEntries(states.map(r=>[r.status,r.n])),products=new Set<string>();let edges=0;
      for(const row of inputs)for(const p of (JSON.parse(row.input) as SecondPilotCaseInput).products){products.add(p.pid);edges++;}
      const count=(table:string)=>(this.db.prepare(`SELECT COUNT(*) n FROM ${table}`).get() as {n:number}).n;
      const seen=(this.db.prepare("SELECT MAX(seen_at) n FROM pilot_workers").get() as {n:number|null}).n;
      const result:SecondPilotOverview={mode:"dry_run",transport:"local-simulator",cases:inputs.length,products:products.size,edges,frozen:count("pilot_snapshots"),queued:counts.queued??0,submitting:counts.submitting??0,simulatedAccepted:counts.simulated_accepted??0,resultUnknown:counts.result_unknown??0,cancelled:counts.cancelled??0,paused:inputs.filter(r=>r.local_control==="paused").length,attempts:count("pilot_attempts"),simulatedReceipts:count("pilot_receipts"),realSends:0,modelCalls:0,worker:{online:seen!==null&&this.now()-seen<Math.max(20_000,this.leaseMs*2),lastSeenAt:seen}};this.db.exec("COMMIT");return result;
    }catch(e){this.db.exec("ROLLBACK");throw e;}
  }
  freeze(caseId:string,expectedRevision:number,requestId:string):SecondPilotSnapshot {return this.tx(()=>this.request(requestId,{type:"freeze",caseId,expectedRevision},"snapshot",()=>{
    const row=this.row(caseId);this.checkRevision(row,expectedRevision);if(row.local_control!=="running")fail(409,"local_paused","本地试验已暂停。");
    if(this.db.prepare("SELECT 1 FROM pilot_actions WHERE case_id=? AND status IN ('submitting','result_unknown')").get(caseId))fail(409,"unresolved_attempt","此关系的原提交结果未明确，不能生成新的执行快照。");
    const fingerprint=hash({caseId,revision:row.revision,controlRevision:row.control_revision,inputHash:row.input_hash}),old=this.db.prepare("SELECT body FROM pilot_snapshots WHERE fingerprint=?").get(fingerprint) as {body:string}|undefined;if(old)return JSON.parse(old.body);
    const input=JSON.parse(row.input) as SecondPilotCaseInput,snapshot:SecondPilotSnapshot={id:`second-snapshot-${randomUUID()}`,caseId,createdAt:this.now(),revision:row.revision,localControlRevision:row.control_revision,fingerprint,sourceFingerprint:input.sourceFingerprint,draftHash:hash(input.draft),input,mode:"dry_run",transport:"local-simulator",liveExecutable:false,realIdentityStatus:"unverified",realControl:"unknown",realMarketingStopped:null};
    this.db.prepare("INSERT INTO pilot_snapshots VALUES(?,?,?,?)").run(snapshot.id,caseId,fingerprint,JSON.stringify(snapshot));return snapshot;
  }));}
  private current(snapshot:SecondPilotSnapshot,row:CaseRow){return row.local_control==="running"&&snapshot.revision===row.revision&&snapshot.localControlRevision===row.control_revision&&hash(snapshot.input)===row.input_hash&&snapshot.draftHash===hash(snapshot.input.draft);}
  queue(snapshotId:string,scenario:SecondPilotScenario,requestId:string):SecondPilotAction {
    if(!["accepted","receipt_lost","before_submit_crash"].includes(scenario))fail(400,"invalid_scenario","只支持明确的本地模拟场景。");
    return this.tx(()=>this.request(requestId,{type:"queue",snapshotId,scenario},"action",()=>{
      const snapshot=this.snapshot(snapshotId),row=this.row(snapshot.caseId),old=this.db.prepare("SELECT * FROM pilot_actions WHERE snapshot_id=?").get(snapshotId) as ActionRow|undefined;
      if(old){if(old.scenario!==scenario)fail(409,"scenario_conflict","冻结意图的模拟场景不能被更换。");return action(old);}
      if(!this.current(snapshot,row))fail(409,"stale_snapshot","控制、来源或草稿已经变化，请重新冻结快照。");
      if(this.db.prepare("SELECT 1 FROM pilot_actions WHERE case_id=? AND status IN ('queued','submitting','result_unknown')").get(row.id))fail(409,"unresolved_attempt","此关系已有待处理或结果未知的意图。");
      if(this.db.prepare("SELECT 1 FROM pilot_actions WHERE case_id=? AND status='simulated_accepted'").get(row.id))fail(409,"case_completed","本轮机会已有模拟接收证据，不重复提交。");
      const id=`second-action-${randomUUID()}`;this.db.prepare("INSERT INTO pilot_actions(id,case_id,snapshot_id,created_at,scenario,status,note) VALUES(?,?,?,?,?,'queued',?)").run(id,row.id,snapshotId,this.now(),scenario,"已登记本地模拟任务；真实达人不会收到消息。");return action(this.actionRow(id));
    }));
  }
  control(caseId:string,expectedRevision:number,mode:SecondPilotLocalControl,requestId:string):SecondPilotCase {
    if(mode!=="running"&&mode!=="paused")fail(400,"invalid_input","本地控制模式无效。");
    return this.tx(()=>this.request(requestId,{type:"control",caseId,expectedRevision,mode},"case",()=>{
      const row=this.row(caseId);this.checkRevision(row,expectedRevision);if(row.local_control!==mode){this.db.prepare("UPDATE pilot_cases SET local_control=?,revision=revision+1,control_revision=control_revision+1,updated_at=? WHERE id=?").run(mode,this.now(),caseId);this.cancelQueued(caseId,"本地试验控制变化，尚未提交的旧意图已取消。");}return this.get(caseId);
    }));
  }
  verify(actionId:string,requestId:string):SecondPilotAction {return this.tx(()=>this.request(requestId,{type:"verify",actionId},"action",()=>{
    const row=this.actionRow(actionId);if(row.status!=="result_unknown")return action(row);
    const receipt=this.db.prepare("SELECT * FROM pilot_receipts WHERE action_id=?").get(row.id) as Receipt|undefined;
    if(!receipt){this.db.prepare("UPDATE pilot_actions SET note=? WHERE id=?").run("核验未找到原意图回执；继续保持结果未知，不增加提交尝试。",row.id);return action(this.actionRow(row.id));}
    this.accept(row,receipt);return action(this.actionRow(row.id));
  }));}
  private owned(row:ActionRow,workerId:string){const current=this.actionRow(row.id);return current.status==="submitting"&&current.fence===row.fence&&current.lease_owner===workerId&&current.lease_until!==null&&current.lease_until>this.now();}
  private accept(row:ActionRow,receipt:Receipt){
    const snapshot=this.snapshot(row.snapshot_id);if(receipt.payload_hash!==hash(snapshot.input.draft.text))fail(409,"receipt_mismatch","原回执内容与冻结草稿不一致。");
    this.db.prepare("UPDATE pilot_actions SET status='simulated_accepted',receipt_ref=?,resolved_at=?,lease_owner=NULL,lease_until=NULL,note=? WHERE id=?").run(receipt.id,this.now(),"原模拟平台回执已核实；不代表真实送达、回复或合作。",row.id);
  }
  protected async beforeSubmit(_actionId:string):Promise<void>{}
  protected async afterSubmit(_actionId:string):Promise<void>{}
  async tick(workerId:string):Promise<boolean>{
    key(workerId,"worker id");const claimed=this.tx(()=>{
      this.db.prepare("INSERT INTO pilot_workers VALUES(?,?) ON CONFLICT(id) DO UPDATE SET seen_at=excluded.seen_at").run(workerId,this.now());
      const row=this.db.prepare("SELECT * FROM pilot_actions WHERE status='queued' OR (status='submitting' AND lease_until<=?) ORDER BY created_at,rowid LIMIT 1").get(this.now()) as ActionRow|undefined;if(!row)return null;
      if(row.status==="submitting"){
        this.db.prepare("UPDATE pilot_actions SET status='result_unknown',fence=fence+1,lease_owner=NULL,lease_until=NULL,note=? WHERE id=?").run("提交租约到期，恢复为结果未知；只允许核验原意图。",row.id);return {recovered:true,row};
      }
      const snapshot=this.snapshot(row.snapshot_id),c=this.row(row.case_id);if(!this.current(snapshot,c)){
        this.db.prepare("UPDATE pilot_actions SET status='cancelled',resolved_at=?,note=? WHERE id=?").run(this.now(),"提交前快照校验未通过，未调用模拟平台。",row.id);return {recovered:true,row};
      }
      this.db.prepare("INSERT INTO pilot_attempts VALUES(?,?,?)").run(row.id,this.now(),hash(snapshot.input.draft.text));
      this.db.prepare("UPDATE pilot_actions SET status='submitting',attempts=attempts+1,fence=fence+1,lease_owner=?,lease_until=?,submitted_at=?,note=? WHERE id=?").run(workerId,this.now()+this.leaseMs,this.now(),"提交意图已持久化，等待本地模拟器记录。",row.id);return {recovered:false,row:this.actionRow(row.id)};
    });
    if(!claimed)return false;if(claimed.recovered)return true;const row=claimed.row;
    await this.beforeSubmit(row.id);
    if(row.scenario==="before_submit_crash")return true;
    const receipt=this.tx(()=>{
      if(!this.owned(row,workerId))return null;const snapshot=this.snapshot(row.snapshot_id),c=this.row(row.case_id);
      if(!this.current(snapshot,c)){
        this.db.prepare("UPDATE pilot_actions SET status='cancelled',resolved_at=?,lease_owner=NULL,lease_until=NULL,note=? WHERE id=?").run(this.now(),"模拟器调用前条件变化，原尝试已停止且没有回执。",row.id);return null;
      }
      const id=`second-sim-receipt-${randomUUID()}`;this.db.prepare("INSERT INTO pilot_receipts VALUES(?,?,?,?)").run(id,row.id,hash(snapshot.input.draft.text),this.now());return this.db.prepare("SELECT * FROM pilot_receipts WHERE action_id=?").get(row.id) as Receipt;
    });
    if(!receipt)return true;await this.afterSubmit(row.id);
    this.tx(()=>{
      if(!this.owned(row,workerId))return;
      if(row.scenario==="receipt_lost")this.db.prepare("UPDATE pilot_actions SET status='result_unknown',lease_owner=NULL,lease_until=NULL,note=? WHERE id=?").run("模拟平台已处理但响应丢失；核验同一意图，不重新提交。",row.id);
      else this.accept(row,receipt);
    });return true;
  }
}
