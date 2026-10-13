"""V1a official-source integration and adversarial contract tests (stdlib)."""
from __future__ import annotations
import copy
from dataclasses import replace
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from v1_official.runner import _enhance, run, validate_v1
from v1_official.sources import (MODULE_URLS, SourceResponse, endpoint, extract, fixture_fetch, live_fetch, require_official_url, sha)
from v0_kernel.storage import read_jsonl

ROOT = Path(__file__).resolve().parents[2]
ORG_A = "993578843"
ORG_B = "923609016"


def example_fixture():
    return {
        ORG_A: {
            "registry_live": {"body": {"organisasjonsnummer": ORG_A, "navn": "SYNTHETIC ALPHA AS"}},
            "roles": {"body": {"rollegrupper": [{"type": {"kode": "STYR"}, "roller": [
                {"type": {"kode": "LEDE", "beskrivelse": "Chair"},
                 "person": {"navn": {"fornavn": "Test", "etternavn": "Person"}}}]}]}},
            "locations": {"body": {"_embedded": {"underenheter": [
                {"organisasjonsnummer": ORG_B, "overordnetEnhet": ORG_A, "navn": "SYNTHETIC WORKPLACE"}]}}},
            "financials": {"body": [{"organisasjonsnummer": ORG_A,
                 "regnskapsperiode": {"fraDato": "2025-01-01", "tilDato": "2025-12-31"},
                 "valuta": "NOK", "regnskapstype": "SELSKAP",
                 "resultatregnskapResultat": {"driftsresultat": {"driftsinntekter": {"sumDriftsinntekter": 1211}}},
                 "eiendeler": {"sumEiendeler": 7800}}]},
        },
        ORG_B: {
            "registry_live": {"body": {"organisasjonsnummer": ORG_B, "navn": "SYNTHETIC BETA AS"}},
            "roles": {"body": {"rollegrupper": []}},
            "locations": {"body": {"_embedded": {"underenheter": []}}},
            "financials": {"body": []},
        },
    }


class OfficialV1Integration(unittest.TestCase):
    def run_case(self, fixture=None, *, max_requests=50, max_seconds=30.0, fetcher=None,
                 expected_bulk_sha=None):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        temp = Path(work.name)
        fixture_path = temp / "response.json"
        fixture_path.write_text(json.dumps(fixture if fixture is not None else example_fixture()), encoding="utf-8")
        output = temp / "output.jsonl"
        report = temp / "report.json"
        result = run(organisations=ROOT / "tests/v0/demo/organisations.jsonl",
                     bulk=ROOT / "tests/v0/demo/frozen-registry.csv",
                     output=output, report=report, run_id="v1-local-synthetic",
                     expected_count=2, expected_bulk_sha256=expected_bulk_sha,
                     fixture_mode=True, fixture_responses=fixture_path,
                     max_requests=max_requests, max_seconds=max_seconds,
                     checkpoint_every=1, fetcher=fetcher)
        return result, read_jsonl(output), report

    def test_full_fixture_is_valid(self):
        report, rows, path = self.run_case()
        self.assertEqual(report["run_status"], "completed", report)
        self.assertTrue(report["baseline_gate"])
        self.assertTrue(report["validation"]["passed"])
        self.assertEqual(len(rows), 2)
        self.assertFalse(report["official_release_ready"])
        self.assertEqual(report["source_requests"], 0)
        self.assertEqual([r["organisation_number"] for r in rows], [ORG_A, ORG_B])
        for module in MODULE_URLS:
            self.assertIn(module, rows[0]["availability"])
        fields = {c["field"] for c in rows[0]["claims"]}
        self.assertTrue({"legal_name", "registered_role", "registered_subunit", "financial_revenue",
                         "official_registry_name"}.issubset(fields))

    def test_missing_live_name_disables_live_claim(self):
        fixture = example_fixture()
        fixture[ORG_A]["registry_live"]["body"]["navn"] = "OTHER COMPANY AS"
        report, rows, _ = self.run_case(fixture)
        self.assertEqual(rows[0]["availability"]["registry_live"], "ambiguous")
        self.assertFalse(any(c["field"] == "official_registry_name" for c in rows[0]["claims"]))
        self.assertTrue(report["validation"]["passed"], report)

    def test_wrong_entity_number_disables_claim(self):
        fixture = example_fixture()
        fixture[ORG_A]["registry_live"]["body"]["organisasjonsnummer"] = ORG_B
        report, rows, _ = self.run_case(fixture)
        self.assertEqual(rows[0]["availability"]["registry_live"], "ambiguous")
        self.assertTrue(report["validation"]["passed"], report)

    def test_mismatched_financial_entity_is_withheld(self):
        fixture = example_fixture()
        fixture[ORG_A]["financials"]["body"][0]["organisasjonsnummer"] = ORG_B
        report, rows, _ = self.run_case(fixture)
        self.assertEqual(rows[0]["availability"]["financials"], "ambiguous")
        self.assertFalse(any(c["field"] == "financial_revenue" for c in rows[0]["claims"]))
        self.assertTrue(report["validation"]["passed"], report)

    def test_missing_period_is_not_zero(self):
        fixture = example_fixture()
        fixture[ORG_A]["financials"]["body"][0].pop("regnskapsperiode")
        report, rows, _ = self.run_case(fixture)
        self.assertFalse(any(c["field"] == "financial_revenue" for c in rows[0]["claims"]))
        self.assertTrue(report["validation"]["passed"])

    def test_oversized_financial_number_withheld(self):
        fixture = example_fixture()
        fixture[ORG_A]["financials"]["body"][0]["eiendeler"]["sumEiendeler"] = 10**25
        report, rows, _ = self.run_case(fixture)
        self.assertFalse(any(c["field"] == "financial_assets" for c in rows[0]["claims"]))
        self.assertTrue(report["validation"]["passed"])

    def test_malformed_role_does_not_break_other_modules(self):
        fixture = example_fixture()
        fixture[ORG_A]["roles"]["body"]["rollegrupper"] = ["broken"]
        report, rows, _ = self.run_case(fixture)
        self.assertEqual(rows[0]["availability"]["roles"], "failed")
        self.assertEqual(rows[0]["availability"]["registry"], "available")
        self.assertTrue(report["validation"]["passed"], report)

    def test_403_is_blocked_and_404_not_available(self):
        fixture = example_fixture()
        fixture[ORG_A]["roles"] = {"status": 403}
        fixture[ORG_B]["roles"] = {"status": 404}
        report, rows, _ = self.run_case(fixture)
        self.assertEqual(rows[0]["availability"]["roles"], "blocked")
        self.assertEqual(rows[1]["availability"]["roles"], "not_available")
        self.assertTrue(report["validation"]["passed"])

    def test_source_hash_not_claim_value_is_evidence(self):
        report, rows, _ = self.run_case()
        self.assertTrue(report["validation"]["passed"])
        official = [e for e in rows[0]["evidence"] if e.get("source_class") == "official_brreg_api"]
        self.assertTrue(official)
        for ev in official:
            self.assertEqual(len(ev["content_sha256"]), 64)
            self.assertEqual(len(ev["semantic_sha256"]), 64)
            self.assertEqual(ev["source_row_key"], ORG_A)

    def test_source_and_claim_mutations_are_caught(self):
        report, rows, _ = self.run_case()
        self.assertTrue(report["validation"]["passed"])
        sources = {(org, module): replace(fixture_fetch(module, org, example_fixture()),
                    retrieved_at=rows[i]["official_modules"][module].get("retrieved_at") or
                    rows[i]["run"]["completed_at"])
                   for i, org in enumerate((ORG_A, ORG_B)) for module in MODULE_URLS}
        from v0_kernel.runner import run as baseline_run
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            baseline_run(organisations=ROOT / "tests/v0/demo/organisations.jsonl",
                         bulk=ROOT / "tests/v0/demo/frozen-registry.csv",
                         output=d / "base.jsonl", report=d / "report.json", run_id="baseline",
                         expected_count=2, fixture_mode=True)
            bases = read_jsonl(d / "base.jsonl")
        self.assertTrue(validate_v1(rows, [ORG_A, ORG_B], bases, sources)["passed"])
        for mutation in ("amount", "source", "orphan_evidence", "missing_snapshot", "wrong_scope", "wrong_identity", "span", "future_evidence"):
            changed = copy.deepcopy(rows)
            claim = next(c for c in changed[0]["claims"] if c["field"] == "financial_revenue")
            evidence_id = claim["evidence_ids"][0]
            evidence = next(e for e in changed[0]["evidence"] if e["id"] == evidence_id)
            if mutation == "amount": claim["value"]["amount"] = 999999
            if mutation == "source": evidence["content_sha256"] = "0" * 64
            if mutation == "span": evidence["claim_span"] = "fabricated value"
            if mutation == "future_evidence": evidence["retrieved_at"] = "2999-01-01T00:00:00Z"
            if mutation == "orphan_evidence":
                changed[0]["evidence"].append(dict(evidence, id="official-forged"))
            if mutation == "missing_snapshot":
                changed[0]["source_snapshots"] = [s for s in changed[0]["source_snapshots"]
                                                   if s.get("source_id") != "financials"]
            if mutation == "wrong_scope": claim["scope"] = "group"
            if mutation == "wrong_identity": changed[0]["legal_identity"]["legal_name"] = "FORGED"
            checked = validate_v1(changed, [ORG_A, ORG_B], bases, sources)
            self.assertFalse(checked["passed"], mutation)

    def test_duplicate_financial_values_do_not_invalidate_run(self):
        fixture = example_fixture()
        fixture[ORG_A]["financials"]["body"].append(
            copy.deepcopy(fixture[ORG_A]["financials"]["body"][0]))
        report, rows, _ = self.run_case(fixture)
        self.assertTrue(report["validation"]["passed"], report)
        self.assertEqual(sum(c["field"] == "financial_revenue" for c in rows[0]["claims"]), 1)

    def test_budget_exhaustion_never_appears_as_not_available(self):
        report, rows, _ = self.run_case(max_seconds=0.000001)
        # Budget exhaustion must degrade optional source coverage honestly;
        # it must not fabricate not_available or turn a valid batch into a zero.
        self.assertEqual(report["run_status"], "completed")
        self.assertTrue(report["partial_research_due_to_budget"])
        self.assertEqual(len(rows), 2)
        self.assertTrue(any("budget" in str(r["official_modules"]) for r in rows))
        self.assertTrue(any(r["availability"]["registry_live"] == "failed" for r in rows))

    def test_required_url_allowlist(self):
        for module in MODULE_URLS:
            require_official_url(endpoint(module, ORG_A))
        for url in ["http://data.brreg.no/enhetsregisteret/api/enheter/" + ORG_A,
                    "https://data.brreg.no.evil.test/enhetsregisteret/api/enheter/" + ORG_A,
                    "https://data.brreg.no:444/enhetsregisteret/api/enheter/" + ORG_A,
                    "https://user@data.brreg.no/enhetsregisteret/api/enheter/" + ORG_A,
                    "https://127.0.0.1/api/enheter/" + ORG_A,
                    "file:///etc/passwd"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                require_official_url(url)

    def test_malformed_source_payloads_never_create_unverified_claims(self):
        malformed = (None, [], {}, "unexpected", 7, True,
                     ["bad"], {"rollegrupper": ["bad"]},
                     {"_embedded": {"underenheter": "bad"}},
                     [{"regnskapsperiode": {"fraDato": "invalid"}}])
        from v1_official.runner import placeholder
        for module in MODULE_URLS:
            for body in malformed:
                with self.subTest(module=module, value=str(body)[:40]):
                    response = SourceResponse(module, endpoint(module, ORG_A),
                                              200, "2026-10-08T10:00:00Z", body=body,
                                              content_sha256="a" * 64)
                    row = placeholder(ORG_A, "synthetic", "2026-10-08T09:59:00Z")
                    row["legal_identity"] = {"organisation_number": ORG_A,
                                               "availability": "available", "legal_name": "SYNTHETIC ALPHA AS"}
                    _enhance(row, module, response)
                    self.assertIn(row["availability"][module],
                                  {"failed", "ambiguous", "not_available", "available"})
                    for claim in row["claims"]:
                        self.assertEqual(claim["organisation_number"], ORG_A)
                        self.assertEqual(claim["scope"], "exact_legal_entity")
                        self.assertEqual(len(claim["evidence_ids"]), 1)

    def test_non_200_never_has_fact(self):
        for status in (403, 404, 429, 500, 302):
            record = SourceResponse("roles", endpoint("roles", ORG_A), status, "2026-10-08T10:00:00Z", error="blocked")
            row = {"organisation_number": ORG_A, "claims": [], "evidence": [], "source_snapshots": [],
                   "availability": {}, "official_modules": {}, "errors": []}
            _enhance(row, "roles", record)
            self.assertEqual(len(row["claims"]), 0)
            self.assertEqual(len(row["evidence"]), 0)
            self.assertEqual(row["availability"]["roles"], "blocked" if status in (403,429) else
                             "not_available" if status == 404 else "failed")

    def test_fixture_cannot_be_labeled_live(self):
        with tempfile.TemporaryDirectory() as t:
            path = Path(t)
            (path / "fix.json").write_text("{}")
            with self.assertRaises(ValueError):
                run(organisations=ROOT / "tests/v0/demo/organisations.jsonl",
                    bulk=ROOT / "tests/v0/demo/frozen-registry.csv",
                    output=path/"o", report=path/"r", run_id="x", expected_count=2,
                    expected_bulk_sha256=None, fixture_mode=False,
                    fixture_responses=path/"fix.json")

    def test_cannot_overwrite_inputs(self):
        with self.assertRaises(ValueError):
            run(organisations=ROOT / "tests/v0/demo/organisations.jsonl",
                bulk=ROOT / "tests/v0/demo/frozen-registry.csv",
                output=ROOT / "tests/v0/demo/frozen-registry.csv", report=ROOT/"x",
                run_id="x", expected_count=2, expected_bulk_sha256=None,
                fixture_mode=True, fixture_responses=ROOT/"tests/v0/demo/frozen-registry.csv")

    def test_missing_modules_fail_safely(self):
        report, rows, _ = self.run_case({ORG_A: {}, ORG_B: {}})
        self.assertEqual(len(rows), 2)
        self.assertTrue(report["validation"]["passed"], report)
        self.assertTrue(all(row["availability"]["roles"] == "failed" for row in rows))

    def test_deterministic_claim_ids(self):
        first, rows1, _ = self.run_case()
        second, rows2, _ = self.run_case()
        self.assertEqual(first["run_status"], "completed")
        self.assertEqual(second["run_status"], "completed")
        for a, b in zip(rows1, rows2):
            e1 = {e["id"] for e in a["evidence"] if e["id"].startswith("official-")}
            e2 = {e["id"] for e in b["evidence"] if e["id"].startswith("official-")}
            self.assertEqual(e1, e2)

if __name__ == "__main__":
    unittest.main()
