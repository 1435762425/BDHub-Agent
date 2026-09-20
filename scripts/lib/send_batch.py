"""从发送池到正式发送：把池子里的"可发槽位"播种成一个可执行批次。

一行发送逻辑都不在这里重写，它只做三件事——**按池子顺序播种、显式授权（含越界探测与窗口）、
把每一步发布出去**：

* **谁、发什么、什么顺序** ← `lib/lead_pool.py` 的 `ready` 层（位置＝达人×商品，每达人一个槽位）
* **逐条复检** ← `cycle_review.choose_candidates(positions=...)`：offer 指纹、当前 handle、命名、
  卡片、控制版本、冷却、去重，一条都不少；不合格的带原因跳过，不回写池子
* **额度闸门** ← 与 `cycle_delivery.reserve_contact()` 同一口径：滚动 24 小时 500 个**新联系**
  （已解锁的达人另有额度、不占这 500）。`widen` 是**显式越界探测**：越过本地保守闸门，去拿平台
  自己的上限信号；越界时每条真实回执都要落账（`cycle_platform_signal` 已有字段），
  单达人 `flight<0` 只标该条、不停整批。
* **执行** ← `send-batch-worker.py` / `cycle_burst.run_cohort`（车道、请求预算、本地冻结材料核验、回查、
  结果未知即停）

`preview()` 只读：它算的就是"这一批会发给谁、发什么、为什么有人被跳过"，页面拿它给操作者看那
2–3 条样例和总数。`freeze_batch()` 才落库；`start_batch()` 只登记明确授权，CLI 随后启动冻结批次 worker。
"""
import json
import math
import re
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lib.cycle_materials import TEMPLATES
from lib.cycle_review import card_rate_gap, choose_candidates
from lib.lead_pool import pool
from lib.second_cycle import CycleError, digest, encoded
from lib.template_library import CUSTOM_ID,render_send_template,resolve_send_template,send_templates

BEIJING = timezone(timedelta(hours=8))
# 与 `cycle_delivery.reserve_contact()` 同一个口径：滚动 24 小时、只算新联系。
NEW_CONTACT_WINDOW_SECONDS = 86400
NEW_CONTACT_LIMIT = 500
DEFAULT_WINDOW = ('09:00', '24:00')
SAMPLE_SIZE = 3
RESERVE_RATE = 0.10


def _beijing(stamp):
    return datetime.fromtimestamp(stamp, BEIJING)


def window_state(window, stamp):
    """窗口是不是开着。``window`` 为 None 表示**没开窗口**（随时可发）。"""
    if not window:
        return {'enabled': False, 'open': True, 'start': None, 'end': None}
    start, end = window
    now = _beijing(stamp).time()
    began = datetime.strptime(start, '%H:%M').time()
    # 24:00 是"到当天结束"，Python 的 time() 顶到 23:59，所以按"结束不设限"处理。
    finished = None if end in ('24:00', '00:00') else datetime.strptime(end, '%H:%M').time()
    inside = now >= began and (finished is None or now < finished)
    return {'enabled': True, 'open': inside, 'start': start, 'end': end}


def _window_arg(window):
    if window is None:
        return None
    if not isinstance(window, (list, tuple)) or len(window) != 2:
        raise CycleError('invalid_send_window')
    start, end = (str(window[0]), str(window[1]))
    # `24:00` 是"到当天结束"：Python 的 %H 只到 23，所以它是显式允许的那个例外。
    if start == '24:00':
        raise CycleError('invalid_send_window')
    for value in (start, end):
        if value == '24:00':
            continue
        try:
            datetime.strptime(value, '%H:%M')
        except ValueError:
            raise CycleError('invalid_send_window') from None
    if start == end:
        raise CycleError('invalid_send_window')
    return (start, end)


def capacity(root, *, now=None, clock=None):
    """滚动 24 小时的新联系额度：用了多少、还剩多少。

    只读，口径与 `cycle_delivery.reserve_contact()` 完全一致——那里是**写入时**的闸门，这里只是
    提前算给操作者看，两者算的是同一件事，不另立一套。
    """
    root = Path(root)
    db = root / 'var/second-cycle.sqlite'
    if not db.exists():
        return None
    stamp = clock() if clock is not None else (now if now is not None else time.time())
    with closing(_connect(db)) as conn:
        plan = conn.execute("SELECT id FROM plan WHERE institution='bjn-local-research' AND market='it' "
                            "AND state='active'").fetchone()
        if not plan:
            return None
        used = _contact_count(conn, plan[0], stamp)
    return {'windowSeconds': NEW_CONTACT_WINDOW_SECONDS, 'limit': NEW_CONTACT_LIMIT, 'used': used,
            'remaining': max(0, NEW_CONTACT_LIMIT - used)}


def _connect(db):
    import sqlite3
    conn = sqlite3.connect(Path(db).resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _contact_count(conn, plan_id, stamp):
    """与 `reserve_contact` 同一句：预留 ∪ 近 24 小时已发卡片，按 oec 去重。"""
    return conn.execute(
        "SELECT count(*) FROM (SELECT oec FROM cycle_contact_reservation WHERE plan_id=? AND reserved>? "
        "UNION SELECT d.oec FROM cycle_delivery d JOIN cycle_delivery_part p ON p.delivery_id=d.id "
        "WHERE d.plan_id=? AND p.kind='card' AND p.started>?)",
        (plan_id, stamp - NEW_CONTACT_WINDOW_SECONDS, plan_id, stamp - NEW_CONTACT_WINDOW_SECONDS)).fetchone()[0]


CONFIG_DEFAULT = {'count': 500, 'widen': False, 'windowEnabled': False, 'template': 'standard',
                  'window': list(DEFAULT_WINDOW)}


def config_path(root):
    return Path(root) / 'config/send-batch.json'


def validate_config(raw):
    """页面能改人数、模板、是否越界，以及窗口开不开与几点到几点。"""
    if not isinstance(raw, dict):
        raise CycleError('invalid_send_config')
    if set(raw) - {'count', 'widen', 'windowEnabled', 'window', 'template'}:
        raise CycleError('invalid_send_config')
    value = {**CONFIG_DEFAULT, **raw}
    # 类型要先判：`1 <= "很多"` 会抛 TypeError，而不是我们自己的具名码——配置文件被人手改坏时
    # 那会变成一句看不懂的崩溃，而不是"按默认值读"。
    if type(value['count']) is not int:
        raise CycleError('invalid_send_count')
    if not 1 <= value['count'] <= 2000:
        raise CycleError('invalid_send_count')
    if type(value['widen']) is not bool or type(value['windowEnabled']) is not bool:
        raise CycleError('invalid_send_config')
    if value['template'] not in TEMPLATES and not (isinstance(value['template'],str) and CUSTOM_ID.fullmatch(value['template'])):
        raise CycleError('template_missing')
    value['window'] = list(_window_arg(value['window'] if value['windowEnabled'] else None) or DEFAULT_WINDOW)
    return value


def load_config(root):
    path = config_path(root)
    try:
        raw = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        return validate_config(raw)
    except (CycleError, ValueError, TypeError, OSError, UnicodeDecodeError):
        # 手改坏/写了一半的配置不能让页面打不开：按默认值读，页面另有一行说它过期了。
        return dict(CONFIG_DEFAULT)


def save_config(root, raw):
    value = validate_config({**load_config(root), **(raw or {})})
    if value['windowEnabled']:
        db=Path(root)/'var/second-cycle.sqlite'
        if db.exists():
            with closing(_connect(db)) as conn:
                if conn.execute("SELECT 1 FROM sqlite_master WHERE name='agent_reply_setting'").fetchone():
                    row=conn.execute('SELECT reply_end,buffer_minutes FROM agent_reply_setting LIMIT 1').fetchone()
                    if row:
                        def minutes(v):
                            if v=='24:00':return 1440
                            h,m=map(int,v.split(':'));return h*60+m
                        if minutes(value['window'][0])<minutes(row['reply_end'])+row['buffer_minutes']:
                            raise CycleError('reply_schedule_overlap')
    path = config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)
    return value


def status(root, *, now=None, clock=None, pool_reader=None, chooser=None):
    """页面要的全部数字：池子六层 + 这一批的预检。配置存 `config/send-batch.json`。"""
    config = load_config(root)
    window = config['window'] if config['windowEnabled'] else None
    state = preview(root, count=config['count'], widen=config['widen'], window=window,
                    template=config['template'],
                    now=now, clock=clock, pool_reader=pool_reader, chooser=chooser)
    layers = pool(root, now=clock() if clock else (now if now is not None else time.time())) if pool_reader is None else {}
    with _store(root) as store:templates=send_templates(store)
    return {'market': 'it', 'account': 'acc6', 'config': config, 'templates': templates, 'preview': state,
            'pool': {'counts': layers.get('counts') or {}, 'layers': layers.get('layers') or {}},
            'batch': batch_status(root), 'available': state.get('available', False)}


def save_and_status(root, raw, *, now=None, clock=None, pool_reader=None, chooser=None):
    """存下设置并回答**存完之后**的状态。

    顺序反过来（先算再存）会让回答里的预检还是旧配置——页面会显示"一批 500 条却能发 600"。
    """
    saved = save_config(root, raw)
    state = status(root, now=now, clock=clock, pool_reader=pool_reader, chooser=chooser)
    return {**state, 'config': saved, 'saved': True}


def preview(root, *, count=500, widen=False, window=None, template='standard', now=None, clock=None,
            pool_reader=None, chooser=None):
    """这一批会发给谁、发什么、谁被跳过（只读，不落库、不碰平台）。"""
    state, _ = _preview(root, count=count, widen=widen, window=window, template=template, now=now, clock=clock,
                        pool_reader=pool_reader, chooser=chooser)
    return state


def _preview(root, *, count=500, widen=False, window=None, template='standard', now=None, clock=None,
             pool_reader=None, chooser=None):
    """Return the public preview and its private, fully frozen candidate list."""
    root = Path(root)
    if type(count) is not int or not 1 <= count <= 2000:
        raise CycleError('invalid_send_count')
    if template not in TEMPLATES and not (isinstance(template,str) and CUSTOM_ID.fullmatch(template)):
        raise CycleError('template_missing')
    window = _window_arg(window)
    stamp = clock() if clock is not None else (now if now is not None else time.time())
    reserve_requested = math.ceil(count * RESERVE_RATE)
    required = count + reserve_requested
    # 池子的可发层里，前面会堆着过不了复检的槽位（缺卡片核验/被关系控制挡住），所以**接着往下取**，
    # 直到凑够正式＋候补或把可发层走完；取过的槽位与跳过原因都要如实报出来。
    read_pool = pool_reader or (lambda: pool(root, now=stamp, limit=min(4000, max(required * 4, required))))
    state = read_pool()
    if not state.get('available'):
        return ({'available': False, 'requested': count, 'reserveRequested': reserve_requested,
                 'required': required, 'sendable': 0, 'reserveReady': 0,
                 'frozenTotal': 0, 'fullPreparation': False, 'samples': [], 'skipped': {},
                 'rateGap': {'same': 0, 'lower': 0, 'lowerByOne': 0, 'higher': 0, 'noCard': 0,
                             'examples': [], 'readable': False},
                 'capacity': None, 'window': window_state(window, stamp), 'widen': bool(widen),
                 'previewHash': None, 'authorization': None}, [])
    slots = [row for row in state['pools'].get('ready', [])]
    positions = [(row['creatorId'], row['pid']) for row in slots]
    with _store(root) as store:
        plan = _plan(store)
        template_spec=resolve_send_template(store,template)
        with closing(_identities(root)) as ids:
            def person(creator, oec):
                row = ids.execute("SELECT current_handle FROM creator_identity WHERE market='it' "
                                  "AND creator_id=? AND oec_id=? AND handle_conflict=0",
                                  (creator, oec)).fetchone()
                return {'handle': row[0]} if row else None
            pick = chooser or choose_candidates
            candidates, skipped = pick(store, plan, person, max(1, len(positions)), positions=positions)
            # 卡的佣金与计划的差距：只有两条路（按卡上的数发 / 重建链），是业务决定，
            # 这一层只把事实摊开——不替用户选。
            rate_gap = card_rate_gap(store, plan, positions)
    cap = capacity(root, now=stamp)
    space = None if widen else (cap or {}).get('remaining')
    window_now = window_state(window, stamp)
    sendable = []
    reserves = []
    for index, candidate in enumerate(candidates):
        # 前 N 位是正式成员，后 ceil(N×10%) 是冻结候补；再后面的才留在池子等下一批。
        if index >= required:
            skipped.append({'sourceId': candidate['source']['sourceId'], 'reason': 'beyond_requested_size'})
            continue
        if index < count and space is not None and len(sendable) >= space:
            skipped.append({'sourceId': candidate['source']['sourceId'], 'reason': 'local_capacity_reached'})
            continue
        if index < count:
            sendable.append(candidate)
        else:
            reserves.append(candidate)
    reasons = {}
    for row in skipped:
        reasons[row.get('reason') or 'unknown'] = reasons.get(row.get('reason') or 'unknown', 0) + 1
    formal_frozen = [_frozen_candidate(candidate, 'formal', template_spec) for candidate in sendable]
    reserve_frozen = [_frozen_candidate(candidate, 'reserve', template_spec) for candidate in reserves]
    frozen = formal_frozen + reserve_frozen
    config = {'count': count, 'widen': bool(widen), 'windowEnabled': window is not None, 'template': template,
              'window': list(window or DEFAULT_WINDOW)}
    authorization = _authorization(count, len(formal_frozen), reserve_requested,
                                   len(reserve_frozen), widen, window, template)
    preview_hash = _preview_hash(config, authorization, frozen)
    return ({'available': True, 'requested': count, 'reserveRequested': reserve_requested, 'required': required,
            'sendable': len(formal_frozen), 'reserveReady': len(reserve_frozen),
            'frozenTotal': len(frozen),
            'fullPreparation': len(formal_frozen) == count and len(reserve_frozen) == reserve_requested,
            'positions': len(positions), 'readyAvailable': (state.get('layers') or {}).get('ready', 0),
            'samples': [_sample(c) for c in formal_frozen[:SAMPLE_SIZE]],
            # 短名的**质量**（不是卡点）：缓存里没有就用卡名、再不行用标题兜底——不让它挡发送。
            'nameQuality': _name_quality(sendable + reserves),
            'skipped': reasons, 'rateGap': rate_gap,
            'capacity': cap, 'window': window_now, 'widen': bool(widen),
            'previewHash': preview_hash, 'authorization': authorization}, frozen)


def _frozen_candidate(candidate, role, template_spec):
    """Freeze the actual card, text and all eligibility revisions shown in the preview."""
    frozen = json.loads(encoded(candidate))
    frozen['batchRole'] = role
    frozen['message'] = render_send_template(template_spec,frozen['name'],frozen['offer'],frozen['handle'])
    return frozen


def _preview_hash(config, authorization, candidates):
    return digest({'schema': 'bdhub.send-preview.v3', 'config': config,
                   'authorization': authorization,
                   'candidates': [{'position': index, 'creatorId': row['creatorId'],
                                   'oec': row['oecId'], 'pid': str(row['pid']),
                                   'sourceId': row['source']['sourceId'],
                                   'offerKey': row['offer']['offerKey'],
                                   'offerFingerprint': row['offerFingerprint'],
                                   'currentListId': str(row['card']['listId']),
                                   'planRevision': row['planRevision'],
                                   'controlRevision': row['controlRevision'],
                                   'candidateHash': digest(row)}
                                  for index, row in enumerate(candidates)]})


def _name_quality(candidates):
    counts = {}
    for candidate in candidates:
        key = candidate.get('nameSource') or '缓存'
        counts[key] = counts.get(key, 0) + 1
    return counts


def _sample(candidate):
    """样例要展示**真正会发出去的那句话**，不是我们对商品的内部叫法。

    话术由本批已选模板渲染（`cycle_materials.render`，v4），商品名用的是意语的
    `mentionIt`；`shortNameZh` 只是操作者的中文备注，绝不进话术，也不能顶在"商品"那一栏
    （以前这里先取中文短名，页面上看起来就像要给意大利达人发中文）。
    """
    offer = candidate['offer']
    name = candidate.get('name') or {}
    source = candidate.get('source') or {}
    source_class = source.get('sourceClass') or ('B' if source.get('sourceKind') == 'kalodata_video' else 'A')
    message = candidate.get('message') if name.get('mentionIt') else None
    return {'handle': candidate.get('handle'), 'oecId':str(candidate.get('oecId') or ''),
            'pid': str(candidate['pid']),'sourceClass':source_class,
            'sourceRank':source.get('sourceRank'),'units':source.get('units'),
            'gmv':source.get('revenueValue') if source_class=='A' else None,
            'videoViews':source.get('videoViews'),'videoId':source.get('videoId'),
            'videoReleasedAt':source.get('videoReleasedAt'),
            'name': name.get('mentionIt') or name.get('shortNameIt') or '',
            'nameZh': name.get('shortNameZh') or '',
            'nameSource': candidate.get('nameSource'),
            'messageIt': (message or {}).get('textIt') or '',
            'messageZh': (message or {}).get('translationZh') or '',
            'template': (message or {}).get('template') or '',
            'creatorPercent': offer.get('creatorPercent'), 'publicPercent': offer.get('publicPercent'),
            'campaignId': str(offer.get('campaignId') or ''), 'catalogSource': offer.get('catalogSource'),
            'currentListId':str((candidate.get('card') or {}).get('listId') or ''),
            'unlocked': bool(candidate.get('relationshipUnlocked'))}


def _authorization(requested, max_people, reserve_requested, reserve_ready, widen, window, template):
    """写进 `cycle_bulk.authorization` 的显式授权：它决定这一批到底被允许做什么。"""
    return {'source': 'current_user_request', 'scope': 'pool_to_send', 'maxPeople': max_people,
            'requestedPeople': requested,
            'reservePeople': reserve_requested, 'frozenPeople': max_people + reserve_ready,
            'reservePolicy': 'ceil-10-percent-v1',
            'widenLocalGate': bool(widen), 'sendWindow': list(window) if window else None,
            'messageTemplate': template,
            'institutionNewContactRollingCap': NEW_CONTACT_LIMIT,
            'materialPolicy': 'frozen-current-binding-v1',
            'note': '正式目标之外只消费本批冻结候补；unknown不释放名额；越界探测时平台原始回执必须落账'}


def _table_exists(db, name):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def _identifier(value, code):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{7,119}', value):
        raise CycleError(code)
    return value


def _hash(value, code):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value):
        raise CycleError(code)
    return value


def _batch_payload(db, row):
    counts = dict(db.execute('SELECT state,count(*) FROM cycle_bulk_item WHERE batch_id=? GROUP BY state',
                             (row['batch_id'],)))
    goal = db.execute('SELECT target FROM cycle_bulk WHERE id=?', (row['batch_id'],)).fetchone()
    target = goal[0] if goal else sum(counts.values())
    candidate_total = db.execute('SELECT count(*) FROM cycle_bulk_candidate WHERE batch_id=?',
                                 (row['batch_id'],)).fetchone()[0]
    attempted = sum(counts.values())
    runtime = None
    if _table_exists(db, 'cycle_bulk_runtime'):
        running = db.execute('SELECT pid,seen,phase FROM cycle_bulk_runtime WHERE batch_id=?',
                             (row['batch_id'],)).fetchone()
        if running:
            runtime = {'pid': running['pid'], 'seenAt': running['seen'], 'phase': running['phase']}
    unknown = []
    if _table_exists(db, 'cycle_delivery') and _table_exists(db, 'cycle_delivery_part'):
        for delivery in db.execute("SELECT d.id,i.creator_id,c.oec,c.pid FROM cycle_delivery d "
                                   "JOIN cycle_bulk_item i ON i.delivery_id=d.id "
                                   "JOIN cycle_bulk_candidate c ON c.batch_id=i.batch_id AND c.creator_id=i.creator_id "
                                   "WHERE i.batch_id=? AND d.state='unknown' ORDER BY d.id",
                                   (row['batch_id'],)):
            unknown.append({'deliveryId': delivery['id'], 'creatorId': delivery['creator_id'],
                            'oecId': delivery['oec'], 'pid': delivery['pid'],
                            'parts': dict(db.execute('SELECT kind,state FROM cycle_delivery_part '
                                                    'WHERE delivery_id=?', (delivery['id'],)))})
    return {'batchId': row['batch_id'], 'requestId': row['request_id'],
            'previewHash': row['preview_hash'], 'revision': row['revision'], 'state': row['state'],
            'target': target, 'attempted': attempted, 'counts': counts,
            'reserveTotal': max(0, candidate_total - target),
            'reservePromoted': max(0, attempted - target),
            'reserveRemaining': max(0, candidate_total - attempted),
            'config': json.loads(row['config_json']),
            'authorization': json.loads(row['authorization_json']),
            'authorizedAt': row['authorized_at'], 'stopRequestedAt': row['stop_requested_at'],
            'createdAt': row['created_at'], 'runtime': runtime, 'unknownDeliveries': unknown}


def batch_status(root, batch_id=None):
    """Read the latest frozen-v2 batch without exposing private candidates to the browser."""
    path = Path(root) / 'var/second-cycle.sqlite'
    if not path.exists():
        return None
    with closing(_connect(path)) as db:
        if not _table_exists(db, 'cycle_bulk_freeze'):
            return None
        if batch_id is not None:
            _identifier(batch_id, 'invalid_batch_id')
            row = db.execute('SELECT * FROM cycle_bulk_freeze WHERE batch_id=?', (batch_id,)).fetchone()
        else:
            row = db.execute("SELECT * FROM cycle_bulk_freeze ORDER BY created_at DESC LIMIT 1").fetchone()
        return _batch_payload(db, row) if row else None


def reconciliation_target(root, batch_id, delivery_id, expected_revision):
    """Return the exact original snapshot hash for one unknown delivery."""
    batch_id = _identifier(batch_id, 'invalid_batch_id')
    delivery_id = _identifier(delivery_id, 'invalid_delivery_id')
    if type(expected_revision) is not int or expected_revision < 1:
        raise CycleError('invalid_revision')
    with _store(Path(root)) as store:
        row = store.db.execute('SELECT * FROM cycle_bulk_freeze WHERE batch_id=?', (batch_id,)).fetchone()
        if not row:
            raise CycleError('batch_missing')
        if row['revision'] != expected_revision:
            raise CycleError('revision_conflict')
        if row['state'] != 'waiting_reconciliation':
            raise CycleError('batch_not_reconcilable')
        delivery = store.db.execute("SELECT d.snapshot FROM cycle_delivery d JOIN cycle_bulk_item i "
                                    "ON i.delivery_id=d.id WHERE i.batch_id=? AND d.id=? AND d.state='unknown'",
                                    (batch_id, delivery_id)).fetchone()
        if not delivery:
            raise CycleError('unknown_delivery_missing')
        return {'batchId': batch_id, 'deliveryId': delivery_id,
                'snapshotHash': digest(json.loads(delivery['snapshot']))}


def settle_reconciliation(root, batch_id, delivery_id, expected_revision):
    """Publish readback outcome without launching a worker or promoting reserves."""
    batch_id = _identifier(batch_id, 'invalid_batch_id')
    delivery_id = _identifier(delivery_id, 'invalid_delivery_id')
    with _store(Path(root)) as store, store.tx():
        row = store.db.execute('SELECT * FROM cycle_bulk_freeze WHERE batch_id=?', (batch_id,)).fetchone()
        if not row or row['revision'] != expected_revision:
            raise CycleError('revision_conflict')
        delivery = store.db.execute("SELECT d.state FROM cycle_delivery d JOIN cycle_bulk_item i "
                                    "ON i.delivery_id=d.id WHERE i.batch_id=? AND d.id=?",
                                    (batch_id, delivery_id)).fetchone()
        if not delivery:
            raise CycleError('unknown_delivery_missing')
        if delivery['state'] == 'confirmed':
            store.db.execute("UPDATE cycle_bulk_item SET state='confirmed',reason=NULL "
                             "WHERE batch_id=? AND delivery_id=?", (batch_id, delivery_id))
        remaining = store.db.execute("SELECT 1 FROM cycle_delivery d JOIN cycle_bulk_item i "
                                     "ON i.delivery_id=d.id WHERE i.batch_id=? AND d.state='unknown'",
                                     (batch_id,)).fetchone()
        if not remaining:
            store.db.execute("UPDATE cycle_bulk_freeze SET state='prepared',revision=revision+1,"
                             "authorized_at=NULL WHERE batch_id=?", (batch_id,))
            store.db.execute("UPDATE cycle_bulk SET state='prepared' WHERE id=?", (batch_id,))
            row = store.db.execute('SELECT * FROM cycle_bulk_freeze WHERE batch_id=?', (batch_id,)).fetchone()
        return _batch_payload(store.db, row)


def _require_freeze_schema(store):
    required = {'cycle_bulk', 'cycle_bulk_item', 'cycle_bulk_freeze', 'cycle_bulk_candidate'}
    if any(not _table_exists(store.db, table) for table in required):
        raise CycleError('send_batch_schema_migration_required')


def freeze_batch(root, request_id, expected_preview_hash, *, now=None, clock=None,
                 pool_reader=None, chooser=None):
    """Persist exactly the preview the user saw.  This never starts a worker or calls a platform."""
    root = Path(root)
    request_id = _identifier(request_id, 'invalid_request_id')
    expected_preview_hash = _hash(expected_preview_hash, 'invalid_preview_hash')
    config = load_config(root)
    window = config['window'] if config['windowEnabled'] else None
    preview_state, candidates = _preview(root, count=config['count'], widen=config['widen'], window=window,
                                         template=config['template'],
                                         now=now, clock=clock, pool_reader=pool_reader, chooser=chooser)
    if preview_state.get('previewHash') != expected_preview_hash:
        raise CycleError('preview_conflict')
    if not candidates:
        raise CycleError('batch_empty')
    if not preview_state.get('fullPreparation'):
        raise CycleError('full_preparation_required')
    stamp = clock() if clock is not None else (now if now is not None else time.time())
    batch_id = 'send-' + digest([request_id, expected_preview_hash])[:24]
    from lib.catalog_binding import offer_fingerprint
    with _store(root) as store, store.tx():
        _require_freeze_schema(store)
        prior = store.db.execute('SELECT * FROM cycle_bulk_freeze WHERE request_id=?',
                                 (request_id,)).fetchone()
        if prior:
            if prior['preview_hash'] != expected_preview_hash:
                raise CycleError('freeze_request_conflict')
            return _batch_payload(store.db, prior)
        active = store.db.execute("SELECT batch_id FROM cycle_bulk_freeze WHERE state IN "
                                  "('prepared','starting','running','stop_requested','waiting_reconciliation',"
                                  "'local_capacity_reached','platform_rejected','material_refresh_record_failed') "
                                  "ORDER BY created_at DESC LIMIT 1").fetchone()
        if active:
            raise CycleError('active_batch_exists')
        plan = _plan(store)
        authorization = preview_state['authorization']
        store.db.execute("INSERT INTO cycle_bulk VALUES(?,?,?,?, 'prepared', ?)",
                         (batch_id, plan, config['count'], encoded(authorization), stamp))
        store.db.execute("INSERT INTO cycle_bulk_freeze(batch_id,request_id,preview_hash,config_json,"
                         "authorization_json,revision,state,created_at) VALUES(?,?,?,?,?,1,'prepared',?)",
                         (batch_id, request_id, expected_preview_hash, encoded(config),
                          encoded(authorization), stamp))
        for position, candidate in enumerate(candidates):
            candidate_json = encoded(candidate)
            candidate_hash = digest(candidate)
            store.db.execute("INSERT INTO cycle_bulk_candidate VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                             (batch_id, position, candidate['creatorId'], candidate['oecId'],
                              str(candidate['pid']), candidate['source']['sourceId'],
                              candidate['offer']['offerKey'], offer_fingerprint(candidate['offer']),
                              str(candidate['card']['listId']), candidate_json, candidate_hash))
            if candidate.get('batchRole') == 'formal':
                store.db.execute("INSERT INTO cycle_bulk_item(batch_id,creator_id,handle,state) "
                                 "VALUES(?,?,?,'pending')",
                                 (batch_id, candidate['creatorId'], candidate['handle']))
        row = store.db.execute('SELECT * FROM cycle_bulk_freeze WHERE batch_id=?', (batch_id,)).fetchone()
        return _batch_payload(store.db, row)


def start_batch(root, batch_id, expected_revision, *, confirmed=False, now=None, clock=None):
    """Record the explicit user authorization.  Process launch remains in the CLI boundary."""
    root = Path(root)
    batch_id = _identifier(batch_id, 'invalid_batch_id')
    if type(expected_revision) is not int or expected_revision < 1 or confirmed is not True:
        raise CycleError('start_confirmation_required')
    stamp = clock() if clock is not None else (now if now is not None else time.time())
    with _store(root) as store, store.tx():
        _require_freeze_schema(store)
        row = store.db.execute('SELECT * FROM cycle_bulk_freeze WHERE batch_id=?', (batch_id,)).fetchone()
        if not row:
            raise CycleError('batch_missing')
        if row['state'] in ('starting', 'running') and row['revision'] == expected_revision + 1:
            return _batch_payload(store.db, row) | {'duplicate': True}
        if row['revision'] != expected_revision:
            raise CycleError('revision_conflict')
        if row['state'] not in ('prepared', 'start_failed'):
            raise CycleError('batch_not_startable')
        target = store.db.execute('SELECT target FROM cycle_bulk WHERE id=?', (batch_id,)).fetchone()[0]
        frozen = store.db.execute('SELECT count(*) FROM cycle_bulk_candidate WHERE batch_id=?',
                                  (batch_id,)).fetchone()[0]
        formal = store.db.execute("SELECT count(*) FROM cycle_bulk_candidate WHERE batch_id=? AND "
                                  "(json_extract(candidate_json,'$.batchRole')='formal' OR "
                                  "(json_extract(candidate_json,'$.batchRole') IS NULL AND position_order<?))",
                                  (batch_id, target)).fetchone()[0]
        items = store.db.execute('SELECT count(*) FROM cycle_bulk_item WHERE batch_id=?',
                                 (batch_id,)).fetchone()[0]
        if target <= 0 or frozen < target or formal != target or items != target:
            raise CycleError('frozen_batch_incomplete')
        if store._plan(store.db.execute('SELECT plan_id FROM cycle_bulk WHERE id=?',
                                       (batch_id,)).fetchone()[0])['state'] != 'active':
            raise CycleError('plan_paused')
        store.db.execute("UPDATE cycle_bulk_freeze SET state='starting',revision=revision+1,authorized_at=?,"
                         "stop_requested_at=NULL WHERE batch_id=?", (stamp, batch_id))
        store.db.execute("UPDATE cycle_bulk SET state='starting' WHERE id=?", (batch_id,))
        row = store.db.execute('SELECT * FROM cycle_bulk_freeze WHERE batch_id=?', (batch_id,)).fetchone()
        return _batch_payload(store.db, row) | {'duplicate': False}


def promote_reserves(store, batch_id):
    """Promote frozen reserves only after a formal member reached a definite non-contact terminal state."""
    batch = store.db.execute('SELECT target,state FROM cycle_bulk WHERE id=?', (batch_id,)).fetchone()
    if not batch or batch['state'] not in ('running', 'waiting_supply'):
        return 0
    unknown = store.db.execute("SELECT 1 FROM cycle_delivery d JOIN cycle_bulk_item i ON i.delivery_id=d.id "
                               "WHERE i.batch_id=? AND d.state='unknown'", (batch_id,)).fetchone()
    if unknown:
        return 0
    counts = dict(store.db.execute('SELECT state,count(*) FROM cycle_bulk_item WHERE batch_id=? GROUP BY state',
                                   (batch_id,)))
    succeeded = counts.get('confirmed', 0) + counts.get('contacted_inquiry', 0)
    active = sum(counts.get(state, 0) for state in ('pending', 'preparing', 'sending'))
    needed = max(0, batch['target'] - succeeded - active)
    if not needed:
        return 0
    rows = store.db.execute("SELECT c.creator_id,c.candidate_json FROM cycle_bulk_candidate c "
                            "WHERE c.batch_id=? AND json_extract(c.candidate_json,'$.batchRole')='reserve' "
                            "AND NOT EXISTS(SELECT 1 FROM cycle_bulk_item i WHERE i.batch_id=c.batch_id "
                            "AND i.creator_id=c.creator_id) ORDER BY c.position_order LIMIT ?",
                            (batch_id, needed)).fetchall()
    for row in rows:
        candidate = json.loads(row['candidate_json'])
        store.db.execute("INSERT INTO cycle_bulk_item(batch_id,creator_id,handle,state) "
                         "VALUES(?,?,?,'pending')", (batch_id, row['creator_id'], candidate['handle']))
    return len(rows)


def mark_batch_running(root, batch_id):
    """Publish a successfully launched worker without spending another user-visible revision."""
    with _store(Path(root)) as store, store.tx():
        row = store.db.execute('SELECT * FROM cycle_bulk_freeze WHERE batch_id=?', (batch_id,)).fetchone()
        if not row or row['state'] != 'starting':
            raise CycleError('batch_not_starting')
        store.db.execute("UPDATE cycle_bulk_freeze SET state='running' WHERE batch_id=?", (batch_id,))
        store.db.execute("UPDATE cycle_bulk SET state='running' WHERE id=?", (batch_id,))
        return _batch_payload(store.db, store.db.execute(
            'SELECT * FROM cycle_bulk_freeze WHERE batch_id=?', (batch_id,)).fetchone())


def mark_batch_start_failed(root, batch_id):
    with _store(Path(root)) as store, store.tx():
        store.db.execute("UPDATE cycle_bulk_freeze SET state='start_failed' WHERE batch_id=? "
                         "AND state='starting'", (batch_id,))
        store.db.execute("UPDATE cycle_bulk SET state='start_failed' WHERE id=? AND state='starting'", (batch_id,))


def stop_batch(root, batch_id, expected_revision, *, now=None, clock=None):
    batch_id = _identifier(batch_id, 'invalid_batch_id')
    if type(expected_revision) is not int or expected_revision < 1:
        raise CycleError('invalid_revision')
    stamp = clock() if clock is not None else (now if now is not None else time.time())
    with _store(Path(root)) as store, store.tx():
        _require_freeze_schema(store)
        row = store.db.execute('SELECT * FROM cycle_bulk_freeze WHERE batch_id=?', (batch_id,)).fetchone()
        if not row:
            raise CycleError('batch_missing')
        if row['state'] in ('stop_requested', 'stopped') and row['revision'] == expected_revision + 1:
            return _batch_payload(store.db, row) | {'duplicate': True}
        if row['revision'] != expected_revision:
            raise CycleError('revision_conflict')
        if row['state'] in ('completed', 'completed_with_exceptions', 'waiting_reconciliation'):
            raise CycleError('batch_not_stoppable')
        state = 'stop_requested' if row['state'] in ('starting', 'running') else 'stopped'
        store.db.execute('UPDATE cycle_bulk_freeze SET state=?,revision=revision+1,stop_requested_at=? '
                         'WHERE batch_id=?', (state, stamp, batch_id))
        store.db.execute('UPDATE cycle_bulk SET state=? WHERE id=?', (state, batch_id))
        row = store.db.execute('SELECT * FROM cycle_bulk_freeze WHERE batch_id=?', (batch_id,)).fetchone()
        return _batch_payload(store.db, row) | {'duplicate': False}


def _store(root):
    from lib.second_cycle import CycleStore
    return CycleStore(root / 'var/second-cycle.sqlite')


def _plan(store):
    row = store.db.execute("SELECT id FROM plan WHERE institution='bjn-local-research' AND market='it' "
                           "AND state='active'").fetchone()
    if not row:
        raise CycleError('plan_paused')
    return row[0]


def _identities(root):
    import sqlite3
    return sqlite3.connect((Path(root) / 'var/creator-identities.sqlite').resolve().as_uri() + '?mode=ro',
                           uri=True)
