#!/usr/bin/env python3
"""V1c.1 strict local gate; never asserts official Builderr score/readiness."""
from __future__ import annotations
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def child(*cmd: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, '-S', *cmd], cwd=ROOT,
                          text=True, capture_output=True, timeout=240)


def main() -> int:
    report = {'status': 'LOCAL_V1C1_GATE_BLOCKED',
              'official_release_ready': False, 'official_v2_schema_verified': False,
              'official_score': None, 'live_brreg_100': 'NOT_RUN',
              'original_starter_suite': 'NOT_VERIFIED'}
    try:
        inherited = child('scripts/release_gate_v1c.py')
        if inherited.returncode:
            raise RuntimeError('V1c inherited release gate failed: ' + inherited.stderr[-800:])
        try:
            base = json.loads(inherited.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError('V1c child lacks structured JSON result') from exc
        if (base.get('status') != 'LOCAL_V1C_GATE_PASS' or
                base.get('legacy_gate') != 'PASS' or
                base.get('synthetic_paged_100') != 'PASS' or
                base.get('budgeted_1000') != 'PASS' or
                base.get('regressions', 0) < 178 or
                base.get('official_release_ready') is not False):
            raise RuntimeError('V1c mandatory-child mismatch, insufficient tests, or false official-readiness')
        new = child('-m', 'unittest', 'discover', '-s', 'tests/v1',
                    '-p', 'test_v1c1_hardening.py', '-q')
        lines = new.stderr + new.stdout
        matches = re.findall(r'Ran (\d+) tests? in ', lines)
        if (new.returncode or matches != ['15'] or
                not re.search(r'^OK\s*$', lines, re.MULTILINE) or 'FAILED' in lines):
            raise RuntimeError('V1c.1 hardening regressions missing/failed: ' + lines[-1100:])
        report.update({'status': 'LOCAL_V1C1_GATE_PASS', 'regressions': base['regressions'],
                       'new_adversarial_tests': 15, 'source_mode': 'OFFLINE_SYNTHETIC_ONLY',
                       'legacy_v1c': 'PASS', 'v1c1_adversarial': 'PASS',
                       'synthetic_paged_100': 'PASS', 'budgeted_1000': 'PASS',
                       'numeric_official_budget': 'UNKNOWN'})
        status = 0
    except (RuntimeError, subprocess.TimeoutExpired, KeyError, TypeError, ValueError) as exc:
        report['error'] = f'{type(exc).__name__}: {str(exc)[:1500]}'
        status = 1
    print(json.dumps(report, indent=2))
    return status


if __name__ == '__main__':
    raise SystemExit(main())
