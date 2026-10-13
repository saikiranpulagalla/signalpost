#!/usr/bin/env python3
"""V1b.1 fail-closed LOCAL gate; does NOT establish official Builderr readiness."""
from __future__ import annotations
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, '-S', *args], cwd=ROOT,
                          text=True, capture_output=True, timeout=180)


def evaluate_legacy(proc: subprocess.CompletedProcess[str]) -> dict:
    if proc.returncode:
        raise RuntimeError(f'V1a legacy gate exited {proc.returncode}: {proc.stderr[-900:]}')
    try:
        report=json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError('V1a gate did not emit exactly one JSON result') from exc
    if report.get('status') != 'LOCAL_V1A_GATE_PASS':
        raise RuntimeError('V1a gate is not PASS')
    checks=report.get('checks')
    if not isinstance(checks,dict) or not checks or not all(v is True for v in checks.values()):
        raise RuntimeError('A mandatory V1a child check is missing or failed')
    if report.get('v0_tests',0) < 93 or report.get('v1_tests',0) < 19 or report.get('synthetic_companies') != 100:
        raise RuntimeError('V1a gate reported insufficient regression/synthetic coverage')
    if report.get('official_v2_schema_verified') is not False:
        raise RuntimeError('Unverified official schema must never be reported as verified')
    return report


def evaluate_tests(proc: subprocess.CompletedProcess[str], minimum: int = 53) -> int:
    if proc.returncode:
        raise RuntimeError(f'V1b.1 tests failed ({proc.returncode}): {proc.stderr[-1600:]}')
    output=proc.stderr+proc.stdout
    result=re.findall(r'Ran (\d+) tests? in ',output)
    if len(result)!=1 or int(result[0])<minimum or not re.search(r'^OK\s*$',output,re.M):
        raise RuntimeError('V1b.1 test report is incomplete or below minimum')
    if 'FAILED' in output or 'ERROR:' in output:
        raise RuntimeError('Contradictory V1b.1 test output')
    return int(result[0])


def evaluate_scale(proc: subprocess.CompletedProcess[str]) -> dict:
    if proc.returncode:
        raise RuntimeError(f"1,000-company request-accounted scale child failed: {proc.stderr[-800:]}")
    try:
        summary=json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError("Scale check lacks valid JSON") from exc
    if (summary.get('scenario')!='SYNTHETIC_1000_500_REQUESTS' or
            summary.get('all_passed') is not True or
            summary.get('source_requests')!=500 or
            summary.get('run_status')!='completed' or
            not isinstance(summary.get('checks'),dict) or
            not summary['checks'] or not all(x is True for x in summary['checks'].values())):
        raise RuntimeError("Scale child's counter/state/coverage checks are inconsistent")
    return summary


def main() -> int:
    result={"status":"LOCAL_V1B1_GATE_BLOCKED", "official_release_ready":False,
            "official_v2_schema_verified":False,"official_score":None}
    try:
        legacy=evaluate_legacy(run('scripts/release_gate_v1.py'))
        count=evaluate_tests(run('-m','unittest','discover','-s','tests/v1','-q'), minimum=59)
        scale=evaluate_scale(run('scripts/v1b1_scale_smoke.py'))
        result.update({"status":"LOCAL_V1B1_GATE_PASS",
                       "v0_regressions":legacy['v0_tests'],"v1_regressions":count,
                       "total_local_regressions":legacy['v0_tests']+count,
                       "synthetic_100_company_gate":"PASS",
                       "synthetic_v0_scale":"1500/1500",
                       "synthetic_budgeted_scale":"1000/1000; 500 requests",
                       "scale_wall_clock_ms":scale['wall_clock_ms'],
                       "all_mandatory_children":"PASS",
                       "source_mode":"OFFLINE_SYNTHETIC_ONLY",
                       "live_100_company_smoke":"NOT_RUN",
                       "original_starter_full_suite":"NOT_VERIFIED",
                       "official_budget_verified":False})
        code=0
    except (RuntimeError, subprocess.TimeoutExpired, KeyError, TypeError) as exc:
        result['error']=f'{type(exc).__name__}: {str(exc)[:1500]}'
        code=1
    print(json.dumps(result, indent=2))
    return code

if __name__=='__main__':
    raise SystemExit(main())
