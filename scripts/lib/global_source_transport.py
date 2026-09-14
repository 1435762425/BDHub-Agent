"""Read-only opportunity source using the old verified HTTP protocol, no old lease writes."""
from contextlib import contextmanager
from pathlib import Path
import hashlib,importlib.util,sys,time
ROOT=Path(__file__).resolve().parents[2];LEGACY=ROOT.parent/'01-BDSystem-V2'
LIST='/api/v1/affiliate/partner/product/opportunity_product/list'
DETAIL='/api/v1/affiliate/partner/product/opportunity_product/campaign_detail'
SELECTED='/api/v1/affiliate/partner/product/pick_up/list'
CATEGORY='/api/v1/affiliate/lux/product/category/childrenv2'
SELECT='/api/v1/affiliate/partner/product/pick_up/select'
CREATE='/api/v1/affiliate/partner/campaign/product_list/create'
DELETE='/api/v1/affiliate/partner/campaign/product_list/delete'
LIST_INVENTORY='/api/v1/affiliate/partner/campaign/product_list/list'

def is_account_busy(error):
    return isinstance(error,BlockingIOError) or isinstance(error,RuntimeError) and str(error)=='account_in_use'

@contextmanager
def opportunity_reader(report,*,stopped=lambda:False,extra_read_endpoints=frozenset(),account_name=None,wait_seconds=15):
    from lib.market_accounts import catalog_read_account
    account=catalog_read_account(ROOT,account_name)
    with _opportunity_transport(report,stopped=stopped,extra_read_endpoints=extra_read_endpoints,account_name=account,wait_seconds=wait_seconds) as t:yield t

@contextmanager
def opportunity_selector(report,selection_scope,*,stopped=lambda:False):
    """Explicit PID/Campaign allowlist for user-authorized selection; never links or IM."""
    if not isinstance(selection_scope,dict):raise ValueError('selection_scope_required')
    with _opportunity_transport(report,stopped=stopped,selection_scope=selection_scope,account_name='acc6') as t:yield t

@contextmanager
def opportunity_card_creator(report,payload,*,stopped=lambda:False,wait_seconds=15):
    """One frozen product-list request on ACC9; no messages, selection, or automatic replay.

    The wait only makes room for the shared account guard; it never retries a write.
    """
    from lib.market_accounts import catalog_read_account
    from copy import deepcopy
    if catalog_read_account(ROOT,'acc9')!='acc9' or not isinstance(payload,dict) or len(payload.get('items',[]))!=1:raise ValueError('card_canary_scope_invalid')
    reads={('/api/v1/affiliate/partner/im/product_list/list','GET'),('/api/v1/affiliate/partner/campaign/product_list/products','GET')}
    with _opportunity_transport(report,stopped=stopped,extra_read_endpoints=reads,creation_scope=deepcopy(payload),account_name='acc9',wait_seconds=wait_seconds) as t:yield t

@contextmanager
def opportunity_card_creator_batch(report,payloads,*,stopped=lambda:False,wait_seconds=15):
    """Many frozen product-list requests on ACC9 under ONE account guard.

    Establishing the account session (profile lock, cookies, signer) is the dominant fixed
    cost of a creation, so a batch shares it. Safety is unchanged: each write must match a
    frozen payload exactly and each product may be written at most once per session.
    """
    from lib.market_accounts import catalog_read_account
    from copy import deepcopy
    if catalog_read_account(ROOT,'acc9')!='acc9' or not isinstance(payloads,dict) or not payloads:raise ValueError('card_batch_scope_invalid')
    frozen={}
    for pid,payload in payloads.items():
        if not isinstance(payload,dict) or len(payload.get('items',[]))!=1 or str(payload['items'][0].get('product_id'))!=str(pid):raise ValueError('card_batch_scope_invalid')
        frozen[str(pid)]=deepcopy(payload)
    reads={('/api/v1/affiliate/partner/im/product_list/list','GET'),('/api/v1/affiliate/partner/campaign/product_list/products','GET')}
    with _opportunity_transport(report,stopped=stopped,extra_read_endpoints=reads,creation_scope=frozen,account_name='acc9',wait_seconds=wait_seconds) as t:yield t

@contextmanager
def opportunity_list_deleter_batch(report,list_ids,*,stopped=lambda:False,wait_seconds=15):
    """Frozen TapLink deletions on ACC9 under ONE account guard.

    Mirrors the creation gate: every DELETE must be exactly {"list_id": <frozen id>} for a
    list selected here, and each list may be deleted at most once per session.
    """
    from lib.market_accounts import catalog_read_account
    if catalog_read_account(ROOT,'acc9')!='acc9':raise ValueError('list_delete_scope_invalid')
    if not isinstance(list_ids,(list,set,tuple)) or not list_ids:raise ValueError('list_delete_scope_invalid')
    frozen={}
    for lid in list_ids:
        lid=str(lid)
        if not lid.isdigit():raise ValueError('list_delete_scope_invalid')
        frozen[lid]={'list_id':lid}
    reads={(LIST_INVENTORY,'GET'),('/api/v1/affiliate/partner/campaign/product_list/products','GET')}
    with _opportunity_transport(report,stopped=stopped,extra_read_endpoints=reads,deletion_scope=frozen,account_name='acc9',wait_seconds=wait_seconds) as t:yield t

@contextmanager
def _opportunity_transport(report,*,stopped=lambda:False,extra_read_endpoints=frozenset(),selection_scope=None,creation_scope=None,deletion_scope=None,account_name='acc6',wait_seconds=15):
    if type(wait_seconds) not in (int,float) or not 0<=wait_seconds<=60:raise ValueError('invalid_guard_wait')
    # campaign/product_list/list is the read-only TapLink inventory (lists all cards for a
    # campaign+source in pages), used to avoid one search per PID. It never writes.
    allowed_extra={('/api/v1/affiliate/partner/im/product_list/list','GET'),('/api/v1/affiliate/partner/campaign/product_list/products','GET'),('/api/v1/affiliate/partner/campaign/product_list/list','GET'),(DELETE,'POST'),('/api/v1/affiliate/partner/campaign/list','GET'),('/api/v1/affiliate/partner/campaign/product/list','GET')}
    if not set(extra_read_endpoints)<=allowed_extra:raise ValueError('source_read_endpoint_forbidden')
    sys.dont_write_bytecode=True
    if str(LEGACY) not in sys.path:sys.path.insert(0,str(LEGACY))
    from bdhub import scheduled_relogin
    from bdhub.hub.markets import identity_for
    from bdhub.send.taplink.transport import account_for
    from bdhub.research.commerce_transport import CommerceTransport
    from lib.second_cycle import digest
    import json
    if selection_scope is not None and account_name!='acc6':raise ValueError('selection_account_not_validated')
    if creation_scope is not None:
        if selection_scope is not None or account_name!='acc9':raise ValueError('card_canary_account_invalid')
        from bdhub.hub.markets import MARKETS
        if MARKETS['it'].capabilities.tap_link not in ('canary','enabled'):raise ValueError('card_canary_capability_unavailable')
    cfg,account=account_for('it',account_name,check_maintenance=False)
    identity=identity_for('it',account=account,cfg=cfg).require_product_search()
    if not identity.partner_id_is_own:raise ValueError('source_identity_not_own')
    saved=json.loads((ROOT/'var/cycle-catalog-it-20260913/selected.json').read_text())['scope']
    binding={'market':'it','account':account_name,'institutionFingerprint':digest(str(identity.im_market_partner_id))}
    if any(saved.get(k)!=binding[k] for k in ('market','institutionFingerprint')):raise ValueError('source_institution_changed')
    path=Path(account.headers_json);before=hashlib.sha256(path.read_bytes()).hexdigest()
    spec=importlib.util.spec_from_file_location('source_readonly_guard',ROOT/'scripts/probe-italy-profile.py');guard=importlib.util.module_from_spec(spec);spec.loader.exec_module(guard)
    from bdhub.send.sharelink.transport import PICK_UP_SELECT_PATH
    if PICK_UP_SELECT_PATH!=SELECT:raise ValueError('selection_endpoint_changed')
    if selection_scope is not None:
        from bdhub.hub.markets import require_capability
        require_capability('it','product_select')
    consumed=set();creation_used=set();delete_used=set()
    class Scoped(CommerceTransport):
        WRITE_ENDPOINTS=frozenset({(CREATE,'POST')}) if creation_scope is not None else frozenset({(DELETE,'POST')}) if deletion_scope is not None else frozenset({(SELECT,'POST')}) if selection_scope is not None else frozenset()
        READ_ENDPOINTS=frozenset({(LIST,'POST'),(DETAIL,'GET'),(CATEGORY,'POST'),(SELECTED,'POST')})|frozenset(extra_read_endpoints)
        def fork_lane(self,pace):
            # Read-only tasks may fork lanes too; writes stay blocked because WRITE_ENDPOINTS
            # is empty and Scoped._xhr rejects any write when no scope was declared.
            lane=Scoped(identity,account,allow_write=selection_scope is not None or creation_scope is not None)
            lane.copy_session_from(self);lane._pace=pace;lane.check_stop=check;lane._batch_lane=True
            return lane
        def allow_verified_nonselection(self,pid,receipt,fresh,absent):
            if selection_scope is None or absent is not True or fresh.get('product_id')!=pid or fresh.get('fs_is_selected') is not False or receipt.get('http')!=200 or receipt.get('code')!=10000 or receipt.get('verification') is not True or receipt.get('ambiguous') is not False:raise ValueError('nonselection_proof_required')
            consumed.discard(pid)
        def _xhr(self,**kwargs):
            if kwargs.get('write'):
                if deletion_scope is not None:
                    body=kwargs.get('payload') or {}
                    lid=str(body.get('list_id') or '')
                    if kwargs.get('path')!=DELETE or kwargs.get('method')!='POST' or body!=deletion_scope.get(lid) or lid in delete_used:raise ValueError('list_delete_outside_intent')
                    delete_used.add(lid)
                    report['platformWrites']=report.get('platformWrites',0)+1
                    report['deleteWrites']=report.get('deleteWrites',0)+1
                    return super()._xhr(**kwargs)
                if creation_scope is not None:
                    body=kwargs.get('payload') or {}
                    if kwargs.get('path')!=CREATE or kwargs.get('method')!='POST':raise ValueError('card_write_outside_intent')
                    if 'items' in creation_scope:
                        # Single frozen request (canary): exactly one write, byte-identical.
                        if creation_used or body!=creation_scope:raise ValueError('card_write_outside_intent')
                        creation_used.add('*')
                    else:
                        # Batch: every write must equal the frozen request of its own product,
                        # and each product may be written at most once in this session.
                        items=body.get('items') or []
                        pid=str(items[0].get('product_id')) if len(items)==1 else ''
                        expected=creation_scope.get(pid)
                        if not expected or pid in creation_used or body!=expected:raise ValueError('card_write_outside_intent')
                        creation_used.add(pid)
                    report['platformWrites']=report.get('platformWrites',0)+1
                    report['createWrites']=report.get('createWrites',0)+1
                    return super()._xhr(**kwargs)
                body=kwargs.get('payload') or {};pid=body.get('product_id');cid=body.get('campaign_id')
                if selection_scope is None or kwargs.get('path')!=SELECT or kwargs.get('method')!='POST' or set(body)!={'product_id','campaign_id'} or not cid or selection_scope.get(pid)!=cid or pid in consumed:raise ValueError('selection_write_outside_intent')
                consumed.add(pid);report['platformWrites']=report.get('platformWrites',0)+1
            if getattr(self,'_batch_lane',False) and not kwargs.get('write'):
                # Concurrent reads return challenges to the coordinator; no parallel verification.
                self._verification_header=''
                return super(CommerceTransport,self)._xhr(**kwargs)
            return super()._xhr(**kwargs)
    report.update(scope=binding,platformWrites=0,oldDatabaseWrites=0,identityFileWrites=0)
    with guard.readonly_guard(account,wait_seconds=wait_seconds):
        report['guardAcquiredAt']=time.time()
        transport=Scoped(identity,account,allow_write=selection_scope is not None or creation_scope is not None)
        def check():
            if stopped():raise ValueError('source_stopped')
            if scheduled_relogin.maintenance_due(account,initialize=False,ignore_retry_throttle=True):raise ValueError('source_maintenance_due')
            if hashlib.sha256(path.read_bytes()).hexdigest()!=before:raise ValueError('source_identity_changed')
        transport.check_stop=check
        try:
            check();yield transport
        finally:
            transport.session.close();report.update(guardReleasedAt=time.time(),identityFileUnchanged=hashlib.sha256(path.read_bytes()).hexdigest()==before,
                verificationAttempts=transport.verification_attempts+report.get('laneVerificationAttempts',0),verificationSuccesses=transport.verification_successes+report.get('laneVerificationSuccesses',0))
