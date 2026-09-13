import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from test_cycle_delivery import DeliveryTests
from lib.cycle_speed import speed_status
class SpeedTests(unittest.TestCase):
 setUp=DeliveryTests.setUp;tearDown=DeliveryTests.tearDown;begin=DeliveryTests.begin;proof=DeliveryTests.proof
 def test_only_confirmed_messages_count_and_unknown_is_visible(self):
  self.begin('card');s=speed_status(self.s,self.p);self.assertEqual(s['contactsPerMinute'],0);self.assertEqual(s['unconfirmedAttemptPercent'],100)
  self.d.confirm(self.id,'card',self.proof('card'));self.now+=4;self.begin('text');self.d.confirm(self.id,'text',self.proof('text'));s=speed_status(self.s,self.p)
  self.assertEqual(s['contactsPerMinute'],0.2);self.assertEqual(s['messagesPerMinute'],0.4);self.assertEqual(s['cardToTextMedianSeconds'],4);self.assertEqual(sum(b['messages'] for b in s['buckets']),2)
 def test_windows_expire_without_counting_historical_total_as_speed(self):
  self.begin('card');self.d.confirm(self.id,'card',self.proof('card'));self.now+=301;s=speed_status(self.s,self.p);self.assertEqual(s['contactsPerMinute'],0);self.assertEqual(s['last15MinutesContacts'],1)
del DeliveryTests
if __name__=='__main__':unittest.main()
