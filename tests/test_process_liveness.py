import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.process_liveness import pid_alive, record_alive


def ps(stdout):
 return patch('lib.process_liveness.subprocess.run',return_value=subprocess.CompletedProcess(['ps'],0,stdout,''))


STARTED=time.mktime(time.strptime('Thu Oct  1 15:41:03 2026','%a %b %d %H:%M:%S %Y'))


class ProcessLivenessTests(unittest.TestCase):
 def test_unreaped_child_does_not_block_worker_recovery(self):
  with patch('lib.process_liveness.os.kill'),ps('Z    Thu Oct  1 15:41:03 2026\n'):
   self.assertFalse(pid_alive(1234))
  with patch('lib.process_liveness.os.kill'),ps('S+   Thu Oct  1 15:41:03 2026\n'):
   self.assertTrue(pid_alive(1234))

 def test_a_pid_taken_by_a_process_started_after_the_record_is_not_the_worker(self):
  # 10-01: the UK session record kept pid 3205 across a restart, now a Chrome helper started later.
  with patch('lib.process_liveness.os.kill'),ps('S    Thu Oct  1 15:41:03 2026\n'):
   self.assertFalse(pid_alive(3205,recorded_at=STARTED-3600))
   self.assertFalse(record_alive({'pid':3205,'state':'stopped','checkedAt':STARTED-3600}))
   self.assertTrue(pid_alive(3205,recorded_at=STARTED))
   # ps truncates to the second; a launcher's startedAt can trail the child's start inside it.
   self.assertTrue(pid_alive(3205,recorded_at=STARTED-0.5))
   self.assertTrue(record_alive({'pid':3205,'startedAt':STARTED-3600,'checkedAt':STARTED+5}))
   self.assertTrue(pid_alive(3205))
   self.assertTrue(record_alive({'pid':3205}))

 def test_another_users_process_is_judged_the_same_way(self):
  with patch('lib.process_liveness.os.kill',side_effect=PermissionError),ps('Ss   Thu Oct  1 15:41:03 2026\n'):
   self.assertFalse(pid_alive(3205,recorded_at=STARTED-60))
   self.assertTrue(pid_alive(3205,recorded_at=STARTED+60))

 def test_unreadable_start_time_keeps_a_live_worker_alive(self):
  for stdout in ('S\n','S    yesterday\n'):
   with patch('lib.process_liveness.os.kill'),ps(stdout):
    self.assertTrue(pid_alive(3205,recorded_at=STARTED),stdout)
  with patch('lib.process_liveness.os.kill'),patch('lib.process_liveness.subprocess.run',side_effect=OSError):
   self.assertTrue(pid_alive(3205,recorded_at=STARTED))
  with patch('lib.process_liveness.os.kill'),patch('lib.process_liveness.subprocess.run',
      return_value=subprocess.CompletedProcess(['ps'],1,'','')):
   self.assertFalse(pid_alive(3205,recorded_at=STARTED))
  for value in (None,[],{'pid':None},{'pid':'3205'},{'pid':0}):
   self.assertFalse(record_alive(value),value)

 def test_real_process_against_its_own_record(self):
  child=subprocess.Popen(['sleep','30'])
  try:
   recorded=time.time()
   self.assertTrue(record_alive({'pid':child.pid,'startedAt':recorded}))
   self.assertFalse(record_alive({'pid':child.pid,'startedAt':recorded-3600}))
  finally:
   child.kill();child.wait()
  self.assertFalse(record_alive({'pid':child.pid,'startedAt':recorded}))

if __name__=='__main__':unittest.main()
