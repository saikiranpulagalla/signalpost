#!/usr/bin/env python3
"""V2a local gate. Never signals official Builderr readiness/score."""
from __future__ import annotations
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]


def run(args:list[str], timeout:int=300):
    return subprocess.run(args,cwd=ROOT,text=True,capture_output=True,timeout=timeout)


def main():
    result={'status':'LOCAL_V2A_GATE_BLOCKED','official_release_ready':False,
            'official_v2_schema_verified':False,'official_score':None,
            'real_brreg_100':'NOT_RUN','production_website_crawl':'NOT_RUN'}
    try:
        baseline=run([sys.executable,'-S','scripts/release_gate_v1d.py'])
        if baseline.returncode:
            raise RuntimeError('Inherited gate failed: '+(baseline.stdout+baseline.stderr)[-900:])
        previous=json.loads(baseline.stdout)
        if (previous.get('status')!='LOCAL_V1D_GATE_PASS' or
            previous.get('offline_regression_tests')!=197 or
            previous.get('v1d_preflight_tests')!=19 or
            previous.get('synthetic_paged_100')!='PASS' or
            previous.get('budgeted_1000')!='PASS' or
            previous.get('official_release_ready') is not False):
            raise RuntimeError('Inherited gate status/count contradicted expectations')
        tests=run([sys.executable,'-S','-m','unittest','discover','-s','tests/v2','-p','test_v2a_*.py','-q'],timeout=60)
        out=tests.stdout+tests.stderr
        counts=re.findall(r'Ran (\d+) tests? in ',out)
        if tests.returncode or counts!=['27'] or not re.search(r'^OK\s*$',out,re.M) or 'FAILED' in out:
            raise RuntimeError('V2a adversarial suite missing or failing: '+out[-950:])
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'results.json'
            cli=run([sys.executable,'-S','scripts/v2a_website_fixture_smoke.py','--fixture',
                     'tests/v2/fixtures/website-identity-SYNTHETIC.json','--output',str(p)])
            if cli.returncode:
                raise RuntimeError('Synthetic website fixture smoke failed')
            fixture=json.loads(p.read_text())
            if (fixture['source_mode']!='SYNTHETIC_OFFLINE_FIXTURE' or
                fixture['company_count']!=2 or fixture['website_claims_published']!=0 or
                [r['identity']['status'] for r in fixture['companies']]!=['group_review','exact_page_identity']):
                raise RuntimeError('Fixture verification outputs inconsistent')
        result.update({'status':'LOCAL_V2A_GATE_PASS','inherited_regressions':197,
                       'v1d_preflight_tests':19,'v2a_adversarial_tests':27,
                       'total_local_tests':224, 'synthetic_website_fixture':'PASS',
                       'synthetic_paged_100':'PASS','budgeted_1000':'PASS',
                       'new_website_claims_published':0,'production_network_calls':0})
        code=0
    except (OSError,ValueError,TypeError,KeyError,RuntimeError,subprocess.TimeoutExpired) as exc:
        result['error']=str(exc)[:1200]
        code=1
    print(json.dumps(result,indent=2))
    return code

if __name__=='__main__':raise SystemExit(main())
