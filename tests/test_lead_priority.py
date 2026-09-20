from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))

from lib.lead_priority import rank_leads,representative_video  # noqa:E402
from lib.second_cycle import CycleError  # noqa:E402


def video(video_id,views,released):return {'videoId':video_id,'views':views,'releasedAt':released}


class LeadPriorityTests(unittest.TestCase):
 def test_representative_video_is_the_highest_single_video_not_a_sum(self):
  selected=representative_video([video('newer',12000,'2026-09-18'),video('highest',26000,'2026-09-14'),
                                 video('too-small',999,'2026-09-19'),video('too-old',90000,'2026-08-01')],
                                as_of='2026-09-20')
  self.assertEqual(selected['videoId'],'highest');self.assertEqual(selected['views'],26000)

 def test_sales_leads_then_highest_video_produce_the_send_order(self):
  rows=[
   {'creatorId':'a','pid':'1729000000000000001','source':'sales','sourceRank':1,'units':10,'gmv':'500','hasOec':True},
   {'creatorId':'b','pid':'1729000000000000002','source':'sales','sourceRank':2,'units':50,'gmv':'300','hasOec':True},
   {'creatorId':'c','pid':'1729000000000000003','source':'sales','sourceRank':2,'units':20,'gmv':'200','hasOec':True},
   {'creatorId':'d','pid':'1729000000000000004','source':'video','hasOec':True,
    'videos':[video('d-old',26000,'2026-09-11')]},
   {'creatorId':'e','pid':'1729000000000000005','source':'video','hasOec':True,
    'videos':[video('e-low',12000,'2026-09-18'),video('e-top',26000,'2026-09-14')]},
  ]
  result=rank_leads(rows,as_of='2026-09-20')
  self.assertEqual([row['creatorId'] for row in result['sendOrder']],['a','b','c','e','d'])
  self.assertEqual(result['sendOrder'][3]['representativeVideo']['videoId'],'e-top')

 def test_same_pair_merges_to_a_and_same_creator_gets_one_slot(self):
  rows=[
   {'creatorId':'a','pid':'1729000000000000001','source':'sales','sourceRank':5,'units':2,'gmv':'20','hasOec':True},
   {'creatorId':'a','pid':'1729000000000000001','source':'video','hasOec':True,
    'videos':[video('same-pair',50000,'2026-09-18')]},
   {'creatorId':'a','pid':'1729000000000000002','source':'video','hasOec':True,
    'videos':[video('other-pid',90000,'2026-09-19')]},
  ]
  result=rank_leads(rows,as_of='2026-09-20')
  self.assertEqual((result['sendOrder'][0]['pid'],result['sendOrder'][0]['sourceClass']),
                   ('1729000000000000001','A'))
  self.assertEqual((result['waiting'][0]['pid'],result['waiting'][0]['reason']),
                   ('1729000000000000002','same_creator_other_pid'))

 def test_identity_and_creator_controls_move_positions_out_of_send_order(self):
  rows=[
   {'creatorId':'missing','pid':'1729000000000000001','source':'video','hasOec':False,
    'videos':[video('missing-video',90000,'2026-09-18')]},
   {'creatorId':'blocked','pid':'1729000000000000002','source':'video','hasOec':True,
    'videos':[video('blocked-video',80000,'2026-09-18')]},
   {'creatorId':'small','pid':'1729000000000000003','source':'video','hasOec':True,
    'videos':[video('small-video',999,'2026-09-18')]},
  ]
  result=rank_leads(rows,as_of='2026-09-20',controls={'blocked':'unresolved'})
  self.assertEqual(result['counts'],{'inputRows':3,'pairPositions':2,'sendable':0,'waiting':2,
                                     'inactive':0,'filtered':1})
  self.assertEqual({row['reason'] for row in result['waiting']},{'identity_required','creator_unresolved'})
  self.assertEqual(result['filtered'][0]['reason'],'no_qualifying_video')

 def test_invalid_control_is_rejected(self):
  rows=[{'creatorId':'a','pid':'1729000000000000001','source':'sales','sourceRank':1,
         'units':1,'gmv':'1','hasOec':True}]
  with self.assertRaisesRegex(CycleError,'controls_invalid'):
   rank_leads(rows,as_of='2026-09-20',controls={'a':'mystery'})


if __name__=='__main__':unittest.main()
