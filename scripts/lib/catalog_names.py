"""Catalogue product short names: what is ready, what is missing, and how to fill the gap.

A TapLink's card name is built from a market-localized product short name.  Italy keeps its legacy
title fallback for compatibility; every other market fails closed until a localized name exists.
That makes name generation part of link preparation, not an optional extra.
"""
import json
import sqlite3
from contextlib import closing
from pathlib import Path

def _recover(root, store, rows, market='it'):
    """Store names an earlier run already generated but never wrote.

    A batch can produce a perfectly good response and still leave no stored name -- the process may
    have died between the two, or the response may have been rejected by a validator that has since
    been corrected. The response is already paid for, so it is reused rather than requested again.
    """
    import sys
    if str(Path(root) / 'scripts') not in sys.path:
        sys.path.insert(0, str(Path(root) / 'scripts'))
    from lib.cycle_materials import checked_names,market_locale,name_key
    from lib.second_cycle import encoded
    recovered = []
    for row in rows:
        key = name_key(row,market)
        prior = store.db.execute('SELECT job_id FROM cycle_name_reservation WHERE id=?', (key,)).fetchone()
        if not prior:
            continue
        job = store.db.execute("SELECT state,inputs,response FROM cycle_name_job "
                               "WHERE id=? AND state IN ('ready','response_saved')", (prior[0],)).fetchone()
        if not job or not job['response']:
            continue
        try:
            originals = json.loads(job['inputs'])
            parsed = checked_names(json.loads(json.loads(job['response'])['content']), originals,market)
        except Exception:  # noqa: BLE001 - an unusable stored response just means "generate it"
            continue
        for index, original in enumerate(originals):
            if name_key(original,market) == key and str(index) in parsed:
                store.db.execute('INSERT INTO cycle_product_name VALUES(?,?,?,?,?,?) '
                                 'ON CONFLICT(id) DO UPDATE SET pid=excluded.pid,locale=excluded.locale,'
                                 'source_title=excluded.source_title,payload=excluded.payload,job_id=excluded.job_id',
                                 (key, row['pid'], market_locale(market), row['title'],
                                  encoded(parsed[str(index)]), prior[0]))
                recovered.append(row['pid'])
                break
    if recovered:
        store.db.commit()
    return recovered


def _forget(store, batch, market='it'):
    """Drop one batch's reservation and job so it can be asked again.

    Only ever used after ``names_invalid``: that means a response arrived and was judged unusable,
    so the outcome is known and a fresh request cannot double-spend an unknown one. Transport
    failures are never retried this way.
    """
    from lib.cycle_materials import name_key
    from lib.second_cycle import digest
    jid = 'names-' + digest([name_key(row,market) for row in batch])
    with store.tx():
        for row in batch:
            store.db.execute('DELETE FROM cycle_name_reservation WHERE id=?', (name_key(row,market),))
        store.db.execute('DELETE FROM cycle_name_job WHERE id=?', (jid,))


def _attempt_split(store, materials, batch, call, tries):
    """Last resort: ask for the products one at a time.

    One product whose title cannot yield a short enough phrase otherwise fails the whole batch with
    it. Individually, only that product fails and the rest are salvaged.
    """
    saved, failed = [], []
    for row in batch:
        result, code = _attempt(store, materials, [row], call, tries)
        if result is None:
            failed.append({'pid': row['pid'], 'code': code})
        else:
            saved.append(result)
    return saved, failed


def _attempt(store, materials, batch, call, tries):
    """Run one batch, re-asking only when a response arrived and failed validation."""
    from lib.second_cycle import CycleError
    for _ in range(tries):
        try:
            return materials.prepare_names(batch, call), None
        except CycleError as error:
            if str(error) != 'names_invalid':
                return None, str(error)
            _forget(store,batch,materials.market)
    return None, 'names_invalid'


BATCH_LIMIT = 5  # the provider-side batch cap enforced by Materials.prepare_names
MAX_CONSECUTIVE_FAILURES = 3  # stop only when failures look systemic, not incidental
INVALID_RESPONSE_TRIES = 3  # a response that fails validation is known-bad, so re-asking is safe


def cached_pids(root,market='it'):
    """Products that already have a usable AI short name, keyed by product id.

    The cache is keyed by product, not by title: the same product arrives with different titles
    from Kalodata and from the platform listing, and a title-keyed cache missed both.
    """
    db = Path(root) / 'var/second-cycle.sqlite'
    if not db.exists():
        return set()
    found = set()
    try:
        with closing(sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
            conn.execute('BEGIN')
            from lib.cycle_materials import market_locale
            for pid, source_title, payload in conn.execute('SELECT pid,source_title,payload FROM cycle_product_name WHERE locale=?',(market_locale(market),)):
                try:
                    data=json.loads(payload);value=data.get('shortNameIt') if market=='it' else data.get('shortName')
                except (TypeError, ValueError):
                    continue
                if source_title==pid or str(pid) in str(value):
                    continue
                if isinstance(value, str) and 1 <= len(value) <= 60:
                    found.add(str(pid))
    except sqlite3.Error:
        return set()
    return found


def scope(root,market='it'):
    """Products whose card name will be built from a short name, with their listing titles."""
    db = Path(root) / 'var/catalog-links.sqlite'
    if not db.exists():
        return {}
    try:
        with closing(sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
            conn.execute('BEGIN')
            # Only the products still waiting for a link need a name: the others already have a
            # card whose name is frozen, and regenerating would not change it.
            columns={row[1] for row in conn.execute("PRAGMA table_info(catalog_prepare_item)")}
            # Older IT-only ledgers and focused test fixtures predate ``run_id``.  They are still
            # valid IT evidence, but must never be projected into another market.
            if 'run_id' not in columns:
                rows=(conn.execute("SELECT pid,listing FROM catalog_prepare_item WHERE state='missing'").fetchall()
                      if market=='it' else [])
            else:
                states=('missing',) if market=='it' else ('pending','reading','read_incomplete','missing','prepared')
                placeholders=','.join('?' for _ in states)
                rows = conn.execute("SELECT i.pid,i.listing,i.title FROM catalog_prepare_item i "
                                    "JOIN catalog_prepare_run r ON r.id=i.run_id "
                                    f"WHERE i.state IN ({placeholders}) AND r.market=?",(*states,market)).fetchall()
    except sqlite3.Error:
        return {}
    out = {}
    for row in rows:
        pid,listing=row[:2];seeded_title=row[2] if len(row)>2 else None
        try:
            title = (json.loads(listing) or {}).get('title')
        except (TypeError, ValueError):
            title = None
        out[str(pid)] = str(title or seeded_title or pid)
    return out


def gap(root,market='it'):
    """What link preparation would name correctly today, and what it would truncate."""
    products = scope(root,market)
    ready = cached_pids(root,market)
    missing = {pid: title for pid, title in products.items() if pid not in ready}
    ordered = sorted(missing.items(), key=lambda row: row[0])
    invalid=sorted(pid for pid,title in products.items() if not title.strip() or title==pid)
    return {'scope': len(products), 'ready': len(products) - len(missing), 'missing': len(missing),
            'invalidSourceTitles':len(invalid),
            'missingSample': [{'pid': pid, 'title': title[:60]} for pid, title in ordered[:5]]}


def discard_invalid_pid_names(root,market='it'):
    """Remove only model rows generated from a bare PID instead of a product title."""
    from lib.cycle_materials import market_locale
    from lib.second_cycle import CycleStore
    with CycleStore(Path(root)/'var/second-cycle.sqlite') as store:
        rows=store.db.execute("SELECT id,job_id FROM cycle_product_name WHERE locale=? AND source_title=pid AND length(pid)=19 AND pid NOT GLOB '*[^0-9]*'",
                              (market_locale(market),)).fetchall()
        jobs=sorted({row['job_id'] for row in rows if row['job_id']})
        with store.tx():
            removed=store.db.execute("DELETE FROM cycle_product_name WHERE locale=? AND source_title=pid AND length(pid)=19 AND pid NOT GLOB '*[^0-9]*'",
                                     (market_locale(market),)).rowcount
            for job in jobs:
                store.db.execute('DELETE FROM cycle_name_reservation WHERE job_id=?',(job,))
                store.db.execute('DELETE FROM cycle_name_job WHERE id=?',(job,))
    return {'removed':removed,'jobsRemoved':len(jobs),'market':market}


def prepare(root, limit=25, *, market='it', call=None, on_progress=None, all_missing=False):
    """Generate short names for products that lack one, reusing the existing provider path.

    A model call costs money and is never retried here: a failure is reported so the operator can
    decide. Names are validated by ``cycle_materials.checked_names`` before anything is stored.
    """
    root = Path(root)
    ceiling=50_000 if all_missing else 5_000
    if type(limit) is not int or not 1 <= limit <= ceiling:
        raise ValueError('catalog_names_limit_invalid')
    products = scope(root,market)
    ready = cached_pids(root,market)
    every = [{'pid': pid, 'title': title, 'offerKey': 'catalog:' + pid}
             for pid, title in sorted(products.items()) if pid not in ready]
    # ``all_missing`` covers whatever is left, so the operator never has to guess a number.
    missing = every if all_missing else every[:limit]
    limit = len(missing) if all_missing else limit
    report = {'requested': len(missing), 'prepared': 0, 'modelCalls': 0, 'errors': [], 'cost': None,
              'total': len(missing)}
    if on_progress:
        on_progress(report)
    if not missing:
        report['stopped'] = 'nothing_missing'
        return report
    import sys
    if str(root / 'scripts') not in sys.path:
        sys.path.insert(0, str(root / 'scripts'))
    from lib.cycle_materials import Materials, name_key
    # A product can be held by a reservation from an earlier, unfinished batch. Skip those instead
    # of letting one stale row stop the whole run, and report them so they are not silently lost.
    import sqlite3 as _sql
    blocked = set()
    try:
        with closing(_sql.connect((root / 'var/second-cycle.sqlite').resolve().as_uri() + '?mode=ro',
                                  uri=True)) as conn:
            conn.execute('BEGIN')
            for row in missing:
                key = name_key(row,market)
                prior = conn.execute('SELECT job_id FROM cycle_name_reservation WHERE id=?', (key,)).fetchone()
                job = conn.execute('SELECT state FROM cycle_name_job WHERE id=?', (prior[0],)).fetchone() if prior else None
                if prior and (job is None or job[0] not in ('ready', 'response_saved')):
                    blocked.add(row['pid'])
    except _sql.Error:
        blocked = set()
    if blocked:
        report['blocked'] = sorted(blocked)
        missing = [row for row in missing if row['pid'] not in blocked]
        report['requested'] = len(missing)
        if not missing:
            report['stopped'] = 'all_blocked_by_prior_reservation'
            return report
    from lib.cycle_materials import Materials
    from lib.second_cycle import CycleError, CycleStore
    if call is None:
        from lib.draft_provider import call_model
        from lib.model_service import guard
        call = guard(root, call_model)
    consecutive = 0
    with CycleStore(root / 'var/second-cycle.sqlite') as store:
        materials = Materials(store,market)
        # Recovery always covers everything still missing: a stored-but-unwritten response is free
        # regardless of how small the requested batch is, and leaving it is pure waste.
        reused = _recover(root,store,every,market)
        if reused:
            report['recovered'] = len(reused)
            report['reusedPids'] = sorted(reused)
            done = set(reused)
            every = [row for row in every if row['pid'] not in done]
            missing = every if all_missing else every[:limit]
            limit = len(missing) if all_missing else limit
            report['total'] = report['total'] - len(reused) if report.get('total') else len(missing)
            report['requested'] = len(missing)
            if on_progress:
                on_progress(report)
        for start in range(0, len(missing), BATCH_LIMIT):
            batch = missing[start:start + BATCH_LIMIT]
            try:
                result, code = _attempt(store, materials, batch, call, INVALID_RESPONSE_TRIES)
            except Exception as error:  # noqa: BLE001 - one bad batch must not lose the report
                result, code = None, type(error).__name__
            if result is None:
                if code == 'names_invalid' and len(batch) > 1:
                    # Salvage the batch by asking per product; the count of failures becomes the
                    # number of genuinely impossible titles instead of the number of batches.
                    saved, failed = _attempt_split(store, materials, batch, call, INVALID_RESPONSE_TRIES)
                    for item in saved:
                        report['modelCalls'] += item.get('modelCalls', 0)
                        report['prepared'] += item.get('prepared', 0)
                        report['cost'] = item.get('cost') or report['cost']
                    report['errors'].extend(failed)
                    consecutive = 0 if saved else consecutive + 1
                    if on_progress:
                        on_progress(report)
                    if consecutive >= MAX_CONSECUTIVE_FAILURES:
                        report['stopped'] = 'repeated_batch_failure'
                        break
                    continue
                report['errors'].append({'pids': [row['pid'] for row in batch], 'code': code})
                consecutive += 1
                if consecutive >= MAX_CONSECUTIVE_FAILURES:
                    report['stopped'] = 'repeated_batch_failure'
                    break
                continue
            consecutive = 0
            report['modelCalls'] += result.get('modelCalls', 0)
            report['prepared'] += result.get('prepared', 0)
            report['cost'] = result.get('cost') or report['cost']
            if on_progress:
                on_progress(report)
    return report
