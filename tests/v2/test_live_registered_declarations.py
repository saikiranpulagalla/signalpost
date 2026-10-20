"""Actual runner with captured SYNTHETIC JSON; all external sockets prohibited."""
import copy,csv,hashlib,json,os,re,subprocess,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path[:0]=[str(ROOT/'src'),str(ROOT)]
from norway_company_agent.official import normalize_entity
from norway_company_agent.profile_integrity import verify_source_record
from norway_company_agent.completion import verify_completion
from norway_company_agent.evaluator_adapter import validate_starter_envelopes
HARNESS=r'''
import importlib.util,sys,json,hashlib,socket,urllib.request
from pathlib import Path
def forbidden(*a,**k):raise AssertionError('EXTERNAL NETWORK START')
socket.socket.connect=forbidden;socket.create_connection=forbidden;urllib.request.OpenerDirector.open=forbidden
spec=importlib.util.spec_from_file_location('runner',sys.argv[1]);r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
from norway_company_agent.http import FetchResult
fixture=Path(sys.argv[2]);calls=[]
def fetch(url,**kw):
 calls.append(url);raw=(fixture/'entity.json').read_bytes();digest=hashlib.sha256(raw).hexdigest()
 (fixture/(digest+'.json')).write_bytes(raw)
 return FetchResult(url,200,10,len(raw),body=json.loads(raw),raw_bytes=raw,content_sha256=digest,retrieved_at='2026-10-01T00:00:00Z')
r.fetch_json=fetch;sys.argv=[sys.argv[1]]+sys.argv[3:]
try:r.main()
finally:print('FIXTURE_SOURCE_STARTS='+str(len(calls)))
'''
class LiveRegisteredDeclarations(unittest.TestCase):
    def setUp(self):
        (ROOT/'.release-gate-tmp').mkdir(exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'.release-gate-tmp');self.addCleanup(self.tmp.cleanup)
        self.d=Path(self.tmp.name);self.raw=self.d/'raw';self.raw.mkdir();self.batch=self.d/'batch.jsonl';self.bulk=self.d/'bulk.csv'
        self.batch.write_text('{"organisation_number":"923609016"}\n')
        with self.bulk.open('w',newline='',encoding='utf-8') as f:
            w=csv.writer(f);w.writerow(['organisasjonsnummer','navn','organisasjonsform.kode']);w.writerow(['923609016','Frozen AS','AS'])
        self.body={'organisasjonsnummer':'923609016','navn':'Søndre Øst AS','organisasjonsform':{'kode':'AS'},
            'aktivitet':['Registrert rådgivning','Registrert rådgivning'],'vedtektsfestetFormaal':['Registrert formål']}
    def run_cli(self,name='run',extra=(),bootstrap=False):
        (self.raw/'entity.json').write_text(json.dumps(self.body,ensure_ascii=False),encoding='utf-8')
        d=self.d/name
        args=['--organisations',str(self.batch),'--modules','registry,accounting_obligation,registry_live',
            '--network-policy','authorized_public_brreg','--confirm-authorized-live-sources','--max-source-requests','1','--source-attempts','1',
            '--workers','1','--run-id',name,'--output-target','starter_compatible','--verified-source-snapshots',str(self.raw),
            '--profiles-output',str(d/'profiles.jsonl'),'--output',str(d/'envelopes.jsonl'),'--report',str(d/'report.json'),
            '--viewer-output',str(d/'viewer.html')]
        args+=['--live-bootstrap'] if bootstrap else ['--bulk',str(self.bulk)]
        env=os.environ.copy();env['TEMP']=env['TMP']=str(self.d)
        return subprocess.run([sys.executable,'-S','-c',HARNESS,str(ROOT/'scripts/run_competition_batch.py'),str(self.raw),*args,*extra],
            cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',timeout=60)
    def profile(self,name='run'):return json.loads((self.d/name/'profiles.jsonl').read_text(encoding='utf-8'))
    def test_literal_parser_deduplicates_and_rejects_malformed(self):
        p=normalize_entity(self.body,declarations=True);self.assertEqual(p['registered_activity'],['Registrert rådgivning'])
        for raw in (None,[],[1],[''],{},True):
            with self.subTest(raw=raw):self.assertNotIn('registered_activity',normalize_entity({'aktivitet':raw},declarations=True))
    def test_actual_cli_exports_exact_source_locators_citations_and_viewer(self):
        r=self.run_cli();self.assertEqual(r.returncode,0,r.stderr);p=self.profile()
        self.assertTrue(verify_source_record(p,'registry_live',self.raw))
        es=[json.loads((self.d/'run/envelopes.jsonl').read_text())];self.assertTrue(validate_starter_envelopes(es,['923609016'])['passed'])
        claims=[c for c in p['research_result']['claims'] if c['field'] in {'registered_activity','registered_purpose'}]
        self.assertEqual(len(claims),2);self.assertEqual(p['research_result']['normalization_version'],4)
        self.assertEqual({c['source_locator']['pointer'] for c in claims},{'/aktivitet','/vedtektsfestetFormaal'})
        self.assertTrue(all(c['source_sha256'] and c['retrieved_at'] and c['evidence_fields']==['registry_live'] for c in claims))
        self.assertIn('not verification of actual operations',json.dumps(p['synthesis']))
        ids={c['id'] for c in p['research_result']['claims']};self.assertTrue(all(set(s['claim_ids'])<=ids for s in p['synthesis']['sentences']))
        d=self.d/'run';verify_completion(report=d/'report.json',output=d/'envelopes.jsonl',profiles=d/'profiles.jsonl')
        self.assertEqual(json.loads(re.search(r'id="profiles">(.*?)</script>',(d/'viewer.html').read_text(encoding='utf-8'),re.S).group(1)),[p])
    def test_legacy_projection_replay_does_not_invent_discarded_fields(self):
        r=self.run_cli();self.assertEqual(r.returncode,0,r.stderr);p=self.profile()
        p['evidence']['registry_live']['value']=normalize_entity(self.body)
        self.assertTrue(verify_source_record(p,'registry_live',self.raw))
        self.assertNotIn('registered_activity',p['evidence']['registry_live']['value'])
    def test_exact_identity_mismatch_and_missing_identity_do_not_publish_declarations(self):
        for org in ('993578843',None):
            self.body['organisasjonsnummer']=org;r=self.run_cli(name='bad'+str(org));self.assertEqual(r.returncode,0,r.stderr)
            self.assertFalse(any(c['field'] in {'registered_activity','registered_purpose'} for c in self.profile('bad'+str(org))['research_result']['claims']))
    def test_forged_content_receipt_claim_and_digest_resume_rejected(self):
        self.assertEqual(self.run_cli().returncode,0);original=self.profile();file=self.d/'run/profiles.jsonl';output=(self.d/'run/envelopes.jsonl').read_bytes()
        for kind in ('value','digest','claim','org','time'):
            p=copy.deepcopy(original)
            if kind=='value':p['evidence']['registry_live']['value']['registered_activity']=['Forged']
            elif kind=='digest':p['evidence']['registry_live']['content_sha256']='0'*64
            elif kind=='org':p['evidence']['registry_live']['value']['organisation_number']='993578843'
            elif kind=='time':p['evidence']['registry_live']['retrieved_at']='2999-01-01T00:00:00Z'
            else:next(c for c in p['research_result']['claims'] if c['field']=='registered_activity')['value']=['Forged']
            file.write_text(json.dumps(p),encoding='utf-8');r=self.run_cli(extra=['--resume']);self.assertNotEqual(r.returncode,0,kind)
            self.assertEqual((self.d/'run/envelopes.jsonl').read_bytes(),output)
    def test_complete_bootstrap_and_frozen_resume_start_zero_requests(self):
        for name,bootstrap in (('frozen',False),('live',True)):
            self.assertEqual(self.run_cli(name=name,bootstrap=bootstrap).returncode,0)
            r=self.run_cli(name=name,bootstrap=bootstrap,extra=['--resume']);self.assertEqual(r.returncode,0,r.stderr)
            self.assertIn('FIXTURE_SOURCE_STARTS=0',r.stdout)
    def test_refresh_preserves_both_evidence_and_identical_replay_no_changes(self):
        self.assertEqual(self.run_cli().returncode,0);prior=self.d/'run/profiles.jsonl'
        args=['--previous-profiles',str(prior),'--previous-bulk',str(self.bulk),'--observation-key',str(self.d/'run/report.json.observation-auth.key')]
        self.assertEqual(self.run_cli('same',extra=args).returncode,0);self.assertEqual(self.profile('same')['changes'],[])
        self.body['vedtektsfestetFormaal']=['Nytt registrert formål'];r=self.run_cli('changed',extra=args);self.assertEqual(r.returncode,0,r.stderr)
        changes=self.profile('changed')['changes'];self.assertEqual(len(changes),1)
        self.assertEqual(changes[0]['field'],'registry_live.registered_purpose');self.assertTrue(changes[0]['previous_evidence'] and changes[0]['current_evidence'])
    def test_frozen_overlap_not_double_counted_and_hostile_text_escaped(self):
        self.bulk.write_text('organisasjonsnummer,navn,organisasjonsform.kode,aktivitet\n923609016,Frozen,AS,Old declaration\n')
        self.body['aktivitet']=['</script><img src=x onerror=alert(1)>'];r=self.run_cli();self.assertEqual(r.returncode,0,r.stderr)
        claims=[c for c in self.profile()['research_result']['claims'] if c['field']=='registered_activity'];self.assertEqual(len(claims),1)
        self.assertEqual(claims[0]['evidence_fields'],['registry_live']);self.assertNotIn('</script><img',(self.d/'run/viewer.html').read_text())
    def test_missing_values_do_not_create_absence_or_false_material_change(self):
        self.assertEqual(self.run_cli().returncode,0);self.body.pop('aktivitet');self.body.pop('vedtektsfestetFormaal')
        r=self.run_cli('missing',extra=['--previous-profiles',str(self.d/'run/profiles.jsonl'),'--previous-bulk',str(self.bulk),
            '--observation-key',str(self.d/'run/report.json.observation-auth.key')]);self.assertEqual(r.returncode,0,r.stderr)
        p=self.profile('missing');self.assertEqual(p['changes'],[]);self.assertTrue(p['historical_observations'])
        self.assertFalse(any(c['field'] in {'registered_activity','registered_purpose'} for c in p['research_result']['claims']))
if __name__=='__main__':unittest.main()
