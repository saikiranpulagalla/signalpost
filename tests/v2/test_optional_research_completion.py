"""Actual CLI graceful-source completion; no external source networking."""
import csv,copy,hashlib,json,os,re,subprocess,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tests/v2')]
import test_admission_configuration_repairs as fixtures
from norway_company_agent.completion import verify_completion
from norway_company_agent.evaluator_adapter import validate_starter_envelopes,starter_availability

INJECT=r'''
original=fetch
injection_fired=set()
def controlled(url,**kwargs):
 result=original(url,**kwargs)
 with lock:
  trigger=mode not in injection_fired and (mode!='terminate' or len(starts)>=4)
  if trigger:injection_fired.add(mode)
 if mode=='terminate' and trigger:
  __import__('time').sleep(.25);__import__('os')._exit(17)
 if mode=='429' and trigger:
  return FetchResult(url,429,result.elapsed_ms,0,error='HTTP 429',retry_after='3600',retrieved_at='2026-10-01T00:00:00Z')
 if mode=='crash' and trigger:raise RuntimeError('Synthetic transport worker crash')
 if mode=='integrity_exception' and trigger:raise ValueError('Synthetic integrity rejection')
 if mode=='identity' and trigger:
  result.body[0]['virksomhet']['organisasjonsnummer']='993578843'
  result.raw_bytes=json.dumps(result.body,sort_keys=True).encode();result.bytes_received=len(result.raw_bytes);result.content_sha256=hashlib.sha256(result.raw_bytes).hexdigest()
 if mode=='corrupt' and trigger:result.content_sha256='0'*64
 if mode=='projection' and trigger:result.body[0]['resultatregnskapResultat']['aarsresultat']=999999
 if mode=='timeout' and trigger:return FetchResult(url,0,10000,0,error='TimeoutError',retrieved_at='2026-10-01T00:00:00Z',body_complete=False)
 if mode=='blocked' and trigger:return FetchResult(url,403,100,0,error='HTTP 403',retrieved_at='2026-10-01T00:00:00Z')
 return result
r.fetch_json=controlled
if mode=='normalizer_crash':
 normalizer=r.fetch_official_modules
 def broken_normalizer(org,modules,fetcher):
  normalizer(org,modules,fetcher=fetcher)
  raise RuntimeError('Unexpected normalization worker failure')
 r.fetch_official_modules=broken_normalizer
'''
HARNESS=fixtures.HARNESS.replace('r.fetch_json=fetch;',INJECT)

class OptionalResearchCompletion(unittest.TestCase):
    def setUp(self):
        (ROOT/'.release-gate-tmp').mkdir(exist_ok=True)
        temp=tempfile.TemporaryDirectory(dir=ROOT/'.release-gate-tmp');self.addCleanup(temp.cleanup);self.d=Path(temp.name)
        self.orgs=fixtures.identities(100);self.batch=self.d/'input.jsonl';self.bulk=self.d/'registry.csv'
        self.batch.write_text(''.join(json.dumps({'organisation_number':org})+'\n' for org in self.orgs))
        with self.bulk.open('w',newline='',encoding='utf-8') as f:
            w=csv.writer(f);w.writerow(['organisasjonsnummer','navn','organisasjonsform.kode'])
            for org in self.orgs:w.writerow([org,'Synthetic '+org,'AS'])
    def execute(self,name,mode,policy='best_effort',extra=()):
        d=self.d/name;d.mkdir(exist_ok=True)
        args=['--organisations',str(self.batch),'--bulk',str(self.bulk),'--modules','registry,accounting_obligation,financials,roles',
            '--source-latency-model',str(ROOT/'config/brreg-admission-observed-20261013.json'),'--source-allocation-policy','core_breadth',
            '--optional-research-policy',policy,'--network-policy','authorized_public_brreg','--confirm-authorized-live-sources',
            '--workers','2','--source-attempts','1','--source-timeout-seconds','10','--max-source-requests','400',
            '--max-runtime-seconds','300','--finalization-reserve-seconds','120','--checkpoint-every','1',
            '--output-target','starter_compatible','--run-id','same','--profiles-output',str(d/'profiles.jsonl'),
            '--output',str(d/'envelopes.jsonl'),'--report',str(d/'report.json'),'--viewer-output',str(d/'viewer.html'),*extra]
        env=os.environ.copy();env['TEMP']=env['TMP']=str(self.d)
        p=subprocess.run([sys.executable,'-S','-c',HARNESS,str(ROOT/'scripts/run_competition_batch.py'),mode,*args],cwd=ROOT,
            env=env,capture_output=True,text=True,encoding='utf-8',timeout=90)
        (d/('execution-'+mode+'.log')).write_text(p.stdout+p.stderr,encoding='utf-8')
        return p,d
    def checked(self,p,d):
        self.assertEqual(p.returncode,0,p.stderr)
        es=[json.loads(s) for s in (d/'envelopes.jsonl').read_text().splitlines()]
        self.assertTrue(validate_starter_envelopes(es,self.orgs)['passed'])
        verify_completion(report=d/'report.json',output=d/'envelopes.jsonl',profiles=d/'profiles.jsonl')
        ps=[json.loads(s) for s in (d/'profiles.jsonl').read_text().splitlines()]
        self.assertEqual(json.loads(re.search(r'id="profiles">(.*?)</script>',(d/'viewer.html').read_text(encoding='utf-8'),re.S).group(1)),ps)
        self.assertEqual(sum(e['operations']['requests'] for e in es),json.loads((d/'report.json').read_text())['operations']['requests'])
        return es,ps
    def test_tails_keep_unstarted_work_explicit_without_invented_availability(self):
        p,d=self.execute('tail','unexpected-tail');es,ps=self.checked(p,d)
        self.assertTrue(any(r.get('status')=='not_fetched' for p in ps for r in p['evidence'].values()))
        diagnostic=[json.loads(s) for e in es for s in e['errors'] if s.startswith('{') and '"attempted"' in s]
        self.assertTrue(diagnostic);self.assertTrue(all(x['selected'] and not x['attempted'] for x in diagnostic))
        self.assertNotIn('not_fetched',json.dumps(es))
        self.assertTrue(all(c['availability']=='available' for e in es for c in e['claims']))
        self.assertTrue(all(e['run']['terminal_status']=='completed' for e in es))
        with self.assertRaises(ValueError):starter_availability('not_fetched')
    def test_429_stops_new_starts_and_finishes_truthful_baselines(self):
        p,d=self.execute('rate','429');es,ps=self.checked(p,d)
        report=json.loads((d/'report.json').read_text());self.assertLessEqual(report['operations']['requests'],2)
        self.assertTrue(any('source service cooldown' in x for e in es for x in e['errors']))
        self.assertTrue(any('failed' in x for e in es for x in e['errors']))
    def test_accounted_transport_worker_crash_is_failed_source_not_false_batch_failure(self):
        p,d=self.execute('crash','crash');es,_=self.checked(p,d)
        self.assertTrue(any('RuntimeError' in x for e in es for x in e['errors']))
        self.assertEqual(json.loads((d/'report.json').read_text())['worker_failures'],[])
    def test_default_strict_scenarios_remain_fail_closed(self):
        for mode in ('unexpected-tail','429','crash'):
            p,d=self.execute('strict-'+mode,mode,'strict');self.assertNotEqual(p.returncode,0)
            marker=d/'report.json.completion.json';self.assertNotEqual(json.loads(marker.read_text()).get('status'),'completed')
    def test_integrity_exceptions_wrong_identity_corrupt_bytes_and_projection_stay_fatal(self):
        for mode in ('integrity_exception','identity','corrupt','projection','normalizer_crash'):
            p,d=self.execute(mode,mode);self.assertNotEqual(p.returncode,0,mode)
            self.assertNotEqual(json.loads((d/'report.json.completion.json').read_text()).get('status'),'completed')
            self.assertFalse((d/'envelopes.jsonl').exists())
    def test_attempted_timeout_and_block_are_not_checked_absence(self):
        for mode in ('timeout','blocked'):
            p,d=self.execute(mode,mode);es,_=self.checked(p,d)
            self.assertTrue(any(('failed' if mode=='timeout' else 'blocked') in x for e in es for x in e['errors']))
    def test_complete_identical_resume_has_zero_new_starts_and_forged_claim_is_rejected(self):
        p,d=self.execute('resume','normal');self.checked(p,d)
        q,_=self.execute('resume','normal',extra=['--resume']);self.checked(q,d)
        self.assertIn('"source_starts": 0',q.stdout)
        path=d/'profiles.jsonl';ps=[json.loads(s) for s in path.read_text().splitlines()]
        ps[0]['research_result']['claims'][0]['value']='FORGED'
        path.write_text(''.join(json.dumps(p)+'\n' for p in ps))
        saved=(d/'envelopes.jsonl').read_bytes();q,_=self.execute('resume','normal',extra=['--resume'])
        self.assertNotEqual(q.returncode,0);self.assertEqual((d/'envelopes.jsonl').read_bytes(),saved)
    def test_process_termination_is_not_completion_and_resume_fetches_incomplete_pairs(self):
        p,d=self.execute('terminate','terminate');self.assertEqual(p.returncode,17)
        self.assertNotEqual(json.loads((d/'report.json.completion.json').read_text()).get('status'),'completed')
        ps=[json.loads(s) for s in (d/'profiles.jsonl').read_text().splitlines()]
        retained=sum('financials' in p.get('observation_receipts',{}) for p in ps)
        q,_=self.execute('terminate','normal',extra=['--resume']);self.checked(q,d)
        starts=json.loads(q.stdout.split('FIXTURE_MEASUREMENTS=')[-1])['source_starts']
        self.assertEqual(starts,84-retained)

if __name__=='__main__':unittest.main()
