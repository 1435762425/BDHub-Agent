import hashlib
import json
from pathlib import Path
import shutil
import smtplib
import ssl
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.business_daily import _activity, _inventory, _leads, collect, read_db, render, ratio, FIELDS
from lib.business_daily_mail import capture, configuration, deliver, save_json, tick
from lib.catalog_binding import offer_fingerprint
from lib.cycle_stats import day_bounds

DAY = '2026-10-03'
START, END = day_bounds(DAY)


def activity_db(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript('''
      CREATE TABLE cycle_delivery(id TEXT PRIMARY KEY,plan_id,oec,pid,state,snapshot);
      CREATE TABLE cycle_delivery_part(delivery_id,kind,state,started);
      CREATE TABLE inbox_event(plan_id,oec,cid,message_id,kind,occurred_ms,historical);
      CREATE TABLE service_reply(plan_id,oec,state,started);
      CREATE TABLE service_case(plan_id,reason,state);
    ''')
    for identity, oec, pid, list_id, at, state, text in (
        ('d1', 'a', '1', '100', START+10, 'confirmed', 'confirmed'),
        ('d2', 'a', '1', '100', START+20, 'confirmed', 'confirmed'),
        ('d3', 'b', '2', '101', START+40, 'partial_delivery', 'unknown'),
        ('d4', 'c', '3', '102', START+50, 'unknown', 'unknown'),
        ('d5', 'd', '4', '103', END+10, 'confirmed', 'confirmed'),
    ):
        snapshot = json.dumps({'conversationId': 'cid-'+oec, 'card': {'listId': list_id}})
        db.execute('INSERT INTO cycle_delivery VALUES(?,?,?,?,?,?)', (identity,'p',oec,pid,state,snapshot))
        db.execute('INSERT INTO cycle_delivery_part VALUES(?,?,?,?)', (identity,'card','unknown' if identity=='d4' else 'confirmed',at))
        db.execute('INSERT INTO cycle_delivery_part VALUES(?,?,?,?)', (identity,'text',text,at+1))
    for mid, oec, cid, kind, at, historical in (
        ('before','a','cid-a','creatorReplies',START+1,0),
        ('reply','a','cid-a','creatorReplies',START+60,0),
        ('reply-again','a','cid-a','creatorReplies',START+65,0),
        ('wrong-cid','a','other','showcaseNotifications',START+65,0),
        ('show','a','cid-a','showcaseNotifications',START+70,0),
        ('history','a','cid-a','showcaseNotifications',START+80,1),
        ('unknown-send','c','cid-c','showcaseNotifications',START+90,0),
        ('future','a','cid-a','showcaseNotifications',END,0),
        ('no-time','a','cid-a','showcaseNotifications',None,0),
    ):
        db.execute('INSERT INTO inbox_event VALUES(?,?,?,?,?,?,?)', ('p',oec,cid,mid,kind,None if at is None else int(at*1000),historical))
    db.execute("INSERT INTO service_reply VALUES('p','a','confirmed',?)", (START+70,))
    db.execute("INSERT INTO service_reply VALUES('p','a','confirmed',?)", (START+80,))
    db.execute("INSERT INTO service_reply VALUES('p','b','unknown',?)", (START+90,))
    db.executemany('INSERT INTO service_case VALUES(?,?,?)', [('p','paid_or_budget','open'),('p','card_result_unknown','open')])
    db.commit()
    return db


class MetricsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/'config').mkdir()
        for name in ('markets.json','market-content.json','link-naming-de.json'):
            shutil.copy(ROOT/'config'/name, self.root/'config'/name)

    def test_missing_state_stays_unknown_and_read_creates_no_var(self):
        seen = []
        def no_pool(root, **kw):
            seen.append(kw['market'])
            return {'available': False}
        report = collect(self.root, DAY, now=END+1, pool_reader=no_pool)
        self.assertEqual(set(seen), {'br','de','fr','my','th'})
        self.assertEqual(len(report['plannedMarkets']), 8)
        self.assertIsNone(report['summary']['sentCreators'])
        self.assertFalse((self.root/'var').exists())
        self.assertEqual(report['platformWrites'], 0)

    def test_unfinished_day_is_rejected(self):
        with self.assertRaises(ValueError):
            collect(self.root, DAY, now=END-1)

    def test_confirmed_cards_full_invites_and_notification_scope_are_separate(self):
        path = self.root/'state.sqlite'
        db = activity_db(path); db.close()
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        with read_db(path) as db:
            result = _activity(db, 'p', START, END)
            with self.assertRaises(sqlite3.OperationalError):
                db.execute('CREATE TABLE forbidden(x)')
        self.assertEqual(result['sentCreators'], 1)
        self.assertEqual(result['sentCards'], 3)
        self.assertEqual(result['consumed'], 2)
        self.assertEqual(result['totalCards'], 2)
        self.assertEqual(result['totalCreators'], 1)
        self.assertEqual(result['totalReplied'], 1)
        self.assertEqual(result['totalShowcase'], 1)
        self.assertEqual(result['showcaseToday'], 1)
        self.assertEqual(result['handledCreators'], 1)
        self.assertEqual(result['humanCases'], 1)
        self.assertEqual(result['technicalUnknown'], 1)
        self.assertEqual(result['missingEventTimes'], 1)
        self.assertEqual(before, hashlib.sha256(path.read_bytes()).hexdigest())

    def test_missing_card_id_is_not_replaced_with_product_id(self):
        db = activity_db(self.root/'state.sqlite')
        db.execute("UPDATE cycle_delivery SET snapshot=? WHERE id='d1'", (json.dumps({'conversationId':'cid-a'}),))
        value = _activity(db, 'p', START, END)
        self.assertIsNone(value['totalCards'])
        self.assertEqual(value['missingCardIds'], 1)
        db.close()

    def test_day_end_is_exclusive_for_new_leads_and_known_zero_is_valid(self):
        db = sqlite3.connect(':memory:')
        db.execute('CREATE TABLE lead_publication_contribution(market,kind,new_pairs,published_at)')
        db.executemany('INSERT INTO lead_publication_contribution VALUES(?,?,?,?)',
                      [('de','A',7,START),('de','B',3,END-1),('de','A',99,END),('fr','A',100,START)])
        self.assertEqual(_leads(db,'de',START,END), {'newA':7,'newB':3})
        self.assertEqual(_leads(db,'br',START,END), {'newA':0,'newB':0})
        db.close()

    def test_inventory_deduplicates_routes_and_checks_exact_binding(self):
        from lib.link_naming import load
        (self.root/'var').mkdir()
        db = sqlite3.connect(':memory:');db.row_factory = sqlite3.Row
        db.executescript('CREATE TABLE catalog(id,plan_id,payload,observed);CREATE TABLE catalog_head(plan_id,snapshot_id);')
        def offer(pid, source, creator):
            return {'pid':pid,'campaignId':'123','catalogSource':source,'creatorPercent':creator,'publicPercent':'5',
                    'totalPercent':'20','stock':'200','available':True,'endAt':'2027-05-01T00:00:00Z','offerKey':pid+source}
        selected = offer('1','selected','10')
        campaign = offer('1','campaign','15')
        other = offer('2','campaign','10')
        expired = {**offer('3','campaign','10'),'endAt':'2026-10-04T00:00:00Z'}
        db.execute('INSERT INTO catalog VALUES(?,?,?,?)', ('s','p',json.dumps([selected,campaign,other,expired]),START))
        db.execute("INSERT INTO catalog_head VALUES('p','s')")
        links = sqlite3.connect(self.root/'var/catalog-links.sqlite')
        links.execute('CREATE TABLE catalog_current_binding(market,catalog_source,pid,campaign_id,state,list_id,offer_fingerprint,commission_rule_version,naming_rule_version,verified_at)')
        links.executemany('INSERT INTO catalog_current_binding VALUES(?,?,?,?,?,?,?,?,?,?)',[
            ('de','campaign','1','123','active','1',offer_fingerprint(campaign),'commission-1-to-2-v1',load(self.root,'de')['version'],START),
            ('de','campaign','2','123','active','2','wrong-fingerprint','commission-1-to-2-v1',load(self.root,'de')['version'],START),
        ]);links.commit();links.close()
        result = _inventory(self.root,db,'p','de',{},END)
        self.assertEqual(result['availableProducts'],2)
        self.assertEqual(result['fullProducts'],0)
        self.assertEqual(result['campaignProducts'],2)
        self.assertEqual(result['taplinkReady'],1)
        db.close()

    def test_report_format_has_cumulative_tables_and_summary_last(self):
        report = collect(self.root, DAY, now=END+1, pool_reader=lambda *a,**k: {'available':False})
        subject, plain, html = render(report)
        self.assertIn('累计转化表现',plain)
        self.assertIn('待核实',plain)
        for unwanted in ('GMV','机构佣金','7 日','批次'):
            self.assertNotIn(unwanted,plain)
        self.assertGreater(plain.rindex('汇总\n'), plain.rindex('回复服务与待办\n'))
        self.assertNotIn('<script',html)
        self.assertIsNone(ratio(0,0))
        self.assertIsNone(ratio(None,5))


def simple_report(root, day, *, now):
    row = dict.fromkeys(FIELDS, 0)
    row.update(market='de',label='德国',fullSupported=True,errors={},taplinkRate=None,replyRate=None,showcaseRate=None)
    return {'date':day,'generatedAt':now,'inventorySampledAt':now,'markets':[row],
            'plannedMarkets':[],'summary':row,'platformWrites':0}


class MailTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);(self.root/'private-data').mkdir()
        self.config={'enabled':True,'timezone':'Asia/Shanghai','sendTime':'09:00','recipients':['recipient@example.com'],
                     'sender':'sender@example.com','smtp':{'host':'smtp.example.com','username':'sender','port':465,
                     'security':'ssl','passwordFile':'private-data/mail.password'}}
        (self.root/'private-data/mail.password').write_text('test-only-password')
        save_json(self.root/'private-data/business-daily-mail.json', self.config)
        self.sent=[];self.fail_login=False;self.fail_send=False;self.fail_quit=False;self.clients=[]
        outer=self
        class SMTP:
            def __init__(self,*args,**kw):
                self.closed=False;outer.clients.append(self)
                outer.assertIn('context',kw)
                outer.assertTrue(kw['context'].check_hostname)
                outer.assertEqual(kw['context'].verify_mode,ssl.CERT_REQUIRED)
                outer.assertGreater(len(kw['context'].get_ca_certs()),0)
            def login(self,user,password):
                if outer.fail_login:raise smtplib.SMTPAuthenticationError(535,b'bad test login')
            def send_message(self,message):
                state=json.loads((outer.root/f'var/business-daily/delivery/{DAY}.json').read_text())
                outer.assertEqual(state['state'],'submitting')
                outer.sent.append(message)
                if outer.fail_send:raise smtplib.SMTPServerDisconnected('test disconnect after possible DATA')
                return {}
            def quit(self):
                if outer.fail_quit:raise smtplib.SMTPServerDisconnected()
                self.closed=True
            def close(self):self.closed=True
        self.factory=SMTP
        capture(self.root,DAY,now=END+1,collector=simple_report)

    def test_success_is_durable_and_replay_never_sends_twice(self):
        result=deliver(self.root,DAY,self.config,now=END+32400,smtp_factory=self.factory)
        self.assertEqual(result['state'],'sent')
        again=deliver(self.root,DAY,self.config,now=END+32460,smtp_factory=self.factory)
        self.assertTrue(again['duplicatePrevented'])
        self.assertEqual(len(self.sent),1)
        self.assertTrue(self.clients[0].closed)

    def test_unknown_result_keeps_original_message_and_never_retries(self):
        self.fail_send=True
        first=deliver(self.root,DAY,self.config,now=END+32400,smtp_factory=self.factory)
        self.assertEqual(first['state'],'unknown')
        self.fail_send=False
        self.assertEqual(deliver(self.root,DAY,self.config,now=END+34000,smtp_factory=self.factory)['state'],'unknown')
        self.assertEqual(len(self.sent),1)

    def test_crashed_submitting_intent_cannot_be_resent(self):
        save_json(self.root/f'var/business-daily/delivery/{DAY}.json',{'date':DAY,'attempts':1,'state':'submitting'})
        result=deliver(self.root,DAY,self.config,now=END+32400,smtp_factory=self.factory)
        self.assertEqual(result['state'],'submitting')
        self.assertEqual(len(self.sent),0)

    def test_auth_failure_is_before_submission_and_retry_is_bounded(self):
        self.fail_login=True
        for at in (END+32400,END+33300,END+34200):
            self.assertEqual(deliver(self.root,DAY,self.config,now=at,smtp_factory=self.factory)['state'],'failed_before_submission')
        self.assertEqual(deliver(self.root,DAY,self.config,now=END+36000,smtp_factory=self.factory)['state'],'failed')
        self.assertEqual(len(self.sent),0)
        self.assertTrue(all(c.closed for c in self.clients))

    def test_post_success_quit_failure_does_not_turn_success_unknown(self):
        self.fail_quit=True
        self.assertEqual(deliver(self.root,DAY,self.config,now=END+32400,smtp_factory=self.factory)['state'],'sent')

    def test_missing_credentials_waits_without_starting_an_email_intent(self):
        (self.root/'private-data/mail.password').unlink()
        result=deliver(self.root,DAY,self.config,now=END+32400,smtp_factory=self.factory)
        self.assertEqual(result['state'],'waiting_mail_configuration')
        self.assertFalse((self.root/f'var/business-daily/delivery/{DAY}.json').exists())

    def test_google_display_spaces_are_not_sent_as_password_characters(self):
        from lib.business_daily_mail import credentials
        self.config['smtp']['host']='smtp.gmail.com'
        (self.root/'private-data/mail.password').write_text('abcd efgh ijkl mnop\n')
        self.assertEqual(credentials(self.root,self.config),'abcdefghijklmnop')

    def test_schedule_is_beijing_time_and_report_snapshot_is_frozen(self):
        self.assertEqual(tick(self.root,now=END+32000,collector=simple_report,smtp_factory=self.factory)['state'],'waiting_send_time')
        report=json.loads((self.root/f'var/business-daily/reports/{DAY}/report.json').read_text())
        self.assertEqual(report['inventorySampledAt'],END+1)
        self.assertEqual(tick(self.root,now=END+32400,collector=simple_report,smtp_factory=self.factory)['state'],'sent')
        self.assertEqual(tick(self.root,now=END+32460,collector=simple_report,smtp_factory=self.factory)['state'],'sent')
        self.assertEqual(len(self.sent),1)

    def test_paused_schedule_never_generates_or_sends(self):
        self.config['enabled']=False;save_json(self.root/'private-data/business-daily-mail.json',self.config)
        result=tick(self.root,now=END+32400,collector=lambda *a,**k: self.fail('must not collect'))
        self.assertEqual(result['state'],'disabled')

    def test_configuration_rejects_plain_smtp_and_header_injection(self):
        self.config['smtp']['security']='none'
        save_json(self.root/'private-data/business-daily-mail.json',self.config)
        with self.assertRaises(ValueError):configuration(self.root)
        self.config['smtp']['security']='ssl';self.config['sender']='sender@example.com\r\nBcc: attacker@example.com'
        save_json(self.root/'private-data/business-daily-mail.json',self.config)
        with self.assertRaises(ValueError):configuration(self.root)


if __name__=='__main__':
    unittest.main()
