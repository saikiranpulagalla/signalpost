"""Persisted-JSONL resume integrity tests through the actual evaluator CLI."""
from __future__ import annotations
import csv, json, os, subprocess, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
ORGS = ["923609016", "993578843", "910608193"]

class ResumeIntegrationV2A4(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory(); self.addCleanup(self.t.cleanup); self.root=Path(self.t.name)
        self.batch=self.root/'batch.txt'; self.bulk=self.root/'bulk.csv'; self.profiles=self.root/'profiles.jsonl'; self.out=self.root/'out.jsonl'; self.report=self.root/'report.json'
        self._batch(ORGS); self._bulk(ORGS)
    def _env(self):
        e=os.environ.copy(); e['TEMP']=str(self.root); e['TMP']=str(self.root); return e
    def _batch(self, orgs): self.batch.write_text('\n'.join(orgs)+'\n',encoding='utf-8')
    def _bulk(self, orgs, suffix=''):
        with self.bulk.open('w',encoding='utf-8',newline='') as h:
            w=csv.DictWriter(h,fieldnames=['organisasjonsnummer','navn','organisasjonsform.kode']); w.writeheader()
            for i,o in enumerate(orgs): w.writerow({'organisasjonsnummer':o,'navn':f'Co {i}{suffix}','organisasjonsform.kode':'AS'})
    def _run(self,resume=False,modules='registry,accounting_obligation'):
        p=subprocess.run([sys.executable,'-S','scripts/run_competition_batch.py','--organisations',str(self.batch),'--bulk',str(self.bulk),'--output',str(self.out),'--profiles-output',str(self.profiles),'--report',str(self.report),'--run-id','resume-test','--expected-count',str(len(self.batch.read_text().splitlines())),'--modules',modules,'--workers','1']+(['--resume'] if resume else []),cwd=ROOT,text=True,capture_output=True,env=self._env(),timeout=20)
        return p
    def _valid(self, orgs):
        from norway_company_agent.batch import validate_envelopes
        rows=[json.loads(x) for x in self.out.read_text(encoding='utf-8').splitlines() if x]
        self.assertEqual([x['organisation_number'] for x in rows],orgs); self.assertTrue(validate_envelopes(rows,orgs)['passed'])
    def test_r01_r02_r03_r04_identity_keyed_reuse(self):
        self.assertEqual(self._run().returncode,0); self._valid(ORGS)
        for orgs in (ORGS, [ORGS[2],ORGS[0],ORGS[1]], [ORGS[1],ORGS[2],ORGS[0]]):
            self._batch(orgs); p=self._run(True); self.assertEqual(p.returncode,0,p.stderr); self._valid(orgs)
            r=json.loads(self.report.read_text()); self.assertGreaterEqual(r['resumed_profiles'],len(orgs))
        self._batch([ORGS[0],ORGS[2]]); p=self._run(True); self.assertNotEqual(p.returncode,0)
    def test_r05_r06_recompute_on_module_or_snapshot_mismatch(self):
        self.assertEqual(self._run().returncode,0)
        p=self._run(True,'accounting_obligation,registry'); self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual(json.loads(self.report.read_text())['resumed_profiles'],0)
        self._bulk(ORGS,' changed'); p=self._run(True); self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual(json.loads(self.report.read_text())['resumed_profiles'],0); self._valid(ORGS)
    def test_r07_r08_r09_r10_r11_corrupt_or_forged_history_is_not_published(self):
        self.assertEqual(self._run().returncode,0)
        rows=[json.loads(x) for x in self.profiles.read_text().splitlines()]
        rows[0]['organisation_number']=ORGS[1]; rows[1]['evidence']['registry']['source_url']='bad'; rows[2]['evidence']['registry']['retrieved_at']='2999-01-01T00:00:00Z'
        rows[0]['evidence']['website']={'field':'website','status':'available','source_url':'https://evil.invalid','retrieved_at':'2026-01-01T00:00:00Z','value':{'publishable':True}}
        self.profiles.write_text('\n'.join(json.dumps(x) for x in rows)+'\n{truncated',encoding='utf-8')
        p=self._run(True); self.assertNotEqual(p.returncode,0); self.assertFalse(self.out.exists() and self.report.exists() and json.loads(self.report.read_text()).get('validation',{}).get('passed'))
    def test_r15_stale_success_is_not_a_current_success_after_invalid_attempt(self):
        self.assertEqual(self._run().returncode,0); old=self.out.read_bytes()
        rows=[json.loads(x) for x in self.profiles.read_text().splitlines()]; rows[0]['evidence']['registry']['source_url']='bad'; self.profiles.write_text('\n'.join(json.dumps(x) for x in rows),encoding='utf-8')
        p=self._run(True); self.assertNotEqual(p.returncode,0); self.assertEqual(self.out.read_bytes(),old)
        report=json.loads(self.report.read_text()); self.assertFalse(report['validation']['passed']); self.assertEqual(report['run_id'],'resume-test')

if __name__=='__main__': unittest.main()
