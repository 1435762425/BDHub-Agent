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
* **执行** ← `bulk-second-send.py` / `cycle_burst.run_cohort`（车道、请求预算、卡片核验、回查、
  结果未知即停）

`preview()` 只读：它算的就是"这一批会发给谁、发什么、为什么有人被跳过"，页面拿它给操作者看那
2–3 条样例和总数。`create()` 才落库（`cycle_bulk`/`cycle_bulk_item`），执行仍由既有驱动器负责。
"""
import json
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lib.cycle_review import card_rate_gap, choose_candidates
from lib.lead_pool import pool
from lib.second_cycle import CycleError, encoded

BEIJING = timezone(timedelta(hours=8))
# 与 `cycle_delivery.reserve_contact()` 同一个口径：滚动 24 小时、只算新联系。
NEW_CONTACT_WINDOW_SECONDS = 86400
NEW_CONTACT_LIMIT = 500
DEFAULT_WINDOW = ('09:00', '24:00')
SAMPLE_SIZE = 3


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


CONFIG_DEFAULT = {'count': 500, 'widen': False, 'windowEnabled': False,
                  'window': list(DEFAULT_WINDOW)}


def config_path(root):
    return Path(root) / 'config/send-batch.json'


def validate_config(raw):
    """页面能改的只有三件：这一批多少条、要不要越界、窗口开不开与几点到几点。"""
    if not isinstance(raw, dict):
        raise CycleError('invalid_send_config')
    if set(raw) - {'count', 'widen', 'windowEnabled', 'window'}:
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
                    now=now, clock=clock, pool_reader=pool_reader, chooser=chooser)
    layers = pool(root, now=clock() if clock else (now if now is not None else time.time())) if pool_reader is None else {}
    return {'config': config, 'preview': state,
            'pool': {'counts': layers.get('counts') or {}, 'layers': layers.get('layers') or {}},
            'available': state.get('available', False)}


def save_and_status(root, raw, *, now=None, clock=None, pool_reader=None, chooser=None):
    """存下设置并回答**存完之后**的状态。

    顺序反过来（先算再存）会让回答里的预检还是旧配置——页面会显示"一批 500 条却能发 600"。
    """
    saved = save_config(root, raw)
    state = status(root, now=now, clock=clock, pool_reader=pool_reader, chooser=chooser)
    return {**state, 'config': saved, 'saved': True}


def preview(root, *, count=500, widen=False, window=None, now=None, clock=None,
            pool_reader=None, chooser=None):
    """这一批会发给谁、发什么、谁被跳过（只读，不落库、不碰平台）。"""
    root = Path(root)
    if type(count) is not int or not 1 <= count <= 2000:
        raise CycleError('invalid_send_count')
    window = _window_arg(window)
    stamp = clock() if clock is not None else (now if now is not None else time.time())
    # 池子的可发层里，前面会堆着过不了复检的槽位（缺卡片核验/被关系控制挡住），所以**接着往下取**，
    # 直到凑够这一批或把可发层走完；取过的槽位与跳过原因都要如实报出来。
    read_pool = pool_reader or (lambda: pool(root, now=stamp, limit=min(4000, max(count * 4, count))))
    state = read_pool()
    if not state.get('available'):
        return {'available': False, 'requested': count, 'sendable': 0, 'samples': [], 'skipped': {},
                'rateGap': {'same': 0, 'lower': 0, 'lowerByOne': 0, 'higher': 0, 'noCard': 0,
                            'examples': [], 'readable': False},
                'capacity': None, 'window': window_state(window, stamp), 'widen': bool(widen)}
    slots = [row for row in state['pools'].get('ready', [])]
    positions = [(row['creatorId'], row['pid']) for row in slots]
    with _store(root) as store:
        plan = _plan(store)
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
    for index, candidate in enumerate(candidates):
        if not window_now['open']:
            skipped.append({'sourceId': candidate['source']['sourceId'], 'reason': 'outside_send_window'})
            continue
        # 一批就是一批：凑够 `count` 条之后剩下的都不进这一批（池子里还留着，下次再发）。
        if len(sendable) >= count:
            skipped.append({'sourceId': candidate['source']['sourceId'], 'reason': 'beyond_requested_size'})
            continue
        if space is not None and len(sendable) >= space:
            skipped.append({'sourceId': candidate['source']['sourceId'], 'reason': 'local_capacity_reached'})
            continue
        sendable.append(candidate)
    reasons = {}
    for row in skipped:
        reasons[row.get('reason') or 'unknown'] = reasons.get(row.get('reason') or 'unknown', 0) + 1
    return {'available': True, 'requested': count, 'sendable': len(sendable),
            'positions': len(positions), 'readyAvailable': (state.get('layers') or {}).get('ready', 0),
            'samples': [_sample(c) for c in sendable[:SAMPLE_SIZE]],
            # 短名的**质量**（不是卡点）：缓存里没有就用卡名、再不行用标题兜底——不让它挡发送。
            'nameQuality': _name_quality(sendable),
            'skipped': reasons, 'rateGap': rate_gap,
            'capacity': cap, 'window': window_now, 'widen': bool(widen),
            'authorization': _authorization(count, widen, window)}


def _name_quality(candidates):
    counts = {}
    for candidate in candidates:
        key = candidate.get('nameSource') or '缓存'
        counts[key] = counts.get(key, 0) + 1
    return counts


def _sample(candidate):
    """样例要展示**真正会发出去的那句话**，不是我们对商品的内部叫法。

    话术由既有模板渲染（`cycle_materials.render`，v4 `standard`），商品名用的是意语的
    `mentionIt`；`shortNameZh` 只是操作者的中文备注，绝不进话术，也不能顶在"商品"那一栏
    （以前这里先取中文短名，页面上看起来就像要给意大利达人发中文）。
    """
    from lib.cycle_materials import render
    offer = candidate['offer']
    name = candidate.get('name') or {}
    message = render(name, offer, 'standard', candidate.get('handle')) if name.get('mentionIt') else None
    return {'handle': candidate.get('handle'), 'pid': str(candidate['pid']),
            'name': name.get('mentionIt') or name.get('shortNameIt') or '',
            'nameZh': name.get('shortNameZh') or '',
            'nameSource': candidate.get('nameSource'),
            'messageIt': (message or {}).get('textIt') or '',
            'messageZh': (message or {}).get('translationZh') or '',
            'template': (message or {}).get('template') or '',
            'creatorPercent': offer.get('creatorPercent'), 'publicPercent': offer.get('publicPercent'),
            'campaignId': str(offer.get('campaignId') or ''), 'catalogSource': offer.get('catalogSource'),
            'unlocked': bool(candidate.get('relationshipUnlocked'))}


def _authorization(count, widen, window):
    """写进 `cycle_bulk.authorization` 的显式授权：它决定这一批到底被允许做什么。"""
    return {'source': 'current_user_request', 'scope': 'pool_to_send', 'maxPeople': count,
            'widenLocalGate': bool(widen), 'sendWindow': list(window) if window else None,
            'institutionNewContactRollingCap': NEW_CONTACT_LIMIT,
            'note': '位置来自发送池 ready 层；越界探测时平台原始回执必须落账'}


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
