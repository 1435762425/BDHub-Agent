"""Local fixtures for historical coverage; no platform calls or real DB writes."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from lib.cycle_inbox import Inbox
from lib.cycle_service import Service
from lib.inbox_history import HistoryBackfill, SCHEMA
from lib.schema_migrations import SECOND_CYCLE_INBOX_HISTORY_DEFERRED
from lib.second_cycle import CycleStore, CycleError, digest, encoded
from test_second_cycle import NOW, edge, offer


class Session:
    account_name = 'acc6'
    im_id = '987'
    market_region = '8'

    def __init__(self, pages):
        self.pages = pages
        self.requests = []

    def conversation(self, cid, oec):
        return SimpleNamespace(conversation_id=cid, oec_id=oec, conversation_type=2, full_cid=b'full-10')

    def history_page(self, conversation, cursor=0, limit=20):
        self.requests.append((cursor, limit))
        value = self.pages[str(cursor)]
        if isinstance(value, Exception):
            raise value
        return value


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = CycleStore(Path(self.tmp.name) / 'db', lambda: NOW)
        self.plan = self.store.plan('test', 'it')
        self.store.publish(self.plan, 's', NOW, [offer()])
        self.store.import_edges(self.plan, [edge()])
        self.oec = self.store.db.execute('SELECT oec FROM relationship').fetchone()[0]
        self.inbox = Inbox(self.store)
        Service(self.store)
        self.inbox.ingest(self.plan, '10', self.oec, dict(events=[], hasMore=False, identityVerified=True))
        self.store.db.executescript(SCHEMA)
        self.store.db.executescript(SECOND_CYCLE_INBOX_HISTORY_DEFERRED.sql)
        self.history = HistoryBackfill(self.store)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def page(self, cursor, following, ids, more=True, kind='creatorReplies'):
        events = [dict(messageId=str(mid), kind=kind, createTimeRaw=int((NOW - 100) * 1000),
                       messageType=1000, conversationId='10', oecId=self.oec) for mid in ids]
        contents = [dict(messageId=str(mid), format='text', text=f'message {mid}', nativeType='text', rawSha256='a' * 64) for mid in ids]
        return dict(events=events, contents=contents, requestCursor=str(cursor), nextCursor=str(following),
                    messageCount=len(events), hasMore=more, identityVerified=True, direction='older', bodySha256=digest(events))

    def run_pages(self, session, **kwargs):
        return self.history.run(session, self.plan, '10', self.oec, **kwargs)

    def test_resume_native_cursor_atomic_dedupe_and_no_reply_or_gap_mutation(self):
        self.store.db.execute("UPDATE inbox_checkpoint SET state='gap'")
        before = [tuple(row) for row in self.store.db.execute('SELECT * FROM relationship')]
        session = Session({'0': self.page(0, 80, [3, 2]), '80': self.page(80, 0, [2, 1], False)})
        first = self.run_pages(session, max_pages=1)
        self.assertEqual((first['state'], first['nextCursor'], first['added']), ('partial', '80', 2))
        self.history = HistoryBackfill(self.store)
        second = self.run_pages(session)
        self.assertEqual((second['state'], second['added'], second['contentsAdded']), ('complete', 1, 1))
        self.assertEqual(session.requests, [(0, 20), (80, 20)])
        self.assertTrue(second['completeOlderRange'])
        self.assertEqual(self.store.db.execute('SELECT sum(historical),count(*) FROM inbox_event').fetchone()[:], (3, 3))
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM inbox_pending').fetchone()[0], 0)
        self.assertEqual(self.store.db.execute('SELECT state FROM inbox_checkpoint').fetchone()[0], 'gap')
        self.assertEqual(before, [tuple(row) for row in self.store.db.execute('SELECT * FROM relationship')])
        self.assertEqual(self.run_pages(session)['pagesRead'], 0)

    def test_transport_error_leaves_last_atomic_cursor_for_restart(self):
        session = Session({'0': self.page(0, 80, [3]), '80': TimeoutError()})
        with self.assertRaises(TimeoutError):self.run_pages(session)
        self.assertEqual(self.history.status(self.plan, '10')['next_cursor'], '80')
        session.pages['80'] = self.page(80, 0, [2], False)
        self.assertEqual(self.run_pages(session)['state'], 'complete')
        self.assertEqual([row[0] for row in session.requests], [0, 80, 80])

    def test_identity_change_stops_before_history_and_page_mismatch_rolls_back(self):
        session = Session({'0': self.page(0, 80, [3])})
        self.run_pages(session, max_pages=1)
        session.im_id = '988'
        with self.assertRaisesRegex(CycleError, 'identity_changed'):self.run_pages(session)
        self.assertEqual(len(session.requests), 1)
        session.im_id = '987'
        page = self.page(80, 0, [2], False)
        page['events'][0]['oecId'] = 'different'
        session.pages['80'] = page
        with self.assertRaisesRegex(CycleError, 'identity_mismatch'):self.run_pages(session)
        self.assertEqual(self.history.status(self.plan, '10')['pages'], 1)

    def test_duplicate_page_even_at_terminal_cannot_prove_completion(self):
        session = Session({'0': self.page(0, 80, [3, 2]), '80': self.page(80, 0, [2, 3], False)})
        report = self.run_pages(session)
        self.assertEqual(report['state'], 'repeated_page')
        self.assertFalse(report['completeOlderRange'])
        self.assertEqual(report['added'], 2)
        self.assertEqual(self.run_pages(session)['pagesRead'], 0)

    def test_stalled_or_reverse_cursor_and_empty_more_stop(self):
        for next_cursor, ids in [(0, [2]), (0, []), (80, [2]), (90, [2])]:
            with self.subTest(cursor=next_cursor, ids=ids):
                self.store.db.execute('DELETE FROM inbox_history_checkpoint')
                self.store.db.execute('DELETE FROM inbox_history_page')
                session = Session({'0': self.page(0, 80, [3]), '80': self.page(80, next_cursor, ids)})
                report = self.run_pages(session)
                self.assertEqual(report['state'], 'cursor_stalled')
                self.assertFalse(report['completeOlderRange'])

    def test_history_preserves_live_content_head_and_pending_revision(self):
        event = self.page(0, 0, [1], False)['events'][0]
        event['createTimeRaw'] = int(NOW * 1000)
        self.inbox.ingest(self.plan, '10', self.oec, dict(events=[event], hasMore=False, identityVerified=True))
        service = Service(self.store)
        content = self.page(0, 0, [1], False)['contents'][0] | {'text': 'newer edit'}
        service.capture(self.plan, '10', self.oec, [content])
        pending = [tuple(row) for row in self.store.db.execute('SELECT * FROM inbox_pending')]
        historical_page = self.page(0, 0, [1], False)
        historical_page['events'][0] = event
        self.run_pages(Session({'0': historical_page}))
        self.assertEqual(self.store.db.execute('SELECT hash FROM inbox_content_head').fetchone()[0], digest(content))
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM inbox_content_version').fetchone()[0], 2)
        self.assertEqual(pending, [tuple(row) for row in self.store.db.execute('SELECT * FROM inbox_pending')])
        self.assertEqual(self.store.db.execute('SELECT historical FROM inbox_event').fetchone()[0], 0)

    def test_missing_live_inbound_body_is_not_activated_by_history(self):
        page = self.page(0, 0, [1], False)
        page['events'][0]['createTimeRaw'] = int(NOW * 1000)
        self.inbox.ingest(self.plan, '10', self.oec, dict(events=page['events'], hasMore=False, identityVerified=True))
        report = self.run_pages(Session({'0': page}))
        self.assertEqual(report['contentsAdded'], 0)
        self.assertEqual(report['deferredToHotReader'], 1)
        self.assertFalse(report['allReturnedMessagesStored'])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM inbox_content_head').fetchone()[0], 0)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM inbox_content_version').fetchone()[0], 1)
        self.assertIsNone(Service(self.store).context(self.plan, self.store.db.execute('SELECT creator_id FROM relationship').fetchone()[0])[0]['content'])

    def test_conflicting_event_rolls_back_page_and_other_events(self):
        self.run_pages(Session({'0': self.page(0, 80, [3])}), max_pages=1)
        bad = self.page(80, 0, [2, 3], False)
        bad['events'][1]['kind'] = 'ourMessages'
        with self.assertRaisesRegex(CycleError, 'event_conflict'):self.run_pages(Session({'80': bad}))
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM inbox_event').fetchone()[0], 1)
        self.assertEqual(self.history.status(self.plan, '10')['pages'], 1)

    def test_stop_and_unindexed_conversation_issue_no_history_request(self):
        session = Session({})
        self.assertEqual(self.run_pages(session, stopped=lambda: True)['state'], 'stopped')
        self.store.db.execute('DELETE FROM inbox_checkpoint')
        with self.assertRaises(CycleError):self.run_pages(session)
        self.assertEqual(session.requests, [])

    def test_unseen_hot_reply_is_deferred_and_regular_reader_still_queues_it(self):
        page = self.page(0, 0, [2, 1], False)
        page['events'][0]['createTimeRaw'] = int(NOW * 1000)
        report = self.run_pages(Session({'0': page}))
        self.assertTrue(report['completeOlderRange'])
        self.assertFalse(report['allReturnedMessagesStored'])
        self.assertEqual(report['deferredToHotReader'], 1)
        self.assertEqual(report['added'], 1)
        self.assertIsNone(self.store.db.execute("SELECT 1 FROM inbox_event WHERE message_id='2'").fetchone())
        repeated = self.run_pages(Session({}))
        self.assertEqual(repeated['deferredToHotReader'], 1)
        hot = self.inbox.ingest(self.plan, '10', self.oec, dict(events=page['events'], hasMore=False, identityVerified=True))
        self.assertEqual(hot['liveReplies'], 1)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM inbox_pending').fetchone()[0], 1)

    def test_outbound_beyond_hot_window_after_baseline_keeps_body_without_pending_or_relationship_changes(self):
        from lib.observed_messages import outbound_messages
        self.store.clock = lambda: NOW + 100
        head = self.page(0, 80, list(range(50, 30, -1)), kind='ourMessages')
        for event in head['events']:
            event['createTimeRaw'] = int((NOW + 50) * 1000)
        self.inbox.ingest(self.plan, '10', self.oec, dict(events=head['events'], hasMore=False, identityVerified=True))
        older = self.page(80, 0, [21], False, kind='ourMessages')
        older['events'][0]['createTimeRaw'] = int((NOW + 10) * 1000)
        relationships = [tuple(row) for row in self.store.db.execute('SELECT * FROM relationship')]
        pending = [tuple(row) for row in self.store.db.execute('SELECT * FROM inbox_pending')]
        report = self.run_pages(Session({'0': head, '80': older}))
        self.assertEqual((report['added'], report['deferredToHotReader']), (1, 0))
        self.assertTrue(report['allReturnedMessagesStored'])
        observed = outbound_messages(self.store.db, self.plan, '10', self.oec)
        recovered = [message for message in observed if message['messageId'] == '21']
        self.assertEqual(len(recovered), 1)
        self.assertEqual((recovered[0]['text'], recovered[0]['status']), ('message 21', 'observed'))
        self.assertEqual(pending, [tuple(row) for row in self.store.db.execute('SELECT * FROM inbox_pending')])
        self.assertEqual(relationships, [tuple(row) for row in self.store.db.execute('SELECT * FROM relationship')])
        self.assertEqual(self.store.db.execute("SELECT historical FROM inbox_event WHERE message_id='21'").fetchone()[0], 1)

    def test_unknown_time_is_deferred_and_failed_first_page_does_not_backdate_coverage(self):
        session = Session({'0': TimeoutError()})
        with self.assertRaises(TimeoutError):self.run_pages(session)
        self.assertEqual(self.history.status(self.plan, '10')['pages'], 0)
        self.store.clock = lambda: NOW + 60
        page = self.page(0, 0, [1], False)
        page['events'][0]['createTimeRaw'] = None
        session.pages['0'] = page
        report = self.run_pages(session)
        self.assertEqual(report['coverageStartedAt'], NOW + 60)
        self.assertEqual(report['deferredToHotReader'], 1)
        self.assertFalse(report['allReturnedMessagesStored'])

    def test_first_checkpoint_rejects_wrong_market_or_communications_account(self):
        session = Session({})
        session.market_region = '19'
        with self.assertRaisesRegex(CycleError, 'account_market_mismatch'):self.run_pages(session)
        session.market_region = '8'
        session.account_name = 'acc1'
        with self.assertRaisesRegex(CycleError, 'account_market_mismatch'):self.run_pages(session)
        self.assertIsNone(self.history.status(self.plan, '10'))
        self.assertEqual(session.requests, [])

    def test_constructor_never_creates_missing_migration(self):
        self.store.db.execute('DROP TABLE inbox_history_page')
        with self.assertRaisesRegex(CycleError, 'migration_required'):HistoryBackfill(self.store)
        self.assertIsNone(self.store.db.execute("SELECT name FROM sqlite_master WHERE name='inbox_history_page'").fetchone())

    def test_new_hot_message_during_backfill_does_not_change_older_range_or_queue(self):
        session = Session({'0': self.page(0, 80, [3]), '80': self.page(80, 0, [1], False)})
        self.run_pages(session, max_pages=1)
        hot = self.page(0, 0, [4, 3], False)['events']
        hot[0]['createTimeRaw'] = int(NOW * 1000)
        self.inbox.ingest(self.plan, '10', self.oec, dict(events=hot, hasMore=False, identityVerified=True))
        pending = [tuple(row) for row in self.store.db.execute('SELECT * FROM inbox_pending')]
        result = self.run_pages(session)
        self.assertTrue(result['completeOlderRange'])
        self.assertEqual(pending, [tuple(row) for row in self.store.db.execute('SELECT * FROM inbox_pending')])
        self.assertEqual(self.store.db.execute("SELECT historical FROM inbox_event WHERE message_id='4'").fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
