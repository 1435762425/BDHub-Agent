"""Screen-at-collection: the operator-controlled full-managed intake thresholds.

Three numbers decide which collected products may enter the selected pool: minimum cumulative
sales, minimum rating, and the minimum gap between the total and public commission rate. They
are business knobs, so they live in ``config/catalog-screen.json`` and are editable from the
货盘 page. Three properties keep that safe:

* screening is a separate step inside the collection job. Collection is the expensive platform
  read; screening is free local arithmetic over listings that are already stored, so changing a
  threshold re-screens stored data instead of re-reading the platform.
* screening only ever *admits*. It never removes a product that is already in the selected pool
  (decision 2026-09-14: the thresholds gate entry, not membership), and it never writes to the
  platform itself -- it produces the candidate list that the verified selection path consumes.
* every decision is recorded per product together with the metric values behind it, so a
  threshold change is auditable instead of silent.

``unrated`` is recorded as a fact, never guessed. An unrated product reports rating ``0``, whose
platform semantics are still unverified, so it is admitted only while ``allowUnrated`` is on and
is always counted separately on the funnel.
"""
import json
import re
import sqlite3
import time
from contextlib import closing
from decimal import Decimal, InvalidOperation
from pathlib import Path

from lib.second_cycle import digest, encoded

DEFAULTS = {'version': 'catalog-screen-v1', 'minSales': 300, 'minRating': 4.0,
            'minCommissionGapPoints': 2.0, 'allowUnrated': True}

# Commission fields are basis points on the platform (100 bp = 1 percentage point).
BP_PER_POINT = 100
# Screen reason codes are generic on purpose: the threshold is read from the config, so a code
# never goes stale when the operator moves a number.
REASONS = ('sales_missing', 'sales_below_min', 'rating_unrated', 'rating_below_min',
           'commission_missing', 'commission_gap_below_min')


def root_of(module_file=__file__):
    return Path(module_file).resolve().parents[2]


def config_path(root):
    return Path(root) / 'config/catalog-screen.json'


def _bounded_int(value, low, high, code):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(code)
    return value


def _bounded_number(value, low, high, code):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(code)
    number = float(value)
    if number != number or not low <= number <= high:
        raise ValueError(code)
    return number


def validate(raw):
    """Normalise and check one config; raises ValueError with a stable code."""
    if not isinstance(raw, dict):
        raise ValueError('catalog_screen_invalid')
    allow = raw.get('allowUnrated', DEFAULTS['allowUnrated'])
    if type(allow) is not bool:
        raise ValueError('catalog_screen_unrated_invalid')
    return {'version': str(raw.get('version') or DEFAULTS['version']),
            'minSales': _bounded_int(raw.get('minSales', DEFAULTS['minSales']), 0, 1_000_000,
                                     'catalog_screen_sales_invalid'),
            'minRating': _bounded_number(raw.get('minRating', DEFAULTS['minRating']), 0, 5,
                                         'catalog_screen_rating_invalid'),
            'minCommissionGapPoints': _bounded_number(
                raw.get('minCommissionGapPoints', DEFAULTS['minCommissionGapPoints']), 0, 100,
                'catalog_screen_gap_invalid'),
            'allowUnrated': allow}


_CACHE = {}


def load(root=None):
    """Read the active config, cached per file identity so tight screening loops stay cheap."""
    if root is None:
        root = root_of()
    path = config_path(root)
    try:
        stat = path.stat()
        stamp = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        stamp = None
    key = str(path)
    cached = _CACHE.get(key)
    if cached and cached[0] == stamp:
        return cached[1]
    config = validate(json.loads(path.read_text(encoding='utf-8')) if stamp else dict(DEFAULTS))
    _CACHE[key] = (stamp, config)
    return config


def save(root, raw):
    """Validate first, then write atomically: a rejected threshold must not touch the file."""
    config = validate(raw)
    path = config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    tmp.replace(path)
    _CACHE.pop(str(path), None)
    return config


def fingerprint(config):
    return digest(config)


def sales(value):
    """Platform sales strings are locale formatted: ``2.349 已售`` and ``1,2K 已售``."""
    if not isinstance(value, str):
        return None
    if re.fullmatch(r'\d+(?:\.\d{3})* 已售', value):
        return int(value.replace('.', '').replace(' 已售', ''))
    if re.fullmatch(r'\d+,\d+K 已售', value):
        return int(Decimal(value.replace('K 已售', '').replace(',', '.')) * 1000)
    return None


def decimal(value):
    try:
        if value is None or isinstance(value, bool):
            return None
        number = Decimal(str(value))
        return number if number.is_finite() else None
    except InvalidOperation:
        return None


def evaluate(product, config=None):
    """Apply the thresholds to one stored listing. No network, no writes, no guesses."""
    config = config or load()
    units = sales(product.get('sales'))
    rating = decimal(product.get('product_rating'))
    total = decimal(product.get('commission_rate'))
    public = decimal(product.get('open_collab_rate'))
    min_rating = Decimal(str(config['minRating']))
    min_gap = Decimal(str(config['minCommissionGapPoints'])) * BP_PER_POINT
    reasons = []
    if units is None:
        reasons.append('sales_missing')
    elif units < config['minSales']:
        reasons.append('sales_below_min')
    unrated = rating is None or rating == 0
    if unrated:
        if not config['allowUnrated']:
            reasons.append('rating_unrated')
    elif not min_rating <= rating <= 5:
        reasons.append('rating_below_min')
    if total is None or public is None:
        reasons.append('commission_missing')
    elif not 0 <= public <= 10000 or not 0 <= total <= 10000 or total - public < min_gap:
        reasons.append('commission_gap_below_min')
    return {'eligible': not reasons, 'reasons': reasons, 'units': units,
            'rating': None if rating is None else float(rating),
            'totalBp': None if total is None else int(total),
            'publicBp': None if public is None else int(public),
            'gapPoints': None if total is None or public is None else float(total - public) / BP_PER_POINT,
            'unrated': unrated,
            # The thresholds this decision was made under, so a stored row explains itself.
            'thresholds': {k: config[k] for k in ('minSales', 'minRating', 'minCommissionGapPoints', 'allowUnrated')}}


SCHEMA = '''
CREATE TABLE IF NOT EXISTS global_source_screen_run(
  run_id TEXT PRIMARY KEY, source_run TEXT NOT NULL, config TEXT NOT NULL, fingerprint TEXT NOT NULL,
  state TEXT NOT NULL, counts TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS global_source_screen(
  run_id TEXT NOT NULL, pid TEXT NOT NULL, state TEXT NOT NULL, reasons TEXT NOT NULL,
  units INTEGER, rating REAL, total_bp INTEGER, public_bp INTEGER, gap_points REAL,
  unrated INTEGER NOT NULL, thresholds TEXT NOT NULL, config_fingerprint TEXT NOT NULL,
  observed REAL NOT NULL, PRIMARY KEY(run_id,pid));
CREATE INDEX IF NOT EXISTS global_screen_state ON global_source_screen(run_id,state);
'''


class Screen:
    """Per-run screening ledger over one collection database."""

    def __init__(self, path, *, clock=time.time):
        self.path = Path(path)
        self.clock = clock
        self.db = sqlite3.connect(self.path, timeout=15)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()

    def record(self, run_id, source_run, config, rows):
        """Replace one run's screening result atomically; the input is already evaluated."""
        counts = {}
        reasons = {}
        for row in rows:
            counts[row['state']] = counts.get(row['state'], 0) + 1
            for reason in row['reasons']:
                reasons[reason] = reasons.get(reason, 0) + 1
        stamp = self.clock()
        with self.db:
            self.db.execute('DELETE FROM global_source_screen WHERE run_id=?', (run_id,))
            self.db.executemany(
                'INSERT INTO global_source_screen(run_id,pid,state,reasons,units,rating,total_bp,'
                'public_bp,gap_points,unrated,thresholds,config_fingerprint,observed) '
                'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                [(run_id, row['pid'], row['state'], encoded(row['reasons']), row['units'],
                  row['rating'], row['totalBp'], row['publicBp'], row['gapPoints'],
                  int(row['unrated']), encoded(row['thresholds']), row['config_fingerprint'], stamp)
                 for row in rows])
            self.db.execute(
                'INSERT INTO global_source_screen_run(run_id,source_run,config,fingerprint,state,'
                'counts,created,updated) VALUES(?,?,?,?,?,?,?,?) '
                'ON CONFLICT(run_id) DO UPDATE SET source_run=excluded.source_run,'
                'config=excluded.config,fingerprint=excluded.fingerprint,state=excluded.state,'
                'counts=excluded.counts,updated=excluded.updated',
                (run_id, source_run, encoded(config), fingerprint(config), 'recorded',
                 encoded({'states': counts, 'reasons': reasons}), stamp, stamp))
        return self.detail(run_id)

    def run_row(self, run_id):
        return self.db.execute('SELECT * FROM global_source_screen_run WHERE run_id=?', (run_id,)).fetchone()

    def latest_run(self, source_run=None):
        if source_run is None:
            row = self.db.execute('SELECT run_id FROM global_source_screen_run ORDER BY created DESC LIMIT 1').fetchone()
        else:
            row = self.db.execute('SELECT run_id FROM global_source_screen_run WHERE source_run=? ORDER BY created DESC LIMIT 1', (source_run,)).fetchone()
        return row[0] if row else None

    def state_counts(self, run_id):
        return {r[0]: r[1] for r in self.db.execute('SELECT state,count(*) FROM global_source_screen WHERE run_id=? GROUP BY state', (run_id,))}

    def reason_counts(self, run_id):
        counts = {}
        for (raw,) in self.db.execute("SELECT reasons FROM global_source_screen WHERE run_id=? AND state='rejected'", (run_id,)):
            for reason in json.loads(raw):
                counts[reason] = counts.get(reason, 0) + 1
        return counts

    def detail(self, run_id):
        row = self.run_row(run_id)
        if not row:
            return None
        return {'runId': run_id, 'sourceRun': row['source_run'], 'config': json.loads(row['config']),
                'fingerprint': row['fingerprint'], 'state': row['state'],
                'counts': self.state_counts(run_id), 'reasons': self.reason_counts(run_id),
                'updatedAt': row['updated']}

    def eligible_pids(self, run_id):
        return [r[0] for r in self.db.execute("SELECT pid FROM global_source_screen WHERE run_id=? AND state='eligible' ORDER BY pid", (run_id,))]

    def selection_split(self, run_id, settled=frozenset()):
        """Split the eligible set by whether the pool actually holds it now.

        The listing flag is the collection snapshot and goes stale the moment a product is
        selected, so a product confirmed into the pool by any batch counts as selected even when
        the old snapshot still says otherwise.
        """
        row = self.run_row(run_id)
        if not row:
            return {}
        split = {'selected': 0, 'unselected': 0, 'unknown': 0}
        sql = ("SELECT s.pid, json_extract(p.payload,'$.fs_is_selected') AS sel "
               "FROM global_source_screen s JOIN global_source_product p "
               "ON p.run_id=? AND p.pid=s.pid WHERE s.run_id=? AND s.state='eligible'")
        for pid, sel in self.db.execute(sql, (row['source_run'], run_id)):
            if sel == 1 or pid in settled:
                split['selected'] += 1
            elif sel == 0:
                split['unselected'] += 1
            else:
                split['unknown'] += 1
        return split

    def unselected_eligible(self, run_id):
        """Eligible products the platform listing still reports as not selected, oldest pid first."""
        row = self.run_row(run_id)
        if not row:
            return []
        sql = ("SELECT s.pid FROM global_source_screen s JOIN global_source_product p "
               "ON p.run_id=? AND p.pid=s.pid WHERE s.run_id=? AND s.state='eligible' "
               "AND json_extract(p.payload,'$.fs_is_selected')=0 ORDER BY s.pid")
        return [r[0] for r in self.db.execute(sql, (row['source_run'], run_id))]


def funnel(path, run_id, settled=frozenset()):
    """The whole 采集→筛出→入池 funnel for one screening record, as numbers only."""
    ledger = Screen(path)
    try:
        detail = ledger.detail(run_id)
        if not detail:
            return None
        row = ledger.run_row(run_id)
        collected = ledger.db.execute('SELECT count(*) FROM global_source_product WHERE run_id=?', (row['source_run'],)).fetchone()[0]
        split = ledger.selection_split(run_id, settled)
        unrated = ledger.db.execute("SELECT count(*),sum(state='eligible') FROM global_source_screen WHERE run_id=? AND unrated=1", (run_id,)).fetchone()
        return detail | {'collected': collected,
                         'eligible': detail['counts'].get('eligible', 0),
                         'rejected': detail['counts'].get('rejected', 0),
                         'selectedEligible': split.get('selected', 0),
                         'unselectedEligible': split.get('unselected', 0),
                         'unknownSelectedFlag': split.get('unknown', 0),
                         # Unrated is a fact, not a verdict: shown so the operator can see how much
                         # of the pool was admitted on the "no rating yet" allowance alone.
                         'unrated': unrated[0] or 0,
                         'unratedEligible': unrated[1] or 0}
    finally:
        ledger.close()


def preview(path, source_run, config, *, active=None, clock=time.time):
    """What a threshold change would do to the stored listing, without writing anything.

    This is the operator's safety net: they see how many products the new numbers admit, how
    many of those are not in the pool yet, and which single reason moves the most.
    """
    active = active or load()
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        rows = [json.loads(r[0]) for r in conn.execute('SELECT payload FROM global_source_product WHERE run_id=? ORDER BY pid', (source_run,))]
    reasons = {}
    eligible = unselected = unrated_eligible = 0
    added = removed = 0
    for product in rows:
        old = evaluate(product, active)
        new = evaluate(product, config)
        if new['eligible']:
            eligible += 1
            unrated_eligible += bool(new['unrated'])
            if product.get('fs_is_selected') is False:
                unselected += 1
        for reason in new['reasons']:
            reasons[reason] = reasons.get(reason, 0) + 1
        if new['eligible'] and not old['eligible']:
            added += 1
        elif old['eligible'] and not new['eligible']:
            removed += 1
    return {'collected': len(rows), 'eligible': eligible, 'rejected': len(rows) - eligible,
            'unselectedEligible': unselected, 'unratedEligible': unrated_eligible,
            'addedVersusActive': added, 'removedVersusActive': removed, 'reasons': reasons,
            'config': config, 'fingerprint': fingerprint(config), 'activeFingerprint': fingerprint(active)}


def screen_run_id(source_run, config):
    """One screening record per (collection run, threshold set).

    Keying on both means a threshold change never overwrites the previous decision, so the page
    can show what the change would add instead of silently rewriting history.
    """
    return 'screen-' + digest([source_run, fingerprint(config)])[:24]


def screen_source(path, source_run, *, root=None, config=None, clock=time.time):
    """Screen every stored product of one collection run and record the outcome.

    Returns the recorded detail. Nothing here touches the platform; the caller decides what to
    do with the eligible-and-unselected remainder.
    """
    config = config or load(root)
    stamp = fingerprint(config)
    run_id = screen_run_id(source_run, config)
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        rows = [json.loads(r[0]) for r in conn.execute('SELECT payload FROM global_source_product WHERE run_id=? ORDER BY pid', (source_run,))]
    evaluated = []
    for product in rows:
        result = evaluate(product, config)
        evaluated.append({'pid': product['product_id'],
                          'state': 'eligible' if result['eligible'] else 'rejected',
                          'reasons': result['reasons'], 'units': result['units'],
                          'rating': result['rating'], 'totalBp': result['totalBp'],
                          'publicBp': result['publicBp'], 'gapPoints': result['gapPoints'],
                          'unrated': result['unrated'], 'thresholds': result['thresholds'],
                          'config_fingerprint': stamp})
    ledger = Screen(path, clock=clock)
    try:
        return ledger.record(run_id, source_run, config, evaluated)
    finally:
        ledger.close()
