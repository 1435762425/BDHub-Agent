"""Lifecycle helpers for BR/UK continuous-send workers."""
import json,os,subprocess,time
from pathlib import Path

def state_path(root,market):return Path(root)/f'var/market-send-worker-{market}.json'
def state(root,market):
 try:value=json.loads(state_path(root,market).read_text(encoding='utf-8'))
 except (OSError,ValueError):return {'running':False,'state':'off','pid':None}
 pid=value.get('pid');alive=False
 if type(pid) is int and pid>0:
  try:os.kill(pid,0);alive=True
  except OSError:pass
 return value|{'running':alive and value.get('running') is True}
def launch(root,market):
 root=Path(root);current=state(root,market)
 if current['running']:return current
 log=root/f'var/market-send-worker-{market}.log'
 with log.open('a',encoding='utf-8') as handle:
  child=subprocess.Popen([str(root/'.venv/bin/python'),str(root/'scripts/market-send-worker.py'),'--market',market],cwd=str(root),stdin=subprocess.DEVNULL,stdout=handle,stderr=subprocess.STDOUT,start_new_session=True,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
 value={'running':True,'state':'starting','pid':child.pid,'startedAt':time.time()};state_path(root,market).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');return value
