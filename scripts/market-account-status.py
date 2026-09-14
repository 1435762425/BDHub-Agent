#!/usr/bin/env python3
"""Local account-center readback. Does not refresh identity or claim accounts."""
import json,sys,time
from datetime import datetime,timedelta
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.market_accounts import load_config,evidence_summary

def status():
    data=load_config(ROOT);sys.path.insert(0,str(ROOT.parent/'01-BDSystem-V2'))
    from bdhub import config,scheduled_relogin
    from bdhub.enrich.profile_lease import ProfileLease
    accounts={a.name:a for a in config.load_accounts(config.load())};result=[]
    for market,pair in data['markets'].items():
        evidence=evidence_summary(ROOT,pair,market);rows=[]
        profiles=[str(Path(accounts[a].profile_dir).resolve()) for a in pair['accounts'] if a in accounts]
        if len(profiles)!=len(set(profiles)):raise ValueError('market_accounts_share_profile')
        for name in pair['accounts']:
            row={'account':name,'role':next(r for r,a in pair['roles'].items() if a==name),'evidence':evidence['accounts'].get(name),'runtimeMigration':'pending'}
            a=accounts.get(name)
            if a is None:row.update(state='missing_config')
            else:
                lease=ProfileLease(a.profile_dir,account=name,market=market,operation='agent-account-center-read')
                owner=lease.public_active_owner();mt=scheduled_relogin.public_account_state(a)
                row.update(state=mt['scheduled_relogin_state'],enabled=a.enabled,legacyLeaseBusy=lease.has_active_owner(),
                    legacyOperation=owner.get('operation') if owner else None,lastLogin=mt['last_scheduled_relogin_at'],nextMaintenance=mt['scheduled_relogin_due_at'],
                    identityUpdatedAt=Path(a.headers_json).stat().st_mtime if Path(a.headers_json).exists() else None)
                row['plannedLoginMaintenance']=(datetime.fromisoformat(row['lastLogin'])+timedelta(hours=data['lifecycle']['loginMaintenanceHours'])).isoformat() if row['lastLogin'] else None
            rows.append(row)
        result.append({'market':market,'state':pair['assignmentState'],'accounts':rows,'pairEvidence':{k:v for k,v in evidence.items() if k!='accounts'},'maintenanceExecutor':pair['maintenanceExecutor'],'autoSwitchEnabled':False})
    return {'markets':result,'lifecycle':data['lifecycle'],'checkedAt':time.time(),'executionEnabled':False,'realSends':0}

if __name__=='__main__':print(json.dumps(status(),ensure_ascii=False))
