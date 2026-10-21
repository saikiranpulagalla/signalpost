"""Sanitized source shapes only; no real pilot bodies, contacts or keys."""
import copy
import json
import re
import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tests/v2')]
import test_live_registered_declarations as fixtures
from norway_company_agent.official import normalize_entity
from norway_company_agent.profile_integrity import verify_source_record


class RegisteredNameHistory(unittest.TestCase):
    def setUp(self):
        self.case=fixtures.LiveRegisteredDeclarations('runTest');self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.case.body['historiskeNavn']=[{'navn':'Tidligere Øst AS','fraDato':'2010-01-02 03:04:05','tilDato':'2020-02-03 04:05:06'}]
    def normalized(self,body=None,observed='2026-10-01T00:00:00Z'):
        return normalize_entity(body or self.case.body,history=True,observed_at=observed)
    def test_literal_intervals_sorted_deduplicated_without_invented_timezone(self):
        self.case.body['historiskeNavn']*=2
        result=self.normalized();self.assertEqual(len(result['registered_name_history']),1)
        row=result['registered_name_history'][0]
        self.assertEqual(row['valid_from'],'2010-01-02 03:04:05')
        self.assertIn('timezone not supplied',row['time_basis'])
        self.assertNotIn('registered_name_history',normalize_entity(self.case.body))
        self.assertNotIn('registered_name_history',self.normalized(observed=None))
    def test_missing_invalid_future_and_cross_entity_intervals_withheld(self):
        original=copy.deepcopy(self.case.body)
        variants=[{'navn':'Former','fraDato':'2020-01-01 00:00:00'},
            {'navn':'Former','fraDato':'2025-01-01 00:00:00','tilDato':'2020-01-01 00:00:00'},
            {'navn':'Former','fraDato':'2020-02-30 00:00:00','tilDato':'2022-01-01 00:00:00'},
            {'navn':'Former','fraDato':'2020-01-01 00:00:00','tilDato':'2026-10-01 00:00:00'},
            {'navn':'Former','fraDato':'2020-01-01 00:00:00','tilDato':'2999-01-01 00:00:00'},
            {'navn':'','fraDato':'2010-01-01 00:00:00','tilDato':'2020-01-01 00:00:00'},
            dict(original['historiskeNavn'][0],organisasjonsnummer='993578843')]
        for item in variants:
            with self.subTest(item=item):
                body=copy.deepcopy(original);body['historiskeNavn']=[item]
                self.assertNotIn('registered_name_history',self.normalized(body))
        body=copy.deepcopy(original);body['organisasjonsnummer']='invalid'
        self.assertNotIn('registered_name_history',self.normalized(body))
    def test_actual_cli_claim_source_citation_viewer_and_hostile_text(self):
        self.case.body['historiskeNavn'][0]['navn']='</script><img src=x onerror=alert(1)> Øst'
        r=self.case.run_cli();self.assertEqual(r.returncode,0,r.stderr)
        p=self.case.profile();self.assertEqual(p['research_result']['normalization_version'],5)
        self.assertTrue(verify_source_record(p,'registry_live',self.case.raw))
        cs=[c for c in p['research_result']['claims'] if c['field']=='registered_name_history'];self.assertEqual(len(cs),1)
        self.assertEqual(cs[0]['source_locator']['pointer'],'/historiskeNavn')
        self.assertEqual(cs[0]['value'][0]['valid_to'],'2020-02-03 04:05:06')
        sentences=[s for s in p['synthesis']['sentences'] if cs[0]['id'] in s['claim_ids']]
        self.assertEqual(len(sentences),1);self.assertIn('not the current legal name',sentences[0]['text'])
        d=self.case.d/'run';html=(d/'viewer.html').read_text(encoding='utf-8')
        self.assertNotIn('</script><img',html)
        self.assertEqual(json.loads(re.search(r'id="profiles">(.*?)</script>',html,re.S).group(1)),[p])
        e=json.loads((d/'envelopes.jsonl').read_text());self.assertEqual(len(e),7)
        self.assertEqual(sum(c['field']=='registered_name_history' for c in e['claims']),1)
        self.assertFalse(e['changes'])
    def test_independent_saved_profile_mutations_rejected_without_overwrite(self):
        self.assertEqual(self.case.run_cli().returncode,0)
        original=self.case.profile();file=self.case.d/'run/profiles.jsonl'
        output=(self.case.d/'run/envelopes.jsonl').read_bytes()
        for kind in ('name','start','end','org','hash','time','claim'):
            p=copy.deepcopy(original);record=p['evidence']['registry_live']
            if kind in ('name','start','end'):
                key={'name':'name','start':'valid_from','end':'valid_to'}[kind]
                record['value']['registered_name_history'][0][key]='FORGED' if kind=='name' else '2011-01-01 00:00:00'
            elif kind=='org':record['value']['organisation_number']='993578843'
            elif kind=='hash':record['content_sha256']='0'*64
            elif kind=='time':record['retrieved_at']='2999-01-01T00:00:00Z'
            else:next(c for c in p['research_result']['claims'] if c['field']=='registered_name_history')['value']=[]
            file.write_text(json.dumps(p),encoding='utf-8')
            r=self.case.run_cli(extra=['--resume']);self.assertNotEqual(r.returncode,0,kind)
            self.assertEqual((self.case.d/'run/envelopes.jsonl').read_bytes(),output)
    def test_zero_call_resume_frozen_and_bootstrap(self):
        for name,bootstrap in (('frozen',False),('bootstrap',True)):
            self.assertEqual(self.case.run_cli(name=name,bootstrap=bootstrap).returncode,0)
            r=self.case.run_cli(name=name,bootstrap=bootstrap,extra=['--resume'])
            self.assertEqual(r.returncode,0,r.stderr);self.assertIn('FIXTURE_SOURCE_STARTS=0',r.stdout)
    def test_authenticated_refresh_same_reordered_and_revised_records(self):
        self.case.body['historiskeNavn'].append({'navn':'Enda eldre AS','fraDato':'2000-01-01 00:00:00','tilDato':'2009-01-01 00:00:00'})
        self.assertEqual(self.case.run_cli().returncode,0)
        args=['--previous-profiles',str(self.case.d/'run/profiles.jsonl'),'--previous-bulk',str(self.case.bulk),
            '--observation-key',str(self.case.d/'run/report.json.observation-auth.key')]
        self.case.body['historiskeNavn'].reverse()
        r=self.case.run_cli('same',extra=args);self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(self.case.profile('same')['changes'],[])
        self.case.body['historiskeNavn'][0]['navn']='Korrigert historisk navn AS'
        r=self.case.run_cli('revised',extra=args);self.assertEqual(r.returncode,0,r.stderr)
        cs=self.case.profile('revised')['changes'];self.assertEqual(len(cs),1)
        self.assertEqual(cs[0]['field'],'registry_live.registered_name_history')
        self.assertTrue(cs[0]['previous_evidence'] and cs[0]['current_evidence'])
    def test_missing_history_is_unknown_not_negative_or_deletion(self):
        self.assertEqual(self.case.run_cli().returncode,0)
        self.case.body.pop('historiskeNavn')
        args=['--previous-profiles',str(self.case.d/'run/profiles.jsonl'),'--previous-bulk',str(self.case.bulk),
            '--observation-key',str(self.case.d/'run/report.json.observation-auth.key')]
        r=self.case.run_cli('missing',extra=args);self.assertEqual(r.returncode,0,r.stderr)
        p=self.case.profile('missing');self.assertEqual(p['changes'],[])
        self.assertTrue(p['historical_observations'])
        self.assertFalse(any(c['field']=='registered_name_history' for c in p['research_result']['claims']))
    def test_legacy_source_projection_remains_authentic_without_retroactive_history(self):
        self.assertEqual(self.case.run_cli().returncode,0)
        p=self.case.profile();p['evidence']['registry_live']['value']=normalize_entity(self.case.body,declarations=True)
        self.assertTrue(verify_source_record(p,'registry_live',self.case.raw))
        self.assertNotIn('registered_name_history',p['evidence']['registry_live']['value'])

if __name__=='__main__':unittest.main()
