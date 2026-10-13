"""Small release-boundary acceptance grid, entirely synthetic and zero network."""
import csv,json,os,re,subprocess,sys,tempfile,time,unittest
from pathlib import Path
from test_independent_audit_repairs import HARNESS

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from norway_company_agent.evaluator_adapter import validate_starter_envelopes
from norway_company_agent.completion import verify_completion

class PreSubmissionReleaseBoundary(unittest.TestCase):
    def test_fresh_sizes_and_selected_omissions(self):
        metrics=[]
        with tempfile.TemporaryDirectory(dir=ROOT/'.release-gate-tmp') as temp:
            base=Path(temp);orgs=[];prefix=10000000
            while len(orgs)<1500:
                ds=list(map(int,str(prefix)));check=11-sum(a*b for a,b in zip(ds,(3,2,7,6,5,4,3,2)))%11
                if check==11:check=0
                if check!=10:orgs.append(str(prefix)+str(check))
                prefix+=1
            cases=[(n,False,None) for n in (100,1000,1100,1500)]+[(2,True,4),(2,True,1),(2,True,4)]
            for index,(count,enriched,cap) in enumerate(cases):
                d=base/str(index);d.mkdir();(d/'raw').mkdir();ids=orgs[:count]
                (d/'input.jsonl').write_text(''.join(json.dumps({'organisation_number':o})+'\n' for o in ids))
                with (d/'bulk.csv').open('w',newline='') as h:
                    w=csv.DictWriter(h,fieldnames=['organisasjonsnummer','navn','organisasjonsform.kode']);w.writeheader()
                    for org in ids:w.writerow({'organisasjonsnummer':org,'navn':'Synthetic '+org,'organisasjonsform.kode':'AS'})
                args=['--organisations',str(d/'input.jsonl'),'--bulk',str(d/'bulk.csv'),'--output',str(d/'envelopes.jsonl'),'--profiles-output',str(d/'profiles.jsonl'),'--report',str(d/'report.json'),'--viewer-output',str(d/'viewer.html'),'--run-id',str(index),'--output-target','starter_compatible','--workers','2','--max-runtime-seconds','60','--finalization-reserve-seconds','5']
                args+=['--modules','registry,accounting_obligation,financials,roles','--network-policy','authorized_public_brreg','--confirm-authorized-live-sources','--max-source-requests',str(cap),'--source-timeout-seconds','1'] if enriched else ['--modules','registry,accounting_obligation','--network-policy','offline']
                if index==6:args+=['--max-runtime-seconds','1','--finalization-reserve-seconds','0.5']
                env=os.environ.copy();env['TEMP']=str(d);env['TMP']=str(d);t=time.monotonic()
                p=subprocess.run([sys.executable,'-S','-c',HARNESS,str(ROOT/'scripts/run_competition_batch.py'),str(d/'raw'),*args],cwd=ROOT,env=env,capture_output=True,text=True,timeout=90)
                self.assertEqual(p.returncode,0,f"case {count}/{cap}: "+p.stderr+p.stdout)
                rows=[json.loads(x) for x in (d/'envelopes.jsonl').read_text().splitlines()];profiles=[json.loads(x) for x in (d/'profiles.jsonl').read_text().splitlines()]
                self.assertTrue(validate_starter_envelopes(rows,ids)['passed']);verify_completion(report=d/'report.json',output=d/'envelopes.jsonl',profiles=d/'profiles.jsonl')
                payload=re.search(r'id="profiles">(.*?)</script>',(d/'viewer.html').read_text(),re.S).group(1);self.assertEqual(json.loads(payload),profiles)
                report=json.loads((d/'report.json').read_text());starts=report['operations']['requests'];self.assertEqual(starts,0 if index==6 else cap or 0)
                if enriched and cap==1:self.assertEqual(sum(c['field'].startswith('financial_') for r in rows for c in r['claims']),1)
                metrics.append({'companies':count,'selected_enrichment':enriched,'fixture_starts':starts,'external_starts':0,'envelopes':len(rows),'claims':sum(len(r['claims']) for r in rows),'seconds':round(time.monotonic()-t,3),'publication':'PASS'})
        print('PRE_SUBMISSION_ACCEPTANCE='+json.dumps(metrics))

if __name__=='__main__':unittest.main()
