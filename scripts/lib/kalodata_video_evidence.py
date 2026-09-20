"""Exact PID video evidence for bounded zero-sale second-outreach exploration.

The video list proves views and product association but does not carry author identity.  Each
qualified video is therefore resolved through the fixed video-detail endpoint before it can become
evidence.  This module never sends messages or changes send-pool eligibility.
"""
from collections import defaultdict
from contextlib import closing
from datetime import date
import json
from pathlib import Path
import re
import sqlite3
import time

from lib.second_cycle import CycleError, digest, encoded


VIDEO_LIST_PATH = '/product/detail/video/queryList'
VIDEO_DETAIL_PATH = '/video/detail'
HANDLE = re.compile(r'[a-z0-9_.]{1,24}\Z')
NUMERIC_ID = re.compile(r'[0-9]{8,32}\Z')


def compact_int(value):
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace(',', '')
    match = re.fullmatch(r'(\d+(?:\.\d+)?)\s*([kKmM万]?)', text)
    if not match:
        return None
    number = float(match.group(1)) * {'':1, 'k':1000, 'K':1000, 'm':1000000,
                                      'M':1000000, '万':10000}[match.group(2)]
    return int(round(number)) if 0 <= number <= 9_007_199_254_740_991 else None


def _text(value, maximum):
    text = str(value or '').strip()
    if any(ord(character) < 32 and character not in '\n\t' for character in text):
        raise CycleError('kalodata_video_text_invalid')
    return text[:maximum]


def release_date(value):
    text=str(value or '').strip()
    match=re.match(r'^(\d{4})[年/.-](\d{1,2})[月/.-](\d{1,2})日?',text)
    if not match:return None
    try:return date(*(int(part) for part in match.groups()))
    except ValueError:return None


def parse_video_list(body, pid, *, window_start, window_end, min_views=1000, page=1):
    if not isinstance(body, dict) or body.get('success') is not True or not isinstance(body.get('data'), list):
        raise CycleError('kalodata_video_list_invalid')
    rows = body['data']
    if len(rows) > 50 or any(not isinstance(row, dict) for row in rows):
        raise CycleError('kalodata_video_list_invalid')
    if not isinstance(pid, str) or not re.fullmatch(r'[0-9]{19}', pid) or type(min_views) is not int \
            or not 1 <= min_views <= 1_000_000_000 or type(page) is not int or page<1:
        raise CycleError('kalodata_video_scope_invalid')
    try:start=date.fromisoformat(window_start);end=date.fromisoformat(window_end)
    except (TypeError,ValueError):raise CycleError('kalodata_video_window_invalid') from None
    candidates=[];unknown_dates=[];ordered=[]
    for rank, row in enumerate(rows, start=(page-1)*50+1):
        video_id = str(row.get('id') or '')
        views=compact_int(row.get('views'));released=release_date(row.get('create_time'))
        if released is not None:ordered.append(released)
        elif NUMERIC_ID.fullmatch(video_id):unknown_dates.append(video_id)
        if not NUMERIC_ID.fullmatch(video_id) or views is None or views<min_views or released is None \
                or not start<=released<=end:continue
        candidates.append({'videoId': video_id, 'pid': pid, 'sourceRank': rank, 'views': views,
                           'sale': compact_int(row.get('sale')) or 0,
                           'revenueRaw': _text(row.get('revenue'), 80) or None,
                           'releaseTime': _text(row.get('create_time'), 80) or None,
                           'duration': _text(row.get('duration'), 40) or None,
                           'description': _text(row.get('description'), 2000),
                           'contentType': _text(row.get('content_type'), 40) or None,
                           'isAd': row.get('ad') in (1, True), 'isAi': row.get('ai_video') in (1, True),
                           'listPayloadHash': digest(row)})
    if any(left<right for left,right in zip(ordered,ordered[1:])):
        raise CycleError('kalodata_video_sort_invalid')
    oldest=min(ordered).isoformat() if ordered else None;newest=max(ordered).isoformat() if ordered else None
    return {'page':page,'rowsReceived':len(rows),'candidates':candidates,'unknownDateVideoIds':unknown_dates,
            'newestReleaseDate':newest,'oldestReleaseDate':oldest,
            'reachedWindowStart':bool(ordered and min(ordered)<=start),
            'listFingerprint':digest(rows)}


def parse_video_detail(body, candidate, observed_at):
    if not isinstance(body, dict) or body.get('success') is not True or not isinstance(body.get('data'), dict):
        raise CycleError('kalodata_video_detail_invalid')
    row = body['data'];video_id = str(row.get('id') or candidate['videoId'])
    if video_id != candidate['videoId']:
        raise CycleError('kalodata_video_identity_mismatch')
    handle = str(row.get('handle') or '').strip().lstrip('@').lower()
    creator_id = str(row.get('creator_id') or row.get('uid') or '')
    if not HANDLE.fullmatch(handle) or not NUMERIC_ID.fullmatch(creator_id):
        raise CycleError('kalodata_video_author_missing')
    views = compact_int(row.get('views'))
    views = candidate['views'] if views is None else views
    description = _text(row.get('description') or candidate['description'], 2000)
    return {'videoId': video_id, 'pid': candidate['pid'], 'kalodataCreatorId': creator_id,
            'handle': handle, 'views': views, 'sale': compact_int(row.get('sale')) or candidate['sale'],
            'revenueRaw': _text(row.get('revenue'), 80) or candidate['revenueRaw'],
            'releaseTime': _text(row.get('release_time') or row.get('create_time'), 80)
                           or candidate['releaseTime'],
            'duration': _text(row.get('duration'), 40) or candidate['duration'],
            'description': description,
            'videoUrl': f'https://www.tiktok.com/@{handle}/video/{video_id}',
            'contentType': _text(row.get('content_type'), 40) or candidate['contentType'],
            'isAd': row.get('ad') in (1, True) or candidate['isAd'],
            'isAi': row.get('ai_video') in (1, True) or candidate['isAi'],
            'payloadHash': digest(row), 'observedAt': observed_at}


def collect(pid, window_start, window_end, requester, *, min_views=1000, clock=time.time):
    try:
        if (date.fromisoformat(window_end)-date.fromisoformat(window_start)).days not in range(1, 180):
            raise ValueError
    except (TypeError, ValueError):
        raise CycleError('kalodata_video_window_invalid') from None
    pages=[];candidates=[];seen=set();page_fingerprints=set();coverage='unknown'
    page=1
    while True:
        payload={'id':pid,'startDate':window_start,'endDate':window_end,'authority':True,
                 'pageNo':page,'pageSize':50,'sort':[{'field':'create_time','type':'DESC'}]}
        listed=parse_video_list(requester(VIDEO_LIST_PATH,payload),pid,window_start=window_start,
                                window_end=window_end,min_views=min_views,page=page)
        if pages and pages[-1]['oldestReleaseDate'] and listed['newestReleaseDate'] and \
                listed['newestReleaseDate']>pages[-1]['oldestReleaseDate']:
            raise CycleError('kalodata_video_sort_invalid')
        if listed['listFingerprint'] in page_fingerprints:raise CycleError('kalodata_video_repeated_page')
        page_fingerprints.add(listed['listFingerprint'])
        pages.append(listed)
        for candidate in listed['candidates']:
            if candidate['videoId'] in seen:raise CycleError('kalodata_video_repeated_page')
            seen.add(candidate['videoId']);candidates.append(candidate)
        if listed['unknownDateVideoIds']:
            coverage='unknown_release_date';break
        if listed['rowsReceived']<50 or listed['reachedWindowStart']:
            coverage='complete';break
        page+=1
    observed_at = clock();evidence=[];errors=[]
    start=date.fromisoformat(window_start);end=date.fromisoformat(window_end)
    for candidate in candidates:
        try:
            detail = requester(VIDEO_DETAIL_PATH, {'id':candidate['videoId'], 'startDate':window_start,
                               'endDate':window_end, 'authority':True})
            row=parse_video_detail(detail,candidate,observed_at);released=release_date(row['releaseTime'])
            if released is None:raise CycleError('kalodata_video_release_date_missing')
            if start<=released<=end:evidence.append(row)
        except CycleError as error:
            errors.append({'videoId':candidate['videoId'], 'code':str(error)})
    fingerprints=[row['listFingerprint'] for row in pages]
    # A new observation must remain distinct even when the list page is byte-for-byte unchanged:
    # detail facts such as the author's current handle can still change between observations.
    run_id = 'video-run-' + digest([pid,window_start,window_end,min_views,
                                    fingerprints,observed_at,
                                    [row['payloadHash'] for row in evidence],errors])[:24]
    state='completed' if coverage=='complete' and not errors else 'completed_with_gaps'
    return {'schema':'bdhub.kalodata-video-evidence.v1', 'runId':run_id, 'pid':pid,
            'windowStart':window_start, 'windowEnd':window_end, 'minViews':min_views,
            'maxVideos':0,'sortField':'create_time','maxPages':0,'pagesRead':len(pages),
            'rowsReceived':sum(row['rowsReceived'] for row in pages),
            'qualifyingVideos':len(candidates),'selectedVideos':len(candidates),
            'resolvedVideos':len(evidence),'coverage':coverage,
            'listFingerprint':digest(fingerprints),'state':state,
            'networkRequests':len(pages)+len(candidates), 'observedAt':observed_at,
            'evidence':evidence, 'errors':errors, 'platformWrites':0, 'realSends':0}


def persist(store, report):
    if report.get('schema') != 'bdhub.kalodata-video-evidence.v1':
        raise CycleError('kalodata_video_report_invalid')
    required = {'kalodata_video_run','kalodata_video_evidence','kalodata_video_head'}
    found = {row[0] for row in store.db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?,?,?)", tuple(required))}
    columns={row[1] for row in store.db.execute('PRAGMA table_info(kalodata_video_run)')}
    paging={'sort_field','max_pages','pages_read','selected_videos','coverage'}
    if found != required or not paging<=columns:
        raise CycleError('second_cycle_schema_migration_required')
    with store.tx():
        prior = store.db.execute('SELECT * FROM kalodata_video_run WHERE run_id=?',(report['runId'],)).fetchone()
        frozen=(report['pid'],report['windowStart'],report['windowEnd'],report['minViews'],report['maxVideos'],
                report['listFingerprint'],report['state'],report['rowsReceived'],report['qualifyingVideos'],
                report['resolvedVideos'],report['networkRequests'],report['observedAt'],report['sortField'],
                report['maxPages'],report['pagesRead'],report['selectedVideos'],report['coverage'])
        if prior:
            observed=tuple(prior[key] for key in ('pid','window_start','window_end','min_views','max_videos',
                'list_fingerprint','state','rows_received','qualifying_videos','resolved_videos','network_requests',
                'observed_at','sort_field','max_pages','pages_read','selected_videos','coverage'))
            if observed != frozen:raise CycleError('kalodata_video_run_conflict')
            return {'runId':report['runId'],'cached':True,'videos':prior['resolved_videos']}
        store.db.execute('''INSERT INTO kalodata_video_run(
            run_id,pid,window_start,window_end,min_views,max_videos,list_fingerprint,state,
            rows_received,qualifying_videos,resolved_videos,network_requests,observed_at,
            sort_field,max_pages,pages_read,selected_videos,coverage)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(report['runId'],*frozen))
        for row in report['evidence']:
            store.db.execute('INSERT INTO kalodata_video_evidence VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (report['runId'],row['videoId'],row['pid'],row['kalodataCreatorId'],row['handle'],row['views'],
                 row['sale'],row['revenueRaw'],row['releaseTime'],row['duration'],row['description'],row['videoUrl'],
                 row['contentType'],int(row['isAd']),int(row['isAi']),row['payloadHash'],row['observedAt']))
        store.db.execute('INSERT INTO kalodata_video_head VALUES(?,?) ON CONFLICT(pid) DO UPDATE SET run_id=excluded.run_id',
                         (report['pid'],report['runId']))
    return {'runId':report['runId'],'cached':False,'videos':report['resolvedVideos']}


def zero_sale_candidates(root, store, pid, limit=20):
    leads = Path(root)/'var/kalodata-leads.sqlite';identities=Path(root)/'var/creator-identities.sqlite'
    if not leads.exists():return []
    zero_ids={};zero_handles=set();positive_ids=set();positive_handles=set()
    with closing(sqlite3.connect(leads.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        for (payload,) in db.execute('SELECT payload FROM leads_page WHERE pid=?',(pid,)):
            for row in json.loads(payload).get('sourceRows') or []:
                handle=str(row.get('handle') or '').strip().lstrip('@').lower()
                creator_id=str(row.get('id') or '');sale=compact_int(row.get('sale'))
                if not HANDLE.fullmatch(handle) or sale is None:continue
                if sale>0:
                    positive_handles.add(handle)
                    if NUMERIC_ID.fullmatch(creator_id):positive_ids.add(creator_id)
                elif NUMERIC_ID.fullmatch(creator_id):zero_ids[creator_id]=handle
                else:zero_handles.add(handle)
    for creator_id in positive_ids:zero_ids.pop(creator_id,None)
    zero_handles-=positive_handles
    known={}
    if identities.exists():
        with closing(sqlite3.connect(identities.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            known={str(row[1]).lower():str(row[0]) for row in db.execute(
                "SELECT creator_id,current_handle FROM creator_identity WHERE market='it' AND handle_conflict=0") if row[1]}
    head=store.db.execute('SELECT h.run_id,r.window_start,r.window_end,r.state,r.sort_field,r.coverage '
        'FROM kalodata_video_head h '
        'JOIN kalodata_video_run r ON r.run_id=h.run_id WHERE h.pid=?',(pid,)).fetchone()
    if not head:return []
    if head['state']!='completed' or head['sort_field']!='create_time' or head['coverage']!='complete':return []
    window_start=date.fromisoformat(head['window_start']);as_of=date.fromisoformat(head['window_end'])
    window_days=(as_of-window_start).days+1
    grouped=defaultdict(list)
    for row in store.db.execute('SELECT * FROM kalodata_video_evidence WHERE run_id=? ORDER BY views DESC',(head[0],)):
        # Kalodata creator ID is the stable author key.  Handle matching is only a fallback for
        # old source receipts that did not carry that ID; otherwise a rename would hide the pair.
        if row['kalodata_creator_id'] in zero_ids or row['handle'] in zero_handles:
            grouped[row['kalodata_creator_id']].append(dict(row))
    candidates=[]
    for kalodata_creator_id,rows in grouped.items():
        handle=rows[0]['handle'];source_handle=zero_ids.get(kalodata_creator_id,handle)
        dated=[(row,release_date(row['release_time'])) for row in rows]
        ages=[max(0,(as_of-released).days) for _,released in dated if released is not None]
        candidates.append({'handle':handle,'sourceHandle':source_handle,
            'kalodataCreatorId':kalodata_creator_id,'creatorId':known.get(handle),
            'knownOec':handle in known,'identityMatch':'current_handle_exact' if handle in known else 'unresolved',
            'pid':pid,'sourceSale':0,'windowDays':window_days,
            'videoCount':len(rows),'maxViews':max(row['views'] for row in rows),
            'maxVideoSale':max(row['sale'] for row in rows),
            'latestAgeDays':min(ages) if ages else None,
            'latestReleaseTime':max((row['release_time'] or '') for row in rows),
            'videos':[{'videoId':row['video_id'],'views':row['views'],'sale':row['sale'],
                       'revenueRaw':row['revenue_raw'],'releaseTime':row['release_time'],
                       'releaseDate':released.isoformat() if released else None,
                       'ageDaysAtObservation':max(0,(as_of-released).days) if released else None,
                       'description':row['description'][:240],'videoUrl':row['video_url'],
                       'isAd':bool(row['is_ad']),'isAi':bool(row['is_ai'])} for row,released in dated[:2]]})
    result=sorted(candidates,key=lambda row:(-row['maxViews'],row['handle']))
    return result if limit is None else result[:limit]


def status(root, store, pid=None):
    where=' WHERE h.pid=?' if pid else '';args=(pid,) if pid else ()
    runs=store.db.execute('SELECT count(*) FROM kalodata_video_run').fetchone()[0]
    videos=store.db.execute('SELECT count(*) FROM kalodata_video_evidence').fetchone()[0]
    heads=list(store.db.execute('SELECT h.pid,h.run_id,r.state,r.window_start,r.window_end,r.min_views,'
        'r.sort_field,r.max_pages,r.pages_read,r.rows_received,r.qualifying_videos,'
        'COALESCE(r.selected_videos,MIN(r.max_videos,r.qualifying_videos)) AS selected_videos,r.coverage,'
        'r.resolved_videos,r.network_requests,r.observed_at '
        'FROM kalodata_video_head h JOIN kalodata_video_run r ON r.run_id=h.run_id'+where+' ORDER BY r.observed_at DESC',args))
    candidates=[]
    for candidate_pid in ([pid] if pid else [row['pid'] for row in heads]):
        candidates.extend(zero_sale_candidates(root,store,candidate_pid,limit=None))
    candidates.sort(key=lambda row:(-row['maxViews'],row['pid'],row['handle']))
    complete_heads=[row for row in heads if row['state']=='completed' and row['sort_field']=='create_time'
                    and row['coverage']=='complete']
    summary={'pidsScanned':len(complete_heads),'headsObserved':len(heads),'candidatePairs':len(candidates),
             'pidsWithCandidates':len({row['pid'] for row in candidates}),
             'uniqueCreators':len({row['kalodataCreatorId'] for row in candidates}),
             'knownOecPairs':sum(bool(row['knownOec']) for row in candidates)}
    by_recency={}
    by_policy={}
    window_days=sorted({(date.fromisoformat(row['window_end'])-date.fromisoformat(row['window_start'])).days+1
                        for row in complete_heads})
    for days in window_days:
        eligible=[row for row in candidates if row['windowDays']==days and row['latestAgeDays'] is not None
                  and row['latestAgeDays']<=days]
        by_recency[str(days)+'d']={'candidatePairs':len(eligible),
            'pidsWithCandidates':len({row['pid'] for row in eligible}),
            'uniqueCreators':len({row['kalodataCreatorId'] for row in eligible}),
            'knownOecPairs':sum(bool(row['knownOec']) for row in eligible)}
        for views in (1000,5000,10000):
            qualified=[row for row in eligible if row['maxViews']>=views]
            by_policy[f'{days}d_views{views}']={'candidatePairs':len(qualified),
                'pidsWithCandidates':len({row['pid'] for row in qualified}),
                'uniqueCreators':len({row['kalodataCreatorId'] for row in qualified}),
                'knownOecPairs':sum(bool(row['knownOec']) for row in qualified),
                'pairsWithVideoSales':sum(row['maxVideoSale']>0 for row in qualified)}
    summary['unknownReleasePairs']=sum(row['latestAgeDays'] is None for row in candidates)
    return {'schema':'bdhub.kalodata-video-status.v1','available':True,'runs':runs,'videos':videos,
            'heads':[dict(row) for row in heads[:50]],'candidateSummary':summary,
            'candidateSummaryByRecency':by_recency,
            'candidateSummaryByPolicy':by_policy,
            'candidates':candidates[:20] if pid is None else candidates,
            'platformWrites':0,'realSends':0,'sendPoolChanged':False}
