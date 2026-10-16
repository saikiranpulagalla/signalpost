"""Source methods/locators, negative diagnostics and nonmutating preflight."""
import hashlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from norway_company_agent.batch import profiles_from_bulk, terminal_envelope
from norway_company_agent.completion import verify_completion
from norway_company_agent.evidence import evidence
from norway_company_agent.evaluator_adapter import adapt_terminal_envelope, validate_starter_envelopes, starter_availability
from norway_company_agent.official import fetch_official_modules
from norway_company_agent.http import FetchResult
from norway_company_agent.research_result import attach_research_result


class SourcePublicationRepairs(unittest.TestCase):
    def setUp(self):
        (ROOT/'.release-gate-tmp').mkdir(exist_ok=True)
        spec=importlib.util.spec_from_file_location('publication_fixtures',ROOT/'tests/v2/test_independent_audit_repairs.py')
        m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
        self.case=m.IndependentAuditRepairs();self.case.setUp();self.addCleanup(self.case.doCleanups)

    def test_known_methods_and_normalized_locators_are_not_raw_source_quotes(self):
        profile=profiles_from_bulk(self.case.bulk,['923609016'])[0][0]
        body=[{'virksomhet':{'organisasjonsnummer':'923609016'},'regnskapstype':'SELSKAP',
            'regnskapsperiode':{'fraDato':'2025-01-01','tilDato':'2025-12-31'},'valuta':'NOK',
            'resultatregnskapResultat':{'aarsresultat':42}}]
        raw=json.dumps(body).encode()
        records,_=fetch_official_modules('923609016',{'financials'},fetcher=lambda url:FetchResult(url,200,1,len(raw),
            body=body,content_sha256=hashlib.sha256(raw).hexdigest(),retrieved_at='2026-10-01T00:00:00Z'))
        profile['evidence'].update(records);attach_research_result(profile)
        claim=next(c for c in profile['research_result']['claims'] if c['field']=='financial_annual_result')
        self.assertEqual(claim['source_locator']['pointer'],'/value/records/0/annual_result')
        self.assertEqual(profile['evidence']['financials']['value']['records'][0]['annual_result'],claim['value']['amount'])
        row=adapt_terminal_envelope(terminal_envelope(profile,run_id='method',modules=['registry','accounting_obligation','financials'],
            started_at='2026-10-01T00:00:00Z',completed_at='2026-10-01T00:00:01Z'))
        item=next(e for e in row['evidence'] if 'financials' in e['extraction_method']['name'])
        self.assertIn('normalized observation',item['claim_span'])
        self.assertIn('/value/records/0/annual_result',item['claim_span'])
        self.assertEqual(set(row),{'organisation_number','run','claims','evidence','changes','errors','operations'})
        self.assertTrue(validate_starter_envelopes([row],['923609016'])['passed'])
        self.assertIsNone(evidence('financials','available','unknown_external_extractor','https://example.invalid')['extraction_method'])

    def test_checked_negative_and_partial_pagination_remain_inspectable(self):
        profile=profiles_from_bulk(self.case.bulk,['923609016'])[0][0]
        body={'_embedded':{'underenheter':[{'organisasjonsnummer':'910608193','overordnetEnhet':'923609016','navn':'Synthetic branch'}]},'page':{'totalPages':2}}
        raw=json.dumps(body).encode()
        def fetch(url):
            if '/konsernstruktur/' in url:
                return FetchResult(url,404,1,0,error='HTTP 404',content_sha256=hashlib.sha256(b'').hexdigest(),retrieved_at='2026-10-01T00:00:00Z')
            return FetchResult(url,200,1,len(raw),body=body,content_sha256=hashlib.sha256(raw).hexdigest(),retrieved_at='2026-10-01T00:00:00Z')
        records,_=fetch_official_modules('923609016',{'locations','group'},fetcher=fetch)
        profile['evidence'].update(records);attach_research_result(profile)
        row=adapt_terminal_envelope(terminal_envelope(profile,run_id='diagnostics',modules=['registry','accounting_obligation','locations','group'],
            started_at='2026-10-01T00:00:00Z',completed_at='2026-10-01T00:00:01Z'))
        self.assertIn('group: not_available',row['errors'])
        diagnostics=' '.join(row['errors'])
        self.assertIn('remaining pages not checked',diagnostics)
        self.assertIn('https://data.brreg.no/enhetsregisteret/api/konsernstruktur/923609016',diagnostics)
        self.assertIn(hashlib.sha256(b'').hexdigest(),diagnostics)
        self.assertTrue(validate_starter_envelopes([row],['923609016'])['passed'])
        self.assertNotIn('corporate_relationship',{c['field'] for c in row['claims']})

    def test_completed_transaction_unchanged_after_existing_telemetry_refusal(self):
        d=self.case.d
        args=['--request-telemetry',str(d/'telemetry.jsonl')]
        first=self.case.run_cli(args);self.assertEqual(first.returncode,0,first.stderr)
        files=['profiles.jsonl','output.jsonl','report.json','report.json.completion.json','telemetry.jsonl']
        before={f:(d/f).read_bytes() for f in files}
        second=self.case.run_cli([*args,'--resume']);self.assertNotEqual(second.returncode,0)
        self.assertIn('new isolated file',second.stderr)
        self.assertEqual(before,{f:(d/f).read_bytes() for f in files})
        verify_completion(report=d/'report.json',output=d/'output.jsonl',profiles=d/'profiles.jsonl')

    def test_six_availability_meanings_and_unattempted_fail_closed(self):
        mapping={'available':'available','not_found':'not_available','blocked':'blocked','not_applicable':'not_applicable','ambiguous':'ambiguous','source_error':'failed'}
        for old,new in mapping.items():self.assertEqual(starter_availability(old),new)
        for state in ('not_fetched','not_scheduled'):
            with self.assertRaises(ValueError):starter_availability(state)
        records,_=fetch_official_modules('923609016',{'financials'},fetcher=lambda url:FetchResult(url,403,1,0,error='HTTP 403'))
        self.assertEqual(records['financials']['status'],'blocked')


if __name__=='__main__':unittest.main()
