"""Bounded, read-only recovery of original outreach intents; never dispatches."""
import fcntl
import json
from functools import wraps
from pathlib import Path

from lib.second_cycle import CycleError

INTERVAL = 300
DEADLINE = 900
ATTEMPTS = {'conversation': 2, 'card': 3, 'text': 3}
ROUND_SECONDS = {'conversation': 180, 'card': 90, 'text': 90}
LEGACY_ACCOUNTS = {'it': 'acc6', 'br': 'acc1', 'my': 'acc8', 'uk': 'acc11'}


def serialized(market=None):
    """Hold across the entire original executor, including result persistence/cleanup.

    OS locks survive no crashed owner. A reconciler cannot free the market slot
    while the original HTTP call or its enclosing executor is still running.
    """
    def decorate(function):
        @wraps(function)
        def call(root, *args, **kwargs):
            target = market or (args[0] if args else kwargs['market'])
            path = Path(root) / 'var' / ('delivery-executor-' + target + '.lock')
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('a') as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise CycleError('delivery_executor_busy') from None
                try:
                    return function(root, *args, **kwargs)
                finally:
                    fcntl.flock(lock, fcntl.LOCK_UN)
        return call
    return decorate


def target(deliveries, did):
    d = deliveries.get(did)
    if d['state'] == 'quarantined_unknown':
        return None
    intent = deliveries.conversation_intent(did)
    if intent and intent['state'] in ('inflight','received','confirmed') and all(p['state']=='ready' for p in d['parts']):
        return 'conversation'
    for p in d['parts']:
        if p['state'] in ('inflight', 'accepted', 'unknown'):
            return p['kind']
    return None


def _attempts(deliveries, did, kind):
    return list(deliveries.s.db.execute(
        "SELECT checked,payload FROM cycle_delivery_check WHERE delivery_id=? AND kind=? ORDER BY checked,rowid",
        (did, 'verification_attempt_' + kind)))


def _scan(session, oec):
    cursor = 0
    seen = set()
    matches = set()
    conversations = set()
    for _ in range(101):
        page = session.initialize(cursor)
        for row in page['conversations']:
            conversations.add(row['conversationId'])
            if row['oecId'] == oec:
                matches.add(row['conversationId'])
        if not page['hasMore']:
            return {'status': 'result_unknown', 'reason': 'conversation_request_uncorrelated',
                    'scanComplete': True, 'observedConversations': len(conversations),
                    'matchingConversations': sorted(matches), 'pages': len(seen) + 1}
        next_cursor = page['nextCursor']
        if str(next_cursor) in seen or str(next_cursor) == str(cursor):
            break
        seen.add(str(next_cursor))
        cursor = int(next_cursor)
    return {'status': 'result_unknown', 'reason': 'conversation_scan_bounded',
            'scanComplete': False, 'observedConversations': len(conversations),
            'matchingConversations': sorted(matches)}


def read_original(root, market, d, kind, intent):
    from lib.market_accounts import load_config
    from lib.market_im_runtime import authenticated
    from lib.italy_im_delivery import ItalyImDeliveryAdapter
    from lib.cycle_send_runtime import descriptor
    c = d['snapshot']
    account = c.get('senderAccount') or LEGACY_ACCOUNTS[market]
    if load_config(root)['markets'][market]['roles']['communications'] != account:
        raise CycleError('reconciliation_original_account_changed')
    report = {}
    with authenticated(root, market, report, read_only=True) as rt:
        if rt['auth'].account_name != account:
            raise CycleError('reconciliation_original_account_changed')
        if market == 'it':
            from lib.second_live_runtime import sender_binding_sha256
            if sender_binding_sha256(rt['auth']) != c.get('senderBindingHash'):
                raise CycleError('reconciliation_original_identity_changed')
        elif c.get('senderImId') and c['senderImId'] != rt['auth'].im_id:
            raise CycleError('reconciliation_original_identity_changed')
        session = rt['session']
        if kind == 'conversation':
            if intent and intent['state'] in ('received','confirmed'):
                from lib.market_send_canary import _received_conversation_id
                cid = _received_conversation_id(intent)
                verified = session.conversation(cid, c['oecId'])
                return {'status':'confirmed','requestRef':intent['request_ref'],
                        'conversationId':cid,'evidenceRef':verified.evidence_ref}
            # A listed CID is not a receipt for our request. Never guess it or send
            # either unsubmitted component even if exactly one match is observed.
            return _scan(session, c['oecId'])
        cid = c.get('conversationId') or (intent or {}).get('cid')
        if not cid:
            raise CycleError('reconciliation_original_cid_missing')
        conversation = session.conversation(cid, c['oecId'])
        part = next(p for p in d['parts'] if p['kind'] == kind)
        receipt = json.loads(part['receipt'] or '{}')
        adapter = ItalyImDeliveryAdapter(rt['auth'], session)
        if kind == 'card':
            card = descriptor(c['card']) if market == 'it' else descriptor(
                c['card'], market, account, rt['partnerHost'] + '/api/v1/affiliate/partner/im/product_list/list')
            return adapter.readback_card(conversation, card, part['request_ref'], message_id=receipt.get('messageId'))
        return adapter.readback(conversation, c['message']['textIt'], part['request_ref'], message_id=receipt.get('messageId'))


def reconcile(root, market, deliveries, did, *, reader=None):
    """Caller owns serialized executor lock. Reserve budget before any remote read."""
    reader = reader or read_original
    d = deliveries.get(did)
    kind = target(deliveries, did)
    base = {'deliveryId': did, 'platformWrites': 0, 'realSends': 0}
    if kind is None:
        return base | {'state': d['state']}
    deliveries.unknown(did, 'card' if kind == 'conversation' else kind)
    now = deliveries.s.clock()
    attempts = _attempts(deliveries, did, kind)
    if attempts and (len(attempts) >= ATTEMPTS[kind] or now >= attempts[0][0] + DEADLINE):
        deliveries.isolate_technical(did, kind, 'verification_budget_exhausted')
        return base | {'state': 'quarantined_unknown'}
    if attempts and now < attempts[-1][0] + INTERVAL:
        return base | {'state': 'waiting_reconciliation', 'nextCheckAt': attempts[-1][0] + INTERVAL}
    deadline = (attempts[0][0] if attempts else now) + DEADLINE
    intent = deliveries.conversation_intent(did)
    ref = intent['request_ref'] if kind == 'conversation' else next(p['request_ref'] for p in d['parts'] if p['kind'] == kind)
    deliveries.record_check(did, 'verification_attempt_' + kind,
                            {'attempt': len(attempts) + 1, 'requestRef': ref, 'deadline': deadline})
    from lib.campaign_join import verification_timeout, VerificationDeadline
    try:
        with verification_timeout(min(ROUND_SECONDS[kind], deadline - now)):
            proof = reader(root, market, d, kind, intent)
    except VerificationDeadline:
        proof = {'status': 'result_unknown', 'reason': 'verification_round_timeout', 'readSucceeded': False}
    except Exception as error:
        # Do not export arbitrary transport strings or secret request content.
        proof = {'status': 'result_unknown', 'reason': 'verification_read_failed',
                 'errorType': type(error).__name__, 'readSucceeded': False}
    deliveries.record_check(did, kind, proof)
    if kind == 'conversation' and proof.get('status') == 'confirmed':
        if not intent or proof.get('requestRef')!=intent['request_ref'] or proof.get('conversationId')!=intent['cid']:
            raise CycleError('conversation_identity_mismatch')
        with deliveries.s.tx():
            current=deliveries.conversation_intent(did)
            if current['cid']!=intent['cid'] or current['request_ref']!=ref:
                raise CycleError('conversation_identity_mismatch')
            deliveries.s.db.execute("UPDATE cycle_conversation_intent SET state='confirmed' WHERE delivery_id=?",(did,))
            deliveries.s.db.execute("UPDATE cycle_delivery_part SET state='cancelled' WHERE delivery_id=? AND state='ready'",(did,))
            deliveries.s.db.execute("UPDATE cycle_delivery SET state='cancelled' WHERE id=?",(did,))
        return base | {'state':'cancelled'}
    if kind != 'conversation' and proof.get('status') == 'confirmed':
        deliveries.confirm(did, kind, {'status': 'confirmed', 'requestRef': ref,
            'oecId': d['oec'], 'kind': kind, 'messageId': proof['messageId'], 'evidenceRef': proof['evidenceRef']}, close_unsubmitted=True)
        return base | {'state': deliveries.get(did)['state']}
    if kind == 'card' and deliveries.quarantine_absent_card(did):
        return base | {'state': 'quarantined_unknown'}
    if len(attempts) + 1 >= ATTEMPTS[kind] or deliveries.s.clock() >= deadline:
        deliveries.isolate_technical(did, kind, 'verification_budget_exhausted')
        return base | {'state': 'quarantined_unknown'}
    return base | {'state': 'waiting_reconciliation', 'nextCheckAt': now + INTERVAL}
