import json,subprocess,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

class RetiredLegacyBulk(unittest.TestCase):
 def test_old_cli_cannot_create_resume_or_send_a_batch(self):
  result=subprocess.run([sys.executable,str(ROOT/'scripts/bulk-second-send.py'),'--batch-id','legacy-test','--target','1'],cwd=ROOT,capture_output=True,text=True,timeout=10)
  self.assertEqual(result.returncode,2)
  self.assertEqual(json.loads(result.stdout)['error'],'legacy_bulk_sender_retired')

if __name__=='__main__':unittest.main()
