import type {
  CollaborationStatus, ConversationDetail, ConversationList, ConversationView, PendingManualReply, TimelineItem,
} from "../../server/conversations/bridge.ts";

export type DecisionTrace = {decisionId:string;input:{prompt:string;context:unknown};decision:unknown;state:string};
type ManualOutcome = "normal" | "paid" | "rejected";
type Snapshot = {
  view:ConversationView;query:string;list:ConversationList|null;selected:string|null;
  detail:ConversationDetail|null;draft:string;busy:boolean;error:string;
  translations:Record<string,string>;translationBusy:string|null;manualOutcome:ManualOutcome;
  trace:DecisionTrace|null;pendingManualReplies:PendingManualReply[];
  olderTimeline:TimelineItem[];olderCursor:string|null;olderBusy:boolean;draftNotice:string;
};
type Fetcher = (input:string, init?:RequestInit) => Promise<Response>;
/** One conversation's editor text: local edits are never replaced by a server read. */
type DraftState = {text:string;dirty:boolean;serverRevision:number};
/** A refused POST, with the backend's statement of whether any send intent exists. */
export class RequestError extends Error {
  status:number;body:Record<string,unknown>|null;
  constructor(status:number, body:Record<string,unknown>|null) {super("request_failed");this.status=status;this.body=body;}
  get intent() {return typeof this.body?.intent === "string" ? this.body.intent : "unknown";}
  get code() {return typeof this.body?.error === "string" ? this.body.error : "";}
}
const refusal = (code:string) => ({
  input_too_large:"文本超过长度上限，未发送；请缩短后再发。",
  invalid_conversation_request:"内容不符合发送要求（为空或超长），未发送。",
  manual_reply_context_changed:"达人状态已变化，未发送；请刷新后核对再发。",
  manual_reply_invalid:"内容不符合发送要求，未发送。",
  manual_card_stale:"商品卡材料已失效，未发送。",
  manual_card_missing:"没有可发送的商品卡，未发送。",
  market_conversation_send_pending:"当前市场人工发送尚未开放，未发送。",
} as Record<string,string>)[code] ?? `发送未提交（${code || "原因未知"}），草稿已保留。`;

/** Owns the page's asynchronous interaction state. Every response belongs to one selection. */
export class ConversationController {
  private snapshot:Snapshot = {
    view:"human",query:"",list:null,selected:null,detail:null,draft:"",busy:false,error:"",
    translations:{},translationBusy:null,manualOutcome:"normal",trace:null,pendingManualReplies:[],
    olderTimeline:[],olderCursor:null,olderBusy:false,draftNotice:"",
  };
  private drafts = new Map<string, DraftState>();
  private listeners = new Set<() => void>();
  private selectionVersion = 0;
  private listVersion = 0;
  private detailVersion = 0;
  private draftVersion = 0;
  private disposed = false;
  private listAbort:AbortController|null = null;
  private detailAbort:AbortController|null = null;
  private pending = new Map<string, PendingManualReply[]>();
  private mutationIds = new Map<string, string>();
  readonly market:string;
  private fetcher:Fetcher;
  private newId:() => string;

  constructor(
    market:string,
    // Browsers reject window.fetch called as a method of another object ("Illegal invocation"),
    // and every call below is this.fetcher(...), so the default must not be the bare function.
    fetcher:Fetcher = (input, init) => fetch(input, init),
    newId = () => crypto.randomUUID(),
  ) {this.market=market;this.fetcher=fetcher;this.newId=newId;}

  subscribe = (listener:() => void) => {this.listeners.add(listener);return () => {this.listeners.delete(listener);};};
  getSnapshot = () => this.snapshot;
  private update(patch:Partial<Snapshot>) {
    if(this.disposed)return;
    this.snapshot = {...this.snapshot,...patch};
    this.listeners.forEach(listener => listener());
  }
  activate() {this.disposed = false;}
  dispose() {
    this.disposed = true;
    this.listVersion++;this.detailVersion++;this.selectionVersion++;
    this.listAbort?.abort();this.detailAbort?.abort();
  }
  private current(cid:string, version:number) {
    return !this.disposed && this.snapshot.selected === cid && this.selectionVersion === version;
  }
  setDraft = (draft:string) => {
    this.draftVersion++;
    const cid = this.snapshot.selected;
    if(cid) {
      const prior = this.drafts.get(cid);
      this.drafts.set(cid,{text:draft,dirty:true,serverRevision:prior?.serverRevision ?? this.snapshot.detail?.draft.revision ?? 0});
    }
    this.update({draft});
  };
  setManualOutcome = (manualOutcome:ManualOutcome) => this.update({manualOutcome});
  select = (cid:string|null) => {
    this.selectionVersion++;this.detailVersion++;this.draftVersion++;
    this.detailAbort?.abort();
    const kept = cid ? this.drafts.get(cid) : undefined;
    this.update({selected:cid,detail:null,draft:kept?.dirty ? kept.text : "",translations:{},translationBusy:null,trace:null,
      error:"",draftNotice:"",olderTimeline:[],olderCursor:null,olderBusy:false,
      pendingManualReplies:cid ? this.pending.get(cid) ?? [] : []});
    if(cid)void this.loadDetail(cid);
  };
  setFilter = (view:ConversationView, query:string) => {
    this.listVersion++;this.listAbort?.abort();
    this.update({view,query,list:null});
    this.select(null);
    void this.loadList();
  };

  loadList = async () => {
    const version = ++this.listVersion;
    this.listAbort?.abort();
    const abort = new AbortController();this.listAbort = abort;
    const {view,query} = this.snapshot;
    try {
      const params = new URLSearchParams({market:this.market,view,query});
      const response = await this.fetcher(`/api/conversations?${params}`,{cache:"no-store",signal:abort.signal});
      if(!response.ok)throw Error();
      const list:ConversationList = await response.json();
      if(this.disposed || version !== this.listVersion)return;
      if(list.view !== view || list.query !== query || list.offset !== 0)throw Error();
      // A refreshed first page is authoritative: removed queue members must not survive locally.
      this.update({list,error:""});
      if(list.total === 0)this.select(null);
      else if(!this.snapshot.selected && list.items[0]?.conversationId)this.select(list.items[0].conversationId);
    } catch {
      if(!abort.signal.aborted && version === this.listVersion)this.update({error:"暂时无法读取会话队列。"});
    }
  };

  loadMore = async () => {
    const initial = this.snapshot.list;
    if(initial?.nextOffset == null)return;
    // The cursor is a queue position, so a long queue is walked to its end; offsets stop at 5000.
    const version = this.listVersion, offset = initial.nextOffset, cursor = initial.nextCursor ?? null;
    const {view,query} = this.snapshot;
    try {
      const params = new URLSearchParams({market:this.market,view,query,...(cursor ? {after:cursor} : {offset:String(offset)})});
      const response = await this.fetcher(`/api/conversations?${params}`,{cache:"no-store"});
      if(!response.ok)throw Error();
      const more:ConversationList = await response.json();
      const current = this.snapshot.list;
      if(this.disposed || version !== this.listVersion || current?.nextOffset !== offset || (current.nextCursor ?? null) !== cursor)return;
      if(more.view !== view || more.query !== query || (!cursor && more.offset !== offset))throw Error();
      const seen = new Set(current.items.map(row => row.creatorId));
      this.update({list:{...more,offset:0,items:[...current.items,...more.items.filter(row => !seen.has(row.creatorId))]}});
    } catch {
      if(version === this.listVersion)this.update({error:"下一页会话暂时无法读取。"});
    }
  };

  private rememberPending(cid:string, items:PendingManualReply[]) {
    this.pending.set(cid,items);
    if(this.snapshot.selected === cid)this.update({pendingManualReplies:items});
  }
  /** Periodic refresh: queue plus the open conversation's facts; never the local draft. */
  refresh = async () => {
    const cid = this.snapshot.selected;
    await this.loadList();
    if(cid && this.snapshot.selected === cid && !this.snapshot.busy)await this.loadDetail(cid,true);
  };
  loadOlder = async () => {
    const {selected:cid,olderCursor,olderBusy} = this.snapshot;
    if(!cid || !olderCursor || olderBusy)return;
    const selection = this.selectionVersion;
    this.update({olderBusy:true});
    try {
      const params = new URLSearchParams({market:this.market,cid,before:olderCursor});
      const response = await this.fetcher(`/api/conversations?${params}`,{cache:"no-store"});
      if(!response.ok)throw Error();
      const page:ConversationDetail = await response.json();
      if(!this.current(cid,selection) || this.snapshot.olderCursor !== olderCursor)return;
      if(page.conversationId !== cid)throw Error();
      const seen = new Set([...this.snapshot.olderTimeline,...(this.snapshot.detail?.timeline ?? [])].map(item => item.id));
      this.update({olderTimeline:[...page.timeline.filter(item => !seen.has(item.id)),...this.snapshot.olderTimeline],
        olderCursor:page.timelineCursor});
    } catch {if(this.current(cid,selection))this.update({error:"更早的消息暂时无法读取；当前消息和发送状态不受影响。"});}
    finally {if(this.current(cid,selection))this.update({olderBusy:false});}
  };
  private async loadDetail(cid:string, refresh = false) {
    if(this.snapshot.selected !== cid)return;
    const version = ++this.detailVersion, selection = this.selectionVersion;
    this.detailAbort?.abort();
    const abort = new AbortController();this.detailAbort = abort;
    try {
      const response = await this.fetcher(`/api/conversations?market=${encodeURIComponent(this.market)}&cid=${cid}`,
        {cache:"no-store",signal:abort.signal});
      if(!response.ok)throw Error();
      const detail:ConversationDetail = await response.json();
      if(!this.current(cid,selection) || version !== this.detailVersion)return;
      if(detail.conversationId !== cid)throw Error();
      const known = this.pending.get(cid) ?? [];
      const requestIds = new Set(detail.pendingManualReplies.map(row => row.requestId));
      this.rememberPending(cid,[...detail.pendingManualReplies,...known.filter(row => !requestIds.has(row.requestId))]);
      const local = this.drafts.get(cid);
      let draft = this.snapshot.draft, draftNotice = this.snapshot.draftNotice;
      if(local?.dirty) {
        // Unsaved text stays; a newer server copy is announced, never silently applied.
        if(detail.draft.revision !== local.serverRevision)draftNotice = "另一个窗口保存了新草稿，本地未保存的内容已保留。";
      } else {
        draft = detail.draft.text;draftNotice = "";
        this.drafts.set(cid,{text:draft,dirty:false,serverRevision:detail.draft.revision});
        if(draft !== this.snapshot.draft)this.draftVersion++;
      }
      if(refresh) {
        // The open page keeps its translations, trace, chosen outcome and loaded history.
        this.update({detail,draft,draftNotice,olderCursor:this.snapshot.olderTimeline.length ? this.snapshot.olderCursor : detail.timelineCursor});
      } else {
        this.update({detail,draft,draftNotice,translations:{},trace:null,olderTimeline:[],olderCursor:detail.timelineCursor,
          manualOutcome:detail.creator.collaboration.status === "paid" ? "paid" :
            detail.creator.collaboration.status === "rejected" ? "rejected" : "normal"});
      }
    } catch {
      if(!abort.signal.aborted && this.current(cid,selection))
        this.update({error:refresh ? "会话内容暂时无法刷新，正在显示上次读取的内容。" : "暂时无法读取会话内容。"});
    }
  }

  private async post(body:Record<string,unknown>) {
    const response = await this.fetcher(`/api/conversations?market=${encodeURIComponent(this.market)}`,{
      method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({...body,market:this.market}),
    });
    if(!response.ok) {
      let value:Record<string,unknown>|null = null;
      try {value = await response.json();} catch {value = null;}
      throw new RequestError(response.status,value && typeof value === "object" ? value : null);
    }
    return response.json();
  }
  private async change(body:Record<string,unknown>, error:string, after?:() => Promise<void>) {
    const cid = this.snapshot.detail?.conversationId, selection = this.selectionVersion;
    if(!cid || this.snapshot.busy)return;
    this.update({busy:true,error:""});
    try {
      await this.post(body);
      if(this.current(cid,selection))await (after ? after() : this.loadDetail(cid));
    } catch {if(this.current(cid,selection))this.update({error});}
    finally {this.update({busy:false});}
  }
  saveDraft = async () => {
    const {detail,draft,busy} = this.snapshot;if(!detail || busy)return;
    const cid = detail.conversationId, selection = this.selectionVersion;
    const local = this.drafts.get(cid);
    this.update({busy:true,error:""});
    try {
      const saved = await this.post({action:"save_draft",cid,text:draft,
        expectedRevision:local?.serverRevision ?? detail.draft.revision});
      const current = this.current(cid,selection) ? this.snapshot.draft : this.drafts.get(cid)?.text ?? draft;
      this.drafts.set(cid,{text:current,dirty:current !== draft,serverRevision:Number(saved.revision)});
      if(this.current(cid,selection)) {this.update({draftNotice:""});await this.loadDetail(cid,true);}
    } catch {if(this.current(cid,selection))this.update({error:"草稿没有保存：另一个窗口已保存新版本。本地内容已保留，请核对后再保存。"});}
    finally {this.update({busy:false});}
  };
  translate = async () => {
    const {detail,draft,busy} = this.snapshot;if(!detail || !draft.trim() || busy)return;
    const cid = detail.conversationId, selection = this.selectionVersion, draftVersion = this.draftVersion;
    this.update({busy:true,error:""});
    try {
      const value = await this.post({action:"translate",target:this.market,text:draft});
      if(this.current(cid,selection) && draftVersion === this.draftVersion)this.setDraft(value.translation);
    } catch {if(this.current(cid,selection))this.update({error:"翻译没有完成，原草稿已保留。"});}
    finally {this.update({busy:false});}
  };
  private async send(kind:"manual"|"manual_card", episodeId?:string) {
    const {detail,draft,busy,pendingManualReplies} = this.snapshot;
    if(!detail || busy || pendingManualReplies.length || kind === "manual" && !draft.trim())return;
    const cid = detail.conversationId, selection = this.selectionVersion, sentText = draft;
    const requestId = `manual-${this.newId()}`;
    // Keep the original identity even when the HTTP response itself is lost.
    const intent:PendingManualReply = {id:null,kind,requestId,state:"unknown"};
    this.rememberPending(cid,[...(this.pending.get(cid) ?? []),intent]);
    this.update({busy:true,error:""});
    try {
      const result = await this.post({action:kind === "manual" ? "send_text" : "send_card",cid,
        ...(kind === "manual" ? {text:draft} : {episodeId}),
        expectedControlRevision:detail.creator.revision,requestId});
      await this.settleManual(cid,selection,requestId,result,kind === "manual" ? sentText : null);
    } catch(error) {
      // Only a backend statement that no intent exists / none was submitted clears the pending row.
      if(error instanceof RequestError && (error.intent === "absent" || error.intent === "not_submitted")) {
        this.rememberPending(cid,(this.pending.get(cid) ?? []).filter(row => row.requestId !== requestId));
        if(this.current(cid,selection))this.update({error:refusal(error.code)});
      } else if(this.current(cid,selection))this.update({error:"人工发送尚未确认。原发送意图已保留，请核验原意图。"});
    } finally {this.update({busy:false});}
  }
  sendText = () => this.send("manual");
  sendCard = (episodeId:string) => this.send("manual_card",episodeId);
  private async settleManual(cid:string, selection:number, requestId:string, result:Record<string,unknown>, sentText:string|null) {
    if(result.requestRef !== requestId || typeof result.state !== "string")throw Error();
    if(result.state === "confirmed" || result.state === "cancelled") {
      this.rememberPending(cid,(this.pending.get(cid) ?? []).filter(row => row.requestId !== requestId));
      if(this.current(cid,selection)) {
        await this.loadDetail(cid);
        if(this.current(cid,selection)) {
          // Clear only the text that was sent; anything typed afterwards stays.
          if(sentText !== null && result.state === "confirmed" && this.snapshot.draft === sentText)this.setDraft("");
          this.update({error:result.state === "confirmed" ? "原发送意图已确认送达。" : "原发送意图已取消，未重发。"});
        }
      }
      return;
    }
    if(!["ready","inflight","accepted","unknown","isolated"].includes(result.state))throw Error();
    this.rememberPending(cid,(this.pending.get(cid) ?? []).map(row => row.requestId !== requestId ? row : {
      ...row,id:typeof result.replyId === "string" ? result.replyId : row.id,state:result.state as PendingManualReply["state"],
    }));
    if(this.current(cid,selection))this.update({error:result.state === "isolated" ?
      "原发送结果仍未知，已停止自动核验；可继续手动核验原消息，不会重新发送。" :
      "原发送意图尚未确认；核验只检查原回执，不会重新发送。"});
  }
  reconcileManual = async (requestId:string) => {
    const {detail,busy,pendingManualReplies} = this.snapshot;
    const original = pendingManualReplies.find(row => row.requestId === requestId);
    if(!detail || busy || !original)return;
    const cid = detail.conversationId, selection = this.selectionVersion;
    this.update({busy:true,error:""});
    try {
      const result = await this.post({action:"reconcile_manual",cid,requestId});
      // A later confirmation never clears the editor: it may hold text written after the original.
      await this.settleManual(cid,selection,requestId,result,null);
    } catch(error) {
      // Only a closed command (it can never be submitted later) releases the editor. "Not found yet"
      // may be an original request that is still on its way.
      if(error instanceof RequestError && error.intent === "not_submitted") {
        this.rememberPending(cid,(this.pending.get(cid) ?? []).filter(row => row.requestId !== requestId));
        if(this.current(cid,selection))this.update({error:"原发送请求已确认关闭、不会再提交；草稿已保留，可以重新发送。"});
      } else if(this.current(cid,selection))this.update({error:"原请求结果尚未查明，已保留原请求；稍后再核验，不会生成新的发送。"});
    }
    finally {this.update({busy:false});}
  };
  /** Read-only check of a held AI reply by its original request; never generates or sends. */
  reconcileHeld = async (requestId:string) => {
    const {detail,busy} = this.snapshot;
    if(!detail || busy || !detail.heldReplies.some(row => row.requestId === requestId))return;
    const cid = detail.conversationId, selection = this.selectionVersion;
    this.update({busy:true,error:""});
    try {
      const result = await this.post({action:"reconcile_manual",cid,requestId});
      if(result.requestRef !== requestId || typeof result.state !== "string")throw Error();
      if(this.current(cid,selection)) {
        await this.loadDetail(cid,true);
        if(this.current(cid,selection))this.update({error:result.state === "confirmed" ?
          "原回复已确认送达。" : "仍未找到原回复的送达证据；已保留原请求，不会重发。"});
      }
    } catch {if(this.current(cid,selection))this.update({error:"原回复暂时无法核验；已保留原请求，不会重发。"});}
    finally {this.update({busy:false});}
  };
  translateInbound = async (id:string, text:string) => {
    const cid = this.snapshot.selected, selection = this.selectionVersion;if(!cid)return;
    this.update({translationBusy:id});
    try {
      const value = await this.post({action:"translate",target:"zh",text});
      if(this.current(cid,selection))this.update({translations:{...this.snapshot.translations,[id]:value.translation}});
    } catch {if(this.current(cid,selection))this.update({error:"达人消息暂时无法翻译，原文已保留。"});}
    finally {if(this.current(cid,selection))this.update({translationBusy:null});}
  };
  loadTrace = async (decisionId:string) => {
    const cid = this.snapshot.selected, selection = this.selectionVersion;if(!cid)return;
    try {
      const response = await this.fetcher(`/api/agent-replies?market=${this.market}&decisionId=${decisionId}`,{cache:"no-store"});
      if(!response.ok)throw Error();
      const trace:DecisionTrace = await response.json();
      if(this.current(cid,selection))this.update({trace});
    } catch {if(this.current(cid,selection))this.update({error:"暂时无法读取本次 AI 判断记录。"});}
  };
  private mutationId(key:string) {
    const id = this.mutationIds.get(key) ?? `conversation-${this.newId()}`;
    this.mutationIds.set(key,id);return id;
  }
  setStatus = async (status:CollaborationStatus) => {
    const detail = this.snapshot.detail;if(!detail)return;
    const key = `${detail.conversationId}:status:${status}:${detail.creator.collaboration.revision}`;
    await this.change({action:"set_collaboration",cid:detail.conversationId,status,
      expectedStatusRevision:detail.creator.collaboration.revision,expectedControlRevision:detail.creator.revision,
      requestId:this.mutationId(key)},"合作状态未保存；另一窗口可能已经更新，请刷新。",
      async () => {this.mutationIds.delete(key);await this.loadDetail(detail.conversationId);});
  };
  resolveManual = async () => {
    const {detail,manualOutcome} = this.snapshot;if(!detail?.case)return;
    const key = `${detail.case.id}:${manualOutcome}:${detail.case.pendingRevision}`;
    await this.change({action:"resolve_manual",cid:detail.conversationId,caseId:detail.case.id,
      latestTurnId:detail.latestTurnId,outcome:manualOutcome,expectedControlRevision:detail.creator.revision,
      expectedPendingRevision:detail.case.pendingRevision,expectedStatusRevision:detail.creator.collaboration.revision,
      requestId:this.mutationId(key)},"事项或达人状态已变化，请刷新后核对；不会重复解除。",
      async () => {this.mutationIds.delete(key);this.select(null);await this.loadList();});
  };
}
