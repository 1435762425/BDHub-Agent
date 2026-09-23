"""Bounded OLDER-message backfill with atomic evidence and resumable native cursors.

Callers explicitly apply registered second_cycle migrations. Construction never initializes a database.
Backfill neither queues replies nor resolves existing gaps or send intentions.
"""
from __future__ import annotations

import hashlib
from lib.cycle_inbox import KINDS
from lib.second_cycle import CycleError, digest, encoded
from lib.market_registry import load_registry

from lib.schema_migrations import SECOND_CYCLE_INBOX_HISTORY

SCHEMA = SECOND_CYCLE_INBOX_HISTORY.sql

_REQUIRED = ('inbox_history_checkpoint', 'inbox_history_page', 'inbox_checkpoint',
             'inbox_event', 'inbox_content_head', 'inbox_content_version')


def _cursor(value):
    if not isinstance(value, str) or not value.isascii() or not value.isdigit() or len(value) > 19 or int(value) > (1 << 63) - 1:
        raise CycleError('history_cursor_invalid')
    return str(int(value))


def _identity(session, conversation):
    return digest([session.account_name, session.im_id, session.market_region,
                   conversation.conversation_id, conversation.oec_id,
                   conversation.conversation_type, hashlib.sha256(conversation.full_cid).hexdigest()])


class HistoryBackfill:
    def __init__(self, store):
        self.s = store
        tables = {row[0] for row in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not set(_REQUIRED).issubset(tables):
            raise CycleError('inbox_history_migration_required')
        columns = {row[1] for row in store.db.execute('PRAGMA table_info(inbox_history_page)')}
        if 'deferred_to_hot' not in columns:
            raise CycleError('inbox_history_migration_required')

    def status(self, plan, cid):
        row = self.s.db.execute('SELECT * FROM inbox_history_checkpoint WHERE plan_id=? AND cid=?', (plan, cid)).fetchone()
        return dict(row) if row else None

    def _binding(self, plan, cid, oec):
        cp = self.s.db.execute('SELECT * FROM inbox_checkpoint WHERE plan_id=? AND cid=?', (plan, cid)).fetchone()
        if not cp or cp['oec'] != oec:
            raise CycleError('history_checkpoint_identity_mismatch')
        if not self.s.db.execute('SELECT 1 FROM relationship WHERE plan_id=? AND oec=?', (plan, oec)).fetchone():
            raise CycleError('relationship_missing')

    def run(self, session, plan, cid, oec, *, max_pages=5, page_size=20, stopped=lambda: False):
        if type(max_pages) is not int or not 1 <= max_pages <= 100 or type(page_size) is not int or not 1 <= page_size <= 50:
            raise CycleError('history_budget_invalid')
        self._binding(plan, cid, oec)
        plan_row = self.s.db.execute('SELECT market FROM plan WHERE id=?', (plan,)).fetchone()
        market = load_registry()['markets'].get(plan_row['market']) if plan_row else None
        if not market or session.market_region != market['platformRegion'] or session.account_name != market['accounts']['communications']:
            raise CycleError('history_account_market_mismatch')
        if stopped():
            return dict(state='stopped', pagesRead=0, added=0, contentsAdded=0, realSends=0, automaticReplies=0)
        # Always obtain fresh CID/OEC/market proof before continuing a persisted cursor.
        conversation = session.conversation(cid, oec)
        identity = _identity(session, conversation)
        with self.s.tx():
            self._binding(plan, cid, oec)
            cp = self.status(plan, cid)
            if cp and (cp['identity_key'] != identity or cp['oec'] != oec):
                raise CycleError('history_identity_changed')
            if not cp:
                now = self.s.clock()
                self.s.db.execute("INSERT INTO inbox_history_checkpoint VALUES(?,?,?,?,?,'partial',0,?,?)",
                                  (plan, cid, oec, identity, '0', now, now))
        result = dict(state='partial', pagesRead=0, added=0, contentsAdded=0, realSends=0, automaticReplies=0)
        for _ in range(max_pages):
            cp = self.status(plan, cid)
            if cp['state'] != 'partial':
                result['state'] = cp['state']
                break
            if stopped():
                result['state'] = 'stopped'
                break
            requested_at = self.s.clock()
            page = session.history_page(conversation, cursor=int(_cursor(cp['next_cursor'])), limit=page_size)
            saved = self._save(plan, cid, oec, identity, cp, page, requested_at=requested_at)
            result['pagesRead'] += 1
            result['added'] += saved['added']
            result['contentsAdded'] += saved['contentsAdded']
            result['state'] = saved['state']
            if saved['state'] != 'partial':
                break
        cp = self.status(plan, cid)
        deferred = self.s.db.execute('SELECT coalesce(sum(deferred_to_hot),0) FROM inbox_history_page WHERE plan_id=? AND cid=?', (plan, cid)).fetchone()[0]
        result.update(nextCursor=cp['next_cursor'], totalPages=cp['pages'],
                      deferredToHotReader=deferred, deferredCountScope='page observations; overlap may repeat',
                      allReturnedMessagesStored=cp['state'] == 'complete' and deferred == 0,
                      # This attests the original OLDER chain; hot inbox changes separately.
                      completeOlderRange=cp['state'] == 'complete', coverageStartedAt=cp['started_at'] if cp['pages'] else None)
        return result

    def _save(self, plan, cid, oec, identity, expected, page, *, requested_at):
        if page.get('identityVerified') is not True or page.get('direction') != 'older' or type(page.get('hasMore')) is not bool:
            raise CycleError('invalid_history')
        request, following = _cursor(page.get('requestCursor')), _cursor(page.get('nextCursor'))
        if request != expected['next_cursor']:
            raise CycleError('history_cursor_mismatch')
        events, contents = page.get('events'), page.get('contents')
        if not isinstance(events, list) or not isinstance(contents, list) or page.get('messageCount') != len(events):
            raise CycleError('invalid_history')
        body_hash = page.get('bodySha256')
        if not isinstance(body_hash, str) or len(body_hash) != 64 or any(c not in '0123456789abcdef' for c in body_hash):
            raise CycleError('history_evidence_missing')
        unique = {}
        for event in events:
            if event.get('conversationId') != cid or event.get('oecId') != oec or event.get('kind') not in KINDS:
                raise CycleError('event_identity_mismatch')
            mid = event.get('messageId')
            if not isinstance(mid, str) or not mid.isascii() or not mid.isdigit() or not 0 < int(mid) <= (1 << 63) - 1:
                raise CycleError('event_id_missing')
            if mid in unique:
                raise CycleError('history_duplicate_message')
            unique[mid] = event
        content_map = {}
        for content in contents:
            if set(content) != {'messageId', 'format', 'text', 'nativeType', 'rawSha256'} or content['format'] not in ('text', 'attachment_or_unsupported'):
                raise CycleError('content_invalid')
            mid = content['messageId']
            if mid not in unique or mid in content_map:
                raise CycleError('content_identity_unverified')
            content_map[mid] = content
        if set(content_map) != set(unique):
            raise CycleError('history_content_missing')
        ids_hash = digest(sorted(unique))
        now = self.s.clock()
        with self.s.tx():
            self._binding(plan, cid, oec)
            current = self.status(plan, cid)
            if current != expected or current['identity_key'] != identity:
                raise CycleError('history_checkpoint_changed')
            db = self.s.db
            repeated = bool(unique and db.execute('SELECT 1 FROM inbox_history_page WHERE plan_id=? AND cid=? AND message_ids_hash=?',
                                                  (plan, cid, ids_hash)).fetchone())
            stalled = page['hasMore'] and (not unique or following == request or following == '0' or
                       (request != '0' and int(following) >= int(request)))
            state = 'repeated_page' if repeated else 'cursor_stalled' if stalled else 'partial' if page['hasMore'] else 'complete'
            added = captured = deferred = 0
            hot = db.execute('SELECT baseline_at FROM inbox_checkpoint WHERE plan_id=? AND cid=?', (plan, cid)).fetchone()
            for mid, event in unique.items():
                previous = db.execute('SELECT * FROM inbox_event WHERE plan_id=? AND cid=? AND message_id=?', (plan, cid, mid)).fetchone()
                if previous and (previous['oec'] != oec or previous['payload'] != encoded(event)):
                    raise CycleError('event_conflict')
                if not previous:
                    stamp = event.get('createTimeRaw')
                    valid = type(stamp) is int and 946684800000 <= stamp <= int(now * 1000) + 300000
                    # Inbox.ingest classifies strictly pre-baseline timestamps as history.
                    # Unknown newer messages must remain absent until the hot reader sees
                    # them, otherwise its dedupe would swallow genuine live replies.
                    # Our verified outbound messages never create pending work, so keep
                    # their bodies even beyond the hot reader's latest-20 window.
                    if event['kind'] != 'ourMessages' and (not valid or stamp >= hot['baseline_at'] * 1000):
                        deferred += 1
                        continue
                    db.execute('INSERT INTO inbox_event VALUES(?,?,?,?,?,?,?,?,?)',
                               (plan, cid, mid, oec, event['kind'], stamp if valid else None, encoded(event), 1, now))
                    added += 1
                if event['kind'] not in ('creatorReplies', 'ourMessages'):
                    continue
                content = content_map[mid]
                content_hash = digest(content)
                # Preserve a hot reader's current head. Recording a historical version must
                # never reopen live pending work or roll a newer edited body backwards.
                db.execute('INSERT OR IGNORE INTO inbox_content_version VALUES(?,?,?,?,?,?)',
                           (plan, cid, mid, content_hash, encoded(content), now))
                # Filling a missing live inbound head could unblock an existing Agent
                # pending item. Leave its activation to the regular hot reader.
                if previous and not previous['historical'] and event['kind'] == 'creatorReplies':
                    if not db.execute('SELECT 1 FROM inbox_content_head WHERE plan_id=? AND cid=? AND message_id=?', (plan, cid, mid)).fetchone():
                        deferred += 1
                    continue
                inserted = db.execute('INSERT OR IGNORE INTO inbox_content_head VALUES(?,?,?,?)', (plan, cid, mid, content_hash)).rowcount
                captured += inserted
            number = current['pages'] + 1
            db.execute('''INSERT INTO inbox_history_page(plan_id,cid,page_number,request_cursor,next_cursor,has_more,
                       message_count,message_ids_hash,body_sha256,added,contents_added,observed_at,deferred_to_hot)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                       (plan, cid, number, request, following, int(page['hasMore']), len(unique), ids_hash,
                        body_hash, added, captured, now, deferred))
            db.execute('UPDATE inbox_history_checkpoint SET next_cursor=?,state=?,pages=?,updated_at=?,started_at=? WHERE plan_id=? AND cid=?',
                       (following, state, number, now, requested_at if current['pages'] == 0 else current['started_at'], plan, cid))
        return dict(state=state, added=added, contentsAdded=captured)
