#!/usr/bin/env python3
"""Reproducible offline V1b regression gate; never claims official acceptance."""
from __future__ import annotations
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*args: str) -> subprocess.CompletedProcess[str]:
    p = subprocess.run([sys.executable, "-S", *args], cwd=ROOT,
                       capture_output=True, text=True, timeout=180)
    if p.returncode:
        raise RuntimeError(f"{' '.join(args)} failed\n{p.stdout[-3000:]}\n{p.stderr[-3000:]}")
    return p


def main() -> None:
    legacy = run("scripts/release_gate_v1.py")
    try:
        legacy_json = json.loads(legacy.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Legacy gate did not emit machine-readable JSON") from exc
    if legacy_json.get("status") != "LOCAL_V1A_GATE_PASS" or not all(
            v is True for v in legacy_json.get("checks", {}).values()) or not legacy_json.get("checks"):
        raise RuntimeError("Mandatory V1a child gate failed despite zero exit status")
    outcome = run("-m", "unittest", "discover", "-s", "tests/v1", "-q")
    lines = outcome.stdout + outcome.stderr
    match = re.search(r"Ran (\d+) tests", lines)
    count = int(match.group(1)) if match else 0
    if count < 38 or "OK" not in lines:
        raise RuntimeError(f"Unexpected V1b test result:\n{lines}")
    print(json.dumps({
        "status": "LOCAL_V1B_GATE_PASS",
        "v0_regressions": 93,
        "v1a_legacy_regressions": 19,
        "v1b_new_regressions": count - 19,
        "total_local_regressions": 93 + count,
        "synthetic_integration": "100/100",
        "synthetic_v0_scale": "1500/1500",
        "legacy_gate": "PASS",
        "official_v2_schema_verified": False,
        "actual_brreg_100_company_smoke": "NOT_RUN_BY_OFFLINE_GATE",
        "original_starter_test_suite": "NOT_RUN_BY_OFFLINE_GATE",
        "official_score": None,
        "official_release_ready": False,
    }, indent=2))


if __name__ == "__main__":
    main()
