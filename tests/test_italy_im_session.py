"""Pigeon wire fixtures only; no platform requests or monkeypatched MX gates."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from lib.italy_im_auth import ItalyImAuthContext, ImProbeDeadline
from lib.italy_im_session import ItalyImReadSession, ItalyImReadError, VerifiedConversation, IM_HOST, PATHS, decode_initial_conversations, _proto

W=_proto()

def info(cid=10,oec="100",market="8",kind=2):
    ext=W.vb(11,W.vb(1,"creator_oec_id")+W.vb(2,oec))+W.vb(11,W.vb(1,"market_region")+W.vb(2,market))
    return W.vb(1,"full-"+str(cid))+W.vi(2,cid)+W.vi(3,kind)+W.vb(4,"PRIVATE_TICKET")+W.vb(50,ext)

def message(cid=10):
    return W.vb(1,"full-"+str(cid))+W.vi(3,55)+W.vi(5,cid)+W.vi(6,1000)+W.vi(7,987)+W.vb(8,"PRIVATE_MESSAGE_BODY")

def envelope(command,sequence,body,status=0):
    return W.vi(1,command)+W.vi(2,sequence)+W.vi(3,status)+W.vb(6,W.vb(command,body))

class Clock:
    def __init__(self):self.value=0.
    def now(self):return self.value
    def sleep(self,seconds):self.value+=seconds

class HTTP:
    def __init__(self,reply=None):self.calls=[];self.reply=reply
    def post(self,url,**kwargs):
        self.calls.append((url,kwargs));fields=W.wire_fields(kwargs["data"]);command=W.one(fields,1);sequence=W.one(fields,2)
        if self.reply is not None:return self.reply(command,sequence,kwargs)
        body=(W.vb(2,info())+W.vb(1,message())+W.vi(4,99)+W.vi(5,1)) if command==203 else W.vb(1,info()) if command==608 else W.vb(1,message())+W.vi(3,0)
        return SimpleNamespace(status_code=200,headers={},content=envelope(command,sequence,body))

def auth(host=IM_HOST,headers=None):
    return ItalyImAuthContext("acc6","987",{"token":"PRIVATE_TOKEN","api_url":"https://"+host},{"market_region":"8"},headers or {"user-agent":"fixture"})

def session(http=None,**kwargs):
    report={};timer=Clock();transport=http or HTTP()
    value=ItalyImReadSession(auth(),report,http=transport,monotonic=timer.now,sleep=timer.sleep,sequence=1,**kwargs)
    return value,report,transport,timer

class ItalyImSessionTests(unittest.TestCase):
    def test_im_host_requested_is_saved_before_dispatch_and_stays_false_on_preflight_stop(self):
        report, updates, timer = {}, [], Clock()
        def reply(command, sequence, _):
            self.assertTrue(report["imHostRequested"])
            self.assertTrue(updates[-1])
            return SimpleNamespace(status_code=200,headers={},content=envelope(command,sequence,b""))
        http=HTTP(reply)
        s=ItalyImReadSession(auth(),report,http=http,monotonic=timer.now,sleep=timer.sleep,
                            on_update=lambda:updates.append(report.get("imHostRequested")))
        self.assertFalse(report["imHostRequested"]);s.initialize();self.assertTrue(report["imHostRequested"])
        stopped, stopped_report, no_http, _=session(stopped=lambda:True)
        with self.assertRaises(ItalyImReadError):stopped.initialize()
        self.assertFalse(stopped_report["imHostRequested"]);self.assertEqual(no_http.calls,[])

    def test_short_conversation_reuse_requires_same_identity_and_expires(self):
        s,report,http,timer=session()
        first=s.conversation('10','100',max_age=15)
        self.assertIs(first,s.conversation('10','100',max_age=15));self.assertEqual(len(http.calls),1)
        with self.assertRaises(ItalyImReadError):s.conversation('10','200',max_age=15)
        self.assertEqual(len(http.calls),2)
        timer.value+=15
        self.assertIsNot(first,s.conversation('10','100',max_age=15));self.assertEqual(len(http.calls),3)
        s.stopped=lambda:True
        with self.assertRaises(ItalyImReadError):s.conversation('10','100',max_age=15)
        self.assertEqual(len(http.calls),3)

    def test_init_exports_only_cid_oec_metadata_and_no_body_or_ticket(self):
        s,report,http,_=session();result=s.initialize()
        self.assertEqual(result["conversations"][0],{"conversationId":"10","oecId":"100","conversationType":2,"ticketPresent":True,"identitySource":"conversation_core.creator_oec_id"})
        self.assertTrue(result["hasMore"]);self.assertFalse(result["completeInbox"]);self.assertEqual(result["messageBodiesDiscarded"],1)
        self.assertNotIn("PRIVATE",json.dumps(result)+json.dumps(report));self.assertNotIn('"100"',json.dumps(report))
        self.assertEqual(http.calls[0][0],"https://"+IM_HOST+PATHS[203]);self.assertFalse(http.calls[0][1]["allow_redirects"])
        self.assertFalse(any(key.lower()=="cookie" for key in http.calls[0][1]["headers"]));self.assertEqual(report["sendRequests"],0)

    def test_same_oec_detail_then_identity_checked_history_have_no_saved_message_text(self):
        s,report,http,timer=session();s.initialize();conversation=s.conversation("10","100");history=s.history_summary(conversation)
        self.assertEqual([W.one(W.wire_fields(kwargs["data"]),1) for _,kwargs in http.calls],[203,608,301])
        self.assertEqual(timer.value,2);self.assertEqual(history,{"messageCount":1,"hasMore":False,"identityVerified":True,"messageBodiesStored":False})
        self.assertNotIn("PRIVATE",repr(conversation)+json.dumps(history)+json.dumps(report))
        self.assertEqual(report["verifiedConversationCount"],1)

    def test_sender_counts_are_scoped_and_missing_sender_is_not_reply(self):
        def row(role,sender=None,**extra):
            value=W.vb(1,'full-10')+W.vi(5,10)
            if sender is not None:value+=W.vi(7,sender)
            for key,val in {'sender_role':role,**extra}.items():value+=W.vb(9,W.vb(1,key)+W.vb(2,val))
            if role=='4' and sender==987:value+=W.vi(10,1789200000000)+W.vi(4,999)
            return W.vb(1,value)
        rows=row('4',987)+row('1',555)+row('1')+row('4',555)+row('3',1,type='notification',starling_content_key='ttspc_im_message_relation_ststem_message_6_plural')
        def reply(command,sequence,_):
            body=W.vb(1,info()) if command==608 else rows+W.vi(3,1)
            return SimpleNamespace(status_code=200,headers={},content=envelope(command,sequence,body))
        s,_,_,_=session(HTTP(reply));conv=s.conversation('10','100')
        result=s.history_summary(conv,include_sender_counts=True)
        self.assertEqual(result['senderCounts'],dict(ourMessages=1,creatorReplies=1,showcaseNotifications=1,otherOrUnknown=2))
        self.assertTrue(result['hasMore']);self.assertIn('returned page',result['countScope'])
        self.assertEqual(result['outboundCreateTimeRaw'],[1789200000000]);self.assertEqual(result['outboundTimeMissingCount'],0)

    def test_event_export_requires_native_ids_and_discards_body(self):
        s,_,_,_=session();conv=s.conversation('10','100');h=s.history_summary(conv,include_events=True)
        self.assertEqual(h['events'][0]['messageId'],'55');self.assertEqual(h['events'][0]['oecId'],'100')
        self.assertNotIn('PRIVATE',json.dumps(h));self.assertFalse(h['messageBodiesStored'])
        def reply(command,sequence,_):
            body=W.vb(1,info()) if command==608 else W.vb(1,W.vb(1,'full-10')+W.vi(5,10))
            return SimpleNamespace(status_code=200,headers={},content=envelope(command,sequence,body))
        s,_,_,_=session(HTTP(reply));conv=s.conversation('10','100')
        with self.assertRaises(ItalyImReadError):s.history_summary(conv,include_events=True)

    def test_contents_are_opt_in_and_separate_from_event_identity(self):
        s,_,_,_=session();conv=s.conversation('10','100')
        old=s.history_summary(conv,include_events=True);new=s.history_summary(conv,include_contents=True)
        self.assertEqual(old['events'],new['events']);self.assertNotIn('contents',old)
        self.assertEqual(new['contents'][0]['text'],'PRIVATE_MESSAGE_BODY');self.assertEqual(new['contents'][0]['format'],'text')

    def test_mx_host_and_cookie_or_write_command_are_rejected_before_http(self):
        original=W.READ_HOST;http=HTTP()
        with self.assertRaises(ItalyImReadError):ItalyImReadSession(auth(host=original),{},http=http)
        with self.assertRaises(ItalyImReadError):ItalyImReadSession(auth(headers={"cookie":"PRIVATE"}),{},http=http)
        s,_,http,_=session(http)
        for cmd in (100,609,[],True):
            with self.assertRaises(ItalyImReadError) as found:s._read(cmd,b"")
            self.assertEqual(found.exception.code,"im_command_forbidden")
        self.assertEqual(http.calls,[]);self.assertEqual(W.READ_HOST,original)

    def test_wrong_sequence_command_or_outer_status_cannot_prove_initialization(self):
        for mutation in (lambda c,q:(c,q+1,0),lambda c,q:(608,q,0),lambda c,q:(c,q,500)):
            def reply(command,sequence,_):
                c,q,status=mutation(command,sequence)
                return SimpleNamespace(status_code=200,headers={},content=envelope(c,q,b"",status))
            s,report,http,_=session(HTTP(reply))
            with self.assertRaises(ItalyImReadError):s.initialize()
            self.assertNotIn("imInitializationVerified",report);self.assertEqual(len(http.calls),1)

    def test_wrong_oec_or_history_cid_is_rejected_without_followup_or_saved_identity(self):
        s,report,http,_=session()
        with self.assertRaises(ItalyImReadError) as found:s.conversation("10","999")
        self.assertEqual(found.exception.code,"im_conversation_identity_mismatch");self.assertEqual(s.verified,{})
        self.assertNotIn("verifiedConversationCount",report)
        def reply(command,sequence,_):
            body=W.vb(1,info()) if command==608 else W.vb(1,message(11))
            return SimpleNamespace(status_code=200,headers={},content=envelope(command,sequence,body))
        s,_,_,_=session(HTTP(reply));conv=s.conversation("10","100")
        with self.assertRaises(ItalyImReadError) as found:s.history_summary(conv)
        self.assertEqual(found.exception.code,"im_history_identity_mismatch")

    def test_unverified_or_other_session_conversation_cannot_read_history(self):
        s,_,http,_=session();fabricated=VerifiedConversation("10",b"full-10",2,b"ticket","100")
        with self.assertRaises(ItalyImReadError):s.history_summary(fabricated)
        self.assertEqual(http.calls,[])

    def test_invalid_and_other_market_rows_are_counted_but_not_exported(self):
        body=W.vb(2,info())+W.vb(2,info(cid=11,market="19"))+W.vb(2,W.vi(2,12))
        result=decode_initial_conversations(body);self.assertEqual(len(result["conversations"]),1);self.assertEqual(result["invalidConversations"],1);self.assertEqual(result["otherMarketConversations"],1)
        with self.assertRaises(ItalyImReadError) as found:decode_initial_conversations(W.vb(2,info())+W.vb(2,info(oec="200")))
        self.assertEqual(found.exception.code,"im_duplicate_identity")

    def test_timeouts_do_not_retry_and_cross_stage_pacer_is_kept(self):
        def interrupted(*_):raise ImProbeDeadline()
        s,report,http,_=session(HTTP(interrupted))
        with self.assertRaises(ImProbeDeadline):s.initialize()
        self.assertEqual(len(http.calls),1);self.assertEqual(report["imReads"][0]["errorCode"],"wall_timeout")
        timer=Clock();context=ItalyImAuthContext("acc6","987",{"token":"PRIVATE_TOKEN","api_url":"https://"+IM_HOST},{"market_region":"8"},{},5)
        s=ItalyImReadSession(context,{},http=HTTP(),monotonic=timer.now,sleep=timer.sleep);s.initialize();self.assertEqual(timer.value,5)
        timer.value=901
        with self.assertRaises(ItalyImReadError) as found:s.initialize()
        self.assertEqual(found.exception.code,"im_auth_expired")

if __name__=="__main__":unittest.main()
