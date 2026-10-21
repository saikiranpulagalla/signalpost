from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.evaluator_adapter import starter_availability
from scripts.select_live_verified_pilot import select_candidates

ORG = "923609016"
OTHER = "993578843"

HARNESS = r'''
import importlib.util, sys, json, hashlib
from pathlib import Path
spec=importlib.util.spec_from_file_location("live_runner",sys.argv[1]); runner=importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)
from norway_company_agent.http import FetchResult
scenario,batch,out,profiles,report=sys.argv[2:]
org="923609016"
def fetch(url, timeout=20.0, attempts=1, allow_redirects=True, policy=None):
    assert allow_redirects is False
    assert str(policy) in ("authorized_public_brreg", "NetworkPolicy.AUTHORIZED_PUBLIC_BRREG")
    if url.endswith("/enheter/"+org):
        body={"organisasjonsnummer": ("993578843" if scenario == "wrong" else org), "navn":"Live Example AS", "organisasjonsform":{"kode":"AS"}, "naeringskode1":{"kode":"62.010","beskrivelse":"Programming"}}
        raw=json.dumps(body).encode()
        return FetchResult(url,200,1,len(raw),body=body,raw_bytes=raw,content_sha256=hashlib.sha256(raw).hexdigest(),retrieved_at="2026-10-12T00:00:00Z")
    if url.endswith("/roller"):
        body={"rollegrupper":[]};raw=json.dumps(body).encode()
        return FetchResult(url,200,1,len(raw),body=body,raw_bytes=raw,content_sha256=hashlib.sha256(raw).hexdigest(),retrieved_at="2026-10-12T00:00:01Z")
    raise AssertionError("unexpected downstream URL "+url)
runner.fetch_json=fetch
sys.argv=[sys.argv[1],"--organisations",batch,"--live-bootstrap","--confirm-authorized-live-sources","--network-policy","authorized_public_brreg","--output",out,"--profiles-output",profiles,"--report",report,"--run-id","live-test","--modules","registry,accounting_obligation,registry_live,roles","--workers","1","--max-source-requests","2","--source-attempts","1","--output-target","starter_compatible"]
runner.main()
'''


class LiveVerifiedBootstrap(unittest.TestCase):
    def setUp(self):
        controlled_temp = ROOT / ".release-gate-tmp"
        controlled_temp.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=controlled_temp)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _run(self, scenario: str):
        batch = self.root / "input.jsonl"
        batch.write_text(json.dumps({"organisation_number": ORG, "sample_slice": "live_verified_candidate"}) + "\n", encoding="utf-8")
        output, profiles, report = self.root / "output.jsonl", self.root / "profiles.jsonl", self.root / "report.json"
        env = os.environ.copy(); env["TEMP"] = str(self.root); env["TMP"] = str(self.root)
        process = subprocess.run([sys.executable, "-S", "-c", textwrap.dedent(HARNESS), str(ROOT / "scripts" / "run_competition_batch.py"), scenario, str(batch), str(output), str(profiles), str(report)], cwd=ROOT, env=env, text=True, capture_output=True, timeout=20)
        return process, output, profiles, report

    def test_deterministic_candidate_ranking_is_distinct_and_seeded(self):
        choices = [ORG, OTHER, "910608193", "914778271", "998989544"]
        first = select_candidates(choices, count=3, seed=20261012)
        self.assertEqual(first, select_candidates(choices, count=3, seed=20261012))
        self.assertEqual(len(first), len(set(first)))
        self.assertNotEqual(first, select_candidates(choices, count=3, seed=1))

    def test_live_exact_identity_bootstrap_reaches_starter_adapter(self):
        process, output, profiles, report = self._run("success")
        self.assertEqual(process.returncode, 0, process.stderr + process.stdout)
        profile = json.loads(profiles.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(profile["name"], "Live Example AS")
        self.assertEqual(profile["evidence"]["registry"]["status"], "available")
        publication = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(publication["organisation_number"], ORG)
        self.assertEqual(set(publication), {"organisation_number", "run", "claims", "evidence", "changes", "errors", "operations"})
        details = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(details["identity_mode"], "live_verified")
        self.assertEqual(details["registry"]["selected"], 1)
        self.assertEqual(details["registry"]["missing"], 0)
        self.assertEqual(details["source_request_policy"]["source_requests_started"], 2)
        self.assertTrue(details["validation"]["starter_compatible"]["passed"])

    def test_wrong_live_identity_blocks_downstream_publication(self):
        process, output, profiles, report = self._run("wrong")
        self.assertEqual(process.returncode, 0, process.stderr + process.stdout)
        profile = json.loads(profiles.read_text(encoding="utf-8").splitlines()[0])
        self.assertIsNone(profile["name"])
        self.assertEqual(profile["evidence"]["registry"]["status"], "source_error")
        self.assertEqual(profile["evidence"]["roles"]["status"], "blocked")
        self.assertEqual(json.loads(report.read_text(encoding="utf-8"))["source_request_policy"]["source_requests_started"], 1)
        self.assertEqual(json.loads(report.read_text(encoding="utf-8"))["registry"]["failed"], 1)
        publication = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(publication["organisation_number"], ORG)
        self.assertFalse(publication["claims"])

    def test_six_starter_availability_states_have_explicit_mapping(self):
        self.assertEqual({starter_availability(value) for value in ("available", "not_found", "blocked", "not_applicable", "ambiguous", "source_error")},
                         {"available", "not_available", "blocked", "not_applicable", "ambiguous", "failed"})
        with self.assertRaises(ValueError):
            starter_availability("not_fetched")
        with self.assertRaises(ValueError):
            starter_availability("complete")

    def test_live_wrapper_refuses_without_explicit_authorization(self):
        batch = self.root / "input.jsonl"; batch.write_text(json.dumps({"organisation_number": ORG}) + "\n", encoding="utf-8")
        process = subprocess.run([sys.executable, "-S", "scripts/run_live_smoke.py", "--identity-mode", "live_verified", "--organisations", str(batch), "--output-dir", str(self.root / "out"), "--run-id", "denied"], cwd=ROOT, text=True, capture_output=True, timeout=10)
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("Refusing live requests", process.stderr + process.stdout)

    def test_runner_refuses_live_bootstrap_without_explicit_authorization(self):
        batch = self.root / "input.jsonl"; batch.write_text(json.dumps({"organisation_number": ORG}) + "\n", encoding="utf-8")
        process = subprocess.run([sys.executable, "-S", "scripts/run_competition_batch.py", "--organisations", str(batch),
                                  "--live-bootstrap", "--output", str(self.root / "output.jsonl"),
                                  "--profiles-output", str(self.root / "profiles.jsonl"), "--report", str(self.root / "report.json"),
                                  "--run-id", "denied-direct", "--modules", "registry,accounting_obligation,registry_live"],
                                 cwd=ROOT, text=True, capture_output=True, timeout=10)
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("Refusing live requests", process.stderr + process.stdout)


if __name__ == "__main__":
    unittest.main()
