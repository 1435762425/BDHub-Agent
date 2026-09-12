"""Fixed card/list/member fixtures; no live account, old store or network calls."""
from copy import deepcopy
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from lib.italy_cards import PID,LIST_ID,CAMPAIGN_ID,HOST,CARD_PATH,MEMBERS_PATH,read_freegrin_card,combine_card_facts,legacy_params,ItalyCardsError
from lib.italy_im_auth import ImProbeDeadline

TIME='2026-09-12T12:00:00Z'
def member(**extra):return {'product_id':PID,'campaign_id':CAMPAIGN_ID,'product_name':'Freegrin fixture','product_status':2,'stock':'123',
    'creator_commission_percent':'1200','total_commission_percent':'1500','plan_commission_percent':'1000','is_under_governed':False,'unavailable_type':0,**extra}
def listing(**extra):return {'product_list_id':LIST_ID,'product_list_name':'Freegrin list','campaign_id':'0','campaign_name':'',
    'campaign_products':[{'product_id':PID,'stock':'125','creator_commission_percent':'1200'}],**extra}
def responses():return [{'code':0,'data':{'total':1,'list':[listing()]}},{'code':0,'data':{'total_num':1,'campaign_products':[member()]}}]
def identity():return SimpleNamespace(market='it',host=HOST,aid='360019',partner_id='111',im_market_partner_id='222',user_language='zh-CN',partner_id_is_own=True)
class Clock:
    def __init__(self):self.value=0
    def now(self):return self.value
    def sleep(self,seconds):self.value+=seconds
class HTTP:
    def __init__(self,payloads=None,*,status=200,verification=False,error=None):self.payloads=payloads or responses();self.calls=[];self.status=status;self.verification=verification;self.error=error
    def get(self,url,**kwargs):
        self.calls.append((url,kwargs))
        if self.error:raise self.error
        value=deepcopy(self.payloads[len(self.calls)-1])
        return SimpleNamespace(status_code=self.status,headers={'bdturing-verify':'PRIVATE_VERIFY'} if self.verification else {},json=lambda:value)
def probe(http=None,**kwargs):
    timer=Clock();report={};http=http or HTTP()
    result=read_freegrin_card(SimpleNamespace(name='acc6',enabled=True),identity(),{'Cookie':'PRIVATE_COOKIE','User-Agent':'fixture'},report,http=http,
        params_factory=lambda i,a:{'partner_id':i.im_market_partner_id,'aid':i.aid},monotonic=timer.now,sleep=timer.sleep,wall_clock=lambda:TIME,**kwargs)
    return result,report,http,timer

class ItalyCardsTests(unittest.TestCase):
    def test_exact_get_contract_uses_product_identity_and_membership_source_two(self):
        facts,report,http,timer=probe()
        self.assertEqual([url for url,_ in http.calls],[HOST+CARD_PATH,HOST+MEMBERS_PATH]);self.assertEqual(timer.value,1)
        self.assertEqual(http.calls[0][1]['params'],{'partner_id':'222','aid':'360019','cur_page':1,'page_size':20,'version':1,'search_type':2,'key_word':PID})
        self.assertEqual(http.calls[1][1]['params'],{'partner_id':'222','aid':'360019','list_id':LIST_ID,'source':2})
        self.assertTrue(all(not q['allow_redirects'] for _,q in http.calls));self.assertFalse(report['environmentProxy'])
        self.assertTrue(facts['bindingVerified']);self.assertEqual(facts['card']['imListCampaignId'],'0');self.assertEqual(facts['card']['sourceCampaignId'],CAMPAIGN_ID)
        self.assertIsNone(facts['imProduct']['campaignId']);self.assertEqual(facts['membershipProduct']['campaignId'],CAMPAIGN_ID)
        self.assertEqual(facts['bindingEvidence'],'im_list_and_product_list_membership')
        self.assertNotIn('PRIVATE',json.dumps(facts)+json.dumps(report));self.assertNotIn('partner_id',json.dumps(report));self.assertEqual(facts['platformWrites'],0)

    def test_real_rates_are_scaled_exactly_and_unknown_does_not_use_historical_quote(self):
        facts,_,_,_=probe();current=facts['membershipProduct']
        self.assertEqual(current['creatorCommission'],{'state':'observed','rawHundredthsOfPercent':'1200','percent':'12'})
        self.assertEqual(current['publicCommission']['percent'],'10');self.assertEqual(current['totalCommission']['percent'],'15')
        data=responses();data[1]['data']['campaign_products'][0]['creator_commission_percent']=None
        changed,_,_,_=probe(HTTP(data));self.assertEqual(changed['membershipProduct']['creatorCommission']['state'],'missing');self.assertIsNone(changed['membershipProduct']['creatorCommission']['percent'])
        data[1]['data']['campaign_products'][0]['creator_commission_percent']='1250.5'
        changed,_,_,_=probe(HTTP(data));self.assertEqual(Decimal(changed['membershipProduct']['creatorCommission']['percent']),Decimal('12.505'))

    def test_no_minimum_commission_or_sample_or_stock_qualification_is_invented(self):
        data=responses();data[1]['data']['campaign_products'][0].update(creator_commission_percent='0',plan_commission_percent='0',stock='0',sample_quota=0,is_under_governed=True)
        facts,_,_,_=probe(HTTP(data));self.assertTrue(facts['bindingVerified']);self.assertEqual(facts['membershipProduct']['stock']['value'],'0')
        self.assertEqual(facts['membershipProduct']['creatorCommission']['percent'],'0');self.assertTrue(facts['membershipProduct']['isUnderGoverned'])
        self.assertNotIn('sample_quota',json.dumps(facts));self.assertNotIn('sendReady',facts)

    def test_wrong_or_malformed_campaign_and_wrong_list_never_gain_verified_binding(self):
        for alter in (lambda r:r['data']['list'][0]['campaign_products'][0].update(campaign_id='999'),
                      lambda r:r['data']['list'][0]['campaign_products'][0].update(campaign_id='PRIVATE_BAD_CAMPAIGN'),
                      lambda r:r['data']['list'][0].update(product_list_id='999'),lambda r:r['data']['list'][0].update(campaign_id='999')):
            data=responses();alter(data[0]);facts,_,_,_=probe(HTTP(data));self.assertFalse(facts['bindingVerified']);self.assertNotIn('PRIVATE_BAD_CAMPAIGN',json.dumps(facts))
        data=responses();data[1]['data']['campaign_products'][0]['campaign_id']='999'
        self.assertFalse(probe(HTTP(data))[0]['bindingVerified'])

    def test_explicit_zero_im_list_is_empty_but_absent_list_is_not_silently_empty(self):
        data=responses();data[0]['data']={'total':0};facts,_,http,_=probe(HTTP(data));self.assertFalse(facts['bindingVerified']);self.assertEqual(len(http.calls),2)
        data[0]['data']={}
        with self.assertRaises(ItalyCardsError) as found:probe(HTTP(data))
        self.assertEqual(found.exception.code,'card_list_incomplete')

    def test_membership_pagination_checks_total_and_duplicates_without_changing_scope(self):
        data=responses();data[1]['data'].update(total_num=2,next_cursor='next');data.append({'code':0,'data':{'total_num':2,'campaign_products':[member(product_id='999',campaign_id='888')]}})
        facts,_,http,_=probe(HTTP(data));self.assertTrue(facts['bindingVerified']);self.assertEqual(http.calls[2][1]['params']['cursor'],'next')
        data[2]['data']['total_num']=3
        with self.assertRaises(ItalyCardsError) as found:probe(HTTP(data))
        self.assertEqual(found.exception.code,'card_members_changed')
        data[2]['data']={'total_num':2,'campaign_products':[member()]}
        with self.assertRaises(ItalyCardsError) as found:probe(HTTP(data))
        self.assertEqual(found.exception.code,'card_members_duplicate')

    def test_verification_redirect_and_private_transport_errors_stop_without_retry(self):
        for http,code in ((HTTP(verification=True),'card_verification_required'),(HTTP(status=302),'card_redirect_rejected'),(HTTP(error=RuntimeError('PRIVATE_ERROR')),'card_transport_error')):
            with self.assertRaises(ItalyCardsError) as found:probe(http)
            self.assertEqual(found.exception.code,code);self.assertEqual(len(http.calls),1);self.assertNotIn('PRIVATE',str(found.exception))
        http=HTTP(error=ImProbeDeadline())
        with self.assertRaises(ImProbeDeadline):probe(http)
        self.assertEqual(len(http.calls),1)

    def test_maintenance_blocks_before_http_and_raw_unexpected_fields_are_discarded(self):
        http=HTTP()
        with self.assertRaises(ItalyCardsError):probe(http,maintenance_due=lambda:True)
        self.assertEqual(http.calls,[])
        data=responses();data[0]['data']['list'][0]['url']='https://private.invalid';data[1]['data']['campaign_products'][0].update(cookie='PRIVATE_COOKIE',contact='PRIVATE_CONTACT')
        facts,_,_,_=probe(HTTP(data));self.assertNotIn('PRIVATE',json.dumps(facts));self.assertNotIn('https://private',json.dumps(facts))

    @unittest.skipUnless(importlib.util.find_spec('yaml'),'legacy Python dependencies not available in this interpreter')
    def test_common_params_reuse_the_existing_legacy_method_without_constructing_a_transport(self):
        result=legacy_params(identity(),SimpleNamespace(fp='fixture-fp',device_id='fixture-device'))
        self.assertEqual(result['partner_id'],'222');self.assertEqual(result['aid'],'360019');self.assertEqual(result['fp'],'fixture-fp');self.assertEqual(result['device_id'],'fixture-device')
        self.assertEqual(result['app_name'],'i18n_ecom_alliance');self.assertEqual(result['timezone_name'],'Asia/Shanghai')

if __name__=='__main__':unittest.main()
