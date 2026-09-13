import sys,unittest
from pathlib import Path
from contextlib import contextmanager
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.cycle_executor import execute
from lib.second_cycle import CycleError
from test_cycle_delivery import DeliveryTests
class ExecutorTests(unittest.TestCase):
 setUp=DeliveryTests.setUp
 tearDown=DeliveryTests.tearDown
 def runtime(self,fail=False,new=False):
  self.calls=[];owner=self;c=self.c;c['conversationId']=None if new else '88';self.s.db.execute('DELETE FROM cycle_delivery_part');self.s.db.execute('DELETE FROM cycle_delivery');self.id=self.d.prepare(self.p,c)['id']
  card=SimpleNamespace(product_id='1',list_id='2',binding_sha256='hash')
  class Adapter:
   def create_once(self,oec,ref,before_dispatch):
    before_dispatch({'oecId':oec,'requestRef':ref,'stage':'create_conversation','market':'it','account':'acc6'});owner.calls.append('create');return {'conversationId':'88'}
   def send(self,kind,ref,before):
    scope={'oecId':c['oecId'],'conversationId':'88','market':'it','account':'acc6','requestRef':ref,'componentKind':kind,'stage':'send_message','productId':'1','listId':'2','campaignId':'0','bindingSha256':'hash'}
    before(scope);owner.calls.append(kind)
    if fail and kind=='card':raise RuntimeError('transport_timeout')
    return {'requestRef':ref}
   def send_card_once(self,conv,card,ref,before_dispatch):return self.send('card',ref,before_dispatch)
   def send_once(self,conv,text,ref,before_dispatch):return self.send('text',ref,before_dispatch)
   def readback_card(self,*_,**kw):return {'status':'confirmed','messageId':'card','evidenceRef':'card-check'}
   def readback(self,*_,**kw):return {'status':'confirmed','messageId':'text','evidenceRef':'text-check'}
  @contextmanager
  def gate():yield lambda:None
  @contextmanager
  def rt(*_,**kw):yield {'adapter':Adapter(),'reads':SimpleNamespace(conversation=lambda *a:SimpleNamespace(conversation_id='88')),'card':card,'write_gate':gate,'validate_card':lambda c:c}
  return rt
 def test_new_conversation_is_durable_and_created_once(self):
  rt=self.runtime(new=True);execute(self.d,self.id,rt,lambda c:None,lambda *a:None);execute(self.d,self.id,rt,lambda c:None,lambda *a:None)
  self.assertEqual(self.calls,['create','card','text']);self.assertEqual(self.d.conversation_intent(self.id)['state'],'confirmed')
 def test_full_card_text_cycle(self):
  rt=self.runtime();r=execute(self.d,self.id,rt,lambda c:None,lambda *a:None)
  self.assertEqual(r['state'],'confirmed');self.assertEqual(self.calls,['card','text'])
  execute(self.d,self.id,rt,lambda c:None,lambda *a:None);self.assertEqual(self.calls,['card','text'])
 def test_timeout_reconciles_card_before_text(self):
  rt=self.runtime(fail=True)
  with self.assertRaises(RuntimeError):execute(self.d,self.id,rt,lambda c:None,lambda *a:None)
  self.assertEqual(self.d.get(self.id)['state'],'unknown')
  r=execute(self.d,self.id,rt,lambda c:None,lambda *a:None)
  self.assertEqual(r['state'],'confirmed');self.assertEqual(self.calls,['card','text'])
 def test_preflight_receives_the_verified_conversation(self):
  rt=self.runtime();observed=[]
  def check(c,rt,conv):observed.append(conv.conversation_id)
  execute(self.d,self.id,rt,lambda c:None,check);self.assertEqual(observed,['88','88'])
 def test_denied_authorization_never_opens_runtime(self):
  rt=self.runtime()
  def denied(c):raise CycleError('denied')
  def forbidden(*a,**kw):raise AssertionError('runtime opened')
  with self.assertRaisesRegex(CycleError,'denied'):execute(self.d,self.id,forbidden,denied,lambda *a:None)
del DeliveryTests
if __name__=='__main__':unittest.main()
