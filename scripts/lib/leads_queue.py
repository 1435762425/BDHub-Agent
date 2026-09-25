"""The PID -> creator-lead query queue.

Which products we ask Kalodata about, and in what order, is a queue rather than a poll. Polling
the whole catalogue every day would spend a hard daily platform budget (``DETAIL.ACCESS_TIMES``)
on products that cannot be out of date yet. Instead:

* **first-time queue** -- products with a valid TapLink that have never been queried, highest
  cumulative sales first. Products without a link stay out: the whole point of the funnel is to
  prepare the link first, so a lead found for an unlinked product cannot be used yet.
* **refresh queue** -- already queried products whose record has reached its refresh age. It only
  becomes due, it is never scanned.

Scope therefore grows by itself as link preparation finishes. First-time work is served first.

The missing piece this module adds is time: the source tables record the *data* window but never
when we asked, so a refresh age could not be computed. ``queried_at`` is that clock. Existing rows
were backfilled to the day the queries actually ran, as confirmed by the operator.
"""
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from lib.global_screen import sales

DEFAULTS = {'version': 'leads-queue-a50-v3', 'refreshDays': 7, 'leadsPerPid': 50, 'windowDays': 14,
            'batchSize': 200, 'maxAttempts': 3}

SCHEMA = '''
CREATE TABLE IF NOT EXISTS leads_query(
  pid TEXT PRIMARY KEY, queried_at REAL NOT NULL, state TEXT NOT NULL,
  window_end TEXT, leads INTEGER, note TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS leads_query_due ON leads_query(queried_at);
-- A failed query must never look like a successful one: it gets no queried_at, only an attempt
-- count, so the product stays at the head of the queue instead of being locked out for a cycle.
CREATE TABLE IF NOT EXISTS leads_attempt(
  pid TEXT PRIMARY KEY, attempts INTEGER NOT NULL DEFAULT 0, last_code TEXT, last_at REAL NOT NULL);
-- Page receipts, written before anything is imported so a crash cannot lose or duplicate a page.
CREATE TABLE IF NOT EXISTS leads_page(
  pid TEXT NOT NULL, cursor TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(pid,cursor));
-- Page receipts belong to a particular market, PID and 14-day query window.  The original
-- leads_page table is retained as the last *completed* query for older read-side consumers.
CREATE TABLE IF NOT EXISTS leads_page_scope(
  query_id TEXT NOT NULL, pid TEXT NOT NULL, cursor TEXT NOT NULL, payload TEXT NOT NULL,
  PRIMARY KEY(query_id,cursor));
CREATE INDEX IF NOT EXISTS leads_page_scope_pid ON leads_page_scope(pid);
CREATE TABLE IF NOT EXISTS leads_page_legacy_history(
  pid TEXT NOT NULL, window_end TEXT NOT NULL, cursor TEXT NOT NULL, payload TEXT NOT NULL,
  PRIMARY KEY(pid,window_end,cursor));
'''


def root_of(module_file=__file__):
    return Path(module_file).resolve().parents[2]


def config_path(root):
    return Path(root) / 'config/leads-queue.json'


def validate(raw):
    if not isinstance(raw, dict):
        raise ValueError('leads_queue_invalid')
    def bounded(key, low, high):
        value = raw.get(key, DEFAULTS[key])
        if type(value) is not int or not low <= value <= high:
            raise ValueError('leads_queue_invalid')
        return value
    return {'version': str(raw.get('version') or DEFAULTS['version']),
            'refreshDays': bounded('refreshDays', 1, 90),
            'leadsPerPid': bounded('leadsPerPid', 1, 50),
            'windowDays': bounded('windowDays', 1, 30),
            'batchSize': bounded('batchSize', 1, 5000),
            'maxAttempts': bounded('maxAttempts', 1, 20)}


def load(root):
    path = config_path(root)
    raw = json.loads(path.read_text(encoding='utf-8')) if path.exists() else dict(DEFAULTS)
    return validate(raw)


def save_config(root, raw):
    """Validate first, then write atomically: a rejected refresh age must not touch the file."""
    config = validate(raw)
    path = config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)
    return config


class Ledger:
    """Per-PID record of when we last asked Kalodata and what came back."""

    def __init__(self, root, market='it'):
        self.market=market
        self.path = Path(root) / ('var/kalodata-leads.sqlite' if market=='it' else f'var/kalodata-leads-{market}.sqlite')
        # The ledger owns its home: a workspace that has never queried anything still reads.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=15)
        self.db.row_factory = sqlite3.Row
        tables={row[0] for row in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'leads_page' in tables and not {'leads_page_scope','leads_page_legacy_history'}<=tables:
            self.db.close()
            raise ValueError('leads_receipt_scope_migration_required')
        self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()

    def record(self, pid, *, state='completed', window_end=None, leads=None, note='', at=None):
        stamp = time.time() if at is None else at
        with self.db:
            self.db.execute('INSERT INTO leads_query(pid,queried_at,state,window_end,leads,note) '
                            'VALUES(?,?,?,?,?,?) ON CONFLICT(pid) DO UPDATE SET '
                            'queried_at=excluded.queried_at,state=excluded.state,'
                            'window_end=excluded.window_end,leads=excluded.leads,note=excluded.note',
                            (pid, stamp, state, window_end, leads, note))
        return stamp

    def backfill(self, pids, *, at, note='回填'):
        """Give already-queried products a clock. Only fills rows that have none."""
        with self.db:
            self.db.executemany('INSERT OR IGNORE INTO leads_query(pid,queried_at,state,note) '
                                'VALUES(?,?,?,?)', [(pid, at, 'backfilled', note) for pid in pids])
        return self.db.execute('SELECT count(*) FROM leads_query').fetchone()[0]

    def succeeded(self, pid, *, window_end=None, leads=None, note='', at=None):
        """Only a query that actually returned may start the refresh clock."""
        stamp = self.record(pid, state='completed', window_end=window_end, leads=leads, note=note, at=at)
        with self.db:
            self.db.execute('DELETE FROM leads_attempt WHERE pid=?', (pid,))
        return stamp

    def succeeded_with_pages(self, pid, query_id, *, window_end, leads, at):
        """Publish the complete page set and query clock together in this ledger."""
        pages=list(self.db.execute('SELECT cursor,payload FROM leads_page_scope WHERE query_id=? AND pid=?',
                                   (query_id,pid)))
        if not pages:raise ValueError('leads_receipts_missing')
        with self.db:
            prior=self.db.execute('SELECT window_end FROM leads_query WHERE pid=?',(pid,)).fetchone()
            if prior and prior[0]:
                self.db.execute('INSERT OR IGNORE INTO leads_page_legacy_history '
                                'SELECT pid,?,cursor,payload FROM leads_page WHERE pid=?',
                                (prior[0],pid))
            self.db.execute('DELETE FROM leads_page WHERE pid=?',(pid,))
            self.db.executemany('INSERT INTO leads_page(pid,cursor,payload) VALUES(?,?,?)',
                                [(pid,cursor,payload) for cursor,payload in pages])
            self.db.execute('INSERT INTO leads_query(pid,queried_at,state,window_end,leads,note) '
                            'VALUES(?,?,?,?,?,?) ON CONFLICT(pid) DO UPDATE SET '
                            'queried_at=excluded.queried_at,state=excluded.state,'
                            'window_end=excluded.window_end,leads=excluded.leads,note=excluded.note',
                            (pid,at,'completed',window_end,leads,'队列查询'))
            self.db.execute('DELETE FROM leads_attempt WHERE pid=?',(pid,))
        return at

    def failed(self, pid, code, *, at=None):
        """Record the failure without giving the product a refresh clock."""
        stamp = time.time() if at is None else at
        with self.db:
            self.db.execute('INSERT INTO leads_attempt(pid,attempts,last_code,last_at) VALUES(?,1,?,?) '
                            'ON CONFLICT(pid) DO UPDATE SET attempts=attempts+1,last_code=excluded.last_code,'
                            'last_at=excluded.last_at', (pid, str(code)[:60], stamp))
        return stamp

    def attempts(self):
        return {row['pid']: dict(row) for row in self.db.execute('SELECT * FROM leads_attempt')}

    def known(self):
        return {row['pid']: dict(row) for row in self.db.execute('SELECT * FROM leads_query')}

    def counts(self):
        return {row[0]: row[1] for row in self.db.execute('SELECT state,count(*) FROM leads_query GROUP BY state')}


QUERIED_STATES = {'completed'}
JOB_STORES = (('var/batch-tasks.sqlite', 'batch_source_job'), ('var/second-cycle.sqlite', 'source_job'))


def queried_from_jobs(root,market='it'):
    """PIDs a Kalodata request actually completed for.

    Only completed jobs count. A ``queued`` or ``awaiting_identity`` job never reached the
    platform, and a quota-blocked job reached it but came back with nothing, so all three must
    stay in the first-time queue. Treating "a job row exists" as "we already asked" would silently
    lock products out of the queue for a whole refresh cycle.
    """
    found = set()
    for name, table in JOB_STORES:
        path = Path(root) / name
        if not path.exists():
            continue
        try:
            with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
                conn.execute('BEGIN')
                tables={row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if table=='source_job' and 'plan' in tables:
                    rows=conn.execute('SELECT DISTINCT j.pid,j.state FROM source_job j JOIN plan p ON p.id=j.plan_id WHERE p.market=?',(market,)).fetchall()
                elif table=='batch_source_job' and 'batch_task' in tables and 'spec' in {row[1] for row in conn.execute('PRAGMA table_info(batch_task)')}:
                    rows=conn.execute("SELECT DISTINCT j.pid,j.state FROM batch_source_job j JOIN batch_task t ON t.id=j.task_id WHERE json_extract(t.spec,'$.market')=?",(market,)).fetchall()
                elif market=='it':
                    rows=conn.execute(f'SELECT DISTINCT pid,state FROM {table}').fetchall()
                else:rows=[]
        except sqlite3.Error:
            continue
        found |= {pid for pid, state in rows if state in QUERIED_STATES}
    return found


def sync(root, *, market='it', at=None, now=None):
    """Reconcile the clock with the job stores, in both directions.

    Adds a clock for newly completed products and removes rows this module itself wrote for
    products that were never actually queried. Real recorded times are never touched.
    """
    root = Path(root)
    stamp = time.time() if at is None else at
    queried = queried_from_jobs(root,market)
    ledger = Ledger(root,market)
    try:
        with ledger.db:
            # A row we wrote ourselves is an estimate of the query day; re-state it on every sync.
            ledger.db.execute("UPDATE leads_query SET queried_at=? WHERE state='backfilled'", (stamp,))
            ledger.db.executemany('INSERT OR IGNORE INTO leads_query(pid,queried_at,state,note) '
                                  'VALUES(?,?,?,?)',
                                  [(pid, stamp, 'backfilled', '回填：按用户指示记为查询当日')
                                   for pid in sorted(queried)])
        stale = [row[0] for row in ledger.db.execute("SELECT pid FROM leads_query WHERE state='backfilled'")]
        removed = [pid for pid in stale if pid not in queried]
        if removed:
            with ledger.db:
                ledger.db.executemany("DELETE FROM leads_query WHERE pid=? AND state='backfilled'",
                                      [(pid,) for pid in removed])
        return {'queried': len(queried),
                'ledgerRows': ledger.db.execute('SELECT count(*) FROM leads_query').fetchone()[0],
                'removed': len(removed), 'removedSample': sorted(removed)[:5]}
    finally:
        ledger.close()


def _read(db, sql, args=()):
    path = Path(db)
    if not path.exists():
        return []
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        conn.execute('BEGIN')
        return list(conn.execute(sql, args))


def eligible_products(root,market='it'):
    """Found creators for **两条渠道**的合格商品并集，keyed by pid, with cumulative sales for ordering.

    队列是跨渠道的：渠道只挂在商品与链接上，找达人是同一件事。以前这里**只读全托的筛分账本**，
    于是非全托的商品即使已经备好链接，也进不了查询队列（实测 444 个被挡在外面）。
    """
    db = Path(root) / ('var/global-source.sqlite' if market=='it' else f'var/global-source-{market}.sqlite')
    runs = _read(db, 'SELECT run_id,source_run FROM global_source_screen_run ORDER BY updated DESC LIMIT 1')
    products = {}
    if runs:
        run_id, source_run = runs[0]
        eligible = {row[0] for row in _read(db, "SELECT pid FROM global_source_screen WHERE run_id=? AND state='eligible'", (run_id,))}
        for pid, payload in _read(db, 'SELECT pid,payload FROM global_source_product WHERE run_id=?', (source_run,)):
            if pid in eligible:
                listing = json.loads(payload)
                products[pid] = {'units': sales(listing.get('sales')) or 0,
                                 'title': str(listing.get('title') or '')[:80],
                                 'channel': 'selected'}
    from lib.fullmanaged_candidates import candidate_rows
    for row in candidate_rows(root,market):
        facts=row['evidence']['product']
        products.setdefault(row['pid'],{'units':sales(facts.get('sales')) or 0,'title':str(facts.get('title') or '')[:80],'channel':'selected'})
    return products | _campaign_products(root, products,market)


def _campaign_products(root, already,market='it'):
    """非全托已入池商品；同一个 PID 若全托也有，保留全托那条（有真实销量数据）。"""
    try:
        from lib.campaign_screen import pool_products
        pool = pool_products(Path(root), source='campaign',market=market)
    except (OSError, ValueError, sqlite3.Error):
        return {}
    out = {}
    for pid, row in pool.items():
        if pid in already:
            continue
        # 销量缺失时按 0 参与排序，但**如实标记没有数据**，页面可以说明。
        out[pid] = {'units': row.get('units') if row.get('units') is not None else 0,
                    'title': str(row.get('title') or '')[:80], 'channel': 'campaign',
                    'unitsKnown': row.get('units') is not None}
    return out


def linked_products(root,market='it'):
    db = Path(root) / 'var/catalog-links.sqlite'
    # Only the canonical standard binding is material.  Preparation states and historical reuse
    # rows are evidence, not sendable links.
    from lib.link_naming import load as load_naming
    version=load_naming(root,market)['version']
    return {row[0]: row[1] for row in _read(
        db, "SELECT pid,state FROM catalog_current_binding WHERE market=? AND state='active' "
            "AND commission_rule_version='commission-1-to-2-v1' AND naming_rule_version=?",(market,version))}


def build(root, *, market='it', config=None, now=None, ledger=None):
    """Describe the queue without touching the platform or enrolling anything."""
    root = Path(root)
    config = config or load(root)
    now = time.time() if now is None else now
    own = ledger is None
    ledger = ledger or Ledger(root,market)
    try:
        products = eligible_products(root,market)
        linked = linked_products(root,market)
        known = ledger.known()
        attempts = ledger.attempts()
        ledger_counts = ledger.counts()
    finally:
        if own:
            ledger.close()
    scope = sorted(set(products) & set(linked))
    age = config['refreshDays'] * 86400
    first, due, waiting, unknown, stuck = [], [], [], [], []
    limit = config['maxAttempts']
    for pid in scope:
        record = known.get(pid)
        if record is None:
            # A product that keeps failing leaves the first-time queue rather than burning the
            # daily budget forever; it is reported so it can be looked at instead of disappearing.
            if attempts.get(pid, {}).get('attempts', 0) >= limit:
                stuck.append(pid)
            else:
                first.append(pid)
        elif record['queried_at'] + age <= now:
            due.append(pid)
        else:
            waiting.append(pid)
    first.sort(key=lambda pid: (-products[pid]['units'], pid))
    due.sort(key=lambda pid: (known[pid]['queried_at'],-products[pid]['units'],pid))
    waiting.sort(key=lambda pid: (known[pid]['queried_at'], pid))
    # A queried product that is no longer in scope is reported, never silently dropped.
    unknown = sorted(set(known) - set(scope))
    # 渠道拆分：队列是**跨渠道**的，页面上要说清"两条都在里面"，否则看不出全托/非全托各占多少。
    by_channel = {'selected': 0, 'campaign': 0}
    units_unknown = 0
    for pid in scope:
        row = products.get(pid) or {}
        by_channel[str(row.get('channel') or 'selected')] = by_channel.get(str(row.get('channel') or 'selected'), 0) + 1
        if row.get('channel') == 'campaign' and row.get('unitsKnown') is False:
            units_unknown += 1
    return {'config': config, 'now': now,
            'eligible': len(products), 'linked': len(linked), 'scope': len(scope),
            'byChannel': by_channel, 'unitsUnknown': units_unknown,
            'inScope': scope,
            'firstTime': [{'pid': pid, 'units': products[pid]['units'], 'title': products[pid]['title']} for pid in first],
            'due': [{'pid': pid, 'queriedAt': known[pid]['queried_at'],
                     'dueAt': known[pid]['queried_at'] + age, 'leads': known[pid]['leads'],
                     'units':products[pid]['units'],'title': products[pid]['title']} for pid in due],
            'waiting': len(waiting), 'unknownScope': len(unknown),
            'stuck': [{'pid': pid, 'attempts': attempts[pid]['attempts'],
                       'code': attempts[pid]['last_code'],
                       'title': products[pid]['title']} for pid in sorted(stuck)],
            'ledger': ledger_counts,
            # One page is enough for most products; measured today at 1.16 pages per product.
            'ordersPerProduct': round(len(first) + len(due), 0),
            'refreshDays': config['refreshDays']}


def queue_items(built):
    """The work that is actually due, in service order: never-queried first, then expired refreshes.

    Products whose refresh has not come round yet are deliberately absent. That is the whole point
    of the age: a product queried three days ago cannot have gone stale.
    """
    items = [{'pid': row['pid'], 'kind': 'first', 'units': row['units'], 'title': row['title'],
              'queriedAt': None, 'dueAt': None}
             for row in built['firstTime']]
    items += [{'pid': row['pid'], 'kind': 'due', 'units': row['units'], 'title': row['title'],
               'queriedAt': row['queriedAt'], 'dueAt': row['dueAt']}
              for row in built['due']]
    return items


def plan(root=None, *, market='it', config=None, now=None, batch_size=None, ledger=None):
    """The next batch: the top ``batchSize`` items of the due queue and nothing else.

    The size is a ceiling, not a target. When fewer items are due than the ceiling, the batch is
    simply smaller -- it is never padded with products whose refresh has not come round, because
    re-reading a product queried days ago buys no new information and spends the daily budget.
    """
    root = Path(root or root_of())
    config = config or load(root)
    built = build(root,market=market, config=config, now=now, ledger=ledger)
    size = config['batchSize'] if batch_size is None else batch_size
    if type(size) is not int or not 1 <= size <= 5000:
        raise ValueError('leads_queue_batch_invalid')
    queue = queue_items(built)
    taken = queue[:size]
    return {'batchSize': size, 'dueQueue': len(queue), 'taken': len(taken),
            'first': sum(1 for row in taken if row['kind'] == 'first'),
            'refresh': sum(1 for row in taken if row['kind'] == 'due'),
            # Zero means there was nothing left to do; it does not mean the batch was filled.
            'shortfall': max(0, size - len(taken)),
            'padded': False,
            'notYetDue': built['waiting'],
            'items': taken, 'built': built}


def run_state_path(root,market='it'):
    return Path(root) / ('var/leads-run.json' if market=='it' else f'var/leads-run-{market}.json')


def run_state(root, *, market='it', clock=time.time):
    """Whether a batch is running right now, plus the last report.

    A run file that says "running" long after it started is treated as finished: a killed process
    must not leave the page claiming work is in flight forever.
    """
    path = run_state_path(root,market)
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(state, dict):
        return None
    started = state.get('startedAt')
    fresh = isinstance(started, (int, float)) and clock() - started < 6 * 3600
    return state | {'running': bool(state.get('running')) and fresh}


def status(root=None, *, market='it', config=None, now=None, batch_size=None):
    root = Path(root or root_of())
    built = build(root,market=market, config=config, now=now)
    queue = queue_items(built)
    size = built['config']['batchSize'] if batch_size is None else batch_size
    taken = queue[:size]
    rolling=None
    if (root/'var/second-cycle.sqlite').exists():
        from lib.rolling_leads import status as rolling_status
        rolling=rolling_status(root,market,at=now)
    return {'market':market,'config': built['config'], 'refreshDays': built['refreshDays'],'rolling':rolling,
            'eligible': built['eligible'], 'linked': built['linked'], 'scope': built['scope'],
            # 渠道拆分与"有多少商品没有销量数据"必须转发：页面靠它们说清"两条渠道都在队列里"，
            # 漏转发时页面拿不到字段（症状就是渠道那格永远是空的）。
            'byChannel': built.get('byChannel'), 'unitsUnknown': built.get('unitsUnknown'),
            'firstTime': len(built['firstTime']), 'due': len(built['due']),
            'waiting': built['waiting'], 'unknownScope': built['unknownScope'],
            'stuck': len(built['stuck']),
            'nextFirstTime': built['firstTime'][:8], 'nextDue': built['due'][:8],
            'batchSize': size, 'dueQueue': len(queue), 'taken': len(taken),
            'batchFirst': sum(1 for row in taken if row['kind'] == 'first'),
            'batchRefresh': sum(1 for row in taken if row['kind'] == 'due'),
            'shortfall': max(0, size - len(taken)), 'padded': False,
            'run': ({'running':bool(rolling.get('activeRun')),'startedAt':(rolling.get('activeRun') or {}).get('started_at')} if rolling and rolling.get('control') else run_state(root,market=market))}
