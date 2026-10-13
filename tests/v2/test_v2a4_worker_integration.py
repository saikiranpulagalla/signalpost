"""Actual-runner subprocess tests for V2 worker failure isolation.

All fault replacement happens in the child interpreter after importing the
runner.  Production command-line options remain unchanged.
"""
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
RUNNER = ROOT / "scripts" / "run_competition_batch.py"
ORGS = ["923609016", "993578843", "910608193"]
sys.path.insert(0, str(ROOT / "src"))

HARNESS = r'''
import importlib.util, sys, time
from pathlib import Path
spec = importlib.util.spec_from_file_location("worker_runner", sys.argv[1])
runner = importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)
scenario, failed, batch, bulk, output, profiles, report, resume, modules = sys.argv[2:11]
failed = set(filter(None, failed.split(",")))
def fake_fetch(org, requested, fetcher=None):
    if scenario == "slow" and org.endswith("843"):
        time.sleep(0.25)
    if org in failed:
        partial = {module: runner.evidence(module, "available", "official_source", "https://data.brreg.no/", value={"partial": True}) for module in requested}
        if scenario == "malformed":
            return partial
        if scenario == "unserializable":
            partial[next(iter(requested))]["value"] = {"bad": object()}
            return partial, []
        if scenario == "unsupported":
            partial[next(iter(requested))]["source_url"] = "not-a-url"
            return partial, []
        if scenario == "unexpected_identity":
            partial["organisation_number"] = "000000000"
            return partial, []
        raise RuntimeError("injected worker failure after partial evidence")
    return ({module: runner.evidence(module, "available", "official_source", "https://data.brreg.no/", value={"org": org, "module": module}) for module in requested}, [])
runner.fetch_official_modules = fake_fetch
if scenario == "persist":
    original = runner.write_jsonl
    final = Path(output)
    def fail_final(path, rows):
        if Path(path) == final:
            raise OSError("injected final output persistence failure")
        return original(path, rows)
    runner.write_jsonl = fail_final
sys.argv = [str(sys.argv[1]), "--organisations", batch, "--bulk", bulk, "--output", output,
    "--profiles-output", profiles, "--report", report, "--run-id", "worker-integration",
    "--expected-count", "3", "--modules", modules, "--workers", "3", "--checkpoint-every", "1"] + (["--resume"] if resume == "1" else [])
sys.argv.extend(["--network-policy", "authorized_public_brreg", "--confirm-authorized-live-sources"])
runner.main()
'''


class WorkerIntegrationV2A4(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.batch, self.bulk = self.root / "batch.txt", self.root / "bulk.csv"
        self.batch.write_text("\n".join(ORGS) + "\n", encoding="utf-8")
        with self.bulk.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["organisasjonsnummer", "navn", "organisasjonsform.kode"])
            writer.writeheader()
            for index, org in enumerate(ORGS):
                writer.writerow({"organisasjonsnummer": org, "navn": f"Company {index}", "organisasjonsform.kode": "AS"})

    def _env(self):
        env = os.environ.copy(); env["TEMP"] = str(self.root); env["TMP"] = str(self.root)
        return env

    def _run(self, scenario="success", failed="", *, resume=False, modules="registry,accounting_obligation,roles"):
        output, profiles, report = self.root / "out.jsonl", self.root / "profiles.jsonl", self.root / "report.json"
        result = subprocess.run([sys.executable, "-S", "-c", textwrap.dedent(HARNESS), str(RUNNER), scenario, failed,
            str(self.batch), str(self.bulk), str(output), str(profiles), str(report), "1" if resume else "0", modules],
            cwd=ROOT, text=True, capture_output=True, env=self._env(), timeout=20)
        return result, output, profiles, report

    def _valid_completed_batch(self, output: Path, report: Path, failed: set[str]):
        envelopes = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual([item["organisation_number"] for item in envelopes], ORGS)
        self.assertEqual(len(envelopes), 3)
        from norway_company_agent.batch import validate_envelopes
        self.assertTrue(validate_envelopes(envelopes, ORGS)["passed"])
        result = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual({item["organisation_number"] for item in result["worker_failures"]}, failed)
        self.assertEqual(result["operations"]["requests"], 0)
        for envelope in envelopes:
            status = envelope["profile"]["evidence"]["roles"]["status"]
            self.assertEqual(status, "source_error" if envelope["organisation_number"] in failed else "available")
        return result

    def test_all_success_slow_and_unexpected_identity_do_not_cross_company_boundary(self):
        for scenario, failed in (("success", ""), ("slow", ""), ("unexpected_identity", ORGS[1])):
            with self.subTest(scenario=scenario):
                result, output, _, report = self._run(scenario, failed)
                expected_exit = 1 if failed else 0
                self.assertEqual(result.returncode, expected_exit, result.stderr + result.stdout)
                self._valid_completed_batch(output, report, set(filter(None, failed.split(","))))

    def test_first_middle_last_and_multiple_exceptions_are_isolated(self):
        for failed in (ORGS[0], ORGS[1], ORGS[2], ",".join((ORGS[0], ORGS[2]))):
            with self.subTest(failed=failed):
                result, output, _, report = self._run("raise", failed)
                self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
                self._valid_completed_batch(output, report, set(failed.split(",")))

    def test_malformed_and_partial_worker_results_are_isolated(self):
        for scenario in ("malformed", "raise"):
            with self.subTest(scenario=scenario):
                result, output, _, report = self._run(scenario, ORGS[1])
                self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
                self._valid_completed_batch(output, report, {ORGS[1]})

    def test_unserializable_worker_value_fails_the_whole_run_without_partial_output(self):
        result, output, profiles, report = self._run("unserializable", ORGS[1])
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(output.exists())
        if profiles.exists():
            # Completion order is intentionally concurrent: an earlier valid
            # checkpoint may survive, but an unserializable first checkpoint
            # may leave no profile artifact. Either result is safe.
            [json.loads(line) for line in profiles.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(json.loads(report.read_text(encoding="utf-8"))["status"], "failed")

    def test_unsupported_evidence_fails_validation_before_publication(self):
        result, output, _, report = self._run("unsupported", ORGS[1])
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(output.exists(), "invalid terminal envelopes must not be published")
        self.assertTrue(report.exists())
        self.assertFalse(json.loads(report.read_text(encoding="utf-8"))["validation"]["passed"])

    def test_final_persistence_failure_is_whole_run_failure(self):
        result, output, profiles, report = self._run("persist")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(output.exists())
        self.assertTrue(profiles.exists())
        self.assertEqual(json.loads(report.read_text(encoding="utf-8"))["status"], "failed")

    def test_worker_exception_during_resume_isolated(self):
        first, output, profiles, report = self._run()
        self.assertEqual(first.returncode, 0, first.stderr)
        result, output, _, report = self._run("raise", ORGS[0], resume=True, modules="registry,accounting_obligation,roles,group")
        self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
        self._valid_completed_batch(output, report, {ORGS[0]})


if __name__ == "__main__":
    unittest.main()
