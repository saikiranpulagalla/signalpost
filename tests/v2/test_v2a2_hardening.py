from __future__ import annotations

import copy
import csv
import gzip
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.batch import (canonical_organisation_number, profiles_from_bulk,
                                        terminal_envelope, validate_envelopes)
from norway_company_agent.evidence import evidence
from v0_kernel.storage import atomic_jsonl, read_jsonl


ORG = "923609016"
NOW = "2026-10-09T00:00:00Z"


class V2A2Hardening(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _write_bulk(self, path: Path, *, compressed: bool = False, duplicate: bool = False):
        opener = gzip.open if compressed else open
        with opener(path, "wt", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["organisasjonsnummer", "navn", "organisasjonsform.kode"])
            writer.writeheader()
            writer.writerow({"organisasjonsnummer": ORG, "navn": "Example AS", "organisasjonsform.kode": "AS"})
            if duplicate:
                writer.writerow({"organisasjonsnummer": ORG, "navn": "Other AS", "organisasjonsform.kode": "AS"})

    def test_canonical_organisation_numbers_reject_ambiguous_values(self):
        self.assertEqual(canonical_organisation_number("NO 923 609 016 MVA"), ORG)
        for bad in ("000000000", "923609017", "９２３６０９０１６", "x923609016", 923609016, True, "9236090160"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                canonical_organisation_number(bad)

    def test_plain_and_gzip_bulk_are_equivalent(self):
        plain, packed = self.root / "bulk.csv", self.root / "bulk.gz"
        self._write_bulk(plain)
        self._write_bulk(packed, compressed=True)
        plain_profiles, _ = profiles_from_bulk(plain, [ORG])
        gzip_profiles, _ = profiles_from_bulk(packed, [ORG])
        self.assertEqual(plain_profiles[0]["name"], gzip_profiles[0]["name"])
        self.assertEqual(plain_profiles[0]["organisation_number"], ORG)

    def test_conflicting_requested_duplicate_fails_closed(self):
        path = self.root / "bulk.csv.gz"
        self._write_bulk(path, compressed=True, duplicate=True)
        with self.assertRaisesRegex(ValueError, "Duplicate registry identity"):
            profiles_from_bulk(path, [ORG])

    def test_evaluator_cli_accepts_plain_and_gzip_bulk(self):
        project = ROOT
        batch = self.root / "batch.txt"
        batch.write_text(ORG + "\n", encoding="utf-8")
        for label, compressed in (("plain", False), ("gzip", True)):
            bulk = self.root / f"{label}.csv"
            self._write_bulk(bulk, compressed=compressed)
            output, profiles, report = (self.root / f"{label}-envelopes.jsonl",
                                        self.root / f"{label}-profiles.jsonl",
                                        self.root / f"{label}-report.json")
            process = subprocess.run([
                sys.executable, "-S", "scripts/run_competition_batch.py", "--organisations", str(batch),
                "--bulk", str(bulk), "--profiles-output", str(profiles), "--output", str(output),
                "--report", str(report), "--run-id", f"v2a2-{label}", "--expected-count", "1",
                "--modules", "registry,accounting_obligation", "--workers", "1",
            ], cwd=project, text=True, capture_output=True)
            self.assertEqual(process.returncode, 0, process.stderr + process.stdout)
            result = json.loads(report.read_text(encoding="utf-8"))
            self.assertTrue(result["validation"]["passed"])
            self.assertEqual(result["emitted_envelopes"], 1)

    def test_evaluator_cli_emits_truthful_placeholder_for_missing_frozen_identity(self):
        batch, bulk = self.root / "batch.txt", self.root / "bulk.csv"
        batch.write_text(ORG + "\n993578843\n", encoding="utf-8")
        self._write_bulk(bulk)
        output, profiles, report = (self.root / "envelopes.jsonl", self.root / "profiles.jsonl", self.root / "report.json")
        process = subprocess.run([
            sys.executable, "-S", "scripts/run_competition_batch.py", "--organisations", str(batch),
            "--bulk", str(bulk), "--profiles-output", str(profiles), "--output", str(output),
            "--report", str(report), "--run-id", "v2a2-missing", "--expected-count", "2",
            "--modules", "registry,accounting_obligation", "--workers", "1",
        ], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(process.returncode, 0, process.stderr + process.stdout)
        envelopes = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([item["organisation_number"] for item in envelopes], [ORG, "993578843"])
        self.assertEqual(envelopes[1]["modules"]["registry"]["state"], "source_error")
        self.assertEqual(envelopes[1]["profile"]["name"], None)

    def test_source_request_budget_is_exact_and_never_starts_network_when_zero(self):
        batch, bulk = self.root / "budget-batch.txt", self.root / "budget-bulk.csv"
        batch.write_text(ORG + "\n", encoding="utf-8")
        self._write_bulk(bulk)
        output, profiles, report = (self.root / "budget-envelopes.jsonl", self.root / "budget-profiles.jsonl", self.root / "budget-report.json")
        process = subprocess.run([
            sys.executable, "-S", "scripts/run_competition_batch.py", "--organisations", str(batch),
            "--bulk", str(bulk), "--profiles-output", str(profiles), "--output", str(output),
            "--report", str(report), "--run-id", "v2-budget-zero", "--expected-count", "1",
            "--modules", "registry,accounting_obligation,roles", "--workers", "1",
            "--max-source-requests", "0", "--source-attempts", "1", "--source-timeout-seconds", "1",
        ], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(process.returncode, 0, process.stderr + process.stdout)
        result = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(result["source_request_policy"]["source_requests_started"], 0)
        self.assertEqual(result["operations"]["requests"], 0)
        profile = json.loads(profiles.read_text(encoding="utf-8").splitlines()[0])
        self.assertNotIn("roles", profile["evidence"])
        self.assertEqual(result["execution_plan"]["admission"]["admitted_organisations"]["roles"], [])

    def test_unattempted_source_fails_closed_for_starter_target(self):
        # Global zero is now an intentional omission. Exercise the original
        # invariant with genuinely admitted work stopped before transport starts.
        harness = '''
import importlib.util,sys,socket,urllib.request
def forbidden(*a,**k):raise AssertionError('SOURCE NETWORK FORBIDDEN')
socket.socket.connect=forbidden;socket.create_connection=forbidden;urllib.request.OpenerDirector.open=forbidden
spec=importlib.util.spec_from_file_location('runner',sys.argv[1]);r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
from norway_company_agent.http import FetchResult
def stopped(url,*,on_request_start=None,**options):
 assert callable(on_request_start)
 return FetchResult(url,0,0,0,error='transport service stopped',request_started=False,retrieved_at='2026-10-20T00:00:00Z')
r.fetch_json=stopped;sys.argv=sys.argv[1:];r.main()
'''
        batch, bulk = self.root / "starter-budget-batch.txt", self.root / "starter-budget-bulk.csv"
        batch.write_text(ORG + "\n", encoding="utf-8")
        self._write_bulk(bulk)
        output, profiles, report = (self.root / "starter-budget-envelopes.jsonl", self.root / "starter-budget-profiles.jsonl", self.root / "starter-budget-report.json")
        process = subprocess.run([
            sys.executable, "-S", "-c", harness, str(ROOT / "scripts/run_competition_batch.py"), "--organisations", str(batch),
            "--bulk", str(bulk), "--profiles-output", str(profiles), "--output", str(output),
            "--report", str(report), "--run-id", "starter-budget-zero", "--expected-count", "1",
            "--modules", "registry,accounting_obligation,roles", "--workers", "1",
            "--max-source-requests", "1", "--source-attempts", "1", "--source-timeout-seconds", "1",
            "--network-policy", "authorized_public_brreg", "--confirm-authorized-live-sources",
            "--output-target", "starter_compatible",
        ], cwd=ROOT, text=True, capture_output=True)
        self.assertNotEqual(process.returncode, 0)
        result = json.loads(report.read_text(encoding="utf-8"))
        self.assertFalse(result["validation"]["passed"])
        self.assertFalse(result["validation"]["starter_compatible"]["passed"])
        self.assertIn("not_fetched", result["validation"]["starter_compatible"]["errors"][0])
        self.assertFalse(output.exists(), "unattempted source must not be published as a starter envelope")
        self.assertEqual(result["execution_plan"]["admission"]["admitted_organisations"]["roles"], [ORG])
        self.assertEqual(result["source_request_policy"]["source_requests_started"], 0)

    def test_intentionally_omitted_family_is_absent_and_starter_publishable(self):
        """A zero pre-run family allocation is not a checked-source result.

        The local starter contract has no availability value for intentionally
        unscheduled research, so this uses the documented omission boundary.
        A zero global budget is also a pre-run omission. The stopped-transport
        case above instead selects a role lookup and then cannot start it.
        """
        batch, bulk = self.root / "omitted-batch.txt", self.root / "omitted-bulk.csv"
        batch.write_text(ORG + "\n", encoding="utf-8")
        self._write_bulk(bulk)
        output, profiles, report = (self.root / "omitted-envelopes.jsonl", self.root / "omitted-profiles.jsonl", self.root / "omitted-report.json")
        process = subprocess.run([
            sys.executable, "-S", "scripts/run_competition_batch.py", "--organisations", str(batch),
            "--bulk", str(bulk), "--profiles-output", str(profiles), "--output", str(output),
            "--report", str(report), "--run-id", "starter-omitted-role", "--expected-count", "1",
            "--modules", "registry,accounting_obligation,roles", "--workers", "1",
            "--source-family-budgets", "roles=0", "--output-target", "starter_compatible",
        ], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(process.returncode, 0, process.stderr + process.stdout)
        details = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(details["execution_plan"]["scheduled_optional_modules"], [])
        self.assertEqual(details["execution_plan"]["not_scheduled_optional_modules"], ["roles"])
        self.assertEqual(details["source_request_policy"]["source_requests_started"], 0)
        profile = json.loads(profiles.read_text(encoding="utf-8").splitlines()[0])
        self.assertNotIn("roles", profile["evidence"])
        publication = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(publication["organisation_number"], ORG)
        self.assertTrue(details["validation"]["starter_compatible"]["passed"])

    def test_exact_request_budget_rejects_retrying_configuration(self):
        batch, bulk = self.root / "bad-budget-batch.txt", self.root / "bad-budget-bulk.csv"
        batch.write_text(ORG + "\n", encoding="utf-8")
        self._write_bulk(bulk)
        process = subprocess.run([
            sys.executable, "-S", "scripts/run_competition_batch.py", "--organisations", str(batch),
            "--bulk", str(bulk), "--profiles-output", str(self.root / "bad-profiles.jsonl"),
            "--output", str(self.root / "bad-envelopes.jsonl"), "--report", str(self.root / "bad-report.json"),
            "--run-id", "v2-bad-budget", "--max-source-requests", "1", "--source-attempts", "2",
        ], cwd=ROOT, text=True, capture_output=True)
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("requires --source-attempts 1", process.stderr + process.stdout)

    def test_terminal_serialization_is_pure_and_quarantines_web(self):
        profile = {"organisation_number": ORG, "evidence": {"website": evidence(
            "website", "available", "test", "https://example.invalid/", retrieved_at=NOW,
            value={"content_sha256": "a" * 64, "publishable": True})}}
        original = copy.deepcopy(profile)
        first = terminal_envelope(profile, run_id="v2a2", modules=["website"], started_at=NOW, completed_at=NOW)
        second = terminal_envelope(profile, run_id="v2a2", modules=["website"], started_at=NOW, completed_at=NOW)
        self.assertEqual(profile, original)
        self.assertEqual(first, second)
        self.assertTrue(first["profile"]["evidence"]["website"]["value"]["publication_disabled"])

    def test_strict_validator_rejects_empty_and_accepts_valid_quarantine(self):
        empty = {"organisation_number": ORG, "state": "complete", "modules": {}}
        self.assertFalse(validate_envelopes([empty], [ORG])["passed"])
        profile = {"organisation_number": ORG, "evidence": {"website": evidence(
            "website", "blocked", "legacy_website_crawler_retired", "https://data.brreg.no/",
            retrieved_at=NOW, note="blocked")}}
        valid = terminal_envelope(profile, run_id="v2a2", modules=["website"], started_at=NOW, completed_at=NOW)
        self.assertTrue(validate_envelopes([valid], [ORG])["passed"])
        forged = copy.deepcopy(valid)
        forged["profile"]["evidence"]["website"]["source_url"] = "not-a-url"
        self.assertFalse(validate_envelopes([forged], [ORG])["passed"])

    def test_atomic_replace_failure_preserves_existing_file_and_cleans_temp(self):
        output = self.root / "output.jsonl"
        atomic_jsonl(output, [{"version": "old"}])
        with patch("v0_kernel.storage.os.replace", side_effect=PermissionError("locked")):
            with self.assertRaises(PermissionError):
                atomic_jsonl(output, [{"version": "new"}])
        self.assertEqual(read_jsonl(output), [{"version": "old"}])
        self.assertEqual(list(self.root.glob(".output.jsonl.*.tmp")), [])

    def test_atomic_serialization_failure_preserves_existing_file(self):
        output = self.root / "output.jsonl"
        atomic_jsonl(output, [{"version": "old"}])
        with self.assertRaises(TypeError):
            atomic_jsonl(output, [{"bad": object()}])
        self.assertEqual(read_jsonl(output), [{"version": "old"}])


if __name__ == "__main__":
    unittest.main()
