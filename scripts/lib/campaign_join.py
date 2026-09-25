"""加入 Seller Campaign：预览 → 显式确认 → 提交 → 回查。

**这是本系统里第二个平台写入动作**（第一个是建 TapLink），所以纪律照抄旧版已经被验证的那套：

* **写入前先落盘**：条目先记 `writing` + `write_attempted`，再发 POST。任何"提过了但不知道结果"的条目
  只能靠**回查**结算，绝不重新提交（`campaign_write_requires_verification`）。
* **原意图回查**：`verify` 重新读一次已加入列表，把 `writing`/`result_unknown` 结算成 `joined`。
  平台加入后可能返回**子活动 id**，所以结算时以"原活动 id 或返回的子活动 id 出现在已加入列表里"为准。
* **一次一个活动一次写入**：冻结请求由传输层校验（`opportunity_campaign_joiner`），
  写不匹配的请求会被拒；同一活动一次会话最多写一次。
* **明确拒绝 ≠ 结果未知**：平台返回明确的非 0 code（且不是验证/超时/鉴权类）才记 `skipped`；
  其余保留 `result_unknown`；其他活动继续，原申请只做有限只读核验。
* **没有合格活动的商品不硬挑**：加入同样只针对**预览里 eligible 的那批**。

**账号**：加入活动是货盘侧的写入，走货盘账号（ACC9，与目录读取、建链同一个）；
能力门禁沿用旧版定义（IT 的 `campaign_join` 在旧库里是 enabled）。
"""
import json
import fcntl
import functools
import signal
import threading
import re
import sqlite3
import time
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lib.cycle_catalog import CAMPAIGNS

# 预览/提交/回查是**同一个 job 的三个动作**：共用一个 id，提交时才看得到预览判定过的条目。
DEFAULT_JOB_ID = 'campaign-join'
# 已确认口径：Campaign 剩余有效期 > 45 天（旧版加入用的是"2 个月"，本次统一到 45 天）。
REMAINING_DAYS = 45
VERIFY_ROUNDS = 4
VERIFY_SECONDS = 300
EXTENSIONS = {
    'request_json': "TEXT NOT NULL DEFAULT '{}'",
    'verify_attempts': 'INTEGER NOT NULL DEFAULT 0',
    'verify_deadline': 'REAL',
    'followup_stopped': 'INTEGER NOT NULL DEFAULT 0',
    'verify_error': "TEXT NOT NULL DEFAULT ''",
}
ITEM_STATES = ('eligible', 'joined', 'writing', 'result_unknown', 'skipped')

def _job_id(market, value):
    return value or (DEFAULT_JOB_ID if market == 'it' else f'{DEFAULT_JOB_ID}-{market}')
SCHEMA = '''
CREATE TABLE IF NOT EXISTS campaign_join_job(
  id TEXT PRIMARY KEY, market TEXT NOT NULL, account TEXT NOT NULL, state TEXT NOT NULL,
  action TEXT NOT NULL, error TEXT NOT NULL DEFAULT '', contact_email TEXT NOT NULL DEFAULT '',
  campaign_ids TEXT NOT NULL DEFAULT '[]', joined_count INTEGER NOT NULL DEFAULT 0,
  created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS campaign_join_item(
  job_id TEXT NOT NULL, campaign_id TEXT NOT NULL, name TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', write_attempted INTEGER NOT NULL DEFAULT 0,
  joined_campaign_id TEXT, updated REAL NOT NULL, PRIMARY KEY(job_id,campaign_id));
CREATE INDEX IF NOT EXISTS campaign_join_item_state ON campaign_join_item(job_id,state);
'''


def root_of(module_file=__file__):
    return Path(module_file).resolve().parents[2]


def db_path(root, market='it'):
    return Path(root) / ('var/campaign-join.sqlite' if market == 'it' else f'var/campaign-join-{market}.sqlite')


def migrate(root, *, market='it'):
    """Explicit additive, repeatable upgrade. Caller backs up existing production state first."""
    path = db_path(root, market)
    if not path.exists():
        return {'exists': False, 'added': []}
    with closing(sqlite3.connect(path, timeout=15)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        columns = {r[1] for r in db.execute('PRAGMA table_info(campaign_join_item)')}
        if not columns:
            raise ValueError('campaign_join_schema_invalid')
        added = []
        for name, definition in EXTENSIONS.items():
            if name not in columns:
                db.execute(f'ALTER TABLE campaign_join_item ADD COLUMN {name} {definition}')
                added.append(name)
    return {'exists': True, 'added': added}


_local = threading.local()


def serialized(function):
    """Keep preview replacement, frozen writes and verification from racing across CLIs."""
    @functools.wraps(function)
    def run(root, *args, **kwargs):
        path = db_path(root, kwargs.get('market', 'it')).with_suffix('.lock').resolve()
        held = getattr(_local, 'held', set())
        if path in held:
            return function(root, *args, **kwargs)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError('campaign_join_busy') from None
            _local.held = held | {path}
            try:
                return function(root, *args, **kwargs)
            finally:
                _local.held = held
                fcntl.flock(lock, fcntl.LOCK_UN)
    return run


class VerificationDeadline(BaseException):
    """Not swallowed by transport retry/verification handlers catching Exception."""


@contextmanager
def verification_timeout(seconds):
    # The production entrypoint is a dedicated CLI on macOS. Arm before transport setup,
    # so authentication, profile locks, pagination and challenge solving share one bound.
    if threading.current_thread() is not threading.main_thread():
        raise ValueError('campaign_verification_requires_main_thread')
    if seconds <= 0:
        raise VerificationDeadline()
    previous = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    if previous_timer[0]:
        raise ValueError('campaign_verification_timer_in_use')
    def expired(*_):
        raise VerificationDeadline()
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def join_payload(campaign_id, email):
    """The frozen request body. Same shape as the legacy protocol; validated before it is frozen."""
    if not re.fullmatch(r'[0-9]{10,32}', str(campaign_id)):
        raise ValueError('campaign_join_id_invalid')
    if not isinstance(email, str) or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email) or len(email) > 254:
        raise ValueError('campaign_join_email_invalid')
    return {'campaign_id': str(campaign_id), 'is_joined': True,
            'contact_info': {'campaign_contact_info': [{'type': 2, 'value': email}]}}


def eligibility(campaign, *, at=None):
    """Whether a joinable campaign is worth joining; returns (ok, reason)."""
    now = datetime.now(timezone.utc) if at is None else at
    end_ms = campaign.get('promotion_end_time')
    try:
        end_ms = int(end_ms)
    except (TypeError, ValueError):
        return False, 'campaign_end_missing'
    if end_ms <= 0:
        return False, 'campaign_end_missing'
    end = datetime.fromtimestamp(end_ms / 1000, timezone.utc)
    start_ms = campaign.get('promotion_start_time')
    try:
        start = datetime.fromtimestamp(int(start_ms) / 1000, timezone.utc) if int(start_ms or 0) > 0 else None
    except (TypeError, ValueError):
        start = None
    if start is not None and start > now:
        return False, 'campaign_not_started'
    if end <= now:
        return False, 'campaign_ended'
    if end <= now + timedelta(days=REMAINING_DAYS):
        return False, 'campaign_expiry_within_45_days'
    return True, ''


class Store:
    """Durable per-campaign join ledger. One job per request key; re-running it is idempotent."""

    def __init__(self, root, *, market='it', clock=time.time, readonly=False):
        self.root = Path(root)
        self.market = market
        self.path = db_path(root, market)
        self.clock = clock
        if readonly:
            self.db = sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
        else:
            fresh = not self.path.exists()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(self.path, timeout=15)
            if fresh:
                self.db.executescript(SCHEMA)
                migrate(root, market=market)
            columns = {r[1] for r in self.db.execute('PRAGMA table_info(campaign_join_item)')}
            if not set(EXTENSIONS) <= columns:
                self.db.close()
                raise ValueError('campaign_join_migration_required')
        self.db.row_factory = sqlite3.Row
        if readonly:
            self.db.execute('BEGIN')

    def close(self):
        self.db.close()

    def job(self, job_id=None):
        if job_id:
            return self.db.execute('SELECT * FROM campaign_join_job WHERE id=?', (job_id,)).fetchone()
        return self.db.execute('SELECT * FROM campaign_join_job WHERE market=? ORDER BY created DESC LIMIT 1',
                               (self.market,)).fetchone()

    def open_job(self, *, account, action, request_id, email='', campaign_ids=()):
        """Create or reuse the job for this request id.

        **One job, several actions** (inspect → apply → verify), exactly like the legacy workspace:
        a repeated request must not fork a second job, or the "already attempted" record would be
        split across two ledgers and a write could be repeated. Only a different account is a conflict.
        """
        existing = self.db.execute('SELECT * FROM campaign_join_job WHERE id=?', (request_id,)).fetchone()
        if existing:
            if account and existing['account'] and existing['account'] != account:
                raise ValueError('campaign_join_request_conflict')
            return existing
        stamp = self.clock()
        with self.db:
            self.db.execute('INSERT INTO campaign_join_job(id,market,account,state,action,contact_email,'
                            'campaign_ids,created,updated) VALUES(?,?,?,?,?,?,?,?,?)',
                            (request_id, self.market, account, 'draft', action, email,
                             json.dumps([str(c) for c in campaign_ids]), stamp, stamp))
        return self.job(request_id)

    def set_job(self, job_id, **fields):
        fields['updated'] = self.clock()
        columns = ','.join(f'{key}=?' for key in fields)
        with self.db:
            self.db.execute(f'UPDATE campaign_join_job SET {columns} WHERE id=?',
                            (*fields.values(), job_id))
        return self.job(job_id)

    def items(self, job_id):
        return [dict(row) for row in
                self.db.execute('SELECT * FROM campaign_join_item WHERE job_id=? ORDER BY campaign_id', (job_id,))]

    def replace_items(self, job_id, rows):
        previous = {r['campaign_id']: r for r in self.items(job_id)}
        stamp = self.clock()
        with self.db:
            self.db.execute('DELETE FROM campaign_join_item WHERE job_id=?', (job_id,))
            self.db.executemany(
                'INSERT INTO campaign_join_item(job_id,campaign_id,name,state,reason,write_attempted,'
                'joined_campaign_id,updated) VALUES(?,?,?,?,?,?,?,?)',
                [(job_id, str(row['campaign_id']), str(row.get('name') or ''), row['state'],
                  str(row.get('reason') or ''), int(row.get('write_attempted') or 0),
                  row.get('joined_campaign_id'), stamp) for row in rows])
            for row in rows:
                old = previous.get(str(row['campaign_id']))
                if old:
                    fields = {key: old[key] for key in EXTENSIONS}
                    self.db.execute('UPDATE campaign_join_item SET ' + ','.join(f'{key}=?' for key in fields)
                                    + ' WHERE job_id=? AND campaign_id=?',
                                    (*fields.values(), job_id, str(row['campaign_id'])))

    def set_item(self, job_id, campaign_id, **fields):
        """Persist one item's new state. Called *before* a write and again after it settles."""
        fields['updated'] = self.clock()
        columns = ','.join(f'{key}=?' for key in fields)
        with self.db:
            self.db.execute(f'UPDATE campaign_join_item SET {columns} WHERE job_id=? AND campaign_id=?',
                            (*fields.values(), job_id, str(campaign_id)))
        return next((row for row in self.items(job_id) if row['campaign_id'] == str(campaign_id)), None)

    def unresolved(self, job_id=None):
        """Items that were written to but never settled -- the only ones a verify may touch."""
        job = self.job(job_id)
        if not job:
            return []
        return [row for row in self.items(job['id']) if row['state'] in ('writing', 'result_unknown')]

    def states(self, job_id=None):
        job = self.job(job_id)
        if not job:
            return {}
        counts = {}
        for row in self.items(job['id']):
            counts[row['state']] = counts.get(row['state'], 0) + 1
        return counts


JOINABLE_PARAMS = {'status': 0, 'crs_campaign_type': 4, 'campaign_list_scene': 2,
                   'seller_campaign_type': 0, 'sort_field': 0}
JOINED_PARAMS = {'campaign_join_status_category': '1', 'crs_campaign_types': ''}


def _page(transport, params, page):
    """One page of a campaign list. Read-only; the transport refuses any write here."""
    query = transport._params() | params | {'cur_page': page, 'page_size': 100}
    outcome = transport._xhr(method='GET', path=CAMPAIGNS, params=query, payload=None, write=False)
    body = transport.require_read(outcome)
    data = (body or {}).get('data') or {}
    rows = data.get('campaign') or []
    if not isinstance(rows, list):
        raise ValueError('campaign_list_malformed')
    total = data.get('total')
    if isinstance(total, str) and total.isascii() and total.isdigit():
        total = int(total)
    return rows, (total if isinstance(total, int) and total >= 0 else None)


def _all_pages(transport, params, *, limit=20):
    """Every page, or an error. **Incomplete pagination is never treated as "nothing there"**."""
    rows, page, expected, seen = [], 1, None, set()
    while True:
        batch, total = _page(transport, params, page)
        if total is None or (expected is not None and expected != total):
            raise ValueError('campaign_list_incomplete')
        expected = total
        ids = [str(row.get('campaign_id') or '') for row in batch if isinstance(row, dict)]
        if len(ids) != len(batch) or not all(ids) or len(set(ids)) != len(ids) or seen.intersection(ids):
            raise ValueError('campaign_list_incomplete')
        seen.update(ids)
        rows.extend(batch)
        if not batch:
            break
        if total is not None and len(rows) >= total:
            if len(rows) != total:
                raise ValueError('campaign_list_incomplete')
            break
        page += 1
        if page > limit:
            raise ValueError('campaign_list_page_limit')
    if total is not None and len(rows) != total:
        # 列表读完了但对不上总数：宁可报错，也不把"没读到"当成"平台没有这个活动"。
        raise ValueError('campaign_list_incomplete')
    return rows


# 「平台活动」这一族：父活动(type 6) 与子活动(type 7)。**已加入列表存的是子活动 id**，
# 所以按 id 直接做差集会得出"漏了 3 个"的假结论——这里按父子关系对照。
PLATFORM_CAMPAIGN_TYPES = (6, 7)


def read_other_categories(transport, joined, *, at=None):
    """其它分类的活动（只读）。加入用的查询**只覆盖 Seller collabs 这一类**（旧版同样如此），
    所以"可加入 0"只说明这一类没有剩余，不代表平台没有别的活动。

    实测平台的分类：`crs_campaign_type` 4=Seller collabs 可加入、5=其已加入的列法、
    6=平台活动（父）、7=平台活动（子）；8/9 属于全托那条渠道。
    """
    at = at if at is not None else datetime.now(timezone.utc)
    rows = {}
    for kind in PLATFORM_CAMPAIGN_TYPES:
        try:
            rows[kind] = _all_pages(transport, {'status': 0, 'crs_campaign_type': kind,
                                                'campaign_list_scene': 2, 'seller_campaign_type': 0,
                                                'sort_field': 0})
        except Exception:                                   # noqa: BLE001 - 读不到就不报这一类，不编数字
            rows[kind] = None
    if rows.get(6) is None or rows.get(7) is None:
        return {'available': False}
    joined = {str(c) for c in (joined or ())}
    unjoined = []
    for row in rows[7]:
        cid = str(row.get('campaign_id') or '')
        if not cid or cid in joined:
            continue
        ok, reason = eligibility(row, at=at)
        unjoined.append({'campaignId': cid, 'name': str(row.get('name') or '')[:80],
                         'status': row.get('status'), 'eligible': ok, 'reason': reason})
    return {'available': True, 'parents': len(rows[6]), 'subs': len(rows[7]),
            'unjoined': unjoined,
            'unjoinedEligible': sum(1 for row in unjoined if row['eligible'])}


def read_campaigns(transport):
    """(可加入活动, 已加入活动 id)。两个都是只读；加入与否以平台自己的回答为准。"""
    joinable = _all_pages(transport, JOINABLE_PARAMS)
    joined = {str(row.get('campaign_id')) for row in _all_pages(transport, JOINED_PARAMS)
              if row.get('campaign_id')}
    return joinable, joined


@serialized
def preview(root, *, market='it', transport=None, clock=time.time, job_id=None):
    """Read-only: what could be joined, what is already joined, and why the rest is not eligible."""
    root = Path(root or root_of())
    report = {'platformWrites': 0}
    job_id = _job_id(market, job_id)
    store = Store(root, market=market, clock=clock)
    try:
        if transport is None:
            from lib.global_source_transport import opportunity_reader
            context = opportunity_reader(report, market=market, extra_read_endpoints={(CAMPAIGNS, 'GET')})
        else:
            context = transport
        with context as live:
            campaigns, joined = read_campaigns(live)
            # 顺带只读地看一眼**其它分类**：加入的查询只覆盖 Seller collabs 一类，
            # 不看一眼就会以为"可加入 0"等于平台上没有活动了。
            other = read_other_categories(live, joined, at=datetime.fromtimestamp(clock(), timezone.utc))
        rows = []
        for campaign in campaigns:
            cid = str(campaign.get('campaign_id') or '')
            if not cid:
                continue
            name = str(campaign.get('name') or campaign.get('campaign_name') or '')
            if cid in joined:
                rows.append({'campaign_id': cid, 'name': name, 'state': 'joined', 'reason': '已加入'})
                continue
            ok, reason = eligibility(campaign, at=datetime.fromtimestamp(clock(), timezone.utc))
            rows.append({'campaign_id': cid, 'name': name,
                         'state': 'eligible' if ok else 'skipped', 'reason': reason})
        job = store.open_job(account=_account_name(root, market), action='inspect', request_id=job_id)
        # 预览**不得**抹掉"提交过"的记录：它原来是整表替换，于是"先预览再提交"的路径
        # （一键加入就是）会把 write_attempted 一并清空，下一次提交就会重复写平台——
        # 而"结果未知绝不重试"正是这条状态机存在的理由。
        previous = {row['campaign_id']: row for row in store.items(job['id'])}
        fresh = {row['campaign_id'] for row in rows}
        merged = []
        for row in rows:
            old = previous.get(row['campaign_id'])
            if old and old['state'] in ('writing', 'result_unknown', 'joined'):
                # Preserve original evidence even when the platform now positively settles it.
                observed = cid in joined or old['joined_campaign_id'] in joined
                row = {**row, 'state': 'joined' if observed else old['state'],
                       'reason': 'observed_joined_in_normal_refresh' if observed else old['reason'],
                       'write_attempted': old['write_attempted'],
                       'joined_campaign_id': old['joined_campaign_id']}
            merged.append(row)
        for cid, old in previous.items():
            if cid not in fresh and old['write_attempted']:
                if cid in joined or old['joined_campaign_id'] in joined:
                    old = {**old, 'state': 'joined', 'reason': 'observed_joined_in_normal_refresh'}
                merged.append(old)
        store.replace_items(job['id'], merged)
        store.set_job(job['id'], state='previewed', error='', joined_count=len(joined))
        # 返回**与 status 同一个封装**，另加三个预览专属字段：页面两处读的是同一份状态，
        # 少了 available 这一层，预览结果会被当成"不可用"直接丢掉（点了按钮像没反应）。
        summary = status(root, market=market, clock=clock, job_id=job['id'])
        return summary | {'campaigns': len(rows), 'joined': len(joined),
                          'otherCategories': other,
                          'eligible': sum(1 for row in merged if row['state'] == 'eligible')}
    finally:
        store.close()


def _account_name(root, market='it'):
    """The catalogue account for this workspace; '' only in a workspace with no market config.

    A real workspace always has config/market-accounts.json -- the fallback exists so an isolated
    test workspace can still exercise the state machine without inventing an account.
    """
    from lib.market_accounts import catalog_read_account
    try:
        return catalog_read_account(Path(root), market=market)
    except (OSError, ValueError, KeyError):
        return ''


@serialized
def apply(root, *, market='it', campaign_ids, email, confirm=False, canary=False, transport=None,
          clock=time.time, job_id=None):
    """Join the named campaigns. **A platform write**: refuses without ``confirm=True``.

    Only campaigns that the current preview called ``eligible`` are accepted, each is written at most
    once; an unresolved item is skipped while other eligible items continue.
    """
    root = Path(root or root_of())
    job_id = _job_id(market, job_id)
    if confirm is not True:
        raise ValueError('campaign_join_confirmation_required')
    wanted = [str(c) for c in (campaign_ids or [])]
    if not wanted or len(set(wanted)) != len(wanted) or len(wanted) > 100 or \
       type(canary) is not bool or canary and len(wanted) != 1:
        raise ValueError('campaign_join_selection_invalid')
    payloads = {cid: join_payload(cid, email) for cid in wanted}
    store = Store(root, market=market, clock=clock)
    report = {'platformWrites': 0}
    account_blocked = False
    try:
        job = store.open_job(account=_account_name(root, market), action='apply', request_id=job_id,
                             email=email, campaign_ids=wanted)
        rows = {row['campaign_id']: row for row in store.items(job['id'])}
        if not rows:
            raise ValueError('campaign_join_preview_required')
        unknown = [row['campaign_id'] for cid, row in rows.items()
                   if row['state'] in ('writing', 'result_unknown') and cid in payloads]
        if unknown:
            # 提过了但没结算：只能先回查，绝不重新提交。
            raise ValueError('campaign_write_requires_verification')
        missing = [cid for cid in wanted if cid not in rows]
        if missing:
            raise ValueError('campaign_join_not_in_preview')
        # 邮箱与勾选范围在写入之前落盘：出事以后这是一条能对得上的证据。
        store.set_job(job['id'], state='applying', action='apply', error='', contact_email=email,
                      campaign_ids=json.dumps(wanted))
        if transport is None:
            from lib.global_source_transport import opportunity_campaign_joiner
            context = opportunity_campaign_joiner(report, payloads, market=market, canary=canary)
        else:
            context = transport
        with context as live:
            campaigns, joined = read_campaigns(live)
            joinable = {str(c.get('campaign_id')) for c in campaigns}
            for cid in wanted:
                row = rows[cid]
                if row['state'] == 'joined' or cid in joined or row['joined_campaign_id'] in joined:
                    store.set_item(job['id'], cid, state='joined', reason='observed_joined_before_write')
                    continue
                if cid not in joinable:
                    store.set_item(job['id'], cid, state='skipped', reason='campaign_not_joinable_now')
                    continue
                campaign = next(c for c in campaigns if str(c.get('campaign_id')) == cid)
                valid, reason = eligibility(campaign, at=datetime.fromtimestamp(clock(), timezone.utc))
                if not valid:
                    store.set_item(job['id'], cid, state='skipped', reason=reason)
                    continue
                if row['write_attempted']:
                    store.set_item(job['id'], cid, state='result_unknown',
                                   reason='campaign_write_requires_verification')
                    continue
                # Persist the attempt BEFORE the write: a crash here must not look like "never tried".
                store.set_item(job['id'], cid, state='writing', write_attempted=1, reason='',
                               request_json=json.dumps(payloads[cid], sort_keys=True),
                               verify_attempts=0, verify_deadline=None, followup_stopped=0, verify_error='')
                try:
                    outcome = _submit_with_verification(live, cid, payloads[cid])
                except Exception as error:                      # noqa: BLE001 - unknown, never a retry
                    # 记**消息**，不只记类型名：只留一个 "TapLinkError" 时根本查不出原因
                    # （实际遇到过：真正的原因是 taplink_endpoint_not_allowed，写根本没发出去）。
                    store.set_item(job['id'], cid, state='result_unknown',
                                   reason='write_uncertain:' + safe_error(error))
                    if _account_failure(error):
                        account_blocked = True
                        break
                    continue
                settled, code = _settle(outcome)
                returned = _returned_campaign(outcome)
                if returned:
                    # 平台加入后可能只把"子活动"列出来；记住它，回查才认得出来。与结果是否明确无关。
                    store.set_item(job['id'], cid, joined_campaign_id=returned)
                if settled == 'joined':
                    store.set_item(job['id'], cid, state='joined', reason=code or 'joined')
                elif settled == 'skipped':
                    store.set_item(job['id'], cid, state='skipped', reason=code or 'rejected')
                else:
                    store.set_item(job['id'], cid, state='result_unknown', reason=code or 'result_unknown')
                    if _account_failure(outcome):
                        account_blocked = True
                        break
            if not store.unresolved(job['id']):
                try:
                    _, joined = read_campaigns(live)
                except Exception:
                    pass  # Optional count refresh cannot change write outcomes.
        counts = store.states(job['id'])
        state = 'needs_verification' if counts.get('writing') or counts.get('result_unknown') else 'completed'
        joined_total = len(joined)
        store.set_job(job['id'], state=state, joined_count=joined_total)
        return {'jobId': job['id'], 'state': state, 'counts': counts,
                'items': store.items(job['id']), 'platformWrites': report.get('platformWrites', 0),
                'accountBlocked': account_blocked}
    except Exception as error:
        job = store.job(job_id)
        if job:
            store.set_job(job['id'], state='blocked', error=safe_error(error))
        raise
    finally:
        store.close()


@serialized
def join_all(root, *, market='it', email, confirm=False, transport=None, clock=time.time, job_id=None):
    """一键加入：**先重新预览一遍**，再把当前全部合格的活动一次提交。

    为什么先预览：不拿页面上的旧列表去写平台——那样会把已经不可加入的活动也提交一遍，
    甚至拿过期的判断去写。预览是只读的，每次点击都重算一遍，代价很小。

    安全纪律与 ``apply`` 完全一致（只接受预览判定为合格的活动、每个活动一次会话只写一次、
    结果未知跳过该项，正常准备后由 ``verify`` 有限回查），因为这里只是把「预览」和「提交」串成一次调用，
    两步共用同一个 job id——提交时才看得到预览判定过的条目。
    """
    root = Path(root or root_of())
    job_id = _job_id(market, job_id)
    if confirm is not True:
        raise ValueError('campaign_join_confirmation_required')
    previewed = preview(root, market=market, transport=transport, clock=clock, job_id=job_id)
    eligible = [row['campaign_id'] for row in previewed['items'] if row['state'] == 'eligible']
    if not eligible:
        return previewed | {'eligible': 0, 'attempted': [], 'platformWrites': 0}
    writes, attempted, account_blocked = 0, [], False
    for start in range(0, len(eligible), 100):
        batch = eligible[start:start + 100]
        applied = apply(root, market=market, campaign_ids=batch, email=email, confirm=True, transport=transport,
                        clock=clock, job_id=job_id)
        writes += int(applied.get('platformWrites') or 0)
        attempted.extend(batch)
        if applied.get('accountBlocked'):
            account_blocked = True
            break
    current = status(root, market=market, clock=clock, job_id=job_id)
    wanted = set(eligible)
    counts = {}
    for row in current.get('items') or []:
        if row['campaign_id'] in wanted:
            counts[row['state']] = counts.get(row['state'], 0) + 1
    # 与 status 同一个封装（页面只认这一种形状），但 **platformWrites 用真实写入次数**，
    # 不能沿用 status 里那个恒为 0 的展示值。
    return current | {
        'eligible': len(eligible), 'attempted': attempted, 'appliedCounts': counts,
        'platformWrites': writes, 'accountBlocked': account_blocked}


@serialized
def verify(root, *, market='it', transport=None, clock=time.time, job_id=None, bounded=False):
    """Original-account reads only; four shared rounds and one durable 300-second deadline.

    `bounded` consumes the remaining rounds in this invocation. A one-round manual call uses
    the same ledger and cannot reset the lifetime budget. Exhausted rows are never polled here.
    """
    entered = clock()
    root = Path(root or root_of())
    job_id = _job_id(market, job_id)
    store = Store(root, market=market, clock=clock)
    settled = joined_settled = 0
    try:
        job = store.job(job_id)
        if not job:
            raise ValueError('campaign_join_job_missing')
        account = _account_name(root, market)
        if job['account'] != account:
            raise ValueError('campaign_join_request_conflict')
        for _ in range(VERIFY_ROUNDS if bounded else 1):
            pending = store.unresolved(job_id)
            active = []
            for row in pending:
                if row['followup_stopped']:
                    continue
                if row['verify_attempts'] >= VERIFY_ROUNDS or (row['verify_deadline'] is not None
                                                               and row['verify_deadline'] <= clock()):
                    store.set_item(job_id, row['campaign_id'], followup_stopped=1)
                else:
                    active.append(row)
            if not active:
                break
            # One shared bound, never 300 seconds per item. Old survivors keep their earlier bound.
            deadline = min([entered + VERIFY_SECONDS] + [row['verify_deadline'] for row in active
                                                         if row['verify_deadline'] is not None])
            with store.db:
                for row in active:
                    store.db.execute("UPDATE campaign_join_item SET verify_deadline=?,"
                                     "verify_attempts=verify_attempts+1,updated=? WHERE job_id=? AND campaign_id=?",
                                     (deadline, clock(), job_id, row['campaign_id']))
            error = ''
            joined = None
            try:
                with verification_timeout(deadline - clock()):
                    if transport is None:
                        from lib.global_source_transport import opportunity_reader
                        context = opportunity_reader({'platformWrites': 0}, market=market,
                            account_name=job['account'], extra_read_endpoints={(CAMPAIGNS, 'GET')})
                    else:
                        context = transport
                    with context as live:
                        joinable, joined = read_campaigns(live)
                    if clock() >= deadline:
                        raise VerificationDeadline()
                    joinable_ids = {str(row['campaign_id']) for row in joinable}
            except VerificationDeadline:
                error = 'campaign_verification_budget_exhausted'
            except Exception as failure:
                # Record a read failure, not negative membership evidence or a fake empty list.
                error = ('campaign_verification_account_failed' if _account_failure(failure)
                         else 'campaign_verification_read_failed:' + type(failure).__name__)
            if error:
                for row in active:
                    store.set_item(job_id, row['campaign_id'], verify_error=error)
            else:
                for row in active:
                    cid = row['campaign_id']
                    if cid in joined or row['joined_campaign_id'] in joined:
                        store.set_item(job_id, cid, state='joined', reason='回查确认已加入', verify_error='')
                        settled += 1
                        joined_settled += 1
                    elif cid in joinable_ids:
                        store.set_item(job_id, cid, state='skipped',
                                       reason='not_joined_platform_lists_it_joinable', verify_error='')
                        settled += 1
                store.set_job(job_id, joined_count=len(joined))
            for row in store.unresolved(job_id):
                if row['verify_attempts'] >= VERIFY_ROUNDS or (row['verify_deadline'] is not None and
                    (clock() >= row['verify_deadline'] or error == 'campaign_verification_budget_exhausted')):
                    store.set_item(job_id, row['campaign_id'], followup_stopped=1)
            if error == 'campaign_verification_budget_exhausted':
                break
        store.set_job(job_id, state='needs_verification' if store.unresolved(job_id) else 'completed', error='')
        return status(root, market=market, clock=clock, job_id=job_id) | {
            'settled': settled, 'joinedSettled': joined_settled}
    finally:
        store.close()


def _join_path():
    from lib.global_source_transport import CAMPAIGN_JOIN
    return CAMPAIGN_JOIN


def safe_error(error):
    code = str(error)
    return code if re.fullmatch(r'(?:campaign|taplink|account|live)_[a-z0-9_]{1,100}', code) else type(error).__name__


def _account_failure(value):
    # Known authentication codes and explicit local account gates stop further POSTs.
    if getattr(value, 'code', None) in (16201010, 10000) or getattr(value, 'http_status', None) in (401, 403):
        return True
    return any(token in str(value).lower() for token in
               ('auth', 'login', 'maintenance', 'account_', 'verification', 'captcha', 'endpoint_not_allowed'))


def _settle(outcome):
    """Classify a write's answer. Only an explicit, non-verification rejection is 'skipped'."""
    http = getattr(outcome, 'http_status', None)
    code = getattr(outcome, 'code', None)
    ambiguous = getattr(outcome, 'ambiguous', False)
    turing = getattr(outcome, 'has_turing', False)
    if http == 200 and isinstance(code, int) and code == 0 and not ambiguous and not turing:
        return 'joined', 'platform_accepted'
    if http == 200 and isinstance(code, int) and code != 0 and not ambiguous and not turing and not _account_failure(outcome):
        return 'skipped', f'platform_rejected:{code}'
    return 'result_unknown', 'result_unknown'


def _submit_with_verification(live, campaign_id, payload):
    """Submit once; a real challenge may solve and replay this exact frozen request once."""
    if str((payload or {}).get('campaign_id') or '') != str(campaign_id):
        raise ValueError('campaign_join_scope_invalid')
    def request():
        return live._xhr(method='POST', path=_join_path(), params=live._params(),
                         payload=payload, write=True)
    outcome = request()
    if not (getattr(outcome, 'http_status', None) == 200 and
            getattr(outcome, 'code', None) == 10000 and
            getattr(outcome, 'has_turing', False) is True and
            getattr(outcome, 'ambiguous', False) is False):
        return outcome
    header = getattr(live, '_verification_header', '')
    if not header:
        raise ValueError('campaign_join_verification_failed')
    live._solve_verification(header)
    live.verification_successes = getattr(live, 'verification_successes', 0) + 1
    replay = request()
    if getattr(replay, 'has_turing', False):
        raise ValueError('campaign_join_verification_failed')
    return replay


def _returned_campaign(outcome):
    """The platform may answer with a child campaign id; remembering it is what makes verify work."""
    payload = getattr(outcome, 'payload', None) or {}
    returned = str(((payload.get('data') or {}) if isinstance(payload, dict) else {}).get('campaign_id') or '')
    return returned if re.fullmatch(r'[0-9]{10,32}', returned) else None


def default_email(root):
    """页面输入框的默认联系邮箱。读不到或不像邮箱就是空串，绝不猜一个出来。"""
    try:
        value = json.loads((Path(root) / 'config/campaign-join.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return ''
    email = str((value or {}).get('email') or '').strip() if isinstance(value, dict) else ''
    return email if re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email) else ''


def status(root=None, *, market='it', clock=time.time, job_id=None):
    """Read-only summary for the page: what is eligible, what was joined, what needs a recheck."""
    root = Path(root or root_of())
    if not db_path(root, market).exists():
        return {'available': False, 'reason': 'campaign_join_not_started'}
    store = Store(root, market=market, clock=clock, readonly=True)
    try:
        job = store.job(job_id)
        if not job:
            return {'available': False, 'reason': 'campaign_join_not_started'}
        return {'available': True, 'jobId': job['id'], 'state': job['state'], 'error': job['error'],
                'account': job['account'], 'joinedCount': job['joined_count'],
                # 回传上次用过的邮箱（没有就用配置里的默认值）：页面预填它，
                # "一键加入"才不需要每次手打邮箱。以页面上提交过的为准。
                'email': job['contact_email'] or default_email(root),
                'counts': store.states(job['id']),
                'unresolved': [row['campaign_id'] for row in store.unresolved(job['id'])],
                'activeVerification': [row['campaign_id'] for row in store.unresolved(job['id'])
                    if not row.get('followup_stopped') and int(row.get('verify_attempts') or 0) < VERIFY_ROUNDS
                    and (row.get('verify_deadline') is None or row['verify_deadline'] > clock())],
                'stoppedUnknown': [row['campaign_id'] for row in store.unresolved(job['id'])
                    if row.get('followup_stopped') or int(row.get('verify_attempts') or 0) >= VERIFY_ROUNDS
                    or (row.get('verify_deadline') is not None and row['verify_deadline'] <= clock())],
                'items': store.items(job['id']), 'platformWrites': 0}
    finally:
        store.close()
