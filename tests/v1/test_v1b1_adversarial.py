"""V1b.1 permanent regression suite based on the two V1b audit rounds."""
from __future__ import annotations
import copy
import csv
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from v0_kernel.storage import read_jsonl
from v1_official.runner import run, validate_v1
from v1_official.sources import SourceResponse, endpoint, extract, fixture_fetch, source_is_complete
from test_v1b_refresh_and_sources import ORG_A, ORG_B, ROOT, fixture

ORG_C = "978617360"  # verified syntactic input only; ALL source content is synthetic


def check_id(org):
    v = sum(int(a)*b for a,b in zip(org[:8], [3,2,7,6,5,4,3,2]))
    return (11 - v % 11) % 11 == int(org[-1])


class HardenedAudit(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.d = Path(self.folder.name)
        self.sequence = 0
        self.fixture = fixture()
        self.orgs = [ORG_A, ORG_B]
        self.registry = ROOT / 'tests/v0/demo/frozen-registry.csv'
        self.assertTrue(check_id(ORG_A) and check_id(ORG_B))

    def execute(self, *, orgs=None, payload=None, previous=None, requests=100, seconds=30,
                fetcher=None, cutoff=None, registry=None):
        orgs = orgs if orgs is not None else self.orgs
        self.sequence += 1
        batch = self.d / f'batch{self.sequence}.jsonl'
        batch.write_text(''.join(json.dumps({'organisation_number': n})+'\n' for n in orgs), encoding='utf-8')
        source = self.d / f'fixture{self.sequence}.json'
        source.write_text(json.dumps(payload if payload is not None else self.fixture), encoding='utf-8')
        out = self.d / f'out{self.sequence}.jsonl'
        rep = self.d / f'report{self.sequence}.json'
        result = run(organisations=batch, bulk=registry or self.registry,
                     output=out, report=rep, run_id=f'audit-{self.sequence}',
                     expected_count=len(orgs), expected_bulk_sha256=None,
                     fixture_mode=True, fixture_responses=None if fetcher else source,
                     fetcher=fetcher, previous_output=previous,
                     expected_previous_sha256=hashlib.sha256(previous.read_bytes()).hexdigest() if previous else None,
                     max_requests=requests, max_seconds=seconds, checkpoint_every=5,
                     evidence_cutoff=cutoff)
        return result, out, read_jsonl(out)

    def registry_for(self, names):
        path = self.d / 'registry-custom.csv'
        with path.open('w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=['organisasjonsnummer','navn','organisasjonsform.kode'])
            writer.writeheader()
            for org,name in names.items():
                writer.writerow({'organisasjonsnummer':org,'navn':name,'organisasjonsform.kode':'AS'})
        return path

    def test_expansion_reorder_and_removed_inputs_are_keyed_not_indexed(self):
        # Pick a valid third identifier via the published check-digit rule.
        org = '978617360'
        if not check_id(org):
            prefix = '97861736'; digit=(11-sum(int(c)*w for c,w in zip(prefix,[3,2,7,6,5,4,3,2]))%11)%11
            org=prefix+str(digit)
        self.assertTrue(check_id(org))
        reg = self.registry_for({ORG_A:'SYNTHETIC ALPHA AS', ORG_B:'SYNTHETIC BETA AS',org:'SYNTHETIC GAMMA AS'})
        data = copy.deepcopy(self.fixture)
        data[org] = {'registry_live':{'body':{'organisasjonsnummer':org,'navn':'SYNTHETIC GAMMA AS'}},
                     'roles':{'body':{'rollegrupper':[]}},
                     'locations':{'body':{'_embedded':{'underenheter':[]}}},'financials':{'body':[]}}
        r1,p1,rows1 = self.execute(registry=reg,payload=data)
        self.assertEqual(r1['run_status'],'completed',r1)
        r2,p2,rows2 = self.execute(orgs=[ORG_B,org,ORG_A],previous=p1,registry=reg,payload=data)
        self.assertEqual(r2['run_status'],'completed',r2)
        mapping = {r['organisation_number']:r for r in rows2}
        self.assertEqual(mapping[org]['official_refresh']['state'],'no_prior_baseline')
        self.assertEqual(mapping[ORG_A]['official_refresh']['state'],'unchanged')
        self.assertEqual(mapping[ORG_B]['official_refresh']['state'],'unchanged')
        self.assertEqual([r['organisation_number'] for r in rows2],[ORG_B,org,ORG_A])
        r3,_,rows3 = self.execute(orgs=[ORG_A], previous=p2, registry=reg,payload=data)
        self.assertEqual(r3['run_status'],'completed',r3)
        self.assertEqual(rows3[0]['organisation_number'],ORG_A)

    def test_outage_then_recovery_emits_change_once(self):
        _,prev,_=self.execute()
        blocked=copy.deepcopy(self.fixture)
        blocked[ORG_A]['roles']={'status':403}
        r2,p2,r2rows=self.execute(payload=blocked,previous=prev)
        self.assertEqual(r2['run_status'],'completed')
        self.assertEqual(r2rows[0]['official_refresh']['state'],'source_unavailable')
        recovered=copy.deepcopy(self.fixture)
        recovered[ORG_A]['roles']['body']['rollegrupper'][0]['roller'][0]['person']['navn']['fornavn']='Grace'
        r3,p3,r3rows=self.execute(payload=recovered,previous=p2)
        self.assertEqual(r3['run_status'],'completed',r3)
        self.assertEqual(sorted(e['kind'] for e in r3rows[0]['official_changes'] if e['module']=='roles'),['added','removed'])
        r4,_,r4rows=self.execute(payload=recovered,previous=p3)
        self.assertEqual(r4['run_status'],'completed',r4)
        self.assertFalse(r4rows[0]['official_changes'])

    def test_unchanged_refreshes_deduplicate_source_versions(self):
        prev=None
        counts=[]
        for i in range(25):
            r,out,rows=self.execute(previous=prev)
            self.assertEqual(r['run_status'],'completed',r)
            self.assertEqual(rows[0]['official_changes'],[])
            counts.append(len(rows[0]['historical_official_snapshots']))
            prev=out
        self.assertLessEqual(max(counts),4)
        self.assertGreaterEqual(len(rows[0]['official_fetch_observations']),4)
        self.assertEqual(len({(o['module'],o['source_sha256'],o['retrieved_at']) for o in rows[0]['official_fetch_observations']}),len(rows[0]['official_fetch_observations']))

    def test_name_change_requires_official_history_and_nfc_equivalence(self):
        body={'organisasjonsnummer':ORG_A,'navn':'SYNTHETIC NEW AS'}
        self.assertEqual(extract('registry_live',ORG_A,body,frozen_legal_name='SYNTHETIC ALPHA AS')[1],'ambiguous')
        body['historiskeNavn']=[{'navn':'SYNTHETIC ALPHA AS','fraDato':'2024-01-01','tilDato':'2025-12-31'}]
        candidates,state=extract('registry_live',ORG_A,body,frozen_legal_name='SYNTHETIC ALPHA AS')
        self.assertEqual(state,'available')
        self.assertEqual(candidates[0]['value'],'SYNTHETIC NEW AS')
        self.assertEqual(extract('registry_live',ORG_B,body,frozen_legal_name='SYNTHETIC ALPHA AS')[1],'ambiguous')
        decomposed='A\u030aSEN AS'
        composed='ÅSEN AS'
        self.assertEqual(extract('registry_live', ORG_A,
                  {'organisasjonsnummer':ORG_A,'navn':decomposed},frozen_legal_name=composed)[1],'available')

    def test_role_collision_keeps_distinct_records(self):
        payload=copy.deepcopy(self.fixture)
        role=payload[ORG_A]['roles']['body']['rollegrupper'][0]['roller'][0]
        payload[ORG_A]['roles']['body']['rollegrupper'][0]['roller'].append(copy.deepcopy(role))
        report,_,rows=self.execute(payload=payload)
        self.assertEqual(report['run_status'],'completed',report)
        claims=[c for c in rows[0]['claims'] if c['field']=='registered_role']
        self.assertEqual(len(claims),2)
        self.assertNotEqual(claims[0]['evidence_ids'],claims[1]['evidence_ids'])

    def test_financial_four_periods_keeps_latest(self):
        payload=copy.deepcopy(self.fixture)
        body=[]
        for i,year in enumerate([2022,2023,2024,2025]):
            body.append({'organisasjonsnummer': ORG_A,'valuta':'NOK','regnskapstype':'SELSKAP',
                  'regnskapsperiode':{'fraDato':f'{year}-01-01','tilDato':f'{year}-12-31'},
                  'resultatregnskapResultat':{'driftsresultat':{'driftsinntekter':{'sumDriftsinntekter':100+i}}}})
        payload[ORG_A]['financials']={'body':body}
        report,_,rows=self.execute(payload=payload)
        self.assertEqual(report['run_status'],'completed',report)
        claims=[c for c in rows[0]['claims'] if c['field']=='financial_revenue']
        self.assertEqual(len(claims),4)
        self.assertEqual({c['reporting_period'].split('/')[-1][:4] for c in claims},{'2022','2023','2024','2025'})

    def test_subunits_90_not_truncated_and_partial_page_reported(self):
        payload=copy.deepcopy(self.fixture)
        source=[]
        prefix=10000000
        while len(source)<90:
            digits=[int(c) for c in str(prefix)]
            check=11-sum(a*b for a,b in zip(digits,(3,2,7,6,5,4,3,2)))%11
            if check==11:check=0
            if check!=10:
                source.append({'organisasjonsnummer':str(prefix)+str(check),'overordnetEnhet':ORG_A,'navn':f'WORKPLACE {len(source)}'})
            prefix+=1
        payload[ORG_A]['locations']['body']={'_embedded':{'underenheter':source},
                                               'page':{'number':0,'totalPages':2,'totalElements':140}}
        report,_,rows=self.execute(payload=payload)
        self.assertEqual(report['run_status'],'completed',report)
        self.assertEqual(sum(c['field']=='registered_subunit' for c in rows[0]['claims']),90)
        self.assertFalse(rows[0]['official_modules']['locations']['source_complete'])
        self.assertFalse(source_is_complete('locations',payload[ORG_A]['locations']['body']))

    def test_budget_exhaustion_does_not_fail_all_outputs(self):
        def counted(module, org):
            return replace(fixture_fetch(module,org,self.fixture), requests=1)
        report,_,rows=self.execute(requests=1, fetcher=counted)
        self.assertEqual(report['run_status'],'completed',report)
        self.assertTrue(report['partial_research_due_to_budget'])
        self.assertTrue(report['validation']['passed'])
        self.assertEqual(len(rows),2)
        self.assertTrue(any(r['official_modules']['financials']['reason']=='budget_exhausted_not_checked' for r in rows))

    def test_hard_timeout_bounds_blocking_fetcher(self):
        def slow(module,org):
            time.sleep(0.3)
            return fixture_fetch(module,org,self.fixture)
        started=time.monotonic()
        r,_,rows=self.execute(fetcher=slow,seconds=0.045)
        self.assertLess(time.monotonic()-started,0.25)
        self.assertEqual(r['run_status'],'completed',r)
        self.assertTrue(r['partial_research_due_to_budget'])
        self.assertTrue(r['unrecoverable_transport_timeout'])
        self.assertEqual(len(rows),2)

    def test_custom_fetcher_cannot_claim_live_provenance(self):
        batch=self.d/'in.jsonl'; batch.write_text(json.dumps({'organisation_number':ORG_A})+'\n')
        out=self.d/'unsafe.jsonl'; report=self.d/'unsafe.json'
        with self.assertRaises(ValueError):
            run(organisations=batch,bulk=self.registry,output=out,report=report,
                run_id='unauth',expected_count=1,expected_bulk_sha256=None,
                fixture_mode=False,fetcher=lambda a,b:fixture_fetch(a,b,self.fixture))
        r,_,_=self.execute(fetcher=lambda module,org: fixture_fetch(module,org,self.fixture))
        self.assertEqual(r['source_mode'],'SYNTHETIC_INJECTED_TRANSPORT')
        self.assertFalse(r['official_release_ready'])

    def test_cutoff_excludes_post_cutoff_data(self):
        r,_,rows=self.execute(cutoff='2020-01-01T00:00:00Z')
        self.assertEqual(r['run_status'],'completed')
        self.assertFalse(any(c['field']=='registered_role' for c in rows[0]['claims']))
        self.assertEqual(rows[0]['availability']['roles'],'failed')

    def test_validator_rejects_contradictory_envelope_and_negative_cost(self):
        r,_,rows=self.execute()
        self.assertTrue(r['validation']['passed'])
        for field,value in [('operations', dict(rows[0]['operations'],third_party_cost_usd=-1)),
                            ('run',dict(rows[0]['run'],terminal_status='failed')),
                            ('errors',[{'anything':'invalid'}]),
                            ('official_modules', dict(rows[0]['official_modules'],roles={'state':'failed'}))]:
            tampered=copy.deepcopy(rows)
            tampered[0][field]=value
            self.assertFalse(validate_v1(tampered,self.orgs,rows,{},None)['passed'],field)

    def test_absent_frozen_identity_not_reported_not_applicable(self):
        reg=self.registry_for({ORG_A:'SYNTHETIC ALPHA AS'})
        r,_,rows=self.execute(registry=reg)
        self.assertEqual(r['run_status'],'completed')
        self.assertEqual(rows[1]['availability']['roles'],'failed')
        self.assertEqual(rows[1]['official_modules']['roles']['reason'],'identity_not_verified')

if __name__ == '__main__':
    unittest.main()

class FailClosedReleaseGateTests(unittest.TestCase):
    def test_legacy_child_zero_exit_without_pass_cannot_pass(self):
        from scripts.release_gate_v1b1 import evaluate_legacy
        import subprocess
        broken=subprocess.CompletedProcess(['fixture'],0,json.dumps({'status':'FAIL','checks':{'source':False}}),'')
        with self.assertRaises(RuntimeError): evaluate_legacy(broken)
        fake=subprocess.CompletedProcess(['fixture'],0,json.dumps({'status':'LOCAL_V1A_GATE_PASS','checks':{'source':False},'v0_tests':93,'v1_tests':19,'synthetic_companies':100}),'')
        with self.assertRaises(RuntimeError): evaluate_legacy(fake)
    def test_test_suite_fake_success_marker_cannot_pass(self):
        from scripts.release_gate_v1b1 import evaluate_tests
        import subprocess
        with self.assertRaises(RuntimeError):
            evaluate_tests(subprocess.CompletedProcess(['fixture'],0,'OK\n','Ran 53 tests in 1s'))
        with self.assertRaises(RuntimeError):
            evaluate_tests(subprocess.CompletedProcess(['fixture'],0,'','Ran 2 tests in 1s\n\nOK\n'))

class FinancialScopeRegressionTests(unittest.TestCase):
    def test_group_only_financials_withheld_from_legal_entity(self):
        row={'organisasjonsnummer': ORG_A,'valuta':'NOK','regnskapstype':'KONSERN',
             'regnskapsperiode':{'fraDato':'2025-01-01','tilDato':'2025-12-31'},
             'resultatregnskapResultat':{'driftsresultat':{'driftsinntekter':{'sumDriftsinntekter':500}}}}
        facts,state=extract('financials', ORG_A,[row],frozen_legal_name=None)
        self.assertEqual(facts,[])
        self.assertEqual(state,'ambiguous')
    def test_unresolved_amendment_conflict_does_not_publish_either_number(self):
        record={'organisasjonsnummer':ORG_A,'valuta':'NOK','regnskapstype':'SELSKAP',
                'regnskapsperiode':{'fraDato':'2025-01-01','tilDato':'2025-12-31'},
                'resultatregnskapResultat':{'driftsresultat':{'driftsinntekter':{'sumDriftsinntekter':100}}}}
        changed=copy.deepcopy(record)
        changed['resultatregnskapResultat']['driftsresultat']['driftsinntekter']['sumDriftsinntekter']=999
        facts,state=extract('financials', ORG_A,[record,changed],frozen_legal_name=None)
        self.assertFalse(any(f['field']=='financial_revenue' for f in facts))
        self.assertEqual(state,'ambiguous')

class RoleOrderingRegression(unittest.TestCase):
    def test_role_page_reordering_does_not_invent_change(self):
        case=HardenedAudit(methodName='test_role_collision_keeps_distinct_records')
        case.setUp()
        try:
            first=copy.deepcopy(case.fixture)
            roles=first[ORG_A]['roles']['body']['rollegrupper'][0]['roller']
            second=copy.deepcopy(roles[0])
            second['person']['navn']['fornavn']='Grace'
            roles.append(second)
            r1,p1,_=case.execute(payload=first)
            self.assertEqual(r1['run_status'],'completed')
            second_run=copy.deepcopy(first)
            second_run[ORG_A]['roles']['body']['rollegrupper'][0]['roller'].reverse()
            r2,_,rows=case.execute(payload=second_run,previous=p1)
            self.assertEqual(r2['run_status'],'completed')
            self.assertFalse([e for e in rows[0]['official_changes'] if e['module']=='roles'])
        finally:
            case.doCleanups()


class ScaleGateIntegrityTests(unittest.TestCase):
    def test_scale_child_zero_exit_but_false_pass_is_rejected(self):
        from scripts.release_gate_v1b1 import evaluate_scale
        import subprocess
        fake=subprocess.CompletedProcess(['fixture'],0,json.dumps({
             'scenario':'SYNTHETIC_1000_500_REQUESTS','all_passed':False,
             'checks':{'exact_envelopes':False},'source_requests':500,
             'run_status':'completed'}),'')
        with self.assertRaises(RuntimeError): evaluate_scale(fake)
