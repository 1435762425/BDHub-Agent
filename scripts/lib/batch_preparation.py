"""Read-only capacity inspection of real cycle data; never grants send eligibility."""
from collections import Counter
import json
from lib.second_cycle import digest,assess_offer
from lib.cycle_materials import name_key

def inspect_preparation(store,plan,identity_reader,target):
    if type(target) is not int or not 1<=target<=100000:raise ValueError('numeric_target_required')
    scope=store._plan(plan);required=target+(target+9)//10
    offers={}
    for _,o in store._offers(plan):
        if assess_offer(o,store.clock())['eligible']:offers.setdefault(o['pid'],[]).append(o)
    eligible=store._eligible_people(plan);people={};reasons=Counter()
    names={r['id']:json.loads(r['payload']) for r in store.db.execute('SELECT id,payload FROM cycle_product_name')}
    cards={(r['offer_key'],r['offer_fingerprint']):json.loads(r['payload']) for r in store.db.execute('SELECT * FROM cycle_card_check WHERE plan_id=?',(plan,))}
    rows=store.db.execute('SELECT r.creator_id,r.oec,e.payload FROM cycle_identity_resolution r JOIN source_edge e USING(plan_id,source_id) WHERE r.plan_id=?',(plan,)).fetchall()
    for row in rows:
        edge=json.loads(row['payload'])
        if row['creator_id'] not in eligible:continue
        if edge.get('sourceKind')!='kalodata_http':reasons['historical_source_not_current_task']+=1;continue
        person=identity_reader(row['creator_id'],row['oec'])
        if not person:reasons['identity_not_verified']+=1;continue
        for o in offers.get(edge['pid'],[]):
            name=names.get(name_key(o));card=cards.get((o['offerKey'],digest(o)))
            located=bool(card and card.get('state')=='verified_read_only' and card.get('pid')==o['pid'] and card.get('sourceCampaignId')==o.get('campaignId') and card.get('creatorPercent')==o['creatorPercent'] and card.get('listId'))
            item={'oec':row['oec'],'pid':o['pid'],'offerKey':o['offerKey'],'name':name,'cardLocated':located,'creatorPercent':o['creatorPercent'],'units':edge.get('units',0)}
            rank=(int(bool(name))+int(located),edge.get('windowEnd',''),item['units'],o['offerKey'])
            prior=people.get(row['oec'])
            if not prior or rank>prior[0]:people[row['oec']]=(rank,item)
    selected=[x[1] for x in sorted(people.values(),key=lambda x:x[0],reverse=True)[:required]]
    groups={}
    for item in selected:
        key=item['offerKey'];g=groups.setdefault(key,{'pid':item['pid'],'offerKey':key,'shortName':item['name']['shortNameZh'] if item['name'] else None,'creatorPercent':item['creatorPercent'],'people':0,'cardLocated':item['cardLocated'],'nameReady':bool(item['name'])})
        g['people']+=1
    local=sum(bool(x['name']) and x['cardLocated'] for x in selected)
    return {'schema':'bdhub.batch-preparation.v1','market':scope['market'],'institution':scope['institution'],'target':target,'reserve':required-target,'required':required,
            'identityCandidates':len(people),'selectedCandidates':len(selected),'namedCandidates':sum(bool(x['name']) for x in selected),'cardLocatedCandidates':sum(x['cardLocated'] for x in selected),
            'localMaterialsComplete':local,'candidateGap':max(0,required-len(people)),'materialGap':required-local,'productGroups':len(groups),
            'namesNeeded':sum(not g['nameReady'] for g in groups.values()),'taplinksToCheckOrCreate':sum(not g['cardLocated'] for g in groups.values()),'products':list(groups.values())[:20],
            'scopeControlApplied':True,'liveEligibilityChecked':False,'taskCreated':False,'executionAllowed':False,'modelCalls':0,'platformWrites':0,'observedAt':store.clock(),
            'blockers':['full_batch_executor_not_connected','current_recipient_and_offer_preflight_required','global_opportunity_source_not_accepted'],
            'basis':'Local source, identity, relationship and card observations only. Located cards are not fresh dispatch approval.'}
