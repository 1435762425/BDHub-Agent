import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from lib.legacy_runtime import configure_vendored_bdhub
configure_vendored_bdhub(root=ROOT)
from bdhub.send import http_write_gate as gate


class WriteGatePersistenceTests(unittest.TestCase):
    def test_shorter_state_has_no_previous_tail_and_preserves_clock(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'state.lock'
            path.write_text(json.dumps({'last_dispatch':123.5,'queue':[{'token':'x'*32,'pid':123,'active':True}]}))
            with gate._state(path,time.monotonic()+1,lambda:False) as state:state['queue']=[]
            self.assertEqual(json.loads(path.read_text()),{'last_dispatch':123.5,'queue':[]})
            with gate._state(path,time.monotonic()+1,lambda:False) as state:
                self.assertEqual(state,{'last_dispatch':123.5,'queue':[]})
            self.assertEqual(path.stat().st_mode&0o777,0o600)

    def test_failed_publication_keeps_previous_state_and_removes_temporary(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'state.lock';before='{"last_dispatch": 123.5, "queue": []}'
            path.write_text(before)
            with patch.object(gate.os,'replace',side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    with gate._state(path,time.monotonic()+1,lambda:False) as state:state['last_dispatch']=999
            self.assertEqual(path.read_text(),before)
            self.assertEqual(sorted(p.name for p in Path(folder).iterdir()),['state.lock','state.lock.guard'])

    def test_malformed_state_fails_without_resetting_original_queue(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'state.lock';before='{"last_dispatch":123,"queue":[]}old queue'
            path.write_text(before)
            with self.assertRaisesRegex(ValueError,'http_write_gate_state_invalid'):
                with gate._state(path,time.monotonic()+1,lambda:False):self.fail('must not dispatch')
            self.assertEqual(path.read_text(),before)

    def test_parallel_writers_keep_one_fifo_despite_state_inode_replacement(self):
        code='''import sys,time,json,os
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from bdhub.send.http_write_gate import shared_write_gate
folder=Path(sys.argv[2]);label=sys.argv[3]
with shared_write_gate('sender',interval=.01,directory=folder) as mark:
 with (folder/'events').open('a') as f:f.write(label+' enter\\n');f.flush()
 time.sleep(.15);mark()
 with (folder/'events').open('a') as f:f.write(label+' exit\\n');f.flush()
'''
        with tempfile.TemporaryDirectory() as folder:
            command=[sys.executable,'-c',code,str(ROOT/'vendor'),folder]
            first=subprocess.Popen(command+['first'],env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
            try:
                deadline=time.monotonic()+5
                while not (Path(folder)/'events').exists():
                    if time.monotonic()>deadline:self.fail('first writer did not enter')
                    time.sleep(.01)
                second=subprocess.Popen(command+['second'],env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
                try:self.assertEqual(first.wait(timeout=10),0);self.assertEqual(second.wait(timeout=10),0)
                finally:
                    if second.poll() is None:second.terminate();second.wait()
                self.assertEqual((Path(folder)/'events').read_text().splitlines(),
                                 ['first enter','first exit','second enter','second exit'])
                state=json.loads(next(Path(folder).glob('*.lock')).read_text())
                self.assertEqual(state['queue'],[]);self.assertGreater(state['last_dispatch'],0)
            finally:
                if first.poll() is None:first.terminate();first.wait()


if __name__=='__main__':unittest.main()
