import json,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path[:0]=[str(ROOT),str(ROOT/'tests/v2')]
import test_jsonl_lifecycle as fixtures

class ZeroAdmission(unittest.TestCase):
    def test_zero_cap_is_preplanned_omission_under_both_policies(self):
        for policy in ('strict','best_effort'):
            helper=fixtures.JsonlLifecycle();helper.setUp()
            try:
                p,d=helper.run_cli('zero-'+policy,policy,extra=['--max-source-requests','0','--network-policy','offline'])
                es,ps=helper.checked(p,d)
                report=json.loads((d/'report.json').read_bytes())
                self.assertTrue(all(not v for v in report['execution_plan']['admission']['admitted_organisations'].values()))
                self.assertEqual(report['operations']['requests'],0)
                self.assertIn('"source_starts": 0',p.stdout)
                self.assertTrue(all(set(pr['evidence'])=={'registry','accounting_obligation'} for pr in ps))
                self.assertTrue(all(not e['errors'] for e in es))
            finally:helper.doCleanups()
    def test_one_request_and_zero_family_caps_remain_bounded(self):
        helper=fixtures.JsonlLifecycle();helper.setUp()
        try:
            p,d=helper.run_cli('one',extra=['--max-source-requests','1'])
            helper.checked(p,d);r=json.loads((d/'report.json').read_bytes())
            self.assertEqual(sum(map(len,r['execution_plan']['admission']['admitted_organisations'].values())),1)
            self.assertEqual(r['source_request_policy']['source_requests_started'],1)
            p,d=helper.run_cli('families-zero',extra=['--source-family-budgets','financials=0,roles=0,locations=0,registry_live=0','--network-policy','offline'])
            helper.checked(p,d);self.assertEqual(json.loads((d/'report.json').read_bytes())['operations']['requests'],0)
        finally:helper.doCleanups()
