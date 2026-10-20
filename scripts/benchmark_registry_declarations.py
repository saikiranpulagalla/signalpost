#!/usr/bin/env python3
"""Compare two actual CLI checkouts on identical SYNTHETIC input/virtual timing.

This is an offline experiment entry point, never an official evaluator command.
Source fixtures and guarded transport are the permanent checked-in test harness.
"""
import argparse,csv,importlib.util,json,os,re,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from norway_company_agent.completion import verify_completion
from norway_company_agent.evaluator_adapter import validate_starter_envelopes
from norway_company_agent.coverage_benchmark import evaluate
from norway_company_agent.batch import profiles_from_bulk

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline',type=Path,required=True);p.add_argument('--candidate',type=Path,default=ROOT)
    p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--sizes',default='100,1000,1100,1500');p.add_argument('--budgets',default='300,600,1200,2700,5400')
    a=p.parse_args();out=a.output_dir.resolve()
    if out.exists():p.error('Use a new isolated benchmark output directory')
    out.mkdir(parents=True)
    spec=importlib.util.spec_from_file_location('permanent_offline_transport',a.candidate/'tests/v2/test_admission_configuration_repairs.py')
    f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)
    harness=f.HARNESS.replace("def forbidden(*a,**k):raise AssertionError('EXTERNAL NETWORK START')",
        "external=[]\ndef forbidden(*a,**k):\n external.append(1);raise AssertionError('EXTERNAL NETWORK START')")
    harness=harness.replace("'external_network_starts':0","'external_network_starts':len(external)")
    results=[]
    read=lambda file:[json.loads(s) for s in file.read_text(encoding='utf-8').splitlines() if s.strip()]
    for count in map(int,a.sizes.split(',')):
        identities=f.identities(count);source=out/f'input-{count}';source.mkdir()
        batch=source/'batch.jsonl';bulk=source/'registry.csv'
        batch.write_text(''.join(json.dumps({'organisation_number':org})+'\n' for org in identities))
        with bulk.open('w',newline='',encoding='utf-8') as h:
            w=csv.DictWriter(h,fieldnames=['organisasjonsnummer','navn','organisasjonsform.kode','aktivitet','vedtektsfestetFormaal']);w.writeheader()
            for index,org in enumerate(identities):w.writerow({'organisasjonsnummer':org,'navn':'Synthetic '+org,'organisasjonsform.kode':'AS',
                'aktivitet':'Synthetic registered activity; not observed operations','vedtektsfestetFormaal':'Synthetic legal purpose' if index%20 else ''})
        baselines={p['organisation_number']:p for p in profiles_from_bulk(bulk,identities)[0]}
        for seconds in map(int,a.budgets.split(',')):
            pair={}
            for label,root in (('baseline',a.baseline.resolve()),('candidate',a.candidate.resolve())):
                d=out/f'{count}-{seconds}-{label}';d.mkdir()
                args=['--organisations',str(batch),'--bulk',str(bulk),'--modules','registry,accounting_obligation,financials,roles,locations,registry_live,group',
                    '--source-priority','financials,roles,locations,registry_live,group','--source-allocation-policy','core_breadth',
                    '--source-latency-model','config/brreg-admission-observed-20261013.json','--source-family-budgets','group=0',
                    '--network-policy','authorized_public_brreg','--confirm-authorized-live-sources','--workers','2',
                    '--max-source-requests','7500','--source-timeout-seconds','10','--source-attempts','1',
                    '--max-runtime-seconds',str(seconds),'--finalization-reserve-seconds','120','--output-target','starter_compatible',
                    '--profiles-output',str(d/'profiles.jsonl'),'--output',str(d/'envelopes.jsonl'),'--report',str(d/'report.json'),
                    '--request-telemetry',str(d/'telemetry.jsonl'),'--viewer-output',str(d/'viewer.html'),'--run-id','identical-comparison']
                env=os.environ.copy();env['TEMP']=env['TMP']=str(d)
                start=time.monotonic()
                proc=subprocess.run([sys.executable,'-S','-c',harness,str(root/'scripts/run_competition_batch.py'),'observed-tail',*args],
                    cwd=root,env=env,capture_output=True,text=True,encoding='utf-8',timeout=150)
                wall=time.monotonic()-start
                (d/'stdout.log').write_text(proc.stdout,encoding='utf-8');(d/'stderr.log').write_text(proc.stderr,encoding='utf-8')
                if proc.returncode:raise RuntimeError(f'{label}/{count}/{seconds}: {proc.stderr[-1000:]}')
                measure=json.loads(proc.stdout.split('FIXTURE_MEASUREMENTS=')[-1]);assert measure['external_network_starts']==0
                profiles=read(d/'profiles.jsonl');envelopes=read(d/'envelopes.jsonl');report=json.loads((d/'report.json').read_text())
                assert validate_starter_envelopes(envelopes,identities)['passed']
                verify_completion(report=d/'report.json',output=d/'envelopes.jsonl',profiles=d/'profiles.jsonl')
                assert json.loads(re.search(r'id="profiles">(.*?)</script>',(d/'viewer.html').read_text(encoding='utf-8'),re.S).group(1))==profiles
                coverage=evaluate(profiles,envelopes,identities,evidence_class='virtual_time',baselines=baselines,key=(d/'report.json.observation-auth.key').read_bytes())
                assert coverage['export_missing_facts']==coverage['export_extra_facts']==coverage['invalid_citation_references']==0
                assert coverage['independently_replayable_claims']==coverage['distinct_declared_facts']
                plan=report['execution_plan']['admission']
                pair[label]={'wall_seconds':round(wall,3),'virtual_source_seconds':measure['simulated_source_seconds'],
                    'requests':report['operations']['requests'],'envelopes':len(envelopes),'claims':coverage['distinct_declared_facts'],
                    'business_companies':coverage['company_family_coverage']['registered_business_declarations']['independently_replayable_companies'],
                    'business_facts':coverage['company_family_coverage']['registered_business_declarations']['independently_replayable_facts'],
                    'admitted':{k:len(v) for k,v in plan['admitted_organisations'].items()},
                    'omitted':plan['intentionally_omitted_counts'],'external_requests':0,'publication':'PASS',
                    'binding_citation_viewer_checks':'PASS','output_bytes':(d/'envelopes.jsonl').stat().st_size,
                    'profile_bytes':(d/'profiles.jsonl').stat().st_size,'viewer_bytes':(d/'viewer.html').stat().st_size}
            before,after=pair['baseline'],pair['candidate']
            assert before['admitted']==after['admitted'] and before['requests']==after['requests']
            assert after['business_companies']==count and after['business_facts']==count+count*19//20
            assert after['claims']-before['claims']==after['business_facts']
            result={'companies':count,'hypothetical_budget_seconds':seconds,'evidence_class':'virtual_time','results':pair,
                'incremental_fixture_companies':after['business_companies'],'incremental_physical_requests':0,
                'limitation':'Synthetic CSV declarations; not new real company coverage, official budget or real-network benchmark.'}
            results.append(result);print(json.dumps(result),flush=True)
            (out/'results.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
    print('IDENTICAL_INPUT_COMPARISON=PASS')
if __name__=='__main__':main()
