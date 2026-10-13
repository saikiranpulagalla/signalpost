"""Subprocess crash tests for the completion-manifest transaction boundary."""
from __future__ import annotations
import csv, hashlib, json, os, subprocess, sys, tempfile, textwrap, unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]; ORGS=["923609016","993578843"]
HARNESS=r'''
import importlib.util, sys
from pathlib import Path
spec=importlib.util.spec_from_file_location("runner",sys.argv[1]); runner=importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)
stage,batch,bulk,out,profiles,report,manifest=sys.argv[2:9]
original_write=runner.write_jsonl
if stage in ("c1","c2"):
 def broken(path,rows):
  if (stage=="c1" and Path(path)==Path(profiles)) or (stage=="c2" and Path(path)==Path(out)): raise OSError(stage)
  return original_write(path,rows)
 runner.write_jsonl=broken
if stage=="c3":
 original=runner.Path.write_text; calls=[0]
 def broken(self,*a,**kw):
  if Path(self)==Path(report):
   calls[0]+=1
   if calls[0]>1: raise OSError("c3")
  return original(self,*a,**kw)
 runner.Path.write_text=broken
if stage=="c4":
 original=runner.atomic_json; calls=[0]
 def broken(path,body):
  calls[0]+=1
  if calls[0]>1: raise OSError("c4")
  return original(path,body)
 runner.atomic_json=broken
sys.argv=[sys.argv[1],"--organisations",batch,"--bulk",bulk,"--output",out,"--profiles-output",profiles,"--report",report,"--completion-manifest",manifest,"--run-id","run-"+stage,"--expected-count","2","--modules","registry,accounting_obligation","--workers","1"]
runner.main()
'''
class MultiFileCommit(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();self.addCleanup(self.t.cleanup);self.d=Path(self.t.name);self.batch=self.d/'b.txt';self.bulk=self.d/'b.csv';self.out=self.d/'o.jsonl';self.pro=self.d/'p.jsonl';self.rep=self.d/'r.json';self.man=self.d/'m.json';self.batch.write_text('\n'.join(ORGS),encoding='utf8')
  with self.bulk.open('w',newline='',encoding='utf8') as h:
   w=csv.DictWriter(h,fieldnames=['organisasjonsnummer','navn','organisasjonsform.kode']);w.writeheader();[w.writerow({'organisasjonsnummer':o,'navn':o,'organisasjonsform.kode':'AS'}) for o in ORGS]
 def env(self):
  e=os.environ.copy();e['TEMP']=str(self.d);e['TMP']=str(self.d);return e
 def execute(self,stage='ok'):
  return subprocess.run([sys.executable,'-S','-c',textwrap.dedent(HARNESS),str(ROOT/'scripts/run_competition_batch.py'),stage,*map(str,(self.batch,self.bulk,self.out,self.pro,self.rep,self.man))],cwd=ROOT,text=True,capture_output=True,env=self.env(),timeout=20)
 def completed(self):
  m=json.loads(self.man.read_text());self.assertEqual(m['status'],'completed');self.assertEqual(m['envelope_sha256'],hashlib.sha256(self.out.read_bytes()).hexdigest());self.assertEqual(m['envelope_count'],2)
 def test_c1_c2_c3_c4_interrupted_attempts_never_complete_manifest(self):
  for stage in ('c1','c2','c3','c4'):
   with self.subTest(stage=stage):
    p=self.execute(stage);self.assertNotEqual(p.returncode,0);m=json.loads(self.man.read_text());self.assertEqual(m['status'],'in_progress');
    for f in (self.pro,self.out):
     if f.exists(): [json.loads(x) for x in f.read_text().splitlines() if x]
 def test_c5_c6_c7_c8_c9_old_output_is_not_current_completion_and_recovers(self):
  self.assertEqual(self.execute().returncode,0); old=self.out.read_bytes();self.completed()
  for stage in ('c2','c3','c4'):
   p=self.execute(stage);self.assertNotEqual(p.returncode,0);self.assertEqual(json.loads(self.man.read_text())['status'],'in_progress');self.assertTrue(self.out.exists())
  self.assertNotEqual(json.loads(self.man.read_text())['status'],'completed')
  self.assertEqual(self.execute().returncode,0);self.completed()
if __name__=='__main__':unittest.main()
