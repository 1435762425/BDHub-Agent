from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import signal
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

PATH=Path(__file__).resolve().parents[1]/'scripts/probe-italy-cards.py'
SPEC=importlib.util.spec_from_file_location('italy_cards_probe_subject',PATH);M=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(M)

class ItalyCardsProbeTests(unittest.TestCase):
    def test_guarded_child_persists_allowed_facts_without_changing_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);identity=root/'identity.fixture';identity.write_text('PRIVATE_IDENTITY');state={'held':False}
            @contextmanager
            def guard(account):
                state['held']=True
                try:yield
                finally:state['held']=False
            runtime=lambda:(SimpleNamespace(headers_json=identity),object(),lambda p:SimpleNamespace(headers={'cookie':'PRIVATE_COOKIE'}),guard,lambda:False)
            def reader(account,identity,headers,report,**kwargs):
                self.assertTrue(state['held']);report['bindingVerified']=True;return {'bindingVerified':True,'platformWrites':0}
            self.assertEqual(M.child(root,runtime_loader=runtime,reader=reader),0)
            report=json.loads((root/'report.json').read_text());self.assertTrue(report['identityFileUnchanged']);self.assertFalse(state['held'])
            self.assertEqual(identity.read_text(),'PRIVATE_IDENTITY');self.assertNotIn('PRIVATE',json.dumps(report));self.assertEqual(stat.S_IMODE((root/'card-facts.json').stat().st_mode),0o600)

    def test_parent_has_less_than_sixty_second_supervision_and_unique_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            var=Path(temporary);output=var/'probe';waits=[]
            class Child:
                pid=999999
                def wait(self,timeout=None):
                    waits.append(timeout)
                    if len(waits)==1:raise subprocess.TimeoutExpired('child',timeout)
                    return 0
            with patch.object(M,'VAR',var),patch.object(M.subprocess,'Popen',return_value=Child()) as start,patch.object(M.os,'killpg') as kill,patch('builtins.print'):
                self.assertEqual(M.main(['--output',str(output)]),2)
                with self.assertRaises(FileExistsError):M.main(['--output',str(output)])
            self.assertEqual(waits,[55,2]);self.assertEqual(kill.call_args.args,(999999,signal.SIGTERM));self.assertEqual(start.call_count,1)
            self.assertEqual(json.loads((output/'report.json').read_text())['errorCode'],'wall_timeout')

if __name__=='__main__':unittest.main()
