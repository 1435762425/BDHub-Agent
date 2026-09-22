"""加入 Seller Campaign：预览 → 显式确认 → 提交 → 回查。

**这是本系统里第二个平台写入动作**（第一个是建 TapLink），所以纪律照抄旧版已经被验证的那套：

* **写入前先落盘**：条目先记 `writing` + `write_attempted`，再发 POST。任何"提过了但不知道结果"的条目
  只能靠**回查**结算，绝不重新提交（`campaign_write_requires_verification`）。
* **原意图回查**：`verify` 重新读一次已加入列表，把 `writing`/`result_unknown` 结算成 `joined`。
  平台加入后可能返回**子活动 id**，所以结算时以"原活动 id 或返回的子活动 id 出现在已加入列表里"为准。
* **一次一个活动一次写入**：冻结请求由传输层校验（`opportunity_campaign_joiner`），
  写不匹配的请求会被拒；同一活动一次会话最多写一次。
* **明确拒绝 ≠ 结果未知**：平台返回明确的非 0 code（且不是验证/超时/鉴权类）才记 `skipped`；
  其余一律 `result_unknown`，停下来等人回查。
* **没有合格活动的商品不硬挑**：加入同样只针对**预览里 eligible 的那批**。

**账号**：加入活动是货盘侧的写入，走货盘账号（ACC9，与目录读取、建链同一个）；
能力门禁沿用旧版定义（IT 的 `campaign_join` 在旧库里是 enabled）。
"""
import json
import re
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lib.cycle_catalog import CAMPAIGNS

# 预览/提交/回查是**同一个 job 的三个动作**：共用一个 id，提交时才看得到预览判定过的条目。
DEFAULT_JOB_ID = 'campaign-join'
# 已确认口径：Campaign 剩余有效期 > 45 天（旧版加入用的是"2 个月"，本次统一到 45 天）。
REMAINING_DAYS = 45
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

    def __init__(self, root, *, market='it', clock=time.time):
        self.root = Path(root)
        self.market = market
        self.path = db_path(root, market)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.db = sqlite3.connect(self.path, timeout=15)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

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
        stamp = self.clock()
        with self.db:
            self.db.execute('DELETE FROM campaign_join_item WHERE job_id=?', (job_id,))
            self.db.executemany(
                'INSERT INTO campaign_join_item(job_id,campaign_id,name,state,reason,write_attempted,'
                'joined_campaign_id,updated) VALUES(?,?,?,?,?,?,?,?)',
                [(job_id, str(row['campaign_id']), str(row.get('name') or ''), row['state'],
                  str(row.get('reason') or ''), int(row.get('write_attempted') or 0),
                  row.get('joined_campaign_id'), stamp) for row in rows])

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
    rows, page = [], 1
    while True:
        batch, total = _page(transport, params, page)
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
            if old and old['state'] in ('writing', 'result_unknown') and row['state'] != 'joined':
                # **未结算**的提交必须原样保留：这才是"结果未知绝不重试"。
                # 平台自己说"已加入"就结算；已经结算过的（joined/skipped）按平台这次的回答刷新——
                # 否则一条结算成"平台说没加入"的记录会永远卡在那里，再点也进不去。
                row = {**row, 'state': old['state'], 'reason': old['reason'],
                       'write_attempted': 1, 'joined_campaign_id': old['joined_campaign_id']}
            merged.append(row)
        for cid, old in previous.items():
            if cid not in fresh and old['write_attempted']:
                merged.append(old)   # 平台不再列它：记录留着（通常就是已经加入）
        store.replace_items(job['id'], merged)
        store.set_job(job['id'], state='previewed', error='', joined_count=len(joined))
        # 返回**与 status 同一个封装**，另加三个预览专属字段：页面两处读的是同一份状态，
        # 少了 available 这一层，预览结果会被当成"不可用"直接丢掉（点了按钮像没反应）。
        summary = status(root, market=market, clock=clock, job_id=job['id'])
        return summary | {'campaigns': len(rows), 'joined': len(joined),
                          'otherCategories': other,
                          'eligible': sum(1 for row in rows if row['state'] == 'eligible')}
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


def apply(root, *, market='it', campaign_ids, email, confirm=False, canary=False, transport=None,
          clock=time.time, job_id=None):
    """Join the named campaigns. **A platform write**: refuses without ``confirm=True``.

    Only campaigns that the current preview called ``eligible`` are accepted, each is written at most
    once, and an unresolved result ends the run instead of being retried.
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
                if row['state'] == 'joined':
                    continue
                if cid not in joinable:
                    store.set_item(job['id'], cid, state='skipped', reason='campaign_not_joinable_now')
                    continue
                if row['write_attempted']:
                    store.set_item(job['id'], cid, state='result_unknown',
                                   reason='campaign_write_requires_verification')
                    break
                # Persist the attempt BEFORE the write: a crash here must not look like "never tried".
                store.set_item(job['id'], cid, state='writing', write_attempted=1, reason='')
                try:
                    outcome = _submit_with_verification(live, cid, payloads[cid])
                except Exception as error:                      # noqa: BLE001 - unknown, never a retry
                    # 记**消息**，不只记类型名：只留一个 "TapLinkError" 时根本查不出原因
                    # （实际遇到过：真正的原因是 taplink_endpoint_not_allowed，写根本没发出去）。
                    store.set_item(job['id'], cid, state='result_unknown',
                                   reason=f'write_uncertain:{type(error).__name__}:{str(error)[:120]}')
                    break
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
                    break
        counts = store.states(job['id'])
        state = 'needs_verification' if counts.get('writing') or counts.get('result_unknown') else 'completed'
        # 写完再读一次"已加入"总数：拿写入**之前**那个数当结论，页面上就永远少算刚加入的这一批
        # （实测：18 个都加入成功、平台 61，页面却还显示 43）。读不到就退回写入前那个数，不编一个。
        joined_total = len(joined)
        try:
            _, joined_after = read_campaigns(live)
            joined_total = len(joined_after)
        except Exception:                                   # noqa: BLE001 - 读数失败不改变写入结论
            pass
        store.set_job(job['id'], state=state, joined_count=joined_total)
        return {'jobId': job['id'], 'state': state, 'counts': counts,
                'items': store.items(job['id']), 'platformWrites': report.get('platformWrites', 0)}
    except Exception as error:
        job = store.job(job_id)
        if job:
            store.set_job(job['id'], state='blocked', error=str(error)[:120])
        raise
    finally:
        store.close()


def join_all(root, *, market='it', email, confirm=False, transport=None, clock=time.time, job_id=None):
    """一键加入：**先重新预览一遍**，再把当前全部合格的活动一次提交。

    为什么先预览：不拿页面上的旧列表去写平台——那样会把已经不可加入的活动也提交一遍，
    甚至拿过期的判断去写。预览是只读的，每次点击都重算一遍，代价很小。

    安全纪律与 ``apply`` 完全一致（只接受预览判定为合格的活动、每个活动一次会话只写一次、
    结果未知立刻停并留给 ``verify`` 回查），因为这里只是把「预览」和「提交」串成一次调用，
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
    writes, attempted = 0, []
    for start in range(0, len(eligible), 100):
        batch = eligible[start:start + 100]
        applied = apply(root, market=market, campaign_ids=batch, email=email, confirm=True, transport=transport,
                        clock=clock, job_id=job_id)
        writes += int(applied.get('platformWrites') or 0)
        attempted.extend(batch)
        if applied.get('state') == 'needs_verification':
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
        'platformWrites': writes}


def verify(root, *, market='it', transport=None, clock=time.time, job_id=None):
    """Read-only settlement: re-read the joined list and settle what was written but unknown."""
    root = Path(root or root_of())
    job_id = _job_id(market, job_id)
    report = {'platformWrites': 0}
    store = Store(root, market=market, clock=clock)
    try:
        job = store.job(job_id)
        if not job:
            raise ValueError('campaign_join_job_missing')
        pending = store.unresolved(job['id'])
        # 没有待结算项时**也要**读一次平台：这个动作同时负责把"已加入总数"对齐到平台，
        # 早退会让写完之后的页面永远停留在写入前那个数（实测 61 被显示成 43）。
        # 返回统一的 status 封装：只回一个 {jobId,settled,counts} 的裸对象会让页面
        # 因为缺 available 而当成"还没预览过"。
        if transport is None:
            from lib.global_source_transport import opportunity_reader
            context = opportunity_reader(report, market=market, extra_read_endpoints={(CAMPAIGNS, 'GET')})
        else:
            context = transport
        with context as live:
            joinable, joined = read_campaigns(live)
        joinable_ids = {str(row.get('campaign_id')) for row in joinable if row.get('campaign_id')}
        settled = 0
        for row in pending:
            candidate = row['joined_campaign_id'] or row['campaign_id']
            if candidate in joined or row['campaign_id'] in joined:
                store.set_item(job['id'], row['campaign_id'], state='joined', reason='回查确认已加入')
                settled += 1
            elif row['campaign_id'] in joinable_ids:
                # 平台把它列在"可加入"里、又不在"已加入"里——**这就是平台自己说"没加入"**。
                # 两个列表是相互独立的回答，所以这个结算有据可依，不是猜的；原因码把依据写清楚，
                # 让人一眼看出结论是"平台说没加入"，而不是"我们放弃确认了"。
                store.set_item(job['id'], row['campaign_id'], state='skipped',
                               reason='not_joined_platform_lists_it_joinable')
                settled += 1
        counts = store.states(job['id'])
        state = 'needs_verification' if counts.get('writing') or counts.get('result_unknown') else 'completed'
        # 已加入总数以**平台这次读回来的**为准。曾经写成"本 job 里 joined 的条目数"，
        # 于是一次回查就把平台的 43 改成了 0——页面上是一个凭空的假数字。
        store.set_job(job['id'], state=state, joined_count=len(joined))
        return status(root, market=market, clock=clock, job_id=job['id']) | {'settled': settled}
    finally:
        store.close()


def _join_path():
    from lib.global_source_transport import CAMPAIGN_JOIN
    return CAMPAIGN_JOIN


def _settle(outcome):
    """Classify a write's answer. Only an explicit, non-verification rejection is 'skipped'."""
    http = getattr(outcome, 'http_status', None)
    code = getattr(outcome, 'code', None)
    ambiguous = getattr(outcome, 'ambiguous', False)
    turing = getattr(outcome, 'has_turing', False)
    if http == 200 and isinstance(code, int) and code == 0 and not ambiguous and not turing:
        return 'joined', 'platform_accepted'
    if http == 200 and isinstance(code, int) and code != 0 and not ambiguous and not turing:
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
    store = Store(root, market=market, clock=clock)
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
                'items': store.items(job['id']), 'platformWrites': 0}
    finally:
        store.close()
