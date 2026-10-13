from __future__ import annotations

import csv
import gzip
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from v0_kernel.contracts import parse_batch, is_valid_orgnr, validate_envelopes
from v0_kernel.registry import row_hash, registry_facts, read_selected_rows
from v0_kernel.runner import canonical_changes, placeholder, run
from v0_kernel.storage import read_jsonl, atomic_jsonl

ORG1 = "993578843"
ORG2 = "923609016"
ORG3 = "979543883"
FIELDS = ["organisasjonsnummer", "navn", "organisasjonsform.kode", "naeringskode1.kode",
          "forretningsadresse.kommune", "sisteInnsendteAarsregnskap"]


class TestKernel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.input = self.folder / "input.jsonl"
        self.bulk = self.folder / "registry.csv"
        self.out = self.folder / "output.jsonl"
        self.report = self.folder / "report.json"
        self.write_inputs([ORG1, ORG2])
        self.write_bulk([
            {"organisasjonsnummer": ORG1, "navn": "Eksempel AS", "organisasjonsform.kode": "AS",
             "naeringskode1.kode": "62.010", "forretningsadresse.kommune": "OSLO", "sisteInnsendteAarsregnskap": "2025"},
            {"organisasjonsnummer": ORG2, "navn": "Test Økonomi AS", "organisasjonsform.kode": "AS",
             "naeringskode1.kode": "62.010", "forretningsadresse.kommune": "TRONDHEIM"},
        ])

    def write_inputs(self, orgs):
        self.input.write_text("".join(json.dumps({"organisation_number": x}) + "\n" for x in orgs), encoding="utf-8")

    def write_bulk(self, rows):
        with self.bulk.open("w", encoding="utf-8", newline="") as f:
            wr = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
            wr.writeheader()
            for r in rows:
                wr.writerow(r)

    def execute(self, **kwargs):
        return run(organisations=self.input, bulk=self.bulk, output=self.out,
                   report=self.report, run_id="test-v0", expected_count=2,
                   checkpoint_every=1, fixture_mode=True, **kwargs)

    def test_checksum_three_valid(self):
        self.assertTrue(all(is_valid_orgnr(v) for v in (ORG1, ORG2, ORG3)))

    def test_checksum_rejects_invalid_and_coercion(self):
        for bad in ["123456789", "000000001", "99357884", "993578843 ", "99-3578843", None, 993578843, "993578843\n"]:
            self.assertFalse(is_valid_orgnr(bad), bad)

    def test_all_nine_digit_alterations_fail_except_real_check_digit(self):
        for new_digit in "0123456789":
            altered = ORG1[:8] + new_digit
            self.assertEqual(is_valid_orgnr(altered), new_digit == ORG1[8])

    def test_jsonl_json_text_inputs(self):
        self.assertEqual(parse_batch(self.input), [ORG1, ORG2])
        for ext, data in [(".txt", ORG1 + "\n" + ORG2 + "\n"),
                          (".json", json.dumps({"organisation_numbers": [ORG1, ORG2]}))]:
            path = self.folder / ("input" + ext)
            path.write_text(data, encoding="utf-8")
            self.assertEqual(parse_batch(path), [ORG1, ORG2])

    def test_input_duplicate_detected(self):
        self.write_inputs([ORG1, ORG1])
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.execute()

    def test_invalid_input_no_silent_sanitization(self):
        self.write_inputs([ORG1, "9-93578843"])
        with self.assertRaisesRegex(ValueError, "Invalid"):
            self.execute()

    def test_count_mismatch_is_hard_error(self):
        self.write_inputs([ORG1])
        with self.assertRaisesRegex(ValueError, "Expected 2"):
            self.execute()

    def test_valid_two_envelopes_with_exact_identity(self):
        report = self.execute()
        rows = read_jsonl(self.out)
        self.assertTrue(report["validated_outputs"]["passed"])
        self.assertEqual([r["organisation_number"] for r in rows], [ORG1, ORG2])
        self.assertEqual(rows[0]["legal_identity"]["legal_name"], "Eksempel AS")
        self.assertEqual(rows[0]["availability"]["registry"], "available")
        self.assertEqual(report["network_requests"], 0)
        self.assertEqual(report["declared_external_cost_usd"], 0)
        self.assertEqual(len(rows[0]["claims"]), 5)
        self.assertEqual(len(rows[0]["evidence"]), len(rows[0]["claims"]))
        self.assertIsNone(report["official_score"])

    def test_no_fabricated_financial_number(self):
        self.execute()
        row = read_jsonl(self.out)[0]
        self.assertEqual(next(c["value"] for c in row["claims"] if c["field"] == "last_submitted_accounts_year"), "2025")
        self.assertFalse(any(c["field"] in {"revenue", "profit", "assets"} for c in row["claims"]))

    def test_missing_registry_row_keeps_terminal_envelope(self):
        self.write_bulk([{"organisasjonsnummer": ORG1, "navn": "Eksempel AS"}])
        result = self.execute()
        rows = read_jsonl(self.out)
        self.assertTrue(result["validated_outputs"]["passed"])
        self.assertEqual(rows[1]["availability"]["registry"], "not_available")
        self.assertEqual(rows[1]["claims"], [])
        self.assertTrue(rows[1]["errors"])

    def test_prior_evidence_survives_source_disappearance(self):
        self.execute()
        old_path = self.folder / "old.jsonl"
        old_path.write_bytes(self.out.read_bytes())
        self.write_bulk([{"organisasjonsnummer": ORG2, "navn": "Test Økonomi AS"}])
        self.execute(previous_output=old_path)
        first = read_jsonl(self.out)[0]
        self.assertEqual(first["claims"], [])
        self.assertGreater(len(first["evidence"]), 0)
        self.assertGreater(len(first["source_snapshots"]), 0)
        self.assertEqual(first["refresh"]["state"], "source_unavailable")

    def test_prior_evidence_survives_source_read_error(self):
        self.execute()
        old_path = self.folder / "old.jsonl"
        old_path.write_bytes(self.out.read_bytes())
        self.bulk.unlink()
        self.execute(previous_output=old_path)
        first = read_jsonl(self.out)[0]
        self.assertEqual(first["availability"]["registry"], "failed")
        self.assertGreater(len(first["evidence"]), 0)
        self.assertGreater(len(first["source_snapshots"]), 0)

    def test_missing_legal_name_withholds_other_claims(self):
        self.write_bulk([{"organisasjonsnummer": ORG1, "organisasjonsform.kode": "AS"},
                         {"organisasjonsnummer": ORG2, "navn": "Test Økonomi AS"}])
        self.execute()
        row = read_jsonl(self.out)[0]
        self.assertEqual(row["legal_identity"]["availability"], "ambiguous")
        self.assertEqual(row["claims"], [])

    def test_blank_field_does_not_become_zero(self):
        self.write_bulk([{"organisasjonsnummer": ORG1, "navn": "X", "naeringskode1.kode": ""},
                         {"organisasjonsnummer": ORG2, "navn": "Y"}])
        self.execute()
        claims = read_jsonl(self.out)[0]["claims"]
        self.assertNotIn("industry_code", {c["field"] for c in claims})

    def test_duplicate_registry_row_after_last_requested_still_detected(self):
        self.write_bulk([{"organisasjonsnummer": ORG1, "navn": "X"},
                         {"organisasjonsnummer": ORG2, "navn": "Y"},
                         {"organisasjonsnummer": ORG1, "navn": "Z"}])
        result = self.execute()
        self.assertIn("Duplicate registry identity", result["interruption_or_error"])

    def test_gzip_bulk_even_if_named_dot_csv(self):
        raw = self.bulk.read_bytes()
        self.bulk.write_bytes(gzip.compress(raw))
        result = self.execute()
        self.assertIsNone(result["interruption_or_error"])
        self.assertEqual(result["registry_available"], 2)

    def test_semicolon_delimited_bulk(self):
        with self.bulk.open("w", encoding="utf-8", newline="") as f:
            wr = csv.DictWriter(f, fieldnames=FIELDS, delimiter=";")
            wr.writeheader()
            wr.writerow({"organisasjonsnummer": ORG1, "navn": "Semikolon, AS"})
            wr.writerow({"organisasjonsnummer": ORG2, "navn": "Semicolon AS"})
        result = self.execute()
        self.assertEqual(result["registry_available"], 2)
        self.assertEqual(read_jsonl(self.out)[0]["legal_identity"]["legal_name"], "Semikolon, AS")

    def test_input_output_collision_cannot_destroy_input(self):
        before = self.input.read_bytes()
        with self.assertRaisesRegex(ValueError, "all inputs"):
            run(organisations=self.input, bulk=self.bulk, output=self.input, report=self.report,
                run_id="x", expected_count=2)
        self.assertEqual(self.input.read_bytes(), before)

    def test_report_bulk_collision_cannot_destroy_registry(self):
        before = self.bulk.read_bytes()
        with self.assertRaisesRegex(ValueError, "all inputs"):
            run(organisations=self.input, bulk=self.bulk, output=self.out, report=self.bulk,
                run_id="x", expected_count=2)
        self.assertEqual(self.bulk.read_bytes(), before)

    def test_registry_missing_file_still_writes_complete_failed_output(self):
        self.bulk.unlink()
        result = self.execute()
        self.assertTrue(result["validated_outputs"]["passed"])
        self.assertIsNotNone(result["interruption_or_error"])
        self.assertEqual(len(read_jsonl(self.out)), 2)
        self.assertTrue(all(r["availability"]["registry"] == "failed" for r in read_jsonl(self.out)))

    def test_corrupt_registry_schema_still_writes_complete_failed_output(self):
        self.bulk.write_text("garbage,headers\nhello,world\n", encoding="utf-8")
        result = self.execute()
        self.assertIn("missing organisation-number", result["interruption_or_error"])
        self.assertEqual(len(read_jsonl(self.out)), 2)

    def test_duplicate_registry_rows_fail_closed(self):
        self.write_bulk([{"organisasjonsnummer": ORG1, "navn": "X"},
                         {"organisasjonsnummer": ORG1, "navn": "Y"},
                         {"organisasjonsnummer": ORG2, "navn": "Z"}])
        result = self.execute()
        self.assertIn("Duplicate registry identity", result["interruption_or_error"])
        self.assertEqual(len(read_jsonl(self.out)), 2)

    def test_first_checkpoint_all_failed_placeholders_then_complete(self):
        snapshots = []
        self.execute(on_checkpoint=lambda rows: snapshots.append(json.loads(json.dumps(rows))))
        self.assertEqual(len(snapshots[0]), 2)
        self.assertTrue(all(r["availability"]["registry"] == "failed" for r in snapshots[0]))
        self.assertTrue(all(r["availability"]["registry"] == "available" for r in snapshots[-1]))

    def test_test_interruption_does_not_truncate_output(self):
        result = self.execute(stop_after=1)
        rows = read_jsonl(self.out)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["availability"]["registry"], "available")
        self.assertEqual(rows[1]["availability"]["registry"], "failed")
        self.assertTrue(result["validated_outputs"]["passed"])
        self.assertTrue(result["interruption_or_error"])

    def test_atomic_write_failure_leaves_previous_artifact(self):
        initial = [{"ok": "old"}]
        atomic_jsonl(self.out, initial)
        with self.assertRaises(TypeError):
            atomic_jsonl(self.out, [{"bad": object()}])
        self.assertEqual(read_jsonl(self.out), initial)

    def test_same_frozen_source_same_claims_and_ids(self):
        self.execute()
        first = read_jsonl(self.out)
        self.execute()
        second = read_jsonl(self.out)
        for a, b in zip(first, second):
            self.assertEqual(a["claims"], b["claims"])
            self.assertEqual({x["id"] for x in a["evidence"]}, {x["id"] for x in b["evidence"]})

    def test_same_snapshot_refresh_is_idempotent(self):
        self.execute()
        old_path = self.folder / "old.jsonl"
        old_path.write_bytes(self.out.read_bytes())
        self.execute(previous_output=old_path)
        self.assertEqual([r["changes"] for r in read_jsonl(self.out)], [[], []])
        self.assertEqual([r["refresh"]["state"] for r in read_jsonl(self.out)], ["unchanged", "unchanged"])
        self.assertEqual([len(r["source_snapshots"]) for r in read_jsonl(self.out)], [1, 1])

    def test_changed_registry_name_preserves_history(self):
        self.execute()
        old_path = self.folder / "old.jsonl"
        old_path.write_bytes(self.out.read_bytes())
        self.write_bulk([{"organisasjonsnummer": ORG1, "navn": "Ny Eksempel AS"},
                         {"organisasjonsnummer": ORG2, "navn": "Test Økonomi AS", "organisasjonsform.kode": "AS",
                          "naeringskode1.kode": "62.010", "forretningsadresse.kommune": "TRONDHEIM"}])
        self.execute(previous_output=old_path)
        rows = read_jsonl(self.out)
        self.assertIn({"field": "legal_name", "kind": "changed", "old": "Eksempel AS", "new": "Ny Eksempel AS"}, rows[0]["changes"])
        self.assertGreaterEqual(len(rows[0]["source_snapshots"]), 2)
        self.assertGreater(len(rows[0]["evidence"]), len(rows[0]["claims"]))

    def test_no_false_deletion_for_missing_field(self):
        before = [{"field": "legal_name", "value": "X", "availability": "available"},
                  {"field": "industry_code", "value": "1", "availability": "available"}]
        prior = {"legal_identity": {"availability": "available"}, "claims": before}
        changes, status = canonical_changes(prior, [before[0]])
        self.assertEqual(changes, [])
        self.assertEqual(status, "unchanged")

    def test_invalid_previous_duplicate_fails_before_overwrite(self):
        self.execute()
        prior_path = self.folder / "duplicate.jsonl"
        r = read_jsonl(self.out)[0]
        atomic_jsonl(prior_path, [r, r])
        result = self.execute(previous_output=prior_path)
        self.assertIn("duplicate organisation", result["interruption_or_error"])
        self.assertFalse(result["execution_success"])
        self.assertEqual(len(read_jsonl(self.out)), 2)
        self.assertTrue(all(r["run"]["terminal_status"] == "failed" for r in read_jsonl(self.out)))

    def test_collision_output_report_rejected(self):
        with self.assertRaisesRegex(ValueError, "must differ"):
            run(organisations=self.input, bulk=self.bulk, output=self.out, report=self.out,
                run_id="test", expected_count=2)

    def test_missing_evidence_ref_rejected(self):
        self.execute()
        rows = read_jsonl(self.out)
        rows[0]["claims"][0]["evidence_ids"] = ["no-such-evidence"]
        verdict = validate_envelopes(rows, [ORG1, ORG2])
        self.assertFalse(verdict["passed"])

    def test_wrong_claim_entity_rejected(self):
        self.execute()
        rows = read_jsonl(self.out)
        rows[0]["claims"][0]["organisation_number"] = ORG2
        self.assertFalse(validate_envelopes(rows, [ORG1, ORG2])["passed"])

    def test_registry_hash_order_independent(self):
        r1 = {"a": "1", "b": "2"}
        r2 = {"b": "2", "a": "1"}
        self.assertEqual(row_hash(r1), row_hash(r2))

    def test_huge_and_binary_registry_fields_are_withheld(self):
        facts = dict(registry_facts({"navn": "A" * 60000, "organisasjonsform.kode": "AS"}))
        self.assertNotIn("legal_name", facts)
        self.assertEqual(facts["legal_form"], "AS")
        facts = dict(registry_facts({"navn": "X\x00Y"}))
        self.assertEqual(facts, {})

    def test_registry_facts_strict_ontology(self):
        facts = dict(registry_facts({"navn": "X", "antallAnsatte": "10", "hjemmeside": "x.no"}))
        self.assertEqual(facts, {"legal_name": "X"})

    def test_bulk_utf8_norwegian(self):
        self.execute()
        self.assertEqual(read_jsonl(self.out)[1]["legal_identity"]["legal_name"], "Test Økonomi AS")

    def test_verify_reversed_input_order(self):
        self.execute()
        first = {r["organisation_number"]: r["claims"] for r in read_jsonl(self.out)}
        self.write_inputs([ORG2, ORG1])
        self.execute()
        new = {r["organisation_number"]: r["claims"] for r in read_jsonl(self.out)}
        self.assertEqual(first, new)
        self.assertEqual([r["organisation_number"] for r in read_jsonl(self.out)], [ORG2, ORG1])

    def test_empty_batch_explicit(self):
        self.write_inputs([])
        res = run(organisations=self.input, bulk=self.bulk, output=self.out, report=self.report,
                  run_id="empty", expected_count=0, fixture_mode=True)
        self.assertTrue(res["validated_outputs"]["passed"])
        self.assertEqual(read_jsonl(self.out), [])


if __name__ == "__main__":
    unittest.main()
