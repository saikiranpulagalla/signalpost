"""V0.2 safety regressions. Explicitly exercises the independent 13-case audit.

These tests use only synthetic data. They DO NOT establish official eligibility.
"""
from __future__ import annotations

import copy
import csv
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from v0_kernel.contracts import validate_envelopes, validate_prior_row
from v0_kernel.registry import read_selected_rows, snapshot_checksum
from v0_kernel.runner import run
from v0_kernel.storage import read_jsonl, atomic_jsonl

A, B = "993578843", "923609016"
COLS = ["organisasjonsnummer", "navn", "organisasjonsform.kode", "ignored"]


class TestV02Hardening(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="v02-safety-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.batch = self.root / "input.jsonl"
        self.bulk = self.root / "registry.csv"
        self.out = self.root / "output.jsonl"
        self.report = self.root / "report.json"
        self.batch.write_text("".join(json.dumps({"organisation_number": o}) + "\n" for o in (A, B)))
        self.write_bulk()

    def write_bulk(self, a="Alpha Company AS", b="Beta Company AS", ignored=""):
        with self.bulk.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=COLS)
            writer.writeheader()
            writer.writerow({"organisasjonsnummer": A, "navn": a, "organisasjonsform.kode": "AS", "ignored": ignored})
            writer.writerow({"organisasjonsnummer": B, "navn": b, "organisasjonsform.kode": "AS"})

    def execute(self, **kw):
        return run(organisations=self.batch, bulk=self.bulk, output=self.out,
                   report=self.report, run_id="audit-v02", expected_count=2,
                   fixture_mode=True, checkpoint_every=1, **kw)

    def saved(self):
        return read_jsonl(self.out)

    def verdict(self, changed):
        return validate_envelopes(changed, [A, B],
              source_rows=read_selected_rows(self.bulk, {A, B}),
              source_snapshot_sha256=snapshot_checksum(self.bulk),
              previous_results={})

    def reject_mutation(self, mutation):
        self.assertTrue(self.execute()["validated_outputs"]["passed"])
        rows = self.saved()
        mutation(rows)
        verdict = self.verdict(rows)
        self.assertFalse(verdict["passed"], verdict)

    def test_s01_run_failed_with_verified_claims(self):
        self.reject_mutation(lambda rows: rows[0]["run"].update(terminal_status="failed"))

    def test_s02_identity_failed_but_registry_available(self):
        self.reject_mutation(lambda rows: rows[0]["legal_identity"].update(availability="failed"))

    def test_s03_fake_jobs_available_without_evidence(self):
        self.reject_mutation(lambda rows: rows[0]["availability"].update(jobs="available"))

    def test_s04_published_zero_confidence(self):
        self.reject_mutation(lambda rows: rows[0]["claims"][0].update(confidence=0.0))

    def test_s05_fabricated_first_run_change_without_prior(self):
        def edit(rows):
            claim = rows[0]["claims"][0]
            rows[0]["refresh"]["state"] = "changed"
            rows[0]["changes"] = [{"field": claim["field"], "kind": "added", "old": None, "new": claim["value"]}]
        self.reject_mutation(edit)

    def test_s06_evidence_after_completion(self):
        def edit(rows):
            future = (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat().replace("+00:00", "Z")
            for ev in rows[0]["evidence"]:
                ev["retrieved_at"] = future
            for snap in rows[0]["source_snapshots"]:
                snap["retrieved_at"] = future
        self.reject_mutation(edit)

    def test_s07_orphan_fake_evidence(self):
        def edit(rows):
            fake = copy.deepcopy(rows[0]["evidence"][0])
            fake["id"] = "registry-" + "a" * 24
            fake["claim_span"] = "legal_name: FABRICATED NAME"
            rows[0]["evidence"].append(fake)
        self.reject_mutation(edit)

    def test_s08_whitespace_is_normalized_consistently(self):
        self.write_bulk(a="  Alpha Company AS  ")
        report = self.execute()
        self.assertTrue(report["execution_success"], report["validated_outputs"]["errors"])
        self.assertEqual(self.saved()[0]["legal_identity"]["legal_name"], "Alpha Company AS")

    def test_s09_zero_coverage_is_not_locally_release_eligible(self):
        with self.bulk.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COLS)
            w.writeheader()
        report = run(organisations=self.batch, bulk=self.bulk, output=self.out, report=self.report,
                     run_id="unavailable", expected_count=2, expected_bulk_sha256=snapshot_checksum(self.bulk))
        self.assertTrue(report["structural_gate_passed"])
        self.assertFalse(report["release_gate_passed"])
        self.assertEqual(report["coverage_observed"], 0)
        self.assertFalse(report["official_release_ready"])

    def test_s10_prior_missing_metadata_produces_complete_failed_output(self):
        self.execute()
        previous = self.root / "prior.jsonl"
        rows = self.saved()
        for key in ("run", "operations", "availability", "refresh", "changes", "errors"):
            rows[0].pop(key)
        atomic_jsonl(previous, rows)
        report = self.execute(previous_output=previous)
        self.assertIn("untrusted previous output", report["interruption_or_error"])
        self.assertEqual(len(self.saved()), 2)
        self.assertTrue(all(not row["claims"] for row in self.saved()))
        self.assertFalse(report["execution_success"])

    def test_s11_unrelated_source_changes_preserve_both_snapshot_versions(self):
        self.execute()
        prior = self.root / "prior.jsonl"
        prior.write_bytes(self.out.read_bytes())
        first_sha = self.saved()[0]["source_snapshots"][0]["snapshot_sha256"]
        self.write_bulk(ignored="irrelevant changed field")
        report = self.execute(previous_output=prior)
        self.assertTrue(report["execution_success"], report["validated_outputs"]["errors"])
        row = self.saved()[0]
        self.assertEqual(row["changes"], [])
        self.assertEqual(row["refresh"]["state"], "unchanged")
        self.assertEqual({s["snapshot_sha256"] for s in row["source_snapshots"]},
                         {first_sha, snapshot_checksum(self.bulk)})
        self.assertEqual(len({e["id"] for e in row["evidence"]}), len(row["evidence"]))
        # Replay unchanged snapshot must not keep adding observations.
        new_prior = self.root / "new_prior.jsonl"
        new_prior.write_bytes(self.out.read_bytes())
        again = self.execute(previous_output=new_prior)
        self.assertTrue(again["execution_success"])
        self.assertEqual(len(self.saved()[0]["source_snapshots"]), 2)
        self.assertEqual(self.saved()[0]["changes"], [])

    def test_s12_mutation_of_original_after_pinned_copy_cannot_poison_claims(self):
        original = snapshot_checksum(self.bulk)
        real = read_selected_rows
        def attack(snapshot, wanted):
            # Mutates caller-controlled original path, not our private pinned snapshot.
            self.write_bulk(a="WRONG REPLACED COMPANY AS")
            return real(snapshot, wanted)
        with patch("v0_kernel.runner.read_selected_rows", side_effect=attack):
            report = run(organisations=self.batch, bulk=self.bulk, output=self.out,
                         report=self.report, run_id="race", expected_count=2,
                         expected_bulk_sha256=original)
        self.assertTrue(report["execution_success"], report["validated_outputs"]["errors"])
        self.assertEqual(self.saved()[0]["legal_identity"]["legal_name"], "Alpha Company AS")
        self.assertNotEqual(original, snapshot_checksum(self.bulk))

    def test_s12_mutation_before_copy_fails_pin_and_does_not_publish(self):
        original = snapshot_checksum(self.bulk)
        self.write_bulk(a="WRONG REPLACED COMPANY AS")
        report = run(organisations=self.batch, bulk=self.bulk, output=self.out,
                     report=self.report, run_id="race", expected_count=2,
                     expected_bulk_sha256=original)
        self.assertFalse(report["execution_success"])
        self.assertFalse(report["release_gate_passed"])
        self.assertTrue(all(not r["claims"] for r in self.saved()))

    def test_s12_mutation_of_private_copy_during_parse_fails_closed(self):
        real = read_selected_rows
        def attack(snapshot, wanted):
            snapshot.chmod(0o600)
            snapshot.write_text("organisasjonsnummer,navn\n" + A + ",BAD COMPANY AS\n")
            return real(snapshot, wanted)
        with patch("v0_kernel.runner.read_selected_rows", side_effect=attack):
            report = self.execute()
        self.assertFalse(report["execution_success"])
        self.assertIn("Private source snapshot modified", report["interruption_or_error"])
        self.assertTrue(all(not r["claims"] for r in self.saved()))

    def test_s13_unhashable_prior_field_never_crashes_or_omits_output(self):
        self.execute()
        previous = self.root / "bad_prior.jsonl"
        rows = self.saved()
        rows[0]["claims"][0]["field"] = ["not", "hashable"]
        atomic_jsonl(previous, rows)
        report = self.execute(previous_output=previous)
        self.assertFalse(report["execution_success"])
        self.assertTrue(self.out.exists() and self.report.exists())
        self.assertEqual(len(self.saved()), 2)
        self.assertTrue(all(r["run"]["terminal_status"] == "failed" for r in self.saved()))

    def test_s13_pinned_nonfixture_malformed_prior_still_emits_output(self):
        self.execute()
        prior = self.root / "malformed.jsonl"
        rows = self.saved()
        rows[0]["claims"][0]["field"] = {"bad": "type"}
        atomic_jsonl(prior, rows)
        report = run(organisations=self.batch, bulk=self.bulk, output=self.out,
                     report=self.report, run_id="pinned-bad-history", expected_count=2,
                     expected_bulk_sha256=snapshot_checksum(self.bulk), previous_output=prior,
                     expected_previous_sha256=snapshot_checksum(prior))
        self.assertFalse(report["execution_success"])
        self.assertFalse(report["release_gate_passed"])
        self.assertEqual(len(self.saved()), 2)
        self.assertEqual(report["processed"], 0)
        self.assertIsNotNone(report["interruption_or_error"])

    def test_legacy_v01_prior_can_be_safely_upgraded(self):
        self.execute()
        previous = self.root / "legacy.jsonl"
        rows = self.saved()
        for row in rows:
            for claim in row["claims"]:
                ev = next(e for e in row["evidence"] if e["id"] == claim["evidence_ids"][0])
                key = f"{claim['organisation_number']}|{claim['field']}|{claim['value']}|{ev['content_sha256']}"
                old_id = "registry-" + hashlib.sha256(key.encode()).hexdigest()[:24]
                ev["id"] = old_id
                claim["evidence_ids"] = [old_id]
        atomic_jsonl(previous, rows)
        report = self.execute(previous_output=previous)
        self.assertTrue(report["execution_success"], report["validated_outputs"]["errors"])
        self.assertEqual(len(self.saved()[0]["source_snapshots"]), 1)

    def test_invalid_state_types_are_rejected_not_crashed(self):
        self.execute()
        orig = self.saved()
        invalid = [[], {}, None, 10, True, "incorrect"]
        for value in invalid:
            for section, key in (("run", "terminal_status"), ("availability", "registry"),
                                 ("legal_identity", "availability"), ("refresh", "state"),
                                 ("evidence", "snapshot_sha256")):
                rows = copy.deepcopy(orig)
                if section == "evidence":
                    rows[0][section][0][key] = value
                else:
                    rows[0][section][key] = value
                with self.subTest(section=section, key=key, invalid=repr(value)):
                    verdict = self.verdict(rows)
                    self.assertFalse(verdict["passed"])

    def test_prior_invalid_state_types_rejected_without_exception(self):
        self.execute()
        orig = self.saved()[0]
        for value in ([], {}, None, 10, True):
            row = copy.deepcopy(orig)
            row["run"]["terminal_status"] = value
            with self.assertRaises(ValueError):
                validate_prior_row(row, A)
            row = copy.deepcopy(orig)
            row["availability"]["registry"] = value
            with self.assertRaises(ValueError):
                validate_prior_row(row, A)

    def test_source_failure_with_valid_prior_preserves_evidence_and_completes_output(self):
        self.execute()
        previous = self.root / "previous.jsonl"
        previous.write_bytes(self.out.read_bytes())
        self.bulk.unlink()
        report = self.execute(previous_output=previous)
        self.assertFalse(report["execution_success"])
        self.assertEqual(len(self.saved()), 2)
        self.assertGreater(len(self.saved()[0]["evidence"]), 0)
        self.assertEqual(self.saved()[0]["claims"], [])
        self.assertTrue(report["structural_gate_passed"], report["validated_outputs"]["errors"])

    def test_real_pinned_source_never_claims_official_evaluator_ready(self):
        digest = snapshot_checksum(self.bulk)
        report = run(organisations=self.batch, bulk=self.bulk, output=self.out,
                     report=self.report, run_id="pinned", expected_count=2,
                     expected_bulk_sha256=digest)
        self.assertTrue(report["release_gate_passed"])
        self.assertFalse(report["official_release_ready"])
        self.assertFalse(report["official_schema_verified"])

    def test_nested_json_leaf_type_fuzz_never_raises(self):
        """324+ deterministic corruptions: validator rejects rather than raises.

        This is an exception-safety test; semantic validation of forged values
        is asserted separately in the targeted tests above.
        """
        self.execute()
        rows = self.saved()
        paths = []
        def walk(value, path=(), depth=0):
            if depth > 5:
                return
            if isinstance(value, dict):
                for k, v in value.items():
                    walk(v, path + (k,), depth + 1)
            elif isinstance(value, list):
                for i, v in enumerate(value[:2]):
                    walk(v, path + (i,), depth + 1)
            else:
                paths.append(path)
        walk(rows[0])
        mutated = 0
        for path in paths:
            for replacement in ([], {}, None, True, 7, "bad"):
                test_rows = copy.deepcopy(rows)
                ref = test_rows[0]
                for key in path[:-1]:
                    ref = ref[key]
                ref[path[-1]] = replacement
                mutated += 1
                with self.subTest(path=path, replacement=repr(replacement)):
                    verdict = self.verdict(test_rows)
                    self.assertIsInstance(verdict["passed"], bool)
                    try:
                        validate_prior_row(test_rows[0], A)
                    except ValueError:
                        pass
        self.assertGreaterEqual(mutated, 300)


if __name__ == "__main__":
    unittest.main()
