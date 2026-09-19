"""Immutable review batches, not executable sending trials."""
import json,time
from datetime import datetime,timezone
from pathlib import Path
from lib.second_cycle import CycleError,encoded,digest,assess_offer
from lib.cycle_materials import select_offers,render,name_key
SCHEMA='''CREATE TABLE IF NOT EXISTS cycle_review_batch(id TEXT PRIMARY KEY,request_id TEXT NOT NULL UNIQUE,request_json TEXT NOT NULL,snapshot_hash TEXT NOT NULL,payload TEXT NOT NULL,created_at REAL NOT NULL);
CREATE TRIGGER IF NOT EXISTS review_no_update BEFORE UPDATE ON cycle_review_batch BEGIN SELECT RAISE(ABORT,'review is immutable'); END;
CREATE TRIGGER IF NOT EXISTS review_no_delete BEFORE DELETE ON cycle_review_batch BEGIN SELECT RAISE(ABORT,'review is immutable'); END;'''

def product_name(store, offer):
    """商品短名：先按缓存键，miss 再**按 pid** 找一次，都没有就是 None。

    键里含标题——同一商品从不同来源来（Kalodata 的 `cuscino per il collo morbido` 与平台列表的
    `Cuscino morbido per il collo` 词序都不同）就会白白 miss，把已经生成过的短名丢掉。短名属于
    **商品**，标题只是模型当时的输入，所以按 pid 兜一次底。
    """
    named=store.db.execute('SELECT payload FROM cycle_product_name WHERE id=?',(name_key(offer),)).fetchone()
    if named is None:
        named=store.db.execute('SELECT payload FROM cycle_product_name WHERE pid=? ORDER BY rowid DESC LIMIT 1',
                               (str(offer['pid']),)).fetchone()
    return json.loads(named[0]) if named else None


def name_from_title(offer):
    """标题的确定性截断（不发模型）。"""
    cleaned = ' '.join(str(offer.get('title') or '').split())
    if not cleaned:
        cleaned = str(offer.get('pid') or '')
    short = cleaned[:30].rsplit(' ', 1)[0] if len(cleaned) > 30 and ' ' in cleaned[:31] else cleaned[:30]
    short = short.strip() or str(offer.get('pid') or '')
    return {'shortNameIt': short, 'shortNameZh': short, 'mentionIt': short,
            'derivedFromTitle': True, 'ref': 'derived-from-title'}


def name_from_card(card):
    """从卡名里取出短名，拼一个够发送用的 name payload；取不到就是 None。

    卡名由 `link_naming.render_full` 按 `config/link-naming.json` 的模板冻结。当前模板是
    `🔥 BJN {短名} {达人佣金}% {tail}`——**前缀里有 emoji**，所以不能假定名字以 `BJN ` 开头
    （以前的写法只认开头，于是整条名字连 `🔥 BJN …%…` 一起被当成短名塞进了话术）。
    """
    import re
    for key in ('listName', 'verifiedListName'):
        raw = ' '.join(str(card.get(key) or '').split())
        if not raw:
            continue
        # 模板里的短名位置就是"`BJN` 之后、`{佣金}%` 之前"；tail 是十六进制摘要。两段都尽量剥掉。
        match = re.search(r'BJN\s+(?P<short>.+?)\s+\d+(?:\.\d+)?%\s+(?P<tail>[0-9a-z]{2,})\s*$', raw, re.I)
        if match is None:
            match = re.search(r'BJN\s+(?P<short>.+?)\s+\d+(?:\.\d+)?%\s*$', raw, re.I)
        if match is None:
            match = re.search(r'BJN\s+(?P<short>.+?)\s*$', raw, re.I)
        if match is None:
            continue
        short = ' '.join(match.group('short').split()).strip(' -–—·')
        if not short or short == raw:
            continue
        return {'shortNameIt': short, 'shortNameZh': short, 'mentionIt': short,
                'derivedFromCard': True, 'ref': 'derived-from-card'}
    return None


def _ledger_cards(links, pid, campaign):
    """台账里这个「商品×活动」的全部候选卡——**三处来源**都要看（以前只看第一处）。

      ① `catalog_prepare_item.card` —— 自己建出来并核验过的卡。**不能只看最新一行**：
         最新那一行常常是被覆盖过的摘要（没有 listId），而有真卡的那一行在下面。
      ② `catalog_prepare_readback`（`kind IN ('reusedLink','verifiedLink')`）—— **复用已有链接**
         时的完整卡读回；`reconcile_from_inventory` 只写这里，**不写** `item.card`。
      ③ `catalog_prepare_reuse`（`reusable=1`）—— 同一份观察，只剩 listId 与佣金**原始值**。

    返回的卡片没有按佣金过滤，调用方各自决定要多严格。
    """
    import sqlite3
    cards = []
    try:
        for row in links.execute(
                "SELECT card FROM catalog_prepare_item WHERE pid=? AND campaign_id=? "
                "AND state IN ('ready','reuse') AND card IS NOT NULL ORDER BY updated DESC",
                (pid, campaign)):
            try:
                value = json.loads(row['card'])
            except ValueError:
                continue
            if isinstance(value, dict):
                cards.append(value | {'_ledgerSource': 'catalog-links'})
        if links.execute("SELECT 1 FROM sqlite_master WHERE name='catalog_prepare_readback'").fetchone():
            for row in links.execute(
                    "SELECT payload FROM catalog_prepare_readback WHERE pid=? AND campaign_id=? "
                    "AND kind IN ('reusedLink','verifiedLink') ORDER BY observed_at DESC", (pid, campaign)):
                try:
                    value = json.loads(row['payload'])
                except ValueError:
                    continue
                if isinstance(value, dict):
                    cards.append(value | {'_ledgerSource': 'catalog-links-readback'})
        if links.execute("SELECT 1 FROM sqlite_master WHERE name='catalog_prepare_reuse'").fetchone():
            for row in links.execute(
                    "SELECT list_id,creator_percent,public_percent FROM catalog_prepare_reuse "
                    "WHERE pid=? AND campaign_id=? AND reusable=1 ORDER BY observed_at DESC", (pid, campaign)):
                value = _reuse_card(row, pid=pid, campaign=campaign, percent=None)
                if value:
                    cards.append(value | {'_ledgerSource': 'catalog-links-reuse'})
    except sqlite3.Error:
        return []
    return cards


def ledger_card(store, offer):
    """Read the one canonical standard binding for this exact current offer."""
    import sqlite3
    from contextlib import closing
    var = Path(store.db.execute('PRAGMA database_list').fetchone()[2]).parent
    path = var / 'catalog-links.sqlite'
    if not path.exists():
        return None
    pid = str(offer['pid'])
    campaign = str(offer.get('campaignId') or '')
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)) as links:
            links.row_factory = sqlite3.Row
            if not links.execute("SELECT 1 FROM sqlite_master WHERE name='catalog_current_binding'").fetchone():
                return None
            row=links.execute("SELECT * FROM catalog_current_binding WHERE market='it' AND catalog_source=? "
                              "AND pid=? AND campaign_id=? AND state='active'",
                              (str(offer.get('catalogSource') or ''),pid,campaign)).fetchone()
            if not row:return None
            from lib.catalog_binding import offer_fingerprint
            if row['offer_fingerprint']!=offer_fingerprint(offer) or row['commission_rule_version']!='commission-1-to-2-v1' or row['naming_rule_version']!='link-naming-v1':return None
            card=json.loads(row['card_payload'])
            value=_usable(card=card,pid=pid,campaign=campaign,percent=str(offer.get('creatorPercent') or ''))
            return value|{'derivedFrom':'catalog-current-binding'} if value else None
    except sqlite3.Error:
        return None
    return None


def _usable(*, card, pid, campaign, percent):
    """这三个相等且 listId 非空，才算"这条 offer 能用的那张卡"。"""
    if not isinstance(card, dict) or card.get('state') != 'verified_read_only':
        return None
    if str(card.get('pid')) != pid or str(card.get('sourceCampaignId')) != campaign:
        return None
    if str(card.get('creatorPercent')) != percent:
        return None
    if not str(card.get('listId') or '').strip():
        return None
    return card | {'requiresFreshReadBeforeSend': False}


def _reuse_card(row, *, pid, campaign, percent, source=None):
    """从 `catalog_prepare_reuse`（只有 listId 与原始佣金）拼一张够发送用的卡。

    没有卡名（listName 为空），所以短名会退到标题那一档——短名是措辞，不是发送前提。
    `percent` 给了就要求它相等（`None` ＝只要定位信息）。
    """
    from decimal import Decimal
    try:
        raw = row['creator_percent']
        rate = format(Decimal(str(raw)) / 100, 'f') if raw not in (None, '') else None
    except (ValueError, TypeError, ArithmeticError):
        return None
    if percent is not None and (rate is None or rate != percent):
        return None
    list_id = str(row['list_id'] or '').strip()
    if not list_id:
        return None
    public = row['public_percent']
    return {'state': 'verified_read_only', 'listId': list_id, 'listName': '',
            'verifiedListName': '', 'pid': pid, 'sourceCampaignId': campaign,
            'wireCampaignId': '0' if source == 'selected' else campaign,
            'creatorPercent': rate if percent is None else percent,
            'publicPercent': format(Decimal(str(public)) / 100, 'f') if public not in (None, '') else None,
            'stock': None, 'stockRequired': False, 'campaignName': '', 'executionAllowed': False,
            'readAccount': 'acc9', 'requiresFreshReadBeforeSend': True}


def card_rate_gap(store, plan, positions, limit_examples=3):
    """Compatibility summary: a canonical binding either matches exactly or is absent."""
    from decimal import Decimal, InvalidOperation
    pids = {str(pid) for _, pid in positions}
    counts = {'same': 0, 'lower': 0, 'lowerByOne': 0, 'higher': 0, 'noCard': 0}
    examples = []
    try:
        offers = {o['pid']: o for o in select_offers(store, plan, None, scoped_pids=pids)}
    except Exception as error:  # noqa: BLE001 - 这一块算不出来**不该让整张卡读不出来**
        # 如实说"没读到"，而不是报一串 0（那会让人以为"没有差距"）。
        return counts | {'examples': [], 'readable': False, 'error': type(error).__name__}
    for pid, offer in sorted(offers.items()):
        card=ledger_card(store,offer)
        if not card:
            counts['noCard']+=1;continue
        plan_rate=str(offer.get('creatorPercent') or '');card_rate=str(card.get('creatorPercent') or '')
        try:delta=Decimal(card_rate)-Decimal(plan_rate)
        except (InvalidOperation,ValueError):counts['noCard']+=1;continue
        if delta==0:counts['same']+=1;continue
        key='higher' if delta>0 else 'lower';counts[key]+=1
        if delta==-1:counts['lowerByOne']+=1
        if len(examples)<limit_examples:examples.append({'pid':pid,'listName':str(card.get('listName') or '')[:60],
            'cardPercent':card_rate,'planPercent':plan_rate,'campaignId':str(offer.get('campaignId') or '')})
    return counts | {'examples': examples, 'readable': True}


def ledger_gap(store, offer):
    """卡拿不到时，**到底是哪一种缺**——这一条要能被人一眼看懂，不然就会把三种完全不同的事
    混成一个"备链缺口"（用户就是这么被误导的）：

      `card_not_read`      台账里这个商品有行、但**没有卡**，而行状态说明它是"判定过、卡没存下来"
                           （`review`/`reuse`）。商品在平台上**有链接**，缺的是一次**只读复读**。
      `no_card_needs_link` 行状态是 `missing`/`reading`/`pending`/`read_incomplete`：读卡**从来没
                           确认过有卡**。抽样 30 个去问发送用的那个卡搜索，0 个有卡——**要建链**
                           （平台写入），不是复读能解决的。
      `card_rate_changed`  台账里的卡是**另一个佣金**（平台/货盘改过佣金）。要复读确认。
      `card_campaign_changed` 台账里的卡挂在**另一个活动**上。
      `card_unverified`    只有"观察到有链接"的摘要，没有可定位的 listId。
      `no_link_in_ledger`  台账里**完全没有**这个商品的行：要核实它凭什么进的池子。
    """
    import sqlite3
    from contextlib import closing
    var = Path(store.db.execute('PRAGMA database_list').fetchone()[2]).parent
    path = var / 'catalog-links.sqlite'
    if not path.exists():
        return 'no_link_in_ledger'
    pid = str(offer['pid']);campaign = str(offer.get('campaignId') or '')
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)) as links:
            links.row_factory = sqlite3.Row
            if not links.execute("SELECT 1 FROM sqlite_master WHERE name='catalog_current_binding'").fetchone():return 'standard_link_missing'
            row=links.execute("SELECT offer_fingerprint,state,card_payload FROM catalog_current_binding WHERE market='it' "
                              "AND catalog_source=? AND pid=? AND campaign_id=?",
                              (str(offer.get('catalogSource') or ''),pid,campaign)).fetchone()
    except sqlite3.Error:
        return 'standard_link_unreadable'
    if not row:return 'standard_link_missing'
    from lib.catalog_binding import offer_fingerprint
    if row['state']!='active' or row['offer_fingerprint']!=offer_fingerprint(offer):return 'standard_link_terms_changed'
    try:card=json.loads(row['card_payload'])
    except (TypeError,ValueError):return 'standard_link_terms_changed'
    return '' if _usable(card=card,pid=pid,campaign=campaign,percent=str(offer.get('creatorPercent') or '')) else 'standard_link_terms_changed'


def _position_rows(store, plan, offers, identity_reader, order):
    """把池子给的 (达人,商品) 位置翻成候选构建需要的行。

    身份取**达人**级的（任一条解析行），商品取这条线索自己的边。找不到身份或这条线索的边就**记原因**
    并跳过——绝不静默丢掉一个位置（"可发层有多少"和"这一批发多少"必须对得平）。
    """
    import collections
    identity = {}
    for row in store.db.execute('SELECT e.payload,r.creator_id,r.oec FROM cycle_identity_resolution r '
                                'JOIN source_edge e USING(plan_id,source_id) WHERE r.plan_id=? '
                                'ORDER BY r.source_id', (plan,)):
        identity.setdefault(str(row['creator_id']), {'oec': row['oec']})
    edges = {}
    for row in store.db.execute("SELECT e.payload, json_extract(e.payload,'$.sourceHandle') AS handle "
                                "FROM source_edge e WHERE e.plan_id=? "
                                "AND json_extract(e.payload,'$.sourceKind')='kalodata_http'", (plan,)):
        edge = json.loads(row['payload'])
        edges.setdefault((str(row['handle']), str(edge['pid'])), row['payload'])
    rows = []
    for creator_id, pid in order:
        who = identity.get(str(creator_id))
        if not who:
            continue
        person = identity_reader(creator_id, who['oec'])
        if not person or not person.get('handle'):
            continue
        payload = edges.get((str(person['handle']), str(pid)))
        if not payload:
            continue
        edge = json.loads(payload)
        rows.append({'payload': payload, 'creator_id': creator_id, 'oec': who['oec'],
                     'evidence_ref': edge.get('evidenceRef')})
    return rows


def choose_candidates(store,plan,identity_reader,limit=3,positions=None):
 """挑候选；`positions` 给了就**按它给的顺序与(达人,商品)来挑**。

 这是"从发送池正式发送"的接缝：池子的 `ready` 层已经决定了"发谁、发哪个商品、什么顺序"，
 所以这里不再自己排序、也不再按达人折叠，只把**同一套复检**（offer 指纹、当前 handle、命名、
 卡片、控制版本、冷却、去重）逐条跑一遍：不合格的照旧进 `skipped` 带原因。
 `positions` 是 `[(creator_id, pid), ...]`，顺序即发送顺序。
 """
 # 评审批次仍然只允许 100；按池子位置播种时要一次看几百条（页面只展示 3 条样例）。
 ceiling=2000 if positions is not None else 100
 if type(limit) is not int or not 1<=limit<=ceiling:raise CycleError('review_limit')
 order=None
 if positions is not None:
  if not isinstance(positions,(list,tuple)) or not 1<=len(positions)<=2000:
   raise CycleError('invalid_positions')
  order={}
  for index,(creator_id,pid) in enumerate(positions):
   key=(str(creator_id),str(pid))
   if key in order:raise CycleError('duplicate_position')
   order[key]=index
 if order is not None:
  # 发送位置来自池子，**池位就是需求**：只在这个池位集合里挑当前合格货盘的 offer，
  # 不要再用 `opportunity`（准备/估算用的需求表）去过滤——那道过滤会把"商品在货盘里、
  # 也有卡"的位置报成"商品不在当前合格货盘"。
  offers={o['pid']:o for o in select_offers(store,plan,None,scoped_pids={p for _,p in order})}
 else:
  offers={o['pid']:o for o in select_offers(store,plan,2000)}
 if order is not None:
  # 池子给的是**达人级**位置：一位达人有了 OECID，他名下的每条线索商品都可以发。所以身份按
  # **达人**取（任一条解析行即可证明），商品按**这条线索**取——不再要求"这个商品有自己的解析行"。
  # 少了这一层，池子里那些"只缺自己池位"的位置会在发送视角里凭空消失（实测 159 个）。
  rows=_position_rows(store,plan,offers,identity_reader,order)
 else:
  rows=store.db.execute('SELECT e.payload,r.creator_id,r.oec,r.evidence_ref FROM cycle_identity_resolution r JOIN source_edge e USING(plan_id,source_id) WHERE r.plan_id=?',(plan,)).fetchall()
 candidates=[];skipped=[]
 for row in rows:
  edge=json.loads(row['payload'])
  key=(str(row['creator_id']),str(edge['pid']))
  if order is not None and key not in order:continue
  control=store.db.execute('SELECT * FROM relationship WHERE plan_id=? AND creator_id=?',(plan,row['creator_id'])).fetchone();o=offers.get(edge['pid'])
  if not o:
   # 商品已经不在当前合格货盘里（快照/佣金/有效期变了）——这是**规则**，不该发。
   skipped.append({'sourceId':edge['sourceId'],'reason':'offer_not_in_current_catalog'});continue
  if not control or control['mode']!='auto' or control['rejected'] or control['inbox_until']:
   skipped.append({'sourceId':edge['sourceId'],'reason':'relationship_blocked'});continue
  if store.db.execute("SELECT 1 FROM sqlite_master WHERE name='cycle_delivery'").fetchone():
   last=store.db.execute("SELECT max(p.started) FROM cycle_delivery d JOIN cycle_delivery_part p ON p.delivery_id=d.id WHERE d.plan_id=? AND d.creator_id=? AND d.state IN ('confirmed','partial_delivery')",(plan,row['creator_id'])).fetchone()[0]
   if last and store.clock()-last<(86400 if control['unlocked'] else 172800):
    # 达人级冷却（已解锁 24h / 未解锁 48h）。**必须记原因**：不记的话"可发层有多少位置"和
    # "这一批发了多少"就对不平，操作者只会看到一批莫名少掉的人。
    skipped.append({'sourceId':edge['sourceId'],'reason':'marketing_cooldown'});continue
  person=identity_reader(row['creator_id'],row['oec'])
  if not person or not person.get('handle'):
   skipped.append({'sourceId':edge['sourceId'],'reason':'current_identity_missing'});continue
  name=product_name(store,o);name_source='缓存' if name else None
  card=ledger_card(store,o)
  if name is None and card is not None:
   # 卡名是建链时**冻结**的 `BJN {短名} {佣金}% {tail}`——短名就在里面，而且和卡上写的是同一个。
   # 用它比重新生成更一致，也不花一次模型调用。
   name=name_from_card(card)
   if name:name_source='取自卡名'
  if name is None:
   # 最后一档兜底：**商品标题**（`link_naming.short_name_for` 在建链时就是这么做的）。
   # 短名是给话术用的措辞，不是发送前提——绝不该因为"还没生成一个漂亮短名"就卡住一条位置。
   # 页面上给「补短名」按钮把质量补回来。
   name=name_from_title(o);name_source='取自标题'
  if not card or card.get('state')!='verified_read_only':
   # 卡拿不到时**必须说清是哪一种缺**：台账没存下卡（复读一次就有）≠ 卡挂在别的活动（要建链）≠
   # 台账里根本没这个商品。混成一个"备链缺口"会把只读的事说成平台写入。
   skipped.append({'sourceId':edge['sourceId'],'reason':ledger_gap(store,o)});continue
  if not name:
   skipped.append({'sourceId':edge['sourceId'],'reason':'missing_short_name'});continue
  candidates.append({'planRevision':store._plan(plan)['revision'],'creatorId':row['creator_id'],'oecId':row['oec'],'handle':person['handle'],'identityEvidence':row['evidence_ref'],'controlRevision':control['revision'],
   'pid':o['pid'],'offer':o,'offerFingerprint':digest(o),'materialKey':name_key(o),'name':name,
   'nameSource':name_source or '缓存','card':card,'source':edge,'relationshipUnlocked':bool(control['unlocked'])})
 if order is not None:
  # 池子的顺序就是发送顺序：不再按截止/销量重排。
  candidates.sort(key=lambda r:order[(str(r['creatorId']),str(r['pid']))])
 else:
  candidates.sort(key=lambda r:(r['source']['windowEnd'],r['source']['units'],r['creatorId']),reverse=True)
 result=[];seen=set()
 for c in candidates:
  # 池子已经保证"每个达人一个可发槽位"；只有自己排序时才需要按达人折叠。
  if order is None and c['oecId'] in seen:
   skipped.append({'sourceId':c['source']['sourceId'],'reason':'duplicate_creator'});continue
  if store.db.execute("SELECT 1 FROM sqlite_master WHERE name='cycle_delivery'").fetchone() and store.db.execute("SELECT 1 FROM cycle_delivery WHERE plan_id=? AND creator_id=? AND pid=? AND source_id=? AND state IN ('ready','running','unknown','confirmed','partial_delivery')",(plan,c['creatorId'],c['pid'],c['source']['sourceId'])).fetchone():
   skipped.append({'sourceId':c['source']['sourceId'],'reason':'delivery_already_exists'});continue
  seen.add(c['oecId']);result.append(c)
  if len(result)==limit:break
 return result,skipped

class ReviewBatches:
 def __init__(self,store):self.store=store;store.db.executescript(SCHEMA)
 def existing(self,request_id,request):
  row=self.store.db.execute('SELECT * FROM cycle_review_batch WHERE request_id=?',(request_id,)).fetchone()
  if row:
   if row['request_json']!=encoded(request):raise CycleError('review_request_conflict')
   return json.loads(row['payload'])
 def create(self,request_id,request,candidates,evidence,skipped):
  previous=self.existing(request_id,request)
  if previous:return previous
  plan=self.store._plan(request['planId'])
  if plan['state']!='active':raise CycleError('plan_paused')
  items=[]
  for candidate in candidates:
   if candidate['planRevision']!=plan['revision']:raise CycleError('plan_changed')
   c=dict(candidate);c['message']=render(c['name'],c['offer'],request['template'],c['handle']);c['checks']=evidence[c['oecId']]
   # A partial local history and an unexplained permission flag are not a quota ledger.
   c['blockingReasons']=['v4_sender_not_connected','institution_market_quota_not_verified','remote_conversation_history_not_verified'];c['executionAllowed']=False
   if c['checks'].get('remoteHistory',{}).get('status')=='observed_summary':
    c['blockingReasons'].remove('remote_conversation_history_not_verified');c['blockingReasons'].append('remote_sender_counts_not_verified')
   history=c['checks'].get('remoteHistory',{}).get('history',{})
   counts=history.get('senderCounts',{})
   c['observedContactSignal']='creator_reply' if counts.get('creatorReplies',0)>0 else 'showcase_notification' if counts.get('showcaseNotifications',0)>0 else 'not_established'
   if c['observedContactSignal']!='not_established':
    c['blockingReasons'].remove('institution_market_quota_not_verified');c['blockingReasons'].append('interaction_evidence_not_applied_to_controller')
   if counts:
    if 'remote_sender_counts_not_verified' in c['blockingReasons']:c['blockingReasons'].remove('remote_sender_counts_not_verified')
    c['blockingReasons'].append('marketing_frequency_not_verified')
    if not c.get('relationshipUnlocked') and c['observedContactSignal']=='not_established' and counts.get('ourMessages',0)+2>5:
     c['blockingReasons'].append('message_allowance_window_unverified')
     raw=history.get('outboundCreateTimeRaw',[])
     valid=[v for v in raw if type(v) is int and 946684800000<=v<=int(self.store.clock()*1000)+300000]
     last=max(valid) if valid else None
     complete_times=len(valid)==counts['ourMessages'] and history.get('outboundTimeMissingCount',0)==0
     c['allowanceReview']={'state':'awaiting_window_verification','historicalOutboundObserved':counts['ourMessages'],'renewalPolicy':'monthly_user_confirmed','resetAt':None,'permanentExclusion':False,'lastObservedOutboundAt':datetime.fromtimestamp(last/1000,timezone.utc).isoformat() if last else None,'observedOutboundTimesComplete':complete_times,'elapsedDaysSinceObservedOutbound':int((self.store.clock()-last/1000)/86400) if last and complete_times else None,'automaticResetApplied':False}
   old=c['checks'].get('legacy',{})
   if old.get('manualState') in ('processing','pending_reply','rejected'):c['blockingReasons'].append('legacy_relationship_needs_review')
   if old.get('activeOrUnknownIntents',0)>0:c['blockingReasons'].append('legacy_delivery_needs_reconciliation')
   items.append(c)
  snapshot={'schema':'bdhub.cycle-review.v1','planId':request['planId'],'planRevision':plan['revision'],'items':items,'skipped':skipped,'preparedAt':self.store.clock(),'expiresAt':self.store.clock()+1800,'executionAllowed':False,'realSends':0}
  fingerprint=digest(snapshot);payload={**snapshot,'id':'review-'+fingerprint[:24],'snapshotHash':fingerprint}
  with self.store.tx():
   if self.store._plan(request['planId'])['revision']!=plan['revision']:raise CycleError('plan_changed')
   current={o['offerKey']:o for _,o in self.store._offers(request['planId'])}
   for c in candidates:
    if c['offer']['offerKey'] not in current or digest(current[c['offer']['offerKey']])!=c['offerFingerprint']:raise CycleError('offer_changed')
    r=self.store.db.execute('SELECT * FROM relationship WHERE plan_id=? AND creator_id=?',(request['planId'],c['creatorId'])).fetchone()
    if not r or r['revision']!=c['controlRevision'] or r['mode']!='auto' or r['rejected'] or r['inbox_until']:raise CycleError('relationship_changed')
   self.store.db.execute('INSERT INTO cycle_review_batch VALUES(?,?,?,?,?,?)',(payload['id'],request_id,encoded(request),fingerprint,encoded(payload),self.store.clock()))
  return payload

def latest_review(store,plan):
 if not store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cycle_review_batch'").fetchone():return None
 r=store.db.execute('SELECT payload FROM cycle_review_batch WHERE json_extract(payload,\'$.planId\')=? ORDER BY created_at DESC LIMIT 1',(plan,)).fetchone()
 if not r:return None
 payload=json.loads(r[0]);return {**payload,'expired':store.clock()>=payload['expiresAt']}
