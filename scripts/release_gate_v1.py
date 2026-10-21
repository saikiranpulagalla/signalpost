#!/usr/bin/env python3
"""V1a local fixture gate. Never claims official score/readiness."""
from __future__ import annotations
import csv
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from v0_kernel.storage import read_jsonl
WEIGHTS = (3, 2, 7, 6, 5, 4, 3, 2)


def orgnr(n: int) -> str | None:
    digits = f"{n:08d}"
    chk = 11 - sum(int(v) * w for v, w in zip(digits, WEIGHTS)) % 11
    chk = 0 if chk == 11 else chk
    return digits + str(chk) if chk != 10 else None


def command(*args: str) -> subprocess.CompletedProcess[str]:
    outcome = subprocess.run([sys.executable, "-S", *args], cwd=ROOT,
                             text=True, capture_output=True, timeout=120)
    if outcome.returncode:
        raise RuntimeError(f"Failed command: {' '.join(args)}\n{outcome.stdout[-2400:]}\n{outcome.stderr[-2400:]}")
    return outcome


def main() -> None:
    v0 = command("scripts/release_gate_v0.py", "--scale", "1500")
    # Pin the legacy V1a regression set. New tests are executed separately by
    # release_gate_v1b.py; broad discovery would invalidate V1a's fixed count.
    tests = command("-m", "unittest", "discover", "-s", "tests/v1",
                    "-p", "test_official_integration.py", "-q")
    if "Ran 19 tests" not in (tests.stderr + tests.stdout):
        raise RuntimeError("Unexpected V1 regression test count")
    with tempfile.TemporaryDirectory(prefix="signalpost-v1-gate-") as work:
        folder = Path(work)
        source = folder / "registry.csv"
        batch = folder / "organisations.jsonl"
        fixture_path = folder / "official.json"
        output = folder / "output.jsonl"
        report_path = folder / "report.json"
        identities = []
        n = 60000000
        while len(identities) < 100:
            identifier = orgnr(n)
            if identifier:
                identities.append(identifier)
            n += 1
        with batch.open("w", encoding="utf-8") as stream:
            for identifier in identities:
                stream.write(json.dumps({"organisation_number": identifier}) + "\n")
        with source.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["organisasjonsnummer", "navn", "organisasjonsform.kode"])
            writer.writeheader()
            for i, identifier in enumerate(identities):
                writer.writerow({"organisasjonsnummer": identifier,
                                 "navn": f"SYNTHETIC NORWEGIAN COMPANY {i} AS", "organisasjonsform.kode": "AS"})
        payload = {}
        for i, identifier in enumerate(identities):
            payload[identifier] = {
                "registry_live": {"status": 200, "body": {
                    "organisasjonsnummer": identifier, "navn": f"SYNTHETIC NORWEGIAN COMPANY {i} AS"}},
                "roles": {"status": 200, "body": {"rollegrupper": []}},
                "locations": {"status": 200, "body": {"_embedded": {"underenheter": []}}},
                "financials": {"status": 200, "body": []},
            }
        fixture_path.write_text(json.dumps(payload), encoding="utf-8")
        run = command("scripts/run_v1_official.py", "--organisations", str(batch),
                      "--bulk", str(source), "--fixture-responses", str(fixture_path),
                      "--fixture-mode", "--output", str(output), "--report", str(report_path),
                      "--run-id", "synthetic-only-release-v1a", "--expected-count", "100")
        data = json.loads(report_path.read_text())
        rows = read_jsonl(output)
        checks = {
            "v0_offline_passed": '"v0_test_suite": "PASS"' in v0.stdout,
            "v1_tests_passed": "OK" in tests.stderr,
            "synthetic_100_complete": len(rows) == 100 and len({x['organisation_number'] for x in rows}) == 100,
            "source_claims_verified": data["validation"]["passed"],
            "runner_finished": data["run_status"] == "completed",
            "no_network_calls": data["source_requests"] == 0,
            "no_official_release_claim": data["official_release_ready"] is False,
            "all_registry_current_available": all(x["availability"]["registry_live"] == "available" for x in rows),
        }
        if not all(checks.values()):
            raise RuntimeError(f"V1 fixture gate failed: {checks}")
        print(json.dumps({"status": "LOCAL_V1A_GATE_PASS", "checks": checks,
                          "v0_tests": 93, "v1_tests": 19,
                          "synthetic_companies": 100, "network_requests": 0,
                          "official_v2_schema_verified": False,
                          "real_100_company_smoke": "NOT_RUN",
                          "official_score": None}, indent=2))

if __name__ == "__main__":
    main()
