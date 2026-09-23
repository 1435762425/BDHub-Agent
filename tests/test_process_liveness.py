import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.process_liveness import pid_alive


class ProcessLivenessTests(unittest.TestCase):
 def test_unreaped_child_does_not_block_worker_recovery(self):
  with patch('lib.process_liveness.os.kill'),patch('lib.process_liveness.subprocess.run',
      return_value=subprocess.CompletedProcess(['ps'],0,'Z    ','') ):
   self.assertFalse(pid_alive(1234))
  with patch('lib.process_liveness.os.kill'),patch('lib.process_liveness.subprocess.run',
      return_value=subprocess.CompletedProcess(['ps'],0,'S+   ','') ):
   self.assertTrue(pid_alive(1234))

if __name__=='__main__':unittest.main()
