"""Local source admission only: never an HTTP test or fake authentic data."""
import csv,gzip,hashlib,json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path[:0]=[str(ROOT/'src'),str(ROOT)]
from norway_company_agent.registry_snapshot_measurement import measure,SOURCE
from norway_company_agent.completion import verify_completion

class RegistrySnapshotMeasurement(unittest.TestCase):
    def setUp(self):
        (ROOT/'.release-gate-tmp').mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'.release-gate-tmp');self.addCleanup(self.temp.cleanup)
        self.d=Path(self.temp.name);self.source=self.d/'source.csv';self.batch=self.d/'batch.jsonl'
        self.batch.write_text('{"organisation_number":"923609016"}\n',encoding='utf-8')
        self.write()
    def write(self,rows=None):
        with self.source.open('w',encoding='utf-8',newline='') as f:
            w=csv.writer(f);w.writerow(['organisasjonsnummer','navn','aktivitet','vedtektsfestetFormaal'])
            w.writerows(rows or [['923609016','Søndre Øst AS','Utvikling\nog rådgivning','["Formål"]']])
    def call(self,**kwargs):
        return measure(self.source,self.batch,hashlib.sha256(self.source.read_bytes()).hexdigest(),kind='synthetic',workspace=self.d,**kwargs)
    def test_plain_and_gzip_same_exact_sample_coverage(self):
        a=self.call();self.source.write_bytes(gzip.compress(self.source.read_bytes()));b=self.call()
        self.assertEqual(a['company_matrix'],b['company_matrix']);self.assertEqual(b['matched'],1)
        self.assertEqual(a['selected_declarations'],{'activity':1,'purpose':1})
        self.assertEqual(a['external_requests'],0);self.assertNotEqual(a['source_sha256'],b['source_sha256'])
    def test_pin_and_byte_caps_fail_closed(self):
        with self.assertRaisesRegex(ValueError,'SHA-256 mismatch'):measure(self.source,self.batch,'0'*64,kind='synthetic')
        with self.assertRaisesRegex(ValueError,'compressed byte ceiling'):self.call(compressed_cap=1)
        self.source.write_bytes(gzip.compress(self.source.read_bytes()))
        with self.assertRaisesRegex(ValueError,'Decoded snapshot'):self.call(decoded_cap=16)
    def test_duplicate_even_outside_requested_sample_rejected(self):
        self.write([['993578843','Other','',''],['993578843','Other','','']])
        with self.assertRaisesRegex(ValueError,'Duplicate'):self.call()
    def test_invalid_identity_missing_schema_and_malformed_csv(self):
        variants=['organisasjonsnummer,navn\n123456789,Wrong\n','navn,aktivitet\nName,Text\n',
                  'organisasjonsnummer,navn,navn\n923609016,A,B\n',
                  'organisasjonsnummer,navn\n923609016,"unterminated\n']
        for text in variants:
            self.source.write_text(text,encoding='utf-8')
            with self.subTest(text=text),self.assertRaises((ValueError,csv.Error)):self.call()
    def test_corrupted_gzip_and_invalid_unicode_rejected(self):
        raw=self.source.read_bytes();self.source.write_bytes(gzip.compress(raw)[:-6])
        with self.assertRaises((EOFError,OSError)):self.call()
        self.source.write_bytes(raw+b'\xff')
        with self.assertRaises(UnicodeError):self.call()
    def test_acquisition_not_fabricated_or_future_dated(self):
        digest=hashlib.sha256(self.source.read_bytes()).hexdigest();a=self.d/'acquisition.json'
        base={'effective_url':SOURCE,'sha256':digest,'status':200,'started_at':'2026-10-01T00:00:00Z','completed_at':'2026-10-01T00:01:00Z'}
        for field,value in [('effective_url','https://example.com'),('sha256','0'*64),('completed_at','2999-01-01T00:00:00Z')]:
            a.write_text(json.dumps({**base,field:value}))
            with self.subTest(field=field),self.assertRaises(ValueError):measure(self.source,self.batch,digest,kind='current_public',acquisition=a)
        with self.assertRaisesRegex(ValueError,'requires acquisition'):measure(self.source,self.batch,digest,kind='current_public')
    def test_deadline_and_unknown_values_no_completion_claim(self):
        values=iter([0,2])
        with self.assertRaises(TimeoutError):self.call(max_seconds=1,clock=lambda:next(values))
        self.write([['923609016','Company','','[broken']]);r=self.call()
        self.assertEqual(r['selected_neither'],1);self.assertEqual(r['selected_declarations'],{'activity':0,'purpose':0})
    def test_measurement_cli_then_actual_runner_same_bytes_and_same_path_refusal(self):
        out=self.d/'measurement';digest=hashlib.sha256(self.source.read_bytes()).hexdigest()
        args=[sys.executable,'-S',str(ROOT/'scripts/measure_registry_snapshot.py'),'--source',str(self.source),'--sha256',digest,
            '--organisations',str(self.batch),'--snapshot-kind','synthetic','--output-dir',str(out)]
        p=subprocess.run(args,cwd=ROOT,capture_output=True,text=True);self.assertEqual(p.returncode,0,p.stderr)
        before=(out/'measurement.json').read_bytes()
        q=subprocess.run(args,cwd=ROOT,capture_output=True,text=True);self.assertNotEqual(q.returncode,0)
        self.assertEqual(before,(out/'measurement.json').read_bytes())
        env=os.environ.copy();env['TEMP']=env['TMP']=str(self.d)
        run=self.d/'run'
        command=[sys.executable,'-S',str(ROOT/'scripts/run_competition_batch.py'),'--organisations',str(self.batch),'--bulk',str(self.source),
            '--modules','registry,accounting_obligation','--network-policy','offline','--output-target','starter_compatible','--run-id','source-measurement',
            '--profiles-output',str(run/'profiles.jsonl'),'--output',str(run/'envelopes.jsonl'),'--report',str(run/'report.json'),'--viewer-output',str(run/'viewer.html')]
        r=subprocess.run(command,cwd=ROOT,env=env,capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stderr)
        verify_completion(report=run/'report.json',output=run/'envelopes.jsonl',profiles=run/'profiles.jsonl')
        row=json.loads((run/'envelopes.jsonl').read_text());fields={c['field'] for c in row['claims']}
        self.assertTrue({'registered_activity','registered_purpose'}<=fields)
        self.assertEqual(row['operations']['requests'],0)
    def test_failed_cli_leaves_only_failed_status_no_valid_cache(self):
        out=self.d/'failed';p=subprocess.run([sys.executable,'-S',str(ROOT/'scripts/measure_registry_snapshot.py'),
            '--source',str(self.source),'--sha256','0'*64,'--organisations',str(self.batch),'--snapshot-kind','synthetic','--output-dir',str(out)],
            cwd=ROOT,capture_output=True,text=True)
        self.assertNotEqual(p.returncode,0);self.assertFalse((out/'measurement.json').exists())
        self.assertEqual(json.loads((out/'status.json').read_text())['status'],'failed')
if __name__=='__main__':unittest.main()
