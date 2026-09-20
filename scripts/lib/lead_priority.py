"""Pure A/B lead ordering policy used before the real video source is connected.

The module accepts synthetic or already-normalized facts only.  It does not open SQLite, call
Kalodata, change the production pool, freeze a batch, or send anything.
"""
from collections import defaultdict
from datetime import date
import re

from lib.second_cycle import CycleError


PID = re.compile(r'[0-9]{19}\Z')
CONTROL_STATES = {'ready','cooling','unresolved','human','excluded'}


def _date(value):
    try:
        parsed=date.fromisoformat(value)
    except (TypeError,ValueError):
        raise CycleError('lead_priority_date_invalid') from None
    return parsed


def representative_video(videos, *, as_of, days=30, min_views=1000):
    """Choose one video only: highest current views, then newest publication time."""
    if not isinstance(videos,list) or type(days) is not int or not 1<=days<=90 or \
            type(min_views) is not int or min_views<1:
        raise CycleError('lead_priority_video_invalid')
    today=_date(as_of);qualified=[]
    for row in videos:
        if not isinstance(row,dict):raise CycleError('lead_priority_video_invalid')
        video_id=str(row.get('videoId') or '');views=row.get('views');released=_date(row.get('releasedAt'))
        if not video_id or len(video_id)>100 or type(views) is not int or views<0:
            raise CycleError('lead_priority_video_invalid')
        age=(today-released).days
        if 0<=age<days and views>=min_views:
            qualified.append({'videoId':video_id,'views':views,'releasedAt':released.isoformat(),
                              'ageDays':age})
    if not qualified:return None
    return max(qualified,key=lambda row:(row['views'],row['releasedAt'],row['videoId']))


def _position(rows, creator_id, pid, as_of):
    has_oec={row.get('hasOec') for row in rows};active={row.get('productActive',True) for row in rows}
    if len(has_oec)!=1 or any(type(value) is not bool for value in has_oec) or len(active)!=1 or \
            any(type(value) is not bool for value in active):
        raise CycleError('lead_priority_pair_conflict')
    sales=[];videos=[]
    for row in rows:
        source=row.get('source')
        if source=='sales':
            rank=row.get('sourceRank');units=row.get('units')
            if type(rank) is not int or rank<1 or type(units) is not int or units<1:
                raise CycleError('lead_priority_sales_invalid')
            sales.append({'sourceRank':rank,'units':units})
        elif source=='video':
            video=representative_video(row.get('videos'),as_of=as_of)
            if video:videos.append(video)
        else:raise CycleError('lead_priority_source_invalid')
    representative=max(videos,key=lambda row:(row['views'],row['releasedAt'],row['videoId'])) if videos else None
    if sales:
        best=min(sales,key=lambda row:(row['sourceRank'],-row['units']))
        source_class='A';key=(0,best['sourceRank'],-best['units'],pid,creator_id)
    elif representative:
        best=None;source_class='B';key=(1,-representative['views'],-_date(representative['releasedAt']).toordinal(),
                                       pid,creator_id)
    else:return None
    return {'creatorId':creator_id,'pid':pid,'sourceClass':source_class,
            'hasOec':next(iter(has_oec)),'productActive':next(iter(active)),
            'sourceRank':best['sourceRank'] if best else None,'units':best['units'] if best else 0,
            'representativeVideo':representative,'_key':key}


def rank_leads(rows, *, as_of, controls=None):
    """Return deterministic lead order and the final one-slot-per-creator sending pool."""
    if not isinstance(rows,list) or not rows:raise CycleError('lead_priority_rows_invalid')
    controls={} if controls is None else controls
    if not isinstance(controls,dict):raise CycleError('lead_priority_controls_invalid')
    grouped=defaultdict(list);filtered=[]
    for index,row in enumerate(rows):
        if not isinstance(row,dict):raise CycleError('lead_priority_rows_invalid')
        creator_id=str(row.get('creatorId') or '');pid=str(row.get('pid') or '')
        if not creator_id or len(creator_id)>100 or not PID.fullmatch(pid):
            raise CycleError('lead_priority_identity_invalid')
        grouped[(creator_id,pid)].append(row)
    positions=[]
    for (creator_id,pid),pair_rows in grouped.items():
        position=_position(pair_rows,creator_id,pid,as_of)
        if position is None:
            filtered.append({'creatorId':creator_id,'pid':pid,'reason':'no_qualifying_video'})
        else:positions.append(position)
    positions.sort(key=lambda row:row['_key'])
    ready_candidates=[];waiting=[];inactive=[]
    for row in positions:
        state=controls.get(row['creatorId'],'ready')
        if state not in CONTROL_STATES:raise CycleError('lead_priority_controls_invalid')
        if not row['productActive']:
            row['result']='inactive';row['reason']='product_inactive';inactive.append(row)
        elif state=='excluded':
            row['result']='inactive';row['reason']='creator_excluded';inactive.append(row)
        elif not row['hasOec']:
            row['result']='waiting';row['reason']='identity_required';waiting.append(row)
        elif state!='ready':
            row['result']='waiting';row['reason']='creator_'+state;waiting.append(row)
        else:ready_candidates.append(row)
    seen=set();ready=[]
    for row in ready_candidates:
        if row['creatorId'] in seen:
            row['result']='waiting';row['reason']='same_creator_other_pid';waiting.append(row)
        else:
            seen.add(row['creatorId']);row['result']='sendable';row['reason']=None
            row['sendOrder']=len(ready)+1;ready.append(row)
    def clean(row):return {key:value for key,value in row.items() if key!='_key'}
    return {'schema':'bdhub.lead-priority-simulation.v1','asOf':_date(as_of).isoformat(),
            'counts':{'inputRows':len(rows),'pairPositions':len(positions),'sendable':len(ready),
                      'waiting':len(waiting),'inactive':len(inactive),'filtered':len(filtered)},
            'sendOrder':[clean(row) for row in ready],
            'waiting':[clean(row) for row in waiting],
            'inactive':[clean(row) for row in inactive],
            'filtered':filtered,'platformWrites':0,'realSends':0}
