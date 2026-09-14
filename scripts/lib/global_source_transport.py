"""Read-only opportunity source using the old verified HTTP protocol, no old lease writes."""
from contextlib import contextmanager
from pathlib import Path
import hashlib,importlib.util,sys,time
ROOT=Path(__file__).resolve().parents[2];LEGACY=ROOT.parent/'01-BDSystem-V2'
LIST='/api/v1/affiliate/partner/product/opportunity_product/list'
DETAIL='/api/v1/affiliate/partner/product/opportunity_product/campaign_detail'
SELECTED='/api/v1/affiliate/partner/product/pick_up/list'
CATEGORY='/api/v1/affiliate/lux/product/category/childrenv2'

@contextmanager
def opportunity_reader(report,*,stopped=lambda:False,extra_read_endpoints=frozenset()):
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
    cfg,account=account_for('it','acc6',check_maintenance=False)
    identity=identity_for('it',account=account,cfg=cfg).require_product_search()
    if not identity.partner_id_is_own:raise ValueError('source_identity_not_own')
    saved=json.loads((ROOT/'var/cycle-catalog-it-20260913/selected.json').read_text())['scope']
    binding={'market':'it','account':'acc6','institutionFingerprint':digest(str(identity.im_market_partner_id))}
    if saved!=binding:raise ValueError('source_institution_changed')
    path=Path(account.headers_json);before=hashlib.sha256(path.read_bytes()).hexdigest()
    spec=importlib.util.spec_from_file_location('source_readonly_guard',ROOT/'scripts/probe-italy-profile.py');guard=importlib.util.module_from_spec(spec);spec.loader.exec_module(guard)
    class ReadOnly(CommerceTransport):
        WRITE_ENDPOINTS=frozenset()
        READ_ENDPOINTS=frozenset({(LIST,'POST'),(DETAIL,'GET'),(CATEGORY,'POST'),(SELECTED,'POST')})|frozenset(extra_read_endpoints)
    report.update(scope=binding,platformWrites=0,oldDatabaseWrites=0,identityFileWrites=0)
    with guard.readonly_guard(account,wait_seconds=15):
        transport=ReadOnly(identity,account,allow_write=False)
        def check():
            if stopped():raise ValueError('source_stopped')
            if scheduled_relogin.maintenance_due(account,initialize=False,ignore_retry_throttle=True):raise ValueError('source_maintenance_due')
            if hashlib.sha256(path.read_bytes()).hexdigest()!=before:raise ValueError('source_identity_changed')
        transport.check_stop=check
        try:
            check();yield transport
        finally:
            transport.session.close();report.update(identityFileUnchanged=hashlib.sha256(path.read_bytes()).hexdigest()==before,
                verificationAttempts=transport.verification_attempts,verificationSuccesses=transport.verification_successes)
