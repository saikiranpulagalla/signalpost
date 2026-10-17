"""Zero-request frozen metadata is not website content or financial statements."""
import copy
import csv
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from norway_company_agent.sampling import frozen_registry_details
from norway_company_agent.profile_integrity import verify_claims
from norway_company_agent.completion import verify_completion


class FrozenMetadataRepairs(unittest.TestCase):
    def setUp(self):
        (ROOT/'.release-gate-tmp').mkdir(exist_ok=True)
        spec=importlib.util.spec_from_file_location('metadata_fixtures',ROOT/'tests/v2/test_independent_audit_repairs.py')
        m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
        self.case=m.IndependentAuditRepairs();self.case.setUp();self.addCleanup(self.case.doCleanups)

    def test_actual_offline_cli_publishes_supported_frozen_metadata(self):
        row={'organisasjonsnummer':'923609016','navn':'Verified Name','organisasjonsform.kode':'AS',
            'forretningsadresse.adresse':'["Synthetic road 1"]','forretningsadresse.postnummer':'0123','forretningsadresse.poststed':'OSLO',
            'hjemmeside':'https://example.invalid/','registreringsdatoEnhetsregisteret':'2020-02-01',
            'stiftelsesdato':'2020-01-01','sisteInnsendteAarsregnskap':'2025'}
        with self.case.bulk.open('w',newline='',encoding='utf-8') as h:
            writer=csv.DictWriter(h,fieldnames=list(row));writer.writeheader();writer.writerow(row)
        d=self.case.d
        p=self.case.run_cli(['--viewer-output',str(d/'viewer.html')],financial=False)
        self.assertEqual(p.returncode,0,p.stderr)
        envelope=self.case.rows('output.jsonl')[0]
        claims={c['field']:c for c in envelope['claims']}
        for field in ('registered_address','registered_website','entity_register_registration_date','incorporation_date','registry_latest_filed_year'):
            self.assertIn(field,claims)
            self.assertTrue(claims[field]['evidence_ids'])
        self.assertEqual(claims['registered_address']['value']['postnummer'],'0123')
        self.assertEqual(claims['registry_latest_filed_year']['value'],2025)
        self.assertFalse(any(f.startswith('financial_') or f=='official_website' for f in claims))
        self.assertEqual(envelope['operations']['requests'],0)
        self.assertEqual(json.loads((d/'report.json').read_text())['source_request_policy']['source_requests_started'],0)
        verify_completion(report=d/'report.json',output=d/'output.jsonl',profiles=d/'profiles.jsonl')
        self.assertIn('entity_register_registration_date',(d/'viewer.html').read_text())

    def test_blank_invalid_dates_and_empty_address_do_not_become_facts(self):
        self.assertEqual(frozen_registry_details({'hjemmeside':'','forretningsadresse.adresse':'[]',
            'registreringsdatoEnhetsregisteret':'2020-02-31','stiftelsesdato':'not a date','sisteInnsendteAarsregnskap':'unknown'}),[])

    def test_authenticated_legacy_claim_format_migrates_without_weakening_values(self):
        first=self.case.run_cli();self.assertEqual(first.returncode,0,first.stderr)
        profile=self.case.rows('profiles.jsonl')[0]
        profile['research_result'].pop('normalization_version')
        verify_claims(profile)
        forged=copy.deepcopy(profile)
        next(c for c in forged['research_result']['claims'] if c['field']=='financial_annual_result')['value']['amount']=999
        with self.assertRaises(ValueError):verify_claims(forged)
        (self.case.d/'profiles.jsonl').write_text(json.dumps(profile)+'\n',encoding='utf-8')
        resumed=self.case.run_cli(['--resume']);self.assertEqual(resumed.returncode,0,resumed.stderr)
        self.assertEqual(json.loads((self.case.d/'report.json').read_text())['source_request_policy']['source_requests_started'],0)
        self.assertEqual(self.case.rows('profiles.jsonl')[0]['research_result']['normalization_version'],2)


if __name__=='__main__':unittest.main()
