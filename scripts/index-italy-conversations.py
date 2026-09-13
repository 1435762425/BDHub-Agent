#!/usr/bin/env python3
"""Bounded resumable IT/ACC6 metadata scan. Read-only platform; local index only."""
import argparse,json,signal,sys
from pathlib import Path
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from lib.cycle_conversations import ConversationIndex
from lib.second_live_runtime import _authenticated
from lib.italy_im_session import ItalyImReadSession

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--pages',type=int,default=15);a=p.parse_args()
 if not 1<=a.pages<=30:p.error('pages must be 1..30')
 index=ConversationIndex(ROOT/'var/it-conversations.sqlite');scope='it:acc6';report={};reads=None
 def timeout(*_):raise TimeoutError('scan_deadline')
 signal.signal(signal.SIGALRM,timeout);signal.setitimer(signal.ITIMER_REAL,55)
 try:
  state=index.state(scope)
  if state['state']!='complete':
   with _authenticated(report,stopped=lambda:False) as (_,_,_,auth,maintenance,available):
    reads=ItalyImReadSession(auth,report,maintenance_due=maintenance)
    for _ in range(a.pages):
     state=index.state(scope)
     if state['state']=='complete':break
     page=reads.initialize(state['cursor']);index.save(scope,state['cursor'],page)
 except Exception as e:report['errorCode']=getattr(e,'code',type(e).__name__)
 finally:
  signal.setitimer(signal.ITIMER_REAL,0)
  if reads:reads.close()
  report.update(scan=index.state(scope),indexedConversations=index.db.execute('SELECT count(*) FROM conversation WHERE scope=?',(scope,)).fetchone()[0],realSends=0)
  (ROOT/'var/it-conversations-scan.json').write_text(json.dumps(report,indent=2)+'\n');index.close()
  print(json.dumps({k:report[k] for k in ('scan','indexedConversations','realSends')}))
if __name__=='__main__':main()
