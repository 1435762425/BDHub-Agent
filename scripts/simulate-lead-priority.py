#!/usr/bin/env python3
"""Run the confirmed A/B lead order on synthetic data only."""
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.dont_write_bytecode=True
sys.path.insert(0,str(ROOT/'scripts'))

from lib.lead_priority import rank_leads  # noqa:E402


def video(video_id,views,released):return {'videoId':video_id,'views':views,'releasedAt':released}


def fixture():
    return [
      {'creatorId':'creator_alba','pid':'1729000000000000001','source':'sales','sourceRank':1,'units':18,'gmv':'420','hasOec':True},
      {'creatorId':'creator_alba','pid':'1729000000000000001','source':'video','hasOec':True,
       'videos':[video('video_alba',80000,'2026-09-10')]},
      {'creatorId':'creator_bruno','pid':'1729000000000000002','source':'sales','sourceRank':2,'units':50,'gmv':'300','hasOec':True},
      {'creatorId':'creator_carla','pid':'1729000000000000003','source':'sales','sourceRank':2,'units':20,'gmv':'200','hasOec':True},
      {'creatorId':'creator_carla','pid':'1729000000000000004','source':'video','hasOec':True,
       'videos':[video('video_carla',150000,'2026-09-15')]},
      {'creatorId':'creator_elena','pid':'1729000000000000005','source':'video','hasOec':True,
       'videos':[video('video_elena_low',12000,'2026-09-18'),video('video_elena_top',26000,'2026-09-14')]},
      {'creatorId':'creator_dario','pid':'1729000000000000006','source':'video','hasOec':True,
       'videos':[video('video_dario',26000,'2026-09-11')]},
      {'creatorId':'creator_faro','pid':'1729000000000000007','source':'video','hasOec':False,
       'videos':[video('video_faro',100000,'2026-09-17')]},
      {'creatorId':'creator_gina','pid':'1729000000000000008','source':'video','hasOec':True,
       'videos':[video('video_gina',50000,'2026-09-16')]},
      {'creatorId':'creator_luca','pid':'1729000000000000009','source':'video','hasOec':True,
       'videos':[video('video_luca',6000,'2026-09-18')]},
      {'creatorId':'creator_hugo','pid':'1729000000000000010','source':'video','hasOec':True,
       'videos':[video('video_hugo',999,'2026-09-18')]},
      {'creatorId':'creator_iris','pid':'1729000000000000011','source':'video','hasOec':True,
       'videos':[video('video_iris',200000,'2026-08-01')]},
    ]


if __name__=='__main__':
    print(json.dumps(rank_leads(fixture(),as_of='2026-09-20',controls={'creator_gina':'unresolved'}),
                     ensure_ascii=False,indent=2))
