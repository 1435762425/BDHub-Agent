"""SDK callbacks are wakeups; canonical HTTP proof and the existing inbox remain the facts."""
import json,threading,time
from collections import OrderedDict,deque
from pathlib import Path
from lib.second_cycle import CycleStore,digest
from lib.cycle_inbox import Inbox,inbox_status
from lib.cycle_service import Service
from lib.italy_im_session import ItalyImReadSession
from lib.request_budget import RequestBudget
from lib.reply_events import backfill


def arm_script(find_api):
    return '''() => {'''+find_api+'''
      if(!api||!api.sdkInstance||api.sdkStatus!==1)return {armed:0};
      const sdk=api.sdkInstance;
      if(window.__BDHUB_RECEIVE&&window.__BDHUB_RECEIVE.sdk===sdk)return {armed:2};
      const state=window.__BDHUB_RECEIVE||{buffer:[],overflow:false};state.sdk=sdk;window.__BDHUB_RECEIVE=state;
      const cs=sdk.conversationService;
      const onMessage=arg=>{for(const m of (Array.isArray(arg)?arg:[arg])){
        if(!m)continue;const id=String(m.serverId||''),cid=String(m.conversationId||'');
        if(!/^[0-9]+$/.test(id)||!/^[0-9]+$/.test(cid))continue;
        if(state.buffer.length>=20000){state.overflow=true;continue;}
        let c=null;try{c=cs.conversations&&cs.conversations.get(cid);}catch(e){}
        const ext=c&&c.originExt||{};
        state.buffer.push({mid:id,cid:cid,oec:String(ext.creator_oec_id||ext['1']||''),
          text:typeof m.content==='string'?m.content.slice(0,65536):null,at:Date.now()});
      }};
      let n=0;for(const name of ['onMessageReceive','onMessageUpsert'])if(typeof sdk[name]==='function'){sdk[name](onMessage);n++;}
      // Browser has receive authority only. HTTP owns all platform writes.
      if(sdk.messageService&&sdk.messageService.sendMessage)sdk.messageService.sendMessage=()=>{throw Error('http_sender_required');};
      return {armed:n};}'''

DRAIN='() => {const w=window.__BDHUB_RECEIVE;return w?{events:w.buffer.slice(0,2000),overflow:w.overflow}:{events:[],overflow:false};}'
ACK='n => {const w=window.__BDHUB_RECEIVE;if(w)w.buffer.splice(0,n);}'

class Receiver:
    def __init__(self,root,market,auth,valid,stopped):
        self.root=Path(root);self.market=market;self.auth=auth;self.valid=valid;self.stopped=stopped
        self.lock=threading.Lock();self.pending=OrderedDict();self.state={'processed':0,'added':0,'liveReplies':0,'sdkSignals':0,'sdkReplaysSkipped':0}
        self.done=threading.Event();self.thread=None
    def signal(self,events):
        with CycleStore(self.root/'var/second-cycle.sqlite') as s:
            plan=s.db.execute("SELECT id FROM plan WHERE market=? AND institution='bjn-local-research'",(self.market,)).fetchone()[0]
            for e in events:
                if not isinstance(e,dict) or not str(e.get('cid','')).isdigit() or not str(e.get('mid','')).isdigit():continue
                cid,mid=e['cid'],e['mid'];cp=s.db.execute('SELECT oec FROM inbox_checkpoint WHERE plan_id=? AND cid=?',(plan,cid)).fetchone()
                oec=cp[0] if cp else str(e.get('oec') or '')
                if not oec.isdigit() or not s.db.execute('SELECT 1 FROM relationship WHERE plan_id=? AND oec=?',(plan,oec)).fetchone():continue
                prior=s.db.execute('SELECT e.kind,v.payload FROM inbox_event e LEFT JOIN inbox_content_head h USING(plan_id,cid,message_id) LEFT JOIN inbox_content_version v ON v.plan_id=h.plan_id AND v.cid=h.cid AND v.message_id=h.message_id AND v.hash=h.hash WHERE e.plan_id=? AND e.cid=? AND e.message_id=?',(plan,cid,mid)).fetchone()
                if prior and (prior['kind'] not in ('creatorReplies','ourMessages') or prior['payload'] and json.loads(prior['payload']).get('text')==e.get('text')):
                    self.state['sdkReplaysSkipped']+=1;continue
                s.db.execute('INSERT INTO im_receive_signal(market,account,cid,oec,message_id,content_hash,observed) VALUES(?,?,?,?,?,?,?) ON CONFLICT(market,account,cid,message_id,content_hash) DO UPDATE SET done=0,checked=0,next_at=0,attempts=0,error_code=NULL,observed=excluded.observed WHERE im_receive_signal.done=1',
                    (self.market,self.auth.account_name,cid,oec,mid,digest(e.get('text')),time.time()))
                self.state['sdkSignals']+=1
    def publish(self,store,error=None):
        target=self.root/'var'/('cycle-inbox-status.json' if self.market=='it' else f'market-inbox-{self.market}.json')
        import os
        value={**self.state,'market':self.market,'pid':os.getpid(),'running':True,'state':'attention' if error else 'completed',
               'errorCode':error,'checkedAt':time.time(),'transport':'sdk_push_http_backfill','platformWrites':0,'realSends':0,'status':inbox_status(store,self.plan)}
        tmp=target.with_suffix('.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False)+'\n');tmp.replace(target)
    def run(self):
        try:
          with CycleStore(self.root/'var/second-cycle.sqlite') as store:
            self.plan=store.db.execute("SELECT id FROM plan WHERE market=? AND institution='bjn-local-research'",(self.market,)).fetchone()[0]
            inbox,service=Inbox(store),Service(store)
            with ItalyImReadSession(self.auth,{},maintenance_due=self.valid,stopped=self.stopped,use_environment_proxy=True,request_budget=RequestBudget(qps=2)) as session:
                next_discovery=0;cursor_path=self.root/f'var/market-inbox-cursor-{self.market}.json'
                try:cursor=int(json.loads(cursor_path.read_text()).get('cursor') or 0)
                except (OSError,ValueError,TypeError):cursor=0
                next_projection=0;needs_projection=False;cold=deque();next_cold=0
                while not self.stopped():
                    self.valid()
                    pause=self.root/'var'/('cycle-inbox.pause' if self.market=='it' else f'market-inbox-{self.market}.pause')
                    if pause.exists() or store._plan(self.plan)['state']!='active':time.sleep(1);continue
                    try:
                        now=time.time()
                        if now>=next_discovery:
                            page=session.initialize(cursor);cursor=int(page['nextCursor']) if page['hasMore'] else 0
                            temporary=cursor_path.with_suffix('.tmp');temporary.write_text(json.dumps({'cursor':cursor})+'\n');temporary.replace(cursor_path)
                            for c in page['conversations']:
                                cid,oec=c.get('conversationId'),c.get('oecId')
                                if c.get('conversationType')!=2 or not store.db.execute('SELECT 1 FROM relationship WHERE plan_id=? AND oec=?',(self.plan,oec)).fetchone():continue
                                cp=store.db.execute('SELECT checked_at FROM inbox_checkpoint WHERE plan_id=? AND cid=?',(self.plan,cid)).fetchone()
                                if not cp or now-cp[0]>=90:
                                    with self.lock:self.pending.setdefault((cid,oec),now*1000)
                            next_discovery=now+30
                        signal=store.db.execute('SELECT cid,oec,min(observed) first_seen FROM im_receive_signal WHERE market=? AND account=? AND done=0 AND next_at<=? GROUP BY cid,oec ORDER BY first_seen LIMIT 1',(self.market,self.auth.account_name,now)).fetchone()
                        if signal:
                            target=((signal['cid'],signal['oec']),signal['first_seen']*1000)
                            store.db.execute('UPDATE im_receive_signal SET checked=?,attempts=attempts+1,next_at=? WHERE market=? AND account=? AND cid=? AND done=0',(now,now+10,self.market,self.auth.account_name,signal['cid']))
                        else:
                            with self.lock:target=self.pending.popitem(last=False) if self.pending else None
                        if not target:
                            if not cold and now>=next_cold:
                                cold.extend((r['cid'],r['oec']) for r in store.db.execute('SELECT cid,oec FROM inbox_checkpoint WHERE plan_id=? ORDER BY checked_at LIMIT 10',(self.plan,)));next_cold=now+10
                            if cold:target=(cold.popleft(),None)
                        if not target:time.sleep(.2);continue
                        (cid,oec),signal_at=target
                        conversation=session.conversation(cid,oec)
                        requested=time.time();history=read_to_overlap(session,conversation,store,self.plan)
                        result=inbox.ingest(self.plan,cid,oec,history);captured=service.capture(self.plan,cid,oec,history.get('contents',[]));needs_projection=needs_projection or result['added']>0 or captured>0
                        for event in history.get('events',[]):
                            store.db.execute('UPDATE im_receive_signal SET done=1 WHERE market=? AND account=? AND cid=? AND message_id=? AND observed<=?',(self.market,self.auth.account_name,cid,event['messageId'],requested))
                        self.state['processed']+=1;self.state['consecutiveErrors']=0
                        for key in ('added','liveReplies'):self.state[key]+=result[key]
                        if signal_at:self.state['lastSignalReadSeconds']=round(max(0,time.time()-signal_at/1000),3)
                        if needs_projection and time.time()>=next_projection:backfill(store);next_projection=time.time()+10;needs_projection=False
                        self.publish(store)
                    except Exception as error:
                        code=getattr(error,'code',None) or type(error).__name__
                        self.state['consecutiveErrors']=self.state.get('consecutiveErrors',0)+1
                        if 'target' in locals() and target:
                            store.db.execute('UPDATE im_receive_signal SET next_at=?+min(300,30*attempts),error_code=? WHERE market=? AND account=? AND cid=? AND done=0',(time.time(),code,self.market,self.auth.account_name,target[0][0]))
                        self.publish(store,code)
                        if self.state['consecutiveErrors']>=3:break
                        time.sleep(3)
        except Exception as error:self.state['fatalError']=getattr(error,'code',None) or type(error).__name__
        finally:self.done.set()
    def start(self):
        self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()
    def close(self):
        if self.thread:self.thread.join()


def read_to_overlap(session,conversation,store,plan,max_pages=5):
    combined=None;events={};contents={};cursor=0
    for _ in range(max_pages):
        page=session.history_summary(conversation,include_events=True,include_contents=True,include_pagination=True,cursor=cursor)
        combined=page
        for event in page['events']:events.setdefault(event['messageId'],event)
        for content in page.get('contents',[]):contents.setdefault(content['messageId'],content)
        overlap=any(store.db.execute('SELECT 1 FROM inbox_event WHERE plan_id=? AND cid=? AND message_id=?',(plan,conversation.conversation_id,e['messageId'])).fetchone() for e in page['events'])
        if overlap or not page['hasMore']:break
        next_cursor=int(page['nextCursor'])
        if next_cursor==cursor:break
        cursor=next_cursor
    return {**combined,'events':list(events.values()),'contents':list(contents.values())}
