"""Coverage support levels must not turn metadata/simulation into genuine truth."""
import copy,gzip,hashlib,importlib.util,json,subprocess,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'src'))
from norway_company_agent.coverage_benchmark import evaluate,telemetry_metrics
from norway_company_agent.batch import profiles_from_bulk

class CoverageBenchmark(unittest.TestCase):
    def setUp(self):
        (ROOT/'.release-gate-tmp').mkdir(exist_ok=True)
        spec=importlib.util.spec_from_file_location('coverage_fixtures',ROOT/'tests/v2/test_independent_audit_repairs.py')
        m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
        self.case=m.IndependentAuditRepairs();self.case.setUp();self.addCleanup(self.case.doCleanups)
        self.assertEqual(self.case.run_cli(financial=False).returncode,0)
        self.profiles=self.case.rows('profiles.jsonl');self.out=self.case.rows('output.jsonl');self.org=['923609016']

    def assess(self,**kwargs):return evaluate(self.profiles,self.out,self.org,evidence_class='synthetic',**kwargs)

    def test_metadata_is_not_independent_authentication(self):
        r=self.assess()
        self.assertGreater(r['metadata_complete_claims'],0)
        self.assertEqual(r['independently_replayable_claims'],0)
        self.assertEqual(r['export_missing_facts'],0)
        self.assertNotIn('score',r)

    def test_pinned_baseline_can_authenticate_facts_without_private_key(self):
        baseline={p['organisation_number']:p for p in profiles_from_bulk(self.case.bulk,self.org)[0]}
        r=self.assess(baselines=baseline)
        self.assertEqual(r['independently_replayable_claims'],r['distinct_declared_facts'])
        self.assertEqual(r['company_family_coverage']['identity_status']['independently_replayable_companies'],1)

    def test_company_substitution_and_order_refused(self):
        with self.assertRaises(ValueError):evaluate(self.profiles,self.out,['993578843'],evidence_class='synthetic')
        with self.assertRaises(ValueError):evaluate(self.profiles,self.out,self.org,evidence_class='mixed')
        t={'run_id':'a','organisation_number':self.org[0],'source_family':'roles','physical_source_request_number':1,'duration_ms':20,'http_status':200}
        self.assertEqual(telemetry_metrics([t],self.org)['physical_starts'],1)
        with self.assertRaises(ValueError):telemetry_metrics([t,{**t,'run_id':'b'}],self.org)
        with self.assertRaises(ValueError):telemetry_metrics([t,t],self.org)

    def test_missing_export_and_invalid_citations_measured(self):
        self.out[0]['claims'].pop()
        self.profiles[0]['synthesis']['sentences'][0]['claim_ids']=['invented']
        r=self.assess();self.assertEqual(r['export_missing_facts'],1);self.assertGreater(r['invalid_citation_references'],0)

    def test_future_evidence_or_forged_source_not_counted_as_supported(self):
        baseline={p['organisation_number']:p for p in profiles_from_bulk(self.case.bulk,self.org)[0]}
        self.profiles[0]['evidence']['registry']['retrieved_at']='2999-01-01T00:00:00Z'
        r=self.assess(baselines=baseline)
        self.assertTrue(r['issues']);self.assertLess(r['independently_replayable_claims'],r['distinct_declared_facts'])
        t={'run_id':'a','organisation_number':self.org[0],'source_family':'roles','physical_source_request_number':1,'http_status':None,'classification':'timeout'}
        metric=telemetry_metrics([t],self.org)['families']['roles']
        self.assertIsNone(metric['p95_ms']);self.assertEqual(metric['missing_timings'],1)

    def test_actual_cli_produces_category_metrics_and_checks_completion(self):
        d=self.case.d;output=d/'coverage.json'
        p=subprocess.run([sys.executable,'-S','scripts/evaluate_coverage.py','--profiles',str(d/'profiles.jsonl'),
            '--envelopes',str(d/'output.jsonl'),'--organisations',str(self.case.batch),'--bulk',str(self.case.bulk),
            '--report',str(d/'report.json'),'--evidence-class','synthetic','--output',str(output)],cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)
        result=json.loads(output.read_text());self.assertEqual(result['completion_bindings'],'PASS')
        self.assertEqual(result['companies'],1);self.assertEqual(result['export_missing_facts'],0)
        manifest=d/'report.json.completion.json';old=manifest.read_bytes()
        bad=subprocess.run([sys.executable,'-S','scripts/evaluate_coverage.py','--profiles',str(d/'profiles.jsonl'),
            '--envelopes',str(d/'output.jsonl'),'--organisations',str(self.case.batch),
            '--evidence-class','synthetic','--output',str(manifest)],cwd=ROOT,capture_output=True,text=True)
        self.assertNotEqual(bad.returncode,0);self.assertEqual(manifest.read_bytes(),old)

    def test_selection_is_pinned_deterministic_and_excludes_historical_sample(self):
        spec=importlib.util.spec_from_file_location('coverage_selection',ROOT/'scripts/select_coverage_sample.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        fixture=importlib.util.spec_from_file_location('coverage_ids',ROOT/'tests/v2/test_admission_configuration_repairs.py')
        ids=importlib.util.module_from_spec(fixture);fixture.loader.exec_module(ids)
        values=ids.identities(30);path=self.case.d/'universe.jsonl.gz'
        with gzip.open(path,'wt',encoding='utf-8') as h:
            for i,org in enumerate(values):h.write(json.dumps({'organisation_number':org,'legal_form':'AS' if i%2 else 'ENK','industry_code':str(i%5),'employees':i,'website':'https://example.invalid' if i%3 else None})+'\n')
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        a,manifest=module.select(path,digest=digest,count=10,seed='fixed',excluded=set(values[:3]))
        b,_=module.select(path,digest=digest,count=10,seed='fixed',excluded=set(values[:3]))
        self.assertEqual(a,b);self.assertEqual(len(a),10)
        self.assertFalse(set(r['organisation_number'] for r in a)&set(values[:3]))
        self.assertIn('organisation_number',manifest['actual_columns'])
        with self.assertRaises(ValueError):module.select(path,digest='0'*64,count=10,seed='fixed',excluded=set())

if __name__=='__main__':unittest.main()
