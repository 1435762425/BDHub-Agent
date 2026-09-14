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

def is_account_busy(error):
    return isinstance(error,BlockingIOError) or isinstance(error,RuntimeError) and str(error)=='account_in_use'

@contextmanager
def opportunity_reader(report,*,stopped=lambda:False,extra_read_endpoints=frozenset(),account_name=None):
    from lib.market_accounts import catalog_read_account
    account=catalog_read_account(ROOT,account_name)
    with _opportunity_transport(report,stopped=stopped,extra_read_endpoints=extra_read_endpoints,account_name=account) as t:yield t

@contextmanager
def opportunity_selector(report,selection_scope,*,stopped=lambda:False):
    """Explicit PID/Campaign allowlist for user-authorized selection; never links or IM."""
    if not isinstance(selection_scope,dict):raise ValueError('selection_scope_required')
    with _opportunity_transport(report,stopped=stopped,selection_scope=selection_scope,account_name='acc6') as t:yield t

@contextmanager
def _opportunity_transport(report,*,stopped=lambda:False,extra_read_endpoints=frozenset(),selection_scope=None,account_name='acc6'):
    allowed_extra={('/api/v1/affiliate/partner/im/product_list/list','GET'),('/api/v1/affiliate/partner/campaign/product_list/products','GET'),('/api/v1/affiliate/partner/campaign/list','GET'),('/api/v1/affiliate/partner/campaign/product/list','GET')}
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
    consumed=set()
    class Scoped(CommerceTransport):
        WRITE_ENDPOINTS=frozenset({(SELECT,'POST')}) if selection_scope is not None else frozenset()
        READ_ENDPOINTS=frozenset({(LIST,'POST'),(DETAIL,'GET'),(CATEGORY,'POST'),(SELECTED,'POST')})|frozenset(extra_read_endpoints)
        def fork_lane(self,pace):
            if selection_scope is None:raise ValueError('selection_lane_requires_scope')
            lane=Scoped(identity,account,allow_write=True);lane.copy_session_from(self);lane._pace=pace;lane.check_stop=check;lane._batch_lane=True
            return lane
        def allow_verified_nonselection(self,pid,receipt,fresh,absent):
            if selection_scope is None or absent is not True or fresh.get('product_id')!=pid or fresh.get('fs_is_selected') is not False or receipt.get('http')!=200 or receipt.get('code')!=10000 or receipt.get('verification') is not True or receipt.get('ambiguous') is not False:raise ValueError('nonselection_proof_required')
            consumed.discard(pid)
        def _xhr(self,**kwargs):
            if kwargs.get('write'):
                body=kwargs.get('payload') or {};pid=body.get('product_id');cid=body.get('campaign_id')
                if selection_scope is None or kwargs.get('path')!=SELECT or kwargs.get('method')!='POST' or set(body)!={'product_id','campaign_id'} or not cid or selection_scope.get(pid)!=cid or pid in consumed:raise ValueError('selection_write_outside_intent')
                consumed.add(pid);report['platformWrites']+=1
            if getattr(self,'_batch_lane',False) and not kwargs.get('write'):
                # Concurrent reads return challenges to the coordinator; no parallel verification.
                self._verification_header=''
                return super(CommerceTransport,self)._xhr(**kwargs)
            return super()._xhr(**kwargs)
    report.update(scope=binding,platformWrites=0,oldDatabaseWrites=0,identityFileWrites=0)
    with guard.readonly_guard(account,wait_seconds=15):
        report['guardAcquiredAt']=time.time()
        transport=Scoped(identity,account,allow_write=selection_scope is not None)
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
