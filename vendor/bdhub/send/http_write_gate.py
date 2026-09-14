"""同一远端IM身份FIFO写队列；文件锁只保护元数据，不包住网络或节拍等待。"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
import time
from uuid import uuid4

from bdhub import config


def _alive(pid):
    try:os.kill(int(pid),0)
    except ProcessLookupError:return False
    except PermissionError:return True
    return True


@contextmanager
def _state(path,deadline,stopped):
    fd=os.open(path,os.O_RDWR|os.O_CREAT,0o600)
    with os.fdopen(fd,'r+',encoding='utf-8') as handle:
        while True:
            if stopped():raise ValueError('http_trial_stopped_before_dispatch')
            try:
                fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic()>=deadline:raise ValueError('http_write_gate_timeout')
                time.sleep(.025)
        try:
            raw=handle.read();state=json.loads(raw) if raw else {'last_dispatch':0.0}
            before=json.dumps(state)
            state.setdefault('queue',[])
            try:yield state
            finally:
                if json.dumps(state)!=before:
                    handle.seek(0);json.dump(state,handle)
                    handle.truncate();handle.flush();os.fsync(handle.fileno())
        finally:fcntl.flock(handle,fcntl.LOCK_UN)


@contextmanager
def shared_write_gate(sender_id, *, interval, stopped=lambda:False, directory=None):
    if not math.isfinite(interval) or interval<=0:raise ValueError('http_write_interval_invalid')
    root=directory or config.ROOT/'data/runtime/im-http-write-gates'
    root.mkdir(parents=True,exist_ok=True)
    path=root/(hashlib.sha256(str(sender_id).encode()).hexdigest()+'.lock')
    deadline=time.monotonic()+60
    token=uuid4().hex;registered=False
    try:
        with _state(path,deadline,stopped) as state:
            if len(state['queue'])>=64:raise ValueError('http_write_queue_full')
            state['queue'].append({'token':token,'pid':os.getpid(),'active':False})
            registered=True
        while True:
            if stopped():raise ValueError('http_trial_stopped_before_dispatch')
            if time.monotonic()>=deadline:raise ValueError('http_write_gate_timeout')
            acquired=False
            with _state(path,deadline,stopped) as state:
                for entry in list(state['queue']):
                    if not _alive(entry['pid']):
                        if entry.get('active'):raise ValueError('http_write_owner_lost')
                        state['queue'].remove(entry)
                last=float(state['last_dispatch'])
                if not math.isfinite(last) or last>time.time()+60:raise ValueError('http_write_clock_invalid')
                if state['queue'] and state['queue'][0]['token']==token and time.time()>=last+interval:
                    state['queue'][0]['active']=True;acquired=True
            if acquired:break
            time.sleep(.025)
        def mark():
            with _state(path,time.monotonic()+60,stopped) as state:
                if not state['queue'] or state['queue'][0]['token']!=token or not state['queue'][0].get('active'):
                    raise ValueError('http_write_owner_changed')
                state['last_dispatch']=time.time()
        yield mark
    finally:
        if registered:
            with _state(path,time.monotonic()+60,lambda:False) as state:
                state['queue']=[entry for entry in state['queue'] if entry['token']!=token]
