"""Measured forecasts are distinct from deadlines; actual CLI publication gate."""
import csv
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from norway_company_agent.admission import plan_family_admission, latency_forecasts
from norway_company_agent.completion import verify_completion
from norway_company_agent.evaluator_adapter import validate_starter_envelopes

HARNESS=r'''
import importlib.util,json,sys,socket,urllib.request,hashlib,threading,types
def forbidden(*a,**k):raise AssertionError('EXTERNAL NETWORK START')
socket.socket.connect=forbidden;socket.create_connection=forbidden;urllib.request.OpenerDirector.open=forbidden
spec=importlib.util.spec_from_file_location('runner',sys.argv[1]);r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
from norway_company_agent.http import FetchResult
from norway_company_agent.telemetry import source_family,source_organisation_number
mode=sys.argv[2];lock=threading.Lock();elapsed=[0.0];last=[None,0,0.0];starts=[]
def mono():
 with lock:return elapsed[0]
r.time=types.SimpleNamespace(monotonic=mono)
latencies={'financials':2.312*1.25,'roles':2.734*1.25,'locations':2.25*1.25,'registry_live':4.5*1.25,'group':2.672*1.25}
def fetch(url,**kwargs):
 family=source_family(url);org=source_organisation_number(url)
 delay=10 if mode=='unexpected-tail' else latencies[family]
 with lock:
  if family!=last[0]:
   if last[1]%2:elapsed[0]+=last[2]/2
   last[:]=[family,0,delay]
  last[1]+=1;elapsed[0]+=delay/2;starts.append([org,family])
 body=[{'virksomhet':{'organisasjonsnummer':org},'regnskapstype':'SELSKAP','regnskapsperiode':{'fraDato':'2025-01-01','tilDato':'2025-12-31'},'valuta':'NOK','resultatregnskapResultat':{'aarsresultat':42}}]
 if family=='roles':body={'organisasjonsnummer':org,'rollegrupper':[{'type':{'beskrivelse':'Board'},'roller':[{'type':{'beskrivelse':'Chair'},'person':{'navn':{'fornavn':'Synthetic','etternavn':'Person'}}}]}]}
 if family=='locations':body={'_embedded':{'underenheter':[{'organisasjonsnummer':'910608193','overordnetEnhet':org,'navn':'Synthetic workplace'}]},'page':{'totalPages':1}}
 if family=='registry_live':body={'organisasjonsnummer':org,'navn':'Synthetic '+org,'organisasjonsform':{'kode':'AS'}}
 if family=='group':return FetchResult(url,404,round(delay*1000),0,error='HTTP 404',retrieved_at='2026-10-01T00:00:00Z',content_sha256=hashlib.sha256(b'').hexdigest())
 raw=json.dumps(body,sort_keys=True).encode()
 return FetchResult(url,200,round(delay*1000),len(raw),body=body,content_sha256=hashlib.sha256(raw).hexdigest(),retrieved_at='2026-10-01T00:00:00Z',raw_bytes=raw)
r.fetch_json=fetch;sys.argv=[sys.argv[1]]+sys.argv[3:]
try:r.main()
finally:print('FIXTURE_MEASUREMENTS='+json.dumps({'source_starts':len(starts),'simulated_source_seconds':mono(),'external_network_starts':0}))
'''


def identities(count):
    result=[];prefix=10000000
    while len(result)<count:
        digits=list(map(int,str(prefix)));check=11-sum(a*b for a,b in zip(digits,(3,2,7,6,5,4,3,2)))%11
        if check==11:check=0
        if check!=10:result.append(str(prefix)+str(check))
        prefix+=1
    return list(reversed(result))


class AdmissionConfigurationRepairs(unittest.TestCase):
    def test_failed_and_unmeasured_timings_are_not_optimistic_forecasts(self):
        spec=importlib.util.spec_from_file_location('model_builder',ROOT/'scripts/build_source_latency_model.py')
        m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
        model=m.build_model([{'source_family':'financials','physical_source_request_number':1,'classification':'timeout','duration_ms':0,'http_status':None}],10)
        self.assertGreaterEqual(model['families']['financials']['forecast_seconds'],10)
        self.assertEqual(model['families']['financials']['failed_or_incomplete_samples'],1)
        self.assertGreaterEqual(model['families']['roles']['forecast_seconds'],10)
        self.assertNotEqual(latency_forecasts(model,10)['financials'],3)

    def test_core_breadth_is_deterministic_bounded_and_preserves_family_waves(self):
        candidates={f:['a','b','c','d'] for f in ('financials','roles','locations','group')}
        selected,omitted=plan_family_admission(candidates,module_family={f:f for f in candidates},expected_seconds={f:4 for f in candidates},
            remaining_seconds=8,request_slots=4,family_slots={'group':0},workers=2,policy='core_breadth')
        self.assertEqual({f:len(v) for f,v in selected.items()},{'financials':2,'roles':2,'locations':0,'group':0})
        self.assertEqual(omitted['group'],4)

    def test_actual_cli_admission_sensitivity_and_adverse_tail_publication(self):
        (ROOT/'.release-gate-tmp').mkdir(exist_ok=True)
        measured=[]
        with tempfile.TemporaryDirectory(dir=ROOT/'.release-gate-tmp') as temporary:
            base=Path(temporary)
            for count in (100,1000,1100,1500):
                orgs=identities(count);batch=base/'input.jsonl';bulk=base/'registry.csv'
                batch.write_text(''.join(json.dumps({'organisation_number':o})+'\n' for o in orgs),encoding='utf-8')
                with bulk.open('w',newline='',encoding='utf-8') as h:
                    w=csv.DictWriter(h,fieldnames=['organisasjonsnummer','navn','organisasjonsform.kode']);w.writeheader()
                    for o in orgs:w.writerow({'organisasjonsnummer':o,'navn':'Synthetic '+o,'organisasjonsform.kode':'AS'})
                for seconds in (300,600,1200,2700,5400):
                    d=base/f'{count}-{seconds}';d.mkdir()
                    args=['--organisations',str(batch),'--bulk',str(bulk),'--modules','registry,accounting_obligation,financials,roles,locations,registry_live,group',
                        '--source-priority','financials,roles,locations,registry_live,group','--source-family-budgets','group=0',
                        '--source-allocation-policy','core_breadth','--source-latency-model',str(ROOT/'config/brreg-admission-observed-20261013.json'),
                        '--network-policy','authorized_public_brreg','--confirm-authorized-live-sources','--workers','2','--source-attempts','1',
                        '--source-timeout-seconds','10','--max-source-requests','7500','--max-runtime-seconds',str(seconds),
                        '--finalization-reserve-seconds','120','--output-target','starter_compatible','--run-id','sensitivity',
                        '--profiles-output',str(d/'profiles.jsonl'),'--output',str(d/'envelopes.jsonl'),'--report',str(d/'report.json'),'--viewer-output',str(d/'viewer.html')]
                    env=os.environ.copy();env['TEMP']=env['TMP']=str(d)
                    p=subprocess.run([sys.executable,'-S','-c',HARNESS,str(ROOT/'scripts/run_competition_batch.py'),'observed-tail',*args],
                        cwd=ROOT,env=env,capture_output=True,text=True,timeout=120)
                    self.assertEqual(p.returncode,0,p.stderr+'\n'+p.stdout[-1000:])
                    report=json.loads((d/'report.json').read_text());rows=[json.loads(l) for l in (d/'envelopes.jsonl').read_text().splitlines()]
                    self.assertTrue(validate_starter_envelopes(rows,orgs)['passed'])
                    verify_completion(report=d/'report.json',output=d/'envelopes.jsonl',profiles=d/'profiles.jsonl')
                    admitted=report['execution_plan']['admission']['admitted_organisations']
                    self.assertEqual(report['operations']['requests'],sum(map(len,admitted.values())))
                    self.assertEqual(sum(r['operations']['requests'] for r in rows),report['operations']['requests'])
                    self.assertGreater(len(admitted['roles']),0)
                    self.assertIn('"external_network_starts": 0',p.stdout)
                    row={'companies':count,'hypothetical_seconds':seconds,'admitted':{f:len(v) for f,v in admitted.items()},
                        'requests':report['operations']['requests'],'terminal_envelopes':len(rows),'claims':sum(len(r['claims']) for r in rows),
                        'unfinished':0,'publication':'PASS','timing':'simulated saved family maxima * 1.25, two ideal workers; not live'}
                    measured.append(row)
                    if count==1000 and seconds==300:
                        # Unexpected all-10-second tails exceed this conditional
                        # model. Do not call them successful publication.
                        changed=list(args)
                        for flag,name in (('--profiles-output','unexpected-profiles.jsonl'),('--output','unexpected-envelopes.jsonl'),('--report','unexpected-report.json'),('--viewer-output','unexpected-viewer.html')):
                            changed[changed.index(flag)+1]=str(d/name)
                        q=subprocess.run([sys.executable,'-S','-c',HARNESS,str(ROOT/'scripts/run_competition_batch.py'),'unexpected-tail',*changed],
                            cwd=ROOT,env=env,capture_output=True,text=True,timeout=60)
                        self.assertNotEqual(q.returncode,0)
                        self.assertFalse((d/'unexpected-envelopes.jsonl').exists())
                        measured.append({'companies':count,'hypothetical_seconds':seconds,'scenario':'unseen all-10-second tails',
                                         'publication':'FAIL CLOSED — admitted work unfinished, no false absence'})
        print('MEASURED_ADMISSION_CLI_RESULTS='+json.dumps(measured))


if __name__=='__main__':unittest.main()
