"""Offline regression checks for the one authorised five-company BRREG run.

These tests read saved normalized artefacts only; they never initialise a
network transport or replay a source request.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.evaluator_adapter import validate_starter_envelopes

LIVE = ROOT / "out" / "live-five-current"
SANITIZED = ROOT / "tests" / "v2" / "fixtures" / "live-five-sanitized"
EXPECTED_ORGS = ["936252710", "825571922", "932175665", "929508599", "932589060"]


def rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class LiveFiveOfflineReplay(unittest.TestCase):
    def setUp(self):
        self.root = LIVE if (LIVE / "profiles.jsonl").exists() else SANITIZED
        self.private_live = self.root == LIVE
        self.profiles = rows(self.root / "profiles.jsonl")
        self.envelopes = rows(self.root / "envelopes.jsonl")
        self.report = json.loads((self.root / "report.json").read_text(encoding="utf-8"))
        completion = self.root / ("report.json.completion.json" if self.private_live else "completion.json")
        self.completion = json.loads(completion.read_text(encoding="utf-8"))

    def test_saved_lineage_and_claim_evidence_baseline(self):
        self.assertEqual([row["organisation_number"] for row in self.profiles], EXPECTED_ORGS)
        self.assertEqual([row["organisation_number"] for row in self.envelopes], EXPECTED_ORGS)
        claims = [claim for profile in self.profiles for claim in profile["research_result"]["claims"]]
        self.assertEqual(len(claims), 166)
        self.assertEqual(self.report["operations"]["requests"], 25)
        self.assertEqual(self.report["validation"]["passed"], True)
        self.assertEqual(self.completion["envelope_count"], 5)
        self.assertEqual(self.completion["envelope_sha256"], hashlib.sha256((self.root / "envelopes.jsonl").read_bytes()).hexdigest())
        for profile, envelope in zip(self.profiles, self.envelopes):
            self.assertEqual(envelope["profile"], profile)
            evidence = profile["evidence"]
            ids = {claim["id"] for claim in profile["research_result"]["claims"]}
            for claim in profile["research_result"]["claims"]:
                self.assertTrue(claim["evidence_fields"])
                for field in claim["evidence_fields"]:
                    record = evidence[field]
                    self.assertEqual(record["status"], "available")
                    self.assertTrue(record["source_url"])
                    self.assertTrue(record["retrieved_at"])
                    self.assertRegex(record["content_sha256"], r"^[0-9a-f]{64}$")
            for sentence in profile["synthesis"]["sentences"]:
                self.assertTrue(set(sentence["claim_ids"]).issubset(ids))
            self.assertNotIn("website", evidence)
            self.assertNotIn("social", evidence)

    def test_financial_scope_group_404_and_encoding_are_preserved_without_guessing(self):
        all_financial = []
        for profile in self.profiles:
            # Private pilot artefacts retain the normalizer's compact source
            # record list.  The distributable fixture deliberately retains
            # only normalized claims so it cannot disclose raw source bodies.
            # Both representations must prove the same entity/scope/period
            # invariants without treating a missing raw body as missing fact.
            raw_financial = profile["evidence"]["financials"].get("value", {}).get("records")
            financial = raw_financial if raw_financial is not None else [
                {
                    "organisation_number": profile["organisation_number"],
                    "scope": claim["scope"],
                    "account_type": claim["value"]["account_type"],
                    "currency": claim["value"]["currency"],
                    "period": claim["reporting_period"],
                }
                for claim in profile["research_result"]["claims"]
                if claim["field"] == "financial_annual_result"
            ]
            self.assertEqual(len(financial), 3)
            for row in financial:
                self.assertEqual(row["organisation_number"], profile["organisation_number"])
                self.assertEqual(row["scope"], "exact_legal_entity")
                self.assertEqual(row["account_type"], "SELSKAP")
                self.assertEqual(row["currency"], "NOK")
                self.assertIn("fraDato", row["period"])
                self.assertIn("tilDato", row["period"])
                all_financial.append(row)
            group = profile["evidence"]["group"]
            self.assertEqual(group["status"], "not_found")
            self.assertIsNone(group.get("value"))
        self.assertEqual(len(all_financial), 15)
        stored = next(profile for profile in self.profiles if profile["organisation_number"] == "825571922")
        # Raw source bytes were not retained, so this checks lossless storage
        # of the decoded U+00D8 instead of guessing from terminal rendering.
        if self.private_live:
            self.assertEqual(stored["name"], stored["evidence"]["registry"]["value"]["navn"])
        self.assertEqual(stored["name"], "SØNDRE SKOGVEI 63C AS")

    def test_offline_starter_export_preserves_order_and_evidence(self):
        temporary_root = ROOT / ".release-gate-tmp"
        temporary_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary_root) as temporary:
            target = Path(temporary) / "starter.jsonl"
            report = Path(temporary) / "export.json"
            process = subprocess.run([
                sys.executable, "-S", "scripts/export_starter_compatible.py", "--profiles", str(self.root / "profiles.jsonl"),
                "--envelopes", str(self.root / "envelopes.jsonl"), "--output", str(target), "--report", str(report),
            ], cwd=ROOT, text=True, capture_output=True, timeout=20)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            exported = rows(target)
            self.assertEqual([row["organisation_number"] for row in exported], EXPECTED_ORGS)
            self.assertTrue(validate_starter_envelopes(exported, EXPECTED_ORGS)["passed"])
            self.assertEqual(sum(len(row["claims"]) for row in exported), 166)
            self.assertTrue(all(row["evidence"] for row in exported))
            financial = [
                claim for row in exported for claim in row["claims"]
                if claim["field"] == "financial_annual_result"
            ]
            self.assertEqual(len(financial), 15)
            for claim in financial:
                self.assertEqual(claim["value"]["scope"], "exact_legal_entity")
                self.assertEqual(claim["value"]["reporting_period"].keys(), {"fraDato", "tilDato"})

    def test_viewer_is_tied_to_saved_profile_content(self):
        viewer = (self.root / ("company-research-viewer.html" if self.private_live else "viewer.html")).read_text(encoding="utf-8")
        for profile in self.profiles:
            self.assertIn(profile["organisation_number"], viewer)
            self.assertIn(profile["name"], viewer)


if __name__ == "__main__":
    unittest.main()
