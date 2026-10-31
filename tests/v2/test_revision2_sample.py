"""Actual-runner synthetic sample, completion and comparison regression."""
import importlib.util,json,re,subprocess,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from norway_company_agent.completion import verify_completion

class RevisionTwoSample(unittest.TestCase):
    def test_actual_sample_positive_facts_citations_and_completion(self):
        with tempfile.TemporaryDirectory() as temp:
            d=Path(temp)/'demo'
            p=subprocess.run([sys.executable,'-B','-S',str(ROOT/'scripts/build_100_company_demo.py'),'--output-dir',str(d)],cwd=ROOT,capture_output=True,text=True,timeout=120)
            self.assertEqual(p.returncode,0,p.stderr)
            rows=[json.loads(x) for x in (d/'envelopes.jsonl').read_bytes().split(b'\n') if x.strip()]
            ps=[json.loads(x) for x in (d/'profiles.jsonl').read_bytes().split(b'\n') if x.strip()]
            self.assertEqual(len({p['organisation_number'] for p in ps}),100)
            for i,expected in ((0,125000),(1,-10000)):
                c=next(c for c in rows[i]['claims'] if c['field']=='financial_annual_result')
                self.assertEqual(c['value']['amount'],expected)
                self.assertEqual(c['value']['currency'],'NOK')
                self.assertEqual(c['value']['reporting_period']['tilDato'],'2025-12-31')
                self.assertTrue(set(c['evidence_ids']) <= {e['id'] for e in rows[i]['evidence']})
                self.assertTrue(any(c['field']=='registered_role' for c in rows[i]['claims']))
                self.assertTrue(any(str(expected) in s['text'] for s in ps[i]['synthesis']['sentences']))
            verify_completion(report=d/'report.json',output=d/'envelopes.jsonl',profiles=d/'profiles.jsonl')
            html=(d/'viewer.html').read_text(encoding='utf-8')
            self.assertEqual(json.loads(re.search(r'id="profiles">(.*?)</script>',html,re.S).group(1)),ps)
            self.assertIn('blocked',json.dumps(rows[2:]))

    def test_comparison_controls_values_and_safe_embedding(self):
        spec=importlib.util.spec_from_file_location('view',ROOT/'scripts/build_profile_viewer.py')
        view=importlib.util.module_from_spec(spec);spec.loader.exec_module(view)
        html=view.render([{'name':'</script><script>BAD</script>','organisation_number':'993578843'}])
        self.assertNotIn('</script><script>BAD',html)
        for text in ('first-company','second-company','comparePair','financial_revenue','financial_annual_result','registered_role','evidence_fields','reporting_period','scope','Choose two different companies'):
            self.assertIn(text,html)
