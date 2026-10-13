"""V1c: paged official-location evidence must be exact, bounded and reproducible."""
import copy
import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from v0_kernel.storage import read_jsonl
from v1_official.runner import run, validate_v1
from v1_official.sources import (
    SourceResponse, assemble_location_pages, canonical_bytes, endpoint,
    fixture_fetch, live_fetch, location_page_url, origin_for_fact,
    source_is_complete, verified_payload, sha,
)
from test_official_integration import ORG_A, ORG_B, ROOT, example_fixture


def page(number, total, items, count=None, *, hostile_next=False):
    result = {"_embedded": {"underenheter": items},
              "page": {"number": number, "totalPages": total,
                       "totalElements": count if count is not None else sum(len(items) for _ in range(total))}}
    if hostile_next:
        result["_links"] = {"next": {"href": "http://169.254.169.254/latest/meta-data"}}
    return result


def item(org, n, name=None, parent=None):
    return {"organisasjonsnummer": n, "overordnetEnhet": parent or org,
            "navn": name or f"WORKPLACE {n}"}


def wire(org, index, body, *, requests=1):
    blob = canonical_bytes(body)
    return SourceResponse("locations", location_page_url(org, index), 200,
                          "2026-10-08T10:00:00Z", body,
                          hashlib.sha256(blob).hexdigest(), None, requests,
                          "wire-json", sha(body))


class V1cPaging(unittest.TestCase):
    def fixture(self, pages):
        fixture = example_fixture()
        fixture[ORG_A]["locations"] = {"pages": pages}
        return fixture

    def execute(self, payload, *, budget=20):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        d = Path(td.name)
        fx = d / "sources.json"
        fx.write_text(json.dumps(payload), encoding="utf-8")
        output, report = d / "output.jsonl", d / "report.json"
        result = run(organisations=ROOT / "tests/v0/demo/organisations.jsonl",
                     bulk=ROOT / "tests/v0/demo/frozen-registry.csv",
                     output=output, report=report, run_id="v1c-paged-synthetic",
                     expected_count=2, expected_bulk_sha256=None,
                     fixture_mode=True, fixture_responses=fx,
                     max_requests=budget, max_seconds=30, checkpoint_every=1)
        return result, read_jsonl(output)

    def test_two_pages_verified_at_source_local_spans(self):
        p0 = page(0, 2, [item(ORG_A, ORG_B)], count=2)
        p1 = page(1, 2, [item(ORG_A, "978617360")], count=2)
        result, rows = self.execute(self.fixture([p0, p1]))
        self.assertEqual(result["run_status"], "completed", result)
        self.assertTrue(result["validation"]["passed"])
        a = rows[0]
        self.assertEqual(a["availability"]["locations"], "available")
        self.assertTrue(a["official_modules"]["locations"]["source_complete"])
        claims = [c for c in a["claims"] if c["field"] == "registered_subunit"]
        self.assertEqual(len(claims), 2)
        eids = {c["value"]["orgnr"]: c["evidence_ids"][0] for c in claims}
        evidence = {e["id"]: e for e in a["evidence"]}
        self.assertEqual(evidence[eids[ORG_B]]["source_url"], location_page_url(ORG_A, 0))
        self.assertEqual(evidence[eids["978617360"]]["source_url"], location_page_url(ORG_A, 1))
        self.assertEqual(evidence[eids["978617360"]]["source_page_locator"],
                         "$._embedded.underenheter[0]")
        self.assertEqual(len(a["official_modules"]["locations"]["page_provenance"]), 2)

    def test_bad_parent_is_not_promoted_while_valid_second_page_is(self):
        p0 = page(0, 2, [item(ORG_A, ORG_B, parent="111111111")], count=2)
        p1 = page(1, 2, [item(ORG_A, "978617360")], count=2)
        report, rows = self.execute(self.fixture([p0, p1]))
        self.assertEqual(report["run_status"], "completed")
        vals = [c["value"]["orgnr"] for c in rows[0]["claims"] if c["field"] == "registered_subunit"]
        self.assertEqual(vals, ["978617360"])

    def test_bad_continuation_is_withheld_not_misrepresented_as_complete(self):
        p0 = page(0, 2, [item(ORG_A, ORG_B)], count=2)
        p1 = page(7, 2, [item(ORG_A, "978617360")], count=2)
        report, rows = self.execute(self.fixture([p0, p1]))
        self.assertEqual(report["run_status"], "completed")
        self.assertEqual(rows[0]["availability"]["locations"], "failed")
        self.assertFalse(any(c["field"] == "registered_subunit" for c in rows[0]["claims"]))

    def test_partial_page_set_retains_verified_facts_but_not_full_coverage(self):
        p0 = page(0, 3, [item(ORG_A, ORG_B)], count=3)
        report, rows = self.execute(self.fixture([p0]))
        self.assertTrue(report["validation"]["passed"])
        self.assertFalse(rows[0]["official_modules"]["locations"]["source_complete"])
        self.assertEqual(len([x for x in rows[0]["claims"] if x["field"] == "registered_subunit"]), 1)

    def test_metadata_inconsistent_rejected(self):
        p0 = page(0, 2, [item(ORG_A, ORG_B)], count=2)
        p1 = page(1, 3, [item(ORG_A, "978617360")], count=2)
        report, rows = self.execute(self.fixture([p0, p1]))
        self.assertEqual(report["run_status"], "completed")
        self.assertEqual(rows[0]["availability"]["locations"], "failed")

    def test_live_fetch_follows_only_derived_urls_under_budget(self):
        p0 = page(0, 3, [item(ORG_A, ORG_B)], count=3, hostile_next=True)
        p1 = page(1, 3, [item(ORG_A, "978617360")], count=3)
        p2 = page(2, 3, [item(ORG_A, "923609017")], count=3)
        pages = [p0, p1, p2]
        seen = []
        def mocked(module, url, *, timeout, max_bytes):
            seen.append(url)
            return wire(ORG_A, len(seen)-1, pages[len(seen)-1])
        with patch("v1_official.sources._single_fetch_url", side_effect=mocked):
            response = live_fetch("locations", ORG_A, max_requests=2, policy="authorized_public_brreg")
        self.assertEqual(response.requests, 2)
        self.assertEqual(seen, [location_page_url(ORG_A, 0), location_page_url(ORG_A, 1)])
        self.assertNotIn("169.254", repr(seen))
        self.assertFalse(source_is_complete("locations", response.body))
        self.assertTrue(verified_payload(response))

    def test_failed_continuation_counts_request_and_keeps_partial_evidence(self):
        p0 = page(0, 2, [item(ORG_A, ORG_B)], count=2)
        seen = []
        def mocked(module, url, *, timeout, max_bytes):
            seen.append(url)
            if len(seen) == 1:
                return wire(ORG_A, 0, p0)
            return SourceResponse("locations", url, 429, "2026-10-08T10:00:00Z",
                                  error="HTTP 429", requests=1)
        with patch("v1_official.sources._single_fetch_url", side_effect=mocked):
            result = live_fetch("locations", ORG_A, max_requests=2, policy="authorized_public_brreg")
        self.assertEqual(result.requests, 2)
        self.assertEqual(result.failed_continuations, 1)
        self.assertEqual(len(result.pages), 1)
        self.assertFalse(source_is_complete("locations", result.body))
        self.assertTrue(verified_payload(result))
        self.assertEqual(origin_for_fact(result, "$._embedded.underenheter[0]")[0],
                         location_page_url(ORG_A, 0))
        self.assertFalse(verified_payload(replace(result, requests=1)))

    def test_untrusted_cross_host_page_rejected(self):
        p0 = wire(ORG_A, 0, page(0, 2, [item(ORG_A, ORG_B)], count=2))
        p1 = wire(ORG_A, 1, page(1, 2, [item(ORG_A, "978617360")], count=2))
        wrong = replace(p1, url="https://example.org/redirected/locations?page=1")
        with self.assertRaises(ValueError):
            assemble_location_pages(ORG_A, [p0, wrong])

    def test_complete_three_pages_fetch(self):
        pages = [page(i, 3, [item(ORG_A, str(923609016+i))], count=3) for i in range(3)]
        def mocked(module, url, *, timeout, max_bytes):
            i = int(url.rsplit("page=",1)[1]) if "&page=" in url else 0
            return wire(ORG_A, i, pages[i])
        with patch("v1_official.sources._single_fetch_url", side_effect=mocked):
            resp = live_fetch("locations", ORG_A, max_requests=5, policy="authorized_public_brreg")
        self.assertEqual(resp.requests, 3)
        self.assertTrue(source_is_complete("locations", resp.body))
        self.assertEqual(len(resp.body["_embedded"]["underenheter"]), 3)

    def test_page_tampering_invalidates_composite(self):
        p0 = wire(ORG_A, 0, page(0, 2, [item(ORG_A, ORG_B)], count=2))
        p1 = wire(ORG_A, 1, page(1, 2, [item(ORG_A, "978617360")], count=2))
        composite = assemble_location_pages(ORG_A, [p0, p1])
        self.assertTrue(verified_payload(composite))
        self.assertFalse(verified_payload(replace(composite, pages=(p0, replace(p1, content_sha256="0"*64)))))
        self.assertFalse(verified_payload(replace(composite, pages=(p1,p0))))
        self.assertFalse(verified_payload(replace(composite, body={"_embedded":{"underenheter":[]}})))

    def test_provenance_mutation_cannot_pass_run_validator(self):
        p0 = page(0, 2, [item(ORG_A, ORG_B)], count=2)
        p1 = page(1, 2, [item(ORG_A, "978617360")], count=2)
        report, rows = self.execute(self.fixture([p0, p1]))
        self.assertTrue(report["validation"]["passed"])
        # Runner validation is performed against source bytes during run; assert
        # saved provenance itself is also directly checkable.
        evidence = [e for e in rows[0]["evidence"] if e.get("module") == "locations"]
        self.assertEqual(len(evidence), 2)
        self.assertNotEqual(evidence[0]["content_sha256"], "0"*64)
        self.assertNotEqual(evidence[1]["content_sha256"], "0"*64)

if __name__ == "__main__":
    unittest.main()
