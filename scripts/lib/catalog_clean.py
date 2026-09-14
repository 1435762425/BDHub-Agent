"""TapLink health classification and gated deletion (production path).

The health rules and the delete protocol are reused verbatim from the legacy implementation
(``bdhub.send.taplink.cleanup``: ``product_health`` / ``classify_list`` / delete endpoint).
What lives here is the durable ledger, the join with the account-wide inventory and the
guards: a card may only be deleted when the whole list is invalid, it has never carried a
send, and it is re-checked immediately before the delete.
"""
import json
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

from lib.second_cycle import digest, encoded

DELETE = '/api/v1/affiliate/partner/campaign/product_list/delete'
LIST_INVENTORY = '/api/v1/affiliate/partner/campaign/product_list/list'
MEMBERS = '/api/v1/affiliate/partner/campaign/product_list/products'
CLEAN_READ_EXTRA = {(LIST_INVENTORY, 'GET'), (MEMBERS, 'GET')}
HEALTH_STATES = {'valid', 'mixed', 'invalid', 'unknown'}
DECISIONS = {'keep', 'delete_candidate', 'review'}

SCHEMA = '''
CREATE TABLE IF NOT EXISTS catalog_clean_run(
 id TEXT PRIMARY KEY,market TEXT NOT NULL,account TEXT NOT NULL,created REAL NOT NULL,scope TEXT);
CREATE TABLE IF NOT EXISTS catalog_clean_item(
 run_id TEXT NOT NULL,list_id TEXT NOT NULL,pid TEXT,campaign_id TEXT,list_name TEXT,
 health TEXT NOT NULL,reason TEXT,decision TEXT NOT NULL,used INTEGER NOT NULL DEFAULT 0,
 evidence TEXT,observed REAL NOT NULL,PRIMARY KEY(run_id,list_id));
CREATE INDEX IF NOT EXISTS catalog_clean_item_decision ON catalog_clean_item(decision);
CREATE TABLE IF NOT EXISTS catalog_clean_intent(
 id TEXT PRIMARY KEY,run_id TEXT NOT NULL,list_id TEXT NOT NULL,state TEXT NOT NULL,
 payload TEXT,receipt TEXT,readback TEXT,created REAL NOT NULL,updated REAL NOT NULL);
'''


def _legacy():
    """The legacy health rules, imported lazily from the vendored copy."""
    root = Path(__file__).resolve().parents[2]
    for candidate in (root / 'vendor', root.parent / '01-BDSystem-V2'):
        if candidate.is_dir() and str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))
    from bdhub.send.taplink.cleanup import UNAVAILABLE, classify_list, product_health
    return product_health, classify_list, UNAVAILABLE


def used_list_ids(root):
    """list_ids that already carried a real send, taken from the frozen delivery snapshots.

    A used link stays even when it later turns invalid: it may still settle commission, and the
    confirmed policy keeps used or unknown-usage links by default.
    """
    db = Path(root) / 'var/second-cycle.sqlite'
    if not db.exists():
        return set()
    with closing(sqlite3.connect(db.as_uri() + '?mode=ro', uri=True)) as conn:
        conn.execute('BEGIN')
        rows = conn.execute("SELECT snapshot FROM cycle_delivery WHERE snapshot LIKE '%listId%'").fetchall()
    out = set()
    for (snapshot,) in rows:
        try:
            data = json.loads(snapshot)
        except Exception:
            continue
        list_id = str(((data.get('card') or {}).get('listId')) or '')
        if list_id.isdigit():
            out.add(list_id)
    return out


def classify_card(members, campaigns=None):
    """The legacy rule applied to one card's stored members -> valid/mixed/invalid/unknown."""
    _, classify_list, _ = _legacy()
    if not members:
        return {'state': 'invalid', 'reason': '空商品列表', 'products': [], 'eligible': True}
    row = {'list_id': '', 'campaign_id': '', 'source': '2', 'product_total': len(members)}
    products = []
    for m in members:
        products.append({'product_id': m.get('pid'), 'product_status': m.get('product_status') or 0,
                         'unavailable_type': m.get('unavailable_type'), 'stock': m.get('stock'),
                         'campaign_id': m.get('member_campaign_id'),
                         'is_under_governed': (m.get('governed') == 1) if m.get('governed') is not None else None})
    return classify_list(row, products, campaigns=campaigns)


def decide(health, used):
    """Confirmed policy: only a fully invalid, never-used card becomes a delete candidate."""
    if health == 'invalid':
        if used:
            return 'keep', '已使用过的链接不自动删除'
        return 'delete_candidate', '整条列表已失效'
    if health == 'mixed':
        return 'keep', '混合有效，保留整条列表'
    if health == 'unknown':
        return 'review', '状态未确认，暂不删除'
    return 'keep', '有效链接保留'


class CatalogClean:
    """Durable cleaning ledger over catalog-links.sqlite."""

    def __init__(self, root):
        self.root = Path(root)
        self.db = sqlite3.connect(self.root / 'var/catalog-links.sqlite', timeout=15)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()

    # ---- run and items ----------------------------------------------------------
    def run_id(self, market='it', account='acc9', day=None):
        return 'catalog-clean-' + digest([market, account, day or time.strftime('%Y-%m-%d')])[:24]

    def open_run(self, market='it', account='acc9', scope=None):
        rid = self.run_id(market, account)
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO catalog_clean_run VALUES(?,?,?,?,?)',
                            (rid, market, account, time.time(), encoded(scope or {'market': market, 'account': account})))
        return rid

    def save_item(self, run_id, list_id, *, pid=None, campaign_id=None, list_name=None,
                  health='unknown', reason=None, decision='review', used=False, evidence=None, now=None):
        if health not in HEALTH_STATES or decision not in DECISIONS:
            raise ValueError('catalog_clean_state_invalid')
        now = now if now is not None else time.time()
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO catalog_clean_item VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                            (str(run_id), str(list_id), pid, campaign_id, list_name, health, reason,
                             decision, 1 if used else 0, encoded(evidence) if evidence is not None else None, now))

    def items(self, run_id, decision=None):
        sql = 'SELECT * FROM catalog_clean_item WHERE run_id=?'
        args = [run_id]
        if decision:
            sql += ' AND decision=?'
            args.append(decision)
        return [dict(r) for r in self.db.execute(sql + ' ORDER BY list_id', args)]

    def summary(self, run_id):
        counts = {r[0]: r[1] for r in self.db.execute('SELECT decision,count(*) FROM catalog_clean_item WHERE run_id=? GROUP BY decision', (run_id,))}
        health = {r[0]: r[1] for r in self.db.execute('SELECT health,count(*) FROM catalog_clean_item WHERE run_id=? GROUP BY health', (run_id,))}
        intents = {r[0]: r[1] for r in self.db.execute('SELECT state,count(*) FROM catalog_clean_intent WHERE run_id=? GROUP BY state', (run_id,))}
        reasons = {r[0]: r[1] for r in self.db.execute('SELECT reason,count(*) FROM catalog_clean_item WHERE run_id=? AND decision<>"keep" GROUP BY reason', (run_id,))}
        return {'runId': run_id, 'total': sum(counts.values()), 'decisions': counts, 'health': health,
                'intents': intents, 'reasons': reasons}

    # ---- planning ---------------------------------------------------------------
    def plan(self, run_id, inventory, campaigns=None, now=None):
        """Classify every card of the account-wide inventory and record the decision.

        Only cards whose whole list is invalid become delete candidates; used links and any
        unknown state stay, exactly as the confirmed policy requires.
        """
        now = now if now is not None else time.time()
        used = used_list_ids(self.root)
        states = {}
        for row in inventory.lists():
            list_id = str(row['list_id'])
            members = [dict(m) for m in self.db.execute('SELECT * FROM catalog_tap_member WHERE list_id=?', (list_id,))]
            verdict = classify_card(members, campaigns)
            health = verdict['state']
            was_used = list_id in used
            decision, why = decide(health, was_used)
            first = members[0] if members else {}
            self.save_item(run_id, list_id, pid=first.get('pid'), campaign_id=first.get('member_campaign_id'),
                           list_name=row.get('name'), health=health, reason=verdict.get('reason') or why,
                           decision=decision, used=was_used,
                           evidence={'products': verdict.get('products'), 'listReason': verdict.get('reason')}, now=now)
            states[decision] = states.get(decision, 0) + 1
        return states

    # ---- delete intents ---------------------------------------------------------
    def freeze_delete(self, run_id, list_id, now=None):
        """One frozen delete intent per list; idempotent, never a second intent for a list."""
        now = now if now is not None else time.time()
        row = self.db.execute('SELECT health,decision,used FROM catalog_clean_item WHERE run_id=? AND list_id=?', (run_id, str(list_id))).fetchone()
        if not row:
            raise ValueError('catalog_clean_item_missing')
        if row['decision'] != 'delete_candidate' or row['used']:
            raise ValueError('catalog_clean_not_eligible')
        intent_id = 'catalog-clean-' + digest([str(list_id), 'delete'])[:24]
        old = self.db.execute('SELECT * FROM catalog_clean_intent WHERE id=?', (intent_id,)).fetchone()
        if old:
            return dict(old)
        with self.db:
            self.db.execute('INSERT INTO catalog_clean_intent VALUES(?,?,?,?,?,NULL,NULL,?,?)',
                            (intent_id, run_id, str(list_id), 'prepared', encoded({'list_id': str(list_id)}), now, now))
        return dict(self.db.execute('SELECT * FROM catalog_clean_intent WHERE id=?', (intent_id,)).fetchone())

    def intent(self, intent_id):
        row = self.db.execute('SELECT * FROM catalog_clean_intent WHERE id=?', (intent_id,)).fetchone()
        if not row:
            raise ValueError('catalog_clean_intent_missing')
        return dict(row)

    def mark(self, intent_id, state, *, receipt=None, readback=None, now=None):
        now = now if now is not None else time.time()
        with self.db:
            self.db.execute('UPDATE catalog_clean_intent SET state=?,receipt=COALESCE(?,receipt),readback=COALESCE(?,readback),updated=? WHERE id=?',
                            (state, encoded(receipt) if receipt is not None else None,
                             encoded(readback) if readback is not None else None, now, intent_id))

    def pending_deletes(self, run_id):
        # Receipt-saved intents are exactly the ones awaiting a delete readback.
        return [dict(r) for r in self.db.execute("SELECT * FROM catalog_clean_intent WHERE run_id=? AND state IN ('prepared','submitted','receipt_saved','unknown') ORDER BY list_id", (run_id,))]
