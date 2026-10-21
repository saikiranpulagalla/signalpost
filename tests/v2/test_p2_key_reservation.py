import os,json,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path[:0]=[str(ROOT),str(ROOT/'tests/v2')]
import test_jsonl_lifecycle as fixtures

class KeyReservation(unittest.TestCase):
    def test_default_and_explicit_key_cannot_alias_any_public_artifact(self):
        for explicit in (False,True):
            for flag in ('--output','--profiles-output','--report','--viewer-output','--completion-manifest','--request-telemetry'):
                if not explicit and flag=='--report':
                    continue  # Default key is derived from the NEW report path.
                helper=fixtures.JsonlLifecycle();helper.setUp()
                try:
                    d=helper.d/'collision';d.mkdir();key=d/('private.key' if explicit else 'report.json.observation-auth.key')
                    key.write_bytes(b'k'*32);before=key.read_bytes()
                    args=[flag,str(key)]+(['--observation-key',str(key)] if explicit else [])
                    p,_=helper.run_cli('collision',rich=False,extra=args)
                    self.assertNotEqual(p.returncode,0,(explicit,flag,p.stdout))
                    self.assertIn('observation key',p.stderr.lower())
                    self.assertEqual(key.read_bytes(),before)
                    self.assertFalse((d/'envelopes.jsonl').exists());self.assertFalse((d/'report.json.completion.json').exists())
                finally:helper.doCleanups()
    def test_existing_hardlink_alias_is_reserved(self):
        helper=fixtures.JsonlLifecycle();helper.setUp()
        try:
            key=helper.d/'private.key';key.write_bytes(b'k'*32)
            alias=helper.d/'alias.jsonl';os.link(key,alias)
            p,d=helper.run_cli('linked',rich=False,extra=['--observation-key',str(key),'--output',str(alias)])
            self.assertNotEqual(p.returncode,0,p.stdout);self.assertIn('observation key',p.stderr.lower())
            self.assertEqual(alias.read_bytes(),b'k'*32)
        finally:helper.doCleanups()
    def test_completed_artifact_set_survives_collision_refusal_unchanged(self):
        helper=fixtures.JsonlLifecycle();helper.setUp()
        try:
            p,d=helper.run_cli('completed',rich=False);helper.checked(p,d)
            saved={f.name:f.read_bytes() for f in d.iterdir() if f.is_file()}
            key=d/'report.json.observation-auth.key'
            p,_=helper.run_cli('completed',rich=False,extra=['--output',str(key)])
            self.assertNotEqual(p.returncode,0);self.assertIn('observation key',p.stderr.lower())
            self.assertEqual({f.name:f.read_bytes() for f in d.iterdir() if f.is_file()},saved)
        finally:helper.doCleanups()
