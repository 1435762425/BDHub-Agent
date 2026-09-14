#!/usr/bin/env python3
"""Local task-card API. No arbitrary paths, commands or platform calls."""
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True
sys.path.insert(0,str(ROOT/'scripts'))
from lib.batch_task_service import TaskService
from lib.batch_tasks import BatchError

def dispatch(service,body):
    if not isinstance(body,dict):raise BatchError('invalid_request')
    action=body.get('action')
    keys={'list':{'action'},'preview':{'action','spec'},'confirm':{'action','token','requestKey'},'detail':{'action','id'},'pause':{'action','id','revision'},'resume':{'action','id','revision'},'priority':{'action','id','revision','priority'}}
    if action not in keys or set(body)!=keys[action]:raise BatchError('invalid_request')
    for key in ('id','token','requestKey'):
        if key in body and (not isinstance(body[key],str) or not 1<=len(body[key])<=120):raise BatchError('invalid_request')
    if action=='list':return service.listing()
    if action=='preview':return service.preview(body['spec'])
    if action=='confirm':return service.confirm(body['token'],body['requestKey'])
    if action=='detail':return service.detail(body['id'])
    return service.control(body['id'],action,body['revision'],body.get('priority'))

def main():
    body=json.loads(sys.stdin.read(65537))
    service=TaskService(ROOT/'var/batch-tasks.sqlite')
    try:print(json.dumps(dispatch(service,body),ensure_ascii=False))
    finally:service.close()
if __name__=='__main__':
    try:main()
    except (BatchError,ValueError,TypeError) as e:print(json.dumps({'error':str(e) if isinstance(e,BatchError) else 'invalid_request'}));sys.exit(2)
    except Exception:print(json.dumps({'error':'task_store_unavailable'}));sys.exit(1)
