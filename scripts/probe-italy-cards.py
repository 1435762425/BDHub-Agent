#!/usr/bin/env python3
"""Read only current ACC6/IT Freegrin card and membership facts into new var."""
from datetime import datetime,timezone
import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from contextlib import redirect_stdout,redirect_stderr
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
LEGACY=Path('/Users/bjn00003/BDHub/01-BDSystem-V2')
VAR=ROOT/'var'
sys.path.insert(0,str(ROOT/'scripts'))
from lib.italy_cards import read_freegrin_card,ItalyCardsError,PID,LIST_ID,CAMPAIGN_ID
from lib.italy_im_auth import ImProbeDeadline


def write_json(path,value):
    tmp=path.with_suffix('.tmp');fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as out:json.dump(value,out,ensure_ascii=False,indent=2);out.write('\n')
    tmp.replace(path)


def runtime():
    sys.dont_write_bytecode=True;sys.path.insert(0,str(LEGACY))
    from bdhub import scheduled_relogin
    from bdhub.enrich.identity_store import load_identity
    from bdhub.hub.markets import identity_for
    from bdhub.send.taplink.transport import account_for
    cfg,account=account_for('it','acc6',check_maintenance=False)
    identity=identity_for('it',account=account,cfg=cfg).require_product_search()
    if not identity.partner_id_is_own:raise ItalyCardsError('card_identity_invalid')
    spec=importlib.util.spec_from_file_location('cards_readonly_guard',ROOT/'scripts/probe-italy-profile.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return account,identity,load_identity,module.readonly_guard,lambda:scheduled_relogin.maintenance_due(account,initialize=False,ignore_retry_throttle=True)


def child(output,*,runtime_loader=runtime,reader=read_freegrin_card):
    report={'schema':'bdhub.italy-card-probe.v1','market':'it','account':'acc6','status':'starting','startedAt':datetime.now(timezone.utc).isoformat(),
        'requests':[],'expectedPid':PID,'expectedListId':LIST_ID,'expectedCampaignId':CAMPAIGN_ID,'platformWrites':0,'legacyDatabaseWrites':0,'identityFileWrites':0,'sendRequests':0,'browserInitializations':0,'wallDeadlineSeconds':50}
    save=lambda:write_json(output/'report.json',report)
    identity_path=before=None;phase='runtime';previous=signal.getsignal(signal.SIGALRM)
    def expired(*_):raise ImProbeDeadline()
    save();signal.signal(signal.SIGALRM,expired);signal.setitimer(signal.ITIMER_REAL,50)
    try:
        with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            account,identity,load_identity,guard,maintenance=runtime_loader();phase='identity'
            identity_path=Path(account.headers_json);before=hashlib.sha256(identity_path.read_bytes()).digest();phase='guard'
            with guard(account):
                phase='identity'
                if hashlib.sha256(identity_path.read_bytes()).digest()!=before:raise ItalyCardsError('card_identity_invalid')
                if maintenance():raise ItalyCardsError('card_maintenance_due')
                headers=load_identity(identity_path).headers;phase='read'
                facts=reader(account,identity,headers,report,maintenance_due=maintenance,on_update=save)
                write_json(output/'card-facts.json',facts)
                report.update(status='completed',factsFile='card-facts.json')
    except ImProbeDeadline:report.update(status='bounded_timeout',errorCode='wall_timeout')
    except ItalyCardsError as error:report.update(status='blocked',errorCode=error.code)
    except BlockingIOError:report.update(status='blocked',errorCode='guard_busy')
    except FileNotFoundError:report.update(status='blocked',errorCode='guard_missing' if phase=='guard' else 'card_runtime_unavailable')
    except RuntimeError as error:report.update(status='blocked',errorCode='guard_busy' if str(error)=='account_in_use' else 'card_runtime_unavailable')
    except Exception:report.update(status='blocked',errorCode='card_runtime_unavailable')
    finally:
        signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,previous)
        if identity_path is not None and before is not None:
            try:report['identityFileUnchanged']=hashlib.sha256(identity_path.read_bytes()).digest()==before
            except Exception:report['identityFileUnchanged']=False
            if not report['identityFileUnchanged']:report.update(status='blocked',errorCode='card_identity_invalid')
        report['finishedAt']=datetime.now(timezone.utc).isoformat();save()
    return 0 if report['status']=='completed' else 2


def output_path(value):
    path=Path(value).resolve()
    if path==VAR.resolve() or not path.is_relative_to(VAR.resolve()):raise ValueError('output must remain in new project var')
    return path


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account',choices=['acc6'],default='acc6');parser.add_argument('--pid',choices=[PID],default=PID)
    parser.add_argument('--output',type=Path);parser.add_argument('--network-child',action='store_true');args=parser.parse_args(argv)
    output=output_path(args.output or VAR/('italy-card-facts-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid4().hex[:8]))
    if args.network_child:
        if not output.is_dir() or (output/'report.json').exists():raise ValueError('child output missing or already used')
        return child(output)
    output.mkdir(mode=0o700,parents=True,exist_ok=False)
    process=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--network-child','--account','acc6','--pid',PID,'--output',str(output)],
        env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},start_new_session=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    timed_out=False;previous=signal.getsignal(signal.SIGTERM)
    def cancelled(*_):raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM,cancelled)
    try:
        try:process.wait(timeout=55)
        except subprocess.TimeoutExpired:timed_out=True
    finally:
        try:os.killpg(process.pid,signal.SIGTERM)
        except ProcessLookupError:pass
        try:process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            process.wait()
        signal.signal(signal.SIGTERM,previous)
    path=output/'report.json';report=json.loads(path.read_text()) if path.exists() else {'status':'blocked','errorCode':'child_no_report'}
    if timed_out:report.update(status='bounded_timeout',errorCode='wall_timeout')
    if timed_out or not path.exists():write_json(path,report)
    print(json.dumps({'status':report.get('status'),'errorCode':report.get('errorCode'),'bindingVerified':report.get('bindingVerified'),
        'readRequests':len(report.get('requests',[])),'platformWrites':0,'sendRequests':0,'report':str(path)},ensure_ascii=False))
    return 0 if report.get('status')=='completed' else 2


if __name__=='__main__':raise SystemExit(main())
