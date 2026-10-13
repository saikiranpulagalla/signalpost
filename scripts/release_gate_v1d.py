#!/usr/bin/env python3
"""V1d local release gate: inherited V1c.1 + source-preflight adversarial suite.

A local PASS means offline verification tooling works. It does NOT mean live
BRREG retrieval, original-starter dependencies, official schema or 65+ points.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    report = {
        "status": "LOCAL_V1D_GATE_BLOCKED", "official_release_ready": False,
        "official_v2_schema_verified": False, "official_score": None,
        "genuine_brreg_100": "NOT_RUN", "starter_dependency_suite": "NOT_VERIFIED",
        "registry_frozen_source_available_locally": False,
    }
    try:
        base = subprocess.run([sys.executable, '-S', 'scripts/release_gate_v1c1.py'],
                              cwd=ROOT, text=True, capture_output=True, timeout=240)
        if base.returncode:
            raise RuntimeError('V1c.1 release gate failed: ' + (base.stdout + base.stderr)[-1100:])
        try:
            old = json.loads(base.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError('Inherited gate did not return structured JSON') from exc
        if (old.get('status') != 'LOCAL_V1C1_GATE_PASS' or
            old.get('legacy_v1c') != 'PASS' or
            old.get('v1c1_adversarial') != 'PASS' or
            old.get('synthetic_paged_100') != 'PASS' or
            old.get('budgeted_1000') != 'PASS' or
            old.get('official_release_ready') is not False or
            old.get('regressions', 0) < 197):
            raise RuntimeError('Inherited release gate had insufficient or contradictory results')
        test = subprocess.run([sys.executable, '-S', '-m', 'unittest', 'discover',
                               '-s', 'tests/v1', '-p', 'test_v1d_live_smoke_readiness.py', '-q'],
                              cwd=ROOT, text=True, capture_output=True, timeout=60)
        text = test.stderr + test.stdout
        matches = re.findall(r'Ran (\d+) tests? in ', text)
        if (test.returncode != 0 or matches != ['19'] or
            not re.search(r'^OK\s*$', text, re.MULTILINE) or 'FAILED' in text):
            raise RuntimeError('V1d preflight tests failed/incomplete: ' + text[-1100:])
        report.update({
            "status": "LOCAL_V1D_GATE_PASS", "offline_regression_tests": old['regressions'],
            "v1d_preflight_tests": 19,
            "trusted_universe_hashes": "PINNED_BY_USER_CONTRACT",
            "trusted_registry_hash": "BUNDLED_STARTER_SNAPSHOT_OR_EXPLICIT_DIAGNOSTIC",
            "synthetic_paged_100": "PASS", "budgeted_1000": "PASS",
            "network_traffic_in_gate": 0,
            "next_required": "Run genuine networked 100-company smoke on a machine with BRREG access",
        })
        ret = 0
    except (RuntimeError, subprocess.TimeoutExpired, TypeError, ValueError, KeyError) as exc:
        report['error'] = f'{type(exc).__name__}: {str(exc)[:1500]}'
        ret = 1
    print(json.dumps(report, indent=2))
    return ret


if __name__ == '__main__':
    raise SystemExit(main())
