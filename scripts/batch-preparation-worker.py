#!/usr/bin/env python3
"""Task preparation worker; scoped HTTP/identity/names/cards, never IM messages."""
import fcntl,json,os,signal,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True
sys.path.insert(0,str(ROOT/'scripts'))
from lib.batch_task_service import TaskService,read_local_preparation
from lib.batch_source_runtime import advance_sources
from lib.batch_material_runtime import advance_materials

def main():
    folder=ROOT/'var/batch-preparation';folder.mkdir(exist_ok=True)
    with (folder/'worker.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        pid=folder/'worker.pid';pid.write_text(str(os.getpid()))
        running=True
        def stop(*_):
            nonlocal running
            running=False
        signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
        service=TaskService(ROOT/'var/batch-tasks.sqlite')
        def prepare(task):
            advance_sources(service,task,ROOT)
            advance_materials(service,task,ROOT)
        try:
            while running:
                try:
                    work=service.tick(lambda spec:read_local_preparation(ROOT,spec),prepare=prepare)
                except Exception:
                    # Never place raw credentials, responses or creator data in log.
                    print(json.dumps({'event':'local_preparation_retry','at':time.time()}),flush=True);work=False
                if not work:time.sleep(2)
        finally:
            service.close();pid.unlink(missing_ok=True)
if __name__=='__main__':main()
