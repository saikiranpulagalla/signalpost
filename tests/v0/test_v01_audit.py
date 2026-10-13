"""V0.1 permanent adversarial regression suite, all offline synthetic fixtures."""
from __future__ import annotations

import copy
import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from v0_kernel.contracts import validate_envelopes, validate_prior_row
from v0_kernel.registry import registry_facts, snapshot_checksum
from v0_kernel.runner import run
from v0_kernel.storage import atomic_jsonl, read_jsonl

A, B = "993578843", "923609016"
COLUMNS = ["organisasjonsnummer", "navn", "organisasjonsform.kode", "sisteInnsendteAarsregnskap"]


class TestV01Adversarial(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.p = Path(temp.name)
        self.batch = self.p / "batch.jsonl"
        self.bulk = self.p / "bulk.csv"
        self.out = self.p / "result.jsonl"
        self.report = self.p / "report.json"
        self.batch.write_text("".join(json.dumps({"organisation_number": x}) + "\n" for x in (A, B)))
        self.make_bulk([
            {"organisasjonsnummer": A, "navn": "Original Company AS", "organisasjonsform.kode": "AS", "sisteInnsendteAarsregnskap": "2025"},
            {"organisasjonsnummer": B, "navn": "Second Company AS", "organisasjonsform.kode": "AS"},
        ])
        self.source_rows = None
        self.source_digest = None

    def make_bulk(self, rows, cols=COLUMNS):
        with self.bulk.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=cols)
            writer.writeheader()
            writer.writerows(rows)

    def execute(self, **kw):
        return run(organisations=self.batch, bulk=self.bulk, output=self.out, report=self.report,
                   run_id="v01-audit", expected_count=2, fixture_mode=True, checkpoint_every=1, **kw)

    def current(self):
        from v0_kernel.registry import read_selected_rows
        return read_selected_rows(self.bulk, {A, B}), snapshot_checksum(self.bulk)

    def verdict(self, rows):
        source_rows, digest = self.current()
        return validate_envelopes(rows, [A, B], source_rows=source_rows, source_snapshot_sha256=digest)

    def mutate(self, fun):
        self.execute()
        rows = read_jsonl(self.out)
        fun(rows)
        result = self.verdict(rows)
        self.assertFalse(result["passed"], f"malicious edit accepted: {result}")
        return result

    def test_01_forged_claim_value_rejected(self):
        self.mutate(lambda x: x[0]["claims"][0].update(value="FABRICATED VALUE"))

    def test_02_bad_hash_span_date_rejected(self):
        def corruption(x):
            x[0]["evidence"][0].update(content_sha256="not-a-hash", claim_span="unrelated", retrieved_at="not-a-date")
        self.mutate(corruption)

    def test_03_missing_identity_changes_and_bad_terminal_rejected(self):
        def corruption(x):
            del x[0]["legal_identity"]
            del x[0]["changes"]
            x[0]["run"]["terminal_status"] = "anything"
        self.mutate(corruption)

    def test_04_foreign_prior_evidence_refused(self):
        self.execute()
        previous = self.p / "previous.jsonl"
        data = read_jsonl(self.out)
        data[0]["evidence"].append({"id": "foreign", "source_url": "https://evil.example", "content_sha256": "bad"})
        atomic_jsonl(previous, data)
        result = self.execute(previous_output=previous)
        self.assertIn("untrusted previous output", result["interruption_or_error"])
        self.assertEqual(len(read_jsonl(self.out)), 2)
        self.assertFalse(result["execution_success"])

    def test_05_untrusted_source_requires_pin_for_non_fixture(self):
        with self.assertRaisesRegex(ValueError, "independently pinned"):
            run(organisations=self.batch, bulk=self.bulk, output=self.out, report=self.report, run_id="non-fixture")
        with self.assertRaisesRegex(ValueError, "independently pinned"):
            run(organisations=self.batch, bulk=self.bulk, output=self.out, report=self.report,
                run_id="non-fixture", expected_bulk_sha256="0" * 63)

    def test_06_invalid_financial_year_withheld_and_blank_name_rejected(self):
        self.make_bulk([
            {"organisasjonsnummer": A, "navn": "   ", "sisteInnsendteAarsregnskap": "banana"},
            {"organisasjonsnummer": B, "navn": "Valid AB", "sisteInnsendteAarsregnskap": "not-a-year"},
        ])
        r = self.execute()
        data = read_jsonl(self.out)
        self.assertTrue(r["validated_outputs"]["passed"])
        self.assertEqual(data[0]["legal_identity"]["availability"], "ambiguous")
        self.assertNotIn("last_submitted_accounts_year", {c["field"] for c in data[1]["claims"]})

    def test_07_oversized_single_csv_field_does_not_destroy_other_company(self):
        self.make_bulk([
            {"organisasjonsnummer": A, "navn": "X" * 200000, "organisasjonsform.kode": "AS"},
            {"organisasjonsnummer": B, "navn": "Valid Company", "organisasjonsform.kode": "AS"},
        ])
        r = self.execute()
        self.assertIsNone(r["interruption_or_error"])
        data = read_jsonl(self.out)
        self.assertEqual(data[0]["legal_identity"]["availability"], "ambiguous")
        self.assertEqual(data[1]["legal_identity"]["availability"], "available")

    def test_08_ignored_csv_field_does_not_duplicate_evidence(self):
        self.execute()
        old = self.p / "old.jsonl"
        old.write_bytes(self.out.read_bytes())
        data = read_jsonl(self.out)
        old_id_set = {e["id"] for e in data[0]["evidence"]}
        self.make_bulk([
            {"organisasjonsnummer": A, "navn": "Original Company AS", "organisasjonsform.kode": "AS", "sisteInnsendteAarsregnskap": "2025", "ignored": "CHANGED"},
            {"organisasjonsnummer": B, "navn": "Second Company AS", "organisasjonsform.kode": "AS"},
        ], COLUMNS + ["ignored"])
        r = self.execute(previous_output=old)
        now = read_jsonl(self.out)
        self.assertTrue(r["validated_outputs"]["passed"], r["validated_outputs"]["errors"])
        self.assertEqual(now[0]["changes"], [])
        self.assertTrue(old_id_set.issubset({e["id"] for e in now[0]["evidence"]}))
        self.assertEqual(len(now[0]["source_snapshots"]), 2)
        self.assertIn(snapshot_checksum(self.bulk),
                      {s["snapshot_sha256"] for s in now[0]["source_snapshots"]})

    def test_09_partial_run_not_eligible(self):
        report = self.execute(stop_after=1)
        self.assertTrue(report["validated_outputs"]["passed"])
        self.assertFalse(report["execution_success"])
        self.assertFalse(report["release_gate_passed"])
        self.assertEqual(report["processed"], 1)
        self.assertEqual(report["failed"], 1)

    def test_10_duplicate_field_with_other_value_rejected(self):
        self.mutate(lambda x: x[0]["claims"].append({**x[0]["claims"][0], "value": "OTHER ENTITY"}))

    def test_11_out_of_range_confidence_rejected(self):
        self.mutate(lambda x: x[0]["claims"][0].update(confidence=100.0))

    def test_12_negative_request_and_cost_rejected(self):
        self.mutate(lambda x: x[0]["operations"].update(requests=-9, third_party_cost_usd=-100))

    def test_13_cross_company_source_key_rejected(self):
        self.mutate(lambda x: x[0]["evidence"][0].update(source_row_key=B))

    def test_14_fabricated_changes_rejected(self):
        self.mutate(lambda x: x[0]["changes"].append({"field": "legal_name", "kind": "changed", "old": "A", "new": "B"}))

    def test_15_available_with_empty_legal_name_rejected(self):
        self.mutate(lambda x: x[0]["legal_identity"].update(legal_name=" ", availability="available"))

    def test_16_unknown_evidence_class_rejected(self):
        self.mutate(lambda x: x[0]["evidence"][0].update(source_class="unofficial-platform"))

    def test_17_fake_evidence_field_locator_rejected(self):
        self.mutate(lambda x: x[0]["evidence"][0].update(source_field_key="arbitrary_new_column"))

    def test_18_existing_claim_with_synchronized_fake_span_still_rejected(self):
        def corruption(x):
            field = x[0]["claims"][0]["field"]
            x[0]["claims"][0]["value"] = "FABRICATED"
            x[0]["evidence"][0]["claim_span"] = f"{field}: FABRICATED"
        self.mutate(corruption)

    def test_19_strict_validator_without_source_rows_is_not_green(self):
        self.execute()
        self.assertFalse(validate_envelopes(read_jsonl(self.out), [A, B])["passed"])

    def test_20_pinned_registry_sha_mismatch_yields_failed_complete_output(self):
        r = run(organisations=self.batch, bulk=self.bulk, output=self.out, report=self.report,
                run_id="checked", expected_count=2, expected_bulk_sha256="0" * 64)
        self.assertEqual(r["processed"], 0)
        self.assertFalse(r["release_gate_passed"])
        self.assertIn("mismatch", r["interruption_or_error"])
        self.assertEqual(len(read_jsonl(self.out)), 2)
        self.assertFalse(any(row["claims"] for row in read_jsonl(self.out)))

    def test_21_pinned_source_matches_non_fixture_but_official_contract_unconfirmed(self):
        digest = snapshot_checksum(self.bulk)
        r = run(organisations=self.batch, bulk=self.bulk, output=self.out, report=self.report,
                run_id="local-source-pin", expected_count=2, expected_bulk_sha256=digest)
        self.assertTrue(r["execution_success"])
        self.assertEqual(r["source_authenticity"], "externally_pinned_sha256")
        self.assertTrue(r["release_gate_passed"])  # local gate ONLY, not Builderr acceptance

    def test_22_previous_digest_mismatch_before_any_overwrite(self):
        self.execute()
        p = self.p / "previous.jsonl"
        p.write_bytes(self.out.read_bytes())
        result = run(organisations=self.batch, bulk=self.bulk, output=self.out, report=self.report,
                     run_id="strict", expected_count=2, expected_bulk_sha256=snapshot_checksum(self.bulk),
                     previous_output=p, expected_previous_sha256="0" * 64)
        self.assertIn("SHA-256 mismatch", result["interruption_or_error"])
        self.assertFalse(result["release_gate_passed"])
        self.assertEqual(len(read_jsonl(self.out)), 2)

    def test_23_true_change_exactly_one_and_no_duplicates_on_rerun(self):
        self.execute()
        prior = self.p / "prior.jsonl"
        prior.write_bytes(self.out.read_bytes())
        self.make_bulk([
            {"organisasjonsnummer": A, "navn": "Changed Company AS", "organisasjonsform.kode": "AS", "sisteInnsendteAarsregnskap": "2025"},
            {"organisasjonsnummer": B, "navn": "Second Company AS", "organisasjonsform.kode": "AS"},
        ])
        self.execute(previous_output=prior)
        rows = read_jsonl(self.out)
        self.assertEqual(rows[0]["changes"], [{"field": "legal_name", "kind": "changed", "old": "Original Company AS", "new": "Changed Company AS"}])
        self.assertEqual(len({e["id"] for e in rows[0]["evidence"]}), len(rows[0]["evidence"]))

    def test_24_fixture_run_never_marked_release_eligible(self):
        r = self.execute()
        self.assertTrue(r["execution_success"])
        self.assertFalse(r["release_gate_passed"])
        self.assertEqual(r["source_authenticity"], "fixture_unverified")

    def test_25_missing_source_never_fabricates_claims(self):
        self.bulk.unlink()
        r = self.execute()
        self.assertFalse(r["execution_success"])
        self.assertTrue(r["validated_outputs"]["passed"])
        self.assertFalse(any(row["claims"] for row in read_jsonl(self.out)))

    def test_26_empty_batch_requires_explicit_opt_in(self):
        self.batch.write_text("")
        with self.assertRaisesRegex(ValueError, "Empty batch"):
            run(organisations=self.batch, bulk=self.bulk, output=self.out, report=self.report,
                run_id="empty", fixture_mode=True)

    def test_27_malformed_unhashable_claim_rejected_without_validator_crash(self):
        self.mutate(lambda x: x[0]["claims"][0].update(field=["bad", "value"]))

    def test_28_missing_evidence_set_rejected(self):
        self.mutate(lambda x: x[0].pop("evidence"))

    def test_29_missing_prior_artifact_refused(self):
        with self.assertRaisesRegex(ValueError, "does not exist"):
            self.execute(previous_output=self.p / "missing-history.jsonl")

    def test_30_duplicate_csv_header_fails_source_closed(self):
        self.bulk.write_text("organisasjonsnummer,navn,navn\n" + A + ",Legal,Impostor\n")
        report = self.execute()
        self.assertIn("duplicate column names", report["interruption_or_error"])
        self.assertEqual(len(read_jsonl(self.out)), 2)
        self.assertEqual(sum(len(x["claims"]) for x in read_jsonl(self.out)), 0)

    def test_31_extra_csv_row_column_fails_closed(self):
        self.bulk.write_text("organisasjonsnummer,navn\n" + A + ",Legal,Injected\n")
        report = self.execute()
        self.assertIn("more columns", report["interruption_or_error"])
        self.assertFalse(report["execution_success"])

    def test_32_future_evidence_rejected(self):
        self.mutate(lambda x: x[0]["evidence"][0].update(retrieved_at="2099-01-01T00:00:00Z"))


if __name__ == "__main__":
    unittest.main()
