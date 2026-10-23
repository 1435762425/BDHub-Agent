"""Account-affine sample reads and one frozen review request.

The BR review contract is deliberately absent by default. A successful empty list
is evidence for reading that list, never proof of creator IDs, payment or approval.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import hashlib
import time
from pathlib import Path

from lib.second_cycle import CycleError, digest

LIST = '/api/v1/affiliate/partner/sample/records/list'
CAMPAIGNS = '/api/v1/affiliate/partner/campaign/list'
PRODUCTS = '/api/v1/affiliate/partner/campaign/product/list'
REVIEW = '/api/v1/affiliate/partner/campaign/sample/budget/review'
# The one request shape observed in this project's historical BR read. Status 2
# is retained as an opaque filter; it is never interpreted as a pending request.
OBSERVED_READ_BODY = {'search_params': [{'search_key': 28, 'search_type': 1, 'value': '2'}],
                      'order_params': [{'order_key': 9, 'order_type': 2}], 'page_size': 5, 'cur_page': 1}
CONTRACT_SCHEMA = 'bdhub.br-sample-contract.v1'


def validate_contract(contract):
    """Require separately observed identity, money and lifecycle contracts."""
    if not isinstance(contract, dict) or contract.get('schema') != CONTRACT_SCHEMA or contract.get('market') != 'br':
        raise CycleError('sample_contract_unverified')
    proofs = contract.get('evidence') or {}
    if any(not isinstance(proofs.get(key), str) or not proofs[key] for key in
           ('records', 'creatorIdentity', 'payment', 'review', 'lifecycle')):
        raise CycleError('sample_contract_evidence_missing')
    paths = contract.get('recordPaths') or {}
    if any(not isinstance(paths.get(key), str) or not paths[key] for key in
           ('applicationId', 'oecId', 'platformCreatorId', 'pid', 'campaignId', 'status', 'paymentRequired')):
        raise CycleError('sample_contract_fields_missing')
    statuses = contract.get('statusMap')
    if not isinstance(statuses, dict) or not statuses or not all(isinstance(k, str) and v in
            {'pending', 'seller_pending', 'approved', 'rejected', 'shipped', 'completed', 'cancelled'} for k, v in statuses.items()):
        raise CycleError('sample_contract_status_missing')
    if not {'pending', 'approved'} <= set(statuses.values()):
        raise CycleError('sample_contract_status_missing')
    if contract.get('paymentFreeValue') is not False:
        raise CycleError('sample_contract_payment_unverified')
    body = contract.get('readBody')
    if not isinstance(body, dict) or any(k not in {'search_params', 'order_params', 'page_size', 'cur_page'} for k in body):
        raise CycleError('sample_contract_read_shape_invalid')
    if type(body.get('page_size')) is not int or not 1 <= body['page_size'] <= 100 or body.get('cur_page') != 1:
        raise CycleError('sample_contract_read_shape_invalid')
    if contract.get('reviewSuccessCode') != 0 or type(contract.get('reviewSuccessCode')) is not int:
        raise CycleError('sample_contract_review_unverified')
    return deepcopy(contract)


def _path(row, path):
    value = row
    for key in path.split('.'):
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def normalize_record(raw, *, contract=None):
    """Retain only business facts; seller/creator contact information never enters this ledger."""
    if not isinstance(raw, dict):
        raise CycleError('sample_record_invalid')
    paths = (contract or {}).get('recordPaths') or {
        'applicationId': 'apply_id', 'oecId': 'creator_info.oec_id', 'pid': 'product_info.product_id',
        'campaignId': 'campaign_info.campaign_id', 'status': 'sample_status'}
    def ident(key):
        value = _path(raw, paths.get(key, ''))
        # IDs remain strings; numeric IDs cannot safely pass through browser JSON.
        return value if isinstance(value, str) and value.isascii() and value.isdigit() and int(value) > 0 else None
    application = ident('applicationId')
    if not application:
        raise CycleError('sample_application_id_missing')
    status = _path(raw, paths.get('status', ''))
    payment = _path(raw, paths.get('paymentRequired', '')) if contract else None
    return {'applicationId': application, 'oecId': ident('oecId'), 'pid': ident('pid'),
            'campaignId': ident('campaignId'), 'platformCreatorId': ident('platformCreatorId') if contract else None,
            'state': (contract or {}).get('statusMap', {}).get(str(status), 'unknown'),
            'nativeStatus': status if type(status) in (str, int) else None,
            'paymentRequired': payment if type(payment) is bool else None,
            'contractFingerprint': digest(contract) if contract else None}


def review_payload(application):
    """Only the observed platform creator ID is allowed; never substitute OEC."""
    values = [application.get(key) for key in ('campaignId', 'platformCreatorId', 'pid')]
    if any(not isinstance(v, str) or not v.isascii() or not v.isdigit() or int(v) <= 0 for v in values):
        raise CycleError('sample_review_identity_unverified')
    if application.get('paymentRequired') is not False:
        raise CycleError('sample_payment_requires_handling')
    return dict(zip(('campaign_id', 'creator_id', 'product_id'), values)) | {'is_approved': True}


class SampleAdapter:
    """Protocol independent of transport construction, useful for narrow contract tests."""
    def __init__(self, transport, report, *, contract=None, frozen=None, stopped=lambda: False):
        self.transport = transport
        self.report = report
        self.contract = validate_contract(contract) if contract is not None else None
        self.frozen = deepcopy(frozen)
        self.stopped = stopped
        self.used = False

    def read_page(self, page=1):
        if self.stopped():
            raise CycleError('sample_stopped')
        if type(page) is not int or not 1 <= page <= 100:
            raise CycleError('sample_page_invalid')
        body = deepcopy(self.contract['readBody'] if self.contract else OBSERVED_READ_BODY)
        body['cur_page'] = page
        result = self.transport._xhr(method='POST', path=LIST, params=self.transport._params(), payload=body, write=False)
        payload = self.transport.require_read(result)
        data = payload.get('data')
        if not isinstance(data, dict):
            raise CycleError('sample_records_response_invalid')
        rows = data.get('sample_records')
        total = data.get('total_num', data.get('total'))
        if type(total) is not int or total < 0 or not isinstance(rows, list) and not (total == 0 and rows is None):
            raise CycleError('sample_records_response_invalid')
        rows = [] if rows is None else rows
        if len(rows) > body['page_size'] or len(rows) > total:
            raise CycleError('sample_records_response_invalid')
        self.report['readRequests'] = self.report.get('readRequests', 0) + 1
        return {'records': [normalize_record(r, contract=self.contract) for r in rows], 'total': total,
                'page': page, 'pageSize': body['page_size'], 'evidenceRef': 'sample-list:' + digest(payload)}

    def refresh_sample_quota(self, offer):
        """Read the exact joined Campaign/PID; incomplete pages never prove absence."""
        from lib.cycle_catalog import sample_facts, timestamp
        from lib.product_stock_policy import unavailable_allowed
        from lib.second_cycle import epoch
        refs = []
        def find(path, params, key, id_key, wanted, max_pages):
            total = None
            seen = set()
            for page in range(1, max_pages + 1):
                if self.stopped():
                    raise CycleError('sample_stopped')
                result = self.transport._xhr(method='GET', path=path,
                    params=self.transport._params() | params | {'cur_page': page, 'page_size': 100}, payload=None, write=False)
                payload = self.transport.require_read(result)
                self.report['readRequests'] = self.report.get('readRequests', 0) + 1
                refs.append(digest(payload))
                data = payload.get('data')
                current = data.get('total_num') if isinstance(data, dict) else None
                if isinstance(current, str) and current.isascii() and current.isdigit():
                    current = int(current)
                rows = data.get(key, []) if isinstance(data, dict) else None
                if type(current) is not int or current < 0 or total is not None and current != total or not isinstance(rows, list) or len(rows) > 100:
                    raise CycleError('sample_offer_pagination_incomplete')
                total = current
                ids = [str(r.get(id_key) or '') for r in rows if isinstance(r, dict)]
                if len(ids) != len(rows) or any(not value.isascii() or not value.isdigit() for value in ids) or len(ids) != len(set(ids)) or seen.intersection(ids):
                    raise CycleError('sample_offer_pagination_incomplete')
                seen.update(ids)
                if len(seen) > total or len(rows) < 100 and len(seen) != total:
                    raise CycleError('sample_offer_pagination_incomplete')
                matches = [r for r in rows if str(r.get(id_key)) == wanted]
                if len(matches) == 1:
                    return matches[0]
                if len(seen) == total:
                    raise CycleError('sample_current_offer_missing')
            raise CycleError('sample_offer_page_limit')
        campaign = find(CAMPAIGNS, {'campaign_join_status_category': '1', 'crs_campaign_types': ''},
                        'campaign', 'campaign_id', offer['campaignId'], 5)
        product = find(PRODUCTS, {'campaign_id': offer['campaignId'],
                       'marked': '0' if str(campaign.get('crs_campaign_type')) == '7' else 'false'},
                       'campaign_product', 'product_id', offer['pid'], 20)
        at = time.time()
        end = timestamp(campaign.get('promotion_end_time'))
        start = timestamp(campaign.get('promotion_start_time'))
        available = str(product.get('product_status')) == '2' and product.get('is_under_governed') is not True and \
                    unavailable_allowed(product.get('unavailable_type'), {}) and end is not None and epoch(end) > at and \
                    (start is None or epoch(start) <= at)
        evidence = 'sample-offer:' + digest(refs)
        return sample_facts(product, evidence, at) | {'pid': offer['pid'], 'campaignId': offer['campaignId'], 'available': available}

    def approve_once(self, payload):
        if self.stopped():
            raise CycleError('sample_stopped')
        if not self.contract or self.frozen is None or payload != self.frozen or self.used:
            raise CycleError('sample_review_outside_intent')
        # Claim the attempt before transport entry: network failure is never replayable.
        self.used = True
        self.report['platformWriteAttempts'] = self.report.get('platformWriteAttempts', 0) + 1
        result = self.transport._xhr(method='POST', path=REVIEW, params=self.transport._params(), payload=deepcopy(payload), write=True)
        if result.http_status:
            self.report['platformWrites'] = self.report.get('platformWrites', 0) + 1
        receipt = {'http': result.http_status, 'code': result.code if type(result.code) is int else None,
                   'evidenceRef': 'sample-review:' + digest(result.payload)}
        if result.ambiguous or result.http_status != 200 or result.has_turing or result.system_error_3 or type(result.code) is not int:
            return receipt | {'status': 'unknown'}
        if result.code != self.contract['reviewSuccessCode']:
            return receipt | {'status': 'rejected'}
        # A success response is an agency decision receipt. Only a lifecycle read
        # can confirm platform approval or show seller confirmation is still due.
        return receipt | {'status': 'accepted'}


@contextmanager
def sample_session(root, market, account_name, report, *, contract=None, frozen=None,
                   expected_binding=None, stopped=lambda: False, wait_seconds=5):
    """Share the original platform slots before acquiring this account's lease."""
    if market != 'br':
        raise CycleError('sample_market_not_supported')
    import uuid
    from lib.first_resources import platform_read
    with platform_read(root, 'first-sample-' + uuid.uuid4().hex, account_name=account_name) as grant:
        def scoped_stopped():
            grant.check()
            return stopped()
        with _account_sample_session(root, market, account_name, report, contract=contract, frozen=frozen,
                                     expected_binding=expected_binding, stopped=scoped_stopped,
                                     wait_seconds=wait_seconds) as adapter:
            yield adapter


@contextmanager
def _account_sample_session(root, market, account_name, report, *, contract=None, frozen=None,
                            expected_binding=None, stopped=lambda: False, wait_seconds=5):
    """Use project-owned identity, existing ProfileLease and fixed endpoint scopes."""
    if market != 'br':
        raise CycleError('sample_market_not_supported')
    if type(wait_seconds) not in (int, float) or not 0 <= wait_seconds <= 15:
        raise CycleError('sample_guard_wait_invalid')
    from lib.market_accounts import catalog_read_account
    root = Path(root)
    if catalog_read_account(root, account_name, market) != account_name:
        raise CycleError('sample_account_unverified')
    if frozen is not None:
        contract = validate_contract(contract)
    from lib.legacy_runtime import configure_vendored_bdhub
    configure_vendored_bdhub(root=root)
    from bdhub import scheduled_relogin
    from bdhub.enrich.profile_lease import ProfileBusyError, ProfileLease
    from bdhub.hub.markets import identity_for
    from bdhub.send.taplink.transport import TapLinkTransport, account_for
    cfg, account = account_for(market, account_name, check_maintenance=False)
    identity = identity_for(market, account=account, cfg=cfg).require_product_search()
    if not identity.partner_id_is_own:
        raise CycleError('sample_identity_not_own')
    headers_path = Path(account.headers_json)
    before = hashlib.sha256(headers_path.read_bytes()).hexdigest()
    binding = {'market': market, 'account': account_name,
               'institutionFingerprint': digest(str(identity.im_market_partner_id)), 'identitySha256': before}
    if expected_binding is not None and binding != expected_binding:
        raise CycleError('sample_original_identity_changed')
    def check():
        if stopped():
            raise CycleError('sample_stopped')
        if scheduled_relogin.maintenance_due(account, initialize=False, ignore_retry_throttle=True):
            raise CycleError('sample_account_maintenance_due')
        if hashlib.sha256(headers_path.read_bytes()).hexdigest() != before:
            raise CycleError('sample_identity_changed')
    class Scoped(TapLinkTransport):
        READ_ENDPOINTS = frozenset({(LIST, 'POST'), (CAMPAIGNS, 'GET'), (PRODUCTS, 'GET')})
        WRITE_ENDPOINTS = frozenset({(REVIEW, 'POST')}) if frozen is not None else frozenset()
        def _xhr(self, **kwargs):
            check()
            if kwargs.get('write') and kwargs.get('payload') != frozen:
                raise CycleError('sample_review_outside_intent')
            return super()._xhr(**kwargs)
    lease = ProfileLease(account.profile_dir, account=account_name, market=market, operation='agent-sample-review' if frozen else 'agent-sample-read')
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            owner = lease.acquire()
            break
        except ProfileBusyError:
            if stopped() or time.monotonic() >= deadline:
                raise CycleError('sample_account_in_use') from None
            time.sleep(.1)
    transport = None
    try:
        check()
        transport = Scoped(identity, account, allow_write=frozen is not None)
        report.update(binding=binding, platformWriteAttempts=0, platformWrites=0)
        adapter = SampleAdapter(transport, report, contract=contract, frozen=frozen, stopped=stopped)
        adapter.binding = binding
        yield adapter
    finally:
        if transport is not None:
            transport.session.close()
        lease.release(owner.token)
        report['identityFileUnchanged'] = hashlib.sha256(headers_path.read_bytes()).hexdigest() == before
