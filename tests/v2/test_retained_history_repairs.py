"""Persisted history and live projections are independently authenticated."""
import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from norway_company_agent.profile_integrity import receipt, verify_live_baseline, verify_history, sign_history
from norway_company_agent.live_bootstrap import candidate_profiles, resolve_live_identity
from norway_company_agent.http import FetchResult
from norway_company_agent.research_result import attach_research_result
from norway_company_agent.completion import verify_completion


class RetainedHistoryRepairs(unittest.TestCase):
    def setUp(self):
        (ROOT/'.release-gate-tmp').mkdir(exist_ok=True)
        spec=importlib.util.spec_from_file_location('adjacent_fixtures',ROOT/'tests/v2/test_independent_audit_repairs.py')
        self.module=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.module)
        self.case=self.module.IndependentAuditRepairs();self.case.setUp();self.addCleanup(self.case.doCleanups)
        self.d=self.case.d

    def make_history(self):
        old=self.case.run_cli(['--output-target','internal_v2'],financial=False)
        self.assertEqual(old.returncode,0,old.stderr)
        previous=self.d/'previous.jsonl';previous.write_bytes((self.d/'profiles.jsonl').read_bytes())
        old_bulk=self.d/'old.csv';old_bulk.write_bytes(self.case.bulk.read_bytes())
        self.case.write_bulk('Current Authenticated Name')
        args=['--previous-profiles',str(previous),'--previous-bulk',str(old_bulk),'--output-target','internal_v2']
        current=self.case.run_cli(args,financial=False);self.assertEqual(current.returncode,0,current.stderr)
        p=self.case.rows('profiles.jsonl')[0]
        self.assertEqual(len(p['changes']),1)
        return p, self.case.rows('output.jsonl')[0]

    def test_isolated_history_mutations_fail_actual_resume_and_export(self):
        profile,envelope=self.make_history()
        key=self.d/'report.json.observation-auth.key'
        published=(self.d/'output.jsonl').read_bytes()
        def mutate(p,kind):
            c=p['changes'][0]
            if kind=='old':c['old_value']='Forged old name'
            elif kind=='new':c['new_value']='Forged current name'
            elif kind=='org':c['organisation_number']='993578843'
            elif kind=='digest':c['previous_evidence']['content_sha256']='0'*64
            elif kind=='event':c['id']='change-forged'
            elif kind=='missing-old':c.pop('previous_evidence')
            elif kind=='missing-new':c.pop('current_evidence')
            elif kind=='future':c['previous_evidence']['retrieved_at']='2999-01-01T00:00:00Z'
            else:p['changes'].append(copy.deepcopy(c))
        for kind in ('old','new','org','digest','event','missing-old','missing-new','future','duplicate'):
            with self.subTest(kind=kind):
                altered=copy.deepcopy(profile);mutate(altered,kind)
                (self.d/'profiles.jsonl').write_text(json.dumps(altered)+'\n',encoding='utf-8')
                resumed=self.case.run_cli(['--resume','--output-target','internal_v2'],financial=False)
                self.assertNotEqual(resumed.returncode,0,resumed.stdout)
                self.assertEqual((self.d/'output.jsonl').read_bytes(),published)
                incoming=copy.deepcopy(envelope);incoming['profile']=altered
                (self.d/'export-input.jsonl').write_text(json.dumps(incoming)+'\n',encoding='utf-8')
                exported=subprocess.run([sys.executable,'-S','scripts/export_starter_compatible.py',
                    '--profiles',str(self.d/'profiles.jsonl'),'--envelopes',str(self.d/'export-input.jsonl'),
                    '--output',str(self.d/'export.jsonl'),'--bulk',str(self.case.bulk),'--observation-key',str(key)],
                    cwd=ROOT,capture_output=True,text=True,timeout=15)
                self.assertNotEqual(exported.returncode,0,exported.stdout)
                self.assertFalse((self.d/'export.jsonl').exists())

    def test_valid_history_resume_export_and_offline_viewer(self):
        p,e=self.make_history();key=self.d/'report.json.observation-auth.key'
        resumed=self.case.run_cli(['--resume','--output-target','internal_v2'],financial=False)
        self.assertEqual(resumed.returncode,0,resumed.stderr)
        self.assertEqual(json.loads((self.d/'report.json').read_text())['operations']['requests'],0)
        verify_completion(report=self.d/'report.json',output=self.d/'output.jsonl',profiles=self.d/'profiles.jsonl')
        exported=subprocess.run([sys.executable,'-S','scripts/export_starter_compatible.py',
            '--profiles',str(self.d/'profiles.jsonl'),'--envelopes',str(self.d/'output.jsonl'),
            '--output',str(self.d/'export.jsonl'),'--bulk',str(self.case.bulk),'--observation-key',str(key)],
            cwd=ROOT,capture_output=True,text=True,timeout=15)
        self.assertEqual(exported.returncode,0,exported.stderr)
        viewer=subprocess.run([sys.executable,'-S','scripts/build_profile_viewer.py','--profiles',str(self.d/'profiles.jsonl'),
            '--output',str(self.d/'verified-viewer.html'),'--observation-key',str(key)],cwd=ROOT,capture_output=True,text=True,timeout=15)
        self.assertEqual(viewer.returncode,0,viewer.stderr)
        self.assertIn('Current Authenticated Name',(self.d/'verified-viewer.html').read_text(encoding='utf-8'))

    def test_signed_but_structurally_inconsistent_events_are_rejected(self):
        p,_=self.make_history();key=(self.d/'report.json.observation-auth.key').read_bytes()
        for kind in ('value','id','future','duplicate'):
            with self.subTest(kind=kind):
                altered=copy.deepcopy(p)
                if kind=='value':altered['changes'][0]['old_value']='Unsupported'
                elif kind=='id':altered['changes'][0]['id']='wrong'
                elif kind=='future':altered['changes'][0]['previous_evidence']['retrieved_at']='2999-01-01T00:00:00Z'
                else:altered['changes']*=2
                sign_history(altered,key)
                with self.assertRaises(ValueError):verify_history(altered,key)

    def test_live_projection_modifications_and_additions_are_rejected(self):
        body={'organisasjonsnummer':'923609016','navn':'Authenticated Name','organisasjonsform':{'kode':'AS'},
            'naeringskode1':{'kode':'62.010','beskrivelse':'Programming'},'hjemmeside':'https://example.invalid/',
            'forretningsadresse':{'kommune':'OSLO','kommunenummer':'0301','adresse':['Real source value']},
            'konkurs':False,'underAvvikling':False,'antallAnsatte':7,'sisteInnsendteAarsregnskap':'2025'}
        raw=json.dumps(body).encode();profiles,_=candidate_profiles(['923609016'])
        profile,_,verified=resolve_live_identity(profiles[0],lambda url:FetchResult(url,200,1,len(raw),body=body,
            content_sha256=hashlib.sha256(raw).hexdigest(),retrieved_at='2026-10-01T00:00:00Z'))
        self.assertTrue(verified);key=b'k'*32
        for field in ('registry','registry_live','accounting_obligation'):
            profile.setdefault('observation_receipts',{})[field]=receipt(profile,field,key)
        attach_research_result(profile);verify_live_baseline(profile,key)
        values={'industry_code':'99.999','industry_label':'Forged','municipality':'BERGEN','municipality_number':'9999',
            'business_address':{'adresse':['Forged']},'postal_address':{'adresse':['Forged addition']},
            'bankrupt':True,'liquidating':True,'website':'https://forged.invalid/','employees':99,'latest_submitted_accounts':'1999'}
        for field,value in values.items():
            with self.subTest(field=field):
                altered=copy.deepcopy(profile);altered[field]=value;attach_research_result(altered)
                with self.assertRaises(ValueError):verify_live_baseline(altered,key)
        altered=copy.deepcopy(profile);altered['evidence']['registry_live']['value']['website']='https://forged.invalid/'
        with self.assertRaises(ValueError):verify_live_baseline(altered,key)

    def test_failed_refresh_keeps_historical_financials_without_current_claims(self):
        self.assertEqual(self.case.run_cli().returncode,0)
        previous=self.d/'previous.jsonl';previous.write_bytes((self.d/'profiles.jsonl').read_bytes())
        old_bulk=self.d/'old.csv';old_bulk.write_bytes(self.case.bulk.read_bytes())
        prior=json.loads(previous.read_text())['evidence']['financials']
        for status in (503,0,403):
            with self.subTest(status=status):
                error='TimeoutError' if status==0 else 'HTTP '+str(status)
                harness=self.module.HARNESS.replace("org=source_organisation_number(url)",
                    "org=source_organisation_number(url)\n return FetchResult(url,"+str(status)+",30,0,error="+repr(error)+",retrieved_at='2026-10-02T00:00:00Z')")
                args=['--organisations',str(self.case.batch),'--bulk',str(self.case.bulk),
                    '--modules','registry,accounting_obligation,financials','--network-policy','authorized_public_brreg',
                    '--confirm-authorized-live-sources','--source-attempts','1','--max-source-requests','1','--run-id','refresh',
                    '--previous-profiles',str(previous),'--previous-bulk',str(old_bulk),'--verified-source-snapshots',str(self.case.raw),
                    '--profiles-output',str(self.d/'profiles.jsonl'),'--output',str(self.d/'output.jsonl'),
                    '--report',str(self.d/'report.json'),'--viewer-output',str(self.d/'viewer.html'),'--output-target','starter_compatible']
                env=os.environ.copy();env['TEMP']=env['TMP']=str(self.d)
                process=subprocess.run([sys.executable,'-S','-c',harness,str(ROOT/'scripts/run_competition_batch.py'),str(self.case.raw),*args],
                    cwd=ROOT,env=env,capture_output=True,text=True,timeout=20)
                self.assertEqual(process.returncode,0,process.stderr)
                profile=self.case.rows('profiles.jsonl')[0]
                self.assertEqual(profile['previous_evidence']['financials'],prior)
                self.assertEqual(profile['changes'],[])
                self.assertFalse(any(c['field'].startswith('financial_') for c in profile['research_result']['claims']))
                self.assertTrue(any(r['module']=='financials' for r in profile['historical_observations']))
                self.assertIn(prior['content_sha256'],(self.d/'viewer.html').read_text(encoding='utf-8'))
                verify_completion(report=self.d/'report.json',output=self.d/'output.jsonl',profiles=self.d/'profiles.jsonl')


if __name__=='__main__':unittest.main()
