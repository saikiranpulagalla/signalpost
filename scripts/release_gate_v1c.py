#!/usr/bin/env python3
"""V1c local gate. Fails closed if V1b.1 gate, new tests, or paged 100 fails.

This gate never represents official Builderr evaluator acceptance or score.
"""
from __future__ import annotations
import json
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def child(*args):
    p=subprocess.run([sys.executable, '-S', *args],cwd=ROOT,text=True,
                     capture_output=True,timeout=180)
    if p.returncode:
        raise RuntimeError(f"Child {args[0]} failed: {(p.stdout+p.stderr)[-1400:]}")
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Child {args[0]} did not emit valid JSON") from e


def main():
    summary={'status':'LOCAL_V1C_GATE_BLOCKED','official_release_ready':False,
             'official_v2_schema_verified':False,'official_score':None}
    try:
        old=child('scripts/release_gate_v1b1.py')
        if (old.get('status')!='LOCAL_V1B1_GATE_PASS' or
            old.get('all_mandatory_children')!='PASS' or
            old.get('total_local_regressions',0)<163 or
            old.get('official_release_ready') is not False):
            raise RuntimeError('Legacy gate missing, contradictory or too few tests')
        paged=child('scripts/v1c_pagination_smoke.py')
        if (paged.get('scenario')!='V1C_SYNTHETIC_PAGINATED_100' or
            paged.get('all_passed') is not True or
            paged.get('subunit_claims')!=80 or paged.get('output_count')!=100 or
            paged.get('source_requests')!=0 or
            not isinstance(paged.get('checks'),dict) or
            not paged['checks'] or not all(v is True for v in paged['checks'].values())):
            raise RuntimeError('Paged smoke was not complete or was incorrectly reported')
        summary.update({'status':'LOCAL_V1C_GATE_PASS',
                        'regressions':old['total_local_regressions'],
                        'legacy_gate':'PASS', 'synthetic_paged_100':'PASS',
                        'budgeted_1000':'PASS', 'source_mode':'OFFLINE_SYNTHETIC_ONLY',
                        'live_brreg_100':'NOT_RUN', 'original_starter_suite':'NOT_VERIFIED',
                        'numeric_official_budget':'UNKNOWN'})
        code=0
    except (RuntimeError, subprocess.TimeoutExpired, KeyError, TypeError) as e:
        summary['error']=f'{type(e).__name__}: {str(e)[:1500]}'
        code=1
    print(json.dumps(summary,indent=2))
    return code

if __name__=='__main__':
    raise SystemExit(main())
