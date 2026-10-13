"""Actual-runner proof that optional source work is scheduled in batch waves."""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ORGS = ["923609016", "993578843", "910608193"]

HARNESS = r'''
import importlib.util, json, sys
spec=importlib.util.spec_from_file_location("wave_runner",sys.argv[1]); runner=importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)
batch,bulk,out,profiles,report,calls=sys.argv[2:]
def fake(org, modules, fetcher=None):
    with open(calls,"a",encoding="utf-8") as handle:
        handle.write(json.dumps({"org":org,"modules":sorted(modules)})+"\n")
    return ({module:runner.evidence(module,"available","official_source","https://data.brreg.no/",value={"org":org},source_row_key=org) for module in modules}, [])
runner.fetch_official_modules=fake
sys.argv=[sys.argv[1],"--organisations",batch,"--bulk",bulk,"--output",out,"--profiles-output",profiles,"--report",report,
 "--run-id","wave-proof","--modules","registry,accounting_obligation,financials,roles","--workers","2",
 "--network-policy","authorized_public_brreg","--confirm-authorized-live-sources","--source-family-budgets","financials=2,roles=1"]
runner.main()
'''


class BatchWideScheduler(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_admission_is_deterministic_and_optional_calls_are_family_waves(self):
        batch, bulk = self.root / "batch.txt", self.root / "bulk.csv"
        batch.write_text("\n".join(ORGS) + "\n", encoding="utf-8")
        with bulk.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["organisasjonsnummer", "navn", "organisasjonsform.kode"])
            writer.writeheader()
            for index, org in enumerate(ORGS):
                writer.writerow({"organisasjonsnummer": org, "navn": f"Company {index}", "organisasjonsform.kode": "AS"})
        out, profiles, report, calls = (self.root / "out.jsonl", self.root / "profiles.jsonl", self.root / "report.json", self.root / "calls.jsonl")
        env = os.environ.copy(); env["TEMP"] = str(self.root); env["TMP"] = str(self.root)
        result = subprocess.run([sys.executable, "-S", "-c", textwrap.dedent(HARNESS),
                                 str(ROOT / "scripts" / "run_competition_batch.py"), str(batch), str(bulk),
                                 str(out), str(profiles), str(report), str(calls)],
                                cwd=ROOT, env=env, text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        detail = json.loads(report.read_text(encoding="utf-8"))
        plan = detail["execution_plan"]
        self.assertEqual(len(plan["admission"]["admitted_organisations"]["financials"]), 2)
        self.assertEqual(len(plan["admission"]["admitted_organisations"]["roles"]), 1)
        self.assertEqual(plan["admission"]["intentionally_omitted_counts"], {"financials": 1, "roles": 2})
        calls_rows = [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([row["modules"] for row in calls_rows], [["financials"], ["financials"], ["roles"]])
        output = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([row["organisation_number"] for row in output], ORGS)
        self.assertEqual(detail["source_request_policy"]["source_requests_started"], 0)


if __name__ == "__main__":
    unittest.main()
