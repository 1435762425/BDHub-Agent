"""Non-secret market assignments and read-only status. No worker starts or credential writes."""
import json,re,sqlite3,time
from contextlib import closing
from pathlib import Path

def validate_config(value):
    if not isinstance(value,dict) or type(value.get('schemaVersion')) is not int or value.get('schemaVersion')!=1 or not isinstance(value.get('markets'),dict):raise ValueError('account_config_invalid')
    used=set()
    for market,pair in value['markets'].items():
        accounts=pair.get('accounts');roles=pair.get('roles') or {}
        if not re.fullmatch(r'[a-z]{2}',market) or not isinstance(accounts,list) or len(accounts)!=2 or len(set(accounts))!=2:raise ValueError('market_requires_two_distinct_accounts')
        if any(not isinstance(a,str) or not re.fullmatch(r'acc[1-9][0-9]*',a) or a in used for a in accounts):raise ValueError('account_assigned_to_multiple_markets_or_invalid')
        if set(roles)!= {'communications','supply'} or set(roles.values())!=set(accounts):raise ValueError('account_role_assignment_invalid')
        authority=pair.get('credentialAuthority');executor=pair.get('maintenanceExecutor')
        if (authority,executor) not in {('legacy_readonly','legacy_lifecycle'),
                                       ('project_owned','agent_identity_generation')}:
            raise ValueError('credential_handoff_not_implemented')
        if pair.get('automaticRoleSwitchEnabled') is not False:raise ValueError('automatic_role_switch_not_implemented')
        used.update(accounts)
    lifecycle=value.get('lifecycle',{})
    project_owned=any(pair.get('credentialAuthority')=='project_owned' for pair in value['markets'].values())
    if lifecycle.get('enableNewMaintenanceWorker') is not project_owned:
        raise ValueError('duplicate_maintenance_executor_forbidden')
    if lifecycle.get('healthPollMinutes') is not None or lifecycle.get('deepCheckHours') is not None:raise ValueError('periodic_health_checks_disabled')
    if lifecycle.get('standbyEarlyMaintenanceHours')!=0:raise ValueError('early_maintenance_disabled')
    if any(type(lifecycle.get(k)) is not int or lifecycle[k]<=0 for k in ('identityRefreshHours','loginMaintenanceHours')):raise ValueError('maintenance_interval_invalid')
    return value

def load_config(root):
    value=validate_config(json.loads((Path(root)/'config/market-accounts.json').read_text()))
    registry_path=Path(root)/'config/markets.json'
    if registry_path.exists():
        from lib.market_registry import load_registry,operational_market_keys
        registry=load_registry(root);enabled=set(operational_market_keys(root))
        if set(value['markets'])!=enabled:raise ValueError('market_account_registry_mismatch')
        for key,pair in value['markets'].items():
            if pair.get('roles')!=registry['markets'][key]['accounts']:
                raise ValueError('market_account_role_registry_mismatch')
            expected={'campaign':registry['markets'][key]['capabilities']['campaignCatalog'],
                      'fullManaged':registry['markets'][key]['capabilities']['fullManagedCatalog']}
            if pair.get('catalogCapabilities')!=expected:raise ValueError('market_catalog_capability_mismatch')
    return value

def _published_capabilities(root,market,account):
    path=Path(root)/'var/second-cycle.sqlite'
    if not path.exists():return None
    try:
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=5)) as db:
            row=db.execute("SELECT generation_id FROM account_identity_generation WHERE market=? AND account=? AND state='published' ORDER BY published_at DESC,rowid DESC LIMIT 1",(market,account)).fetchone()
            if not row:return None
            return {name:state for name,state in db.execute(
                'SELECT capability,state FROM account_capability_observation WHERE generation_id=?',(row[0],))}
    except (OSError,sqlite3.Error):
        return None

def catalog_read_account(root,pinned=None,market='it'):
    pair=load_config(root)['markets'].get(market)
    if not pair:raise ValueError('market_account_assignment_missing')
    account=pinned or pair['roles']['supply']
    if account not in pair['accounts']:raise ValueError('catalog_account_outside_assignment')
    capabilities=_published_capabilities(root,market,account)
    if capabilities is not None:
        if capabilities.get('catalog_read')!='verified':raise ValueError('catalog_account_not_verified')
        return account
    evidence=evidence_summary(root,pair,market)
    if evidence.get('state')!='verified_readonly' or evidence['accounts'].get(account,{}).get('capabilities',{}).get('catalog_read')!='verified':raise ValueError('catalog_account_not_verified')
    return account

def catalog_scope(root,account,market='it'):
    if market=='it':
        scope=json.loads((Path(root)/'var/cycle-catalog-it-20260913/selected.json').read_text())['scope']
    else:
        from lib.legacy_runtime import configure_vendored_bdhub
        configure_vendored_bdhub(root=Path(root),legacy_root=Path(root).parent/'01-BDSystem-V2')
        from bdhub.hub.markets import identity_for
        from bdhub.send.taplink.transport import account_for
        from lib.second_cycle import digest
        cfg,registered=account_for(market,account,check_maintenance=False)
        identity=identity_for(market,account=registered,cfg=cfg).require_product_search()
        scope={'market':market,'institutionFingerprint':digest(str(identity.im_market_partner_id))}
    return scope|{'account':account}

def evidence_summary(root,pair,market='it'):
    path=(Path(root)/pair['validationEvidence']).resolve()
    if not path.is_relative_to((Path(root)/'var').resolve()):raise ValueError('evidence_path_invalid')
    if not path.exists():return {'state':'missing','accounts':{}}
    evidence=json.loads(path.read_text());rows=evidence.get('accounts',[])
    passed=evidence.get('passed') is True and evidence.get('sameInstitution') is True and evidence.get('sameMarket') is True
    passed=passed and len(rows)==2 and {r.get('account') for r in rows}==set(pair['accounts']) and all(r.get('market')==market and r.get('identityFileUnchanged') is True and r.get('state')=='passed_readonly' for r in rows)
    passed=passed and isinstance(evidence.get('independentGuardsOverlapSeconds'),(int,float)) and evidence['independentGuardsOverlapSeconds']>0
    if not passed:return {'state':'not_verified','accounts':{}}
    return {'state':'verified_readonly','sameSender':evidence.get('sameSender') is True,'sharedCardCount':len(evidence.get('sharedCardIds',[])),
        'concurrentReadSeconds':evidence.get('independentGuardsOverlapSeconds'),
        'accounts':{r['account']:{'checkedAt':r['startedAt'],'capabilities':r['capabilities'],'tokenHasExplicitExpiry':bool(r.get('tokenExpiryFields'))} for r in rows}}

def maintenance_plan(*,now,last_success,role,active_writes,other_maintaining,policy,operation='login'):
    """Pure proposed lifecycle policy, not an enabled maintenance worker."""
    if operation not in ('login','identity_refresh'):raise ValueError('maintenance_operation_invalid')
    if last_success is None:return {'decision':'verify_baseline','execute':False}
    interval=policy['loginMaintenanceHours' if operation=='login' else 'identityRefreshHours']
    if type(interval) is not int or interval<=0:raise ValueError('maintenance_interval_invalid')
    hard_due=last_success+interval*3600
    if now<hard_due:return {'decision':'not_due','nextDue':hard_due,'hardDue':hard_due,'execute':False}
    if active_writes:return {'decision':'drain_inflight','hardDue':hard_due,'execute':False}
    if other_maintaining:return {'decision':'wait_maintenance_slot','hardDue':hard_due,'execute':False}
    return {'decision':'maintenance_ready','hardDue':hard_due,'execute':False}

def route_proposal(pair,task,accounts,*,paused=False,pinned_account=None,result_unknown=False):
    """Policy simulation for design/tests; always returns execute=false until adapters are migrated."""
    if result_unknown:
        return {'state':'verify_original' if pinned_account in pair['accounts'] else 'original_binding_required','account':pinned_account,'execute':False}
    if paused:return {'state':'paused','account':None,'execute':False}
    primary=pair['roles']['communications' if task in ('im_read','send','reply') else 'supply']
    order=[pinned_account] if pinned_account else [primary]+[a for a in pair['accounts'] if a!=primary]
    for name in order:
        if name not in pair['accounts']:continue
        status=accounts.get(name,{})
        if status.get('health')=='healthy' and not status.get('busy') and status.get('capabilities',{}).get(task)=='verified':
            return {'state':'proposed','account':name,'execute':False}
    return {'state':'waiting_verified_account','account':None,'execute':False}
