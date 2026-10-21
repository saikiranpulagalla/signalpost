"""Actual runner lifecycle plus an independent byte-LF artifact oracle."""
import csv,hashlib,json,os,re,subprocess,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from norway_company_agent.completion import verify_completion

TEXT='\u2028Ø\u2029Å\u0085 "quote" \\ end\u2028'
ORGS=['923609016','993578843']
HARNESS=r'''
import importlib.util,json,sys,hashlib,socket,urllib.request
from pathlib import Path
def forbidden(*a,**k):raise AssertionError('External source forbidden')
socket.socket.connect=forbidden;socket.create_connection=forbidden;urllib.request.OpenerDirector.open=forbidden
spec=importlib.util.spec_from_file_location('runner',sys.argv[1]);r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
from norway_company_agent.http import FetchResult
from norway_company_agent.telemetry import source_family,source_organisation_number
mode=sys.argv[2];text='\u2028Ø\u2029Å\u0085 "quote" \\ end\u2028';calls=[];checkpoints=[]
def fetch(url,**kw):
 org=source_organisation_number(url);family=source_family(url);calls.append([org,family])
 if mode=='note' and family=='financials':return FetchResult(url,403,1,0,error='HTTP 403 '+text,retrieved_at='2026-10-20T00:00:00Z')
 body={'organisasjonsnummer':org}
 if family=='financials':body=[{'virksomhet':{'organisasjonsnummer':org},'regnskapstype':'SELSKAP','regnskapsperiode':{'fraDato':'2025-01-01','tilDato':'2025-12-31'},'valuta':'NOK','resultatregnskapResultat':{'aarsresultat':42}}]
 if family=='roles':body.update(rollegrupper=[{'type':{'beskrivelse':'Board'},'roller':[{'type':{'beskrivelse':'Chair'},'person':{'navn':{'fornavn':'Synthetic','etternavn':text}}}]}])
 if family=='locations':body={'_embedded':{'underenheter':[]},'page':{'totalPages':1}}
 if family=='registry_live':body.update(navn='Live '+text,aktivitet=[text],vedtektsfestetFormaal=[text],historiskeNavn=[{'navn':text,'fraDato':'2001-01-01 00:00:00','tilDato':'2010-01-01 00:00:00'}])
 raw=json.dumps(body,ensure_ascii=False,sort_keys=True).encode();return FetchResult(url,200,1,len(raw),body=body,raw_bytes=raw,content_sha256=hashlib.sha256(raw).hexdigest(),retrieved_at='2026-10-20T00:00:00Z')
r.fetch_json=fetch
write=r.write_jsonl
def checkpoint(path,rows):
 write(path,rows)
 decoded=[json.loads(line) for line in Path(path).read_bytes().split(b'\n') if line.strip()]
 assert decoded==rows
 checkpoints.append(len(rows))
r.write_jsonl=checkpoint;sys.argv=[sys.argv[1]]+sys.argv[3:]
try:r.main()
finally:print('LIFECYCLE='+json.dumps({'source_starts':len(calls),'external_starts':0,'checkpoints':checkpoints}))
'''

def independent(d, expected_text=TEXT):
    load=lambda p:[json.loads(line) for line in p.read_bytes().split(b'\n') if line.strip()]
    es=load(d/'envelopes.jsonl');ps=load(d/'profiles.jsonl')
    assert [e['organisation_number'] for e in es]==ORGS==[p['organisation_number'] for p in ps]
    assert all(p['sample_slice']==expected_text for p in ps)
    for e,p in zip(es,ps):
        assert set(e)=={'organisation_number','run','claims','evidence','changes','errors','operations'}
        available={ev['id'] for ev in e['evidence']}
        assert all(set(c['evidence_ids'])<=available and c['availability'] in {'available','blocked','failed','not_available','not_applicable','ambiguous'} for c in e['claims'])
        assert len(e['claims'])==len(p['research_result']['claims'])
        assert any(c['field']=='legal_name' and expected_text in c['value'] for c in e['claims'])
    marker=json.loads((d/'report.json.completion.json').read_bytes());assert marker['status']=='completed'
    for field,name in [('report_sha256','report.json'),('envelope_sha256','envelopes.jsonl'),('profiles_sha256','profiles.jsonl')]:assert marker[field]==hashlib.sha256((d/name).read_bytes()).hexdigest()
    assert json.loads(re.search(r'id="profiles">(.*?)</script>',(d/'viewer.html').read_text(encoding='utf-8'),re.S).group(1))==ps
    return es,ps

class JsonlLifecycle(unittest.TestCase):
    def setUp(self):
        (ROOT/'.release-gate-tmp').mkdir(exist_ok=True)
        temp=tempfile.TemporaryDirectory(dir=ROOT/'.release-gate-tmp');self.addCleanup(temp.cleanup);self.d=Path(temp.name)
        self.batch=self.d/'batch.jsonl';self.bulk=self.d/'registry.csv'
        self.batch.write_bytes(('\r\n'.join(json.dumps({'organisation_number':o,'sample_slice':TEXT},ensure_ascii=False) for o in ORGS)).encode())
        self.write_bulk(self.bulk)
    def write_bulk(self,path,changed=False,text=TEXT):
        with path.open('w',newline='',encoding='utf-8') as f:
            w=csv.writer(f);w.writerow(['organisasjonsnummer','navn','organisasjonsform.kode','aktivitet','vedtektsfestetFormaal'])
            for i,org in enumerate(ORGS):w.writerow([org,('Changed ' if changed and i==0 else 'Synthetic ')+text,'AS',text,text])
    def run_cli(self,name,policy='best_effort',extra=(),mode='normal',rich=True,text=TEXT):
        d=self.d/name;d.mkdir(exist_ok=True)
        args=['--organisations',str(self.batch),'--bulk',str(self.bulk),'--modules','registry,accounting_obligation'+(',financials,roles,locations,registry_live' if rich else ''),
         '--network-policy','authorized_public_brreg' if rich else 'offline','--optional-research-policy',policy,'--output-target','starter_compatible',
         '--source-allocation-policy','core_breadth','--source-latency-model',str(ROOT/'config/brreg-admission-observed-20261013.json'),
         '--workers','2','--source-attempts','1','--source-timeout-seconds','10','--max-source-requests','20','--max-runtime-seconds','300','--finalization-reserve-seconds','30',
         '--checkpoint-every','1','--profiles-output',str(d/'profiles.jsonl'),'--output',str(d/'envelopes.jsonl'),'--report',str(d/'report.json'),'--viewer-output',str(d/'viewer.html'),'--run-id',name,*extra]
        if rich:args+=['--confirm-authorized-live-sources']
        env=os.environ.copy();env.update(TEMP=str(self.d),TMP=str(self.d),PYTHONDONTWRITEBYTECODE='1')
        harness=HARNESS.replace('calls=[];checkpoints=[]','text='+repr(text)+';calls=[];checkpoints=[]')
        p=subprocess.run([sys.executable,'-B','-S','-c',harness,str(ROOT/'scripts/run_competition_batch.py'),mode,*args],cwd=self.d,env=env,capture_output=True,text=True,encoding='utf-8',timeout=40)
        return p,d
    def checked(self,p,d,text=TEXT):
        self.assertEqual(p.returncode,0,p.stderr);self.assertIn('"external_starts": 0',p.stdout)
        verify_completion(report=d/'report.json',output=d/'envelopes.jsonl',profiles=d/'profiles.jsonl')
        return independent(d,text)
    def test_each_separator_literal_and_escaped_input_lifecycle(self):
        for index,separator in enumerate(('\u2028','\u2029','\u0085','\u2028\u2029\u0085')):
            text=separator+'Ø'+separator+' " \\ Å'+separator*2
            self.write_bulk(self.bulk,text=text)
            for escaped in (False,True):
                self.batch.write_bytes(('\n'.join(json.dumps({'organisation_number':o,'sample_slice':text},ensure_ascii=escaped) for o in ORGS)+'\n').encode())
                for policy in ('strict','best_effort'):
                    with self.subTest(separator=repr(separator),escaped=escaped,policy=policy):
                        p,d=self.run_cli(f'each-{index}-{escaped}-{policy}',policy,text=text)
                        es,ps=self.checked(p,d,text)
                        for e in es:
                            fields={c['field']:c['value'] for c in e['claims']}
                            self.assertEqual(fields['registered_activity'],[text]);self.assertEqual(fields['registered_purpose'],[text]);self.assertEqual(fields['registered_name_history'][0]['name'],text)
    def test_rich_fresh_checkpoint_resume_and_viewer_both_policies(self):
        for policy in ('strict','best_effort'):
            p,d=self.run_cli(policy,policy);es,ps=self.checked(p,d)
            for e in es:
                fields={c['field']:c['value'] for c in e['claims']}
                self.assertEqual(fields['registered_activity'],[TEXT]);self.assertEqual(fields['registered_purpose'],[TEXT])
                self.assertEqual(fields['registered_name_history'][0]['name'],TEXT)
            q,_=self.run_cli(policy,policy,extra=['--resume']);again,resumed_ps=self.checked(q,d)
            self.assertIn('"source_starts": 0',q.stdout);self.assertEqual([e['claims'] for e in es],[e['claims'] for e in again])
            out=d/'regenerated.html'
            command=[sys.executable,'-B','-S',str(ROOT/'scripts/build_profile_viewer.py'),'--profiles',str(d/'profiles.jsonl'),'--output',str(out)]
            result=subprocess.run(command,cwd=self.d,capture_output=True,text=True,timeout=20);self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(re.search(r'id="profiles">(.*?)</script>',out.read_text(encoding='utf-8'),re.S).group(1)),resumed_ps)
    def test_blocked_source_notes_keep_literal_text_both_policies(self):
        for policy in ('strict','best_effort'):
            p,d=self.run_cli('note-'+policy,policy,mode='note');es,ps=self.checked(p,d)
            self.assertTrue(all(TEXT in p['evidence']['financials']['note'] for p in ps))
            self.assertTrue(all(not any(c['field'].startswith('financial_') for c in e['claims']) for e in es))
    def test_authenticated_refresh_unchanged_and_material_name_change(self):
        for policy in ('strict','best_effort'):
            p,old=self.run_cli('old-'+policy,policy);self.checked(p,old)
            previous=self.d/('previous-'+policy+'.csv');previous.write_bytes(self.bulk.read_bytes())
            extra=['--previous-profiles',str(old/'profiles.jsonl'),'--previous-bulk',str(previous),'--observation-key',str(old/'report.json.observation-auth.key')]
            p,new=self.run_cli('unchanged-'+policy,policy,extra);es,_=self.checked(p,new);self.assertTrue(all(not e['changes'] for e in es))
            self.write_bulk(self.bulk,changed=True)
            p,new=self.run_cli('changed-'+policy,policy,extra);es,ps=self.checked(p,new)
            events=[c for e in es for c in e['changes']];self.assertEqual(len(events),1);self.assertEqual(events[0]['field'],'registry.name')
            self.assertIn(TEXT,events[0]['old_value']);self.assertIn(TEXT,events[0]['new_value'])
            self.assertTrue(events[0]['previous_evidence']);self.assertTrue(events[0]['current_evidence'])
            self.write_bulk(self.bulk)
    def test_saved_internal_export_and_corrupt_completion_rejection(self):
        p,d=self.run_cli('internal',extra=['--output-target','internal_v2'],rich=False);self.assertEqual(p.returncode,0,p.stderr)
        out=d/'starter.jsonl'
        result=subprocess.run([sys.executable,'-B','-S',str(ROOT/'scripts/export_starter_compatible.py'),'--profiles',str(d/'profiles.jsonl'),'--envelopes',str(d/'envelopes.jsonl'),'--bulk',str(self.bulk),'--output',str(out)],cwd=self.d,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
        rows=[json.loads(line) for line in out.read_bytes().split(b'\n') if line.strip()];self.assertEqual(len(rows),2)
        self.assertTrue(all(any(TEXT in str(c['value']) for c in row['claims']) for row in rows))
        with (d/'profiles.jsonl').open('ab') as f:f.write(b' ')
        with self.assertRaises(ValueError):verify_completion(report=d/'report.json',output=d/'envelopes.jsonl',profiles=d/'profiles.jsonl')

if __name__=='__main__':unittest.main()
