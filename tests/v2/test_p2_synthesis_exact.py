"""Display the existing finite value, not an unmarked six-digit approximation."""
import copy,re,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'src'))
from norway_company_agent.synthesis import build_synthesis

class ExactFinancialPresentation(unittest.TestCase):
    def test_existing_amount_roundtrips_and_claim_is_unchanged(self):
        for amount in (123456789.75,9007199254740993,0,-0.0,-987654321.125,0.12345678901234568,1e100):
            claim={'id':'claim-source','field':'financial_annual_result','value':{'amount':amount,'currency':'NOK','account_type':'SELSKAP'},'scope':'exact_legal_entity','reporting_period':{'fraDato':'2025-01-01','tilDato':'2025-12-31'}}
            original=copy.deepcopy(claim)
            rows=build_synthesis({'claims':[claim]})['sentences']
            row=next(r for r in rows if r['section']=='Financial position')
            with self.subTest(amount=amount):
                self.assertIn('Annual result: '+str(amount)+' NOK',row['text'])
                self.assertIn('2025-01-01 to 2025-12-31 (SELSKAP, exact legal entity)',row['text'])
                self.assertEqual(row['claim_ids'],['claim-source']);self.assertEqual(claim,original)
    def test_missing_or_invalid_amount_is_not_displayed_as_zero(self):
        for amount in (None,'42',True,float('nan'),float('inf')):
            claim={'id':'x','field':'financial_annual_result','scope':'exact_legal_entity','value':{'amount':amount,'currency':'NOK','account_type':'SELSKAP'},'reporting_period':{'fraDato':'2025-01-01','tilDato':'2025-12-31'}}
            self.assertFalse(any(r['section']=='Financial position' for r in build_synthesis({'claims':[claim]})['sentences']))
