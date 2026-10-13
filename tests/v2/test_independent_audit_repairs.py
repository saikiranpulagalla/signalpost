"""Actual zero-network CLI regressions for independent audit findings."""
from __future__ import annotations
import copy, csv, hashlib, importlib.util, json, os, subprocess, sys, tempfile, unittest, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from norway_company_agent.sampling import source_boolean
from norway_company_agent.official import normalize_locations
from norway_company_agent.admission import admit_wave
from norway_company_agent.evaluator_adapter import validate_starter_envelopes
from norway_company_agent.completion import verify_completion
from norway_company_agent.live_bootstrap import candidate_profiles, resolve_live_identity
from norway_company_agent.http import FetchResult

ORG = "923609016"
HARNESS = r'''
import importlib.util,sys,json,hashlib,socket,urllib.request
from pathlib import Path
spec=importlib.util.spec_from_file_location('runner',sys.argv[1]);r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
from norway_company_agent.http import FetchResult
from norway_company_agent.telemetry import source_organisation_number
def forbidden(*a,**k):raise AssertionError('EXTERNAL NETWORK START')
socket.create_connection=forbidden;urllib.request.OpenerDirector.open=forbidden
fixtures=Path(sys.argv[2])
def fetch(url,**kwargs):
 org=source_organisation_number(url)
 if '/roller' in url and (fixtures/'interrupt').exists():raise RuntimeError('injected worker interruption')
 body={'id':1,'virksomhet':{'organisasjonsnummer':org},'regnskapstype':'SELSKAP','regnskapsperiode':{'fraDato':'2025-01-01','tilDato':'2025-12-31'},'valuta':'NOK','resultatregnskapResultat':{'aarsresultat':42}}
 body={'organisasjonsnummer':org,'rollegrupper':[]} if '/roller' in url else [body]
 raw=json.dumps(body,sort_keys=True).encode();digest=hashlib.sha256(raw).hexdigest()
 (fixtures/(digest+'.json')).write_bytes(raw)
 return FetchResult(url,200,10,len(raw),body=body,content_sha256=digest,retrieved_at='2026-10-01T00:00:00Z',raw_bytes=raw)
r.fetch_json=fetch
sys.argv=[sys.argv[1]]+sys.argv[3:]
r.main()
'''

class IndependentAuditRepairs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / '.release-gate-tmp')
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        self.batch=self.d/'batch.jsonl';self.bulk=self.d/'bulk.csv';self.raw=self.d/'raw';self.raw.mkdir()
        self.batch.write_text(json.dumps({'organisation_number':ORG})+'\n',encoding='utf-8')
        self.write_bulk('Verified Name')
    def write_bulk(self,name):
        with self.bulk.open('w',newline='',encoding='utf-8') as h:
            w=csv.DictWriter(h,fieldnames=['organisasjonsnummer','navn','organisasjonsform.kode']);w.writeheader();w.writerow({'organisasjonsnummer':ORG,'navn':name,'organisasjonsform.kode':'AS'})
    def run_cli(self,extra=(),financial=True):
        args=['--organisations',str(self.batch),'--bulk',str(self.bulk),'--output',str(self.d/'output.jsonl'),'--profiles-output',str(self.d/'profiles.jsonl'),'--report',str(self.d/'report.json'),'--run-id','repair','--output-target','starter_compatible','--workers','1','--verified-source-snapshots',str(self.raw)]
        if financial:args+=['--modules','registry,accounting_obligation,financials','--network-policy','authorized_public_brreg','--confirm-authorized-live-sources','--source-attempts','1','--max-source-requests','5']
        else:args+=['--modules','registry,accounting_obligation','--network-policy','offline']
        env=os.environ.copy();env['TEMP']=str(self.d);env['TMP']=str(self.d)
        return subprocess.run([sys.executable,'-S','-c',HARNESS,str(ROOT/'scripts/run_competition_batch.py'),str(self.raw),*args,*extra],cwd=ROOT,env=env,text=True,capture_output=True,timeout=120)
    def rows(self,name):return [json.loads(l) for l in (self.d/name).read_text(encoding='utf-8').splitlines()]
    def test_seven_independent_valid_json_resume_mutations_are_rejected(self):
        p=self.run_cli();self.assertEqual(p.returncode,0,p.stderr)
        original=self.rows('profiles.jsonl');old_output=(self.d/'output.jsonl').read_bytes()
        def mutate(p,kind):
            if kind=='name':p['name']='Forged'
            elif kind=='amount':p['evidence']['financials']['value']['records'][0]['annual_result']=999
            elif kind=='period':p['evidence']['financials']['value']['records'][0]['period']['tilDato']='2024-12-31'
            elif kind=='org':p['evidence']['financials']['source_row_key']='993578843'
            elif kind=='digest':p['evidence']['financials']['content_sha256']='0'*64
            elif kind=='time':p['evidence']['financials']['retrieved_at']='2999-01-01T00:00:00Z'
            else:p['research_result']['claims'][0]['value']='Forged claim'
        for kind in ('name','amount','period','org','digest','time','claim'):
            with self.subTest(kind=kind):
                rows=copy.deepcopy(original);mutate(rows[0],kind)
                (self.d/'profiles.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
                result=self.run_cli(['--resume']);self.assertNotEqual(result.returncode,0,result.stdout)
                self.assertEqual((self.d/'output.jsonl').read_bytes(),old_output)
    def test_authenticated_financial_resume_starts_zero_requests_and_exports(self):
        self.assertEqual(self.run_cli().returncode,0)
        initial=self.rows('output.jsonl');self.assertEqual(initial[0]['operations']['requests'],1)
        result=self.run_cli(['--resume']);self.assertEqual(result.returncode,0,result.stderr)
        report=json.loads((self.d/'report.json').read_text());self.assertEqual(report['source_request_policy']['source_requests_started'],0)
        self.assertEqual(self.rows('output.jsonl')[0]['operations']['requests'],0)
        self.assertTrue(validate_starter_envelopes(self.rows('output.jsonl'),[ORG])['passed'])
        value=verify_completion(report=self.d/'report.json',output=self.d/'output.jsonl',profiles=self.d/'profiles.jsonl')
        self.assertEqual(value['run_id'],'repair')
    def test_interrupted_waves_reuse_only_authenticated_completed_pairs(self):
        args=['--modules','registry,accounting_obligation,financials,roles','--request-telemetry',str(self.d/'telemetry.jsonl')]
        (self.raw/'interrupt').touch()
        first=self.run_cli(args);self.assertNotEqual(first.returncode,0)
        report=json.loads((self.d/'report.json').read_text());self.assertEqual(report['operations']['requests'],2)
        self.assertEqual(self.rows('output.jsonl')[0]['operations']['requests'],2)
        telemetry=self.rows('telemetry.jsonl');self.assertEqual(len(telemetry),2)
        (self.raw/'interrupt').unlink()
        second=self.run_cli(['--modules','registry,accounting_obligation,financials,roles','--resume','--request-telemetry',str(self.d/'telemetry-2.jsonl')]);self.assertEqual(second.returncode,0,second.stderr)
        report=json.loads((self.d/'report.json').read_text());self.assertEqual(report['source_request_policy']['source_family_started'],{'roles':1})
        third=self.run_cli(['--modules','registry,accounting_obligation,financials,roles','--resume','--request-telemetry',str(self.d/'telemetry-3.jsonl')]);self.assertEqual(third.returncode,0,third.stderr)
        self.assertEqual(json.loads((self.d/'report.json').read_text())['source_request_policy']['source_requests_started'],0)
    def test_swapped_report_and_output_are_rejected(self):
        self.assertEqual(self.run_cli().returncode,0)
        for name in ('report.json','output.jsonl'):
            original=(self.d/name).read_bytes()
            (self.d/name).write_bytes(original.replace(b'repair',b'other-run'))
            with self.assertRaises(ValueError):verify_completion(report=self.d/'report.json',output=self.d/'output.jsonl',profiles=self.d/'profiles.jsonl')
            (self.d/name).write_bytes(original)
    def test_saved_export_rejects_mutually_consistent_forged_files(self):
        self.assertEqual(self.run_cli(['--output-target','internal_v2']).returncode,0)
        profiles=self.rows('profiles.jsonl');envelopes=self.rows('output.jsonl')
        args=[sys.executable,'-S',str(ROOT/'scripts/export_starter_compatible.py'),'--profiles',str(self.d/'profiles.jsonl'),'--envelopes',str(self.d/'output.jsonl'),'--output',str(self.d/'export.jsonl'),'--bulk',str(self.bulk),'--verified-source-snapshots',str(self.raw)]
        good=subprocess.run(args,cwd=ROOT,capture_output=True,text=True);self.assertEqual(good.returncode,0,good.stderr)
        committed=(self.d/'export.jsonl').read_bytes()
        for kind in ('name','amount','period','org','digest','time','claim'):
            with self.subTest(kind=kind):
                p=copy.deepcopy(profiles)
                if kind=='name':p[0]['name']='Forged'
                elif kind=='amount':p[0]['evidence']['financials']['value']['records'][0]['annual_result']=999
                elif kind=='period':p[0]['evidence']['financials']['value']['records'][0]['period']['tilDato']='2024-12-31'
                elif kind=='org':p[0]['evidence']['financials']['source_row_key']='993578843'
                elif kind=='digest':p[0]['evidence']['financials']['content_sha256']='0'*64
                elif kind=='time':p[0]['evidence']['financials']['retrieved_at']='2999-01-01T00:00:00Z'
                else:p[0]['research_result']['claims'][0]['value']='Forged claim'
                e=copy.deepcopy(envelopes);e[0]['profile']=p[0]
                (self.d/'profiles.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in p),encoding='utf-8')
                (self.d/'output.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in e),encoding='utf-8')
                bad=subprocess.run(args,cwd=ROOT,capture_output=True,text=True);self.assertNotEqual(bad.returncode,0)
                self.assertEqual((self.d/'export.jsonl').read_bytes(),committed)
    def test_actual_cli_large_batches_under_hypothetical_budgets(self):
        measurements=[]
        def orgs(count):
            result=[];prefix=10000000
            while len(result)<count:
                digits=[int(c) for c in str(prefix)];check=11-sum(a*b for a,b in zip(digits,(3,2,7,6,5,4,3,2)))%11
                if check==11:check=0
                if check!=10:result.append(str(prefix)+str(check))
                prefix+=1
            return result
        for count in (100,1000,1500):
            identities=orgs(count)
            self.batch.write_text(''.join(json.dumps({'organisation_number':o})+'\n' for o in identities),encoding='utf-8')
            with self.bulk.open('w',newline='',encoding='utf-8') as h:
                w=csv.DictWriter(h,fieldnames=['organisasjonsnummer','navn','organisasjonsform.kode']);w.writeheader()
                for o in identities:w.writerow({'organisasjonsnummer':o,'navn':'Synthetic '+o,'organisasjonsform.kode':'AS'})
            for seconds in (300,600,1200,2700):
                with self.subTest(count=count,seconds=seconds):
                    p=self.run_cli(['--workers','2','--max-source-requests',str(count),'--source-timeout-seconds','2','--max-runtime-seconds',str(seconds),'--finalization-reserve-seconds','10'])
                    self.assertEqual(p.returncode,0,p.stderr)
                    rows=self.rows('output.jsonl');self.assertEqual([r['organisation_number'] for r in rows],identities)
                    self.assertTrue(validate_starter_envelopes(rows,identities)['passed'])
                    report=json.loads((self.d/'report.json').read_text());started=report['source_request_policy']['source_requests_started']
                    admitted=len(report['execution_plan']['admission']['admitted_organisations']['financials'])
                    self.assertEqual(started,admitted);self.assertEqual(sum(r['operations']['requests'] for r in rows),started)
                    verify_completion(report=self.d/'report.json',output=self.d/'output.jsonl',profiles=self.d/'profiles.jsonl')
                    measurements.append({'companies':count,'budget_seconds':seconds,'admitted':admitted,'completed':started,'terminal_count':len(rows),'claims':sum(len(r['claims']) for r in rows),'zero_external_network':True})
        print('ACTUAL_CLI_DEADLINE_RESULTS='+json.dumps(measurements))
    def test_transaction_lock_survives_concurrent_attempt_and_killed_owner(self):
        code=HARNESS.replace('r.main()',r'''
original=Path.write_text
def pause(path,data,*a,**k):
 result=original(path,data,*a,**k)
 if path.name=='report.json' and '"expected_count"' in data:
  original(fixtures/'ready','1')
  while not (fixtures/'release').exists():time.sleep(.02)
 return result
import time
Path.write_text=pause
r.main()
''')
        args=['--organisations',str(self.batch),'--bulk',str(self.bulk),'--output',str(self.d/'output.jsonl'),'--profiles-output',str(self.d/'profiles.jsonl'),'--report',str(self.d/'report.json'),'--run-id','owner','--output-target','starter_compatible','--network-policy','offline']
        child=subprocess.Popen([sys.executable,'-S','-c',code,str(ROOT/'scripts/run_competition_batch.py'),str(self.raw),*args],cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            end=time.monotonic()+15
            while not (self.raw/'ready').exists():
                self.assertIsNone(child.poll());self.assertLess(time.monotonic(),end);time.sleep(.02)
            refused=self.run_cli(financial=False);self.assertNotEqual(refused.returncode,0)
            self.assertIn('lock',refused.stderr.lower())
            child.kill();child.communicate(timeout=10)
            self.assertNotEqual(child.returncode,0)
            recovered=self.run_cli(financial=False);self.assertEqual(recovered.returncode,0,recovered.stderr)
            self.assertEqual(verify_completion(report=self.d/'report.json',output=self.d/'output.jsonl',profiles=self.d/'profiles.jsonl')['run_id'],'repair')
        finally:
            if child.poll() is None:child.kill();child.communicate(timeout=10)
    def test_registry_unknowns_do_not_publish_negative_facts(self):
        self.assertEqual(self.run_cli(financial=False).returncode,0)
        self.assertNotIn('legal_status',{c['field'] for c in self.rows('output.jsonl')[0]['claims']})
        for value in (None,'','yes','0','1',0,1):self.assertIsNone(source_boolean(value))
        for value in ('true',' TRUE ',True):self.assertIs(source_boolean(value),True)
        for value in ('false',' FALSE ',False):self.assertIs(source_boolean(value),False)
    def test_subunit_parent_validation_and_incomplete_pages(self):
        valid={'organisasjonsnummer':'910608193','overordnetEnhet':ORG,'navn':'Branch'}
        wrong={**valid,'overordnetEnhet':'993578843'}
        missing={k:v for k,v in valid.items() if k!='overordnetEnhet'}
        result=normalize_locations({'_embedded':{'underenheter':[valid,wrong,missing]},'page':{'totalPages':2}},ORG)
        self.assertEqual(len(result['locations']),1);self.assertEqual(result['locations'][0]['parent_organisation_number'],ORG)
        self.assertFalse(result['complete']);self.assertEqual(len(result['rejected_records']),2)
    def test_conservative_admission_is_shared_and_publishes_minimal_batch(self):
        self.assertEqual(admit_wave(['a','b','c'],workers=1,expected_seconds=20,remaining_seconds=8,request_slots=6,family_slots=None)[0],[])
        result=self.run_cli(['--max-runtime-seconds','10','--finalization-reserve-seconds','2'])
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(len(self.rows('output.jsonl')),1)
        report=json.loads((self.d/'report.json').read_text());self.assertEqual(report['source_request_policy']['source_requests_started'],0)
        self.assertEqual(report['execution_plan']['admission']['intentionally_omitted_counts']['financials'],1)
    def test_uncapped_retries_are_refused_before_any_source_start(self):
        args=['--organisations',str(self.batch),'--bulk',str(self.bulk),'--output',str(self.d/'denied.jsonl'),'--profiles-output',str(self.d/'denied-profiles.jsonl'),'--report',str(self.d/'denied-report.json'),'--run-id','denied-retries','--modules','registry,accounting_obligation,financials','--network-policy','authorized_public_brreg','--confirm-authorized-live-sources','--source-attempts','2']
        result=subprocess.run([sys.executable,'-S','-c',HARNESS,str(ROOT/'scripts/run_competition_batch.py'),str(self.raw),*args],cwd=ROOT,text=True,capture_output=True,timeout=20)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('one physical attempt',result.stderr)
        self.assertEqual(list(self.raw.iterdir()),[])
        self.assertFalse((self.d/'denied.jsonl').exists())
    def test_simulator_and_actual_cli_share_odd_batch_wave_barriers(self):
        families=('financials','roles','locations','group')
        telemetry=self.d/'saved-latencies.jsonl'
        telemetry.write_text(''.join(json.dumps({'source_family':f,'duration_ms':400000})+'\n' for f in families),encoding='utf-8')
        model=self.d/'model.json';model.write_text(json.dumps({f:400 for f in families}),encoding='utf-8')
        simulated=self.d/'simulation.json'
        p=subprocess.run([sys.executable,'-S',str(ROOT/'scripts/simulate_evaluator_budget.py'),'--telemetry',str(telemetry),'--output',str(simulated),'--identity-mode','frozen_replay','--companies','1','--workers','2','--reserve-seconds','30'],cwd=ROOT,text=True,capture_output=True,timeout=20)
        self.assertEqual(p.returncode,0,p.stderr)
        scenario=next(r for r in json.loads(simulated.read_text())['scenarios'] if r['deadline_seconds_hypothetical']==1200)
        actual=self.run_cli(['--modules','registry,accounting_obligation,financials,roles,locations,group','--workers','2','--max-runtime-seconds','1200','--finalization-reserve-seconds','30','--source-latency-model',str(model)])
        self.assertEqual(actual.returncode,0,actual.stderr)
        admitted=json.loads((self.d/'report.json').read_text())['execution_plan']['admission']['admitted_organisations']
        self.assertEqual({f:len(admitted[f]) for f in families},{f:scenario['source_family_starts'].get(f,0) for f in families})
        self.assertEqual({f:len(admitted[f]) for f in families},{'financials':1,'roles':1,'locations':0,'group':0})
    def test_unattempted_live_identity_is_not_a_failed_source_observation(self):
        profiles,_=candidate_profiles([ORG])
        for reason in ('source request budget exhausted','execution deadline reserve reached'):
            profile,_,verified=resolve_live_identity(copy.deepcopy(profiles[0]),lambda url:FetchResult(url,0,0,0,error=reason))
            self.assertFalse(verified)
            self.assertEqual(profile['evidence']['registry']['status'],'not_fetched')
            self.assertEqual(profile['evidence']['registry_live']['status'],'not_fetched')
        args=['--organisations',str(self.batch),'--live-bootstrap','--output',str(self.d/'live-output.jsonl'),'--profiles-output',str(self.d/'live-profiles.jsonl'),'--report',str(self.d/'live-report.json'),'--run-id','live-budget-zero','--modules','registry,accounting_obligation,registry_live','--network-policy','authorized_public_brreg','--confirm-authorized-live-sources','--source-attempts','1','--max-source-requests','0','--output-target','starter_compatible']
        result=subprocess.run([sys.executable,'-S','-c',HARNESS,str(ROOT/'scripts/run_competition_batch.py'),str(self.raw),*args],cwd=ROOT,text=True,capture_output=True,timeout=20)
        self.assertNotEqual(result.returncode,0)
        self.assertFalse((self.d/'live-output.jsonl').exists())
        self.assertEqual(list(self.raw.iterdir()),[])
        self.assertEqual(self.rows('live-profiles.jsonl')[0]['evidence']['registry']['status'],'not_fetched')
        self.assertEqual(json.loads((self.d/'live-report.json').read_text())['source_request_policy']['source_requests_started'],0)
    def test_explicit_refresh_preserves_supported_change_and_idempotence(self):
        self.assertEqual(self.run_cli(financial=False).returncode,0)
        previous=self.d/'previous.jsonl';previous.write_bytes((self.d/'profiles.jsonl').read_bytes())
        old_bulk=self.d/'old.csv';old_bulk.write_bytes(self.bulk.read_bytes());self.write_bulk('New Verified Name')
        args=['--previous-profiles',str(previous),'--previous-bulk',str(old_bulk)]
        result=self.run_cli(args,financial=False);self.assertEqual(result.returncode,0,result.stderr)
        changes=self.rows('output.jsonl')[0]['changes'];self.assertEqual(len(changes),1)
        self.assertEqual(changes[0]['old_value'],'Verified Name');self.assertEqual(changes[0]['new_value'],'New Verified Name')
        self.assertIn('previous_evidence',changes[0]);first_id=changes[0]['id']
        self.assertEqual(self.run_cli(args,financial=False).returncode,0);self.assertEqual(self.rows('output.jsonl')[0]['changes'][0]['id'],first_id)
        previous.write_bytes((self.d/'profiles.jsonl').read_bytes());old_bulk.write_bytes(self.bulk.read_bytes())
        self.assertEqual(self.run_cli(args,financial=False).returncode,0);self.assertEqual(self.rows('output.jsonl')[0]['changes'],[])

if __name__=='__main__':unittest.main()
