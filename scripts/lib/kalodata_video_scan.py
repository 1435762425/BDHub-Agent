"""Durable full-window Kalodata video scan and current B-lead projection."""
from collections import Counter
from datetime import date
import json
from pathlib import Path
import sqlite3
import time

from lib.kalodata_video_evidence import (VIDEO_DETAIL_PATH,VIDEO_LIST_PATH,parse_video_detail,
                                         parse_video_list,persist,release_date)
from lib.second_cycle import CycleError,CycleStore,digest,encoded


AUTHOR_MISSING='author_missing'
AUTHOR_MISSING_LIMIT=20


def initialize(root,scope,window_start,window_end,*,min_views=1000,clock=time.time):
    if not isinstance(scope,list) or not scope:raise CycleError('video_scan_scope_invalid')
    normalized=[]
    for row in scope:
        pid=str(row.get('pid') or '');units=row.get('units')
        if len(pid)!=19 or not pid.isdigit() or type(units) is not int or units<0:
            raise CycleError('video_scan_scope_invalid')
        normalized.append({'pid':pid,'units':units})
    normalized=sorted({row['pid']:row for row in normalized}.values(),key=lambda row:(-row['units'],row['pid']))
    try:
        if (date.fromisoformat(window_end)-date.fromisoformat(window_start)).days not in range(1,90):raise ValueError
    except (TypeError,ValueError):raise CycleError('kalodata_video_window_invalid') from None
    fingerprint=digest(normalized);generation='video-generation-'+digest([window_start,window_end,min_views,fingerprint])[:24]
    stamp=clock()
    with CycleStore(Path(root)/'var/second-cycle.sqlite') as store,store.tx():
        old=store.db.execute('SELECT * FROM kalodata_video_generation WHERE generation_id=?',(generation,)).fetchone()
        if old:
            if (old['window_start'],old['window_end'],old['min_views'],old['scope_fingerprint'],old['scope_count']) != \
                    (window_start,window_end,min_views,fingerprint,len(normalized)):
                raise CycleError('video_scan_generation_conflict')
            return {'generationId':generation,'scope':old['scope_count'],'cached':True}
        store.db.execute('DELETE FROM video_lead_current')
        store.db.execute('DELETE FROM kalodata_video_head')
        store.db.execute('INSERT INTO kalodata_video_generation VALUES(?,?,?,?,?,?,?,?,?,?)',
            (generation,window_start,window_end,min_views,'queued',fingerprint,len(normalized),stamp,stamp,None))
        store.db.executemany('INSERT INTO kalodata_video_scan_job VALUES(?,?,?,?,?,?,?,?,?,?)',
            [(generation,row['pid'],row['units'],'queued',1,0,0,0,None,stamp) for row in normalized])
    return {'generationId':generation,'scope':len(normalized),'cached':False}


def _generation(store,generation_id):
    row=store.db.execute('SELECT * FROM kalodata_video_generation WHERE generation_id=?',(generation_id,)).fetchone()
    if not row:raise CycleError('video_scan_generation_missing')
    return row


def next_job(store,generation_id):
    _generation(store,generation_id)
    return store.db.execute("""SELECT * FROM kalodata_video_scan_job WHERE generation_id=?
      AND state IN ('listing','detailing','queued')
      ORDER BY CASE state WHEN 'listing' THEN 0 WHEN 'detailing' THEN 0 ELSE 1 END,
      updated_at,priority_units DESC,pid LIMIT 1""",(generation_id,)).fetchone()


def _candidate(row):
    return {'videoId':row['video_id'],'pid':row['pid'],'sourceRank':row['source_rank'],'views':row['views'],
            'sale':row['sale'],'revenueRaw':row['revenue_raw'],'releaseTime':row['release_time'],
            'duration':row['duration'],'description':row['description'],'contentType':row['content_type'],
            'isAd':bool(row['is_ad']),'isAi':bool(row['is_ai']),'listPayloadHash':row['list_payload_hash']}


def _save_page(store,generation,pid,page,body,parsed,stamp):
    payload=encoded(body['data']);fingerprint=parsed['listFingerprint']
    with store.tx():
        if store.db.execute('SELECT 1 FROM kalodata_video_scan_page WHERE generation_id=? AND pid=? '
                            'AND rows_fingerprint=?',(generation,pid,fingerprint)).fetchone():
            raise CycleError('kalodata_video_repeated_page')
        store.db.execute('INSERT INTO kalodata_video_scan_page VALUES(?,?,?,?,?,?,?,?,?,?)',
            (generation,pid,page,parsed['rowsReceived'],fingerprint,parsed['newestReleaseDate'],
             parsed['oldestReleaseDate'],int(parsed['reachedWindowStart']),payload,stamp))
        for row in parsed['candidates']:
            store.db.execute('''INSERT INTO kalodata_video_scan_item(
              generation_id,pid,video_id,source_rank,views,sale,revenue_raw,release_time,duration,
              description,content_type,is_ad,is_ai,list_payload_hash,detail_state,kalodata_creator_id,
              handle,detail_payload_hash,video_url,observed_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
              (generation,pid,row['videoId'],row['sourceRank'],row['views'],row['sale'],row['revenueRaw'],
               row['releaseTime'],row['duration'],row['description'],row['contentType'],int(row['isAd']),
               int(row['isAi']),row['listPayloadHash'],'pending',None,None,None,None,stamp))
        done=parsed['rowsReceived']<50 or parsed['reachedWindowStart']
        store.db.execute("UPDATE kalodata_video_scan_job SET state=?,next_page=?,list_done=?,"
                         "list_requests=list_requests+1,updated_at=? WHERE generation_id=? AND pid=?",
                         ('detailing' if done else 'listing',page if done else page+1,int(done),stamp,generation,pid))


def _save_detail(store,generation,pid,row,detail,stamp,*,network):
    with store.tx():
        store.db.execute('INSERT INTO kalodata_video_author_cache VALUES(?,?,?,?,?,?) '
                         'ON CONFLICT(video_id) DO UPDATE SET kalodata_creator_id=excluded.kalodata_creator_id,'
                         'handle=excluded.handle,payload_hash=excluded.payload_hash,video_url=excluded.video_url,'
                         'observed_at=excluded.observed_at',
                         (detail['videoId'],detail['kalodataCreatorId'],detail['handle'],detail['payloadHash'],
                          detail['videoUrl'],stamp))
        store.db.execute("UPDATE kalodata_video_scan_item SET detail_state='resolved',kalodata_creator_id=?,"
                         "handle=?,detail_payload_hash=?,video_url=?,observed_at=? WHERE generation_id=? AND pid=? "
                         "AND video_id=?",
                         (detail['kalodataCreatorId'],detail['handle'],detail['payloadHash'],detail['videoUrl'],stamp,
                          generation,pid,row['video_id']))
        if network:
            store.db.execute('UPDATE kalodata_video_scan_job SET detail_requests=detail_requests+1,updated_at=? '
                             'WHERE generation_id=? AND pid=?',(stamp,generation,pid))


def _skip_detail(store,generation,pid,row,body,stamp):
    """A video whose detail names no author cannot yield a lead: keep the response hash as evidence and move on,
    unless so many are missing in this generation that the source itself looks broken."""
    with store.tx():
        missing=store.db.execute('SELECT count(*) FROM kalodata_video_scan_item WHERE generation_id=? AND detail_state=?',
                                 (generation,AUTHOR_MISSING)).fetchone()[0]
        if missing>=AUTHOR_MISSING_LIMIT:raise CycleError('kalodata_video_author_missing')
        store.db.execute('UPDATE kalodata_video_scan_item SET detail_state=?,detail_payload_hash=?,observed_at=? '
                         'WHERE generation_id=? AND pid=? AND video_id=?',
                         (AUTHOR_MISSING,digest(body),stamp,generation,pid,row['video_id']))
        store.db.execute('UPDATE kalodata_video_scan_job SET detail_requests=detail_requests+1,updated_at=? '
                         'WHERE generation_id=? AND pid=?',(stamp,generation,pid))


def _report(store,generation_row,job,clock):
    generation=job['generation_id'];pid=job['pid'];stamp=clock()
    pages=list(store.db.execute('SELECT * FROM kalodata_video_scan_page WHERE generation_id=? AND pid=? '
                                'ORDER BY page_no',(generation,pid)))
    items=list(store.db.execute("SELECT * FROM kalodata_video_scan_item WHERE generation_id=? AND pid=? "
                                "AND detail_state='resolved' ORDER BY source_rank,video_id",(generation,pid)))
    pending=store.db.execute("SELECT count(*) FROM kalodata_video_scan_item WHERE generation_id=? AND pid=? "
                             "AND detail_state NOT IN ('resolved',?)",(generation,pid,AUTHOR_MISSING)).fetchone()[0]
    if pending:raise CycleError('video_scan_detail_incomplete')
    # Like the single-PID collector, a video without an author is a gap, not full coverage.
    errors=[{'videoId':row[0],'code':'kalodata_video_author_missing'} for row in store.db.execute(
        'SELECT video_id FROM kalodata_video_scan_item WHERE generation_id=? AND pid=? AND detail_state=? '
        'ORDER BY source_rank,video_id',(generation,pid,AUTHOR_MISSING))]
    evidence=[]
    for row in items:
        evidence.append({'videoId':row['video_id'],'pid':pid,'kalodataCreatorId':row['kalodata_creator_id'],
            'handle':row['handle'],'views':row['views'],'sale':row['sale'],'revenueRaw':row['revenue_raw'],
            'releaseTime':row['release_time'],'duration':row['duration'],'description':row['description'],
            'videoUrl':row['video_url'],'contentType':row['content_type'],'isAd':bool(row['is_ad']),
            'isAi':bool(row['is_ai']),'payloadHash':row['detail_payload_hash'],'observedAt':row['observed_at']})
    fingerprints=[row['rows_fingerprint'] for row in pages]
    run_id='video-run-'+digest([generation,pid,fingerprints,[row['payloadHash'] for row in evidence]]+
                               ([errors] if errors else []))[:24]
    return {'schema':'bdhub.kalodata-video-evidence.v1','runId':run_id,'pid':pid,
        'windowStart':generation_row['window_start'],'windowEnd':generation_row['window_end'],
        'minViews':generation_row['min_views'],'maxVideos':0,'sortField':'create_time','maxPages':0,
        'pagesRead':len(pages),'rowsReceived':sum(row['rows_received'] for row in pages),
        'qualifyingVideos':len(items)+len(errors),'selectedVideos':len(items)+len(errors),'resolvedVideos':len(items),
        'coverage':'complete','listFingerprint':digest(fingerprints),'state':'completed_with_gaps' if errors else 'completed',
        'networkRequests':job['list_requests']+job['detail_requests'],'observedAt':stamp,
        'evidence':evidence,'errors':errors,'platformWrites':0,'realSends':0}


def _publish_current(store,report,generation_id):
    grouped={}
    for row in report['evidence']:
        key=row['kalodataCreatorId'];released=release_date(row['releaseTime'])
        if released is None:raise CycleError('kalodata_video_release_date_missing')
        choice=(row['views'],released.isoformat(),row['videoId'])
        if key not in grouped or choice>grouped[key][0]:grouped[key]=(choice,row,released.isoformat())
    with store.tx():
        store.db.execute('DELETE FROM video_lead_current WHERE pid=?',(report['pid'],))
        for creator_id,(_,row,released) in grouped.items():
            store.db.execute('INSERT INTO video_lead_current VALUES(?,?,?,?,?,?,?,?,?,?)',
              (generation_id,report['pid'],creator_id,row['handle'],report['runId'],row['videoId'],row['views'],released,
               row['sale'],report['observedAt']))


def scan_one(root,generation_id,requester,*,clock=time.time):
    with CycleStore(Path(root)/'var/second-cycle.sqlite') as store:
        generation=_generation(store,generation_id);job=next_job(store,generation_id)
        if not job:return {'status':'idle','generationId':generation_id,'networkRequests':0}
        pid=job['pid'];network=0
        if job['state']=='queued':
            with store.tx():store.db.execute("UPDATE kalodata_video_scan_job SET state='listing',updated_at=? "
                                             "WHERE generation_id=? AND pid=?",(clock(),generation_id,pid))
        while True:
            job=store.db.execute('SELECT * FROM kalodata_video_scan_job WHERE generation_id=? AND pid=?',
                                 (generation_id,pid)).fetchone()
            if job['state']!='listing':break
            page=job['next_page'];payload={'id':pid,'startDate':generation['window_start'],
                'endDate':generation['window_end'],'authority':True,'pageNo':page,'pageSize':50,
                'sort':[{'field':'create_time','type':'DESC'}]}
            body=requester(VIDEO_LIST_PATH,payload);network+=1
            parsed=parse_video_list(body,pid,window_start=generation['window_start'],
                                    window_end=generation['window_end'],min_views=generation['min_views'],page=page)
            if parsed['unknownDateVideoIds']:raise CycleError('kalodata_video_release_date_missing')
            _save_page(store,generation_id,pid,page,body,parsed,clock())
        while True:
            row=store.db.execute("SELECT * FROM kalodata_video_scan_item WHERE generation_id=? AND pid=? "
                                 "AND detail_state='pending' ORDER BY source_rank,video_id LIMIT 1",
                                 (generation_id,pid)).fetchone()
            if not row:break
            cached=store.db.execute('SELECT * FROM kalodata_video_author_cache WHERE video_id=?',
                                    (row['video_id'],)).fetchone()
            if cached:
                detail={'videoId':row['video_id'],'kalodataCreatorId':cached['kalodata_creator_id'],
                        'handle':cached['handle'],'payloadHash':cached['payload_hash'],'videoUrl':cached['video_url']}
                _save_detail(store,generation_id,pid,row,detail,clock(),network=False);continue
            payload={'id':row['video_id'],'startDate':generation['window_start'],
                     'endDate':generation['window_end'],'authority':True}
            body=requester(VIDEO_DETAIL_PATH,payload);network+=1
            try:detail=parse_video_detail(body,_candidate(row),clock())
            except CycleError as error:
                if str(error)!='kalodata_video_author_missing':raise
                _skip_detail(store,generation_id,pid,row,body,clock());continue
            _save_detail(store,generation_id,pid,row,detail,clock(),network=True)
        job=store.db.execute('SELECT * FROM kalodata_video_scan_job WHERE generation_id=? AND pid=?',
                             (generation_id,pid)).fetchone()
        report=_report(store,generation,job,clock);persist(store,report)
        _publish_current(store,report,generation_id)
        with store.tx():
            store.db.execute("UPDATE kalodata_video_scan_job SET state='completed',error=NULL,updated_at=? "
                             "WHERE generation_id=? AND pid=?",(clock(),generation_id,pid))
            remaining=store.db.execute("SELECT count(*) FROM kalodata_video_scan_job WHERE generation_id=? "
                                       "AND state<>'completed'",(generation_id,)).fetchone()[0]
            store.db.execute("UPDATE kalodata_video_generation SET state=?,updated_at=?,error=NULL WHERE generation_id=?",
                             ('completed' if remaining==0 else 'running',clock(),generation_id))
        return {'status':'completed','generationId':generation_id,'pid':pid,'pages':report['pagesRead'],
                'videos':report['resolvedVideos'],'authorMissing':len(report['errors']),
                'leads':len({row['kalodataCreatorId'] for row in report['evidence']}),
                'networkRequests':network,'platformWrites':0,'realSends':0}


def mark_stopped(root,generation_id,code,*,clock=time.time):
    state='paused_quota' if code=='kalodata_daily_quota_exhausted' else 'blocked'
    with CycleStore(Path(root)/'var/second-cycle.sqlite') as store,store.tx():
        _generation(store,generation_id)
        store.db.execute('UPDATE kalodata_video_generation SET state=?,updated_at=?,error=? WHERE generation_id=?',
                         (state,clock(),code,generation_id))


def status(root,generation_id):
    with CycleStore(Path(root)/'var/second-cycle.sqlite') as store:
        generation=_generation(store,generation_id)
        counts=Counter({row[0]:row[1] for row in store.db.execute(
            'SELECT state,count(*) FROM kalodata_video_scan_job WHERE generation_id=? GROUP BY state',(generation_id,))})
        requests=store.db.execute('SELECT COALESCE(sum(list_requests),0),COALESCE(sum(detail_requests),0) '
                                  'FROM kalodata_video_scan_job WHERE generation_id=?',(generation_id,)).fetchone()
        leads=store.db.execute('SELECT count(*),count(DISTINCT pid) FROM video_lead_current '
                               'WHERE generation_id=?',(generation_id,)).fetchone()
        missing=store.db.execute('SELECT count(*) FROM kalodata_video_scan_item WHERE generation_id=? '
                                 'AND detail_state=?',(generation_id,AUTHOR_MISSING)).fetchone()[0]
        current=store.db.execute("SELECT pid,state,next_page,list_requests,detail_requests,error FROM "
                                 "kalodata_video_scan_job WHERE generation_id=? AND state<>'completed' "
                                 "ORDER BY CASE state WHEN 'listing' THEN 0 WHEN 'detailing' THEN 0 ELSE 1 END,"
                                 "updated_at,priority_units DESC,pid LIMIT 1",(generation_id,)).fetchone()
        return {'schema':'bdhub.kalodata-video-crawl.v1','generationId':generation_id,
                'state':generation['state'],'scope':generation['scope_count'],'counts':dict(counts),
                'completed':counts['completed'],'remaining':generation['scope_count']-counts['completed'],
                'listRequests':requests[0],'detailRequests':requests[1],'authorMissing':missing,'videoLeads':leads[0],
                'leadPids':leads[1],'current':dict(current) if current else None,'error':generation['error'],
                'platformWrites':0,'realSends':0}
