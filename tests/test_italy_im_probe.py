"""Probe orchestration tests: fake runtime/auth; no old file or network mutation."""
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

PATH=Path(__file__).resolve().parents[1]/"scripts/probe-italy-im.py"
SPEC=importlib.util.spec_from_file_location("italy_im_probe_subject",PATH)
M=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(M)


class ItalyImProbeTests(unittest.TestCase):
    def test_child_holds_existing_guard_and_writes_only_safe_auth_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);identity_file=root/"identity.fixture";identity_file.write_text("FAKE_PRIVATE_IDENTITY")
            state={"locked":False,"calls":0}
            @contextmanager
            def guard(account):
                state["locked"]=True
                try: yield
                finally: state["locked"]=False
            def runtime():
                return SimpleNamespace(headers_json=identity_file),object(),lambda path:SimpleNamespace(headers={"cookie":"FAKE_PRIVATE_COOKIE"}),guard,lambda:False,"canary"
            def authenticate(account,identity,headers,report,**kwargs):
                self.assertTrue(state["locked"]);self.assertTrue(kwargs["use_environment_proxy"]);state["calls"]+=1
                report.update(apiHost="observed.example.invalid",tokenPresent=True)
                return SimpleNamespace(token="FAKE_PRIVATE_TOKEN")
            before=signal.getsignal(signal.SIGALRM)
            self.assertEqual(M.network_child(root,runtime_loader=runtime,authenticate=authenticate),0)
            report=json.loads((root/"report.json").read_text());self.assertEqual(report["status"],"completed");self.assertTrue(report["identityFileUnchanged"])
            self.assertEqual(identity_file.read_text(),"FAKE_PRIVATE_IDENTITY");self.assertFalse(state["locked"]);self.assertEqual(state["calls"],1)
            self.assertNotIn("FAKE_PRIVATE",json.dumps(report));self.assertEqual(stat.S_IMODE((root/"report.json").stat().st_mode),0o600)
            self.assertEqual(signal.getsignal(signal.SIGALRM),before);self.assertEqual(signal.getitimer(signal.ITIMER_REAL),(0.0,0.0))

    def test_busy_guard_prevents_auth_and_private_failure_text_never_leaks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);identity_file=root/"identity.fixture";identity_file.write_text("fixture")
            @contextmanager
            def guard(account):
                raise BlockingIOError("PRIVATE_PATH")
                yield
            runtime=lambda:(SimpleNamespace(headers_json=identity_file),object(),lambda p:None,guard,lambda:False,"canary")
            with patch.object(M,"authenticate_it",side_effect=AssertionError("no network")):
                result=M.network_child(root,runtime_loader=runtime,authenticate=lambda *_a,**_k: (_ for _ in ()).throw(AssertionError("no auth")))
            self.assertEqual(result,2);report=json.loads((root/"report.json").read_text());self.assertEqual(report["errorCode"],"guard_busy");self.assertNotIn("PRIVATE",json.dumps(report))

    def test_unique_output_and_scope_are_validated_before_child_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            var=Path(temporary);used=var/"used";used.mkdir()
            with patch.object(M,"VAR",var),patch.object(M.subprocess,"Popen") as child:
                with self.assertRaises(FileExistsError):M.main(["--output",str(used)])
                with self.assertRaises(ValueError):M.main(["--output",str(var.parent/"outside")])
                child.assert_not_called()

    def test_hard_parent_deadline_terminates_only_its_child_process_group(self):
        with tempfile.TemporaryDirectory() as temporary:
            var=Path(temporary);output=var/"probe";waits=[subprocess.TimeoutExpired("child",65),subprocess.TimeoutExpired("child",3),0]
            class Child:
                pid=999999
                def wait(self,timeout=None):
                    value=waits.pop(0)
                    if isinstance(value,Exception):raise value
                    return value
            with patch.object(M,"VAR",var),patch.object(M.subprocess,"Popen",return_value=Child()) as start,patch.object(M.os,"killpg") as terminate,patch("builtins.print"):
                self.assertEqual(M.main(["--output",str(output)]),2)
            self.assertEqual(terminate.call_args_list[0].args,(999999,signal.SIGTERM));self.assertEqual(terminate.call_args_list[1].args,(999999,signal.SIGKILL))
            self.assertIn("--network-child",start.call_args.args[0]);self.assertTrue(start.call_args.kwargs["start_new_session"])
            self.assertEqual(json.loads((output/"report.json").read_text())["errorCode"],"wall_timeout")

    def test_active_owner_error_is_reported_as_busy_not_as_auth_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);identity=root/"identity.fixture";identity.write_text("fixture")
            @contextmanager
            def guard(account):
                raise RuntimeError("account_in_use")
                yield
            runtime=lambda:(SimpleNamespace(headers_json=identity),object(),lambda p:None,guard,lambda:False,"canary")
            self.assertEqual(M.network_child(root,runtime_loader=runtime,authenticate=lambda *_a,**_k:None),2)
            self.assertEqual(json.loads((root/"report.json").read_text())["errorCode"],"guard_busy")

    def test_private_initial_projection_never_copies_debug_body_or_ticket(self):
        result=M.private_initial({"conversations":[{"conversationId":"10","oecId":"100","conversationType":2,"ticketPresent":True,"identitySource":"conversation_core.creator_oec_id","ticket":"PRIVATE_TICKET","message":"PRIVATE_BODY"}],
            "hasMore":True,"nextCursor":"1","invalidConversations":0,"otherMarketConversations":0,"messageBodiesDiscarded":5,"pageOnly":True,"completeInbox":False,"token":"PRIVATE_TOKEN","raw":"PRIVATE_BODY"})
        self.assertNotIn("PRIVATE",json.dumps(result));self.assertEqual(result["conversations"][0]["oecId"],"100")

    def test_cid_input_is_bounded_and_uses_private_file_not_command_line_identities(self):
        with tempfile.TemporaryDirectory() as temporary:
            var=Path(temporary);path=var/"targets.json"
            with patch.object(M,"VAR",var):
                path.write_text(json.dumps({"market":"it","conversations":[{"conversationId":"10","oecId":"100"}]}))
                self.assertEqual(M.read_verify_file(path),[{"conversationId":"10","oecId":"100","conversationType":2}])
                for value in ({"market":"mx","conversations":[]},{"market":"it","conversations":[{"conversationId":"10","oecId":"100","send":True}]},{"market":"it","conversations":[{"conversationId":10,"oecId":"100"}]}):
                    path.write_text(json.dumps(value))
                    with self.assertRaises(ValueError):M.read_verify_file(path)


if __name__=="__main__":unittest.main()
