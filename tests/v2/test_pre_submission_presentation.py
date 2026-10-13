"""Schema-derived presentation fixtures; no HTTP or invented real observations."""
import copy, importlib.util, json, re, sys, unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from norway_company_agent.synthesis import build_synthesis

def claim(field,value,year=None,**kw):
    return {'id':f'{field}-{year or 0}', 'field':field, 'value':value,
            'scope':'exact_legal_entity', 'evidence_fields':['financials'],
            'reporting_period':{'fraDato':f'{year}-01-01','tilDato':f'{year}-12-31'} if year else None, **kw}
def financial(year=2025,currency='NOK',unit='currency units',scope='exact_legal_entity'):
    return claim('financial_revenue',{'amount':100 if year==2024 else 120,'currency':currency,'account_type':'SELSKAP','unit':unit},year,scope=scope)
def result(claims=(),availability=None):
    return {'claims':list(claims),'availability':availability or {}}
def text(s):return ' '.join(x['text'] for x in s['sentences'])

class PreSubmissionPresentation(unittest.TestCase):
    def test_complete_financial_periods_and_comparison(self):
        cs=[financial(2024),financial()];s=build_synthesis(result(cs))
        self.assertIn('120 NOK',text(s));self.assertIn('2025-12-31',text(s));self.assertIn('difference from 2024: 20',text(s))
        self.assertTrue(all(set(x['claim_ids'])<={c['id'] for c in cs} for x in s['sentences']))
    def test_missing_financial_records_never_infer_poor_performance(self):
        t=text(build_synthesis(result(availability={'financials':'not_found'})))
        self.assertIn('checked source',t);self.assertNotIn('Operating income',t);self.assertNotIn('unprofitable',t)
        self.assertIn('no publishable exact-entity',text(build_synthesis(result(availability={'financials':'available'}))))
    def test_one_period_no_comparison(self):
        t=text(build_synthesis(result([financial()])));self.assertIn('120 NOK',t);self.assertNotIn('difference',t)
    def test_incomparable_currency_scope_unit_no_comparison(self):
        for old in (financial(2024,'EUR'),financial(2024,scope='consolidated_group'),financial(2024,unit='thousands')):
            self.assertNotIn('difference',text(build_synthesis(result([old,financial()]))))
        c=financial();c['value'].pop('unit');self.assertNotIn('difference',text(build_synthesis(result([financial(2024),c]))))
    def test_missing_leadership_no_named_leader(self):
        self.assertNotIn('Registered Chair',text(build_synthesis(result())))
    def test_checked_absence_and_not_researched_are_distinct(self):
        t=text(build_synthesis(result(availability={'roles':'not_found','financials':'not_fetched'})))
        self.assertIn('roles: checked source',t);self.assertIn('financials: not attempted',t);self.assertIn('Not researched in this profile: locations, group',t)
    def test_changed_history_requires_both_evidence_versions(self):
        c=claim('legal_name','Søndre AS');event={'id':'change-1','field':'registry.name','old_value':'Old AS','new_value':'Søndre AS','previous_evidence':{'value':'Old'},'current_evidence':{'value':'New'}}
        s=build_synthesis(result([c]),[event]);self.assertIn('Old AS → Søndre AS',text(s));self.assertEqual(s['sentences'][1]['change_ids'],['change-1'])
        event.pop('previous_evidence');self.assertNotIn('Verified registry.name change',text(build_synthesis(result([c]),[event])))
    def test_unicode_and_supported_roles_locations(self):
        cs=[claim('legal_name','Søndre Øst AS'),claim('registered_role',{'name':'Åse Ødegård','role':'Chair','group':'Board'}),claim('registered_subunit',{'organisation_number':'910608193','name':'Øst branch','address':{'poststed':'BØ'}})]
        t=text(build_synthesis(result(cs)));self.assertIn('Åse Ødegård',t);self.assertIn('BØ',t);self.assertNotIn('record(s)',t)
    def test_no_health_or_growth_inferences_and_finite_amounts(self):
        c=financial();c['value']['amount']=float('nan');t=text(build_synthesis(result([c])))
        self.assertNotIn('nan',t);self.assertNotIn('healthy',t);self.assertNotIn('growing',t)
    def test_viewer_safe_embedding_and_history_structure(self):
        spec=importlib.util.spec_from_file_location('viewer',ROOT/'scripts/build_profile_viewer.py');v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v)
        p={'name':'</script><img src=x onerror=alert(1)> Søndre','organisation_number':'923609016','changes':[]}
        page=v.render([p]);payload=re.search(r'id="profiles">(.*?)</script>',page,re.S).group(1)
        self.assertNotIn('<img',payload);self.assertEqual(json.loads(payload),[p]);self.assertIn('Verified change history',page);self.assertIn("['https:','http:']",page);self.assertIn('url.username||url.password',page);self.assertNotIn('fetch(',page)

if __name__=='__main__':unittest.main()
