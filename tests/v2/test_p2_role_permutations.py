import copy,sys,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2];sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from norway_company_agent.refresh import supported_change,diff_profile

class RolePermutations(unittest.TestCase):
    def test_authenticated_actual_cli_permutation_has_no_change(self):
        sys.path.insert(0,str(ROOT/'tests/v2'))
        import test_jsonl_lifecycle as fixtures
        original=next(line for line in fixtures.HARNESS.split('\n') if "if family=='roles':" in line)
        prefix=" if family=='roles':body.update(rollegrupper=[{'type':{'beskrivelse':'Board'},'sistEndret':'2025-01-01','roller':"
        people=[{'type':{'beskrivelse':'Chair'},'person':{'navn':{'fornavn':'Synthetic','etternavn':'Å'}}},{'type':{'beskrivelse':'Member'},'person':{'navn':{'fornavn':'Other','etternavn':'Ø'}}}]
        for policy in ('strict','best_effort'):
            helper=fixtures.JsonlLifecycle();helper.setUp()
            try:
                old=fixtures.HARNESS.replace(original,prefix+repr(people)+'}])').replace('2026-10-20T00:00:00Z','2026-10-19T00:00:00Z')
                with patch.object(fixtures,'HARNESS',old):p,d=helper.run_cli('old-'+policy,policy)
                helper.checked(p,d)
                current=fixtures.HARNESS.replace(original,prefix+repr(people[::-1])+'}])')
                with patch.object(fixtures,'HARNESS',current):
                    p,new=helper.run_cli('new-'+policy,policy,extra=['--previous-profiles',str(d/'profiles.jsonl'),'--previous-bulk',str(helper.bulk),'--observation-key',str(d/'report.json.observation-auth.key')])
                es,ps=helper.checked(p,new);self.assertTrue(all(not e['changes'] for e in es))
            finally:helper.doCleanups()
    def test_permutations_preserve_multiplicity_and_actual_changes(self):
        a={'name':'Synthetic Å','role':'Chair','organisation_number':None,'last_changed':'2025-01-01'}
        b={'name':'Other Ø','role':'Member','organisation_number':None,'last_changed':'2025-01-01'}
        old={'status':'available','value':{'roles':[a,b,a]},'content_sha256':'a'*64,'retrieved_at':'2026-10-19T00:00:00Z'}
        new={**copy.deepcopy(old),'value':{'roles':[a,a,b]},'content_sha256':'b'*64,'retrieved_at':'2026-10-20T00:00:00Z'}
        before=copy.deepcopy((old,new))
        self.assertIsNone(supported_change('923609016','roles.roles',old,new))
        self.assertEqual(diff_profile({'organisation_number':'923609016','evidence':{'roles':old}},{'organisation_number':'923609016','evidence':{'roles':new}}),[])
        self.assertEqual((old,new),before)
        for roles in ([a,b],[a,b,a,b],[{**a,'name':'Changed'},b,a],[{**a,'role':'Member'},b,a],[{**a,'organisation_number':'993578843'},b,a],[{**a,'last_changed':'2025-02-01'},b,a]):
            changed={**new,'value':{'roles':roles}}
            event=supported_change('923609016','roles.roles',old,changed)
            self.assertIsNotNone(event);self.assertEqual(event['previous_evidence'],old);self.assertEqual(event['current_evidence'],changed)
    def test_other_collection_fields_retain_existing_order_semantics(self):
        old={'status':'available','value':{'years':['2023','2024']},'content_sha256':'a'*64}
        new={**old,'value':{'years':['2024','2023']},'content_sha256':'b'*64}
        self.assertIsNotNone(supported_change('923609016','financial_history.years',old,new))
