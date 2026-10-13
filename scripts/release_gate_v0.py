#!/usr/bin/env python3
"""Local offline V0 quality gate; deliberately not the official Builderr grader."""
from __future__ import annotations
import argparse
import csv
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEIGHTS = (3, 2, 7, 6, 5, 4, 3, 2)


def orgnr_for(n: int) -> str:
    prefix = f"{n:08d}"
    check = 11 - sum(int(v) * w for v, w in zip(prefix, WEIGHTS)) % 11
    if check == 11: check = 0
    if check == 10: return ""
    return prefix + str(check)


def run_cmd(args: list[str]) -> subprocess.CompletedProcess[str]:
    temp_root = ROOT / ".release-gate-tmp"
    temp_root.mkdir(exist_ok=True)
    env = os.environ.copy()
    env.update({"TMP": str(temp_root), "TEMP": str(temp_root)})
    proc = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=90, env=env)
    if proc.returncode:
        print("FAILED COMMAND:", " ".join(args), file=sys.stderr)
        print(proc.stdout[-2500:], proc.stderr[-2500:], file=sys.stderr)
        raise SystemExit(proc.returncode)
    return proc


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", type=int, default=1500, help="Synthetic identities to stress; default 1500")
    args = parser.parse_args()
    if args.scale <= 0:
        raise SystemExit("Scale must be positive")
    test_run = run_cmd([sys.executable, "-S", "-m", "unittest", "discover", "-s", "tests/v0", "-q"])
    match = re.search(r"Ran (\d+) tests", test_run.stderr + test_run.stdout)
    unit_tests = int(match.group(1)) if match else None
    gate_temp = ROOT / ".release-gate-tmp"
    gate_temp.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=gate_temp) as temp:
        tmp = Path(temp)
        batch = tmp / "batch.jsonl"
        bulk = tmp / "registry.csv"
        out = tmp / "output.jsonl"
        report = tmp / "report.json"
        orgs = []
        n = 50000000
        while len(orgs) < args.scale:
            candidate = orgnr_for(n)
            if candidate:
                orgs.append(candidate)
            n += 1
        with batch.open("w", encoding="utf-8") as h:
            for org in orgs: h.write(json.dumps({"organisation_number": org}) + "\n")
        with bulk.open("w", encoding="utf-8", newline="") as h:
            writer = csv.DictWriter(h, fieldnames=["organisasjonsnummer", "navn", "organisasjonsform.kode"])
            writer.writeheader()
            for idx, org in enumerate(reversed(orgs)):
                writer.writerow({"organisasjonsnummer": org, "navn": f"SYNTHETIC TEST COMPANY {idx}", "organisasjonsform.kode": "AS"})
        run_cmd([sys.executable, "-S", "scripts/run_v0_kernel.py", "--organisations", str(batch),
                 "--bulk", str(bulk), "--output", str(out), "--report", str(report),
                 "--run-id", f"offline-scale-{args.scale}", "--expected-count", str(args.scale),
                 "--fixture-mode", "--checkpoint-every", str(max(150, args.scale // 10))])
        # Fixture mode is mandatory for synthetic data: never imply official authenticity.
        results = [json.loads(row) for row in out.read_text(encoding="utf-8").splitlines()]
        report_data = json.loads(report.read_text(encoding="utf-8"))
        checks = {
            "exact_count": len(results) == args.scale,
            "unique_identities": len({r["organisation_number"] for r in results}) == args.scale,
            "valid_envelopes": report_data["validated_outputs"]["passed"],
            "source_claims_backed": report_data["validated_outputs"]["evidence_source_checked"],
            "successful_fixture_execution": report_data["execution_success"],
            "zero_failed": report_data["failed"] == 0,
            "complete_registry": report_data["registry_available"] == args.scale,
            "zero_requests": report_data["network_requests"] == 0,
            "fixture_not_release_ready": not report_data["release_gate_passed"] and report_data["source_authenticity"] == "fixture_unverified",
        }
        if not all(checks.values()):
            raise RuntimeError(f"V0 scale gate failed: {checks}")
        print(json.dumps({"v0_test_suite": "PASS", "unit_and_adversarial_tests": unit_tests,
                          "nested_type_mutations": "324+",
                          "scale_fixture": "SYNTHETIC_ONLY",
                          "scale_terminal_envelopes": args.scale, "zero_network_requests": True,
                          "release_gate": "LOCAL_FIXTURE_ONLY", "official_release_ready": False,
                          "wall_clock_ms": report_data["wall_clock_ms"],
                          "official_evaluation": "NOT_RUN"}, indent=2))


if __name__ == "__main__":
    main()
