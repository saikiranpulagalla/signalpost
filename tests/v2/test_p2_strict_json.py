"""Non-finite data must not cross the source or publication trust boundary."""
import json,sys,unittest,subprocess
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2];sys.path[:0]=[str(ROOT),str(ROOT/'src'),str(ROOT/'tests/v2')]
from v0_kernel.storage import read_jsonl_bytes,atomic_jsonl
import test_jsonl_lifecycle as fixtures

class StrictJson(unittest.TestCase):
    def test_direct_refresh_entrypoint_finds_shared_codec_from_unrelated_cwd(self):
        helper=fixtures.JsonlLifecycle();helper.setUp()
        try:
            p=subprocess.run([sys.executable,'-B','-S',str(ROOT/'scripts/run_refresh_replay.py'),'--help'],cwd=helper.d,capture_output=True,text=True,timeout=10)
            self.assertEqual(p.returncode,0,p.stderr)
        finally:helper.doCleanups()
    def test_stdlib_transport_rejects_nonfinite_and_finite_codec_is_compatible(self):
        import test_transport_deadline_repairs as transport
        from v0_kernel.json_codec import loads,dumps
        helper=transport.TransportDeadlineRepairs();helper.setUp()
        try:
            bad=helper.fetch(transport.response(b'{"employees":NaN}'),timeout=1)
            self.assertEqual(bad.error,'StrictJSONError');self.assertIsNone(bad.body)
            self.assertEqual(bad.raw_bytes,b'{"employees":NaN}')
        finally:helper.doCleanups()
        value={'text':'Ø\u2028\u2029\u0085','amounts':[0,-0.0,123456789.75,9007199254740993]}
        self.assertEqual(dumps(value,ensure_ascii=False,sort_keys=True),json.dumps(value,ensure_ascii=False,sort_keys=True))
        self.assertEqual(loads(dumps(value)),value)
    def test_nonfinite_physical_records_are_rejected(self):
        for token in ('NaN','Infinity','-Infinity','1e400'):
            with self.subTest(token=token),self.assertRaises(ValueError):
                read_jsonl_bytes(('{"value":'+token+'}\n').encode())
    def test_atomic_writer_rejects_nonfinite_without_replacing_old_bytes(self):
        helper=fixtures.JsonlLifecycle();helper.setUp()
        try:
            output=helper.d/'atomic.jsonl';output.write_bytes(b'{"previous":true}\n')
            for number in (float('nan'),float('inf'),-float('inf')):
                with self.subTest(number=number),self.assertRaises(ValueError):atomic_jsonl(output,[{'nested':[number]}])
                self.assertEqual(output.read_bytes(),b'{"previous":true}\n')
        finally:helper.doCleanups()
    def test_fresh_nonfinite_source_is_fatal_under_both_policies(self):
        harness=fixtures.HARNESS.replace("body={'_embedded':{'underenheter':[]},'page':{'totalPages':1}}", "body={'_embedded':{'underenheter':[{'organisasjonsnummer':'910608193','overordnetEnhet':org,'antallAnsatte':float('nan')}]},'page':{'totalPages':1}}")
        # Match the real transport's decoded representation, including the
        # stdlib decoder's non-finite singleton (not a separately made NaN).
        harness=harness.replace(';return FetchResult(url,200', ';body=json.loads(raw);return FetchResult(url,200')
        for policy in ('strict','best_effort'):
            helper=fixtures.JsonlLifecycle();helper.setUp()
            try:
                with patch.object(fixtures,'HARNESS',harness):p,d=helper.run_cli('nonfinite-'+policy,policy)
                self.assertNotEqual(p.returncode,0,p.stdout)
                self.assertNotEqual(json.loads((d/'report.json.completion.json').read_bytes()).get('status'),'completed')
                if (d/'envelopes.jsonl').exists():
                    for raw in (d/'envelopes.jsonl').read_bytes().split(b'\n'):
                        if raw:json.loads(raw,parse_constant=lambda token:(_ for _ in ()).throw(ValueError(token)))
            finally:helper.doCleanups()
