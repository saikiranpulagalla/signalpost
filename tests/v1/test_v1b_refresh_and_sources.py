"""V1b fresh adversarial tests: independently exercise source and refresh trust."""
from __future__ import annotations

import copy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import random

from v0_kernel.storage import read_jsonl
from v1_official.runner import _enhance, run, placeholder, validate_v1
from v1_official.refresh import load_prior, refresh_view
from v1_official.sources import (SourceResponse, endpoint, extract, fixture_fetch,
                                 verified_payload, sha, locator_proves_fact)

ROOT = Path(__file__).resolve().parents[2]
ORG_A = "993578843"
ORG_B = "923609016"


def fixture():
    return {
        ORG_A: {
            "registry_live": {"body": {"organisasjonsnummer": ORG_A, "navn": "SYNTHETIC ALPHA AS"}},
            "roles": {"body": {"rollegrupper": [{"type": {"kode": "STYR"}, "roller": [
                {"type": {"kode": "LEDE"}, "person": {"navn": {"fornavn": "Ada", "etternavn": "Test"}}}]}]}},
            "locations": {"body": {"_embedded": {"underenheter": [
                {"overordnetEnhet": ORG_A, "organisasjonsnummer": ORG_B, "navn": "WORKPLACE"}]}}},
            "financials": {"body": []},
        },
        ORG_B: {
            "registry_live": {"body": {"organisasjonsnummer": ORG_B, "navn": "SYNTHETIC BETA AS"}},
            "roles": {"body": {"rollegrupper": []}},
            "locations": {"body": {"_embedded": {"underenheter": []}}},
            "financials": {"body": []},
        },
    }


class V1bSourceGuard(unittest.TestCase):
    def test_missing_parent_does_not_publish_subunit(self):
        body = copy.deepcopy(fixture()[ORG_A]["locations"]["body"])
        body["_embedded"]["underenheter"][0].pop("overordnetEnhet")
        facts, state = extract("locations", ORG_A, body, frozen_legal_name="SYNTHETIC ALPHA AS")
        self.assertEqual(facts, [])
        self.assertEqual(state, "ambiguous")

    def test_wrong_parent_does_not_publish_subunit(self):
        body = copy.deepcopy(fixture()[ORG_A]["locations"]["body"])
        body["_embedded"]["underenheter"][0]["overordnetEnhet"] = ORG_B
        facts, state = extract("locations", ORG_A, body, frozen_legal_name=None)
        self.assertEqual((facts, state), ([], "ambiguous"))

    def test_valid_parent_and_real_locator(self):
        body = fixture()[ORG_A]["locations"]["body"]
        facts, state = extract("locations", ORG_A, body, frozen_legal_name=None)
        self.assertEqual(state, "available")
        self.assertEqual(facts[0]["locator"], "$._embedded.underenheter[0]")
        self.assertTrue(locator_proves_fact("locations", ORG_A, body, facts[0]))
        self.assertFalse(locator_proves_fact("locations", ORG_B, body, facts[0]))

    def test_roles_have_real_group_and_member_jsonpath(self):
        body = {"rollegrupper": [
            {"roller": [{"type": {"kode": "LEDE"}, "person": {"navn": {"fornavn": "A"}}}]},
            {"roller": [{"type": {"kode": "DAGL"}, "person": {"navn": {"fornavn": "B"}}},
                       {"type": {"kode": "MEDL"}, "person": {"navn": {"fornavn": "C"}}}]}]}
        facts, state = extract("roles", ORG_A, body, frozen_legal_name=None)
        self.assertEqual(state, "available")
        self.assertEqual([f["locator"] for f in facts],
                         ["$.rollegrupper[0].roller[0]", "$.rollegrupper[1].roller[0]",
                          "$.rollegrupper[1].roller[1]"])
        self.assertTrue(all(locator_proves_fact("roles", ORG_A, body, f) for f in facts))
        fake = dict(facts[2], locator="$.rollegrupper[0].roller[0]")
        self.assertFalse(locator_proves_fact("roles", ORG_A, body, fake))

    def test_financial_claims_reference_actual_nested_json_not_synthetic_paths(self):
        body = [{"organisasjonsnummer": ORG_A,
                 "regnskapsperiode": {"fraDato": "2025-01-01", "tilDato": "2025-12-31"},
                 "valuta": "NOK", "regnskapstype": "SELSKAP",
                 "resultatregnskapResultat": {"driftsresultat": {"driftsinntekter": {"sumDriftsinntekter": 1234},
                                                            "driftsresultat": 120}, "aarsresultat": 95},
                 "eiendeler": {"sumEiendeler": 5000},
                 "egenkapitalGjeld": {"egenkapital": {"sumEgenkapital": 2000},
                                     "gjeldOversikt": {"sumGjeld": 3000}}}]
        facts, state = extract("financials", ORG_A, body, frozen_legal_name=None)
        self.assertEqual(state, "available")
        self.assertEqual(len(facts), 6)
        self.assertTrue(all(locator_proves_fact("financials", ORG_A, body, f) for f in facts))
        amount = next(f for f in facts if f["field"] == "financial_revenue")
        self.assertEqual(amount["locator"],
                         "$[0].resultatregnskapResultat.driftsresultat.driftsinntekter.sumDriftsinntekter")
        self.assertFalse(locator_proves_fact("financials", ORG_A, body,
                                             dict(amount, locator="$.records[0].revenue")))
        self.assertFalse(locator_proves_fact("financials", ORG_A, body,
                                             dict(amount, value=dict(amount["value"], amount=1235))))

    def test_malformed_role_group_fails_closed(self):
        body = {"rollegrupper": [{"roller": ["malicious item"]}]}
        with self.assertRaises((TypeError, AttributeError, ValueError)):
            extract("roles", ORG_A, body, frozen_legal_name=None)

    def test_arbitrary_hash_is_not_transport_attestation(self):
        body = fixture()[ORG_A]["registry_live"]["body"]
        forged = SourceResponse("registry_live", endpoint("registry_live", ORG_A), 200,
                                "2026-10-08T06:00:00Z", body=body, content_sha256="a"*64)
        self.assertFalse(verified_payload(forged))
        row = placeholder(ORG_A, "synthetic", "2026-10-08T05:59:00Z")
        row["legal_identity"] = {"organisation_number": ORG_A, "legal_name": "SYNTHETIC ALPHA AS", "availability": "available"}
        _enhance(row, "registry_live", forged)
        self.assertEqual(row["availability"]["registry_live"], "failed")
        self.assertEqual(row["claims"], [])

    def test_modified_attested_fixture_response_cannot_publish(self):
        response = fixture_fetch("registry_live", ORG_A, fixture())
        self.assertTrue(verified_payload(response))
        tampered = replace(response, body={"organisasjonsnummer": ORG_A, "navn": "FAKE"})
        self.assertFalse(verified_payload(tampered))
        row = placeholder(ORG_A, "synthetic", "2026-10-08T05:59:00Z")
        row["legal_identity"] = {"organisation_number": ORG_A, "legal_name": "SYNTHETIC ALPHA AS", "availability": "available"}
        _enhance(row, "registry_live", tampered)
        self.assertEqual(row["claims"], [])

    def test_fixture_body_hash_is_exact(self):
        response = fixture_fetch("registry_live", ORG_A, fixture())
        self.assertEqual(response.body_sha256, sha(response.body))
        self.assertEqual(response.body_sha256, response.content_sha256)
        self.assertEqual(response.digest_kind, "canonical-json")

    def test_fail_closed_unknown_attestation_kind(self):
        r = fixture_fetch("registry_live", ORG_A, fixture())
        self.assertFalse(verified_payload(replace(r, digest_kind="UNKNOWN")))
        self.assertFalse(verified_payload(replace(r, content_sha256="0"*64)))
        self.assertFalse(verified_payload(replace(r, body_sha256="0"*64)))


class V1bOfficialRefresh(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.directory = Path(self.work.name)
        self.orgs = [ORG_A, ORG_B]
        self.base = fixture()

    def execute(self, payload=None, *, prior=None, digest=None):
        i = len(list(self.directory.glob("output*.jsonl")))
        fixture_path = self.directory / f"fixture{i}.json"
        fixture_path.write_text(json.dumps(payload if payload is not None else self.base), encoding="utf-8")
        output = self.directory / f"output{i}.jsonl"
        report = self.directory / f"report{i}.json"
        result = run(organisations=ROOT / "tests/v0/demo/organisations.jsonl",
                     bulk=ROOT / "tests/v0/demo/frozen-registry.csv",
                     output=output, report=report, run_id=f"v1b-synthetic-{i}", expected_count=2,
                     expected_bulk_sha256=None, fixture_mode=True, fixture_responses=fixture_path,
                     previous_output=prior, expected_previous_sha256=digest, max_requests=20,
                     max_seconds=30, checkpoint_every=1)
        return result, output, read_jsonl(output)

    @staticmethod
    def filehash(p):
        return hashlib.sha256(p.read_bytes()).hexdigest()

    def test_first_run_has_no_false_change(self):
        result, _, rows = self.execute()
        self.assertEqual(result["run_status"], "completed", result)
        self.assertEqual(rows[0]["official_refresh"]["state"], "no_prior_baseline")
        self.assertEqual(rows[0]["official_changes"], [])

    def test_same_semantic_facts_do_not_create_changes(self):
        _, p, old = self.execute()
        result, _, rows = self.execute(prior=p, digest=self.filehash(p))
        self.assertEqual(result["run_status"], "completed", result)
        self.assertEqual(rows[0]["official_refresh"]["state"], "unchanged")
        self.assertEqual(rows[0]["official_changes"], [])
        self.assertTrue(rows[0]["official_refresh"]["previous_sha256"])
        self.assertEqual(rows[0]["historical_official_evidence"], [])  # Same evidence IDs.
        self.assertTrue(rows[0]["historical_official_snapshots"])

    def test_changed_role_emits_add_and_remove(self):
        _, p, _ = self.execute()
        modified = copy.deepcopy(self.base)
        modified[ORG_A]["roles"]["body"]["rollegrupper"][0]["roller"][0]["person"]["navn"]["fornavn"] = "Grace"
        result, _, rows = self.execute(modified, prior=p, digest=self.filehash(p))
        self.assertEqual(result["run_status"], "completed", result)
        kinds = [e["kind"] for e in rows[0]["official_changes"] if e["module"] == "roles"]
        self.assertEqual(sorted(kinds), ["added", "removed"])
        self.assertEqual(rows[0]["official_refresh"]["state"], "changed")
        self.assertTrue(any(e["module"] == "roles" for e in rows[0]["historical_official_evidence"]))

    def test_blocked_source_cannot_create_fake_removal(self):
        _, p, _ = self.execute()
        modified = copy.deepcopy(self.base)
        modified[ORG_A]["roles"] = {"status": 403}
        result, _, rows = self.execute(modified, prior=p, digest=self.filehash(p))
        self.assertEqual(result["run_status"], "completed", result)
        self.assertFalse(any(e["module"] == "roles" for e in rows[0]["official_changes"]))
        self.assertEqual(rows[0]["official_refresh"]["state"], "source_unavailable")
        self.assertEqual(rows[0]["availability"]["roles"], "blocked")

    def test_multiple_refreshes_preserve_old_versions(self):
        _, p1, _ = self.execute()
        modified = copy.deepcopy(self.base)
        modified[ORG_A]["roles"]["body"]["rollegrupper"][0]["roller"][0]["person"]["navn"]["fornavn"] = "Grace"
        _, p2, rows2 = self.execute(modified, prior=p1, digest=self.filehash(p1))
        _, _, rows3 = self.execute(modified, prior=p2, digest=self.filehash(p2))
        self.assertEqual(rows3[0]["official_changes"], [])
        previous_eids = {e["id"] for e in rows2[0]["historical_official_evidence"]}
        current_history = {e["id"] for e in rows3[0]["historical_official_evidence"]}
        self.assertTrue(previous_eids.issubset(current_history))

    def test_rejects_tampered_prior_digest_and_keeps_envelopes(self):
        _, p, _ = self.execute()
        result, out, rows = self.execute(prior=p, digest="0"*64)
        self.assertEqual(result["run_status"], "failed")
        self.assertEqual(len(rows), 2)
        self.assertEqual([r["organisation_number"] for r in rows], self.orgs)
        self.assertTrue(any("digest" in e for e in result["errors"]))

    def test_rejects_malformed_prior_json_type_with_complete_checkpoint(self):
        bad = self.directory / "bad.jsonl"
        bad.write_text('[]\n[]\n', encoding="utf-8")
        result, out, rows = self.execute(prior=bad, digest=self.filehash(bad))
        self.assertEqual(result["run_status"], "failed")
        self.assertEqual(len(rows), 2)

    def test_rejects_unsigned_history_even_with_correct_shape(self):
        _, p, rows = self.execute()
        data = p.read_text()
        modified = data.replace("official_registry_name", "forged_registry_name")
        p.write_text(modified)
        result, _, _ = self.execute(prior=p, digest=hashlib.sha256(data.encode()).hexdigest())
        self.assertEqual(result["run_status"], "failed")

    def test_no_explicit_prior_digest_rejected_before_output(self):
        _, p, _ = self.execute()
        with self.assertRaises(ValueError):
            self.execute(prior=p, digest=None)

    def test_validator_survives_300_malformed_envelope_mutations(self):
        _, _, original = self.execute()
        rng = random.Random(20261008)
        keys = ("claims", "evidence", "official_modules", "source_snapshots",
                "availability", "legal_identity", "run", "operations",
                "official_refresh", "official_changes")
        mutations = (None, [], {}, "BROKEN", True, -1, 0.0, [1, 2])
        for _ in range(300):
            altered = copy.deepcopy(original)
            changed_key = rng.choice(keys)
            altered[0][changed_key] = rng.choice(mutations)
            validation = validate_v1(altered, self.orgs, original, {})
            self.assertIsInstance(validation, dict)
            self.assertFalse(validation["passed"])

    def test_required_refresh_metadata_cannot_be_stripped(self):
        _, _, rows = self.execute()
        mutated = copy.deepcopy(rows)
        mutated[0].pop("official_refresh")
        check = validate_v1(mutated, self.orgs, rows, {})
        self.assertFalse(check["passed"])


if __name__ == "__main__":
    unittest.main()
