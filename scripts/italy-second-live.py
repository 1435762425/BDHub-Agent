#!/usr/bin/env python3
"""Prepare a reviewable IT card+template trial; writes need explicit approval."""
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime,timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import sys
from lib.second_outreach import SecondOutreachStore,fingerprint
from lib.second_card_binding import card_from_facts,load_card_facts,commercial_facts_from_facts,validate_promotion_claims
from lib.second_live_trial import LiveTrialStore,LiveTrialError
from lib.second_live_runtime import read_sender_binding,live_runtime
from lib.second_live_runner import run_trial

ROOT=Path(__file__).resolve().parents[1];VAR=ROOT/'var'

def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.tmp')
    with temporary.open('w') as file:json.dump(value,file,ensure_ascii=False,indent=2);file.write('\n')
    temporary.chmod(0o600);temporary.replace(path)

def scope_path(trial_id):
    if not re.fullmatch(r'second_live_trial_[a-f0-9]{32}',trial_id):raise ValueError('trial_id_invalid')
    return VAR/'second-live'/f'{trial_id}.json'

def prepare(opportunity_id,template_id,card_path,request_id):
    facts=load_card_facts(card_path);card=card_from_facts(facts)
    with SecondOutreachStore() as source:
        source.sync();opportunity=source.get(opportunity_id)
        draft=source.command(request_id+':template',{'type':'render_template','opportunityId':opportunity_id,'expectedRevision':opportunity['revision'],'templateId':template_id})
        source.template_draft(draft['draftId'],require_current=True)
    if not draft.get('requiresCard') or draft.get('deliveryOrder')!='card_then_text' or draft['requiredCardPids']!=[card.product_id]:
        raise ValueError('all_required_cards_must_be_verified')
    validate_promotion_claims(draft.get('promotionClaims',[]),commercial_facts_from_facts(facts))
    auth_report={};sender_hash=read_sender_binding(auth_report)
    approval_facts={'senderBindingHash':sender_hash,'templateContextFingerprint':draft['contextFingerprint'],
                    'cardBindingSha256':card.binding_sha256,'opportunityFingerprint':draft['sourceFingerprint']}
    input_item={'opportunityId':opportunity_id,'creatorId':draft['recipient']['creatorId'],'oecId':draft['recipient']['oecId'],
                'handle':draft['recipient']['handle'],'draftId':draft['draftId'],'textIt':draft['textIt'],'translationZh':draft['translationZh'],
                'contextFingerprint':draft['contextFingerprint'],'cards':[asdict(card)]}
    with LiveTrialStore(VAR) as store:trial=store.create_trial(request_id,[input_item],fingerprint(approval_facts))
    scope={'schema':'bdhub.second-live-scope.v1','trialId':trial['trialId'],'snapshotHash':trial['snapshotHash'],
           'approvalFacts':approval_facts,'cardFactsPath':str(Path(card_path).resolve().relative_to(VAR.resolve())),
           'commercialFacts':commercial_facts_from_facts(facts),'authReport':auth_report}
    path=scope_path(trial['trialId'])
    if path.exists():
        original=json.loads(path.read_text())
        if original['snapshotHash']!=scope['snapshotHash'] or original['approvalFacts']!=approval_facts:raise ValueError('scope_conflict')
    else:save(path,scope)
    item=trial['snapshot']['items'][0]
    review=(f"# 意大利二发实测批次\n\n状态：未批准、未发送。账号 ACC6，1 位达人，1 张商品卡 + 1 条固定话术。\n\n"
            f"- 达人：@{item['handle']}\n- 商品：Freegrin 口腔片\n- PID：{card.product_id}\n"
            f"- 顺序：卡片确认后才发文字；卡片不明则停止，已确认卡不重发。\n- 模板：{draft['templateName']} v{draft['templateVersion']}，每人生成 Token 为 0。\n\n"
            f"意大利语正文：\n\n{item['textIt']}\n\n中文辅助：\n\n{item['translationZh']}\n\n"
            f"批次：`{trial['trialId']}`\n\n快照：`{trial['snapshotHash']}`\n\n有效期：{trial['expiresAt']}（UTC）。过期不能直接执行。\n")
    path.with_suffix('.md').write_text(review)
    return {'trialId':trial['trialId'],'snapshotHash':trial['snapshotHash'],'approved':trial['approved'],'expiresAt':trial['expiresAt'],
            'handle':item['handle'],'components':len(item['cards'])+1,'review':str(path.with_suffix('.md'))}

def execute(trial_id):
    scope=json.loads(scope_path(trial_id).read_text())
    with LiveTrialStore(VAR) as store:
        trial=store.get_trial(trial_id)
        if scope['snapshotHash']!=trial['snapshotHash'] or fingerprint(scope['approvalFacts'])!=trial['snapshot']['sourceFingerprint']:
            raise ValueError('scope_conflict')
        reports=[]
        def validate(item):
            with SecondOutreachStore() as source:draft=source.template_draft(item['draftId'],require_current=True)
            if (draft['contextFingerprint']!=item['contextFingerprint'] or draft['textIt']!=item['textIt'] or draft['translationZh']!=item['translationZh']
                    or draft['recipient']['oecId']!=item['oecId'] or draft['recipient']['creatorId']!=item['creatorId'] or not draft.get('requiresCard')
                    or draft['requiredCardPids']!=[card['product_id'] for card in item['cards']]):raise ValueError('source_changed')
        @contextmanager
        def runtime(item,*,read_only=False):
            report={'startedAt':datetime.now(timezone.utc).isoformat()};reports.append(report)
            with SecondOutreachStore() as source:message=source.template_draft(item['draftId'])
            claims=message.get('promotionClaims',[])
            def stopped():
                if read_only:return False
                current=store.get_trial(trial_id)
                return current['paused'] or current['expired'] or not current['approved']
            try:
                with live_runtime(scope['approvalFacts']['senderBindingHash'],report,stopped=stopped) as value:
                    original=value['validate_card']
                    def validated(card):
                        current=original(card)
                        validate_promotion_claims([claim for claim in claims if claim['pid']==card.product_id],report['cardRefreshes'][-1]['commercialFacts'])
                        return current
                    def validate_text():
                        from lib.italy_im_delivery import ItalyVerifiedProductCard
                        for frozen_card in item['cards']:validated(ItalyVerifiedProductCard(**frozen_card))
                    yield {**value,'validate_card':validated,'validate_text':validate_text}
            finally:report['finishedAt']=datetime.now(timezone.utc).isoformat()
        try:result=run_trial(store,trial_id,runtime_factory=runtime,validate_item=validate)
        finally:save(scope_path(trial_id).with_suffix('.runtime.json'),{'trialId':trial_id,'reports':reports})
        save(scope_path(trial_id).with_suffix('.result.json'),result)
        return {'status':result['status'],'trialId':trial_id,'items':[{'itemId':item['itemId'],'state':item['state'],'partialDelivery':item['partialDelivery']} for item in result['trial']['items']]}

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('command',choices=['prepare','show','approve','run'])
    parser.add_argument('--opportunity-id');parser.add_argument('--template-id',default='it-second-brief');parser.add_argument('--card-facts',type=Path)
    parser.add_argument('--request-id');parser.add_argument('--trial-id');parser.add_argument('--expected-hash');args=parser.parse_args()
    def timeout(*_):raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM,timeout);signal.signal(signal.SIGALRM,timeout);signal.alarm(300)
    try:
        if args.command=='prepare':
            if not all([args.opportunity_id,args.card_facts,args.request_id]):raise ValueError('prepare_fields_required')
            result=prepare(args.opportunity_id,args.template_id,args.card_facts,args.request_id)
        elif args.command=='run':result=execute(args.trial_id)
        else:
            with LiveTrialStore(VAR) as store:
                trial=store.approve(args.trial_id,args.expected_hash) if args.command=='approve' else store.get_trial(args.trial_id)
                result={'trialId':trial['trialId'],'snapshotHash':trial['snapshotHash'],'approved':trial['approved'],'paused':trial['paused'],'expired':trial['expired'],'items':trial['items']}
        print(json.dumps(result,ensure_ascii=False));return 0
    except (KeyboardInterrupt,SystemExit):return 130
    except Exception as error:
        code=getattr(error,'code',None)
        print(json.dumps({'error':code if isinstance(code,str) and re.fullmatch(r'[a-z0-9_]+',code) else 'second_live_failed'}));return 1
    finally:signal.alarm(0)

if __name__=='__main__':raise SystemExit(main())
